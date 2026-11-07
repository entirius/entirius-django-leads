# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class ScoringProfile(BaseModel):
    """How `scoring_service` rates a company: points per matching rule, thresholds turning points into a segment
    (S1 best, S4 worst), disqualifiers forcing S4. Shapes are validated by the API schemas, not here."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="scoring_profiles")
    key = models.SlugField(max_length=64, default="leads.scoring")
    # {"S": 20, "M": 40, "L": 70}: points >= L is S1, >= M S2, >= S S3, below S4.
    thresholds = models.JSONField(default=dict)
    # [{"field": "lead_type", "op": "in", "value": ["RETAILER"], "points": 30, "label": "..."}]
    rules = models.JSONField(default=list)
    # [{"field": "platform", "op": "eq", "value": "wix", "label": "reason shown in the timeline"}]
    disqualifiers = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="leads_scoringprofile_channel_key")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.key}"
