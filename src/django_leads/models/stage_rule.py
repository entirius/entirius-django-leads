# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.exceptions import ValidationError
from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import ContactStrategy, RuleAction, RuleTrigger


class StageRule(BaseModel):
    """What happens when a company enters a stage or its intel is ready; evaluated by `rule_service`."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="rules")
    trigger = models.CharField(max_length=16, choices=RuleTrigger.choices)
    stage = models.ForeignKey(
        "django_leads.Stage", on_delete=models.CASCADE, null=True, blank=True, related_name="rules"
    )
    action = models.CharField(max_length=16, choices=RuleAction.choices, default=RuleAction.COMMUNICATE)
    template_key = models.CharField(max_length=128, blank=True, default="")
    contact_strategy = models.CharField(max_length=16, choices=ContactStrategy.choices, default=ContactStrategy.PRIMARY)
    require_hooks = models.BooleanField(default=True)
    require_email = models.BooleanField(default=True)
    # No effect and not exposed by the API: the outreach gate always requires a legal basis.
    require_legal_basis = models.BooleanField(default=True)
    cooldown_hours = models.PositiveIntegerField(default=24)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.trigger}:{self.action}"

    def clean(self) -> None:
        errors = {}
        if self.trigger == RuleTrigger.STAGE_ENTERED and self.stage_id is None:
            errors["stage"] = "required for stage_entered rules"
        if self.stage_id and self.stage.channel_id != self.channel_id:
            errors["stage"] = "belongs to another channel"
        if self.action == RuleAction.COMMUNICATE and not self.template_key:
            errors["template_key"] = "required for communicate rules"
        if errors:
            raise ValidationError(errors)
