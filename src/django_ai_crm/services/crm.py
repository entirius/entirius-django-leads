# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The only place that touches other modules — through their public services, imported lazily.

Every module is soft: `available(<module>)` says whether its operations exist. The caller sees only what is theirs:
their own tasks, the companies and deals they own, the drafts of companies they own. Anything else is "not found",
the same answer as a missing row, so the agent never confirms that another salesperson's record exists.
"""

from datetime import datetime, tzinfo
from typing import Any

from django.apps import apps

from django_ai_crm.services.caller import Caller, ToolError

MODULES = {"todo": "django_todo", "leads": "django_leads", "communicator": "django_communicator"}
COMPANY_REF_PREFIX = "leads.Company:"


def available(module: str) -> bool:
    return apps.is_installed(MODULES[module])


def require(module: str) -> None:
    if not available(module):
        raise ToolError("unavailable", f"The {module} module is not installed.")


# --- todo ------------------------------------------------------------------------------------------------------------


def today_tasks(caller: Caller, tz: tzinfo) -> tuple[list[Any], int]:
    """The caller's open tasks overdue and due today (as the Today screen) and the count due in the next 7 days."""
    from django_todo.services import task_service

    summary = task_service.today(caller.channel_idx, caller.user, tz=tz)
    return summary.rows, summary.upcoming_7d


def own_task(caller: Caller, task_id: int):
    from django_todo.models import Task
    from django_todo.services import task_service

    try:
        task = task_service.get_task(caller.channel_idx, task_id)
    except Task.DoesNotExist:
        task = None
    if task is None or task.owner_id != caller.user.pk:
        raise ToolError("not_found", f"Task {task_id} not found among your tasks.")
    return task


def close_task(caller: Caller, task_id: int, outcome: str):
    from django_todo.services import task_service

    task = own_task(caller, task_id)
    try:
        return task_service.close(task, outcome=outcome, actor=caller.actor)
    except task_service.TaskStateError as exc:
        raise ToolError("conflict", str(exc)) from None


def create_task(caller: Caller, company, *, title: str, due_at: datetime | None, kind: str, note: str):
    from django_todo.services import task_service

    try:
        return task_service.create(
            channel_idx=caller.channel_idx, subject_ref=f"{COMPANY_REF_PREFIX}{company.pk}",
            subject_label=company.name or company.domain, kind=kind, title=title, note=note, due_at=due_at,
            owner_id=caller.user.pk, created_by=caller.actor, source=f"ai_crm.{caller.source}",
        )  # fmt: skip
    except ValueError as exc:
        raise ToolError("invalid", str(exc)) from None


# --- leads -----------------------------------------------------------------------------------------------------------


def own_company(caller: Caller, company_id: int):
    from django_leads.models import Company

    company = Company.objects.select_related("channel").filter(pk=company_id).first()
    if company is None or company.channel.idx != caller.channel_idx or company.owner_id != caller.user.pk:
        raise ToolError("not_found", f"Company {company_id} not found among your companies.")
    return company


def company_of_ref(caller: Caller, subject_ref: str):
    """The company a task is about, when it is a leads company the caller owns; else None."""
    if not available("leads") or not (subject_ref or "").startswith(COMPANY_REF_PREFIX):
        return None
    try:
        return own_company(caller, int(subject_ref.removeprefix(COMPANY_REF_PREFIX)))
    except (ValueError, ToolError):
        return None


def add_note(caller: Caller, company, text: str):
    from django_leads.enums import ActivityKind
    from django_leads.services import activity_service

    data = {"event": "note_added", "text": text, "via": "ai_crm", "source": caller.source}
    return activity_service.record(company, ActivityKind.NOTE, f"note: {text}", data=data, actor=caller.actor)


def own_deal(caller: Caller, deal_id: int):
    from django_leads.models import Deal

    deal = Deal.objects.select_related("channel", "company", "stage").filter(pk=deal_id).first()
    mine = deal is not None and caller.user.pk in (deal.owner_id, deal.company.owner_id)
    if not mine or deal.channel.idx != caller.channel_idx:
        raise ToolError("not_found", f"Deal {deal_id} not found among your deals.")
    return deal


def deal_stage(deal, key: str):
    from django_leads.models import DealStage

    stage = DealStage.objects.filter(channel_id=deal.channel_id, key=key).first()
    if stage is None:
        keys = ", ".join(
            DealStage.objects.filter(channel_id=deal.channel_id).order_by("order").values_list("key", flat=True)
        )
        raise ToolError("invalid", f"Unknown deal stage {key!r}; the stages are: {keys}.")
    return stage


def is_lost(stage) -> bool:
    from django_leads.enums import DealStageKind

    return stage.kind == DealStageKind.LOST


def move_deal(caller: Caller, deal, stage, lost_reason: str = ""):
    from django_leads.services import deal_service

    try:
        return deal_service.transition(deal, stage, actor=caller.actor, lost_reason=lost_reason)
    except deal_service.InvalidDeal as exc:
        raise ToolError("invalid", str(exc)) from None
    except deal_service.DealArchived:
        raise ToolError("conflict", "The deal is archived; restore it first.") from None


def set_do_not_contact(caller: Caller, company, reason: str):
    from django_leads.services import company_service

    company = company_service.set_do_not_contact(company, True, actor=caller.actor)
    if reason:
        add_note(caller, company, f"do not contact — {reason}")
    return company


# --- communicator ----------------------------------------------------------------------------------------------------


def _comm_channel(caller: Caller):
    from django_communicator.models import Channel

    return Channel.objects.filter(idx=caller.channel_idx).first()


def waiting_counts(caller: Caller, *, drafts: bool, replies: bool) -> dict[str, int | None]:
    """Drafts waiting for review and replies flagged for a person — the two Today counters (channel-wide counts)."""
    from django_communicator.services import inbox_service

    channel = _comm_channel(caller)
    counts: dict[str, int | None] = {"drafts": None, "replies": None}
    if channel is None:
        return counts
    if drafts:
        counts["drafts"] = inbox_service.count_conversations(channel)["draft"]
    if replies:
        counts["replies"] = inbox_service.list_replies(channel, inbox_service.ReplyFilter(needs_review=True)).count()
    return counts


def own_draft(caller: Caller, message_id: int):
    from django_communicator.enums import MessageStatus
    from django_communicator.models import Message
    from django_communicator.services import review_service

    channel = _comm_channel(caller)
    try:
        message = review_service.get_message(channel, message_id) if channel else None
    except Message.DoesNotExist:
        message = None
    if message is None or company_of_ref(caller, message.thread.subject_ref) is None:
        raise ToolError("not_found", f"Draft {message_id} not found among the drafts of your companies.")
    if message.status != MessageStatus.REVIEW_REQUIRED:
        raise ToolError("conflict", f"Draft {message_id} is not waiting for review ({message.status}).")
    return message


def approve_draft(caller: Caller, message):
    from django_communicator.services import message_service, review_service

    try:
        review_service.accept(message, user=caller.user)
    except (message_service.InvalidTransitionError, review_service.ReviewError) as exc:
        raise ToolError("conflict", getattr(exc, "client_message", str(exc))) from None
    company = company_of_ref(caller, message.thread.subject_ref)
    if company is not None:
        add_note(caller, company, f"draft {message.pk} approved for sending")
    return message
