# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`subject_ref` of communicator threads: only `leads.Company:<int>` belongs to leads."""

import re

from django_leads.models import Company

SUBJECT_REF = re.compile(r"^leads\.Company:(\d+)$")


def company_from_ref(subject_ref: str) -> Company | None:
    match = SUBJECT_REF.match(subject_ref or "")
    return Company.objects.select_related("channel").filter(pk=int(match.group(1))).first() if match else None
