# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Channel settings a person may change through the API; `default_currency` is an ISO 4217 code.

Every change that moves a value writes a `settings` entry into the channel history (old and new value per field).
"""

from typing import Any

from django.db import transaction

from django_leads.enums import ActivityKind
from django_leads.models import Channel
from django_leads.services import activity_service, deal_service

DEFAULT_CURRENCY = "default_currency"
POTENTIAL = "potential_settings"
EDITABLE_FIELDS = frozenset({"resume_days", "default_currency", "history_months", POTENTIAL})


def update_channel(channel: Channel, updates: dict[str, Any], *, actor: str = "system") -> Channel:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_channel: {sorted(invalid)}")
    before = {field: _value(channel, field) for field in updates}
    values = {
        field: deal_service.resolve_currency(value) if field == DEFAULT_CURRENCY else value
        for field, value in updates.items()
    }  # every value resolved before the first one is set: a bad one saves nothing
    with transaction.atomic():
        for field, value in values.items():
            setattr(channel, field, value)
        channel.save(update_fields=[*updates, "modified_at"])
        changes = _record_changes(channel, updates, before, actor)
        if POTENTIAL in changes:  # the thresholds moved: every company of the channel is recomputed
            from django_leads.tasks import refresh_channel_potential

            transaction.on_commit(lambda: refresh_channel_potential.delay(channel.pk))
    return channel


def _record_changes(channel: Channel, updates: dict[str, Any], before: dict[str, Any], actor: str) -> dict:
    changes = {
        field: {"from": before[field], "to": _value(channel, field)}
        for field in sorted(updates)
        if before[field] != _value(channel, field)
    }
    if changes:
        message = f"settings: {', '.join(changes)}"
        data = {"event": "channel_settings", "changes": changes}
        activity_service.record_channel(channel, ActivityKind.SETTINGS, message, data=data, actor=actor)
    return changes


def _value(channel: Channel, field: str) -> Any:
    if field == DEFAULT_CURRENCY:
        return channel.default_currency.iso3 if channel.default_currency_id else None
    return getattr(channel, field)
