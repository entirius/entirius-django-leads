# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the leads admin API v2."""

from django_agreements.enums import LegalBasis
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator

from django_leads.enums import CompanyType, ContactStrategy, RuleAction, RuleTrigger, StageKind
from django_leads.services.company_service import SORT_FIELDS
from django_leads.utils.domains import registrable_domain


class CompanyListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str = Field(default="", max_length=64, description="Stage key filter.", examples=["new"])
    search: str = Field(default="", max_length=253, description="Substring of name or domain.", examples=["shop"])
    sort: str = Field(
        default="name", description="name, domain, stage_entered_at or last_activity_at; `-` for descending."
    )

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
    company_type: CompanyType = Field(default=CompanyType.UNKNOWN, description="Company type.")
    industry: str = Field(default="", max_length=128, description="Industry.")

    @field_validator("domain")
    @classmethod
    def domain_is_registrable(cls, value: str) -> str:
        return registrable_domain(value)


class CompanyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255, description="Company name.")
    website: HttpUrl | None = Field(default=None, description="Website URL.")
    company_type: CompanyType | None = Field(default=None, description="Company type.")
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


class StageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_]+$", description="Slug.")
    label: str | None = Field(default=None, min_length=1, max_length=128, description="Label.")
    order: int | None = Field(default=None, ge=0, le=32767, description="Position in the pipeline.")
    kind: StageKind | None = Field(default=None, description="open, won, lost or unresponsive.")
    is_terminal: bool | None = Field(default=None, description="No further stages after this one.")
    on_reply: bool | None = Field(default=None, description="Companies move here when a contact replies.")


class ActivityListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: int | None = Field(default=None, description="Company id filter.", examples=[101])


class RuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: RuleTrigger = Field(description="stage_entered or intel_ready.", examples=["stage_entered"])
    stage_id: int | None = Field(default=None, description="Stage; required for stage_entered.", examples=[102])
    action: RuleAction = Field(default=RuleAction.COMMUNICATE, description="request_audit or communicate.")
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
    action: RuleAction | None = Field(default=None, description="request_audit or communicate.")
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
