# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models

from django_leads.enums import ActivityKind


class Activity(models.Model):
    """Timeline entry of a company; written only by `activity_service`."""

    company = models.ForeignKey("django_leads.Company", on_delete=models.CASCADE, related_name="activities")
    contact = models.ForeignKey(
        "django_leads.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="activities"
    )
    kind = models.CharField(max_length=32, choices=ActivityKind.choices)
    message = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True)
    actor = models.CharField(max_length=128, default="system")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name_plural = "activities"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}"
