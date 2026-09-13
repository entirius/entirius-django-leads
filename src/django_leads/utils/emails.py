# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


def normalize_email(raw: str) -> str:
    """The contact dedup key: surrounding whitespace dropped, lower-cased."""
    return (raw or "").strip().lower()
