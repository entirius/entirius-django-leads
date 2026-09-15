# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The GDPR hooks of django_leads, exposed as `django_leads.gdpr.gdpr_export` / `gdpr_erase`."""

from typing import Any

from django.db import transaction
from django.db.models import Q, QuerySet

from django_leads.models import Activity, Company, Contact
from django_leads.services import erased_address_service, retention_service
from django_leads.utils.emails import anonymised_address, normalize_email

ERASED = "[erased]"


def contacts_of_email(email: str) -> QuerySet[Contact]:
    """Contacts with the address in every channel, including contacts already anonymised to its token. A blank
    address raises `ValueError` — it would select every contact without an email."""
    if not normalize_email(email):
        raise ValueError("an email address is required")
    return Contact.objects.filter(Q(email=normalize_email(email)) | Q(email=anonymised_address(email)))


def gdpr_export(email: str) -> dict[str, Any]:
    contacts = contacts_of_email(email)
    return {
        "Contact": list(contacts.values()),
        "Company": list(Company.objects.filter(pk__in=contacts.values("company_id")).values()),
        "Activity": list(Activity.objects.filter(contact__in=contacts).values()),
    }


def gdpr_erase(email: str) -> dict[str, int]:
    """The address remembered as erased (contact or not), contacts anonymised (actor `gdpr`, locked, once), then
    every Activity of those contacts scrubbed."""
    with transaction.atomic():
        contacts = list(contacts_of_email(email))
        erased_address_service.remember(email)
        for contact in contacts:
            retention_service.anonymise_contact(contact, actor="gdpr")
        activities = Activity.objects.filter(contact__in=contacts).update(message=ERASED, data={"erased": True})
    return {"contacts": len(contacts), "activities": activities}
