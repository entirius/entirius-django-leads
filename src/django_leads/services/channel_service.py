# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Channel settings a person may change through the API; `default_currency` is an ISO 4217 code."""

from typing import Any

from django_leads.models import Channel
from django_leads.services import deal_service

DEFAULT_CURRENCY = "default_currency"
EDITABLE_FIELDS = frozenset({"resume_days", "default_currency"})


def update_channel(channel: Channel, updates: dict[str, Any]) -> Channel:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_channel: {sorted(invalid)}")
    for field, value in updates.items():
        setattr(channel, field, deal_service.resolve_currency(value) if field == DEFAULT_CURRENCY else value)
    channel.save(update_fields=[*updates, "modified_at"])
    return channel
