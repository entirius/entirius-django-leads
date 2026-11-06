# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Response schemas of the leads admin API v2."""

from datetime import date, datetime
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
    on_sent: bool = Field(description="Target when a message is sent (forward only).", examples=[False])


class LeadTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Lead type id.", examples=[100])
    code: str = Field(description="Upper-case code; what companies and template audiences hold.", examples=["RETAILER"])
    label: str = Field(description="Label.", examples=["Retailer"])
    order: int = Field(description="Display order.", examples=[0])
    is_active: bool = Field(description="Offered, filtered on and guessed.", examples=[True])


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
    wrong_person_at: datetime | None = Field(
        description="A reply said this is the wrong person; no more outreach to the contact.", examples=[None]
    )

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
    lead_type: str = Field(description="Lead type code of the channel, or UNKNOWN.", examples=["RETAILER"])
    industry: str = Field(description="Industry.", examples=[""])
    description: str = Field(description="Description.", examples=[""])
    platform: str = Field(description="Shop platform.", examples=[""])
    hooks: list[Any] = Field(description="Outreach hooks (read-only).", examples=[[]])
    stage: StageResponse = Field(description="Current stage.")
    stage_entered_at: datetime = Field(description="Entered the current stage.", examples=["2026-09-13T12:00:00Z"])
    source: str = Field(description="csv, form, manual or connector.", examples=["csv"])
    origin: str = Field(
        description="Business origin (ecom.house, a partner); empty when unknown.", examples=["ecom.house"]
    )
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
    customer_name: str = Field(
        default="", description="Name of the linked customer; empty without accounts.", examples=["Jan Kowalski"]
    )


class ImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Batch id.", examples=[1])
    source: str = Field(description="Source.", examples=["csv"])
    filename: str = Field(description="Uploaded file name.", examples=["leads.csv"])
    status: str = Field(description="pending, running, done or failed.", examples=["done"])
    created_count: int = Field(description="Rows that created a company.", examples=[8])
    matched_count: int = Field(description="Rows that matched a company.", examples=[1])
    skipped_count: int = Field(description="Rows skipped.", examples=[3])
    row_count: int = Field(description="Data rows processed so far.", examples=[12])
    size_bytes: int = Field(description="Uploaded file size.", examples=[1024])
    created_by: str = Field(description="Uploader.", examples=["admin"])
    created_at: datetime = Field(description="Uploaded.", examples=["2026-09-13T12:00:00Z"])


class ImportReportEntry(BaseModel):
    row: int = Field(description="CSV line number (header = 1); 0 for a file-level failure.", examples=[2])
    reason: str = Field(description="Skip reason or file-level error code.", examples=["no_domain_no_email"])


class ImportBatchDetailResponse(ImportBatchResponse):
    report: list[ImportReportEntry] = Field(
        description="Skipped rows (row number and reason code, no row values), capped at LEADS_IMPORT_REPORT_MAX."
    )


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


class LeadTypeListResponse(BaseModel):
    results: list[LeadTypeResponse] = Field(
        description="Lead types in display order (UNKNOWN is built in, never listed)."
    )


class ActivityListResponse(_Page):
    results: list[ActivityResponse] = Field(description="Timeline entries, newest first.")


class ImportBatchListResponse(_Page):
    results: list[ImportBatchResponse] = Field(description="Batches, newest first.")


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Rule id.", examples=[100])
    trigger: str = Field(description="stage_entered or intel_ready.", examples=["stage_entered"])
    stage_id: int | None = Field(description="Stage id.", examples=[102])
    action: str = Field(description="request_audit, communicate or score.", examples=["communicate"])
    template_key: str = Field(description="Communicator template.", examples=["lead.cold.b2b"])
    contact_strategy: str = Field(description="primary or ai_pick.", examples=["primary"])
    require_hooks: bool = Field(description="Skip companies without hooks.", examples=[True])
    require_email: bool = Field(description="Skip companies without a contact email.", examples=[True])
    cooldown_hours: int = Field(description="No re-run within.", examples=[24])
    is_active: bool = Field(description="Evaluated at all.", examples=[True])
    order: int = Field(description="Evaluation order.", examples=[0])


class RuleListResponse(BaseModel):
    results: list[RuleResponse] = Field(description="Rules in evaluation order.")


class RuleRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Run id.", examples=[1])
    rule_id: int = Field(description="Rule id.", examples=[100])
    company_id: int = Field(description="Company id.", examples=[102])
    fired_at: datetime = Field(description="Evaluated.", examples=["2026-09-13T12:00:00Z"])
    outcome: str = Field(description="fired, skipped, blocked or cooldown; empty while claimed.", examples=["fired"])
    detail: str = Field(description="Short reason.", examples=["message 7 review_required"])


class RuleRunListResponse(_Page):
    results: list[RuleRunResponse] = Field(description="Runs, newest first.")


class ProfileResponse(BaseModel):
    """List shape — the prompt is never listed."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Profile id.", examples=[100])
    key: str = Field(description="Profile key.", examples=["leads.analysis"])
    model: str = Field(description="Toolbox model.", examples=["fake-chat"])


class ProfileDetailResponse(ProfileResponse):
    prompt_text: str = Field(description="Prompt with placeholders.", examples=["..."])
    json_schema: dict[str, Any] = Field(description="Output JSON schema.", examples=[{}])


class AnalysisProfileResponse(ProfileResponse):
    is_active: bool = Field(description="Used by the analysis.", examples=[True])


class AnalysisProfileDetailResponse(ProfileDetailResponse):
    is_active: bool = Field(description="Used by the analysis.", examples=[True])


class ProfileListResponse(BaseModel):
    results: list[ProfileResponse] = Field(description="Profiles of the channel.")


class AnalysisProfileListResponse(BaseModel):
    results: list[AnalysisProfileResponse] = Field(description="Profiles of the channel.")


class ScoringProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Profile id.", examples=[100])
    key: str = Field(description="Profile key.", examples=["leads.scoring"])
    thresholds: dict[str, int] = Field(
        description="Points for S3 (S), S2 (M), S1 (L).", examples=[{"S": 20, "M": 40, "L": 70}]
    )
    rules: list[dict[str, Any]] = Field(description="Point rules.", examples=[[]])
    disqualifiers: list[dict[str, Any]] = Field(description="Conditions forcing S4.", examples=[[]])
    is_active: bool = Field(description="Used by scoring.", examples=[True])


class ScoringProfileListResponse(BaseModel):
    results: list[ScoringProfileResponse] = Field(description="Profiles of the channel.")


class ScoreResponse(BaseModel):
    segment: str = Field(description="S1 (best) to S4.", examples=["S2"])
    points: int = Field(description="Sum of the matching rules.", examples=[45])
    disqualified: bool = Field(description="A disqualifier forced S4.", examples=[False])
    reasons: list[str] = Field(description="Matching rules or disqualifiers.", examples=[["retailer: +30"]])
    profile_id: int | None = Field(description="Profile used.", examples=[100])


class PilotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Pilot id.", examples=[100])
    company_id: int = Field(description="Company id.", examples=[101])
    start_date: date = Field(description="First day.", examples=["2026-11-10"])
    end_date: date | None = Field(description="Last day.", examples=["2026-12-10"])
    success_criterion: str = Field(description="What counts as success.", examples=["10 orders in 30 days"])
    result: str = Field(description="pending, success, failure or cancelled.", examples=["pending"])
    result_note: str = Field(description="Outcome in words.", examples=[""])
    license_switch_date: date | None = Field(description="Day of the switch to a licence.", examples=["2026-12-15"])


class PilotListResponse(_Page):
    results: list[PilotResponse] = Field(description="Pilots, newest start first.")


class LicenseSwitchDue(BaseModel):
    pilot_id: int = Field(description="Pilot id.", examples=[100])
    company_id: int = Field(description="Company id.", examples=[101])
    domain: str = Field(description="Company domain.", examples=["example-shop-2.test"])
    switch_date: date = Field(description="Licence switch day.", examples=["2026-12-15"])


class PilotReportResponse(BaseModel):
    as_of: date = Field(description="Day the report is computed for.", examples=["2026-11-10"])
    total: int = Field(description="All pilots of the channel.", examples=[4])
    by_result: dict[str, int] = Field(description="Pilots per result, every result present.", examples=[{"pending": 2}])
    running: int = Field(description="Pending pilots started and not past their end date.", examples=[2])
    overdue: int = Field(description="Pending pilots past their end date.", examples=[1])
    avg_length_days: float | None = Field(
        description="Mean length of finished pilots with an end date.", examples=[28.5]
    )
    license_switch_due: list[LicenseSwitchDue] = Field(description="Successful pilots switching within 30 days.")


class FunnelTransition(BaseModel):
    from_stage: str = Field(description="Stage key left.", examples=["new"])
    to_stage: str = Field(description="Stage key entered.", examples=["contacted"])
    count: int = Field(description="Transitions in the window.", examples=[12])
    avg_seconds_in_from_stage: float | None = Field(
        description="Mean time spent in `from_stage` first.", examples=[86400.0]
    )


class FunnelStage(BaseModel):
    stage: str = Field(description="Stage key.", examples=["contacted"])
    current: int = Field(description="Companies in the stage now (origin filter, no date window).", examples=[5])
    entered: int = Field(description="Transitions into the stage in the window.", examples=[12])
    left: int = Field(description="Transitions out of the stage in the window.", examples=[7])
    avg_seconds_in_stage: float | None = Field(
        description="Mean time in the stage before leaving.", examples=[172800.0]
    )


class FunnelOrigin(BaseModel):
    origin: str = Field(description="Origin; empty = unknown.", examples=["ecom.house"])
    companies: int = Field(description="Companies of the channel.", examples=[40])


class FunnelResponse(BaseModel):
    companies: int = Field(description="Companies matching the origin filter.", examples=[40])
    transitions: list[FunnelTransition] = Field(description="Per (from, to) pair, most frequent first.")
    stages: list[FunnelStage] = Field(description="Per stage seen.")
    origins: list[FunnelOrigin] = Field(description="Companies per origin, whole channel (never filtered).")


class DraftResponse(BaseModel):
    message_id: int = Field(description="Communicator message id.", examples=[7])
    status: str = Field(description="Message status.", examples=["review_required"])


class AuditRequestedResponse(BaseModel):
    audit_id: str = Field(description="siteintel audit id.", examples=["6f1c..."])
    status: str = Field(description="Audit status.", examples=["pending"])


class CustomerLinkResponse(BaseModel):
    customer_uid: str = Field(description="Linked customer uid.", examples=["0b8f..."])


class DevEvaluateResponse(BaseModel):
    runs: list[RuleRunResponse] = Field(description="Runs of this evaluation, in rule order.")


class DevRotateResponse(BaseModel):
    rotated: int = Field(description="Threads that rotated or parked a company.", examples=[1])


class DevRetryAnalysesResponse(BaseModel):
    recovered: int = Field(description="Analyses that succeeded on retry.", examples=[1])
    failed: int = Field(description="Analyses whose retry failed again.", examples=[0])


class DevAnonymiseResponse(BaseModel):
    anonymised: dict[str, int] = Field(
        description="Anonymised contacts per channel idx.", examples=[{"default-europe": 1}]
    )


class GdprExportResponse(BaseModel):
    email: str = Field(description="Normalised address of the data subject.", examples=["jan@example-shop-2.test"])
    generated_at: datetime = Field(description="When the export was built.", examples=["2026-09-14T06:00:00Z"])
    modules: dict[str, dict[str, Any]] = Field(
        description="App name → rows keyed by model name; apps without GDPR hooks are absent.",
        examples=[{"django_leads": {"Contact": [], "Company": [], "Activity": []}}],
    )


class GdprEraseResponse(BaseModel):
    modules: dict[str, dict[str, int]] = Field(
        description="App name → rows touched per kind.", examples=[{"django_leads": {"contacts": 1, "activities": 4}}]
    )


class ReplyRouteResponse(BaseModel):
    intent: str = Field(description="The reply intent class.", examples=["not_interested"])
    action: str = Field(description="reply_stage, stage or none.", examples=["stage"])
    stage_id: int | None = Field(description="Target stage of action stage.", examples=[4])
    stage_key: str | None = Field(description="Its key.", examples=["lost"])
    flag_contact: bool = Field(description="Flag the replying contact as the wrong person.", examples=[False])
    is_default: bool = Field(description="No row: the code default applies.", examples=[True])


class ReplyRouteListResponse(BaseModel):
    results: list[ReplyRouteResponse] = Field(description="One entry per intent class.", examples=[[]])
