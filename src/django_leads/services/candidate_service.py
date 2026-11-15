# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""New people for a company — the public face of leads towards a prospecting module.

- `look_for_another_person`: a company whose contact turned out to be the wrong person or opted out, and that has
  nobody else to mail, says so (`looking for another person` Activity, `contact_needed` on commit). Only that person is
  blocked, never the company; a do-not-contact company is not searched for.
- `accept_candidate`: a person a salesperson accepted becomes a contact (source `prospecting`, legal basis
  legitimate interest), so the outreach gate lets it be mailed. Leads never learns how the person was found."""

from django.db import transaction
from django_agreements.enums import LegalBasis

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Company
from django_leads.services import activity_service, erased_address_service, recipient_service
from django_leads.signals import contact_needed
from django_leads.utils.refs import company_from_ref, contact_ref

WRONG_PERSON = "wrong_person"
OPTED_OUT = "opted_out"
LOOKING_MESSAGE = "looking for another person"
EVENT = "contact_needed"


def needs_another_person(company: Company) -> bool:
    """No contact of the company passes the outreach gate, and the company itself may be contacted."""
    if company.do_not_contact:
        return False
    return not any(recipient_service.eligible(contact) for contact in recipient_service.candidates(company))


def look_for_another_person(company: Company, *, reason: str) -> bool:
    """True when the company now waits for another person (Activity recorded, signal sent on commit)."""
    if not needs_another_person(company):
        return False
    data = {"event": EVENT, "reason": reason}
    activity_service.record(company, ActivityKind.NOTE, LOOKING_MESSAGE, data=data)
    fields = {"subject_ref": f"leads.Company:{company.pk}", "channel_idx": company.channel.idx}
    fields.update(company_name=company.name or company.domain, domain=company.domain, reason=reason)
    transaction.on_commit(lambda: contact_needed.send_robust(sender=Company, **fields))
    return True


def accept_candidate(
    *,
    subject_ref: str,
    channel_idx: str,
    email: str,
    first_name: str,
    last_name: str,
    job_title: str = "",
    source_note: str = "",
    actor: str,
) -> str:
    """The new contact's reference (`leads.Contact:<id>`). Raises `ValueError` for an unknown company, a company of
    another channel, an erased or anonymised address, or an address that already is a contact of the company."""
    from django_leads.services import contact_service

    company = company_from_ref(subject_ref)
    if company is None or company.channel.idx != channel_idx:
        raise ValueError(f"no company {subject_ref}")
    if erased_address_service.is_erased(email):
        raise ValueError(erased_address_service.SKIP_REASON)
    row = {"email": email, "first_name": first_name, "last_name": last_name, "job_title": job_title}
    row.update(source=LeadSource.PROSPECTING, legal_basis=LegalBasis.LEGITIMATE_INTEREST)
    try:
        contact = contact_service.create_contact(company, row, actor=actor)
    except contact_service.ContactExists as error:
        raise ValueError(str(error)) from None
    data = {"event": "candidate_accepted", "source": source_note}
    message = f"contact added from prospecting: {first_name} {last_name}"
    activity_service.record(company, ActivityKind.NOTE, message, contact=contact, data=data, actor=actor)
    return contact_ref(contact)
