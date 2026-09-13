# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""contact_forms submissions → Company + Contact. Reads `django_contact_forms.Lead`, never writes it."""

import logging
from typing import Any

from django_agreements.enums import LegalBasis

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Channel, Contact
from django_leads.services import activity_service, company_service, contact_service
from django_leads.utils.domains import email_domain, registrable_domain
from django_leads.utils.emails import normalize_email

logger = logging.getLogger(__name__)

ACTOR = "form"


def import_form_lead(lead: Any, channel_idx: str) -> Contact | None:
    """Upsert the submission's company and contact; `None` when the channel or a company domain is missing."""
    channel = Channel.objects.filter(idx=channel_idx).first()
    if channel is None:
        logger.info("leads: no leads channel %s for contact_forms lead %s", channel_idx, lead.pk)
        return None
    domain = form_domain(lead)
    if domain is None:
        logger.info("leads: contact_forms lead %s skipped, no company domain", lead.pk)
        return None
    company_row = {"domain": domain, "name": lead.company, "source": LeadSource.FORM}
    company, _ = company_service.upsert_company(channel, company_row, actor=ACTOR)
    contact, _ = contact_service.upsert_contact(company, contact_row(lead), actor=ACTOR)
    if not has_consent(lead.raw_data):
        activity_service.record(company, ActivityKind.FORM, "no legal basis", contact=contact, actor=ACTOR)
    data = {"lead_id": lead.pk, "contact_form_id": lead.contact_form_id}
    activity_service.record(company, ActivityKind.FORM, "form submission", contact=contact, data=data, actor=ACTOR)
    return contact


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
        "legal_basis": LegalBasis.CONSENT if has_consent(lead.raw_data) else None,
        "source": LeadSource.FORM,
    }


def has_consent(raw_data: Any) -> bool:
    raw = raw_data if isinstance(raw_data, dict) else {}
    return any(raw.get(key) for key in leads_settings.LEADS_FORM_CONSENT_KEYS)
