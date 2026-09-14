# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django.utils import timezone

from django_leads.enums import ClaimState


class Claim(models.Model):
    """Written `claimed` before a paid call (intel analysis, rotation draft) and completed after it — a redelivered
    task finds the key and never pays again. Keys: `intel:<audit>:<task>`, `rotation:<thread>`."""

    key = models.CharField(max_length=128, unique=True)
    company = models.ForeignKey("django_leads.Company", on_delete=models.CASCADE, related_name="claims")
    state = models.CharField(max_length=16, choices=ClaimState.choices, default=ClaimState.CLAIMED)
    detail = models.CharField(max_length=64, blank=True, default="")
    failures = models.PositiveSmallIntegerField(default=0)
    attempted_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.key}:{self.state}"
