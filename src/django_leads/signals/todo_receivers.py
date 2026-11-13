# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""django_todo → leads timeline (soft: connected only when django_todo is installed).

A task about a company or a deal of this module shows in the company's history: created by a person, done with its outcome,
cancelled. Tasks leads creates itself write no "created" activity — the reply activity already says why.
"""

from django_leads.enums import ActivityKind
from django_leads.models import Company, Deal
from django_leads.services import activity_service, task_bridge
from django_leads.signals._deferred import after_commit
from django_leads.utils.refs import company_from_ref, contact_from_ref, deal_from_ref


def connect() -> bool:
    """Connect the receivers when django_todo is installed; `False` otherwise."""
    if not task_bridge.available():
        return False
    from django_todo.signals import task_closed, task_created

    task_created.connect(on_task_created, dispatch_uid="django_leads.on_task_created")
    task_closed.connect(on_task_closed, dispatch_uid="django_leads.on_task_closed")
    return True


def on_task_created(sender, task, **kwargs) -> None:
    if task.source.startswith(task_bridge.SOURCE_PREFIX):
        return
    after_commit(lambda: record_task(task, f"task: {task.title}", task.created_by), "on_task_created")


def on_task_closed(sender, task, **kwargs) -> None:
    after_commit(lambda: record_closed(task), "on_task_closed")


def record_closed(task) -> None:
    """Re-read first: a task scrubbed in the closing transaction (anonymisation) carries no personal text here."""
    from django_todo.enums import Status

    task.refresh_from_db()
    if task.status != Status.DONE:
        record_task(task, f"cancelled: {task.title}", task.done_by)
    elif task.outcome:
        record_task(task, f"done: {task.title} — {task.outcome}", task.done_by)
    else:
        record_task(task, f"done: {task.title}", task.done_by)


def record_task(task, message: str, actor: str) -> None:
    """Activity `task` on the referenced company of the task's channel — for a deal, its company with the deal set;
    any other subject is ignored."""
    company, deal = subject_of(task.subject_ref)
    if company is None or company.channel.idx != task.channel_idx:
        return
    contact = contact_from_ref(company, task.contact_ref)
    data = {"task_id": task.pk, "kind": task.kind, "status": task.status}
    activity_service.record(
        company, ActivityKind.TASK, message, contact=contact, deal=deal, data=data, actor=actor or "system"
    )


def subject_of(subject_ref: str) -> tuple[Company | None, Deal | None]:
    deal = deal_from_ref(subject_ref)
    return (deal.company, deal) if deal else (company_from_ref(subject_ref), None)
