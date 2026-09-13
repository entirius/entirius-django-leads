# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Silence after a sequence: draft to the next contact, park the company as `unresponsive` after the maximum."""

import logging

from django.db import transaction
from django_communicator.enums import SequenceStopReason, ThreadStatus
from django_communicator.models import Thread

from django_leads.enums import ActivityKind, StageKind
from django_leads.models import Activity, Company, Contact, Stage
from django_leads.services import activity_service, outreach_service, recipient_service, stage_service
from django_leads.settings import LEADS_ROTATION_MAX
from django_leads.utils.refs import company_from_ref

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """The channel has no stage of kind `unresponsive`."""


def rotate_thread(thread: Thread) -> Contact | None:
    """At most one rotation per thread (the company row lock serialises receiver and daily scan); subjects of other
    modules are ignored."""
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return None
    try:
        with transaction.atomic():
            Company.objects.select_for_update().filter(pk=company.pk).first()
            rotated = Activity.objects.filter(company=company, kind=ActivityKind.ROTATION, data__thread_id=thread.pk)
            return None if rotated.exists() else rotate_company(company, thread_id=thread.pk)
    except ConfigurationError:
        logger.error("leads: channel %s has no unresponsive stage", company.channel.idx)
        activity_service.record(company, ActivityKind.ROTATION, "no unresponsive stage", data={"thread_id": thread.pk})
        return None


def rotate_unresponsive() -> int:
    """Daily scan: open threads whose sequence finished without a reply."""
    threads = Thread.objects.filter(
        status=ThreadStatus.OPEN,
        sequence_state__stop_reason=SequenceStopReason.FINISHED,
        subject_ref__startswith="leads.Company:",
    )
    return sum(rotate_thread(thread) is not None for thread in threads.order_by("pk"))


def rotate_company(company: Company, *, thread_id: int | None = None) -> Contact | None:
    data = {"thread_id": thread_id}
    if company.do_not_contact:
        activity_service.record(company, ActivityKind.BLOCKED, "blocked: do_not_contact", data=data)
        return None
    contact = _next_contact(company)
    if company.rotation_count >= LEADS_ROTATION_MAX or contact is None:
        return _park(company, data)
    message = outreach_service.request_draft(company, contact, _template_key(company), actor="system")
    company.rotation_count += 1
    company.save(update_fields=["rotation_count", "modified_at"])
    data |= {"contact_id": contact.pk, "message_id": message.pk if message else None}
    activity_service.record(company, ActivityKind.ROTATION, "rotated to the next contact", contact=contact, data=data)
    return contact


def _next_contact(company: Company) -> Contact | None:
    ref = outreach_service.subject_ref(company)
    written = Thread.objects.filter(channel__idx=company.channel.idx, subject_ref=ref).values_list(
        "recipient_email", flat=True
    )
    emails = {email.lower() for email in written}
    return next(
        (contact for contact in recipient_service.candidates(company) if contact.email.lower() not in emails), None
    )


def _template_key(company: Company) -> str:
    last_draft = (
        Activity.objects.filter(company=company, kind=ActivityKind.DRAFT).order_by("-created_at", "-id").first()
    )
    return last_draft.data.get("template_key", "") if last_draft else ""


def _park(company: Company, data: dict) -> None:
    stage = Stage.objects.filter(channel_id=company.channel_id, kind=StageKind.UNRESPONSIVE).order_by("order").first()
    if stage is None:
        activity_service.record(company, ActivityKind.ROTATION, "no unresponsive stage", data=data)
        raise ConfigurationError(f"channel {company.channel.idx} has no unresponsive stage")
    activity_service.record(company, ActivityKind.ROTATION, "rotation exhausted", data=data)
    stage_service.transition_stage(company, stage, actor="system")
    return None
