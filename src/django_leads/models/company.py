# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import CompanyType, LeadSource


class Company(BaseModel):
    """A prospect company; `(channel, domain)` is the dedup key — domain is always the registrable domain."""

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="companies")
    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=253)
    website = models.URLField(blank=True, default="")
    company_type = models.CharField(max_length=16, choices=CompanyType.choices, default=CompanyType.UNKNOWN)
    industry = models.CharField(max_length=128, blank=True, default="")
    description = models.TextField(blank=True, default="")
    platform = models.CharField(max_length=64, blank=True, default="")
    hooks = models.JSONField(default=list, blank=True)
    stage = models.ForeignKey("django_leads.Stage", on_delete=models.PROTECT, related_name="companies")
    stage_entered_at = models.DateTimeField()
    source = models.CharField(max_length=16, choices=LeadSource.choices)
    external_ref = models.CharField(max_length=128, blank=True, default="")
    do_not_contact = models.BooleanField(default=False)
    customer_uid = models.UUIDField(null=True, blank=True)
    rotation_count = models.PositiveSmallIntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "companies"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["channel", "domain"], name="leads_company_channel_domain_uniq")]
        indexes = [models.Index(fields=["channel", "stage"], name="leads_company_channel_stage")]

    def __str__(self) -> str:
        return f"{self.name} ({self.domain})"
