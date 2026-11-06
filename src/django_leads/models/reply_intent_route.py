# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.exceptions import ValidationError
from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import ReplyIntent, ReplyStageAction


class ReplyIntentRoute(BaseModel):
    """What a reply of one intent class (classified by communicator, T4) does to the company in one channel.
    No row = the code default (`reply_route_service.DEFAULTS`): what a reply did before T4, except that an
    out-of-office reply leaves the stage alone and a wrong-person reply flags the contact."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="reply_routes")
    intent = models.CharField(max_length=24, choices=ReplyIntent.choices)
    action = models.CharField(max_length=16, choices=ReplyStageAction.choices, default=ReplyStageAction.REPLY_STAGE)
    stage = models.ForeignKey(
        "django_leads.Stage", on_delete=models.CASCADE, null=True, blank=True, related_name="reply_routes"
    )
    flag_contact = models.BooleanField(
        default=False, help_text="Mark the replying contact as the wrong person: no more outreach to them."
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "intent"], name="leads_replyroute_channel_intent")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.intent}:{self.action}"

    def clean(self) -> None:
        super().clean()
        if self.action == ReplyStageAction.STAGE and self.stage_id is None:
            raise ValidationError({"stage": "action stage needs a stage"})
        if self.action != ReplyStageAction.STAGE and self.stage_id is not None:
            raise ValidationError({"stage": "a stage is only for action stage"})
        if self.stage_id is not None and self.stage.channel_id != self.channel_id:
            raise ValidationError({"stage": "the stage belongs to another channel"})
