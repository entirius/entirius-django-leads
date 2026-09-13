# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Won seam: link an existing accounts Customer by the primary contact's email (accounts is a soft dependency)."""

import functools
import logging

from django_leads.enums import ActivityKind
from django_leads.models import Company
from django_leads.services import activity_service

logger = logging.getLogger(__name__)


class NoCustomer(Exception):
    """No Customer owns the email of the company's primary contact."""


@functools.cache
def accounts_available() -> bool:
    try:
        from django_accounts.models import Customer  # noqa: F401
    except (ImportError, RuntimeError):
        logger.info("django_accounts not installed — create-customer is disabled")
        return False
    return True


def link_customer(company: Company, *, actor: str) -> str:
    contact = company.contacts.exclude(email="").order_by("-is_primary", "pk").first()
    uid = find_customer_uid(contact.email) if contact else None
    if uid is None:
        raise NoCustomer(f"no customer for company {company.pk}")
    company.customer_uid = uid
    company.save(update_fields=["customer_uid", "modified_at"])
    activity_service.record(company, ActivityKind.NOTE, f"linked customer {uid}", actor=actor)
    return str(uid)


def find_customer_uid(email: str):
    """`Customer.email` is a property returning an allauth EmailAddress — match through EmailAddress itself."""
    from allauth.account.models import EmailAddress
    from django_accounts.models import Customer

    address = EmailAddress.objects.filter(email__iexact=email).select_related("user").first()
    customer = Customer.objects.filter(user=address.user).first() if address else None
    return customer.uid if customer else None
