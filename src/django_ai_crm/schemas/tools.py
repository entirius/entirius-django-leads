# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Arguments of the agent's tools — one model per tool; its JSON Schema is the MCP `inputSchema`."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskKind = Literal["mail", "phone", "linkedin", "meeting", "todo"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class DayPlanArgs(_Args):
    tz: str = Field(default="", description="IANA time zone of the day, e.g. Europe/Warsaw; empty = the server's.")


class CloseTaskArgs(_Args):
    task_id: int = Field(description="The task to close (task_id of a plan item).")
    outcome: str = Field(default="", max_length=2000, description="What happened, in a few words.")


class CreateTaskArgs(_Args):
    company_id: int = Field(description="The company the task is about (one you own).")
    title: str = Field(min_length=1, max_length=255)
    due: str = Field(default="", description="today, tomorrow, YYYY-MM-DD or an ISO date-time; empty = no deadline.")
    kind: TaskKind = "todo"
    note: str = Field(default="", max_length=2000)


class AddNoteArgs(_Args):
    company_id: int = Field(description="The company the note is about (one you own).")
    text: str = Field(min_length=1, max_length=2000)


class MoveDealArgs(_Args):
    deal_id: int
    stage: str = Field(min_length=1, max_length=64, description="The key of the target deal stage.")
    lost_reason: str = Field(default="", max_length=255, description="Required when the stage is a lost stage.")


class DoNotContactArgs(_Args):
    company_id: int
    reason: str = Field(default="", max_length=255)


class ApproveDraftArgs(_Args):
    message_id: int = Field(description="The draft waiting for review (communicator message id).")


class ProposalArgs(_Args):
    proposal_id: int
