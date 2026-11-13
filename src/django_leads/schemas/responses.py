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


class StageEventResponse(BaseModel):
    event: str = Field(description="first_sent, reply, reply:<class> or sequence_finished.", examples=["reply"])
    target_stage_key: str | None = Field(description="Stage it moves to; null keeps the stage.", examples=["replied"])
    forward_only: bool = Field(description="Moves only to a stage of a higher order.", examples=[True])
    configured: bool = Field(description="A row exists; false = the event moves nothing.", examples=[True])

    @classmethod
    def of(cls, entry) -> "StageEventResponse":
        key = entry.target_stage.key if entry.target_stage else None
        return cls(
            event=entry.event, target_stage_key=key, forward_only=entry.forward_only, configured=entry.configured
        )


class StageEventListResponse(BaseModel):
    results: list[StageEventResponse] = Field(description="One entry per event.")


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
    wrong_person_at: datetime | None = Field(description="Flagged by a wrong-person reply.", examples=[None])
    attributes: dict[str, Any] = Field(description="Contact attribute values by key.", examples=[{"role": "buyer"}])

    @classmethod
    def of(cls, contact) -> "ContactResponse":
        fields = {name: getattr(contact, name) for name in cls.model_fields if name != "language"}
        return cls.model_validate({**fields, "language": contact.language.iso2.lower() if contact.language else None})


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Activity id.", examples=[1])
    company_id: int = Field(description="Company id.", examples=[100])
    contact_id: int | None = Field(description="Contact id.", examples=[None])
    deal_id: int | None = Field(description="Deal the entry is about; null = none or deleted.", examples=[None])
    kind: str = Field(description="Activity kind.", examples=["import"])
    message: str = Field(description="Short description.", examples=["import matched"])
    data: dict[str, Any] = Field(description="Structured details.", examples=[{}])
    actor: str = Field(description="Username, system or import:<batch id>.", examples=["system"])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])


class OwnerResponse(BaseModel):
    """A staff user as leads shows one — no e-mail, no roles."""

    id: int = Field(description="User id.", examples=[7])
    username: str = Field(description="Username.", examples=["anna"])
    name: str = Field(description="Full name; empty when the user has none.", examples=["Anna Nowak"])

    @classmethod
    def of(cls, user) -> "OwnerResponse":
        return cls(id=user.pk, username=user.get_username(), name=user.get_full_name())


class OwnerListResponse(BaseModel):
    results: list[OwnerResponse] = Field(description="Active staff users by name, at most 50.")


class OwnerAssignResponse(BaseModel):
    updated: int = Field(description="Companies whose owner changed.", examples=[3])


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
    external_ref: str = Field(description="External reference.", examples=[""])
    do_not_contact: bool = Field(description="Never contact.", examples=[False])
    customer_uid: str | None = Field(description="Linked customer uid (read-only).", examples=[None])
    rotation_count: int = Field(description="Contact rotations.", examples=[0])
    last_activity_at: datetime | None = Field(description="Last timeline entry.", examples=[None])
    origin: str = Field(description="Where the lead came from.", examples=["fair"])
    attributes: dict[str, Any] = Field(description="Company attribute values by key.", examples=[{"size": "micro"}])
    score_points: int | None = Field(description="Points of the last scoring; null = never scored.", examples=[45])
    score_segment: str = Field(description="S1 (best) … S4 of the last scoring; empty = never scored.", examples=["S2"])
    scored_at: datetime | None = Field(description="Last scored.", examples=[None])
    owner: OwnerResponse | None = Field(description="Owner; null = none.", examples=[None])

    @classmethod
    def of(cls, company) -> "CompanyResponse":
        related = ("stage", "customer_uid", "owner")
        fields = {name: getattr(company, name) for name in cls.model_fields if name not in related}
        uid = str(company.customer_uid) if company.customer_uid else None
        owner = OwnerResponse.of(company.owner) if company.owner_id else None
        return cls.model_validate({**fields, "stage": company.stage, "customer_uid": uid, "owner": owner})


class DealCounts(BaseModel):
    open: int = Field(description="Open deals.", examples=[1])
    won: int = Field(description="Won deals.", examples=[0])
    lost: int = Field(description="Lost deals.", examples=[0])


class CompanyDetailResponse(CompanyResponse):
    deals: DealCounts = Field(description="Deals of the company per stage kind.")
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
    run_rules: bool = Field(description="Created companies enter their first stage with its rules.", examples=[False])
    owner_id: int | None = Field(description="Owner given to created and unowned matched companies.", examples=[None])
    rules_queued: int = Field(
        default=0, description="Rule evaluations queued: one per created company with run_rules.", examples=[0]
    )

    @classmethod
    def of(cls, batch) -> "ImportBatchResponse":
        response = cls.model_validate(batch)
        response.rules_queued = batch.created_count if batch.run_rules else 0
        return response


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
    conditions: list[dict[str, Any]] = Field(
        description="All must hold: {field, op, value}.", examples=[[{"field": "attr.size", "op": "eq", "value": "s"}]]
    )


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


class DraftResponse(BaseModel):
    message_id: int = Field(description="Communicator message id.", examples=[7])
    status: str = Field(description="Message status.", examples=["review_required"])


class ChannelSettingsResponse(BaseModel):
    idx: str = Field(description="Channel idx.", examples=["default-europe"])
    name: str = Field(description="Channel name.", examples=["Default Europe"])
    default_currency: str | None = Field(description="ISO 4217 of new deals; null = none.", examples=["PLN"])
    resume_days: dict[str, int] = Field(
        description="Effective days until a follow-up task per reply class (not_now, out_of_office).",
        examples=[{"not_now": 30, "out_of_office": 7}],
    )


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


class LegalBasisState(BaseModel):
    value: str | None = Field(description="GDPR legal basis; null when none.", examples=["legitimate_interest"])
    source: str | None = Field(description="admin, import or form; null when never recorded.", examples=["import"])
    set_at: datetime | None = Field(description="When it was recorded.", examples=["2026-11-01T10:00:00Z"])


class SuppressionRow(BaseModel):
    id: int = Field(description="Suppression id.", examples=[3])
    kind: str = Field(description="email, domain or email_token.", examples=["email"])
    source: str = Field(description="Who suppressed it.", examples=["manual"])
    reason: str = Field(description="Free text.", examples=[""])
    scope: str = Field(description="channel or install.", examples=["channel"])
    created_at: datetime = Field(description="Created.", examples=["2026-11-01T10:00:00Z"])


class AddressStatusState(BaseModel):
    status: str = Field(description="unknown, valid, risky or invalid.", examples=["invalid"])
    reason: str = Field(description="Why.", examples=["hard_bounce"])
    verified_at: datetime | None = Field(description="Checked.", examples=[None])


class CommunicatorAnswer(BaseModel):
    email: str = Field(description="Normalised address.", examples=["jan@example-shop-2.test"])
    refusal: str | None = Field(description="suppressed, address_invalid or null.", examples=[None])
    suppressions: list[SuppressionRow] = Field(description="Matching suppression rows.")
    address_status: AddressStatusState | None = Field(description="What communicator knows of the address.")


class RetentionState(BaseModel):
    days: int = Field(description="Idle days before anonymisation.", examples=[180])
    due_at: datetime | None = Field(description="Anonymised after, unless active again.", examples=[None])
    exempt_won: bool = Field(description="The company sits in a won stage: never anonymised.", examples=[False])


class MailableResponse(BaseModel):
    eligible: bool = Field(description="The outreach gate lets a draft go to the contact.", examples=[False])
    reasons: list[str] = Field(description="Every refusal, in gate order.", examples=[["opted_out", "suppressed"]])
    legal_basis: LegalBasisState = Field(description="Legal basis and where it came from.")
    communicator: CommunicatorAnswer | None = Field(description="Suppression answer; null without an email.")
    retention: RetentionState = Field(description="Retention clock of the company.")


class FunnelTransition(BaseModel):
    from_stage: str = Field(description="Stage key left.", examples=["new"])
    to_stage: str = Field(description="Stage key entered.", examples=["contacted"])
    count: int = Field(description="Moves.", examples=[12])
    mean_seconds: float | None = Field(description="Mean time in the source stage.", examples=[86400.0])


class FunnelStage(BaseModel):
    key: str = Field(description="Stage key.", examples=["contacted"])
    label: str = Field(description="Label.", examples=["Contacted"])
    current: int = Field(description="Companies in the stage now.", examples=[4])
    entered: int = Field(description="Moves into the stage in the period.", examples=[12])
    left: int = Field(description="Moves out of the stage in the period.", examples=[8])
    mean_seconds: float | None = Field(description="Mean time in the stage of the moves out.", examples=[3600.0])


class FunnelOrigin(BaseModel):
    origin: str = Field(description="Origin; empty = none recorded.", examples=["fair"])
    companies: int = Field(description="Companies created in the period.", examples=[20])


class FunnelResponse(BaseModel):
    transitions: list[FunnelTransition] = Field(description="Stage moves in the period, most frequent first.")
    stages: list[FunnelStage] = Field(description="Every stage in pipeline order.")
    origins: list[FunnelOrigin] = Field(description="Companies per origin, most first.")


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


class AttributeDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Definition id.", examples=[100])
    entity: str = Field(description="company, contact or deal.", examples=["company"])
    key: str = Field(description="Slug; the key in `attributes`, `attr.<key>` as a fact.", examples=["size"])
    label: str = Field(description="Label.", examples=["Company size"])
    kind: str = Field(description="text, number, bool, date, select, multiselect or url.", examples=["select"])
    options: list[dict[str, str]] = Field(
        description="Choices {value, label}.", examples=[[{"value": "s", "label": "S"}]]
    )
    required: bool = Field(description="Required on create and on a patch touching the key.", examples=[False])
    order: int = Field(description="Display order.", examples=[0])
    show_in_list: bool = Field(description="Shown and filterable in the company list.", examples=[True])
    is_active: bool = Field(description="Accepted in values, conditions, imports and templates.", examples=[True])


class AttributeDefinitionListResponse(BaseModel):
    results: list[AttributeDefinitionResponse] = Field(description="Definitions by entity, then display order.")


class FieldResponse(BaseModel):
    name: str = Field(description="Fact name: a built-in or attr.<key>.", examples=["attr.size"])
    kind: str = Field(description="Kind; decides the ops a condition may use.", examples=["select"])
    label: str = Field(description="Label.", examples=["Company size"])
    options: list[dict[str, str]] = Field(description="Choices {value, label} of select facts.", examples=[[]])


class FieldListResponse(BaseModel):
    results: list[FieldResponse] = Field(
        description="Built-in facts, then active company attributes; with entity=deal the active deal attributes."
    )


class ScoringProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Profile id.", examples=[100])
    key: str = Field(description="Profile key.", examples=["leads.scoring"])
    thresholds: dict[str, int] = Field(description="S <= M <= L.", examples=[{"S": 20, "M": 40, "L": 70}])
    rules: list[dict[str, Any]] = Field(description="{field, op, value, points, label}.", examples=[[]])
    disqualifiers: list[dict[str, Any]] = Field(description="{field, op, value, label}.", examples=[[]])
    is_active: bool = Field(description="The first active profile by key is used.", examples=[True])


class ScoringProfileListResponse(BaseModel):
    results: list[ScoringProfileResponse] = Field(description="Profiles by key.")


class ScoreReason(BaseModel):
    field: str = Field(description="Fact the rule named.", examples=["lead_type"])
    kind: str = Field(description="rule or disqualifier.", examples=["rule"])
    points: int = Field(description="Points it added (0 for a disqualifier).", examples=[30])
    label: str = Field(description="Label of the rule.", examples=["Retailer"])


class ScoreResponse(BaseModel):
    segment: str = Field(description="S1 (best) … S4.", examples=["S2"])
    points: int = Field(description="Sum of the matching rules.", examples=[45])
    disqualified: bool = Field(description="A disqualifier matched (S4).", examples=[False])
    reasons: list[str] = Field(description="Reasons as text.", examples=[["Retailer: +30"]])
    reasons_structured: list[ScoreReason] = Field(description="Reasons as data.")
    profile_id: int | None = Field(description="Profile used.", examples=[100])


class DealStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Deal stage id.", examples=[10])
    key: str = Field(description="Slug.", examples=["offer"])
    label: str = Field(description="Label.", examples=["Offer sent"])
    order: int = Field(description="Position in the pipeline.", examples=[10])
    kind: str = Field(description="open, won or lost.", examples=["open"])


class DealStageListResponse(BaseModel):
    results: list[DealStageResponse] = Field(description="Deal stages in pipeline order.")


class DealStageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str = Field(description="Slug.", examples=["offer"])
    label: str = Field(description="Label.", examples=["Offer sent"])
    kind: str = Field(description="open, won or lost.", examples=["open"])


class DealCompany(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Company id.", examples=[100])
    name: str = Field(description="Name.", examples=["Example Shop"])
    domain: str = Field(description="Registrable domain.", examples=["example-shop-2.test"])


class DealContact(BaseModel):
    id: int = Field(description="Contact id.", examples=[100])
    name: str = Field(description="Full name; the email when the contact has no name.", examples=["Jan Kowalski"])


def _money(value) -> str | None:
    return None if value is None else format(value, "f")


class DealResponse(BaseModel):
    id: int = Field(description="Deal id.", examples=[7])
    title: str = Field(description="What is sold.", examples=["Shop audit"])
    company: DealCompany = Field(description="The company.")
    contact: DealContact | None = Field(description="Contact; null = none.", examples=[None])
    owner: OwnerResponse | None = Field(description="Owner; null = none.", examples=[None])
    stage: DealStageSummary = Field(description="Current deal stage.")
    stage_entered_at: datetime = Field(description="Entered the current stage.", examples=["2026-11-13T12:00:00Z"])
    value_one_off: str | None = Field(description="One-off value, decimal string.", examples=["12000.00"])
    value_recurring: str | None = Field(description="Recurring value per period, decimal string.", examples=["500.00"])
    recurring_period: str = Field(description="month or year.", examples=["month"])
    currency: str | None = Field(description="ISO 4217; null = none.", examples=["PLN"])
    expected_close_date: date | None = Field(description="Expected close date.", examples=["2026-12-31"])
    closed_at: datetime | None = Field(description="Entered a won or lost stage; null = open.", examples=[None])
    lost_reason: str = Field(description="Why it was lost; empty unless lost.", examples=[""])
    attributes: dict[str, Any] = Field(description="Deal attribute values by key.", examples=[{}])
    open_tasks: int = Field(description="Open tasks about the deal; 0 without the todo module.", examples=[1])
    subject_ref: str = Field(description="Reference for a task about the deal.", examples=["leads.Deal:7"])
    subject_label: str = Field(description="Label for a task about the deal.", examples=["Example Shop — Shop audit"])
    created_by: str = Field(description="Who opened it.", examples=["anna"])
    created_at: datetime = Field(description="Opened.", examples=["2026-11-13T12:00:00Z"])
    modified_at: datetime = Field(description="Last change.", examples=["2026-11-13T12:00:00Z"])

    @classmethod
    def of(cls, deal, *, open_tasks: int, subject_ref: str, subject_label: str) -> "DealResponse":
        """`deal` with company, contact, owner, stage and currency joined."""
        contact = deal.contact
        return cls(
            id=deal.pk, title=deal.title, company=DealCompany.model_validate(deal.company),
            contact=DealContact(id=contact.pk, name=_contact_name(contact)) if contact else None,
            owner=OwnerResponse.of(deal.owner) if deal.owner_id else None,
            stage=DealStageSummary.model_validate(deal.stage), stage_entered_at=deal.stage_entered_at,
            value_one_off=_money(deal.value_one_off), value_recurring=_money(deal.value_recurring),
            recurring_period=deal.recurring_period, currency=deal.currency.iso3 if deal.currency_id else None,
            expected_close_date=deal.expected_close_date, closed_at=deal.closed_at, lost_reason=deal.lost_reason,
            attributes=deal.attributes, open_tasks=open_tasks, subject_ref=subject_ref, subject_label=subject_label,
            created_by=deal.created_by, created_at=deal.created_at, modified_at=deal.modified_at,
        )  # fmt: skip


def _contact_name(contact) -> str:
    return f"{contact.first_name} {contact.last_name}".strip() or contact.email


class DealListResponse(_Page):
    results: list[DealResponse] = Field(description="Deals of the page.")


class DealTotal(BaseModel):
    currency: str = Field(description="ISO 4217.", examples=["PLN"])
    one_off: str = Field(description="Sum of one-off values, decimal string.", examples=["24000.00"])
    recurring: str = Field(
        description="Sum of recurring values per month (a yearly value counts as a twelfth), decimal string.",
        examples=["1000.00"],
    )


class DealBoardColumn(BaseModel):
    stage: DealStageResponse = Field(description="The deal stage.")
    count: int = Field(description="Deals in the stage.", examples=[3])
    totals: list[DealTotal] = Field(description="Totals per currency; never added across currencies.")
    deals: list[DealResponse] = Field(description="The first 25 deals, longest in the stage first.")


class DealBoardResponse(BaseModel):
    results: list[DealBoardColumn] = Field(description="One column per deal stage in pipeline order.")
