# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompanyCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(default="", max_length=253)
    note: str = Field(default="", max_length=2000)


class SearchCreateRequest(_Strict):
    subject_ref: str = Field(min_length=1, max_length=128, examples=["leads.Company:42"])
    company_name: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=3, max_length=253, examples=["example.com"])


class SearchListQuery(BaseModel):
    subject_ref: str | None = None


class CandidateListQuery(BaseModel):
    subject_ref: str | None = None
    status: Literal["pending", "accepted", "rejected"] | None = None


class CandidateAcceptRequest(_Strict):
    email: str = Field(default="", max_length=254, description="Blank = the candidate's best address.")


class CandidateRejectRequest(_Strict):
    reason: str = Field(default="", max_length=255)
