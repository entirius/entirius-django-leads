# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Words for the screens: the company name on communicator threads, the reply alert title and its single bell entry,
the `data` of every CRM activity, the import report's ignored columns and automation count."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone
from django_communicator import signals as communicator_signals
from django_communicator.enums import SequenceStopReason
from django_communicator.models import Sequence, Thread, ThreadSequenceState
from django_communicator.services import compose_service
from django_notifications.models import Notification

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Deal
from django_leads.schemas.responses import ImportBatchResponse
from django_leads.services import alert_service, deal_service, owner_service, sequence_note_service
from tests.test_compose import TEXT, compose, footer, mailer, piotr  # noqa: F401 — fixtures
from tests.test_deals import open_deal, pipeline  # noqa: F401 — fixture
from tests.test_import_attributes import definitions, run  # noqa: F401 — fixture
from tests.test_import_rules import run as run_rules
from tests.test_owner import draft, notifications, staff  # noqa: F401 — fixture
from tests.test_stage_events import reply, thread_of
from tests.test_tasks import manual_task, needs_todo

pytestmark = pytest.mark.django_db


def last(company, kind: str) -> Activity:
    return company.activities.filter(kind=kind).order_by("pk").last()


# --- subject label ----------------------------------------------------------------------------------------------


def test_a_draft_names_the_company_on_the_thread(shop):
    assert draft(shop).call_args.kwargs["subject_label"] == shop.name


def test_a_hand_written_mail_names_the_company_on_the_thread(mailer, footer, shop, piotr, admin_api):  # noqa: F811
    with mock.patch.object(compose_service, "compose", wraps=compose_service.compose) as spy:
        assert compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT}).status_code == 201
    assert spy.call_args.kwargs["subject_label"] == shop.name
    assert Thread.objects.get().subject_label == shop.name


# --- reply alert ------------------------------------------------------------------------------------------------


def answers(company, intent: str = "not_now", needs_review: bool = False) -> list:
    responses = communicator_signals.reply_received.send_robust(
        sender=Thread, thread=thread_of(company), reply=reply(intent, needs_review=needs_review)
    )
    return [response for _, response in responses]


def test_the_reply_alert_names_the_company_and_the_class_and_answers_alerted(shop, notifications):  # noqa: F811
    assert communicator_signals.REPLY_ALERTED in answers(shop)
    assert list(Notification.objects.values_list("title", flat=True)) == [f"Reply from {shop.name} — Not now"]


def test_a_reply_without_a_class_has_no_suffix(shop, notifications):  # noqa: F811
    answers(shop, intent="")
    assert list(Notification.objects.values_list("title", flat=True)) == [f"Reply from {shop.name}"]


def test_a_flagged_reply_without_a_class_needs_review(shop, notifications):  # noqa: F811
    answers(shop, intent="", needs_review=True)
    assert list(Notification.objects.values_list("title", flat=True)) == [f"Reply from {shop.name} — Needs review"]


def test_without_notifications_leads_does_not_answer_alerted(shop):
    with mock.patch.object(alert_service, "_notifications_available", return_value=False):
        assert communicator_signals.REPLY_ALERTED not in answers(shop)
    assert last(shop, ActivityKind.REPLY).data["event"] == "reply"


def test_a_company_of_no_one_is_left_to_communicator(channel):
    thread = SimpleNamespace(pk=5, subject_ref="other.Thing:1", recipient_email="ola@example-shop-4.test")
    responses = communicator_signals.reply_received.send_robust(sender=Thread, thread=thread, reply=reply("not_now"))
    assert communicator_signals.REPLY_ALERTED not in [response for _, response in responses]


# --- activity data ----------------------------------------------------------------------------------------------


def test_reply_activity_data(shop):
    answers(shop, intent="interested")
    assert last(shop, ActivityKind.REPLY).data == {
        "event": "reply",
        "thread_id": 5,
        "reply_id": 9,
        "class": "interested",
    }


@needs_todo
def test_task_activities_carry_the_event_title_kind_and_reason(shop, django_capture_on_commit_callbacks):
    from django_todo.services import task_service

    with django_capture_on_commit_callbacks(execute=True):
        task = manual_task(shop)
    with django_capture_on_commit_callbacks(execute=True):
        task_service.cancel(task, actor="seller", reason="not needed")
    with django_capture_on_commit_callbacks(execute=True):
        task_service.reopen(task, actor="seller")
    rows = list(shop.activities.filter(kind=ActivityKind.TASK).order_by("pk"))
    assert [row.data["event"] for row in rows] == ["task_created", "task_cancelled", "task_reopened"]
    assert {(row.data["task_id"], row.data["title"], row.data["kind"]) for row in rows} == {
        (task.pk, "Call back", "phone")
    }
    assert rows[1].data["reason"] == "not needed" and rows[2].message == "reopened: Call back"


def test_hand_written_mail_data_and_the_sequence_it_stopped(mailer, footer, shop, piotr, admin_api):  # noqa: F811
    thread = Thread.objects.create(channel=mailer, subject_ref=f"leads.Company:{shop.pk}", recipient_email=piotr.email)
    sequence = Sequence.objects.create(channel=mailer, key="follow")
    ThreadSequenceState.objects.create(thread=thread, sequence=sequence, next_due_at=timezone.now())
    message_id = compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT}).json()["message_id"]
    data = last(shop, ActivityKind.DRAFT).data
    assert (data["event"], data["message_id"], data["thread_id"]) == ("composed", message_id, thread.pk)
    note = last(shop, ActivityKind.NOTE)
    assert (note.message, note.data) == ("sequence stopped", {"event": "sequence_stopped"})


def test_a_paused_sequence_names_the_resume_moment(mailer, shop, piotr):  # noqa: F811
    thread = Thread.objects.create(channel=mailer, subject_ref=f"leads.Company:{shop.pk}", recipient_email=piotr.email)
    now, resume_at = timezone.now(), timezone.now() + timedelta(days=3)
    ThreadSequenceState.objects.create(
        thread=thread, sequence=Sequence.objects.create(channel=mailer, key="follow"), stopped_at=now,
        stop_reason=SequenceStopReason.PAUSED, resume_at=resume_at,
    )  # fmt: skip
    note = sequence_note_service.record(shop, piotr, thread_id=thread.pk, since=now, actor="system")
    assert note.data == {"event": "sequence_paused", "resume_at": resume_at.isoformat()}
    assert sequence_note_service.record(shop, piotr, thread_id=thread.pk, since=now + timedelta(1), actor="x") is None


def test_deal_activities_carry_event_title_and_stage_names(admin_api, pipeline, shop):  # noqa: F811
    deal_id = open_deal(admin_api, shop)["id"]
    deal = Deal.objects.get(pk=deal_id)
    deal_service.transition(deal, pipeline["offer"], actor="seller")
    opened, moved = (row.data for row in Activity.objects.filter(deal_id=deal_id).order_by("pk"))
    assert opened == {"deal_id": deal_id, "title": "Shop audit", "event": "deal_opened"}
    assert moved == {
        "deal_id": deal_id, "title": "Shop audit", "event": "deal_moved", "from": "lead", "to": "offer",
        "from_stage": "Lead", "to_stage": "Offer",
    }  # fmt: skip


def test_owner_changes_carry_the_owner_name(admin_api, pipeline, shop):  # noqa: F811
    anna = staff("anna", first_name="Anna", last_name="Nowak")
    owner_service.set_owner(shop, anna, actor="test")
    assert last(shop, ActivityKind.NOTE).data == {"event": "owner_set", "owner_name": "Anna Nowak"}
    owner_service.set_owner(shop, None, actor="test")
    assert last(shop, ActivityKind.NOTE).data == {"event": "owner_cleared", "owner_name": ""}
    deal = Deal.objects.get(pk=open_deal(admin_api, shop)["id"])
    owner_service.set_deal_owner(deal, staff("piotr"), actor="test")
    data = last(shop, ActivityKind.DEAL).data
    assert (data["event"], data["owner_name"], data["deal_id"]) == ("owner_set", "piotr", deal.pk)


def test_bulk_assign_carries_the_owner_name(channel, shop):
    owner_service.assign(channel, [shop.pk], staff("anna", first_name="Anna"), actor="test")
    assert last(shop, ActivityKind.NOTE).data == {"event": "owner_set", "owner_name": "Anna"}


# --- import report ----------------------------------------------------------------------------------------------


def test_the_import_report_names_ignored_columns(channel, definitions):  # noqa: F811
    batch = run(channel, "A,a-shop.pl,,,,,,,")
    body = ImportBatchResponse.of(batch).model_dump()
    assert body["ignored_columns"] == ["attr.gone", "shoe_size"] and body["automation_started_count"] == 0
    assert batch.report == []  # the names are in `ignored_columns` only


@pytest.mark.parametrize(("rules", "started"), [(True, 3), (False, 0)])
def test_automation_started_count(channel, rules, started, django_capture_on_commit_callbacks):
    batch, _ = run_rules(channel, run_rules=rules, capture=django_capture_on_commit_callbacks)
    assert batch.automation_started_count == started
    assert type(batch).objects.get(pk=batch.pk).automation_started_count == started
