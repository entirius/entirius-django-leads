# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class ChannelManager(models.Manager):
    def get_by_natural_key(self, idx: str) -> "Channel":
        return self.get(idx=idx)


class Channel(BaseModel):
    """Pattern 2 scoping channel — own model, never auto-created from signals."""

    idx = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128, default="", blank=True)
    default_language = models.ForeignKey(
        "django_regional.Language",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads_default_channels",
    )
    languages = models.ManyToManyField("django_regional.Language", blank=True, related_name="leads_channels")
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    # Sender route (communicator key) of companies that have none of their own; blank = communicator's default.
    default_sender_route = models.CharField(max_length=64, blank=True, default="")

    # "Not now" loop (Q20): days until a contact is approached again when the reply gave no date, per intent class
    # (`{"not_now": 30, "out_of_office": 7}`); a missing key falls back to `LEADS_RESUME_DEFAULT_DAYS`.
    resume_days = models.JSONField(default=dict, blank=True)

    objects = ChannelManager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.idx

    def natural_key(self) -> tuple[str]:
        return (self.idx,)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.default_language and not self.languages.filter(pk=self.default_language_id).exists():
            self.languages.add(self.default_language)
