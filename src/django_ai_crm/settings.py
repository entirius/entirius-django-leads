# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Module settings, read lazily from Django settings on every access (`override_settings` works)."""

from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    # The toolbox model of the chat; empty = the chat understands only its short commands (no model call).
    "AI_CRM_MODEL": "",
    "AI_CRM_MAX_TOKENS": 1024,
    # At most this many actions of one model answer are run.
    "AI_CRM_MAX_ACTIONS": 5,
    # A proposal not confirmed within this many hours expires.
    "AI_CRM_PROPOSAL_TTL_HOURS": 24,
}


def get(name: str) -> Any:
    return getattr(settings, name, DEFAULTS[name])
