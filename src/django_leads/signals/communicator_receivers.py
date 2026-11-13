# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""communicator → leads: timeline, the event → stage map (first send, replies by class, finished sequences),
do-not-contact (reviewer skips, confirmed opt-outs), hard bounces; the outreach gate on draft retries."""

import logging

from django_communicator.services.reply_plan_service import reply_title
from django_communicator.signals import (
    REPLY_ALERTED,
    company_skipped,
    draft_retry_requested,
    message_bounced,
    message_sent,
    optout_confirmed,
    reply_received,
    sequence_finished,
)

from django_leads.enums import ActivityKind, StageEvent
from django_leads.models import Activity
from django_leads.services import (
    activity_service,
    alert_service,
    company_service,
    contact_service,
    follow_up_service,
    recipient_service,
    sequence_note_service,
    stage_event_service,
)
from django_leads.signals._deferred import after_commit
from django_leads.utils.refs import company_from_ref

logger = logging.getLogger(__name__)

REPLY_EXCERPT = 200
WRONG_PERSON = "wrong_person"
NO_ALERT = "none"


def connect() -> None:
    reply_received.connect(handle_reply_received, dispatch_uid="django_leads.handle_reply_received")
    company_skipped.connect(on_company_skipped, dispatch_uid="django_leads.on_company_skipped")
    message_sent.connect(on_message_sent, dispatch_uid="django_leads.on_message_sent")
    sequence_finished.connect(on_sequence_finished, dispatch_uid="django_leads.on_sequence_finished")
    draft_retry_requested.connect(on_draft_retry_requested, dispatch_uid="django_leads.on_draft_retry_requested")
    optout_confirmed.connect(on_optout_confirmed, dispatch_uid="django_leads.on_optout_confirmed")
    message_bounced.connect(on_message_bounced, dispatch_uid="django_leads.on_message_bounced")


def handle_reply_received(sender, thread, reply, **kwargs) -> str | None:
    """Synchronous: communicator sends the signal after its commit and raises its own alert unless a receiver
    answers `REPLY_ALERTED`. A failure is logged and answers None — communicator alerts instead."""
    try:
        return REPLY_ALERTED if record_reply(thread, reply) else None
    except Exception:
        logger.exception("leads: receiver handle_reply_received failed")
        return None


def on_company_skipped(sender, subject_ref, **kwargs) -> None:
    after_commit(lambda: block_company(subject_ref), "on_company_skipped")


def on_message_sent(sender, message, **kwargs) -> None:
    after_commit(lambda: record_sent(message), "on_message_sent")


def on_sequence_finished(sender, thread, **kwargs) -> None:
    after_commit(lambda: finish_sequence(thread), "on_sequence_finished")


def on_optout_confirmed(sender, email, channel_idx, **kwargs) -> None:
    after_commit(
        lambda: contact_service.opt_out_address(email, channel_idx, actor="communicator"), "on_optout_confirmed"
    )


def on_message_bounced(sender, message, hard=False, reason="", **kwargs) -> None:
    if hard:
        after_commit(lambda: record_bounce(message, reason), "on_message_bounced")


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


def record_reply(thread, reply) -> bool:
    """Timeline with the reply's class, the wrong-person flag, the map, the sequence note, the owner's next step as
    a task, and last an alert of the plan's severity. True when the alert went out."""
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return False
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first()
    intent = reply.intent
    data = {"event": "reply", "thread_id": thread.pk, "reply_id": reply.pk, "class": intent}
    message = f"reply received: {intent}" if intent else "reply received"
    activity_service.record(company, ActivityKind.REPLY, message, contact=contact, data=data)
    if contact is not None and intent == WRONG_PERSON:
        contact_service.flag_wrong_person(contact, actor="system")
    company = stage_event_service.apply(company, stage_event_service.reply_event(intent))
    sequence_note_service.record(company, contact, thread_id=thread.pk, since=reply.received_at, actor="system")
    follow_up_service.on_reply(company, contact, reply)
    return alert_reply(company, reply)


def alert_reply(company, reply) -> bool:
    """`Reply from <company> — <class words>` to the owner, else the role; nothing for a plan without an alert."""
    severity = (reply.plan_applied or {}).get("notify") or "high"
    if severity == NO_ALERT:
        return False
    body = (reply.body_text or "")[:REPLY_EXCERPT]
    return alert_service.alert(company, severity=severity, title=reply_title(company.name, reply.intent), body=body)


def block_company(subject_ref: str) -> None:
    company = company_from_ref(subject_ref)
    if company is None:
        return
    company_service.set_do_not_contact(company, True, actor="reviewer")
    activity_service.record(company, ActivityKind.BLOCKED, "skipped by reviewer")


def record_sent(message) -> None:
    """Activity `sent`; the company's first one applies `first_sent`."""
    company = company_from_ref(message.thread.subject_ref)
    if company is None:
        return
    first = not company.activities.filter(kind=ActivityKind.SENT).exists()
    contact = company.contacts.filter(email__iexact=message.thread.recipient_email).first()
    activity_service.record(
        company, ActivityKind.SENT, "message sent", contact=contact, data={"message_id": message.pk}
    )
    if first:
        stage_event_service.apply(company, StageEvent.FIRST_SENT)


def finish_sequence(thread) -> None:
    """The optional stage move of `sequence_finished`, then the rotation (which ignores other modules' subjects)."""
    from django_leads.tasks import rotate_thread

    if company := company_from_ref(thread.subject_ref):
        stage_event_service.apply(company, StageEvent.SEQUENCE_FINISHED)
    rotate_thread.delay(thread.pk)


def record_bounce(message, reason: str) -> None:
    company = company_from_ref(message.thread.subject_ref)
    if company is None:
        return
    contact = company.contacts.filter(email__iexact=message.thread.recipient_email).first()
    data = {"message_id": message.pk, "reason": reason}
    activity_service.record(company, ActivityKind.NOTE, "address invalid: hard bounce", contact=contact, data=data)
