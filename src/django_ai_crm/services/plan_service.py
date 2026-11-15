# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The day plan: what the salesperson has to do today, in order — the same work as the Today screen.

1. their open tasks overdue, then due today (django_todo, oldest due first);
2. drafts waiting for review and replies waiting for a person (django_communicator, the Today counters);
A company coming back is a task ("Come back", created by leads), so it is in the first list.

Each source gates itself: module not installed or area not readable → the source is left out, never an error.
"""

from datetime import tzinfo
from typing import Any

from django.utils import timezone

from django_ai_crm.services import crm
from django_ai_crm.services.caller import (
    CONVERSATIONS_READ,
    REVIEW_READ,
    TASKS_READ,
    Caller,
)


def day_plan(caller: Caller, tz: tzinfo | None = None) -> dict[str, Any]:
    tz = tz or timezone.get_current_timezone()
    items: list[dict[str, Any]] = []
    upcoming = None
    if crm.available("todo") and caller.can(TASKS_READ):
        rows, upcoming = crm.today_tasks(caller, tz)
        items += [_task_item(caller, task, tz) for task in rows]
    if crm.available("communicator"):
        counts = crm.waiting_counts(
            caller, drafts=caller.can(REVIEW_READ), replies=caller.can(CONVERSATIONS_READ)
        )  # fmt: skip
        items += [{"type": kind, "count": count} for kind, count in counts.items() if count]
    for number, item in enumerate(items, start=1):
        item["n"] = number
    return {"items": items, "upcoming_7d": upcoming, "generated_at": timezone.now().isoformat()}


def _task_item(caller: Caller, task, tz: tzinfo) -> dict[str, Any]:
    company = crm.company_of_ref(caller, task.subject_ref)
    today_start = timezone.now().astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "type": "task",
        "task_id": task.pk,
        "title": task.title,
        "kind": task.kind,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "overdue": bool(task.due_at and task.due_at < today_start),
        "subject_ref": task.subject_ref,
        "subject_label": task.subject_label,
        "company_id": company.pk if company else None,
    }


def item(plan: dict[str, Any], number: int) -> dict[str, Any] | None:
    """The plan item with that number (1-based), or None."""
    for item in plan["items"]:
        if item["n"] == number:
            return item
    return None


def as_text(plan: dict[str, Any], language: str = "en") -> str:
    """The plan as numbered lines for a chat or a terminal."""
    from django_ai_crm.services.texts import text

    if not plan["items"]:
        return text("plan_empty", language)
    lines = [text("plan_head", language)]
    for item in plan["items"]:
        lines.append(f"{item['n']}. {_line(item, language)}")
    lines.append(text("plan_hint", language))
    return "\n".join(lines)


def _line(item: dict[str, Any], language: str) -> str:
    from django_ai_crm.services.texts import text

    if item["type"] == "task":
        when = text("overdue", language) if item["overdue"] else text("today", language)
        about = f" — {item['subject_label']}" if item["subject_label"] else ""
        return f"[{when}] {item['title']}{about} (task #{item['task_id']})"
    return text(f"{item['type']}_line", language, count=item["count"])
