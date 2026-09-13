# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""CSV import: rows are normalised, deduplicated and written chunk by chunk (≤ 2 selects + bulk writes each)."""

import csv
import io
import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from django_agreements.enums import LegalBasis
from django_regional.models import Language

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, CompanyType, ImportStatus, LeadSource
from django_leads.models import Activity, Channel, Company, Contact, ImportBatch, Stage
from django_leads.services import activity_service, company_service, contact_service, stage_service
from django_leads.utils.domains import email_domain, registrable_domain
from django_leads.utils.emails import normalize_email

CSV_COLUMNS = (
    "company_name",
    "domain",
    "website",
    "company_type",
    "industry",
    "first_name",
    "last_name",
    "email",
    "job_title",
    "language",
    "legal_basis",
    "phone",
)


class SkipRow(ValueError):
    """The row is not imported; the message is the report reason."""


@dataclass
class Lookups:
    """Per-batch data fetched once, outside the chunk query budget."""

    stage: Stage
    languages: dict[str, Language]


def create_batch(channel: Channel, filename: str, content: str, created_by: str) -> ImportBatch:
    return ImportBatch.objects.create(channel=channel, filename=filename, content=content, created_by=created_by)


def parse_rows(file: Iterable[str]) -> Iterator[dict[str, str]]:
    """Stream CSV rows as dicts of the known columns; the header row is required."""
    reader = csv.DictReader(file)
    if not reader.fieldnames or not set(CSV_COLUMNS) & {name.strip() for name in reader.fieldnames}:
        raise ValueError("missing CSV header")
    for raw in reader:
        yield {column: (raw.get(column) or "").strip() for column in CSV_COLUMNS}


def build_lookups(channel: Channel) -> Lookups:
    languages = {language.iso2.lower(): language for language in Language.objects.all()}
    return Lookups(stage=stage_service.first_stage(channel), languages=languages)


def run_batch(batch: ImportBatch) -> ImportBatch:
    """Apply the whole file; a redelivered finished batch is left alone."""
    if batch.status == ImportStatus.DONE:
        return batch
    _reset(batch)
    try:
        lookups = build_lookups(batch.channel)
        rows = enumerate(parse_rows(io.StringIO(batch.content)), start=2)
        while chunk := list(itertools.islice(rows, leads_settings.LEADS_IMPORT_CHUNK_SIZE)):
            apply_chunk(batch, chunk, lookups)
    except ValueError as error:
        batch.report.append({"row": 0, "action": "failed", "reason": str(error)})
        return _finish(batch, ImportStatus.FAILED)
    return _finish(batch, ImportStatus.DONE)


def apply_chunk(batch: ImportBatch, rows: list[tuple[int, dict[str, str]]], lookups: Lookups) -> None:
    """Upsert one chunk of `(line number, row)`; counts and report are saved at the end."""
    writer = _ChunkWriter(batch, lookups)
    prepared = writer.prepare(rows)
    writer.prefetch(prepared)
    for line, company_row, contact_row in prepared:
        writer.apply(line, company_row, contact_row)
    writer.flush()


def normalise_row(raw: dict[str, str], languages: dict[str, Language]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """`(company row, contact row or None)`; raises `SkipRow` with the report reason."""
    email = normalize_email(raw["email"])
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
    if not email:
        return company, None
    contact = {key: raw[key] for key in ("first_name", "last_name", "job_title", "phone")}
    language = languages.get(raw["language"].lower())
    return company, {
        **contact,
        "email": email,
        "language": language,
        "legal_basis": legal_basis,
        "source": LeadSource.CSV,
    }


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


@dataclass
class _ChunkWriter:
    batch: ImportBatch
    lookups: Lookups
    companies: dict[str, Company] = field(default_factory=dict)
    contacts: dict[tuple[str, str], Contact] = field(default_factory=dict)
    new: list[Any] = field(default_factory=list)
    dirty: list[Any] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)

    @property
    def actor(self) -> str:
        return f"import:{self.batch.pk}"

    def prepare(self, rows: list[tuple[int, dict[str, str]]]) -> list[tuple[int, dict, dict | None]]:
        prepared = []
        for line, raw in rows:
            try:
                prepared.append((line, *normalise_row(raw, self.lookups.languages)))
            except SkipRow as reason:
                self._report(line, "skipped", str(reason))
        return prepared

    def prefetch(self, prepared: list[tuple[int, dict, dict | None]]) -> None:
        """The chunk's two selects: existing companies by domain, their contacts by email."""
        domains = {company_row["domain"] for _, company_row, _ in prepared}
        emails = {contact_row["email"] for _, _, contact_row in prepared if contact_row}
        existing = Company.objects.filter(channel=self.batch.channel, domain__in=domains)
        self.companies = {company.domain: company for company in existing}
        by_id = {company.pk: company for company in self.companies.values()}
        found = Contact.objects.filter(company__in=list(by_id), email__in=emails) if by_id and emails else []
        self.contacts = {(by_id[contact.company_id].domain, contact.email): contact for contact in found}

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
        self._report(line, "created" if created else "matched", "")

    def flush(self) -> None:
        self._write(Company, company_service.FILL_FIELDS)
        self._write(Contact, contact_service.FILL_FIELDS)
        activity_service.record_many(self.activities)
        self.batch.save(update_fields=["created_count", "matched_count", "skipped_count", "report", "modified_at"])

    def _company(self, row: dict) -> tuple[Company, bool]:
        company = self.companies.get(row["domain"])
        if company is not None:
            return company, False
        company = company_service.build_company(self.batch.channel, row, self.lookups.stage)
        self.companies[row["domain"]] = company
        self.new.append(company)
        return company, True

    def _contact(self, company: Company, row: dict, filled: list[str]) -> Contact:
        key = (company.domain, row["email"])
        contact = self.contacts.get(key)
        if contact is None:
            contact = self.contacts[key] = contact_service.build_contact(company, row)
            self.new.append(contact)
            return contact
        filled.extend(self._fill(contact, row, contact_service.FILL_FIELDS))
        return contact

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

    def _report(self, line: int, action: str, reason: str) -> None:
        counter = f"{action}_count"
        setattr(self.batch, counter, getattr(self.batch, counter) + 1)
        self.batch.report.append({"row": line, "action": action, "reason": reason})


def _reset(batch: ImportBatch) -> None:
    batch.status, batch.report = ImportStatus.RUNNING, []
    batch.created_count = batch.matched_count = batch.skipped_count = 0
    batch.save()


def _finish(batch: ImportBatch, status: str) -> ImportBatch:
    batch.status = status
    batch.save(update_fields=["status", "report", "modified_at"])
    return batch
