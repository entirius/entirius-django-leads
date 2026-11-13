# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the leads admin API v2."""

from datetime import date
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django_agreements.enums import LegalBasis
from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator

from django_leads.enums import (
    UNKNOWN_LEAD_TYPE,
    AttributeEntity,
    AttributeKind,
    ConditionOp,
    ContactStrategy,
    RuleAction,
    RuleTrigger,
    ScoreSegment,
    StageKind,
)
from django_leads.services.company_service import SORT_FIELDS
from django_leads.utils.domains import registrable_domain

# Format only — whether a code is an active lead type of the channel is the service's call (`lead_type_service`).
LEAD_TYPE_PATTERN = r"^[A-Z0-9_]+$"
ATTRIBUTE_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
ATTRIBUTES_DESCRIPTION = (
    "Custom attribute values by key (active definitions of the channel); on update a partial patch, null removes a key."
)


class CompanyListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str = Field(default="", max_length=64, description="Stage key filter.", examples=["new"])
    search: str = Field(default="", max_length=253, description="Substring of name or domain.", examples=["shop"])
    sort: str = Field(
        default="name",
        description="name, domain, stage_entered_at, last_activity_at or score_points; `-` for descending.",
    )
    lead_type: str = Field(
        default="",
        max_length=32,
        pattern=r"^[A-Z0-9_]*$",
        description="Lead type code filter (an active lead type of the channel, or UNKNOWN); blank = any.",
        examples=["RETAILER"],
    )
    do_not_contact: bool | None = Field(default=None, description="do_not_contact flag filter.")
    has_reply: bool | None = Field(default=None, description="At least one reply activity (true) or none (false).")
    origin: str = Field(default="", max_length=64, description="Exact origin filter; blank = any.", examples=["fair"])
    segment: ScoreSegment | None = Field(default=None, description="Score segment filter (S1 best … S4).")

    @field_validator("sort")
    @classmethod
    def sort_in_allowlist(cls, value: str) -> str:
        if value.lstrip("-") not in SORT_FIELDS:
            raise ValueError(f"sort must be one of {sorted(SORT_FIELDS)} (optionally prefixed with -)")
        return value


class CompanyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=253, description="Domain or URL; stored registrable.")
    name: str = Field(default="", max_length=255, description="Company name; the domain when empty.")
    website: HttpUrl | None = Field(default=None, description="Website URL.")
    lead_type: str = Field(
        default=UNKNOWN_LEAD_TYPE, max_length=32, pattern=LEAD_TYPE_PATTERN, description="Lead type code."
    )
    industry: str = Field(default="", max_length=128, description="Industry.")
    attributes: dict[str, Any] = Field(default_factory=dict, description=ATTRIBUTES_DESCRIPTION)

    @field_validator("domain")
    @classmethod
    def domain_is_registrable(cls, value: str) -> str:
        return registrable_domain(value)


class CompanyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255, description="Company name.")
    website: HttpUrl | None = Field(default=None, description="Website URL.")
    lead_type: str | None = Field(default=None, max_length=32, pattern=LEAD_TYPE_PATTERN, description="Lead type code.")
    industry: str | None = Field(default=None, max_length=128, description="Industry.")
    description: str | None = Field(default=None, description="Free-text description.")
    do_not_contact: bool | None = Field(default=None, description="Never contact this company.")
    external_ref: str | None = Field(default=None, max_length=128, description="Reference in an external system.")
    origin: str | None = Field(default=None, max_length=64, description="Where the lead came from.")
    attributes: dict[str, Any] | None = Field(default=None, description=ATTRIBUTES_DESCRIPTION)


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_key: str = Field(min_length=1, max_length=64, description="Target stage key.", examples=["contacted"])


class ContactListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: int | None = Field(default=None, description="Company id filter.", examples=[101])


CONSENT_REF_DESCRIPTION = "Where consent was given (document, call, form); required when legal_basis is consent."


def require_consent_ref(request: BaseModel) -> BaseModel:
    """`consent` from an operator always says where it was given."""
    if request.legal_basis == LegalBasis.CONSENT and not (request.consent_ref or "").strip():
        raise ValueError("consent_ref is required when legal_basis is consent")
    return request


class ContactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: int = Field(description="Company of the contact.", examples=[101])
    email: EmailStr | None = Field(default=None, description="Email; immutable once set.")
    first_name: str = Field(default="", max_length=128, description="First name.")
    last_name: str = Field(default="", max_length=128, description="Last name.")
    job_title: str = Field(default="", max_length=128, description="Job title.")
    phone: str = Field(default="", max_length=32, description="Phone.")
    language: str | None = Field(default=None, min_length=2, max_length=2, description="ISO 639-1 code.")
    is_primary: bool = Field(default=False, description="Primary contact of the company.")
    legal_basis: LegalBasis | None = Field(default=None, description="GDPR legal basis.")
    consent_ref: str | None = Field(default=None, max_length=255, description=CONSENT_REF_DESCRIPTION)
    attributes: dict[str, Any] | None = Field(default=None, description=ATTRIBUTES_DESCRIPTION)

    _consent_needs_ref = model_validator(mode="after")(require_consent_ref)


class ContactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = Field(default=None, description="Email; only while the contact has none.")
    first_name: str | None = Field(default=None, max_length=128, description="First name.")
    last_name: str | None = Field(default=None, max_length=128, description="Last name.")
    job_title: str | None = Field(default=None, max_length=128, description="Job title.")
    phone: str | None = Field(default=None, max_length=32, description="Phone.")
    language: str | None = Field(default=None, min_length=2, max_length=2, description="ISO 639-1 code.")
    is_primary: bool | None = Field(default=None, description="Primary contact of the company.")
    legal_basis: LegalBasis | None = Field(default=None, description="GDPR legal basis.")
    consent_ref: str | None = Field(default=None, max_length=255, description=CONSENT_REF_DESCRIPTION)
    attributes: dict[str, Any] | None = Field(default=None, description=ATTRIBUTES_DESCRIPTION)

    _consent_needs_ref = model_validator(mode="after")(require_consent_ref)


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_]+$", description="Slug.", examples=["new"])
    label: str = Field(min_length=1, max_length=128, description="Label.", examples=["New"])
    order: int = Field(default=0, ge=0, le=32767, description="Position in the pipeline.")
    kind: StageKind = Field(default=StageKind.OPEN, description="open, won, lost or unresponsive.")
    is_terminal: bool = Field(default=False, description="No further stages after this one.")


class StageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_]+$", description="Slug.")
    label: str | None = Field(default=None, min_length=1, max_length=128, description="Label.")
    order: int | None = Field(default=None, ge=0, le=32767, description="Position in the pipeline.")
    kind: StageKind | None = Field(default=None, description="open, won, lost or unresponsive.")
    is_terminal: bool | None = Field(default=None, description="No further stages after this one.")


class StageEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_stage_key: str | None = Field(
        min_length=1, max_length=64, description="Stage the event moves to; null keeps the stage.", examples=["replied"]
    )
    forward_only: bool = Field(default=True, description="Move only to a stage of a higher order.")


class LeadTypeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=1, max_length=32, pattern=LEAD_TYPE_PATTERN, description="Upper-case code.", examples=["RETAILER"]
    )
    label: str = Field(min_length=1, max_length=128, description="Label.", examples=["Retailer"])
    order: int = Field(default=0, ge=0, le=32767, description="Display order.")
    is_active: bool = Field(default=True, description="Offered, filtered on and guessed.")


class LeadTypeUpdateRequest(BaseModel):
    """The code is fixed after create — companies and communicator templates hold it."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=128, description="Label.")
    order: int | None = Field(default=None, ge=0, le=32767, description="Display order.")
    is_active: bool | None = Field(default=None, description="Offered, filtered on and guessed.")


class ActivityListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: int | None = Field(default=None, description="Company id filter.", examples=[101])


class AttributeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=64, description="Stored value.", examples=["micro"])
    label: str = Field(min_length=1, max_length=128, description="Label.", examples=["Micro (1-9)"])


class AttributeDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: AttributeEntity = Field(description="company, contact or deal.", examples=["company"])
    key: str = Field(min_length=1, max_length=48, pattern=ATTRIBUTE_KEY_PATTERN, description="Slug.", examples=["size"])
    label: str = Field(min_length=1, max_length=128, description="Label.", examples=["Company size"])
    kind: AttributeKind = Field(description="text, number, bool, date, select, multiselect or url.")
    options: list[AttributeOption] = Field(default_factory=list, description="Choices; required for the select kinds.")
    required: bool = Field(default=False, description="A value is required on create and may not be removed.")
    order: int = Field(default=0, ge=0, le=32767, description="Display order.")
    show_in_list: bool = Field(default=False, description="Shown and filterable in the company list.")
    is_active: bool = Field(default=True, description="Accepted in values, conditions, imports and templates.")


class AttributeDefinitionUpdateRequest(BaseModel):
    """Entity and key are fixed after create; the kind only while no value is stored."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=128, description="Label.")
    kind: AttributeKind | None = Field(default=None, description="Refused (409) once a value is stored.")
    options: list[AttributeOption] | None = Field(default=None, description="Choices of the select kinds.")
    required: bool | None = Field(default=None, description="Required on create and on a patch touching the key.")
    order: int | None = Field(default=None, ge=0, le=32767, description="Display order.")
    show_in_list: bool | None = Field(default=None, description="Shown and filterable in the company list.")
    is_active: bool | None = Field(default=None, description="Accepted in values, conditions, imports and templates.")


class AttributeListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity: AttributeEntity | None = Field(default=None, description="Only definitions of this entity.")


class Condition(BaseModel):
    """Shape only — whether the field exists and takes the op is the service's call (`condition_service`)."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=64, description="A fact name from `fields/`.", examples=["attr.size"])
    op: ConditionOp = Field(description="eq, ne, in, contains, gte, lte or empty.", examples=["eq"])
    value: Any = Field(default=None, description="Compared value; a list for in, a bool for empty (default true).")


class ScoringRule(Condition):
    points: int = Field(ge=-1000, le=1000, description="Added when the condition holds.", examples=[30])
    label: str = Field(default="", max_length=128, description="Reason shown in the timeline.", examples=["Retailer"])


class Disqualifier(Condition):
    label: str = Field(default="", max_length=128, description="Reason shown in the timeline.", examples=["Too small"])


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    S: int = Field(description="Points from which a company is S3.", examples=[20])
    M: int = Field(description="Points from which a company is S2.", examples=[40])
    L: int = Field(description="Points from which a company is S1.", examples=[70])


class ScoringProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        default="leads.scoring", min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_.]+$", description="Profile key."
    )
    thresholds: Thresholds = Field(description="S <= M <= L.")
    rules: list[ScoringRule] = Field(default_factory=list, description="Points per matching condition.")
    disqualifiers: list[Disqualifier] = Field(default_factory=list, description="A match forces S4.")
    is_active: bool = Field(default=True, description="The first active profile by key is used.")


class ScoringProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_.]+$")
    thresholds: Thresholds | None = Field(default=None, description="S <= M <= L.")
    rules: list[ScoringRule] | None = Field(default=None, description="Points per matching condition.")
    disqualifiers: list[Disqualifier] | None = Field(default=None, description="A match forces S4.")
    is_active: bool | None = Field(default=None, description="The first active profile by key is used.")


class RuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: RuleTrigger = Field(description="stage_entered or intel_ready.", examples=["stage_entered"])
    stage_id: int | None = Field(default=None, description="Stage; required for stage_entered.", examples=[102])
    action: RuleAction = Field(default=RuleAction.COMMUNICATE, description="request_audit, communicate or score.")
    template_key: str = Field(default="", max_length=128, description="Communicator template; for communicate.")
    contact_strategy: ContactStrategy = Field(default=ContactStrategy.PRIMARY, description="primary or ai_pick.")
    require_hooks: bool = Field(default=True, description="Skip companies without hooks.")
    require_email: bool = Field(default=True, description="Skip companies without a contact email.")
    cooldown_hours: int = Field(default=24, ge=0, le=8760, description="No re-run for the company within.")
    is_active: bool = Field(default=True, description="Evaluated at all.")
    order: int = Field(default=0, ge=0, le=32767, description="Evaluation order.")
    conditions: list[Condition] = Field(default_factory=list, description="All must hold, else the run is skipped.")


class RuleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: RuleTrigger | None = Field(default=None, description="stage_entered or intel_ready.")
    stage_id: int | None = Field(default=None, description="Stage; null clears it.", examples=[102])
    action: RuleAction | None = Field(default=None, description="request_audit, communicate or score.")
    template_key: str | None = Field(default=None, max_length=128, description="Communicator template.")
    contact_strategy: ContactStrategy | None = Field(default=None, description="primary or ai_pick.")
    require_hooks: bool | None = Field(default=None, description="Skip companies without hooks.")
    require_email: bool | None = Field(default=None, description="Skip companies without a contact email.")
    cooldown_hours: int | None = Field(default=None, ge=0, le=8760, description="No re-run within.")
    is_active: bool | None = Field(default=None, description="Evaluated at all.")
    order: int | None = Field(default=None, ge=0, le=32767, description="Evaluation order.")
    conditions: list[Condition] | None = Field(default=None, description="All must hold, else the run is skipped.")


class RuleRunListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: int | None = Field(default=None, description="Company id filter.", examples=[102])


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_.]+$", description="Profile key.")
    prompt_text: str = Field(min_length=1, description="Prompt with placeholders.")
    json_schema: dict = Field(default_factory=dict, description="Output JSON schema.")
    model: str = Field(min_length=1, max_length=128, description="Toolbox model.", examples=["fake-chat"])


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_.]+$")
    prompt_text: str | None = Field(default=None, min_length=1, description="Prompt with placeholders.")
    json_schema: dict | None = Field(default=None, description="Output JSON schema.")
    model: str | None = Field(default=None, min_length=1, max_length=128, description="Toolbox model.")


class AnalysisProfileRequest(ProfileRequest):
    is_active: bool = Field(default=True, description="Used by the analysis.")


class AnalysisProfileUpdateRequest(ProfileUpdateRequest):
    is_active: bool | None = Field(default=None, description="Used by the analysis.")


class UploadOptions(BaseModel):
    """Multipart fields next to `file`."""

    model_config = ConfigDict(extra="ignore")

    run_rules: bool = Field(default=False, description="Created companies enter their first stage with its rules.")


class FunnelQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    origin: str = Field(default="", max_length=64, description="Only companies of this origin; blank = all.")
    date_from: date | None = Field(default=None, description="First day (inclusive).", examples=["2026-11-01"])
    date_to: date | None = Field(default=None, description="Last day (inclusive).", examples=["2026-11-30"])


class CommunicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(
        min_length=1, max_length=128, description="Communicator template.", examples=["lead.cold.b2b"]
    )
    contact_id: int = Field(description="Contact of the company.", examples=[102])


class DevEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: int = Field(description="Company id.", examples=[102])
    trigger: RuleTrigger = Field(description="stage_entered or intel_ready.")
    stage_key: str | None = Field(default=None, max_length=64, description="Stage; default the company's stage.")


class DevAnonymiseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: AwareDatetime | None = Field(
        default=None, description="Clock of the retention run; now when omitted.", examples=["2026-09-15T06:00:00Z"]
    )


class GdprRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254, description="Address of the data subject.", examples=["jan@example-shop-2.test"])

    @field_validator("email")
    @classmethod
    def email_is_valid(cls, value: str) -> str:
        """Django's validator: reserved test domains (`.test`) stay accepted, unlike `EmailStr`."""
        try:
            validate_email(value.strip())
        except DjangoValidationError:
            raise ValueError("not a valid email address") from None
        return value.strip()
