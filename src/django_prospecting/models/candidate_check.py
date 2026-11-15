# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.db import models
from django_utils.models.base_model import BaseModel

from django_prospecting.enums import CheckKind, CheckResult


class CandidateCheck(BaseModel):
    """One of the four checks of a candidate: its result, what was found, where and when, and the searches tried."""

    candidate = models.ForeignKey("django_prospecting.Candidate", on_delete=models.CASCADE, related_name="checks")
    kind = models.CharField(max_length=16, choices=CheckKind.choices)
    result = models.CharField(max_length=16, choices=CheckResult.choices, default=CheckResult.NO_INFO)
    value = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=500, blank=True, default="")
    checked_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["candidate", "pk"]
        constraints = [models.UniqueConstraint(fields=["candidate", "kind"], name="prospecting_check_kind_uniq")]

    def __str__(self) -> str:
        return f"{self.kind}: {self.result}"
