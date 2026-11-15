# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Module settings — host overrides via Django settings of the same name, read at import."""

from django.conf import settings

QUEUE_DEFAULT = getattr(settings, "PROSPECTING_QUEUE_DEFAULT", "prospecting_default")

# Where people are found: "toolbox" (public search through the AI toolbox) or "fixed" (the same fixed candidates
# for every company, no network — for test stacks), or a dotted path to a class with `find_people()`.
PROSPECTING_SEARCH_PROVIDER = getattr(settings, "PROSPECTING_SEARCH_PROVIDER", "toolbox")
# The toolbox model of the public search (required for the "toolbox" provider).
PROSPECTING_SEARCH_MODEL = getattr(settings, "PROSPECTING_SEARCH_MODEL", "")
# Who confirms a mailbox without sending: "stub" (rules on the address, no network — test stacks only) or a dotted
# path to a class with `check(email)`. The real service is a decision of iteration 2.
PROSPECTING_ADDRESS_TESTER = getattr(settings, "PROSPECTING_ADDRESS_TESTER", "stub")

# Each of the four checks is tried at most this many searches; still missing after that = "no information".
PROSPECTING_MAX_ATTEMPTS = getattr(settings, "PROSPECTING_MAX_ATTEMPTS", 3)
# At most this many searches per company (any reason, all time) and provider calls per channel and day.
PROSPECTING_COMPANY_SEARCH_LIMIT = getattr(settings, "PROSPECTING_COMPANY_SEARCH_LIMIT", 3)
PROSPECTING_DAILY_BUDGET = getattr(settings, "PROSPECTING_DAILY_BUDGET", 100)
# At most this many candidates are kept per search.
PROSPECTING_MAX_CANDIDATES = getattr(settings, "PROSPECTING_MAX_CANDIDATES", 5)
# Possible addresses tried per person.
PROSPECTING_MAX_ADDRESSES = getattr(settings, "PROSPECTING_MAX_ADDRESSES", 3)
# A queued search untouched for this long lost its worker: it is failed and no longer blocks a new search.
PROSPECTING_QUEUED_STALE_MINUTES = getattr(settings, "PROSPECTING_QUEUED_STALE_MINUTES", 60)
