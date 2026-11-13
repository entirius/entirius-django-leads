# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Channel settings a person may change through the API."""

from typing import Any

from django_leads.models import Channel

EDITABLE_FIELDS = frozenset({"resume_days"})


def update_channel(channel: Channel, updates: dict[str, Any]) -> Channel:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_channel: {sorted(invalid)}")
    for field, value in updates.items():
        setattr(channel, field, value)
    channel.save(update_fields=[*updates, "modified_at"])
    return channel
