# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Which column of an import file goes to which CRM field: the proposal (header words, then the values, then the
channel's custom fields), the preview of the first rows, the check of a mapping the user
confirmed, and the file rewritten as the import's own CSV (`CSV_COLUMNS` + `attr.<key>`), so the queued run reads one
format whatever the upload was.

One column goes to one field. When two columns propose the same field the more certain one wins and the other is
ignored with `duplicate_field`; `full_name` (split into first and last name) gives way to `first_name` / `last_name`.
Proposals and previews hold sample values for the person who uploaded the file and are never stored or logged."""

import csv
import io
import json
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from django_leads import settings as leads_settings
from django_leads.connectors.csv import ATTRIBUTE_PREFIXES, COLUMN_ALIASES, CSV_COLUMNS
from django_leads.enums import AttributeEntity
from django_leads.models import Channel
from django_leads.services import attribute_service, import_service, lead_type_service, stage_service
from django_leads.services.import_file_service import ZIP_MAGIC, Table, check_csv_size, read_table
from django_leads.services.import_vocabulary import CONCEPT_SYNONYMS, FIELD_SYNONYMS, WEAK_WORDS, compact, normalise

IGNORE = "ignore"
CERTAIN = 0.85
CHECK = 0.5
SAMPLE_COUNT = 5
SAMPLE_MAX_LENGTH = 80
DETECT_ROWS = 200
PREVIEW_ROWS = 5
COMPANY_KEYS = frozenset({"domain", "website", "email"})
NAME_PARTS = frozenset({"first_name", "last_name"})
TYPED_FIELDS = frozenset({"email", "website", "domain", "phone"})
FREE_TEXT_FIELDS = frozenset(
    {"company_name", "full_name", "first_name", "last_name", "job_title", "industry", "description", "origin"}
)

# (key, English label, entity) — the built-in targets, in the order the CMS lists them.
BUILTIN_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("company_name", "Company name", "company"),
    ("domain", "Domain", "company"),
    ("website", "Website", "company"),
    ("industry", "Industry", "company"),
    ("lead_type", "Company type", "company"),
    ("origin", "Origin", "company"),
    ("description", "Notes", "company"),
    ("full_name", "Full name (split into first and last name)", "contact"),
    ("first_name", "First name", "contact"),
    ("last_name", "Last name", "contact"),
    ("email", "Email", "contact"),
    ("phone", "Phone", "contact"),
    ("job_title", "Job title", "contact"),
    ("language", "Language", "contact"),
    ("legal_basis", "Legal basis", "contact"),
)

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.IGNORECASE)
HOST_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/\S*)?", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?[\d\s\-().\/]{6,24}")
POSTAL_RE = re.compile(r"\d{2}-\d{3}")


class MappingError(ValueError):
    """A mapping or a file the import refuses; `code` is the API issue, `field` the request field."""

    def __init__(self, code: str, message: str, field_name: str = "mapping") -> None:
        super().__init__(message)
        self.code, self.message, self.field = code, message, field_name


@dataclass
class Proposal:
    name: str
    index: int
    field: str = IGNORE
    confidence: float = 0.0
    source: str = "none"
    reason: str | None = "unrecognised"
    concept: str | None = None
    conflict_with: str | None = None
    samples: list[str] = dataclass_field(default_factory=list)

    @property
    def level(self) -> str:
        if self.source == "user":
            return "certain"
        if self.field == IGNORE:
            return "check" if self.reason == "duplicate_field" else "unrecognised"
        return "certain" if self.confidence >= CERTAIN else "check"

    def dump(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "index": self.index,
            "field": self.field,
            "confidence": round(self.confidence, 2),
            "level": self.level,
            "source": self.source,
            "reason": self.reason if self.field == IGNORE else None,
            "concept": self.concept,
            "samples": self.samples,
        }
        if self.conflict_with is not None:
            data["conflict_with"] = self.conflict_with
        return data


@dataclass
class Prepared:
    """An upload ready for `store_upload`: the import CSV, and what to note on the batch."""

    content: str
    column_mapping: list[dict[str, Any]]
    ignored_columns: list[str]


# --- the targets -----------------------------------------------------------------------------------------------------


def field_catalogue(channel: Channel) -> list[dict[str, Any]]:
    """Built-in fields, then the channel's active company (`attr.<key>`) and contact (`contact.attr.<key>`)
    attributes."""
    fields = [
        {"key": key, "label": label, "entity": entity, "custom": False, "kind": "text"}
        for key, label, entity in BUILTIN_FIELDS
    ]
    for prefix, entity in (("attr.", AttributeEntity.COMPANY), ("contact.attr.", AttributeEntity.CONTACT)):
        for key, definition in attribute_service.active_definitions(channel, entity).items():
            fields.append(
                {
                    "key": f"{prefix}{key}",
                    "label": definition.label,
                    "entity": str(entity),
                    "custom": True,
                    "kind": definition.kind,
                }
            )
    return fields


# --- the proposal ----------------------------------------------------------------------------------------------------


@dataclass
class _Context:
    catalogue: list[dict[str, Any]]
    languages: frozenset[str]
    lead_types: frozenset[str]

    @property
    def keys(self) -> set[str]:
        return {item["key"] for item in self.catalogue}

    @property
    def custom(self) -> list[dict[str, Any]]:
        return [item for item in self.catalogue if item["custom"]]


def _context(channel: Channel) -> _Context:
    from django_regional.models import Language

    languages = frozenset(code.lower() for code in Language.objects.values_list("iso2", flat=True) if code)
    return _Context(field_catalogue(channel), languages, frozenset(lead_type_service.active_codes(channel)))


def propose(table: Table, channel: Channel, user_mapping: dict[str, str] | None = None) -> list[Proposal]:
    """One proposal per column; a column named in `user_mapping` takes the user's field (`source: user`)."""
    return _propose(table, _context(channel), user_mapping or {})


def _propose(table: Table, context: _Context, user_mapping: dict[str, str]) -> list[Proposal]:
    proposals = []
    for index, name in enumerate(table.columns):
        values = [value for value in table.column_values(index) if value]
        proposal = Proposal(name=name, index=index, samples=_samples(values))
        if name in user_mapping:
            chosen = user_mapping[name]
            proposal.field, proposal.confidence, proposal.source = chosen, 1.0, "user"
            proposal.reason = "user" if chosen == IGNORE else None
        else:
            _guess(proposal, table.raw_header[index] if index < len(table.raw_header) else name, values, context)
        proposals.append(proposal)
    _resolve_conflicts(proposals)
    return proposals


def _samples(values: list[str]) -> list[str]:
    return [value[:SAMPLE_MAX_LENGTH] for value in dict.fromkeys(values)][:SAMPLE_COUNT]


def _guess(proposal: Proposal, header: str, values: list[str], context: _Context) -> None:
    by_header = _match_header(header, context)
    by_content = _match_content(values[:DETECT_ROWS], context) if values else None
    if by_header and not _values_overrule(by_header, by_content):
        target, concept, confidence, source = by_header
    elif by_content:
        target, concept, ratio, _ = by_content
        target = target or _custom_for_concept(concept, context)
        confidence, source = min(0.6 + 0.2 * ratio, 0.8), "content"
    else:
        proposal.reason = "unrecognised" if values else "empty_column"
        return
    proposal.concept, proposal.confidence, proposal.source = concept, confidence, source
    if target is None:
        proposal.reason = "no_crm_field"
        return
    proposal.field, proposal.reason = target, None


def _values_overrule(by_header: tuple, by_content: tuple | None) -> bool:
    """Values win over a weak header, and over a free-text header when they are clearly addresses, sites or phone
    numbers (`Kontakt` holding emails); an exact column name always stands."""
    if by_content is None or by_content[2] < 0.9 or by_header[3] == "exact":
        return False
    return by_header[2] < 0.9 or (by_content[0] in TYPED_FIELDS and by_header[0] in FREE_TEXT_FIELDS)


def _match_header(header: str, context: _Context) -> tuple[str | None, str | None, float, str] | None:
    """(field or None, concept, confidence, source) — or None when the header says nothing."""
    stripped = header.strip()
    exact = {old: column for column, old in COLUMN_ALIASES.items()}.get(stripped, stripped)
    if exact in context.keys:
        return exact, None, 1.0, "exact"
    key = compact(header)
    if not key:
        return None
    for item in context.custom:
        short = item["key"].split(".")[-1]
        if key in (compact(short), compact(item["label"])):
            return item["key"], None, 0.97, "custom_field"
    words = {key, key.removesuffix("s")} if len(key) > 3 else {key}
    for target, synonyms in FIELD_SYNONYMS.items():
        if words & {compact(word) for word in synonyms}:
            return target, None, 0.95, "header"
    for concept, synonyms in CONCEPT_SYNONYMS.items():
        if words & {compact(word) for word in synonyms}:
            custom = _custom_for_concept(concept, context)
            return custom, concept, 0.9 if custom else 0.95, "custom_field" if custom else "header"
    return _match_words(normalise(header).split(), context)


def _match_words(tokens: list[str], context: _Context) -> tuple[str | None, str | None, float, str] | None:
    """A synonym found as whole words inside a longer header (`Telefon komórkowy służbowy`): the longest one wins,
    a lone weak word (`nazwa`, `adres`) only when nothing else matched."""
    best: tuple[tuple[bool, int], str, bool] | None = None
    for group, is_concept in ((FIELD_SYNONYMS, False), (CONCEPT_SYNONYMS, True)):
        for target, synonyms in group.items():
            for synonym in synonyms:
                words = normalise(synonym).split()
                if not words or not _contains(tokens, words):
                    continue
                rank = (not (len(words) == 1 and words[0] in WEAK_WORDS), len(" ".join(words)))
                if best is None or rank > best[0]:
                    best = (rank, target, is_concept)
    if best is None:
        return None
    (strong, _), target, is_concept = best
    confidence = 0.75 if strong else 0.55
    if not is_concept:
        return target, None, confidence, "header"
    custom = _custom_for_concept(target, context)
    return custom, target, confidence, "custom_field" if custom else "header"


def _contains(tokens: list[str], words: list[str]) -> bool:
    size = len(words)
    return any(tokens[start : start + size] == words for start in range(len(tokens) - size + 1))


def _custom_for_concept(concept: str | None, context: _Context) -> str | None:
    if concept is None:
        return None
    words = {compact(word) for word in CONCEPT_SYNONYMS[concept]} | {compact(concept)}
    for item in context.custom:
        if compact(item["key"].split(".")[-1]) in words or compact(item["label"]) in words:
            return item["key"]
    return None


def _match_content(values: list[str], context: _Context) -> tuple[str | None, str | None, float, str] | None:
    """(field, concept, share of values that fit, "content") of the first detector most values fit (≥ 80 %)."""
    total = len(values)

    def share(test) -> float:
        return sum(1 for value in values if test(value)) / total

    if (ratio := share(lambda v: EMAIL_RE.fullmatch(v) is not None)) >= 0.8:
        return "email", None, ratio, "content"
    if (ratio := share(lambda v: "@" not in v and HOST_RE.fullmatch(v) is not None)) >= 0.8:
        site = share(lambda v: v.lower().startswith(("http", "www.")) or "/" in v)
        return ("website" if site >= 0.5 else "domain"), None, ratio, "content"
    if (ratio := share(lambda v: POSTAL_RE.fullmatch(v) is not None)) >= 0.8:
        return None, "postal_code", ratio, "content"
    if (ratio := share(is_nip)) >= 0.8:
        return None, "tax_id", ratio, "content"
    if (ratio := share(is_regon)) >= 0.8:
        return None, "regon", ratio, "content"
    if (ratio := share(is_phone)) >= 0.8:
        return "phone", None, ratio, "content"
    if context.lead_types and (ratio := share(lambda v: v.upper() in context.lead_types)) >= 0.8:
        return "lead_type", None, ratio, "content"
    if context.languages and (ratio := share(lambda v: len(v) == 2 and v.lower() in context.languages)) >= 0.8:
        return "language", None, ratio, "content"
    return None


def _digits(value: str) -> str:
    text = value.strip().upper()
    return re.sub(r"[\s\-.]", "", text.removeprefix("PL"))


def is_nip(value: str) -> bool:
    """A Polish tax id with a valid check digit (`PL` prefix, dashes and spaces allowed)."""
    digits = _digits(value)
    if not re.fullmatch(r"\d{10}", digits):
        return False
    total = sum(int(d) * w for d, w in zip(digits, (6, 5, 7, 2, 3, 4, 5, 6, 7), strict=False)) % 11
    return total != 10 and total == int(digits[9])


def is_regon(value: str) -> bool:
    digits = _digits(value)
    weights = {9: (8, 9, 2, 3, 4, 5, 6, 7), 14: (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)}.get(len(digits))
    if weights is None or not digits.isdigit():
        return False
    check = sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 % 10
    return check == int(digits[-1])


def is_phone(value: str) -> bool:
    return PHONE_RE.fullmatch(value) is not None and 7 <= sum(char.isdigit() for char in value) <= 15


def _resolve_conflicts(proposals: list[Proposal]) -> None:
    """One field, one column: the user's choice, then the higher confidence, then the earlier column wins. A full
    name and a first / last name column are one conflict too; the full name gives way unless only it is the user's."""
    winners: dict[str, Proposal] = {}
    for proposal in sorted(proposals, key=_priority):
        if proposal.field == IGNORE:
            continue
        rival = winners.get(proposal.field)
        if rival is not None:
            _lose(proposal, rival)
        else:
            winners[proposal.field] = proposal
    full = winners.get("full_name")
    parts = [winners[name] for name in sorted(NAME_PARTS) if name in winners]
    if full is None or not parts:
        return
    if full.source == "user" and all(part.source != "user" for part in parts):
        for part in parts:
            _lose(part, full)
    else:
        _lose(full, parts[0])


def _priority(proposal: Proposal) -> tuple[bool, float, int]:
    return proposal.source != "user", -proposal.confidence, proposal.index


def _lose(proposal: Proposal, winner: Proposal) -> None:
    proposal.field, proposal.reason, proposal.conflict_with = IGNORE, "duplicate_field", winner.name


# --- the user's mapping ----------------------------------------------------------------------------------------------


def parse_mapping(raw: str | None) -> dict[str, str] | None:
    """The multipart `mapping`: absent → None, else a JSON object `{column: field}`."""
    if raw is None or raw == "":
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise MappingError("invalid_json", 'The mapping must be a JSON object {"column": "field"}.')
    return value


def check_names(table: Table, keys: set[str], mapping: dict[str, str]) -> None:
    for column, target in mapping.items():
        if column not in table.columns:
            raise MappingError("unknown_column", f'Unknown column "{column}".')
        if target != IGNORE and target not in keys:
            raise MappingError("unknown_field", f'Column "{column}": unknown field "{target}".')


def check_complete(mapping: dict[str, str]) -> None:
    """What an import refuses and a preview only warns about."""
    chosen: dict[str, str] = {}
    for column, target in mapping.items():
        if target == IGNORE:
            continue
        if target in chosen:
            raise MappingError("duplicate_field", f'Field "{target}" is chosen for "{chosen[target]}" and "{column}".')
        chosen[target] = column
    if "full_name" in chosen and NAME_PARTS & chosen.keys():
        raise MappingError("duplicate_field", "Full name cannot be imported together with first or last name.")
    if not COMPANY_KEYS & chosen.keys():
        raise MappingError("no_company_key", "Map at least one column to domain, website or email.")


def warnings(mapping: dict[str, str]) -> list[dict[str, str]]:
    chosen = {target for target in mapping.values() if target != IGNORE}
    found = []
    if not COMPANY_KEYS & chosen:
        found.append(
            {"code": "no_company_key", "message": "No column maps to domain, website or email: every row is skipped."}
        )
    if "company_name" not in chosen:
        found.append({"code": "no_company_name", "message": "No column maps to the company name: the domain is used."})
    return found


# --- rows ------------------------------------------------------------------------------------------------------------


def split_name(value: str) -> tuple[str, str]:
    """`Jan Kowalski` → (Jan, Kowalski); `Kowalski, Jan` → (Jan, Kowalski); one word is the first name."""
    text = " ".join(value.split())
    if "," in text:
        last, first = (part.strip() for part in text.split(",", 1))
        return first, last
    first, _, last = text.partition(" ")
    return first, last


def mapped_row(row: list[str], mapping: list[tuple[int, str]]) -> dict[str, Any]:
    """A table row in the shape `connectors.csv.parse_rows` yields."""
    out: dict[str, Any] = dict.fromkeys(CSV_COLUMNS, "")
    out["attributes"], out["contact_attributes"] = {}, {}
    for index, target in mapping:
        value = row[index]
        if target == "full_name":
            out["first_name"], out["last_name"] = split_name(value)
            continue
        prefix = next((p for p in sorted(ATTRIBUTE_PREFIXES, key=len, reverse=True) if target.startswith(p)), None)
        if prefix is not None:
            out[ATTRIBUTE_PREFIXES[prefix]][target.removeprefix(prefix)] = value
        else:
            out[target] = value
    return out


def _pairs(table: Table, mapping: dict[str, str]) -> list[tuple[int, str]]:
    return [(index, mapping[name]) for index, name in enumerate(table.columns) if mapping.get(name, IGNORE) != IGNORE]


def _flat_values(row: dict[str, Any]) -> dict[str, str]:
    values = {key: value for key, value in row.items() if isinstance(value, str) and value}
    for prefix, target in ATTRIBUTE_PREFIXES.items():
        values.update({f"{prefix}{key}": value for key, value in row[target].items() if value})
    return values


def to_csv(table: Table, mapping: dict[str, str]) -> str:
    """The table as the import's CSV: `CSV_COLUMNS`, then the `attr.<key>` columns the mapping uses."""
    pairs = _pairs(table, mapping)
    extra = [target for _, target in pairs if target.startswith(tuple(ATTRIBUTE_PREFIXES))]
    header = [*CSV_COLUMNS, *extra]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in table.rows:
        flat = _flat_values(mapped_row(row, pairs))
        writer.writerow([flat.get(column, "") for column in header])
    return buffer.getvalue()


def identity_csv(table: Table) -> str:
    """The table back as a comma CSV under its own header — the old exact-name import reads it unchanged."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(table.columns)
    writer.writerows(table.rows)
    return buffer.getvalue()


def _skip_reason(row: dict[str, Any], lookups: Any) -> str | None:
    try:
        import_service.normalise_row(row, lookups)
    except import_service.SkipRow as reason:
        return str(reason)
    return None


def _preview_lookups(channel: Channel) -> Any:
    try:
        return import_service.build_lookups(channel)
    except stage_service.NoStages:
        return None


# --- the two requests ------------------------------------------------------------------------------------------------


def preview(channel: Channel, filename: str, data: bytes, raw_mapping: str | None = None) -> dict[str, Any]:
    """The proposal, the first rows after it and the warnings — nothing is written."""
    table = read_table(filename, data)
    context = _context(channel)
    user = parse_mapping(raw_mapping)
    if user:
        check_names(table, context.keys, user)
    proposals = _propose(table, context, user or {})
    mapping = {p.name: p.field for p in proposals}
    pairs = _pairs(table, mapping)
    lookups = _preview_lookups(channel)
    rows = []
    for number, row in enumerate(table.rows[:PREVIEW_ROWS], start=2):
        values = mapped_row(row, pairs)
        reason = _skip_reason(values, lookups) if lookups is not None else None
        rows.append({"row": number, "values": _flat_values(values), "skip_reason": reason})
    return {
        "filename": filename,
        "format": table.format,
        "encoding": table.encoding,
        "delimiter": table.delimiter,
        "sheet": table.sheet,
        "row_count": len(table.rows),
        "columns": [p.dump() for p in proposals],
        "rows": rows,
        "warnings": warnings(mapping),
        "fields": context.catalogue,
    }


def prepare_import(channel: Channel, filename: str, data: bytes, raw_mapping: str | None) -> Prepared:
    """The upload as the import's CSV. Without `mapping` the old exact-name import: a UTF-8 file is passed through
    as it is, any other readable file (xlsx, cp1250, `;`) is rewritten under its own header."""
    mapping = parse_mapping(raw_mapping)
    if mapping is None:
        return Prepared(_legacy_content(filename, data), [], [])
    table = read_table(filename, data)
    context = _context(channel)
    check_names(table, context.keys, mapping)
    resolved = {name: mapping.get(name, IGNORE) for name in table.columns}
    check_complete(resolved)
    labels = {item["key"]: item["label"] for item in context.catalogue}
    report = [
        {
            "column": name[: import_service.HEADER_MAX_LENGTH],
            "field": resolved[name],
            "label": labels.get(resolved[name]),
        }
        for name in table.columns
    ]
    ignored = [name[: import_service.HEADER_MAX_LENGTH] for name in table.columns if resolved[name] == IGNORE]
    return Prepared(to_csv(table, resolved), report, ignored[: leads_settings.LEADS_IMPORT_REPORT_MAX])


def _legacy_content(filename: str, data: bytes) -> str:
    if not data.startswith(ZIP_MAGIC):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = None
        if text is not None and _reads_as_comma_csv(text):
            check_csv_size(text)
            return text
    return identity_csv(read_table(filename, data))


def _reads_as_comma_csv(text: str) -> bool:
    header = next(csv.reader(io.StringIO(text)), [])
    names = {name.strip() for name in header}
    return len(header) > 1 or bool(names & {*CSV_COLUMNS, *COLUMN_ALIASES.values()}) or not text.strip()
