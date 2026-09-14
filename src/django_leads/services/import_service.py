# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""CSV import: rows are normalised, deduplicated and written chunk by chunk (≤ 3 selects + bulk writes each: erased
addresses, companies, contacts).

Lifecycle: every chunk commits atomically together with the batch counters and `last_row_done`, so a retry
resumes after the last committed row. A row the bulk write rejects is retried alone in a savepoint and skipped
with a reason code; any other error fails the batch with an error code — a run never leaves it `running`.
The CSV is never stored: the report keeps row numbers and reason codes only."""

import csv
import itertools
import logging
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, DataError, IntegrityError, OperationalError, transaction
from django.db.models import Model, Q
from django_agreements.enums import LegalBasis
from django_regional.models import Language

from django_leads import settings as leads_settings
from django_leads.connectors.base import ImportFailed
from django_leads.connectors.csv import CsvConnector, parse_rows  # noqa: F401 — `parse_rows` kept importable here
from django_leads.enums import ActivityKind, CompanyType, ImportStatus, LeadSource
from django_leads.models import Activity, Channel, Company, Contact, ImportBatch, Stage
from django_leads.services import (
    activity_service,
    company_service,
    contact_service,
    erased_address_service,
    stage_service,
)
from django_leads.utils.domains import email_domain, registrable_domain
from django_leads.utils.emails import normalize_email

logger = logging.getLogger(__name__)

ERROR_CODES = {csv.Error: "csv_error", UnicodeDecodeError: "encoding_error", stage_service.NoStages: "no_stages"}


class SkipRow(ValueError):
    """The row is not imported; the message is the report reason code."""


@dataclass
class Lookups:
    """Per-batch data fetched once, outside the chunk query budget."""

    stage: Stage
    languages: dict[str, Language]


def create_batch(channel: Channel, filename: str, size_bytes: int, created_by: str) -> ImportBatch:
    return ImportBatch.objects.create(channel=channel, filename=filename, size_bytes=size_bytes, created_by=created_by)


def build_lookups(channel: Channel) -> Lookups:
    languages = {language.iso2.lower(): language for language in Language.objects.all()}
    return Lookups(stage=stage_service.first_stage(channel), languages=languages)


def upload_path(batch_id: int) -> Path:
    return Path(leads_settings.LEADS_IMPORT_TMP_DIR) / f"{batch_id}.csv"


def store_upload(batch_id: int, content: str) -> Path:
    """Write the upload to `<LEADS_IMPORT_TMP_DIR>/<batch_id>.csv`, readable by the owner only."""
    path = upload_path(batch_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(descriptor, "w", encoding="utf-8") as file:
        file.write(content)
    return path


def run_content(batch: ImportBatch, content: str) -> ImportBatch:
    """Synchronous run of an uploaded text through its temp file, deleted whatever the outcome."""
    path = store_upload(batch.pk, content)
    try:
        return run_file(batch, path)
    finally:
        path.unlink(missing_ok=True)


def run_upload(batch_id: int, *, legacy: bool = False) -> ImportBatch:
    """The queued run: the stored upload of the batch, deleted once the batch is done or failed. A message from
    before the id-only contract (`legacy`) fails the batch without touching its content. Only `OperationalError`
    propagates — the file stays for the retry."""
    batch = ImportBatch.objects.select_related("channel").get(pk=batch_id)
    path = upload_path(batch_id)
    finished = batch.status in (ImportStatus.DONE, ImportStatus.FAILED)
    if not finished and (legacy or not path.exists()):
        fail_batch(batch, "legacy_message" if legacy else "missing_file")
    elif not finished:
        run_file(batch, path)
    path.unlink(missing_ok=True)
    return batch


def give_up(batch_id: int, code: str) -> None:
    """After the last retry: fail the batch and drop its file. When the database is still down the batch is left
    to `sweep_service.fail_stale_batches`."""
    upload_path(batch_id).unlink(missing_ok=True)
    try:
        fail_batch(ImportBatch.objects.get(pk=batch_id), code)
    except DatabaseError:
        logger.error("leads: import batch %s not marked failed (%s), left for the stale sweep", batch_id, code)


def run_file(batch: ImportBatch, path: Path) -> ImportBatch:
    """Apply the file; a finished batch is left alone. Only `OperationalError` propagates (task retry)."""
    if batch.status in (ImportStatus.DONE, ImportStatus.FAILED):
        return batch
    batch.status = ImportStatus.RUNNING
    batch.save(update_fields=["status", "modified_at"])
    try:
        candidates = CsvConnector(path).fetch_candidates(batch.channel)
        apply_rows(batch, (asdict(candidate) for candidate in candidates))
    except OperationalError:
        raise
    except Exception as error:
        logger.warning("leads: import batch %s failed (%s)", batch.pk, type(error).__name__)
        return fail_batch(batch, error_code(error))
    batch.status = ImportStatus.DONE
    batch.save(update_fields=["status", "modified_at"])
    return batch


def apply_rows(batch: ImportBatch, rows: Iterable[dict[str, str]]) -> None:
    lookups = build_lookups(batch.channel)
    pending = ((line, row) for line, row in enumerate(rows, start=2) if line > batch.last_row_done)
    while chunk := list(itertools.islice(pending, leads_settings.LEADS_IMPORT_CHUNK_SIZE)):
        with transaction.atomic():
            apply_chunk(batch, chunk, lookups)


def error_code(error: Exception) -> str:
    if isinstance(error, ImportFailed):
        return str(error)
    return next((code for kind, code in ERROR_CODES.items() if isinstance(error, kind)), "internal_error")


def fail_batch(batch: ImportBatch, code: str) -> ImportBatch:
    """Counters and report as last committed, plus the file-level entry `{row: 0, reason: code}`."""
    batch.refresh_from_db()
    batch.status = ImportStatus.FAILED
    batch.report.append({"row": 0, "reason": code})
    batch.save(update_fields=["status", "report", "modified_at"])
    return batch


def apply_chunk(batch: ImportBatch, rows: list[tuple[int, dict[str, str]]], lookups: Lookups) -> None:
    """Upsert one chunk of `(line number, row)` in bulk; rows the database rejects are redone one by one."""
    writer = _ChunkWriter(batch, lookups)
    prepared = writer.prepare(rows)
    writer.prefetch(prepared)
    for line, company_row, contact_row in prepared:
        writer.apply(line, company_row, contact_row)
    try:
        with transaction.atomic():
            writer.flush()
    except (DataError, IntegrityError):
        writer.outcomes = [_apply_alone(batch, *row) for row in prepared]
    save_progress(batch, [*writer.skipped, *writer.outcomes], rows[-1][0])


def save_progress(batch: ImportBatch, outcomes: list[tuple[int, str, str]], last_line: int) -> None:
    """One batch update per chunk: counters, resume point and — only when a row was skipped — the capped report."""
    for _, action, _ in outcomes:
        setattr(batch, f"{action}_count", getattr(batch, f"{action}_count") + 1)
    room = max(leads_settings.LEADS_IMPORT_REPORT_MAX - len(batch.report), 0)
    skipped = [{"row": line, "reason": reason} for line, action, reason in outcomes if action == "skipped"]
    batch.report.extend(skipped[:room])
    batch.last_row_done, batch.row_count = last_line, last_line - 1
    fields = ["created_count", "matched_count", "skipped_count", "last_row_done", "row_count", "modified_at"]
    batch.save(update_fields=[*fields, "report"] if skipped[:room] else fields)


def normalise_row(raw: dict[str, str], languages: dict[str, Language]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """`(company row, contact row or None)`; raises `SkipRow` with the report reason."""
    email = _row_email(raw)
    legal_basis = raw["legal_basis"].lower() or None
    if legal_basis and legal_basis not in LegalBasis.values:
        raise SkipRow("invalid_legal_basis")
    company = {
        "domain": _row_domain(raw, email),
        "name": raw["company_name"],
        "website": raw["website"],
        "company_type": raw["company_type"].upper() if raw["company_type"].upper() in CompanyType.values else "",
        "industry": raw["industry"],
        "source": LeadSource.CSV,
    }
    _check_lengths(Company, company)
    if not (email or raw["first_name"] or raw["last_name"]):
        return company, None
    return company, _contact_row(raw, email, legal_basis, languages)


def _contact_row(raw: dict[str, str], email: str, legal_basis: str | None, languages: dict[str, Language]) -> dict:
    """A contact without email is kept when it has a name (L-08)."""
    contact = {key: raw[key] for key in ("first_name", "last_name", "job_title", "phone")}
    _check_lengths(Contact, {**contact, "email": email})
    language = languages.get(raw["language"].lower())
    return {**contact, "email": email, "language": language, "legal_basis": legal_basis, "source": LeadSource.CSV}


def _row_email(raw: dict[str, str]) -> str:
    email = normalize_email(raw["email"])
    if not email:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        raise SkipRow("invalid_email") from None
    return email


def _row_domain(raw: dict[str, str], email: str) -> str:
    source = raw["domain"] or raw["website"]
    if not source and not email:
        raise SkipRow("no_domain_no_email")
    try:
        domain = registrable_domain(source) if source else email_domain(email)
    except ValueError:
        raise SkipRow("invalid_domain") from None
    if not source and domain in leads_settings.LEADS_FREEMAIL_DOMAINS:
        raise SkipRow("freemail_no_domain")
    return domain


def _check_lengths(model: type[Model], values: dict[str, Any]) -> None:
    """A value longer than its column is a skipped row (`too_long:<field>`), never a database error."""
    for name, value in values.items():
        limit = model._meta.get_field(name).max_length
        if isinstance(value, str) and limit and len(value) > limit:
            raise SkipRow(f"too_long:{name}")


def contact_key(domain: str, email: str, first_name: str, last_name: str) -> tuple[str, str]:
    """Dedup key within a chunk: the email, or the name for a contact without email."""
    return domain, email or f"name:{first_name.casefold()} {last_name.casefold()}"


def _apply_alone(batch: ImportBatch, line: int, company_row: dict, contact_row: dict | None) -> tuple[int, str, str]:
    """The slow path of one row, in its own savepoint through the race-safe upserts."""
    try:
        with transaction.atomic():
            return _upsert_row(batch, line, company_row, contact_row)
    except DataError:
        return line, "skipped", "invalid_value"
    except IntegrityError:
        return line, "skipped", "conflict"


def _upsert_row(batch: ImportBatch, line: int, company_row: dict, contact_row: dict | None) -> tuple[int, str, str]:
    company, created = company_service.upsert_company(batch.channel, company_row)
    contact = contact_service.upsert_contact(company, contact_row)[0] if contact_row else None
    message, data = "import created" if created else "import matched", {"row": line}
    activities = [
        Activity(
            company=company,
            contact=contact,
            kind=ActivityKind.IMPORT,
            message=message,
            data=data,
            actor=f"import:{batch.pk}",
        )
    ]
    basis = contact_row and contact_row["legal_basis"]
    if basis and (activity := _propose_basis(batch, line, contact, basis)):
        contact.save(update_fields=["legal_basis", "modified_at"])
        activities.append(activity)
    activity_service.record_many(activities)
    return line, "created" if created else "matched", ""


def _propose_basis(batch: ImportBatch, line: int, contact: Contact, basis: str) -> Activity | None:
    ref = f"import:{batch.pk}:{line}"
    return contact_service.propose_legal_basis(
        contact, basis, source="import", consent_ref=ref, actor=f"import:{batch.pk}"
    )


@dataclass
class _ChunkWriter:
    batch: ImportBatch
    lookups: Lookups
    companies: dict[str, Company] = field(default_factory=dict)
    contacts: dict[tuple[str, str], Contact] = field(default_factory=dict)
    new: list[Any] = field(default_factory=list)
    dirty: list[Any] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    outcomes: list[tuple[int, str, str]] = field(default_factory=list)
    skipped: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def actor(self) -> str:
        return f"import:{self.batch.pk}"

    def prepare(self, rows: list[tuple[int, dict[str, str]]]) -> list[tuple[int, dict, dict | None]]:
        prepared = []
        for line, raw in rows:
            try:
                prepared.append((line, *normalise_row(raw, self.lookups.languages)))
            except SkipRow as reason:
                self.skipped.append((line, "skipped", str(reason)))
        return self._without_erased(prepared)

    def _without_erased(self, prepared: list[tuple[int, dict, dict | None]]) -> list[tuple[int, dict, dict | None]]:
        """A row whose email was erased or anonymised is skipped whole — no company, no contact (one select)."""
        erased = erased_address_service.erased_emails(
            contact_row["email"] for _, _, contact_row in prepared if contact_row
        )
        kept = []
        for line, company_row, contact_row in prepared:
            if contact_row and contact_row["email"] in erased:
                self.skipped.append((line, "skipped", erased_address_service.SKIP_REASON))
            else:
                kept.append((line, company_row, contact_row))
        return kept

    def prefetch(self, prepared: list[tuple[int, dict, dict | None]]) -> None:
        """The chunk's two selects: existing companies by domain, their contacts by email (or without one)."""
        domains = {company_row["domain"] for _, company_row, _ in prepared}
        emails = {contact_row["email"] for _, _, contact_row in prepared if contact_row}
        existing = Company.objects.filter(channel=self.batch.channel, domain__in=domains)
        self.companies = {company.domain: company for company in existing}
        by_id = {company.pk: company for company in self.companies.values()}
        by_email = Q(email__in=emails - {""}) | Q(email="")
        found = Contact.objects.filter(by_email, company__in=list(by_id)) if by_id and emails else []
        self.contacts = {contact_key(by_id[c.company_id].domain, c.email, c.first_name, c.last_name): c for c in found}

    def apply(self, line: int, company_row: dict, contact_row: dict | None) -> None:
        company, created = self._company(company_row)
        filled = [] if created else self._fill(company, company_row, company_service.FILL_FIELDS)
        contact = self._contact(company, contact_row, filled) if contact_row else None
        message = "import created" if created else "import matched"
        data = {"row": line, "filled": filled}
        self.activities.append(
            Activity(
                company=company, contact=contact, kind=ActivityKind.IMPORT, message=message, data=data, actor=self.actor
            )
        )
        if contact_row and contact_row["legal_basis"]:
            self._basis(line, contact, contact_row["legal_basis"])
        self.outcomes.append((line, "created" if created else "matched", ""))

    def flush(self) -> None:
        self._write(Company, company_service.FILL_FIELDS)
        self._write(Contact, (*contact_service.FILL_FIELDS, "legal_basis"))
        activity_service.record_many(self.activities)

    def _company(self, row: dict) -> tuple[Company, bool]:
        company = self.companies.get(row["domain"])
        if company is not None:
            return company, False
        company = company_service.build_company(self.batch.channel, row, self.lookups.stage)
        self.companies[row["domain"]] = company
        self.new.append(company)
        return company, True

    def _contact(self, company: Company, row: dict, filled: list[str]) -> Contact:
        key = contact_key(company.domain, row["email"], row["first_name"], row["last_name"])
        contact = self.contacts.get(key)
        if contact is None:
            contact = self.contacts[key] = contact_service.build_contact(company, row)
            self.new.append(contact)
            return contact
        filled.extend(self._fill(contact, row, contact_service.FILL_FIELDS))
        return contact

    def _basis(self, line: int, contact: Contact, basis: str) -> None:
        activity = _propose_basis(self.batch, line, contact, basis)
        if activity is None:
            return
        self.activities.append(activity)
        if activity.kind == ActivityKind.LEGAL_BASIS and contact.pk and contact not in self.dirty:
            self.dirty.append(contact)

    def _fill(self, instance: Any, row: dict, fields: tuple[str, ...]) -> list[str]:
        filled = company_service.fill_empty(instance, row, fields)
        if filled and instance.pk and instance not in self.dirty:
            self.dirty.append(instance)
        return filled

    def _write(self, model: type, fields: tuple[str, ...]) -> None:
        created = [instance for instance in self.new if isinstance(instance, model)]
        changed = [instance for instance in self.dirty if isinstance(instance, model)]
        if created:
            model.objects.bulk_create(created)
        if changed:
            model.objects.bulk_update(changed, fields)
