# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request bodies and queries of the admin API (Pydantic, extra="forbid")."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# A REST caller names where it runs (the CMS screen or anything else); MCP is always "mcp".
RestSource = Literal["cms", "api"]
Language = Literal["en", "pl"]


class PlanQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tz: str = ""
    language: Language = "en"


class ToolCallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict = Field(default_factory=dict)
    source: RestSource = "api"
    language: Language = "en"


class HistoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    source: RestSource = "api"
    language: Language = "en"
    history: list[HistoryTurn] = Field(default_factory=list, max_length=20)
