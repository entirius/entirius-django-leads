# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A salesperson's decision on a candidate: accept (→ a contact in leads) or reject. Only pending candidates change."""

from django.db import transaction
from django.utils import timezone

from django_prospecting.enums import CandidateStatus
from django_prospecting.models import Candidate
from django_prospecting.services import leads_bridge


class CandidateStateError(Exception):
    """The candidate was already decided."""


def list_candidates(channel_idx: str, *, subject_ref: str | None = None, status: str | None = None):
    rows = Candidate.objects.filter(channel_idx=channel_idx).prefetch_related("checks").select_related("search")
    if subject_ref:
        rows = rows.filter(subject_ref=subject_ref)
    if status:
        rows = rows.filter(status=status)
    return rows


def get_candidate(channel_idx: str, pk: int) -> Candidate:
    return list_candidates(channel_idx).get(pk=pk)


def accept(candidate: Candidate, *, actor: str, email: str = "") -> Candidate:
    """The given address (one of the guesses or typed by the salesperson) or the candidate's best one. Raises
    `CandidateStateError`, `ValueError` (no address, refused by leads) and `leads_bridge.LeadsUnavailable`."""
    with transaction.atomic():
        locked = Candidate.objects.select_for_update().get(pk=candidate.pk)
        if locked.status != CandidateStatus.PENDING:
            raise CandidateStateError(f"candidate is {locked.status}")
        address = (email or locked.email).strip().lower()
        if not address:
            raise ValueError("an accepted candidate needs an e-mail address")
        locked.contact_ref = leads_bridge.add_contact(locked, email=address, actor=actor)
        locked.email, locked.status = address, CandidateStatus.ACCEPTED
        locked.decided_by, locked.decided_at = actor, timezone.now()
        locked.save(update_fields=["contact_ref", "email", "status", "decided_by", "decided_at", "modified_at"])
    return get_candidate(locked.channel_idx, locked.pk)


def reject(candidate: Candidate, *, actor: str, reason: str = "") -> Candidate:
    updated = Candidate.objects.filter(pk=candidate.pk, status=CandidateStatus.PENDING).update(
        status=CandidateStatus.REJECTED, decided_by=actor, decided_at=timezone.now(), reject_reason=reason[:255],
        modified_at=timezone.now(),
    )  # fmt: skip
    if not updated:
        raise CandidateStateError("candidate was already decided")
    return get_candidate(candidate.channel_idx, candidate.pk)
