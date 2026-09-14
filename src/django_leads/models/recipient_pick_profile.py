# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class RecipientPickProfile(BaseModel):
    """Prompt choosing the contact to write to. Placeholders: {company_name} {hooks} {contacts_json}."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="recipient_profiles")
    key = models.SlugField(max_length=64, default="leads.pick_recipient")
    prompt_text = models.TextField()
    json_schema = models.JSONField(default=dict)
    model = models.CharField(max_length=128)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="leads_recipientprofile_channel_key")]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.key}"
