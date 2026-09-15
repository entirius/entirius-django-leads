# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_agreements.enums import LegalBasis
from django_utils.models.base_model import BaseModel

from django_leads.enums import LeadSource


class Contact(BaseModel):
    """A person at a company; `(company, email)` is unique for non-empty emails."""

    company = models.ForeignKey("django_leads.Company", on_delete=models.CASCADE, related_name="contacts")
    email = models.EmailField(blank=True, default="")
    first_name = models.CharField(max_length=128, blank=True, default="")
    last_name = models.CharField(max_length=128, blank=True, default="")
    job_title = models.CharField(max_length=128, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    language = models.ForeignKey("django_regional.Language", on_delete=models.SET_NULL, null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    source = models.CharField(max_length=16, choices=LeadSource.choices)
    # null = no legal basis recorded (rules skip the contact) — the API exposes it as null.
    legal_basis = models.CharField(max_length=32, choices=LegalBasis.choices, null=True, blank=True)  # noqa: DJ001
    # Soft reference to django_agreements.ConsentRecord — no FK across modules.
    consent_ref = models.IntegerField(null=True, blank=True)
    opt_out_at = models.DateTimeField(null=True, blank=True)
    anonymised_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_primary", "last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "email"], condition=~models.Q(email=""), name="leads_contact_company_email_uniq"
            )
        ]

    def __str__(self) -> str:
        return self.email or f"{self.first_name} {self.last_name}".strip()
