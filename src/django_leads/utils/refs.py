# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""References other modules hold: `leads.Company:<int>` (communicator threads, tasks), `leads.Contact:<int>` (tasks)."""

import re

from django_leads.models import Company, Contact

SUBJECT_REF = re.compile(r"^leads\.Company:(\d+)$")
CONTACT_REF = re.compile(r"^leads\.Contact:(\d+)$")


def company_from_ref(subject_ref: str) -> Company | None:
    match = SUBJECT_REF.match(subject_ref or "")
    return Company.objects.select_related("channel").filter(pk=int(match.group(1))).first() if match else None


def contact_ref(contact: Contact) -> str:
    return f"leads.Contact:{contact.pk}"


def contact_from_ref(company: Company, ref: str) -> Contact | None:
    """The referenced contact when it belongs to the company; None for any other reference."""
    match = CONTACT_REF.match(ref or "")
    return company.contacts.filter(pk=int(match.group(1))).first() if match else None
