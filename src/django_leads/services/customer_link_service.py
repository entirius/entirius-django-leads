# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Won seam: link an existing accounts Customer by the primary contact's email (accounts is a soft dependency)."""

from django_leads.enums import ActivityKind
from django_leads.models import Company
from django_leads.services import activity_service


class NoCustomer(Exception):
    """No Customer owns the email of the company's primary contact."""


def link_customer(company: Company, *, actor: str) -> str:
    contact = company.contacts.exclude(email="").order_by("-is_primary", "pk").first()
    uid = find_customer_uid(contact.email) if contact else None
    if uid is None:
        raise NoCustomer(f"no customer for company {company.pk}")
    company.customer_uid = uid
    company.save(update_fields=["customer_uid", "modified_at"])
    activity_service.record(company, ActivityKind.NOTE, f"linked customer {uid}", actor=actor)
    return str(uid)


def find_customer_name(uid) -> str:
    """Display name of a linked Customer — the company card shows it instead of the uid.
    Empty when accounts is absent or the customer is gone: the uid stays the source of truth."""
    if not uid:
        return ""
    try:
        from django_accounts.models import Customer
    except ImportError:
        return ""
    customer = Customer.objects.filter(uid=uid).select_related("user").first()
    return f"{customer.first_name} {customer.last_name}".strip() if customer else ""


def find_customer_uid(email: str):
    """`Customer.email` is a property returning an allauth EmailAddress — match through a verified EmailAddress only
    (an unverified address proves nothing about who owns the account)."""
    from allauth.account.models import EmailAddress
    from django_accounts.models import Customer

    address = EmailAddress.objects.filter(email__iexact=email, verified=True).select_related("user").first()
    customer = Customer.objects.filter(user=address.user).first() if address else None
    return customer.uid if customer else None
