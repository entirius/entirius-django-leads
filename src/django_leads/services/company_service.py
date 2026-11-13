# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Companies: dedup by `(channel, registrable domain)`; a match fills empty fields only."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OrderBy, OuterRef, Q, QuerySet
from django.utils import timezone

from django_leads.enums import UNKNOWN_LEAD_TYPE, ActivityKind, AttributeEntity, AttributeKind
from django_leads.models import Activity, Channel, Company, Stage
from django_leads.services import (
    activity_service,
    attribute_service,
    lead_type_service,
    stage_service,
    suppression_service,
)

FILL_FIELDS = ("name", "website", "lead_type", "industry", "origin")
# `do_not_contact` has its own writer: `set_do_not_contact`.
EDITABLE_FIELDS = frozenset({"name", "website", "lead_type", "industry", "description", "external_ref", "origin"})
SORT_FIELDS = frozenset({"name", "domain", "stage_entered_at", "last_activity_at", "score_points"})
# `attributes` is never set from a dict: create / patch go through `attribute_service`.
ATTRIBUTES = "attributes"
LIST_FILTER_KINDS = frozenset({AttributeKind.SELECT, AttributeKind.BOOL, AttributeKind.TEXT})
EMPTY_VALUES = (None, "", UNKNOWN_LEAD_TYPE)


class CompanyExists(Exception):
    """The channel already has a company on this domain."""


def fill_empty(instance: Any, row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    """Copy row values into empty instance fields, and attribute keys the instance lacks; returns the filled field
    names (`attributes` once for any key). Never overwrites."""
    filled = [
        field for field in fields if getattr(instance, field) in EMPTY_VALUES and row.get(field) not in EMPTY_VALUES
    ]
    for field in filled:
        setattr(instance, field, row[field])
    return [*filled, *attribute_service.fill_missing(instance, row.get(ATTRIBUTES))]


def build_company(channel: Channel, row: dict[str, Any], stage: Stage) -> Company:
    """An unsaved company in the channel's first stage; `row["domain"]` is already registrable, `row["attributes"]`
    (optional) already cleaned."""
    values = {field: row[field] for field in FILL_FIELDS if row.get(field) not in (None, "")}
    values.setdefault("name", row["domain"])
    return Company(
        channel=channel,
        domain=row["domain"],
        source=row["source"],
        stage=stage,
        stage_entered_at=timezone.now(),
        attributes=row.get(ATTRIBUTES) or {},
        **values,
    )


def upsert_company(channel: Channel, row: dict[str, Any]) -> tuple[Company, bool]:
    """Race-safe: the insert runs in a savepoint; a concurrent insert of the same domain is re-read and merged.
    Writes no Activity — the caller records its own (import or form)."""
    companies = Company.objects.filter(channel=channel, domain=row["domain"])
    company = companies.first()
    if company is None:
        try:
            with transaction.atomic():
                company = build_company(channel, row, stage_service.first_stage(channel))
                company.save()
            return company, True
        except IntegrityError:
            company = companies.get()
    filled = fill_empty(company, row, FILL_FIELDS)
    if filled:
        company.save(update_fields=[*filled, "modified_at"])
    return company, False


def create_company(channel: Channel, row: dict[str, Any], *, actor: str) -> Company:
    """Manual create over the API — an existing domain is a conflict, never a silent match. The lead type must be an
    active one of the channel (`UnknownLeadType`); attributes are cleaned with required keys enforced
    (`InvalidAttribute`)."""
    lead_type_service.check_code(channel, row.get("lead_type") or UNKNOWN_LEAD_TYPE)
    attributes = attribute_service.initial(channel, AttributeEntity.COMPANY, row.get(ATTRIBUTES) or {})
    company = build_company(channel, {**row, ATTRIBUTES: attributes}, stage_service.first_stage(channel))
    try:
        with transaction.atomic():
            company.save()
    except IntegrityError:
        raise CompanyExists(f"company on {row['domain']} already exists") from None
    activity_service.record(company, ActivityKind.NOTE, "company created", actor=actor)
    return company


def update_company(company: Company, updates: dict[str, Any]) -> Company:
    """`attributes` is a partial patch merged by `attribute_service` (a null key is removed)."""
    updates = dict(updates)
    patch = updates.pop(ATTRIBUTES, None)
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
    if updates.get("lead_type", company.lead_type) != company.lead_type:  # a deactivated type stays editable around
        lead_type_service.check_code(company.channel, updates["lead_type"])
    for field, value in updates.items():
        setattr(company, field, value)
    with transaction.atomic():
        if patch is not None:
            channel = company.channel
            updates[ATTRIBUTES] = attribute_service.apply_locked(company, channel, AttributeEntity.COMPANY, patch)
        company.save(update_fields=[*updates, "modified_at"])
    return company


def set_do_not_contact(company: Company, value: bool, *, actor: str) -> Company:
    """The only writer of `do_not_contact`: on suppresses the company's domain in communicator, off removes that
    suppression. Setting the current value (as stored) changes nothing."""
    changed = Company.objects.filter(pk=company.pk, do_not_contact=not value)
    company.do_not_contact = value
    if not changed.update(do_not_contact=value, modified_at=timezone.now()):
        return company
    message = f"do not contact: {'on' if value else 'off'}"
    activity_service.record(company, ActivityKind.NOTE, message, actor=actor)
    if value:
        suppression_service.suppress_domain(company.channel, company.domain)
    else:
        suppression_service.release_domain(company.channel, company.domain)
    return company


def list_companies(
    channel: Channel,
    *,
    stage: str = "",
    search: str = "",
    sort: str = "name",
    lead_type: str = "",
    do_not_contact: bool | None = None,
    has_reply: bool | None = None,
    origin: str = "",
    segment: str = "",
    attributes: dict[str, str] | None = None,
) -> QuerySet[Company]:
    """`sort` must be validated against `SORT_FIELDS` (± prefix) by the caller's schema; `lead_type` must be an
    active lead type of the channel (`UnknownLeadType`); `attributes` keys must be list filters (`filter_attributes`).
    Never-scored companies sort last on `score_points` either way."""
    if sort.lstrip("-") not in SORT_FIELDS:
        raise ValueError(f"unknown sort {sort!r}")
    companies = Company.objects.filter(channel=channel).select_related("stage")
    if stage:
        companies = companies.filter(stage__key=stage)
    if search:
        companies = companies.filter(Q(name__icontains=search) | Q(domain__icontains=search))
    if origin:
        companies = companies.filter(origin=origin)
    if lead_type:
        lead_type_service.check_code(channel, lead_type)
    if segment:
        companies = companies.filter(score_segment=segment)
    if attributes:
        companies = filter_attributes(channel, companies, attributes)
    companies = filter_flags(companies, lead_type, do_not_contact, has_reply)
    return companies.order_by(_ordering(sort), "id")


def _ordering(sort: str) -> str | OrderBy:
    if sort.lstrip("-") != "score_points":
        return sort
    column = F("score_points")
    return column.desc(nulls_last=True) if sort.startswith("-") else column.asc(nulls_last=True)


def filter_attributes(channel: Channel, companies: QuerySet[Company], filters: dict[str, str]) -> QuerySet[Company]:
    """Exact match on active `show_in_list` attributes of kind select, bool or text, as a JSON containment lookup
    (served by the GIN index on `attributes`); anything else raises `InvalidAttribute`."""
    definitions = attribute_service.active_definitions(channel, AttributeEntity.COMPANY)
    for key, raw in filters.items():
        definition = definitions.get(key)
        if definition is None or not definition.show_in_list or definition.kind not in LIST_FILTER_KINDS:
            raise attribute_service.InvalidAttribute(key, "not a list filter of this channel")
        try:
            value = attribute_service.clean_value(definition.kind, definition.options, raw)
        except ValueError as error:
            raise attribute_service.InvalidAttribute(key, str(error)) from None
        companies = companies.filter(attributes__contains={key: value})
    return companies


def filter_flags(
    companies: QuerySet[Company], lead_type: str, do_not_contact: bool | None, has_reply: bool | None
) -> QuerySet[Company]:
    """`has_reply` is an `Exists()` subquery — one row per company, so counts and paging stay exact."""
    if lead_type:
        companies = companies.filter(lead_type=lead_type)
    if do_not_contact is not None:
        companies = companies.filter(do_not_contact=do_not_contact)
    if has_reply is not None:
        replies = Activity.objects.filter(company=OuterRef("pk"), kind=ActivityKind.REPLY)
        companies = companies.filter(Exists(replies)) if has_reply else companies.exclude(Exists(replies))
    return companies
