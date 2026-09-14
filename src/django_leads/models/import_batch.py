# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import ImportStatus, LeadSource


class ImportBatch(BaseModel):
    """One uploaded file; counters, `last_row_done` and the capped `report` (`{row, reason}` of skipped rows)
    are committed with every chunk. The CSV itself is never stored."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="import_batches")
    source = models.CharField(max_length=16, choices=LeadSource.choices, default=LeadSource.CSV)
    filename = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=ImportStatus.choices, default=ImportStatus.PENDING)
    created_count = models.PositiveIntegerField(default=0)
    matched_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    # CSV line of the last committed row — a retried run resumes after it.
    last_row_done = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=list, blank=True)
    created_by = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        verbose_name_plural = "import batches"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.filename} [{self.status}]"
