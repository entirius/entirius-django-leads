# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The contract of a search provider: what it answers about the people of one company."""

from pydantic import BaseModel, Field


class ProviderError(Exception):
    """The provider could not answer this time (transport, budget, unusable answer); the search may try again."""


class FoundPerson(BaseModel):
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(min_length=1, max_length=128)
    job_title: str = Field(default="", max_length=128)
    # Whether the role fits the offer, and whether the person still works there; null = no information.
    role_fits: bool | None = None
    still_employed: bool | None = None
    email: str = Field(default="", max_length=254)
    profile_url: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=500)


class FoundAddress(BaseModel):
    """A published address of the company, with the person it belongs to when known — the evidence of the pattern."""

    email: str = Field(max_length=254)
    first_name: str = Field(default="", max_length=128)
    last_name: str = Field(default="", max_length=128)
    source: str = Field(default="", max_length=500)


class SearchAnswer(BaseModel):
    people: list[FoundPerson] = Field(default_factory=list)
    addresses: list[FoundAddress] = Field(default_factory=list)


JSON_SCHEMA = SearchAnswer.model_json_schema()
