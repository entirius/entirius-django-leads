# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""communicator → leads timeline: replies, reviewer skips, sends and finished sequences; the outreach gate on draft
retries."""

from django.utils import timezone
from django_communicator import signals as communicator_signals
from django_communicator.signals import (
    company_skipped,
    draft_retry_requested,
    message_sent,
    reply_received,
    sequence_finished,
)

from django_leads.enums import ActivityKind, ReplyStageAction
from django_leads.models import Activity, Company, Contact, Stage
from django_leads.services import (
    activity_service,
    alert_service,
    dnc_service,
    recipient_service,
    reply_route_service,
    resume_service,
    stage_service,
    verification_service,
)
from django_leads.signals._deferred import after_commit
from django_leads.utils.refs import company_from_ref

REPLY_EXCERPT = 200


def connect() -> None:
    reply_received.connect(on_reply_received, dispatch_uid="django_leads.on_reply_received")
    company_skipped.connect(on_company_skipped, dispatch_uid="django_leads.on_company_skipped")
    message_sent.connect(on_message_sent, dispatch_uid="django_leads.on_message_sent")
    sequence_finished.connect(on_sequence_finished, dispatch_uid="django_leads.on_sequence_finished")
    draft_retry_requested.connect(on_draft_retry_requested, dispatch_uid="django_leads.on_draft_retry_requested")
    optout_confirmed = getattr(communicator_signals, "optout_confirmed", None)
    if optout_confirmed is not None:
        optout_confirmed.connect(on_optout_confirmed, dispatch_uid="django_leads.on_optout_confirmed")
    message_bounced = getattr(communicator_signals, "message_bounced", None)  # older communicators: the daily sync
    if message_bounced is not None:
        message_bounced.connect(on_message_bounced, dispatch_uid="django_leads.on_message_bounced")
    followup_due = getattr(communicator_signals, "reply_followup_due", None)  # communicator with T4
    if followup_due is not None:
        followup_due.connect(on_reply_followup_due, dispatch_uid="django_leads.on_reply_followup_due")


def on_reply_received(sender, thread, reply, **kwargs) -> None:
    after_commit(lambda: record_reply(thread, reply), "on_reply_received")


def on_optout_confirmed(sender, subject_ref, email, **kwargs) -> None:
    after_commit(lambda: record_optout(subject_ref, email), "on_optout_confirmed")


def on_message_bounced(sender, message, hard=True, **kwargs) -> None:
    if hard:
        after_commit(lambda: verification_service.mark_thread_bounce(message.thread), "on_message_bounced")


def on_reply_followup_due(sender, thread, reply, **kwargs) -> None:
    after_commit(lambda: record_followup_due(thread, reply), "on_reply_followup_due")


def on_company_skipped(sender, subject_ref, **kwargs) -> None:
    after_commit(lambda: block_company(subject_ref), "on_company_skipped")


def on_message_sent(sender, message, **kwargs) -> None:
    after_commit(lambda: record_sent(message), "on_message_sent")


def on_sequence_finished(sender, thread, **kwargs) -> None:
    from django_leads.tasks import rotate_thread

    after_commit(lambda: rotate_thread.delay(thread.pk), "on_sequence_finished")


def on_draft_retry_requested(sender, message, **kwargs) -> str | None:
    """Synchronous: the reason the outreach gate refuses the thread's contact now, None when it may be drafted.
    A refusal leaves one `blocked: <reason>` Activity per draft, like `outreach_service.request_draft`."""
    thread = message.thread
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return None
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first()
    if contact is not None:
        contact.company = company
    reason = recipient_service.block_reason(contact) if contact else "no_contact"
    if reason:
        record_retry_blocked(company, contact, message, reason)
    return reason


def record_retry_blocked(company, contact, message, reason: str) -> None:
    blocked = Activity.objects.filter(company=company, kind=ActivityKind.BLOCKED, data__message_id=message.pk)
    if blocked.exists():
        return
    data = {"message_id": message.pk}
    activity_service.record(company, ActivityKind.BLOCKED, f"blocked: {reason}", contact=contact, data=data)


def record_reply(thread, reply) -> None:
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first()
    data = {"thread_id": thread.pk, "reply_id": reply.pk}
    intent = getattr(reply, "intent", "") or ""  # T4: set by communicator when the AI toolbox classified the reply
    if intent:
        confidence, return_date = getattr(reply, "intent_confidence", None), getattr(reply, "intent_return_date", None)
        data |= {
            "intent": intent,
            "intent_confidence": confidence,
            "intent_return_date": return_date and str(return_date),
        }
    message = f"reply received: {intent}" if intent else "reply received"
    activity_service.record(company, ActivityKind.REPLY, message, contact=contact, data=data)
    route = reply_route_service.route_for(company.channel, intent)
    apply_route(company, contact, route)
    resume_service.on_reply(company, contact, reply)
    plan = getattr(reply, "action_applied", None) or {}  # communicator's plan for the class (T4); none = pre-T4
    severity = plan.get("notify", "high")
    if severity != "none":
        title = f"Reply from {company.name}" + (f" ({intent})" if intent else "")
        alert_service.alert(company, severity=severity, title=title, body=(reply.body_text or "")[:REPLY_EXCERPT])


def apply_route(company, contact, route) -> None:
    """The stage and contact part of a reply's next step: `reply_stage` is the pre-T4 move; `stage` goes to the
    configured stage but never out of a terminal one; `none` leaves the company where it is."""
    if route.action == ReplyStageAction.REPLY_STAGE:
        stage = Stage.objects.filter(channel_id=company.channel_id, on_reply=True).order_by("order").first()
    elif route.action == ReplyStageAction.STAGE and not company.stage.is_terminal:
        stage = Stage.objects.filter(pk=route.stage_id, channel_id=company.channel_id).first()
    else:
        stage = None
    if stage is not None:
        stage_service.transition_stage(company, stage, actor="system")
    if route.flag_contact and contact is not None and contact.wrong_person_at is None:
        Contact.objects.filter(pk=contact.pk).update(wrong_person_at=timezone.now())
        activity_service.record(company, ActivityKind.NOTE, "contact flagged: wrong person", contact=contact)


def record_optout(subject_ref: str, email: str) -> None:
    """A confirmed opt-out stops the address in every channel: `dnc_service.add` marks all its contacts and makes sure
    every communicator channel suppresses it (the confirm only suppressed the thread's channel)."""
    company = company_from_ref(subject_ref)
    if company is None:
        return
    dnc_service.add(email=email, reason="opt-out confirmed", actor="communicator")


def record_followup_due(thread, reply) -> None:
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first()
    intent = getattr(reply, "intent", "") or ""
    data = {"thread_id": thread.pk, "reply_id": reply.pk, "intent": intent}
    activity_service.record(
        company, ActivityKind.NOTE, f"follow-up reminder: {intent or 'reply'}", contact=contact, data=data
    )


def block_company(subject_ref: str) -> None:
    company = company_from_ref(subject_ref)
    if company is None:
        return
    Company.objects.filter(pk=company.pk).update(do_not_contact=True)
    activity_service.record(company, ActivityKind.BLOCKED, "skipped by reviewer")


def record_sent(message) -> None:
    company = company_from_ref(message.thread.subject_ref)
    if company is None:
        return
    contact = company.contacts.filter(email__iexact=message.thread.recipient_email).first()
    activity_service.record(
        company, ActivityKind.SENT, "message sent", contact=contact, data={"message_id": message.pk}
    )
    move_to_sent_stage(company)


def move_to_sent_stage(company: Company) -> None:
    """F3: the `on_sent` stage takes a company whose stage lies before it — never backwards, never out of a terminal
    stage, so a reply that already moved the company is kept."""
    company.refresh_from_db()
    target = Stage.objects.filter(channel_id=company.channel_id, on_sent=True).order_by("order", "id").first()
    if target is None or company.stage.is_terminal or company.stage.order >= target.order:
        return
    stage_service.transition_stage(company, target, actor="system")
