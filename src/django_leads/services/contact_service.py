# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Contacts: dedup by `(company, normalised email)`; a match fills empty fields only. Email is set once, then immutable.

`legal_basis` is never filled like the other fields: it changes only with a `legal_basis` Activity
(old → new, source, consent_ref), and a basis that differs from a recorded one is logged as a conflict."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django_agreements.enums import LegalBasis
from django_communicator.models import Thread

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel, Company, Contact
from django_leads.services import activity_service, retention_service
from django_leads.services.company_service import fill_empty
from django_leads.services.outreach_service import subject_ref
from django_leads.utils.emails import normalize_email

FILL_FIELDS = ("first_name", "last_name", "job_title", "phone", "language")
EDITABLE_FIELDS = frozenset(
    {"email", "first_name", "last_name", "job_title", "phone", "language", "is_primary", "legal_basis"}
)
# Timeline entries that mean outreach reached the contact (or the contact answered it).
OUTREACH_KINDS = (ActivityKind.DRAFT, ActivityKind.SENT, ActivityKind.REPLY, ActivityKind.BOUNCE, ActivityKind.OPTOUT)


class ContactExists(Exception):
    """The company already has a contact with this email."""


class EmailImmutable(ValueError):
    """A contact without an email may get one; a set email never changes."""


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


def admin_consent_ref(actor: str, basis: str | None, consent_ref: str | None) -> str:
    """`admin:<actor>` — plus where consent was given for `consent`, which without it stays empty (refused)."""
    if basis != LegalBasis.CONSENT:
        return f"admin:{actor}"
    return f"admin:{actor}:{consent_ref}" if consent_ref else ""


def create_contact(company: Company, row: dict[str, Any], *, actor: str, consent_ref: str | None = None) -> Contact:
    contact = build_contact(company, row)
    contact.is_primary = bool(row.get("is_primary"))
    try:
        with transaction.atomic():
            contact.save()
            _keep_one_primary(contact)
    except IntegrityError:
        raise ContactExists(f"contact {contact.email} already exists") from None
    activity_service.record(company, ActivityKind.NOTE, "contact created", contact=contact, actor=actor)
    if basis := row.get("legal_basis"):
        ref = admin_consent_ref(actor, basis, consent_ref)
        set_legal_basis(contact, basis, source="admin", consent_ref=ref, actor=actor)
    return contact


def update_contact(contact: Contact, updates: dict[str, Any], *, actor: str, consent_ref: str | None = None) -> Contact:
    invalid = set(updates) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
    if "email" in updates:
        updates = {**updates, "email": _first_email(contact, updates["email"])}
    fields = {field: value for field, value in updates.items() if field != "legal_basis"}
    for field, value in fields.items():
        setattr(contact, field, value)
    try:
        with transaction.atomic():
            contact.save(update_fields=[*fields, "modified_at"])
            _keep_one_primary(contact)
    except IntegrityError:
        raise ContactExists(f"contact {contact.email} already exists") from None
    if "legal_basis" in updates:
        basis = updates["legal_basis"]
        ref = admin_consent_ref(actor, basis, consent_ref)
        set_legal_basis(contact, basis, source="admin", consent_ref=ref, actor=actor)
    return contact


def _first_email(contact: Contact, email: str) -> str:
    if contact.email:
        raise EmailImmutable("email is immutable once set")
    return normalize_email(email)


def _keep_one_primary(contact: Contact) -> None:
    """One primary per company: a contact made primary unsets the others (caller's transaction)."""
    if contact.is_primary:
        others = Contact.objects.filter(company_id=contact.company_id, is_primary=True).exclude(pk=contact.pk)
        others.update(is_primary=False)


def is_used(contact: Contact) -> bool:
    """Whether outreach or GDPR history hangs on the contact, so removing it must keep the row (anonymise).

    Used means any of: it opted out (`opt_out_at`); consent was recorded (`legal_basis` consent or a `consent_ref`);
    it has an outreach Activity (draft, sent, reply, bounce, opt-out); communicator holds a thread to its email under
    the company's `subject_ref` (messages live in threads, so a thread covers them). An anonymised contact is used.
    """
    if contact.anonymised_at or contact.opt_out_at or contact.consent_ref is not None:
        return True
    if contact.legal_basis == LegalBasis.CONSENT:
        return True
    if contact.activities.filter(kind__in=OUTREACH_KINDS).exists():
        return True
    if not contact.email:
        return False
    threads = Thread.objects.filter(subject_ref=subject_ref(contact.company), recipient_email__iexact=contact.email)
    return threads.exists()


def remove_contact(contact: Contact, *, actor: str) -> Contact | None:
    """Remove a contact: deleted when never used (returns None, a `contact removed` note stays on the timeline),
    anonymised when used (`retention_service.anonymise_contact`: personal data blanked, history kept; returns the row).

    Company, then contact are locked — the order of `outreach_service.request_draft` — so no draft can start between
    the check and the delete. Removing the primary promotes nobody."""
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=contact.company_id)
        locked = Contact.objects.select_for_update().select_related("company").get(pk=contact.pk)
        if is_used(locked):
            return retention_service.anonymise_contact(locked, actor=actor)
        activity_service.record(locked.company, ActivityKind.NOTE, "contact removed", actor=actor)
        locked.delete()
    return None


def list_contacts(channel: Channel, company_id: int | None = None) -> QuerySet[Contact]:
    contacts = Contact.objects.filter(company__channel=channel).select_related("language").order_by("id")
    return contacts.filter(company_id=company_id) if company_id else contacts
