# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.db import models
from django_utils.models.base_model import BaseModel

from django_prospecting.enums import CandidateStatus


class Candidate(BaseModel):
    """A person found for a company, waiting for a salesperson's decision. Only an accepted candidate is handed to
    the caller (leads) as a contact; nothing here sends mail."""

    search = models.ForeignKey("django_prospecting.PeopleSearch", on_delete=models.CASCADE, related_name="candidates")
    channel_idx = models.CharField(max_length=64, db_index=True)
    subject_ref = models.CharField(max_length=128, db_index=True)
    first_name = models.CharField(max_length=128)
    last_name = models.CharField(max_length=128)
    job_title = models.CharField(max_length=128, blank=True, default="")
    profile_url = models.URLField(max_length=500, blank=True, default="")
    # Where and when the person was found.
    source = models.CharField(max_length=500, blank=True, default="")
    found_at = models.DateTimeField()
    # The best address (confirmed by the tester, else the first guess) and every guess with its verdict and source.
    email = models.EmailField(blank=True, default="")
    email_options = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=CandidateStatus.choices, default=CandidateStatus.PENDING)
    decided_by = models.CharField(max_length=150, blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=255, blank=True, default="")
    # The caller's reference of the contact made from an accepted candidate (`leads.Contact:7`).
    contact_ref = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["subject_ref", "pk"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"
