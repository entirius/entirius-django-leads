# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import StageEvent


class StageEventRule(BaseModel):
    """Where an event moves a company of the channel: a null `target_stage` keeps the stage, no row = no move."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="stage_events")
    event = models.CharField(max_length=32, choices=StageEvent.choices)
    target_stage = models.ForeignKey(
        "django_leads.Stage", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Move only to a stage of a higher `order` than the current one.
    forward_only = models.BooleanField(default=True)

    class Meta:
        ordering = ["event"]
        constraints = [models.UniqueConstraint(fields=["channel", "event"], name="leads_stageevent_channel_event_uniq")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.event}"
