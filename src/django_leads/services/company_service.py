# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Companies: dedup by `(channel, registrable domain)`; a match fills empty fields only."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from django_leads.enums import ActivityKind, CompanyType
from django_leads.models import Channel, Company, Stage
from django_leads.services import activity_service, stage_service

FILL_FIELDS = ("name", "website", "company_type", "industry")
EDITABLE_FIELDS = frozenset(
    {"name", "website", "company_type", "industry", "description", "do_not_contact", "external_ref"}
)
SORT_FIELDS = frozenset({"name", "domain", "stage_entered_at", "last_activity_at"})
EMPTY_VALUES = (None, "", CompanyType.UNKNOWN)


class CompanyExists(Exception):
    """The channel already has a company on this domain."""


def fill_empty(instance: Any, row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    """Copy row values into empty instance fields; returns the filled field names. Never overwrites."""
    filled = [
        field for field in fields if getattr(instance, field) in EMPTY_VALUES and row.get(field) not in EMPTY_VALUES
    ]
    for field in filled:
        setattr(instance, field, row[field])
    return filled


def build_company(channel: Channel, row: dict[str, Any], stage: Stage) -> Company:
    """An unsaved company in the channel's first stage; `row["domain"]` is already registrable."""
    values = {field: row[field] for field in FILL_FIELDS if row.get(field) not in (None, "")}
    values.setdefault("name", row["domain"])
    return Company(
        channel=channel,
        domain=row["domain"],
        source=row["source"],
        stage=stage,
        stage_entered_at=timezone.now(),
        **values,
    )


def upsert_company(channel: Channel, row: dict[str, Any], *, actor: str = "system") -> tuple[Company, bool]:
    company = Company.objects.filter(channel=channel, domain=row["domain"]).first()
    if company is None:
        company = build_company(channel, row, stage_service.first_stage(channel))
        company.save()
        return company, True
    filled = fill_empty(company, row, FILL_FIELDS)
    if filled:
        company.save(update_fields=[*filled, "modified_at"])
    activity_service.record(company, ActivityKind.IMPORT, "import matched", data={"filled": filled}, actor=actor)
    return company, False


def create_company(channel: Channel, row: dict[str, Any], *, actor: str) -> Company:
    """Manual create over the API — an existing domain is a conflict, never a silent match."""
    company = build_company(channel, row, stage_service.first_stage(channel))
    try:
        with transaction.atomic():
            company.save()
    except IntegrityError:
        raise CompanyExists(f"company on {row['domain']} already exists") from None
    activity_service.record(company, ActivityKind.NOTE, "company created", actor=actor)
    return company


def update_company(company: Company, updates: dict[str, Any]) -> Company:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
    for field, value in updates.items():
        setattr(company, field, value)
    company.save(update_fields=[*updates, "modified_at"])
    return company


def list_companies(channel: Channel, *, stage: str = "", search: str = "", sort: str = "name") -> QuerySet[Company]:
    """`sort` must be validated against `SORT_FIELDS` (± prefix) by the caller's schema."""
    if sort.lstrip("-") not in SORT_FIELDS:
        raise ValueError(f"unknown sort {sort!r}")
    companies = Company.objects.filter(channel=channel).select_related("stage")
    if stage:
        companies = companies.filter(stage__key=stage)
    if search:
        companies = companies.filter(Q(name__icontains=search) | Q(domain__icontains=search))
    return companies.order_by(sort, "id")
