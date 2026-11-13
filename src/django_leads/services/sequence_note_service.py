# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Activity `note` "sequence stopped" / "sequence paused" when a reply or a hand-written mail has just ended the
automation of a company's thread; nothing when the thread had no running sequence."""

from datetime import datetime

from django_communicator.enums import SequenceStopReason
from django_communicator.models import ThreadSequenceState

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Company, Contact
from django_leads.services import activity_service

STOPPED = frozenset({SequenceStopReason.REPLIED, SequenceStopReason.MANUAL})


def record(
    company: Company, contact: Contact | None, *, thread_id: int, since: datetime, actor: str
) -> Activity | None:
    """`since`: the moment of the reply or the mail; a sequence stopped before it is not this event's doing."""
    state = ThreadSequenceState.objects.filter(thread_id=thread_id, stopped_at__gte=since).first()
    if state is None:
        return None
    if state.stop_reason == SequenceStopReason.PAUSED:
        return _paused(company, contact, state.resume_at, actor)
    if state.stop_reason in STOPPED:
        data = {"event": "sequence_stopped"}
        return activity_service.record(
            company, ActivityKind.NOTE, "sequence stopped", contact=contact, data=data, actor=actor
        )
    return None


def _paused(company: Company, contact: Contact | None, resume_at: datetime | None, actor: str) -> Activity:
    data = {"event": "sequence_paused", "resume_at": resume_at.isoformat() if resume_at else None}
    message = f"sequence paused until {resume_at.date().isoformat()}" if resume_at else "sequence paused"
    return activity_service.record(company, ActivityKind.NOTE, message, contact=contact, data=data, actor=actor)
