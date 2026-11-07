# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_agreements.enums import LegalBasis
from django_utils.models.base_model import BaseModel

from django_leads.enums import EmailStatus, LeadSource


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
    wrong_person_at = models.DateTimeField(
        null=True, blank=True, help_text="A reply said this is the wrong person (T4): no more outreach to this contact."
    )
    # Address verification (Q5): `unknown` until the first check; the reason is a short code, never the address.
    email_status = models.CharField(max_length=8, choices=EmailStatus.choices, default=EmailStatus.UNKNOWN)
    email_status_reason = models.CharField(max_length=64, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)
    # "Not now" loop (Q20): a reply of class not_now / out_of_office set the day the contact is approached again.
    # Cleared when it is done, cancelled (a newer reply, an opt-out) or the contact is anonymised.
    resume_at = models.DateTimeField(null=True, blank=True)
    resume_intent = models.CharField(max_length=24, blank=True, default="")

    class Meta:
        ordering = ["-is_primary", "last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "email"], condition=~models.Q(email=""), name="leads_contact_company_email_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["email_status", "email_verified_at"], name="leads_contact_email_status"),
            models.Index(fields=["resume_at"], name="leads_contact_resume_at"),
        ]

    def __str__(self) -> str:
        return self.email or f"{self.first_name} {self.last_name}".strip()
