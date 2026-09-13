# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Prompt rendering for the leads profiles — placeholders replaced verbatim, other braces left alone (JSON)."""

import json
from typing import Any

SUMMARY_MAX_BYTES = 8192


def render_prompt(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def json_summary(data: Any) -> str:
    """Compact JSON cut to `SUMMARY_MAX_BYTES` (a cut value is no longer valid JSON — the model reads it as text)."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return text.encode()[:SUMMARY_MAX_BYTES].decode(errors="ignore")
