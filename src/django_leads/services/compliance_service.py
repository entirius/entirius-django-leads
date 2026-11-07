# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Q7: "why is this contact (not) mailable" — every gate reason at once plus the facts behind them (legal basis and who
set it, address status, retention due date, sender route). Read-only; the gate itself stays `recipient_service`."""

from datetime import timedelta

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, StageKind
from django_leads.models import Activity, Contact
from django_leads.services import recipient_service, sender_route_service


def explain(contact: Contact) -> dict:
    company = contact.company
    reasons = recipient_service.block_reasons(contact)
    return {
        "contact_id": contact.pk,
        "company_id": company.pk,
        "eligible": not reasons,
        "reasons": reasons,
        "legal_basis": {"value": contact.legal_basis, "consent_ref": contact.consent_ref, **_basis_origin(contact)},
        "email": {
            "status": contact.email_status,
            "reason": contact.email_status_reason,
            "verified_at": contact.email_verified_at,
        },
        "opt_out_at": contact.opt_out_at,
        "company_do_not_contact": company.do_not_contact,
        "retention": _retention(contact),
        "sender_route": sender_route_service.effective_route(company),
    }


def _basis_origin(contact: Contact) -> dict:
    """Source and time of the last change of the basis, from the timeline."""
    last = Activity.objects.filter(contact=contact, kind=ActivityKind.LEGAL_BASIS).order_by("-id").first()
    if last is None:
        return {"source": None, "set_at": None}
    return {"source": last.data.get("source"), "set_at": last.created_at}


def _retention(contact: Contact) -> dict:
    company = contact.company
    days = company.channel.retention_days or leads_settings.LEADS_RETENTION_DAYS
    exempt = company.stage.kind == StageKind.WON
    due = None if exempt or company.last_activity_at is None else company.last_activity_at + timedelta(days=days)
    return {"days": days, "exempt_won": exempt, "anonymised_at": contact.anonymised_at, "due_at": due}
