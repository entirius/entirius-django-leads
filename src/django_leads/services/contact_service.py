# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Contacts: dedup by `(company, normalised email)`; a match fills empty fields only. Email is immutable."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_leads.enums import ActivityKind
from django_leads.models import Channel, Company, Contact
from django_leads.services import activity_service
from django_leads.services.company_service import fill_empty
from django_leads.utils.emails import normalize_email

FILL_FIELDS = ("first_name", "last_name", "job_title", "phone", "language", "legal_basis")
EDITABLE_FIELDS = frozenset({"first_name", "last_name", "job_title", "phone", "language", "is_primary", "legal_basis"})


class ContactExists(Exception):
    """The company already has a contact with this email."""


def build_contact(company: Company, row: dict[str, Any]) -> Contact:
    values = {field: row[field] for field in FILL_FIELDS if row.get(field) not in (None, "")}
    return Contact(company=company, email=normalize_email(row.get("email", "")), source=row["source"], **values)


def upsert_contact(company: Company, row: dict[str, Any], *, actor: str = "system") -> tuple[Contact, bool]:
    email = normalize_email(row.get("email", ""))
    contact = Contact.objects.filter(company=company, email=email).first() if email else None
    if contact is None:
        contact = build_contact(company, row)
        contact.save()
        return contact, True
    filled = fill_empty(contact, row, FILL_FIELDS)
    if filled:
        contact.save(update_fields=[*filled, "modified_at"])
    data = {"filled": filled}
    activity_service.record(company, ActivityKind.IMPORT, "import matched", contact=contact, data=data, actor=actor)
    return contact, False


def create_contact(company: Company, row: dict[str, Any], *, actor: str) -> Contact:
    contact = build_contact(company, row)
    contact.is_primary = bool(row.get("is_primary"))
    try:
        with transaction.atomic():
            contact.save()
    except IntegrityError:
        raise ContactExists(f"contact {contact.email} already exists") from None
    activity_service.record(company, ActivityKind.NOTE, "contact created", contact=contact, actor=actor)
    return contact


def update_contact(contact: Contact, updates: dict[str, Any]) -> Contact:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
    for field, value in updates.items():
        setattr(contact, field, value)
    contact.save(update_fields=[*updates, "modified_at"])
    return contact


def list_contacts(channel: Channel, company_id: int | None = None) -> QuerySet[Contact]:
    contacts = Contact.objects.filter(company__channel=channel).select_related("language").order_by("id")
    return contacts.filter(company_id=company_id) if company_id else contacts
