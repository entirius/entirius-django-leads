# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Drafts through communicator only — leads never creates a Message itself."""

from dataclasses import dataclass

from django.db import transaction
from django_agreements.models import Channel as AgreementsChannel
from django_agreements.services.clause_set_service import ClauseSetMissing, render_legal_footer, resolve_clause_set
from django_communicator.enums import MessageStatus
from django_communicator.models import Message, Thread
from django_communicator.services.communicate_service import LegalFooterRequiredError, RecipientData, communicate

from django_leads.enums import ActivityKind
from django_leads.models import Company, Contact
from django_leads.services import activity_service, recipient_service

CONTEXT_FIELDS = ("domain", "company_type", "industry", "description", "platform", "hooks")


def subject_ref(company: Company) -> str:
    return f"leads.Company:{company.pk}"


@dataclass(frozen=True)
class Blocked:
    """The outreach gate refused the draft; `reason` is the code recorded as `blocked: <reason>`."""

    reason: str


def request_draft(
    company: Company, contact: Contact, template_key: str, *, actor: str, thread: Thread | None = None
) -> Message | Blocked | None:
    """A reviewable draft for the contact; `Blocked` when the outreach gate refuses, None (Activity `skipped`) when
    no legal footer can be built. The gate and `communicate()` share one short transaction on the locked company and
    contact rows — an opt-out or `do_not_contact` either commits first (blocked) or waits for the draft commit."""
    try:
        with transaction.atomic():
            company, contact = _lock(company, contact)
            reason = recipient_service.block_reason(contact)
            if reason:
                return _blocked(company, contact, reason, actor)
            message = _communicate(company, contact, template_key, thread)
            _record_draft(company, contact, message, template_key, actor)
    except (ClauseSetMissing, AgreementsChannel.DoesNotExist):
        return _skipped(company, contact, "skipped: no clause set", actor)
    except LegalFooterRequiredError:
        return _skipped(company, contact, "skipped: no legal basis", actor)
    return message


def _lock(company: Company, contact: Contact) -> tuple[Company, Contact]:
    """Company first, then the contact — the order every locked phase of the module uses."""
    locked_company = Company.objects.select_for_update().get(pk=company.pk)
    locked_contact = Contact.objects.select_for_update().get(pk=contact.pk, company=locked_company)
    locked_contact.company = locked_company
    return locked_company, locked_contact


def _blocked(company: Company, contact: Contact, reason: str, actor: str) -> Blocked:
    activity_service.record(company, ActivityKind.BLOCKED, f"blocked: {reason}", contact=contact, actor=actor)
    return Blocked(reason)


def _communicate(company: Company, contact: Contact, template_key: str, thread: Thread | None) -> Message:
    language = _language_code(company, contact)
    return communicate(
        channel_idx=company.channel.idx,
        template_key=template_key,
        recipient=RecipientData(
            email=contact.email,
            first_name=contact.first_name,
            last_name=contact.last_name,
            language=language,
            legal_footer=_legal_footer(company, contact, language),
        ),
        context=_context(company, contact),
        subject_ref=subject_ref(company),
        requires_review=True,
        thread=thread,
    )


def _language_code(company: Company, contact: Contact) -> str:
    language = contact.language or company.channel.default_language
    return language.iso2.lower() if language else ""


def _legal_footer(company: Company, contact: Contact, language: str) -> str:
    """The outreach gate guarantees a legal basis — the clause set of that basis builds the footer."""
    clause_set = resolve_clause_set(
        channel_idx=company.channel.idx, legal_basis=contact.legal_basis, language_code=language
    )
    return render_legal_footer(clause_set, recipient_email=contact.email)


def _context(company: Company, contact: Contact) -> dict:
    fields = {field: getattr(company, field) for field in CONTEXT_FIELDS}
    people = {"job_title": contact.job_title, "first_name": contact.first_name, "last_name": contact.last_name}
    return {"company_name": company.name, "website": company.website or company.domain, **fields, **people}


def _record_draft(company: Company, contact: Contact, message: Message, template_key: str, actor: str) -> None:
    data = {"message_id": message.pk, "status": message.status, "template_key": template_key}
    activity_service.record(
        company, ActivityKind.DRAFT, f"draft {message.status}", contact=contact, data=data, actor=actor
    )
    if message.status == MessageStatus.SUPPRESSED:
        activity_service.record(
            company, ActivityKind.BLOCKED, "suppressed by communicator", contact=contact, actor=actor
        )


def _skipped(company: Company, contact: Contact, message: str, actor: str) -> None:
    activity_service.record(company, ActivityKind.SKIPPED, message, contact=contact, actor=actor)
    return None
