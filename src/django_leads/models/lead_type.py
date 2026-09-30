# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.validators import RegexValidator
from django.db import models
from django_utils.models.base_model import BaseModel

validate_lead_type_code = RegexValidator(r"^[A-Z0-9_]+$", "Upper-case code, e.g. RETAILER.")


class LeadType(BaseModel):
    """A kind of lead the channel works with (retailer, wholesale, …) — configuration, not code.

    `Company.lead_type` holds the `code`; `UNKNOWN` is built in and never a row. Communicator templates target a
    lead type through their `audience`.
    """

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="lead_types")
    code = models.CharField(max_length=32, validators=[validate_lead_type_code])
    label = models.CharField(max_length=128)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "code"]
        constraints = [models.UniqueConstraint(fields=["channel", "code"], name="leads_lead_type_channel_code_uniq")]

    def __str__(self) -> str:
        return f"{self.label} ({self.code})"
