# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django.utils import timezone

from django_leads.enums import RuleOutcome


class RuleRun(models.Model):
    """One evaluation of a rule for a company; any run inside `cooldown_hours` stops the next one."""

    rule = models.ForeignKey("django_leads.StageRule", on_delete=models.CASCADE, related_name="runs")
    company = models.ForeignKey("django_leads.Company", on_delete=models.CASCADE, related_name="rule_runs")
    fired_at = models.DateTimeField(default=timezone.now)
    outcome = models.CharField(max_length=16, choices=RuleOutcome.choices)
    detail = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-fired_at", "-id"]
        indexes = [models.Index(fields=["rule", "company", "-fired_at"], name="leads_rulerun_rule_company")]

    def __str__(self) -> str:
        return f"{self.rule_id}:{self.company_id}:{self.outcome}"
