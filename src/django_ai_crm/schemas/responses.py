# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Responses of the agent's REST API: what the CMS reads. Every field is present on every response (null when it has
no value); the tool-specific `data` stays a free object because every tool answers with its own fields."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanItemResponse(BaseModel):
    n: int = Field(description="Number in the plan, 1-based: what the salesperson says in the chat.")
    type: Literal["task", "drafts", "replies"]
    # type "task"
    task_id: int | None = None
    title: str | None = None
    kind: str | None = None
    due_at: str | None = None
    overdue: bool | None = None
    subject_ref: str | None = None
    subject_label: str | None = None
    company_id: int | None = None
    # type "drafts" / "replies": channel-wide counters
    count: int | None = None


class PlanResponse(BaseModel):
    items: list[PlanItemResponse]
    upcoming_7d: int | None = Field(description="Open tasks due in the next 7 days; null without django_todo.")
    generated_at: str
    text: str | None = Field(default=None, description="Numbered lines for a chat or a terminal (not in a chat turn).")


class ProposalResponse(BaseModel):
    id: int
    tool: str
    arguments: dict[str, Any]
    summary: str
    source: str
    status: str
    created_at: str


class ProposalListResponse(BaseModel):
    results: list[ProposalResponse]


class ToolSpecResponse(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any]  # noqa: N815 - the MCP name


class ToolListResponse(BaseModel):
    results: list[ToolSpecResponse]


class ToolResultResponse(BaseModel):
    """`status`: done | proposed | rejected | failed | error (chat only: a refused call, with its `code`)."""

    tool: str
    status: str
    message: str
    code: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    proposal: ProposalResponse | None = Field(default=None, description="The proposal made, when status is proposed.")


class ChatResponse(BaseModel):
    reply: str
    results: list[ToolResultResponse]
    used_model: bool
    proposals: list[ProposalResponse]
    plan: PlanResponse
