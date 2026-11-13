# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Why a contact may (not) be mailed: the gate's reasons, the legal basis and where it came from, communicator's
suppression answer and the retention clock — one read for the "mailable" dialog."""

from datetime import timedelta
from typing import Any

from django_leads.enums import ActivityKind, StageKind
from django_leads.models import Company, Contact
from django_leads.services import recipient_service, retention_service, suppression_service


def mailable(contact: Contact) -> dict[str, Any]:
    reasons = recipient_service.block_reasons(contact)
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "legal_basis": _legal_basis(contact),
        "communicator": _communicator(contact),
        "retention": _retention(contact.company),
    }


def _legal_basis(contact: Contact) -> dict[str, Any]:
    change = contact.activities.filter(kind=ActivityKind.LEGAL_BASIS).order_by("-created_at", "-id").first()
    source = change.data.get("source") if change else None
    return {"value": contact.legal_basis, "source": source, "set_at": change.created_at if change else None}


def _communicator(contact: Contact) -> dict[str, Any] | None:
    answer = suppression_service.explain(contact.company.channel, contact.email)
    if answer is None:
        return None
    status = answer["address_status"]
    fields = ("status", "reason", "verified_at")
    return {**answer, "address_status": {name: getattr(status, name) for name in fields} if status else None}


def _retention(company: Company) -> dict[str, Any]:
    days = retention_service.retention_days(company.channel)
    due_at = company.last_activity_at + timedelta(days=days) if company.last_activity_at else None
    return {"days": days, "due_at": due_at, "exempt_won": company.stage.kind == StageKind.WON}
