# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Claims around paid toolbox calls: take before the call (committed, no lock held during it), finish after it."""

from datetime import datetime, timedelta

from django.utils import timezone

from django_leads import settings as leads_settings
from django_leads.enums import ClaimState
from django_leads.models import Claim, Company

OUTCOME_UNKNOWN = "outcome_unknown"


def stale_before() -> datetime:
    return timezone.now() - timedelta(minutes=leads_settings.LEADS_CLAIM_STALE_MINUTES)


def take(company: Company, key: str) -> Claim | None:
    """A new `claimed` row, or None when the key was taken before (a stale claim is failed on the way)."""
    claim, created = Claim.objects.get_or_create(key=key, defaults={"company": company})
    if created:
        return claim
    expire_if_stale(claim)
    return None


def expire_if_stale(claim: Claim) -> None:
    """A claim left `claimed` past `LEADS_CLAIM_STALE_MINUTES` failed mid-call — never retried automatically."""
    if claim.state == ClaimState.CLAIMED and claim.attempted_at < stale_before():
        finish(claim, ClaimState.FAILED, OUTCOME_UNKNOWN)


def finish(claim: Claim, state: str, detail: str = "", **fields) -> None:
    for name, value in {"state": state, "detail": detail, **fields}.items():
        setattr(claim, name, value)
    claim.save(update_fields=["state", "detail", *fields])
