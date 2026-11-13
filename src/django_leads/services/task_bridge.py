# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tasks in django_todo — the only service of leads that imports it (soft dependency, extra `todo`).

Without django_todo installed every call is a no-op: nothing is created, cancelled, scrubbed or exported.
"""

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.apps import apps
from django.db.models import Count

from django_leads.models import Company, Contact
from django_leads.services import owner_service
from django_leads.services.outreach_service import subject_ref
from django_leads.utils.refs import contact_ref

# Every task leads creates has a source with this prefix; the timeline writes no "created" activity for those.
SOURCE_PREFIX = "leads."
CREATED_BY = "reply"
CANCELLED_BY = "system"


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    title: str
    due_at: datetime
    source: str
    dedup_key: str
    note: str = ""


def available() -> bool:
    return apps.is_installed("django_todo")


def create(company: Company, contact: Contact | None, spec: TaskSpec) -> None:
    """An open task about the company for its owner (an owner who cannot hold tasks → unowned); an open task with
    the same dedup key in the channel stays as it is."""
    if not available():
        return
    from django_todo.services import task_service

    task_service.create(
        channel_idx=company.channel.idx, subject_ref=subject_ref(company), subject_label=company.name or company.domain,
        contact_ref=contact_ref(contact) if contact else "", owner_id=_owner_id(company),
        created_by=CREATED_BY, kind=spec.kind, title=spec.title, due_at=spec.due_at, source=spec.source,
        dedup_key=spec.dedup_key, note=spec.note,
    )  # fmt: skip


def _owner_id(company: Company) -> int | None:
    """django_todo assigns only active staff: a deactivated or demoted owner leaves the task unowned."""
    owner_id = owner_service.alert_recipient_id(company)
    return owner_id if owner_id is not None and company.owner.is_staff else None


def cancel(channel_idx: str, dedup_key: str, *, reason: str) -> int:
    """Cancel leads' open task holding the key (never another module's); returns how many were cancelled (a
    concurrent close is skipped)."""
    if not available():
        return 0
    from django_todo.enums import Status
    from django_todo.models import Task
    from django_todo.services import task_service

    cancelled = 0
    open_tasks = Task.objects.filter(channel_idx=channel_idx, dedup_key=dedup_key, status=Status.OPEN)
    for task in open_tasks.filter(source__startswith=SOURCE_PREFIX):
        with suppress(task_service.TaskStateError):
            task_service.cancel(task, actor=CANCELLED_BY, reason=reason)
            cancelled += 1
    return cancelled


def cancel_about(channel_idx: str, subject_ref: str, *, reason: str) -> int:
    """Cancel every open task about the subject (any source: a person's too) — its subject is going away."""
    if not available():
        return 0
    from django_todo.enums import Status
    from django_todo.models import Task
    from django_todo.services import task_service

    cancelled = 0
    for task in Task.objects.filter(channel_idx=channel_idx, subject_ref=subject_ref, status=Status.OPEN):
        with suppress(task_service.TaskStateError):
            task_service.cancel(task, actor=CANCELLED_BY, reason=reason)
            cancelled += 1
    return cancelled


def scrub(contact: Contact) -> int:
    """Erase the free text of every task about the contact; returns the number of tasks."""
    if not available():
        return 0
    from django_todo.services import task_service

    return task_service.scrub(contact_ref=contact_ref(contact))


def export(contacts: list[Contact]) -> list[dict[str, Any]]:
    """The tasks of the contacts for a GDPR export: id, title, status, due."""
    if not available():
        return []
    from django_todo.models import Task

    refs = [contact_ref(contact) for contact in contacts]
    return list(Task.objects.filter(contact_ref__in=refs).order_by("pk").values("id", "title", "status", "due_at"))


def open_counts(channel_idx: str, subject_refs: list[str]) -> dict[str, int]:
    """Open tasks per subject reference of the channel, one query; `{}` without django_todo."""
    if not available() or not subject_refs:
        return {}
    from django_todo.enums import Status
    from django_todo.models import Task

    tasks = Task.objects.filter(channel_idx=channel_idx, subject_ref__in=subject_refs, status=Status.OPEN)
    rows = tasks.values("subject_ref").annotate(open=Count("id")).order_by()
    return {row["subject_ref"]: row["open"] for row in rows}
