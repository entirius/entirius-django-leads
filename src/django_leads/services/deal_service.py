# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Deals: the only writer of `Deal` — open, update (field whitelist), move through the deal stages, delete.

Amounts are `Decimal` end to end, never negative, and never without a currency. The owner changes through
`owner_service.set_deal_owner`, the stage only through `transition`; `company`, `closed_at` are never set from a dict.
"""

from datetime import date
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone
from django_regional.models import Currency

from django_leads.enums import ActivityKind, AttributeEntity, DealStageKind
from django_leads.models import Channel, Company, Deal, DealStage
from django_leads.services import activity_service, attribute_service, deal_stage_service, owner_service, task_bridge
from django_leads.signals import deal_stage_changed
from django_leads.utils.refs import deal_ref

EDITABLE_FIELDS = frozenset(
    {
        "title",
        "contact_id",
        "value_one_off",
        "value_recurring",
        "recurring_period",
        "currency",
        "expected_close_date",
        "attributes",
    }
)
MONEY_FIELDS = ("value_one_off", "value_recurring")
NOT_NULL_FIELDS = ("title", "recurring_period")
DEAL_DELETED = "deal deleted"
ATTRIBUTES = "attributes"
# API sort name → model field; values and dates without a value sort last either way.
SORT_FIELDS = {
    "expected_close_date": "expected_close_date",
    "stage_entered_at": "stage_entered_at",
    "value_one_off": "value_one_off",
    "value_recurring": "value_recurring",
    "created": "created_at",
}
RELATED = ("company", "contact", "owner", "stage", "currency")


class InvalidDeal(ValueError):
    """A value the deal rules refuse; `field` names it."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


class DealClosed(Exception):
    """A won or lost deal cannot be deleted."""


def resolve_currency(code: str | None) -> Currency | None:
    """An ISO 4217 code of the platform's currencies; None clears."""
    if code is None:
        return None
    currency = Currency.objects.filter(iso3=code.upper()).first()
    if currency is None:
        raise InvalidDeal("currency", f"unknown currency {code!r}")
    return currency


def default_owner(company: Company, user: AbstractBaseUser) -> AbstractBaseUser | None:
    """The company's owner while active, else the creator when they can own deals."""
    if owner_service.alert_recipient_id(company) is not None:
        return company.owner
    return owner_service.default_owner(user)


@transaction.atomic
def create(company: Company, fields: dict[str, Any], *, owner: AbstractBaseUser | None, actor: str) -> Deal:
    """A deal in the channel's first open stage (`MissingDealStage` without one); currency defaults to the
    channel's. Activity `deal` "deal opened: <title>" on the company timeline."""
    channel = company.channel
    stage = deal_stage_service.first_of_kind(channel, DealStageKind.OPEN)
    deal = Deal(channel=channel, company=company, stage=stage, stage_entered_at=timezone.now(), owner=owner)
    deal.currency_id, deal.created_by = channel.default_currency_id, actor
    values = dict(fields)
    deal.attributes = attribute_service.initial(channel, AttributeEntity.DEAL, values.pop(ATTRIBUTES, None) or {})
    _assign(deal, values)
    deal.save()
    _record(deal, f"deal opened: {deal.title}", actor)
    return deal


@transaction.atomic
def update(deal: Deal, updates: dict[str, Any], *, actor: str) -> Deal:
    """Whitelisted fields only; `attributes` is merged (null removes a key). Activity `deal` "deal updated"."""
    if not updates:
        return deal
    values = dict(updates)
    patch = values.pop(ATTRIBUTES, None)
    _assign(deal, values)
    if patch is not None:
        attribute_service.apply_locked(deal, deal.channel, AttributeEntity.DEAL, patch)
    # Only the patched columns: a full save would write back a stage or owner a concurrent request just changed.
    deal.save(update_fields=[*values, *([ATTRIBUTES] if patch is not None else []), "modified_at"])
    _record(deal, f"deal updated: {deal.title}", actor, fields=sorted(updates))
    return deal


@transaction.atomic
def transition(deal: Deal, stage: DealStage, *, actor: str, lost_reason: str = "") -> Deal:
    """Any stage to any stage of the channel. Won / lost set `closed_at`, lost needs a reason, leaving a closed stage
    clears both. Activity `deal` "deal <from> -> <to>"; `deal_stage_changed` on commit. The same stage is a no-op."""
    if stage.channel_id != deal.channel_id:
        raise ValueError("deal stage belongs to another channel")
    lost = stage.kind == DealStageKind.LOST
    if lost and not lost_reason.strip():
        raise InvalidDeal("lost_reason", "is required to lose a deal")
    if stage.pk == deal.stage_id:
        return deal
    previous, now = deal.stage, timezone.now()
    deal.stage, deal.stage_entered_at = stage, now
    deal.closed_at = now if deal_stage_service.is_closed(stage) else None
    deal.lost_reason = lost_reason.strip() if lost else ""
    deal.save(update_fields=["stage", "stage_entered_at", "closed_at", "lost_reason", "modified_at"])
    _record(deal, f"deal {previous.key} -> {stage.key}", actor, **{"from": previous.key, "to": stage.key})
    transaction.on_commit(lambda: deal_stage_changed.send(sender=Deal, deal=deal, previous=previous, stage=stage))
    return deal


@transaction.atomic
def delete(deal: Deal, *, actor: str) -> None:
    """Open deals only (`DealClosed`); the timeline keeps "deal deleted: <title>" with the old id; the deal's open
    tasks are cancelled."""
    if deal_stage_service.is_closed(deal.stage):
        raise DealClosed(f"deal {deal.pk} is {deal.stage.kind} — only an open deal can be deleted")
    task_bridge.cancel_about(deal.channel.idx, deal_ref(deal), reason=DEAL_DELETED)
    data = {"deal_id": deal.pk}
    activity_service.record(deal.company, ActivityKind.DEAL, f"deal deleted: {deal.title}", data=data, actor=actor)
    deal.delete()


def list_deals(channel: Channel, *, sort: str = "-created", user_id: int | None = None, **filters: Any) -> QuerySet:
    """Filters: `stage` (key), `kind`, `owner` (me / none / id), `company` (id), `search` (title, company name or
    domain), `close_from` / `close_to` (expected close date). `sort` must be in `SORT_FIELDS` (± prefix)."""
    deals = Deal.objects.filter(channel=channel).select_related(*RELATED)
    deals = owner_service.filter_by_owner(deals, filters.pop("owner", ""), user_id)
    return _filter(deals, **filters).order_by(_order(sort), "-id")


def counts(company: Company) -> dict[str, int]:
    """Deals of the company per stage kind, one query."""
    kinds = {kind: Count("id", filter=Q(stage__kind=kind)) for kind in DealStageKind.values}
    return Deal.objects.filter(company=company).aggregate(**kinds)


def subject_label(deal: Deal) -> str:
    """How a task about the deal names it: "<company> — <title>"."""
    return f"{deal.company.name or deal.company.domain} — {deal.title}"


def _filter(
    deals: QuerySet,
    *,
    stage: str = "",
    kind: str = "",
    company: int | None = None,
    search: str = "",
    close_from: date | None = None,
    close_to: date | None = None,
) -> QuerySet:
    lookups = {
        "stage__key": stage,
        "stage__kind": kind,
        "company_id": company,
        "expected_close_date__gte": close_from,
        "expected_close_date__lte": close_to,
    }
    deals = deals.filter(**{lookup: value for lookup, value in lookups.items() if value not in ("", None)})
    if not search:
        return deals
    return deals.filter(
        Q(title__icontains=search) | Q(company__name__icontains=search) | Q(company__domain__icontains=search)
    )


def _order(sort: str):
    field = SORT_FIELDS.get(sort.lstrip("-"))
    if field is None:
        raise ValueError(f"unknown sort {sort!r}")
    return F(field).desc(nulls_last=True) if sort.startswith("-") else F(field).asc(nulls_last=True)


def _assign(deal: Deal, fields: dict[str, Any]) -> None:
    invalid = set(fields) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via deal_service: {sorted(invalid)}")
    for field, value in fields.items():
        if value is None and field in NOT_NULL_FIELDS:
            raise InvalidDeal(field, "may not be null")
        setattr(deal, field, _RESOLVERS.get(field, _same)(deal, value))
    _check_money(deal)


def _check_money(deal: Deal) -> None:
    for field in MONEY_FIELDS:
        value = getattr(deal, field)
        if value is not None and value < 0:
            raise InvalidDeal(field, "must not be negative")
        if value is not None and deal.currency_id is None:
            raise InvalidDeal("currency", "is required with a value")


def _contact_id(deal: Deal, contact_id: int | None) -> int | None:
    if contact_id is not None and not deal.company.contacts.filter(pk=contact_id).exists():
        raise InvalidDeal("contact_id", "not a contact of the deal's company")
    return contact_id


def _same(deal: Deal, value: Any) -> Any:
    return value


_RESOLVERS = {"contact_id": _contact_id, "currency": lambda deal, code: resolve_currency(code)}


def _record(deal: Deal, message: str, actor: str, **data: Any) -> None:
    data = {"deal_id": deal.pk, **data}
    activity_service.record(deal.company, ActivityKind.DEAL, message, deal=deal, data=data, actor=actor)
