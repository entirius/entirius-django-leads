# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Drafts through communicator only — leads never creates a Message itself."""

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


def request_draft(
    company: Company, contact: Contact, template_key: str, *, actor: str, thread: Thread | None = None
) -> Message | None:
    """A reviewable draft for the contact; None (Activity `blocked`/`skipped`) when the outreach gate refuses the
    contact or no legal footer can be built."""
    if check_gate(contact, actor=actor):
        return None
    language = _language_code(company, contact)
    try:
        footer = _legal_footer(company, contact, language)
        message = communicate(
            channel_idx=company.channel.idx,
            template_key=template_key,
            recipient=RecipientData(
                email=contact.email,
                first_name=contact.first_name,
                last_name=contact.last_name,
                language=language,
                legal_footer=footer,
            ),
            context=_context(company, contact),
            subject_ref=subject_ref(company),
            requires_review=True,
            thread=thread,
        )
    except (ClauseSetMissing, AgreementsChannel.DoesNotExist):
        return _skipped(company, contact, "skipped: no clause set", actor)
    except LegalFooterRequiredError:
        return _skipped(company, contact, "skipped: no legal basis", actor)
    _record_draft(company, contact, message, template_key, actor)
    return message


def check_gate(contact: Contact, *, actor: str) -> str | None:
    """The outreach gate on freshly read contact and company rows; a refusal is recorded as `blocked: <reason>`."""
    fresh = Contact.objects.select_related("company").get(pk=contact.pk)
    reason = recipient_service.block_reason(fresh)
    if reason:
        message = f"blocked: {reason}"
        activity_service.record(fresh.company, ActivityKind.BLOCKED, message, contact=fresh, actor=actor)
    return reason


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
