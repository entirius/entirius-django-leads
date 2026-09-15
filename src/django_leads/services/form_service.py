# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""contact_forms submissions → Company + Contact. Reads `django_contact_forms.Lead`, never writes it."""

import logging
from typing import Any

from django_agreements.enums import LegalBasis

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Channel, Contact
from django_leads.services import activity_service, company_service, contact_service, erased_address_service
from django_leads.utils.domains import email_domain, registrable_domain
from django_leads.utils.emails import normalize_email

logger = logging.getLogger(__name__)

ACTOR = "form"
CONSENT_VALUES = frozenset({"true", "1", "yes", "on", "y", "tak"})


def import_form_lead(lead: Any, channel_idx: str) -> Contact | None:
    """Upsert the submission's company and contact; `None` when the channel or a company domain is missing, or the
    address was erased or anonymised (nothing created). A submission already imported (its `form submission`
    Activity exists) is a no-op returning `None`."""
    channel = Channel.objects.filter(idx=channel_idx).first()
    if channel is None:
        logger.info("leads: no leads channel %s for contact_forms lead %s", channel_idx, lead.pk)
        return None
    if Activity.objects.filter(kind=ActivityKind.FORM, company__channel=channel, data__lead_id=lead.pk).exists():
        return None
    if erased_address_service.is_erased(lead.email):
        logger.info("leads: contact_forms lead %s skipped, %s", lead.pk, erased_address_service.SKIP_REASON)
        return None
    domain = form_domain(lead)
    if domain is None:
        logger.info("leads: contact_forms lead %s skipped, no company domain", lead.pk)
        return None
    company_row = {"domain": domain, "name": lead.company, "source": LeadSource.FORM}
    company, _ = company_service.upsert_company(channel, company_row)
    contact, _ = contact_service.upsert_contact(company, contact_row(lead))
    activity_service.record_many(form_activities(lead, contact))
    return contact


def form_activities(lead: Any, contact: Contact) -> list[Activity]:
    """The legal-basis outcome (saved on the contact) and the submission itself — FORM kinds only otherwise."""
    activities = [basis] if (basis := consent_activity(lead, contact)) else []
    if not contact.legal_basis:
        activities.append(_form_activity(contact, "no legal basis", {}))
    data = {"lead_id": lead.pk, "contact_form_id": lead.contact_form_id}
    return [*activities, _form_activity(contact, "form submission", data)]


def consent_activity(lead: Any, contact: Contact) -> Activity | None:
    if not has_consent(lead.raw_data):
        return None
    ref = f"form:{lead.pk}"
    activity = contact_service.propose_legal_basis(
        contact, LegalBasis.CONSENT, source="form", consent_ref=ref, actor=ACTOR
    )
    if activity is not None and activity.kind == ActivityKind.LEGAL_BASIS:
        contact.save(update_fields=["legal_basis", "modified_at"])
    return activity


def _form_activity(contact: Contact, message: str, data: dict) -> Activity:
    return Activity(
        company=contact.company, contact=contact, kind=ActivityKind.FORM, message=message, data=data, actor=ACTOR
    )


def form_domain(lead: Any) -> str | None:
    """The first website key of the body, else the email host; free-mail hosts and junk give `None`."""
    raw = lead.raw_data if isinstance(lead.raw_data, dict) else {}
    website = next((str(raw[key]) for key in leads_settings.LEADS_FORM_WEBSITE_KEYS if raw.get(key)), "")
    try:
        return registrable_domain(website)
    except ValueError:
        pass
    try:
        domain = email_domain(normalize_email(lead.email))
    except ValueError:
        return None
    return None if domain in leads_settings.LEADS_FREEMAIL_DOMAINS else domain


def contact_row(lead: Any) -> dict[str, Any]:
    first_name, _, last_name = (lead.name or "").strip().partition(" ")
    return {
        "email": lead.email,
        "first_name": first_name,
        "last_name": last_name.strip(),
        "phone": lead.phone,
        "language": lead.contact_form.language,
        "source": LeadSource.FORM,
    }


def has_consent(raw_data: Any) -> bool:
    raw = raw_data if isinstance(raw_data, dict) else {}
    return any(is_consent(raw.get(key)) for key in leads_settings.LEADS_FORM_CONSENT_KEYS)


def is_consent(value: Any) -> bool:
    """Only an explicit yes: `True`, `1` or a yes-word — HTML forms send `"false"`, `"0"`, `"off"` as strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in CONSENT_VALUES
