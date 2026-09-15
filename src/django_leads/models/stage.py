# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import StageKind


class Stage(BaseModel):
    """One step of a channel's pipeline; new companies enter the lowest `order`."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="stages")
    key = models.SlugField(max_length=64)
    label = models.CharField(max_length=128)
    order = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(max_length=16, choices=StageKind.choices, default=StageKind.OPEN)
    is_terminal = models.BooleanField(default=False)
    on_reply = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="leads_stage_channel_key_uniq")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.key}"
