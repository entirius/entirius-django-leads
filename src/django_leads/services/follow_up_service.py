# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The next step after a reply, as a task for the company's owner — at most one task per reply.

Nothing is drafted when a "come back" task is due: the person decides.
"""

import logging
from datetime import date, datetime, time, timedelta

from django.utils import timezone
from django_communicator.enums import ReplyIntent

from django_leads.models import Channel, Company, Contact
from django_leads.services import task_bridge
from django_leads.services.task_bridge import TaskSpec

logger = logging.getLogger(__name__)

DEFAULT_RESUME_DAYS = {ReplyIntent.NOT_NOW.value: 30, ReplyIntent.OUT_OF_OFFICE.value: 7}
KIND_TODO = "todo"
KIND_MAIL = "mail"
NEWER_REPLY = "newer reply"
OPTED_OUT = "opted out"
ANONYMISED = "anonymised"


def resume_days(channel: Channel) -> dict[str, int]:
    """Days until a "come back" task per reply class: the channel's own values over the defaults."""
    return {**DEFAULT_RESUME_DAYS, **channel.resume_days}


def resume_key(contact: Contact) -> str:
    return f"resume:{contact.pk}"


def on_reply(company: Company, contact: Contact | None, reply) -> None:
    """A newer reply cancels the contact's open "come back" task, then the reply's own task. Never raises: a failure
    is logged and the reply stays recorded."""
    try:
        if contact is not None:
            cancel_resume(contact, NEWER_REPLY)
        if spec := task_for(company, contact, reply):
            task_bridge.create(company, contact, spec)
    except Exception:
        logger.exception("leads: follow-up task for reply %s failed", reply.pk)


def cancel_resume(contact: Contact, reason: str) -> None:
    task_bridge.cancel(contact.company.channel.idx, resume_key(contact), reason=reason)


def task_for(company: Company, contact: Contact | None, reply) -> TaskSpec | None:
    """First match: needs review or no class → read it; interested; then the classes about the contact. None for
    `unsubscribe` (the opt-out path asks a person), `not_interested`, `unclear` and a reply without a contact."""
    now = timezone.now()
    if reply.needs_review or not reply.intent:
        return TaskSpec(KIND_TODO, "Read the reply", now, "leads.reply.review", f"reply:{reply.pk}")
    if reply.intent == ReplyIntent.INTERESTED:
        title = "Interested: answer and open a deal"
        return TaskSpec(KIND_TODO, title, now, "leads.reply.interested", f"reply:{reply.pk}")
    if contact is None:
        return None
    return _contact_task(company, contact, reply, now)


def _contact_task(company: Company, contact: Contact, reply, now: datetime) -> TaskSpec | None:
    days = resume_days(company.channel)
    if reply.intent == ReplyIntent.NOT_NOW:
        default = now + timedelta(days=days[ReplyIntent.NOT_NOW])
        due = _day_after(reply.intent_return_date) if reply.intent_return_date else default
        return TaskSpec(KIND_MAIL, f"Come back: {_name(contact)}", due, "leads.reply.not_now", resume_key(contact))
    if reply.intent == ReplyIntent.OUT_OF_OFFICE and reply.intent_return_date is None:
        due = now + timedelta(days=days[ReplyIntent.OUT_OF_OFFICE])
        title = f"Try again: {_name(contact)}"
        return TaskSpec(KIND_MAIL, title, due, "leads.reply.out_of_office", resume_key(contact))
    if reply.intent == ReplyIntent.WRONG_PERSON:
        title = "Find the right person"
        return TaskSpec(KIND_TODO, title, now, "leads.reply.wrong_person", f"wrong:{contact.pk}")
    return None


def _day_after(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))


def _name(contact: Contact) -> str:
    return " ".join(part for part in (contact.first_name, contact.last_name) if part) or contact.email
