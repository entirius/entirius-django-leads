# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""GDPR hooks of prospecting (discovered by django_leads): candidates are real persons found and guessed addresses
tested for them. Export lists them; erasure deletes the candidates (and their checks) that hold the address."""

from typing import Any

from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast

from django_prospecting.models import Candidate, CandidateCheck


def _normalised(email: str) -> str:
    if not (address := email.strip().lower()):
        raise ValueError("an email address is required")
    return address


def _candidates(email: str) -> QuerySet[Candidate]:
    """Candidates whose address, any guessed address, or check value (the address the check confirmed) is the one."""
    address = _normalised(email)
    options = Cast("email_options", TextField())
    by_check = CandidateCheck.objects.filter(value__iexact=address).values("candidate_id")
    return Candidate.objects.annotate(options_text=options).filter(
        Q(email__iexact=address) | Q(options_text__icontains=address) | Q(pk__in=by_check)
    )


def gdpr_export(email: str) -> dict[str, Any]:
    candidates = _candidates(email)
    checks = CandidateCheck.objects.filter(candidate__in=candidates)
    return {
        "Candidate": list(candidates.values("id", "channel_idx", "subject_ref", "first_name", "last_name",
                                            "job_title", "profile_url", "source", "found_at", "email",
                                            "email_options", "status", "decided_by", "decided_at", "contact_ref")),
        "CandidateCheck": list(checks.values()),
    }  # fmt: skip


def gdpr_erase(email: str) -> dict[str, int]:
    """Candidates holding the address are deleted with their checks (nothing else here names the person)."""
    ids = list(_candidates(email).values_list("pk", flat=True))
    deleted, _ = Candidate.objects.filter(pk__in=ids).delete()
    return {"candidates": len(ids), "rows": deleted}
