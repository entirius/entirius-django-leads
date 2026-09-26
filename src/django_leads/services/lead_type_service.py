# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Lead types of a channel: CRUD and the one answer to "is this code a lead type here?".

`UNKNOWN` is built in (never a row) and always valid; a company keeps the code as a string, so a type that becomes
inactive still reads on its companies but is no longer offered, filtered on or guessed.
"""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_leads.enums import UNKNOWN_LEAD_TYPE
from django_leads.models import Channel, Company, LeadType

CREATE_FIELDS = frozenset({"code", "label", "order", "is_active"})
# The code is what companies and templates hold — renaming it would orphan both, so it is fixed after create.
UPDATE_FIELDS = frozenset({"label", "order", "is_active"})


class LeadTypeExists(Exception):
    """The channel already has a lead type with this code."""


class LeadTypeInUse(Exception):
    """Companies of the channel still carry the code."""


class UnknownLeadType(ValueError):
    """The code is not an active lead type of the channel."""


def list_lead_types(channel: Channel) -> QuerySet[LeadType]:
    return LeadType.objects.filter(channel=channel).order_by("order", "code")


def active_codes(channel: Channel) -> frozenset[str]:
    """Codes a company of the channel may carry: the active lead types plus the built-in `UNKNOWN`."""
    codes = list_lead_types(channel).filter(is_active=True).values_list("code", flat=True)
    return frozenset({*codes, UNKNOWN_LEAD_TYPE})


def check_code(channel: Channel, code: str) -> str:
    """The code itself when the channel accepts it; raises `UnknownLeadType` otherwise."""
    if code not in active_codes(channel):
        raise UnknownLeadType(f"lead_type {code!r} is not an active lead type of channel {channel.idx}")
    return code


def create_lead_type(channel: Channel, fields: dict[str, Any]) -> LeadType:
    _check_fields(fields, CREATE_FIELDS)
    if fields.get("code") == UNKNOWN_LEAD_TYPE:
        raise LeadTypeExists(f"{UNKNOWN_LEAD_TYPE} is built in")
    try:
        with transaction.atomic():
            return LeadType.objects.create(channel=channel, **fields)
    except IntegrityError:
        raise LeadTypeExists(f"lead type {fields.get('code')!r} already exists") from None


def update_lead_type(lead_type: LeadType, updates: dict[str, Any]) -> LeadType:
    _check_fields(updates, UPDATE_FIELDS)
    for field, value in updates.items():
        setattr(lead_type, field, value)
    lead_type.save(update_fields=[*updates, "modified_at"])
    return lead_type


def delete_lead_type(lead_type: LeadType) -> None:
    if Company.objects.filter(channel_id=lead_type.channel_id, lead_type=lead_type.code).exists():
        raise LeadTypeInUse(f"companies still carry lead type {lead_type.code!r} — retype them or deactivate it")
    lead_type.delete()


def _check_fields(fields: dict[str, Any], allowed: frozenset[str]) -> None:
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
