# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Retention: personal data of contacts at inactive companies is pseudonymised — rows are never deleted."""

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, StageKind
from django_leads.models import Activity, Channel, Company, Contact
from django_leads.services import activity_service, erased_address_service
from django_leads.services.outreach_service import subject_ref
from django_leads.signals import contact_anonymised
from django_leads.utils.emails import anonymised_address, email_hash

PERSONAL_FIELDS = ("first_name", "last_name", "job_title", "phone")


def select_inactive_contacts(channel: Channel, *, as_of: datetime) -> QuerySet[Contact]:
    """Contacts not yet anonymised at companies of the channel idle since the cutoff (never in a `won` stage),
    without an Activity of their own after it."""
    cutoff = as_of - timedelta(days=channel.retention_days or leads_settings.LEADS_RETENTION_DAYS)
    companies = Company.objects.filter(channel=channel, last_activity_at__lt=cutoff).exclude(stage__kind=StageKind.WON)
    recent = Activity.objects.filter(contact=OuterRef("pk"), created_at__gte=cutoff)
    contacts = Contact.objects.filter(company__in=companies, anonymised_at__isnull=True).exclude(Exists(recent))
    return contacts.select_related("company").order_by("pk")


def anonymise_contact(contact: Contact, *, actor: str = "retention") -> Contact:
    """Email → token (remembered in `ErasedAddress`), name, job title and phone cleared; Activity `anonymised` with the
    hash; `contact_anonymised` on commit. The row is re-read under a lock: a contact already anonymised (by a
    concurrent erase or retention run) is returned unchanged, so the Activity and the signal happen once."""
    with transaction.atomic():
        locked = Contact.objects.select_for_update(of=("self",)).select_related("company").get(pk=contact.pk)
        if locked.anonymised_at is None:
            _pseudonymise(locked, actor)
    return locked


def _pseudonymise(contact: Contact, actor: str) -> None:
    original = contact.email
    hashed = email_hash(original or f"contact:{contact.pk}")
    contact.email = anonymised_address(original) if original else ""
    for name in PERSONAL_FIELDS:
        setattr(contact, name, "")
    contact.anonymised_at = timezone.now()
    contact.save(update_fields=["email", *PERSONAL_FIELDS, "anonymised_at", "modified_at"])
    erased_address_service.remember(original)
    activity_service.record(
        contact.company, ActivityKind.ANONYMISED, "contact anonymised", contact=contact,
        data={"email_hash": hashed}, actor=actor,
    )  # fmt: skip
    signal_kwargs = {
        "email_hash": hashed,
        "anonymised_email": contact.email,
        "subject_ref": subject_ref(contact.company),
    }
    transaction.on_commit(lambda: contact_anonymised.send(sender=Contact, **signal_kwargs))


def anonymise_inactive(as_of: datetime) -> dict[str, int]:
    """Anonymised contacts per channel idx."""
    return {channel.idx: _anonymise_channel(channel, as_of) for channel in Channel.objects.order_by("idx")}


def _anonymise_channel(channel: Channel, as_of: datetime) -> int:
    contacts = list(select_inactive_contacts(channel, as_of=as_of))
    for contact in contacts:
        anonymise_contact(contact)
    return len(contacts)
