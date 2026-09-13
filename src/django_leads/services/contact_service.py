# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Contacts: dedup by `(company, normalised email)`; a match fills empty fields only. Email is immutable.

`legal_basis` is never filled like the other fields: it changes only with a `legal_basis` Activity
(old → new, source, consent_ref), and a basis that differs from a recorded one is logged as a conflict."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django_agreements.enums import LegalBasis

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel, Company, Contact
from django_leads.services import activity_service
from django_leads.services.company_service import fill_empty
from django_leads.utils.emails import normalize_email

FILL_FIELDS = ("first_name", "last_name", "job_title", "phone", "language")
EDITABLE_FIELDS = frozenset({"first_name", "last_name", "job_title", "phone", "language", "is_primary", "legal_basis"})


class ContactExists(Exception):
    """The company already has a contact with this email."""


class ConsentRefRequired(ValueError):
    """`consent` is recorded only with a reference to where it was given."""


def build_contact(company: Company, row: dict[str, Any]) -> Contact:
    values = {field: row[field] for field in FILL_FIELDS if row.get(field) not in (None, "")}
    return Contact(company=company, email=normalize_email(row.get("email", "")), source=row["source"], **values)


def find_contact(company: Company, row: dict[str, Any]) -> Contact | None:
    """By email; a contact without email is matched by its name within the company."""
    email = normalize_email(row.get("email", ""))
    if email:
        return Contact.objects.filter(company=company, email=email).first()
    names = {"first_name__iexact": row.get("first_name", ""), "last_name__iexact": row.get("last_name", "")}
    return Contact.objects.filter(company=company, email="", **names).first()


def upsert_contact(company: Company, row: dict[str, Any]) -> tuple[Contact, bool]:
    """Race-safe: the insert runs in a savepoint; a concurrent insert of the same email is re-read and merged."""
    contact = find_contact(company, row)
    if contact is None:
        try:
            with transaction.atomic():
                contact = build_contact(company, row)
                contact.save()
            return contact, True
        except IntegrityError:
            contact = find_contact(company, row)
            if contact is None:
                raise
    filled = fill_empty(contact, row, FILL_FIELDS)
    if filled:
        contact.save(update_fields=[*filled, "modified_at"])
    return contact, False


def propose_legal_basis(contact: Contact, basis: str, *, source: str, consent_ref: str, actor: str) -> Activity | None:
    """Import/form rule, unsaved: an empty basis is set (`legal_basis` Activity), a different recorded basis
    stays and a `legal_basis_conflict` Activity is returned, the same basis is a no-op (`None`)."""
    if contact.legal_basis == basis:
        return None
    data = {"from": contact.legal_basis, "to": basis, "source": source, "consent_ref": consent_ref}
    if contact.legal_basis:
        return _basis_activity(contact, ActivityKind.LEGAL_BASIS_CONFLICT, data, actor)
    return _change_basis(contact, basis, data, actor)


def set_legal_basis(contact: Contact, basis: str | None, *, source: str, consent_ref: str, actor: str) -> Contact:
    """The explicit change (operator): replaces the basis and records old → new."""
    if contact.legal_basis == basis:
        return contact
    data = {"from": contact.legal_basis, "to": basis, "source": source, "consent_ref": consent_ref}
    activity = _change_basis(contact, basis, data, actor)
    contact.save(update_fields=["legal_basis", "modified_at"])
    activity_service.record_many([activity])
    return contact


def _change_basis(contact: Contact, basis: str | None, data: dict, actor: str) -> Activity:
    if basis == LegalBasis.CONSENT and not data["consent_ref"]:
        raise ConsentRefRequired("consent needs a consent_ref")
    contact.legal_basis = basis
    return _basis_activity(contact, ActivityKind.LEGAL_BASIS, data, actor)


def _basis_activity(contact: Contact, kind: str, data: dict, actor: str) -> Activity:
    message = f"legal basis {data['from']} -> {data['to']}"
    return Activity(company=contact.company, contact=contact, kind=kind, message=message, data=data, actor=actor)


def create_contact(company: Company, row: dict[str, Any], *, actor: str) -> Contact:
    contact = build_contact(company, row)
    contact.is_primary = bool(row.get("is_primary"))
    try:
        with transaction.atomic():
            contact.save()
    except IntegrityError:
        raise ContactExists(f"contact {contact.email} already exists") from None
    activity_service.record(company, ActivityKind.NOTE, "contact created", contact=contact, actor=actor)
    if row.get("legal_basis"):
        set_legal_basis(contact, row["legal_basis"], source="admin", consent_ref=f"admin:{actor}", actor=actor)
    return contact


def update_contact(contact: Contact, updates: dict[str, Any], *, actor: str) -> Contact:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
    fields = {field: value for field, value in updates.items() if field != "legal_basis"}
    for field, value in fields.items():
        setattr(contact, field, value)
    contact.save(update_fields=[*fields, "modified_at"])
    if "legal_basis" in updates:
        basis = updates["legal_basis"]
        set_legal_basis(contact, basis, source="admin", consent_ref=f"admin:{actor}", actor=actor)
    return contact


def list_contacts(channel: Channel, company_id: int | None = None) -> QuerySet[Contact]:
    contacts = Contact.objects.filter(company__channel=channel).select_related("language").order_by("id")
    return contacts.filter(company_id=company_id) if company_id else contacts
