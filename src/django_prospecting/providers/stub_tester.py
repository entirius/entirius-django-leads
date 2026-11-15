# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Address tester stand-in for test stacks: no network, rules on the address only. The real tester (an external
service, so that probes from our mail server never land it on a blocklist) is a decision of iteration 2.

- a domain starting with `catchall.` accepts everything → `catch_all`;
- a local part `first.last` (a dot between two words of 2+ letters) → `valid`;
- anything else → `invalid`."""

import re

from django_prospecting.enums import AddressVerdict

SOURCE = "stub address tester (no mail sent, no network)"
FIRST_DOT_LAST = re.compile(r"^[a-z]{2,}\.[a-z]{2,}$")


class StubTester:
    source = SOURCE

    def check(self, email: str) -> str:
        local, _, domain = email.lower().partition("@")
        if domain.startswith("catchall."):
            return AddressVerdict.CATCH_ALL
        return AddressVerdict.VALID if FIRST_DOT_LAST.match(local) else AddressVerdict.INVALID
