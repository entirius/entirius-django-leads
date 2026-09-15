# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Silence after a sequence: draft to the next contact, park the company as `unresponsive` after the maximum.

Claim `rotation:<thread>` per thread: taken under the company row lock (in-flight rotations of the company count
toward `LEADS_ROTATION_MAX`), the draft requested outside the lock, the count raised with `F()` only after a draft.
No draft → `retry` after `LEADS_ROTATION_RETRY_HOURS`, `failed` after `LEADS_ROTATION_MAX_FAILURES`.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone
from django_communicator.enums import SequenceStopReason, ThreadStatus
from django_communicator.models import Thread

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, ClaimState, StageKind
from django_leads.models import Activity, Claim, Company, Contact, Stage
from django_leads.services import activity_service, claim_service, outreach_service, recipient_service, stage_service
from django_leads.utils.refs import company_from_ref

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """The channel has no stage of kind `unresponsive`."""


def rotate_thread(thread: Thread) -> Contact | None:
    """At most one rotation per thread; subjects of other modules are ignored."""
    company = company_from_ref(thread.subject_ref)
    if company is None:
        return None
    try:
        claim, contact = _claim(company, thread.pk)
    except ConfigurationError:
        logger.error("leads: channel %s has no unresponsive stage", company.channel.idx)
        activity_service.record(company, ActivityKind.ROTATION, "no unresponsive stage", data={"thread_id": thread.pk})
        return None
    return _draft(claim, contact, thread.pk) if contact else None


def rotate_unresponsive(channel_idx: str | None = None) -> int:
    """Daily scan: open threads whose sequence finished without a reply; one failing thread never stops the scan."""
    return sum(_rotate_isolated(thread) for thread in finished_threads(channel_idx))


def finished_threads(channel_idx: str | None) -> QuerySet[Thread]:
    threads = Thread.objects.filter(
        status=ThreadStatus.OPEN,
        sequence_state__stop_reason=SequenceStopReason.FINISHED,
        subject_ref__startswith="leads.Company:",
    )
    return (threads.filter(channel__idx=channel_idx) if channel_idx else threads).order_by("pk")


def _rotate_isolated(thread: Thread) -> bool:
    try:
        return rotate_thread(thread) is not None
    except Exception as error:
        logger.error("leads: rotation of thread %s failed: %s", thread.pk, type(error).__name__)
        return False


def _claim(company: Company, thread_id: int) -> tuple[Claim | None, Contact | None]:
    """Locked phase: blocked and parked threads are closed here; a contact is returned only with a `claimed` claim."""
    with transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company.pk)
        claim = _open_claim(company, thread_id)
        if claim is None:
            return None, None
        data = {"thread_id": thread_id}
        if company.do_not_contact:
            activity_service.record(company, ActivityKind.BLOCKED, "blocked: do_not_contact", data=data)
            return _close(claim, "do_not_contact"), None
        contact = _next_contact(company)
        if company.rotation_count >= leads_settings.LEADS_ROTATION_MAX or contact is None:
            _park(company, data)
            return _close(claim, "parked"), None
        claim_service.finish(claim, ClaimState.CLAIMED, attempted_at=timezone.now())
        return claim, contact


def _open_claim(company: Company, thread_id: int) -> Claim | None:
    """None while another rotation of the company is in flight, or when this thread is done, failed or waiting."""
    key = f"rotation:{thread_id}"
    in_flight = company.claims.filter(key__startswith="rotation:", state=ClaimState.CLAIMED).exclude(key=key)
    if in_flight.filter(attempted_at__gte=claim_service.stale_before()).exists():
        return None
    claim, created = Claim.objects.get_or_create(key=key, defaults={"company": company})
    if created:
        return claim
    claim_service.expire_if_stale(claim)
    retry_after = timezone.now() - timedelta(hours=leads_settings.LEADS_ROTATION_RETRY_HOURS)
    return claim if claim.state == ClaimState.RETRY and claim.attempted_at <= retry_after else None


def _close(claim: Claim, detail: str) -> Claim:
    claim_service.finish(claim, ClaimState.DONE, detail)
    return claim


def _draft(claim: Claim, contact: Contact, thread_id: int) -> Contact | None:
    """Outside the claim lock; `request_draft` takes the gate on locked rows. The count and marker only after a draft."""
    company = claim.company
    message = outreach_service.request_draft(company, contact, _template_key(company), actor="system")
    if message is None or isinstance(message, outreach_service.Blocked):
        _no_draft(claim, contact, thread_id)
        return None
    data = {"thread_id": thread_id, "contact_id": contact.pk, "message_id": message.pk}
    with transaction.atomic():
        Company.objects.filter(pk=company.pk).update(rotation_count=F("rotation_count") + 1, modified_at=timezone.now())
        _close(claim, "rotated")
        activity_service.record(
            company, ActivityKind.ROTATION, "rotated to the next contact", contact=contact, data=data
        )
    return contact


def _no_draft(claim: Claim, contact: Contact, thread_id: int) -> None:
    failures = claim.failures + 1
    gave_up = failures >= leads_settings.LEADS_ROTATION_MAX_FAILURES
    claim_service.finish(claim, ClaimState.FAILED if gave_up else ClaimState.RETRY, "no_draft", failures=failures)
    data = {"thread_id": thread_id, "contact_id": contact.pk, "failures": failures}
    activity_service.record(claim.company, ActivityKind.ROTATION_FAILED, "rotation failed: no draft", data=data)
    if gave_up:
        activity_service.record(claim.company, ActivityKind.ROTATION_GAVE_UP, "rotation gave up", data=data)


def _next_contact(company: Company) -> Contact | None:
    """The first candidate not written to yet that passes the outreach gate."""
    ref = outreach_service.subject_ref(company)
    written = Thread.objects.filter(channel__idx=company.channel.idx, subject_ref=ref).values_list(
        "recipient_email", flat=True
    )
    emails = {email.lower() for email in written}
    fresh = (contact for contact in recipient_service.candidates(company) if contact.email.lower() not in emails)
    return next((contact for contact in fresh if recipient_service.eligible(contact)), None)


def _template_key(company: Company) -> str:
    last_draft = (
        Activity.objects.filter(company=company, kind=ActivityKind.DRAFT).order_by("-created_at", "-id").first()
    )
    return last_draft.data.get("template_key", "") if last_draft else ""


def _park(company: Company, data: dict) -> None:
    """Raises before writing anything — the caller records the missing stage outside the rolled-back block."""
    stage = Stage.objects.filter(channel_id=company.channel_id, kind=StageKind.UNRESPONSIVE).order_by("order").first()
    if stage is None:
        raise ConfigurationError(f"channel {company.channel.idx} has no unresponsive stage")
    activity_service.record(company, ActivityKind.ROTATION, "rotation exhausted", data=data)
    stage_service.transition_stage(company, stage, actor="system")
