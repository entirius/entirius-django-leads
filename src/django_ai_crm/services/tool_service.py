# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The agent's tools — one set for the CMS chat, the REST API and the MCP server.

`call(caller, name, arguments)` validates the arguments, checks the caller's permission and ownership, and either
runs the tool or — for an irreversible action (send a draft, lose a deal, do not contact) — stores a `Proposal` and
returns it unrun. Only `confirm_proposal` by the same user runs a proposal. No path around that: the chat and the
model's actions go through `call` too.
"""

import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ValidationError

from django_ai_crm.schemas import tools as args
from django_ai_crm.services import crm, plan_service, proposal_service
from django_ai_crm.services.caller import (
    COMPANIES_WRITE,
    REVIEW_WRITE,
    TASKS_WRITE,
    Caller,
    ToolError,
)
from django_ai_crm.services.texts import text

DONE, PROPOSED, REJECTED, FAILED = "done", "proposed", "rejected", "failed"
TODAY_WORDS, TOMORROW_WORDS = {"today", "dziś", "dzis", "dzisiaj"}, {"tomorrow", "jutro"}
DUE_HOUR = time(9, 0)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: type[BaseModel]
    run: Callable[[Caller, Any, str], dict[str, Any]]
    module: str | None = None
    permission: str | None = None
    # A summary of the change when the call must be confirmed first; None = run it now.
    confirm: Callable[[Caller, Any], str | None] | None = None


def result(status: str, message: str, **data: Any) -> dict[str, Any]:
    return {"status": status, "message": message, "data": data}


# --- tools -----------------------------------------------------------------------------------------------------------


def _day_plan(caller: Caller, a: args.DayPlanArgs, language: str) -> dict[str, Any]:
    plan = plan_service.day_plan(caller, _zone(a.tz))
    return result(DONE, plan_service.as_text(plan, language), plan=plan)


def _close_task(caller: Caller, a: args.CloseTaskArgs, language: str) -> dict[str, Any]:
    task = crm.close_task(caller, a.task_id, a.outcome.strip())
    message = text("task_closed", language, id=task.pk, outcome=task.outcome or task.title)
    return result(DONE, message, task_id=task.pk, task_status=task.status, done_by=task.done_by)


def _create_task(caller: Caller, a: args.CreateTaskArgs, language: str) -> dict[str, Any]:
    company = _company(caller, a.company_id)
    task = crm.create_task(caller, company, title=a.title, due_at=parse_due(a.due), kind=a.kind, note=a.note)
    message = text("task_created", language, id=task.pk, company=task.subject_label, title=task.title)
    due = task.due_at.isoformat() if task.due_at else None
    return result(DONE, message, task_id=task.pk, company_id=company.pk, due_at=due, created_by=task.created_by)


def _add_note(caller: Caller, a: args.AddNoteArgs, language: str) -> dict[str, Any]:
    company = _company(caller, a.company_id)
    activity = crm.add_note(caller, company, a.text.strip())
    message = text("note_added", language, company=company.name or company.domain)
    return result(DONE, message, activity_id=activity.pk, company_id=company.pk, actor=activity.actor)


def _move_deal(caller: Caller, a: args.MoveDealArgs, language: str) -> dict[str, Any]:
    deal = crm.own_deal(caller, a.deal_id)
    stage = crm.deal_stage(deal, a.stage)
    deal = crm.move_deal(caller, deal, stage, a.lost_reason)
    return result(DONE, text("deal_moved", language, id=deal.pk, stage=stage.label), deal_id=deal.pk, stage=stage.key)


def _confirm_move_deal(caller: Caller, a: args.MoveDealArgs) -> str | None:
    deal = crm.own_deal(caller, a.deal_id)
    stage = crm.deal_stage(deal, a.stage)
    if not crm.is_lost(stage):
        return None
    if not a.lost_reason.strip():
        raise ToolError("invalid", "lost_reason: is required to lose a deal")
    return f"mark deal #{deal.pk} “{deal.title}” as {stage.label} — {a.lost_reason.strip()}"


def _do_not_contact(caller: Caller, a: args.DoNotContactArgs, language: str) -> dict[str, Any]:
    company = crm.set_do_not_contact(caller, _company(caller, a.company_id), a.reason.strip())
    message = text("do_not_contact", language, company=company.name or company.domain)
    return result(DONE, message, company_id=company.pk, do_not_contact=company.do_not_contact)


def _confirm_do_not_contact(caller: Caller, a: args.DoNotContactArgs) -> str:
    company = _company(caller, a.company_id)
    reason = f" — {a.reason.strip()}" if a.reason.strip() else ""
    return f"set Do not contact on {company.name or company.domain}{reason}"


def _approve_draft(caller: Caller, a: args.ApproveDraftArgs, language: str) -> dict[str, Any]:
    message = crm.approve_draft(caller, crm.own_draft(caller, a.message_id))
    return result(
        DONE, text("draft_approved", language, id=message.pk), message_id=message.pk, message_status=message.status
    )


def _confirm_approve_draft(caller: Caller, a: args.ApproveDraftArgs) -> str:
    message = crm.own_draft(caller, a.message_id)
    return f"send draft #{message.pk} “{message.subject}” to {message.thread.recipient_email}"


def _list_proposals(caller: Caller, _a: args.NoArgs, language: str) -> dict[str, Any]:
    pending = [proposal_service.dump(p) for p in proposal_service.pending(caller)]
    lines = [f"#{p['id']}: {p['summary']}" for p in pending]
    return result(DONE, "\n".join(lines) or text("no_proposal", language), proposals=pending)


def _confirm_proposal(caller: Caller, a: args.ProposalArgs, language: str) -> dict[str, Any]:
    proposal = proposal_service.take(caller, a.proposal_id)
    tool = TOOLS[proposal.tool]
    try:
        outcome = _run(tool, caller, tool.schema.model_validate(proposal.arguments), language)
    except ToolError as exc:
        # The proposal is used up either way; its failure is stored (no raise: that would roll the mark back).
        proposal_service.fail(proposal, exc.message)
        return result(FAILED, text("error", language, message=exc.message), proposal_id=proposal.pk, code=exc.code)
    proposal_service.confirmed(proposal, outcome)
    return {**outcome, "data": {**outcome["data"], "proposal_id": proposal.pk}}


def _reject_proposal(caller: Caller, a: args.ProposalArgs, language: str) -> dict[str, Any]:
    proposal = proposal_service.reject(caller, a.proposal_id)
    return result(REJECTED, text("proposal_rejected", language, summary=proposal.summary), proposal_id=proposal.pk)


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "get_day_plan",
            "The salesperson's work for today, in order: their overdue and today tasks, drafts waiting for review, "
            "replies waiting for a person. Items are numbered (n).",
            args.DayPlanArgs,
            _day_plan,
        ),
        Tool(
            "close_task",
            "Close one of the salesperson's open tasks with what happened (the outcome).",
            args.CloseTaskArgs,
            _close_task,
            "todo",
            TASKS_WRITE,
        ),
        Tool(
            "create_task",
            "Create a new task for the salesperson about a company they own (e.g. the next step after a call).",
            args.CreateTaskArgs,
            _create_task,
            "todo",
            TASKS_WRITE,
        ),
        Tool(
            "add_note",
            "Add a note to the timeline of a company the salesperson owns.",
            args.AddNoteArgs,
            _add_note,
            "leads",
            COMPANIES_WRITE,
        ),
        Tool(
            "move_deal",
            "Move a deal of the salesperson to another stage. A lost stage needs lost_reason and is only proposed: "
            "it runs after the salesperson confirms it.",
            args.MoveDealArgs,
            _move_deal,
            "leads",
            COMPANIES_WRITE,
            _confirm_move_deal,
        ),
        Tool(
            "mark_do_not_contact",
            "Propose Do not contact for a company the salesperson owns; runs only after the salesperson confirms.",
            args.DoNotContactArgs,
            _do_not_contact,
            "leads",
            COMPANIES_WRITE,
            _confirm_do_not_contact,
        ),
        Tool(
            "approve_draft",
            "Propose sending a draft of a company the salesperson owns; runs only after the salesperson confirms.",
            args.ApproveDraftArgs,
            _approve_draft,
            "communicator",
            REVIEW_WRITE,
            _confirm_approve_draft,
        ),
        Tool("list_proposals", "The actions waiting for the salesperson's confirmation.", args.NoArgs, _list_proposals),
        Tool(
            "confirm_proposal",
            "Run a proposed action. Call it only when the salesperson has said yes to that proposal.",
            args.ProposalArgs,
            _confirm_proposal,
        ),
        Tool("reject_proposal", "Drop a proposed action.", args.ProposalArgs, _reject_proposal),
    )
}


# --- entry point -----------------------------------------------------------------------------------------------------


def call(caller: Caller, name: str, arguments: dict | None, language: str = "en") -> dict[str, Any]:
    """Run or propose one tool call; `ToolError` when it is refused."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError("not_found", f"Unknown tool {name!r}.")
    try:
        parsed = tool.schema.model_validate(arguments or {})
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors())
        raise ToolError("invalid", problems) from None
    _check(tool, caller)
    summary = tool.confirm(caller, parsed) if tool.confirm else None
    if summary is not None:
        proposal = proposal_service.create(caller, tool.name, parsed.model_dump(), summary)
        message = text("proposal", language, summary=summary, id=proposal.pk)
        proposed = result(PROPOSED, message, proposal_id=proposal.pk)
        return {**proposed, "tool": tool.name, "proposal": proposal_service.dump(proposal)}
    return _run(tool, caller, parsed, language)


def _check(tool: Tool, caller: Caller) -> None:
    if tool.module:
        crm.require(tool.module)
    if tool.permission:
        caller.require(tool.permission)


def _run(tool: Tool, caller: Caller, parsed: BaseModel, language: str) -> dict[str, Any]:
    _check(tool, caller)
    with transaction.atomic():
        return {**tool.run(caller, parsed, language), "tool": tool.name}


def catalogue() -> list[dict[str, Any]]:
    """Every tool as MCP lists it: name, description, inputSchema."""
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": tool.schema.model_json_schema()}
        for tool in TOOLS.values()
    ]


# --- helpers ---------------------------------------------------------------------------------------------------------


def _company(caller: Caller, company_id: int):
    crm.require("leads")
    return crm.own_company(caller, company_id)


def _zone(name: str):
    if not name:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ToolError("invalid", f"tz: unknown time zone {name!r}") from None


def parse_due(value: str) -> datetime | None:
    """today / tomorrow (en, pl) / YYYY-MM-DD → 09:00 local that day; an ISO date-time as given; empty → None."""
    value = (value or "").strip().lower()
    if not value:
        return None
    local_today = timezone.localdate()
    if value in TODAY_WORDS:
        day: date | None = local_today
    elif value in TOMORROW_WORDS:
        day = local_today + timedelta(days=1)
    else:
        day = None
    if day is None:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ToolError("invalid", f"due: not a date {value!r}") from None
        if len(value) > 10:
            return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
        day = parsed.date()
    return timezone.make_aware(datetime.combine(day, DUE_HOUR))
