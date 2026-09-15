# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""communicator → leads timeline: replies, reviewer skips, sends and finished sequences; the outreach gate on draft
retries."""

from django_communicator.signals import (
    company_skipped,
    draft_retry_requested,
    message_sent,
    reply_received,
    sequence_finished,
)

from django_leads.enums import ActivityKind
from django_leads.models import Company, Stage
from django_leads.services import activity_service, alert_service, recipient_service, stage_service
from django_leads.signals._deferred import after_commit
from django_leads.utils.refs import company_from_ref

REPLY_EXCERPT = 200


def connect() -> None:
    reply_received.connect(on_reply_received, dispatch_uid="django_leads.on_reply_received")
    company_skipped.connect(on_company_skipped, dispatch_uid="django_leads.on_company_skipped")
    message_sent.connect(on_message_sent, dispatch_uid="django_leads.on_message_sent")
    sequence_finished.connect(on_sequence_finished, dispatch_uid="django_leads.on_sequence_finished")
    draft_retry_requested.connect(on_draft_retry_requested, dispatch_uid="django_leads.on_draft_retry_requested")


def on_reply_received(sender, thread, reply, **kwargs) -> None:
    after_commit(lambda: record_reply(thread, reply), "on_reply_received")


def on_company_skipped(sender, subject_ref, **kwargs) -> None:
    after_commit(lambda: block_company(subject_ref), "on_company_skipped")


def on_message_sent(sender, message, **kwargs) -> None:
    after_commit(lambda: record_sent(message), "on_message_sent")


def on_sequence_finished(sender, thread, **kwargs) -> None:
    from django_leads.tasks import rotate_thread

    after_commit(lambda: rotate_thread.delay(thread.pk), "on_sequence_finished")


def on_draft_retry_requested(sender, message, **kwargs) -> str | None:
    """Synchronous: the reason the outreach gate refuses the thread's contact now, None when it may be drafted."""
    thread = message.thread
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return None
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first()
    if contact is None:
        return "no_contact"
    contact.company = company
    return recipient_service.block_reason(contact)


def record_reply(thread, reply) -> None:
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first()
    data = {"thread_id": thread.pk, "reply_id": reply.pk}
    activity_service.record(company, ActivityKind.REPLY, "reply received", contact=contact, data=data)
    stage = Stage.objects.filter(channel_id=company.channel_id, on_reply=True).order_by("order").first()
    if stage is not None:
        stage_service.transition_stage(company, stage, actor="system")
    body = (reply.body_text or "")[:REPLY_EXCERPT]
    alert_service.alert(company, severity="high", title=f"Reply from {company.name}", body=body)


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
