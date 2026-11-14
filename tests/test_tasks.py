# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tasks from replies (django_todo, soft dependency), their cancellation, the timeline, resume days, GDPR."""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_communicator import signals as communicator_signals
from django_communicator.models import Thread

from django_leads.enums import ActivityKind
from django_leads.gdpr import hooks
from django_leads.models import Activity, Contact
from django_leads.services import contact_service, retention_service, task_bridge
from tests.conftest import api_url

pytestmark = pytest.mark.django_db
needs_todo = pytest.mark.skipif(not apps.is_installed("django_todo"), reason="django_todo not installed")

PIOTR = "piotr@example-shop-4.test"


@pytest.fixture
def piotr(shop) -> Contact:
    contact = shop.contacts.get(email=PIOTR)
    contact.first_name, contact.last_name = "Piotr", "Nowak"
    contact.save(update_fields=["first_name", "last_name"])
    return contact


def reply(pk: int = 9, intent: str = "", *, needs_review: bool = False, return_date: date | None = None):
    return SimpleNamespace(
        pk=pk, body_text="", intent=intent, needs_review=needs_review, intent_return_date=return_date,
        plan_applied={"notify": "none"}, received_at=timezone.now(),
    )  # fmt: skip


def send_reply(company, answer, capture, email: str = PIOTR) -> None:
    thread = SimpleNamespace(pk=5, subject_ref=f"leads.Company:{company.pk}", recipient_email=email)
    with capture(execute=True):
        communicator_signals.reply_received.send(sender=Thread, thread=thread, reply=answer)


def tasks(**filters):
    from django_todo.models import Task

    return list(Task.objects.filter(**filters).order_by("pk"))


def timeline(company) -> list[str]:
    return list(company.activities.filter(kind=ActivityKind.TASK).order_by("pk").values_list("message", flat=True))


def day_after(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))


# --- tasks per event -------------------------------------------------------------------------------------------------

ROWS = [
    # intent, needs_review, return date, kind, title, due in days (None = the day after the return date), source, key
    ("interested", False, None, "todo", "Interested: answer and open a deal", 0, "leads.reply.interested", "reply:9"),
    ("not_now", False, None, "mail", "Come back: Piotr Nowak", 30, "leads.reply.not_now", "resume:{c}"),
    ("not_now", False, date(2027, 3, 1), "mail", "Come back: Piotr Nowak", None, "leads.reply.not_now", "resume:{c}"),
    ("out_of_office", False, None, "mail", "Try again: Piotr Nowak", 7, "leads.reply.out_of_office", "resume:{c}"),
    ("wrong_person", False, None, "todo", "Find the right person", 0, "leads.reply.wrong_person", "wrong:{c}"),
    ("interested", True, None, "todo", "Read the reply", 0, "leads.reply.review", "reply:9"),
    ("", False, None, "todo", "Read the reply", 0, "leads.reply.review", "reply:9"),
]


@needs_todo
@pytest.mark.parametrize(("intent", "review", "returns", "kind", "title", "days", "source", "key"), ROWS)
def test_a_reply_creates_one_task_per_the_table(
    shop, piotr, intent, review, returns, kind, title, days, source, key, django_capture_on_commit_callbacks
):
    before = timezone.now()
    send_reply(shop, reply(intent=intent, needs_review=review, return_date=returns), django_capture_on_commit_callbacks)
    [task] = tasks()
    assert (task.kind, task.title, task.source, task.dedup_key) == (kind, title, source, key.format(c=piotr.pk))
    assert (task.subject_ref, task.subject_label) == (f"leads.Company:{shop.pk}", "Example Shop 4")
    assert (task.contact_ref, task.created_by, task.owner_id) == (f"leads.Contact:{piotr.pk}", "reply", None)
    expected = day_after(returns) if days is None else before + timedelta(days=days)
    assert abs(task.due_at - expected) < timedelta(seconds=5)
    assert timeline(shop) == []  # leads' own tasks write no "created" activity


@needs_todo
@pytest.mark.parametrize("intent", ["unsubscribe", "not_interested", "unclear"])
def test_no_task_for_other_classes(shop, piotr, intent, django_capture_on_commit_callbacks):
    send_reply(shop, reply(intent=intent), django_capture_on_commit_callbacks)
    assert tasks() == []


@needs_todo
def test_out_of_office_with_a_return_date_creates_no_task(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(intent="out_of_office", return_date=date(2027, 1, 7)), django_capture_on_commit_callbacks)
    assert tasks() == []


@needs_todo
def test_an_interested_reply_goes_to_the_owner_due_now(shop, piotr, django_capture_on_commit_callbacks):
    owner = get_user_model().objects.create_user(username="seller", is_staff=True)
    shop.owner = owner
    shop.save(update_fields=["owner"])
    send_reply(shop, reply(intent="interested"), django_capture_on_commit_callbacks)
    [task] = tasks()
    assert task.owner_id == owner.pk and task.due_at <= timezone.now()
    assert task.note == "Open a deal on this company"


@needs_todo
def test_the_same_reply_twice_creates_one_task(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(intent="interested"), django_capture_on_commit_callbacks)
    send_reply(shop, reply(intent="interested"), django_capture_on_commit_callbacks)
    assert len(tasks()) == 1


@needs_todo
def test_channel_resume_days_set_the_due_date(shop, piotr, channel, django_capture_on_commit_callbacks):
    channel.resume_days = {"not_now": 10}
    channel.save(update_fields=["resume_days"])
    send_reply(shop, reply(intent="not_now"), django_capture_on_commit_callbacks)
    assert abs(tasks()[0].due_at - (timezone.now() + timedelta(days=10))) < timedelta(seconds=5)


# --- cancellation ----------------------------------------------------------------------------------------------------


@needs_todo
def test_a_newer_reply_cancels_the_come_back_task(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(9, intent="not_now"), django_capture_on_commit_callbacks)
    send_reply(shop, reply(10, intent="interested"), django_capture_on_commit_callbacks)
    come_back, interested = tasks()
    assert (come_back.status, come_back.outcome, come_back.done_by) == ("cancelled", "newer reply", "system")
    assert (interested.status, interested.source) == ("open", "leads.reply.interested")
    assert timeline(shop) == ["cancelled: Come back: Piotr Nowak"]


@needs_todo
def test_a_second_not_now_replaces_the_come_back_task(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(9, intent="not_now"), django_capture_on_commit_callbacks)
    send_reply(shop, reply(10, intent="not_now", return_date=date(2027, 5, 4)), django_capture_on_commit_callbacks)
    first, second = tasks()
    assert first.status == "cancelled" and second.status == "open" and second.due_at == day_after(date(2027, 5, 4))


@needs_todo
def test_an_opt_out_cancels_the_come_back_task(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(intent="not_now"), django_capture_on_commit_callbacks)
    with django_capture_on_commit_callbacks(execute=True):
        contact_service.opt_out(piotr, reason="manual", actor="operator")
    [task] = tasks()
    assert (task.status, task.outcome) == ("cancelled", "opted out")


@needs_todo
def test_anonymisation_cancels_and_scrubs_the_tasks(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(intent="not_now"), django_capture_on_commit_callbacks)
    with django_capture_on_commit_callbacks(execute=True):
        retention_service.anonymise_contact(piotr)
    [task] = tasks()
    assert (task.status, task.title, task.outcome) == ("cancelled", "[erased]", "[erased]")
    assert timeline(shop) == ["cancelled: [erased]"]  # the timeline never sees the name


def test_without_django_todo_a_reply_creates_nothing_and_raises_nothing(
    shop, piotr, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(task_bridge, "available", lambda: False)
    send_reply(shop, reply(intent="not_now"), django_capture_on_commit_callbacks)
    contact_service.opt_out(piotr, reason="manual", actor="operator")
    assert task_bridge.scrub(piotr) == 0 and task_bridge.export([piotr]) == []
    assert Activity.objects.filter(company=shop, kind=ActivityKind.REPLY).count() == 1
    if apps.is_installed("django_todo"):
        assert tasks() == []


@needs_todo
def test_a_failing_task_creation_keeps_the_reply(shop, piotr, django_capture_on_commit_callbacks):
    with mock.patch.object(task_bridge, "create", side_effect=RuntimeError("boom")):
        send_reply(shop, reply(intent="interested"), django_capture_on_commit_callbacks)
    assert Activity.objects.filter(company=shop, kind=ActivityKind.REPLY).count() == 1


# --- timeline --------------------------------------------------------------------------------------------------------


def manual_task(company, contact=None, **fields):
    from django_todo.services import task_service

    contact_ref = f"leads.Contact:{contact.pk}" if contact else ""
    defaults = {"kind": "phone", "title": "Call back", "created_by": "seller", "source": "manual"}
    return task_service.create(
        channel_idx=company.channel.idx, subject_ref=f"leads.Company:{company.pk}", subject_label=company.name,
        contact_ref=contact_ref, **{**defaults, **fields},
    )  # fmt: skip


@needs_todo
def test_a_person_s_task_shows_when_created_and_closed(shop, piotr, django_capture_on_commit_callbacks):
    from django_todo.services import task_service

    with django_capture_on_commit_callbacks(execute=True):
        task = manual_task(shop, piotr)
    with django_capture_on_commit_callbacks(execute=True):
        task_service.close(task, outcome="meeting on Friday", actor="seller")
    assert timeline(shop) == ["task: Call back", "done: Call back — meeting on Friday"]
    closed = shop.activities.filter(kind=ActivityKind.TASK).order_by("pk").last()
    created = shop.activities.filter(kind=ActivityKind.TASK).order_by("pk").first()
    assert created.data == {
        "task_id": task.pk, "title": "Call back", "kind": "phone", "status": "open", "event": "task_created",
    }  # fmt: skip
    assert closed.data == {
        "task_id": task.pk, "title": "Call back", "kind": "phone", "status": "done", "event": "task_done",
        "outcome": "meeting on Friday",
    }  # fmt: skip
    assert (closed.contact, closed.actor) == (piotr, "seller")


@needs_todo
def test_a_cancelled_task_shows_in_the_timeline(shop, django_capture_on_commit_callbacks):
    from django_todo.services import task_service

    with django_capture_on_commit_callbacks(execute=True):
        task = manual_task(shop)
        task_service.cancel(task, actor="seller", reason="duplicate")
    assert timeline(shop) == ["task: Call back", "cancelled: Call back"]


@needs_todo
@pytest.mark.parametrize("ref", ["other.Thing:1", "leads.Company:999999"])
def test_tasks_of_other_subjects_are_ignored(shop, ref, django_capture_on_commit_callbacks):
    from django_todo.services import task_service

    with django_capture_on_commit_callbacks(execute=True):
        task_service.create(
            channel_idx=shop.channel.idx, subject_ref=ref, subject_label="x", kind="todo", title="t", created_by="a"
        )
    assert not Activity.objects.filter(kind=ActivityKind.TASK).exists()


@needs_todo
def test_a_task_of_another_channel_is_ignored(shop, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        manual_task(SimpleNamespace(pk=shop.pk, name="x", channel=SimpleNamespace(idx="other")))
    assert timeline(shop) == []


# --- channel settings ------------------------------------------------------------------------------------------------


def test_channel_settings_show_the_defaults_and_take_overrides(channel, admin_api):
    assert admin_api.get(api_url("channel/")).json()["resume_days"] == {"not_now": 30, "out_of_office": 7}
    response = admin_api.patch(api_url("channel/"), {"resume_days": {"not_now": 14}}, format="json")
    assert response.status_code == 200 and response.json()["resume_days"] == {"not_now": 14, "out_of_office": 7}
    channel.refresh_from_db()
    assert channel.resume_days == {"not_now": 14}


@pytest.mark.parametrize(
    "body", [{"resume_days": {"not_now": 0}}, {"resume_days": {"later": 3}}, {"retention_days": 5}]
)
def test_channel_settings_reject_bad_values(channel, admin_api, body):
    assert admin_api.patch(api_url("channel/"), body, format="json").status_code == 400


def test_channel_settings_need_staff(channel, customer_api):
    assert customer_api.get(api_url("channel/")).status_code == 403


# --- GDPR ------------------------------------------------------------------------------------------------------------


@needs_todo
def test_gdpr_export_lists_and_erase_scrubs_the_contact_s_tasks(shop, piotr, django_capture_on_commit_callbacks):
    send_reply(shop, reply(intent="not_now"), django_capture_on_commit_callbacks)
    [exported] = hooks.gdpr_export(PIOTR)["Task"]
    assert (exported["title"], exported["status"]) == ("Come back: Piotr Nowak", "open")
    assert set(exported) == {"id", "title", "status", "due_at"}
    hooks.gdpr_erase(PIOTR)
    assert tasks()[0].title == "[erased]"


@needs_todo
def test_cancel_leaves_another_module_s_task_with_the_same_key(shop, piotr):
    from django_todo.services import task_service

    task_service.create(
        channel_idx=shop.channel.idx, subject_ref="other.Thing:1", subject_label="x", kind="todo", title="t",
        created_by="a", source="other", dedup_key=f"resume:{piotr.pk}",
    )  # fmt: skip
    assert task_bridge.cancel(shop.channel.idx, f"resume:{piotr.pk}", reason="newer reply") == 0
    assert tasks()[0].status == "open"


def test_channel_settings_null_resets_to_the_defaults(channel, admin_api):
    channel.resume_days = {"not_now": 3}
    channel.save(update_fields=["resume_days"])
    response = admin_api.patch(api_url("channel/"), {"resume_days": None}, format="json")
    assert response.json()["resume_days"] == {"not_now": 30, "out_of_office": 7}
    channel.refresh_from_db()
    assert channel.resume_days == {}
