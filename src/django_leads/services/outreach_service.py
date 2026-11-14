# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Drafts through communicator only — leads never creates a Message itself."""

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django_agreements.models import Channel as AgreementsChannel
from django_agreements.services.clause_set_service import ClauseSetMissing, render_legal_footer, resolve_clause_set
from django_communicator.enums import MessageStatus
from django_communicator.models import Message, Reply, Thread
from django_communicator.services import compose_service
from django_communicator.services.communicate_service import LegalFooterRequiredError, RecipientData, communicate
from django_utils.toolbox import ToolboxDisabledError

from django_leads.enums import ActivityKind
from django_leads.models import Company, Contact
from django_leads.services import activity_service, fact_service, recipient_service, sequence_note_service

SKIPPED_TOOLBOX_DISABLED = "skipped: AI toolbox disabled"
CONTEXT_FIELDS = ("domain", "lead_type", "industry", "description", "platform", "hooks")


def subject_ref(company: Company) -> str:
    return f"leads.Company:{company.pk}"


class NoLegalBasis(Exception):
    """No legal footer for the contact: no clause set of its legal basis and language, or an empty footer."""


@dataclass(frozen=True)
class Blocked:
    """The outreach gate refused the draft; `reason` is the code recorded as `blocked: <reason>`."""

    reason: str


def request_draft(
    company: Company, contact: Contact, template_key: str, *, actor: str, thread: Thread | None = None
) -> Message | Blocked | None:
    """A reviewable draft for the contact; `Blocked` when the outreach gate refuses, None (Activity `skipped`) when
    no legal footer can be built. The gate and `communicate()` share one short transaction on the locked company and
    contact rows — an opt-out or `do_not_contact` either commits first (blocked) or waits for the draft commit.
    Raises `ToolboxDisabledError` (Activity `skipped: AI toolbox disabled`) for an AI template while the toolbox is
    switched off."""
    try:
        with transaction.atomic():
            company, contact = _lock(company, contact)
            reason = recipient_service.block_reason(contact)
            if reason:
                return _blocked(company, contact, reason, actor)
            message = _communicate(company, contact, template_key, thread)
            _record_draft(company, contact, message, actor, template_key=template_key)
    except (ClauseSetMissing, AgreementsChannel.DoesNotExist):
        return _skipped(company, contact, "skipped: no clause set", actor)
    except LegalFooterRequiredError:
        return _skipped(company, contact, "skipped: no legal basis", actor)
    except ToolboxDisabledError:
        _skipped(company, contact, SKIPPED_TOOLBOX_DISABLED, actor)
        raise
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


def compose_message(
    company: Company, contact: Contact, *, subject: str, body: str, actor: AbstractBaseUser, reply: Reply | None = None
) -> Message | Blocked:
    """A hand-written message from the owner's mailbox: the locks and the gate of `request_draft` (a refusal →
    Activity `blocked`), the legal footer of the contact's basis and language, then communicator's `compose`.
    Raises `NoLegalBasis`, and communicator's `Channel.DoesNotExist`, `ComposeError` and `ThreadMismatchError`."""
    username = actor.get_username()
    try:
        with transaction.atomic():
            company, contact = _lock(company, contact)
            if reason := recipient_service.block_reason(contact):
                return _blocked(company, contact, reason, username)
            message = compose_service.compose(
                channel_idx=company.channel.idx, recipient=_recipient(company, contact), subject=subject,
                body_text=body, subject_ref=subject_ref(company), author=actor, mailbox_user_id=company.owner_id,
                in_reply_to=reply, subject_label=company.name,
            )  # fmt: skip
            _record_composed(company, contact, message, username)
    except (ClauseSetMissing, AgreementsChannel.DoesNotExist, LegalFooterRequiredError):
        raise NoLegalBasis from None
    return message


def reply_of(company: Company, reply_id: int) -> Reply | None:
    """The reply when it belongs to a thread of the company, else None."""
    return Reply.objects.select_related("thread").filter(pk=reply_id, thread__subject_ref=subject_ref(company)).first()


def _communicate(company: Company, contact: Contact, template_key: str, thread: Thread | None) -> Message:
    return communicate(
        channel_idx=company.channel.idx,
        template_key=template_key,
        recipient=_recipient(company, contact),
        context=_context(company, contact),
        subject_ref=subject_ref(company),
        requires_review=True,
        thread=thread,
        audience=company.lead_type,  # the template's lead-type variant, else its any-audience default
        mailbox_user_id=company.owner_id,  # the owner's mailbox, else the channel default
        subject_label=company.name,
    )


def _recipient(company: Company, contact: Contact) -> RecipientData:
    language = _language_code(company, contact)
    return RecipientData(
        email=contact.email,
        first_name=contact.first_name,
        last_name=contact.last_name,
        language=language,
        legal_footer=_legal_footer(company, contact, language),
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


class BlankMissing(dict):
    """`{attr[key]}` of a key without a value renders empty instead of failing the render."""

    def __missing__(self, key: str) -> str:
        return ""


def _context(company: Company, contact: Contact) -> dict:
    fields = {field: getattr(company, field) for field in CONTEXT_FIELDS}
    people = {"job_title": contact.job_title, "first_name": contact.first_name, "last_name": contact.last_name}
    facts = fact_service.facts(company, contact)
    attributes = {
        "attr": _renderable(fact_service.attributes_of(facts, fact_service.ATTR_PREFIX)),
        "contact_attr": _renderable(fact_service.attributes_of(facts, fact_service.CONTACT_ATTR_PREFIX)),
    }
    return {
        "company_name": company.name,
        "website": company.website or company.domain,
        **fields,
        **people,
        **attributes,
    }


def _renderable(values: dict) -> BlankMissing:
    """Every active key is present (empty without a value); a multiselect renders as a comma-separated list."""
    return BlankMissing({key: _text(value) for key, value in values.items()})


def _text(value: object) -> str:
    if value is None:
        return ""
    return ", ".join(value) if isinstance(value, list) else str(value)


def _record_composed(company: Company, contact: Contact, message: Message, actor: str) -> None:
    """Activity `draft` "written by hand" (event `composed`), then the note of the sequence it stopped."""
    extra = {"event": "composed", "thread_id": message.thread_id}
    _record_draft(company, contact, message, actor, text="written by hand", extra=extra)
    since = message.created_at
    sequence_note_service.record(company, contact, thread_id=message.thread_id, since=since, actor=actor)


def _record_draft(
    company: Company,
    contact: Contact,
    message: Message,
    actor: str,
    *,
    template_key: str = "",
    text: str = "",
    extra: dict | None = None,
) -> None:
    """Activity `draft` (`draft <status>`, or the given text) with the message id; a suppressed message also
    `blocked`."""
    data = {"message_id": message.pk, "status": message.status, "template_key": template_key, **(extra or {})}
    text = text or f"draft {message.status}"
    activity_service.record(company, ActivityKind.DRAFT, text, contact=contact, data=data, actor=actor)
    if message.status == MessageStatus.SUPPRESSED:
        activity_service.record(
            company, ActivityKind.BLOCKED, "suppressed by communicator", contact=contact, actor=actor
        )


def _skipped(company: Company, contact: Contact, message: str, actor: str) -> None:
    activity_service.record(company, ActivityKind.SKIPPED, message, contact=contact, actor=actor)
    return None
