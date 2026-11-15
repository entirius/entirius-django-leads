# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.db import models
from django_utils.models.base_model import BaseModel

from django_prospecting.enums import SearchReason, SearchStatus


class PeopleSearch(BaseModel):
    """One search for new people at one company. `subject_ref` is the caller's opaque company reference
    (`leads.Company:42`); it is never parsed here."""

    channel_idx = models.CharField(max_length=64, db_index=True)
    subject_ref = models.CharField(max_length=128, db_index=True)
    company_name = models.CharField(max_length=255)
    domain = models.CharField(max_length=253)
    reason = models.CharField(max_length=16, choices=SearchReason.choices)
    status = models.CharField(max_length=16, choices=SearchStatus.choices, default=SearchStatus.QUEUED)
    # Provider calls made (each counts against the daily budget).
    attempts = models.PositiveSmallIntegerField(default=0)
    # The address pattern of the company (`{first}.{last}`), "" when none could be worked out; with its evidence.
    mail_pattern = models.CharField(max_length=32, blank=True, default="")
    pattern_source = models.CharField(max_length=500, blank=True, default="")
    requested_by = models.CharField(max_length=150, blank=True, default="")
    detail = models.CharField(max_length=255, blank=True, default="")
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.company_name} ({self.status})"
