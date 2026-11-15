# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The conversation: the salesperson says what they did, the agent updates the CRM through the tools.

Two layers, both ending in `tool_service.call` (so the same checks and confirmations apply):
1. short commands in English and Polish ("done 1: called", "zrobione 1: …", "note 2: …", "yes") — no model, no cost;
2. anything else goes to the AI toolbox model (`AI_CRM_MODEL`), which answers with a reply and tool calls. The model
   never confirms a proposal: only the salesperson's own "yes" (or a direct `confirm_proposal` call) does.

Without a model, or when the toolbox fails, the agent says what it understands instead of guessing.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from django_ai_crm import settings as module_settings
from django_ai_crm.services import plan_service, proposal_service, tool_service
from django_ai_crm.services.caller import Caller, ToolError
from django_ai_crm.services.texts import text

logger = logging.getLogger("django_ai_crm.chat")

CHAT_TAG = "ai_crm.chat"
MODEL_FORBIDDEN_TOOLS = frozenset({"confirm_proposal"})
SEP = r"\s*[:\-–—,]\s*"

PLAN = re.compile(r"^(plan|today|next|what'?s next|co dalej|plan dnia|dzi[sś](iaj)?)\W*$", re.I)
DONE = re.compile(rf"^(done|did|zrobione|zrobi[łl]em|zrobi[łl]am|gotowe)\s+(#)?(\d+)(?:{SEP}(.*))?$", re.I | re.S)
NOTE = re.compile(rf"^(note|notatka)\s+(#)?(\d+){SEP}(.+)$", re.I | re.S)
TASK = re.compile(rf"^(task|zadanie)\s+(#)?(\d+)(?:\s+([\w-]+))?{SEP}(.+)$", re.I | re.S)
DEAL = re.compile(rf"^deal\s+#?(\d+)\s*(?:->|→|to|na)\s*([\w-]+)(?:{SEP}(.+))?$", re.I | re.S)
DNC = re.compile(rf"^(do not contact|dnc|nie kontaktuj)\s+(#)?(\d+)(?:{SEP}(.+))?$", re.I | re.S)
SEND = re.compile(r"^(send|approve|wy[sś]lij|akceptuj)\s+(?:draft\s+)?#?(\d+)\W*$", re.I)
YES = re.compile(r"^(yes|y|confirm|tak|potwierdzam|ok)(?:\s+#?(\d+))?\W*$", re.I)
NO = re.compile(r"^(no|n|cancel|reject|nie|anuluj)(?:\s+#?(\d+))?\W*$", re.I)

REPLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "actions"],
    "properties": {
        "reply": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool", "arguments"],
                "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
            },
        },
    },
}
SYSTEM_PROMPT = """You are the AI CRM assistant of one salesperson. You lead them through their day plan and update
the CRM from what they tell you they did: close tasks with their outcome, add notes, create the next task, move deals.
Use only the tools listed below, only with ids from the plan or from the salesperson. Irreversible actions are proposed
by the tools and wait for the salesperson's yes — never claim they are done. Answer in the salesperson's language,
briefly. Answer as JSON: {"reply": "<what you tell the salesperson>", "actions": [{"tool": "<name>",
"arguments": {...}}]}; actions may be empty."""


@dataclass
class Turn:
    reply: str
    results: list[dict[str, Any]] = field(default_factory=list)
    used_model: bool = False


def chat(caller: Caller, message: str, *, language: str = "en", history: list[dict] | None = None) -> dict[str, Any]:
    message = message.strip()
    plan = plan_service.day_plan(caller)
    turn = _command(caller, message, plan, language)
    if turn is None:
        turn = _model(caller, message, plan, language, history or [])
    fresh = plan_service.day_plan(caller)
    pending = [proposal_service.dump(p) for p in proposal_service.pending(caller)]
    return {
        "reply": turn.reply,
        "results": turn.results,
        "used_model": turn.used_model,
        "proposals": pending,
        "plan": fresh,
    }


# --- commands --------------------------------------------------------------------------------------------------------


def _command(caller: Caller, message: str, plan: dict, language: str) -> Turn | None:
    if PLAN.match(message):
        return Turn(plan_service.as_text(plan, language))
    if match := DONE.match(message):
        _, own_id, number, outcome = match.groups()
        target = _target(plan, number, own_id, "task_id", language)
        return (
            target
            if isinstance(target, Turn)
            else _call(caller, "close_task", {"task_id": target, "outcome": outcome or ""}, language)
        )  # noqa: E501
    if match := NOTE.match(message):
        _, own_id, number, body = match.groups()
        target = _target(plan, number, own_id, "company_id", language)
        return (
            target
            if isinstance(target, Turn)
            else _call(caller, "add_note", {"company_id": target, "text": body}, language)
        )  # noqa: E501
    if match := TASK.match(message):
        _, own_id, number, due, title = match.groups()
        target = _target(plan, number, own_id, "company_id", language)
        if isinstance(target, Turn):
            return target
        return _call(caller, "create_task", {"company_id": target, "title": title.strip(), "due": due or ""}, language)
    if match := DEAL.match(message):
        deal_id, stage, reason = match.groups()
        arguments = {"deal_id": int(deal_id), "stage": stage, "lost_reason": (reason or "").strip()}
        return _call(caller, "move_deal", arguments, language)
    if match := DNC.match(message):
        _, own_id, number, reason = match.groups()
        target = _target(plan, number, own_id, "company_id", language)
        if isinstance(target, Turn):
            return target
        return _call(caller, "mark_do_not_contact", {"company_id": target, "reason": (reason or "").strip()}, language)
    if match := SEND.match(message):
        return _call(caller, "approve_draft", {"message_id": int(match.group(2))}, language)
    if match := YES.match(message):
        return _decide(caller, "confirm_proposal", match.group(2), language)
    if match := NO.match(message):
        return _decide(caller, "reject_proposal", match.group(2), language)
    return None


def _target(plan: dict, number: str, own_id: str | None, key: str, language: str) -> int | Turn:
    """`#12` = the object's own id; a plain number = that item of the plan (its task, or its company)."""
    if own_id:
        return int(number)
    item = plan_service.item(plan, int(number))
    if item is None or item.get(key) is None:
        return Turn(text("item_not_task", language, n=number))
    return item[key]


def _decide(caller: Caller, tool: str, proposal_id: str | None, language: str) -> Turn:
    if proposal_id is None:
        proposal = proposal_service.latest(caller)
        if proposal is None:
            return Turn(text("no_proposal", language))
        proposal_id = str(proposal.pk)
    return _call(caller, tool, {"proposal_id": int(proposal_id)}, language)


def _call(caller: Caller, tool: str, arguments: dict, language: str) -> Turn:
    outcome = _safe_call(caller, tool, arguments, language)
    return Turn(outcome["message"], [outcome])


def _safe_call(caller: Caller, tool: str, arguments: dict, language: str) -> dict[str, Any]:
    try:
        return tool_service.call(caller, tool, arguments, language)
    except ToolError as exc:
        return {
            "tool": tool,
            "status": "error",
            "code": exc.code,
            "message": text("error", language, message=exc.message),
        }  # noqa: E501


# --- model -----------------------------------------------------------------------------------------------------------


def _model(caller: Caller, message: str, plan: dict, language: str, history: list[dict]) -> Turn:
    model = module_settings.get("AI_CRM_MODEL")
    if not model or not getattr(settings, "AI_TOOLBOX_ENABLED", True):
        return Turn(text("not_understood", language))
    answer = _complete(caller, model, message, plan, history)
    if answer is None:
        return Turn(text("not_understood", language), used_model=True)
    results = []
    for action in answer["actions"][: module_settings.get("AI_CRM_MAX_ACTIONS")]:
        tool = action.get("tool")
        if tool in MODEL_FORBIDDEN_TOOLS:
            continue
        results.append(_safe_call(caller, tool, action.get("arguments") or {}, language))
    reply = "\n".join([answer["reply"], *(r["message"] for r in results)]).strip()
    return Turn(reply or text("not_understood", language), results, used_model=True)


def _complete(caller: Caller, model: str, message: str, plan: dict, history: list[dict]) -> dict | None:
    """The model's {reply, actions}, or None when the toolbox fails or answers something else."""
    from django_utils.toolbox.client import ToolboxClient
    from django_utils.toolbox.errors import ToolboxError
    from django_utils.toolbox.schemas import CompletionRequest, Message

    tools = [
        {"name": t["name"], "description": t["description"], "arguments": t["inputSchema"]}
        for t in tool_service.catalogue()
        if t["name"] not in MODEL_FORBIDDEN_TOOLS
    ]  # noqa: E501
    pending = [proposal_service.dump(p) for p in proposal_service.pending(caller)]
    context = json.dumps({"tools": tools, "plan": plan["items"], "pending_proposals": pending}, ensure_ascii=False)
    earlier = [
        Message(role=turn["role"], content=str(turn.get("content", ""))[:2000])
        for turn in history[-10:]
        if turn.get("role") in ("user", "assistant")
    ]
    request = CompletionRequest(
        model=model,
        messages=[
            Message(role="system", content=f"{SYSTEM_PROMPT}\n\n{context}"),
            *earlier,
            Message(role="user", content=message),
        ],  # noqa: E501
        json_schema=REPLY_SCHEMA,
        max_tokens=module_settings.get("AI_CRM_MAX_TOKENS"),
        tags=[CHAT_TAG, f"ai_crm.source:{caller.source}"],
    )
    try:
        with ToolboxClient() as client:
            response = client.complete(request)
    except ToolboxError as exc:
        logger.warning("event=ai_crm_chat_toolbox_failed error=%s", type(exc).__name__)
        return None
    parsed = response.parsed
    if not isinstance(parsed, dict) or not isinstance(parsed.get("reply"), str):
        logger.info("event=ai_crm_chat_unusable_answer model=%s", response.model)
        return None
    actions = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
    return {"reply": parsed["reply"], "actions": [a for a in actions if isinstance(a, dict)]}
