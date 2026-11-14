# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import RecurringPeriod

MONEY = {"max_digits": 14, "decimal_places": 2, "null": True, "blank": True}


class Deal(BaseModel):
    """A sale on a company, moved through the channel's deal stages — written only by `deal_service`."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="deals")
    company = models.ForeignKey("django_leads.Company", on_delete=models.CASCADE, related_name="deals")
    contact = models.ForeignKey(
        "django_leads.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="deals"
    )
    title = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    stage = models.ForeignKey("django_leads.DealStage", on_delete=models.PROTECT, related_name="deals")
    stage_entered_at = models.DateTimeField()
    value_one_off = models.DecimalField(**MONEY)
    value_recurring = models.DecimalField(**MONEY)
    recurring_period = models.CharField(max_length=8, choices=RecurringPeriod.choices, default=RecurringPeriod.MONTH)
    currency = models.ForeignKey(
        "django_regional.Currency", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    expected_close_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True, default="")
    # Set by `deal_service.archive` (closed deals only): out of the lists, the board and the company's counts.
    archived_at = models.DateTimeField(null=True, blank=True)
    # Values of the channel's deal `AttributeDefinition`s — written only through `attribute_service`.
    attributes = models.JSONField(default=dict, blank=True)
    created_by = models.CharField(max_length=128, default="system")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["channel", "stage"], name="leads_deal_channel_stage"),
            models.Index(fields=["channel", "owner"], name="leads_deal_channel_owner"),
            models.Index(fields=["company"], name="leads_deal_company"),
        ]

    def __str__(self) -> str:
        return self.title
