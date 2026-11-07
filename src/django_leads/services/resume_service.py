# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The "not now" loop (Q20): a reply of class not_now / out_of_office puts a due day on the contact, and the daily task
drafts to the contact again that day.

The draft goes through `outreach_service.request_draft`, so the same outreach gate decides as for every other draft:
opt-out, wrong person, `do_not_contact` of the company, the shared do-not-contact list, an invalid address (verified
again first when the verification loop is on). Whatever the gate refuses ends the loop for that contact; a contact who
unsubscribed, refused or was flagged is never resumed. The number of drafts per run is capped (`LEADS_RESUME_BATCH`).

An out-of-office reply with a return date is resumed by communicator itself (it pauses the sequence and resumes it the
day after); leads plans nothing then, so the contact is not written to twice."""

from datetime import UTC, date, datetime, time, timedelta

from django.utils import timezone
from django_utils.toolbox import ToolboxDisabledError

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, ReplyIntent
from django_leads.models import Channel, Company, Contact
from django_leads.services import activity_service, outreach_service, rotation_service

PAUSE_INTENTS = (ReplyIntent.NOT_NOW, ReplyIntent.OUT_OF_OFFICE)


def default_days(channel: Channel, intent: str) -> int:
    configured = (channel.resume_days or {}).get(intent)
    return int(configured or leads_settings.LEADS_RESUME_DEFAULT_DAYS.get(intent, 30))


def due_moment(channel: Channel, intent: str, return_date: date | None, *, now: datetime | None = None) -> datetime:
    """The day after the stated return (00:00 UTC) when it lies ahead, else now plus the channel's window."""
    now = now or timezone.now()
    if return_date is not None:
        moment = datetime.combine(return_date + timedelta(days=1), time.min, tzinfo=UTC)
        if moment > now:
            return moment
    return now + timedelta(days=default_days(channel, intent))


def on_reply(company: Company, contact: Contact | None, reply) -> None:
    """Called for every classified or unclassified reply: plan the return for the pause classes, cancel a plan for any
    other reply (a newer answer overrides an older "not now")."""
    if contact is None:
        return
    intent = getattr(reply, "intent", "") or ""
    if intent not in PAUSE_INTENTS:
        cancel(contact, "newer reply")
        return
    if intent == ReplyIntent.OUT_OF_OFFICE and getattr(reply, "resume_at", None):
        return  # communicator resumes this sequence on the return day
    return_date = getattr(reply, "intent_return_date", None)
    due = due_moment(company.channel, intent, return_date)
    Contact.objects.filter(pk=contact.pk).update(resume_at=due, resume_intent=intent, modified_at=timezone.now())
    dated = return_date is not None and due.date() == return_date + timedelta(days=1)
    data = {"intent": intent, "resume_at": due.isoformat(), "from_return_date": dated}
    message = f"resume planned: {due.date().isoformat()}"
    activity_service.record(company, ActivityKind.RESUME, message, contact=contact, data=data)


def cancel(contact: Contact, reason: str) -> None:
    """Ends a planned return (nothing happens, nothing is recorded, when none is planned)."""
    cleared = Contact.objects.filter(pk=contact.pk, resume_at__isnull=False).update(
        resume_at=None, resume_intent="", modified_at=timezone.now()
    )
    if cleared:
        activity_service.record(contact.company, ActivityKind.RESUME, f"resume cancelled: {reason}", contact=contact)


def due_contacts(*, now: datetime | None = None, channel_idx: str | None = None, company_id: int | None = None):
    contacts = Contact.objects.filter(resume_at__lte=now or timezone.now(), anonymised_at__isnull=True)
    if channel_idx:
        contacts = contacts.filter(company__channel__idx=channel_idx)
    if company_id:
        contacts = contacts.filter(company_id=company_id)
    return contacts.select_related("company__channel", "company__stage").order_by("resume_at", "pk")


def run_due(
    *, now: datetime | None = None, channel_idx: str | None = None, company_id: int | None = None
) -> dict[str, int]:
    """Draft to every contact whose day has come, at most `LEADS_RESUME_BATCH` per run.
    Counts: `resumed` (a draft was requested), `cancelled` (the gate refused or nothing to send), `waiting` (the AI
    toolbox is switched off: tried again next run)."""
    counts = {"resumed": 0, "cancelled": 0, "waiting": 0}
    for contact in due_contacts(now=now, channel_idx=channel_idx, company_id=company_id)[
        : leads_settings.LEADS_RESUME_BATCH
    ]:
        counts[_resume_one(contact)] += 1
    return counts


def _resume_one(contact: Contact) -> str:
    company, due = contact.company, contact.resume_at
    claimed = Contact.objects.filter(pk=contact.pk, resume_at=due).update(resume_at=None)  # one worker wins
    if not claimed:
        return "waiting"
    template_key = rotation_service._template_key(company)  # noqa: SLF001 — the template of the last draft
    if not template_key:
        return _finish(contact, "cancelled", "no earlier draft to repeat")
    try:
        result = outreach_service.request_draft(company, contact, template_key, actor="resume")
    except ToolboxDisabledError:
        Contact.objects.filter(pk=contact.pk, resume_at__isnull=True).update(resume_at=due)
        return "waiting"
    if isinstance(result, outreach_service.Blocked):
        return _finish(contact, "cancelled", f"blocked: {result.reason}")
    if result is None:
        return _finish(contact, "cancelled", "no draft")
    activity_service.record(
        company, ActivityKind.RESUME, "resumed: draft requested", contact=contact,
        data={"message_id": result.pk, "intent": contact.resume_intent},
    )  # fmt: skip
    Contact.objects.filter(pk=contact.pk).update(resume_intent="")
    return "resumed"


def _finish(contact: Contact, outcome: str, reason: str) -> str:
    Contact.objects.filter(pk=contact.pk).update(resume_intent="")
    activity_service.record(contact.company, ActivityKind.RESUME, f"resume cancelled: {reason}", contact=contact)
    return outcome
