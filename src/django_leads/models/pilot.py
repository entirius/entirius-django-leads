# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import PilotResult


class Pilot(BaseModel):
    """A trial run with a company (segment S5 path): dates, what counts as success, the outcome and the date the
    company moves to a paid licence. Written only through `pilot_service`."""

    company = models.ForeignKey("django_leads.Company", on_delete=models.CASCADE, related_name="pilots")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    success_criterion = models.TextField(blank=True, default="")
    result = models.CharField(max_length=16, choices=PilotResult.choices, default=PilotResult.PENDING)
    result_note = models.TextField(blank=True, default="")
    license_switch_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [models.Index(fields=["company", "result"], name="leads_pilot_company_result")]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.start_date}:{self.result}"
