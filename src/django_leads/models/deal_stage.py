# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import DealStageKind


class DealStage(BaseModel):
    """One step of the channel's sales pipeline; a new deal enters the first `open` stage by `order`."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="deal_stages")
    key = models.SlugField(max_length=64)
    label = models.CharField(max_length=128)
    order = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(max_length=16, choices=DealStageKind.choices, default=DealStageKind.OPEN)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="leads_dealstage_channel_key_uniq")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.key}"
