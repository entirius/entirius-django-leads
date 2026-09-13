# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class AnalysisProfile(BaseModel):
    """Prompt turning siteintel reports into hooks. Placeholders: {company_name} {website} {lighthouse_summary}
    {urlscan_summary} {heuristic_summary}. Never logged, never in API lists."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="analysis_profiles")
    key = models.SlugField(max_length=64, default="leads.analysis")
    prompt_text = models.TextField()
    json_schema = models.JSONField(default=dict)
    model = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="leads_analysisprofile_channel_key")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.key}"
