# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.db import models
from django_utils.models.base_model import BaseModel

from django_prospecting.enums import CompanySource


class CompanyProspect(BaseModel):
    """Placeholder of the Companies part: a company that may become a lead. No search runs yet — sources such as
    public registers come later; today rows are only added by hand."""

    channel_idx = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=253, blank=True, default="")
    source = models.CharField(max_length=16, choices=CompanySource.choices, default=CompanySource.MANUAL)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name
