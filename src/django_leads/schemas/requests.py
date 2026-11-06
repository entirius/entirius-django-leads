# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the leads admin API v2."""

from datetime import date
from typing import Literal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django_agreements.enums import LegalBasis
from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator

from django_leads.enums import UNKNOWN_LEAD_TYPE, ContactStrategy, PilotResult, RuleAction, RuleTrigger, StageKind
from django_leads.services.company_service import SORT_FIELDS
from django_leads.utils.domains import registrable_domain

# Format only — whether a code is an active lead type of the channel is the service's call (`lead_type_service`).
LEAD_TYPE_PATTERN = r"^[A-Z0-9_]+$"


class CompanyListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str = Field(default="", max_length=64, description="Stage key filter.", examples=["new"])
    search: str = Field(default="", max_length=253, description="Substring of name or domain.", examples=["shop"])
    sort: str = Field(
        default="name", description="name, domain, stage_entered_at or last_activity_at; `-` for descending."
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

    _consent_needs_ref = model_validator(mode="after")(require_consent_ref)


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_]+$", description="Slug.", examples=["new"])
    label: str = Field(min_length=1, max_length=128, description="Label.", examples=["New"])
    order: int = Field(default=0, ge=0, le=32767, description="Position in the pipeline.")
    kind: StageKind = Field(default=StageKind.OPEN, description="open, won, lost or unresponsive.")
    is_terminal: bool = Field(default=False, description="No further stages after this one.")
    on_reply: bool = Field(default=False, description="Companies move here when a contact replies.")
    on_sent: bool = Field(default=False, description="Companies move here (forward only) when a message is sent.")


class StageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_]+$", description="Slug.")
    label: str | None = Field(default=None, min_length=1, max_length=128, description="Label.")
    order: int | None = Field(default=None, ge=0, le=32767, description="Position in the pipeline.")
    kind: StageKind | None = Field(default=None, description="open, won, lost or unresponsive.")
    is_terminal: bool | None = Field(default=None, description="No further stages after this one.")
    on_reply: bool | None = Field(default=None, description="Companies move here when a contact replies.")
    on_sent: bool | None = Field(default=None, description="Companies move here (forward only) when a message is sent.")


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


ScoreField = Literal["lead_type", "industry", "platform", "domain", "hooks_count", "emails_count", "rotation_count"]
ScoreOp = Literal["eq", "in", "contains", "gte", "lte"]


class ScoreCondition(BaseModel):
    """A test on one company fact; shared by scoring rules (with points) and disqualifiers."""

    model_config = ConfigDict(extra="forbid")

    field: ScoreField = Field(description="Company fact tested.", examples=["lead_type"])
    op: ScoreOp = Field(description="eq, in (value is a list), contains (text), gte or lte (counts).", examples=["in"])
    value: str | int | list[str] = Field(description="Compared value; a list for `in`.", examples=[["RETAILER"]])
    label: str = Field(default="", max_length=120, description="Reason shown in the timeline.", examples=["retailer"])

    @model_validator(mode="after")
    def op_fits_value(self) -> "ScoreCondition":
        counts = self.field in {"hooks_count", "emails_count", "rotation_count"}
        if self.op == "in":
            if counts or not isinstance(self.value, list):
                raise ValueError("op `in` needs a list value and a text field")
        elif isinstance(self.value, list):
            raise ValueError("only op `in` takes a list value")
        elif counts and (self.op == "contains" or not isinstance(self.value, int)):
            raise ValueError("count fields take eq, gte or lte with an integer")
        elif not counts and (self.op in {"gte", "lte"} or not isinstance(self.value, str)):
            raise ValueError("text fields take eq, in or contains with a string")
        return self


class ScoreRuleItem(ScoreCondition):
    points: int = Field(ge=-100, le=100, description="Added when the condition holds.", examples=[30])


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    S: int = Field(ge=0, le=1000, description="Points from which a company is S3.", examples=[20])
    M: int = Field(ge=0, le=1000, description="Points from which a company is S2.", examples=[40])
    L: int = Field(ge=0, le=1000, description="Points from which a company is S1.", examples=[70])

    @model_validator(mode="after")
    def ordered(self) -> "Thresholds":
        if not self.S <= self.M <= self.L:
            raise ValueError("thresholds must satisfy S <= M <= L")
        return self


class ScoringProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        default="leads.scoring", min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_.]+$", description="Profile key."
    )
    thresholds: Thresholds = Field(description="Points needed for S3, S2 and S1; below S is S4.")
    rules: list[ScoreRuleItem] = Field(default_factory=list, max_length=100, description="Point rules.")
    disqualifiers: list[ScoreCondition] = Field(default_factory=list, max_length=50, description="Force S4.")
    is_active: bool = Field(default=True, description="Used by scoring.")


class ScoringProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_.]+$")
    thresholds: Thresholds | None = Field(default=None, description="Points needed for S3, S2 and S1.")
    rules: list[ScoreRuleItem] | None = Field(default=None, max_length=100, description="Point rules.")
    disqualifiers: list[ScoreCondition] | None = Field(default=None, max_length=50, description="Force S4.")
    is_active: bool | None = Field(default=None, description="Used by scoring.")


class PilotListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: int | None = Field(default=None, description="Company id filter.", examples=[101])
    result: PilotResult | None = Field(default=None, description="pending, success, failure or cancelled.")


class PilotReportQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    as_of: date | None = Field(default=None, description="Day the report is computed for; today when omitted.")


class PilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: int = Field(description="Company of the channel.", examples=[101])
    start_date: date = Field(description="First day of the pilot.", examples=["2026-11-10"])
    end_date: date | None = Field(default=None, description="Planned or actual last day.")
    success_criterion: str = Field(default="", description="What counts as success.", examples=["10 orders in 30 days"])
    result: PilotResult = Field(default=PilotResult.PENDING, description="pending, success, failure or cancelled.")
    result_note: str = Field(default="", description="Outcome in words.")
    license_switch_date: date | None = Field(default=None, description="Day the company moves to a licence (S5).")


class PilotUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date | None = Field(default=None, description="First day of the pilot.")
    end_date: date | None = Field(default=None, description="Last day; null clears it.")
    success_criterion: str | None = Field(default=None, description="What counts as success.")
    result: PilotResult | None = Field(default=None, description="pending, success, failure or cancelled.")
    result_note: str | None = Field(default=None, description="Outcome in words.")
    license_switch_date: date | None = Field(default=None, description="Licence switch day; null clears it.")


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
