# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models

from django_leads.enums import ActivityKind


class Activity(models.Model):
    """History entry of a channel, mostly about a company; written only by `activity_service`.

    `channel` is set on every entry (the history list reads it); `company` is null only for entries about the
    channel itself (settings changes)."""

    channel = models.ForeignKey(
        "django_leads.Channel", on_delete=models.CASCADE, null=True, blank=True, related_name="activities"
    )
    company = models.ForeignKey(
        "django_leads.Company", on_delete=models.CASCADE, null=True, blank=True, related_name="activities"
    )
    contact = models.ForeignKey(
        "django_leads.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="activities"
    )
    # The deal the entry is about; the entry stays on the company's timeline when the deal is deleted.
    deal = models.ForeignKey(
        "django_leads.Deal", on_delete=models.SET_NULL, null=True, blank=True, related_name="activities"
    )
    kind = models.CharField(max_length=32, choices=ActivityKind.choices)
    message = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True)
    actor = models.CharField(max_length=128, default="system")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name_plural = "activities"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["channel", "created_at"], name="django_lead_act_chan_idx"),
            models.Index(fields=["channel", "actor", "created_at"], name="django_lead_act_actor_idx"),
            models.Index(fields=["channel", "kind", "created_at"], name="django_lead_act_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}"

    def save(self, *args, **kwargs):
        if self.channel_id is None and self.company_id is not None:
            self.channel_id = self.company.channel_id
        super().save(*args, **kwargs)
