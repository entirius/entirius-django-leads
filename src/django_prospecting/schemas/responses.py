# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _FromModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyResponse(_FromModel):
    id: int
    name: str
    domain: str
    source: str
    note: str
    created_at: datetime


class CompanyListResponse(BaseModel):
    description: str
    results: list[CompanyResponse]


class SearchResponse(_FromModel):
    id: int
    subject_ref: str
    company_name: str
    domain: str
    reason: str
    status: str
    attempts: int
    mail_pattern: str
    pattern_source: str
    requested_by: str
    detail: str
    created_at: datetime
    finished_at: datetime | None


class SearchListResponse(BaseModel):
    results: list[SearchResponse]


class CheckResponse(_FromModel):
    kind: str
    result: str = Field(description="`ok` or `no_info` (still missing after every attempt).")
    value: str
    source: str
    checked_at: datetime | None
    attempts: int


class EmailOption(BaseModel):
    email: str
    source: str = ""
    verdict: str = ""
    tested_at: str = ""
    tested_by: str = ""


class CandidateResponse(_FromModel):
    id: int
    search_id: int
    subject_ref: str
    company_name: str
    first_name: str
    last_name: str
    job_title: str
    profile_url: str
    source: str
    found_at: datetime
    email: str
    email_options: list[EmailOption]
    status: str
    decided_by: str
    decided_at: datetime | None
    reject_reason: str
    contact_ref: str
    checks: list[CheckResponse]
    missing: list[str] = Field(description="Kinds of the checks without information.")

    @field_validator("checks", mode="before")
    @classmethod
    def _checks(cls, value):
        return list(value.all()) if hasattr(value, "all") else value

    @classmethod
    def of(cls, candidate) -> "CandidateResponse":
        checks = list(candidate.checks.all())
        return cls.model_validate(
            {
                **{field: getattr(candidate, field) for field in cls.model_fields if hasattr(candidate, field)},
                "company_name": candidate.search.company_name,
                "checks": [CheckResponse.model_validate(check) for check in checks],
                "missing": [check.kind for check in checks if check.result != "ok"],
            }
        )


class CandidateListResponse(BaseModel):
    results: list[CandidateResponse]


class LimitsResponse(BaseModel):
    search_provider: str
    address_tester: str
    max_attempts: int
    company_search_limit: int
    daily_budget: int
    spent_today: int
    max_candidates: int
