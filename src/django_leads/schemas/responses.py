# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Response schemas of the leads admin API v2."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Stage id.", examples=[100])
    key: str = Field(description="Slug.", examples=["new"])
    label: str = Field(description="Label.", examples=["New"])
    order: int = Field(description="Position in the pipeline.", examples=[0])
    kind: str = Field(description="open, won, lost or unresponsive.", examples=["open"])
    is_terminal: bool = Field(description="No further stages.", examples=[False])
    on_reply: bool = Field(description="Target on a reply.", examples=[False])


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Contact id.", examples=[100])
    company_id: int = Field(description="Company id.", examples=[100])
    email: str = Field(description="Email; empty when unknown.", examples=["jan@example-shop-2.test"])
    first_name: str = Field(description="First name.", examples=["Jan"])
    last_name: str = Field(description="Last name.", examples=["Kowalski"])
    job_title: str = Field(description="Job title.", examples=["Owner"])
    phone: str = Field(description="Phone.", examples=[""])
    language: str | None = Field(description="ISO 639-1 code.", examples=["pl"])
    is_primary: bool = Field(description="Primary contact.", examples=[True])
    source: str = Field(description="csv, form, manual or connector.", examples=["csv"])
    legal_basis: str | None = Field(description="GDPR legal basis; null when none.", examples=["consent"])
    opt_out_at: datetime | None = Field(description="Opted out.", examples=[None])
    anonymised_at: datetime | None = Field(description="Anonymised.", examples=[None])

    @classmethod
    def of(cls, contact) -> "ContactResponse":
        fields = {name: getattr(contact, name) for name in cls.model_fields if name != "language"}
        return cls.model_validate({**fields, "language": contact.language.iso2.lower() if contact.language else None})


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Activity id.", examples=[1])
    company_id: int = Field(description="Company id.", examples=[100])
    contact_id: int | None = Field(description="Contact id.", examples=[None])
    kind: str = Field(description="Activity kind.", examples=["import"])
    message: str = Field(description="Short description.", examples=["import matched"])
    data: dict[str, Any] = Field(description="Structured details.", examples=[{}])
    actor: str = Field(description="Username, system or import:<batch id>.", examples=["system"])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Company id.", examples=[100])
    name: str = Field(description="Name.", examples=["Example Shop"])
    domain: str = Field(description="Registrable domain.", examples=["example-shop-2.test"])
    website: str = Field(description="Website.", examples=[""])
    company_type: str = Field(description="MANUFACTURER, WHOLESALE, RETAILER or UNKNOWN.", examples=["RETAILER"])
    industry: str = Field(description="Industry.", examples=[""])
    description: str = Field(description="Description.", examples=[""])
    platform: str = Field(description="Shop platform.", examples=[""])
    hooks: list[Any] = Field(description="Outreach hooks (read-only).", examples=[[]])
    stage: StageResponse = Field(description="Current stage.")
    stage_entered_at: datetime = Field(description="Entered the current stage.", examples=["2026-09-13T12:00:00Z"])
    source: str = Field(description="csv, form, manual or connector.", examples=["csv"])
    external_ref: str = Field(description="External reference.", examples=[""])
    do_not_contact: bool = Field(description="Never contact.", examples=[False])
    customer_uid: str | None = Field(description="Linked customer uid (read-only).", examples=[None])
    rotation_count: int = Field(description="Contact rotations.", examples=[0])
    last_activity_at: datetime | None = Field(description="Last timeline entry.", examples=[None])

    @classmethod
    def of(cls, company) -> "CompanyResponse":
        fields = {name: getattr(company, name) for name in cls.model_fields if name not in ("stage", "customer_uid")}
        uid = str(company.customer_uid) if company.customer_uid else None
        return cls.model_validate({**fields, "stage": company.stage, "customer_uid": uid})


class CompanyDetailResponse(CompanyResponse):
    contacts: list[ContactResponse] = Field(description="Contacts of the company.")
    activities: list[ActivityResponse] = Field(description="Last 20 timeline entries, newest first.")


class ImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Batch id.", examples=[1])
    source: str = Field(description="Source.", examples=["csv"])
    filename: str = Field(description="Uploaded file name.", examples=["leads.csv"])
    status: str = Field(description="pending, running, done or failed.", examples=["done"])
    created_count: int = Field(description="Rows that created a company.", examples=[8])
    matched_count: int = Field(description="Rows that matched a company.", examples=[1])
    skipped_count: int = Field(description="Rows skipped.", examples=[3])
    created_by: str = Field(description="Uploader.", examples=["admin"])
    created_at: datetime = Field(description="Uploaded.", examples=["2026-09-13T12:00:00Z"])


class ImportReportEntry(BaseModel):
    row: int = Field(description="CSV line number (header = 1); 0 for a file-level failure.", examples=[2])
    action: str = Field(description="created, matched, skipped or failed.", examples=["skipped"])
    reason: str = Field(description="Skip or failure reason.", examples=["no_domain_no_email"])


class ImportBatchDetailResponse(ImportBatchResponse):
    report: list[ImportReportEntry] = Field(description="One entry per processed row.")


class _Page(BaseModel):
    count: int = Field(description="Total matching rows.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])


class CompanyListResponse(_Page):
    results: list[CompanyResponse] = Field(description="Companies of the page.")


class ContactListResponse(_Page):
    results: list[ContactResponse] = Field(description="Contacts of the page.")


class StageListResponse(BaseModel):
    results: list[StageResponse] = Field(description="Stages in pipeline order.")


class ActivityListResponse(_Page):
    results: list[ActivityResponse] = Field(description="Timeline entries, newest first.")


class ImportBatchListResponse(_Page):
    results: list[ImportBatchResponse] = Field(description="Batches, newest first.")
