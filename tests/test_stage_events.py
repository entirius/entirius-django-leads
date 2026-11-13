# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The event → stage map: lookup, guards, receivers of the three event families, API, data migration."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_communicator import signals as communicator_signals
from django_communicator.models import Message, Thread

from django_leads.enums import ActivityKind, StageEvent
from django_leads.models import Activity, Company, Stage, StageEventRule
from django_leads.services import stage_event_service, stage_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db


def stage(key: str) -> Stage:
    return Stage.objects.get(key=key)


def put_rule(channel, event: str, target: str | None, *, forward_only: bool = True) -> StageEventRule:
    fields = {"target_stage": stage(target) if target else None, "forward_only": forward_only}
    return StageEventRule.objects.update_or_create(channel=channel, event=event, defaults=fields)[0]


def placed(company: Company, key: str) -> Company:
    Company.objects.filter(pk=company.pk).update(stage=stage(key))
    company.refresh_from_db()
    return company


def thread_of(company: Company, email: str = "ola@example-shop-4.test") -> SimpleNamespace:
    return SimpleNamespace(pk=5, subject_ref=f"leads.Company:{company.pk}", recipient_email=email)


def reply(intent: str = "", notify: str | None = None) -> SimpleNamespace:
    plan = {"notify": notify} if notify else {}
    return SimpleNamespace(pk=9, body_text="Thanks", intent=intent, plan_applied=plan)


def send_reply(company, intent: str = "", notify: str | None = None, capture=None) -> mock.MagicMock:
    with mock.patch("django_leads.services.alert_service.alert") as alert, capture(execute=True):
        communicator_signals.reply_received.send(sender=Thread, thread=thread_of(company), reply=reply(intent, notify))
    company.refresh_from_db()
    return alert


def send_sent(company, capture) -> None:
    message = SimpleNamespace(pk=3, thread=thread_of(company, "piotr@example-shop-4.test"))
    with capture(execute=True):
        communicator_signals.message_sent.send(sender=Message, message=message)
    company.refresh_from_db()


# --- lookup ----------------------------------------------------------------------------------------------------------


def test_reply_class_falls_back_to_reply(channel):
    assert stage_event_service.resolve(channel, "reply:interested").event == StageEvent.REPLY
    put_rule(channel, "reply:interested", "won")
    assert stage_event_service.resolve(channel, "reply:interested").target_stage.key == "won"


def test_no_row_means_no_rule(channel):
    StageEventRule.objects.all().delete()
    assert stage_event_service.resolve(channel, "reply:interested") is None
    assert stage_event_service.resolve(channel, "first_sent") is None


def test_first_sent_never_falls_back(channel):
    assert stage_event_service.resolve(channel, StageEvent.FIRST_SENT) is None


@pytest.mark.parametrize(
    ("current", "target", "forward_only", "moves"),
    [
        ("new", "contacted", True, True),
        ("contacted", "new", True, False),
        ("contacted", "new", False, True),
        ("contacted", "contacted", False, False),
        ("won", "replied", False, False),
        ("won", "unresponsive", True, False),
    ],
)
def test_may_move_guards(channel, current, target, forward_only, moves):
    assert stage_event_service.may_move(stage(current), stage(target), forward_only=forward_only) is moves


def test_lost_stage_is_never_left(channel, company):
    lost = Stage.objects.create(channel=channel, key="lost", label="Lost", order=25, kind="lost")
    Company.objects.filter(pk=company.pk).update(stage=lost)
    company.refresh_from_db()
    put_rule(channel, "reply", "replied", forward_only=False)
    assert stage_event_service.apply(company, "reply").stage.key == "lost"


def test_apply_moves_through_transition_stage(channel, company):
    put_rule(channel, StageEvent.FIRST_SENT, "contacted")
    moved = stage_event_service.apply(company, StageEvent.FIRST_SENT)
    assert moved.stage.key == "contacted"
    assert Activity.objects.get(company=company, kind=ActivityKind.STAGE).data == {"from": "new", "to": "contacted"}


# --- receivers: first send ---------------------------------------------------------------------------------------


def test_first_sent_moves_forward(channel, shop, django_capture_on_commit_callbacks):
    put_rule(channel, StageEvent.FIRST_SENT, "contacted")
    send_sent(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "contacted"


def test_first_sent_without_row_keeps_the_stage(channel, shop, django_capture_on_commit_callbacks):
    send_sent(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "new" and Activity.objects.filter(company=shop, kind=ActivityKind.SENT).count() == 1


def test_first_sent_never_moves_backwards(channel, shop, django_capture_on_commit_callbacks):
    put_rule(channel, StageEvent.FIRST_SENT, "contacted")
    placed(shop, "replied")
    send_sent(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "replied"


def test_only_the_first_send_applies(channel, shop, django_capture_on_commit_callbacks):
    put_rule(channel, StageEvent.FIRST_SENT, "contacted", forward_only=False)
    send_sent(shop, django_capture_on_commit_callbacks)
    placed(shop, "new")
    send_sent(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "new"


# --- receivers: replies by class ---------------------------------------------------------------------------------


def test_out_of_office_reply_keeps_the_stage(channel, shop, django_capture_on_commit_callbacks):
    put_rule(channel, "reply:out_of_office", None)
    send_reply(shop, "out_of_office", capture=django_capture_on_commit_callbacks)
    assert shop.stage.key == "new"
    assert Activity.objects.get(company=shop, kind=ActivityKind.REPLY).message == "reply received: out_of_office"


def test_interested_reply_moves(channel, shop, django_capture_on_commit_callbacks):
    put_rule(channel, "reply:interested", "won")
    send_reply(shop, "interested", capture=django_capture_on_commit_callbacks)
    assert shop.stage.key == "won"
    data = Activity.objects.get(company=shop, kind=ActivityKind.REPLY).data
    assert data == {"thread_id": 5, "reply_id": 9, "class": "interested"}


def test_reply_class_without_own_row_uses_reply(channel, shop, django_capture_on_commit_callbacks):
    send_reply(shop, "not_now", capture=django_capture_on_commit_callbacks)
    assert shop.stage.key == "replied"


def test_reply_without_any_row_keeps_the_stage(channel, shop, django_capture_on_commit_callbacks):
    StageEventRule.objects.all().delete()
    send_reply(shop, "interested", capture=django_capture_on_commit_callbacks)
    assert shop.stage.key == "new"


def test_reply_never_leaves_won(channel, shop, django_capture_on_commit_callbacks):
    placed(shop, "won")
    send_reply(shop, "interested", capture=django_capture_on_commit_callbacks)
    assert shop.stage.key == "won"


@pytest.mark.parametrize(("notify", "severity"), [(None, "high"), ("low", "low"), ("medium", "medium")])
def test_reply_alert_takes_the_plan_severity(shop, notify, severity, django_capture_on_commit_callbacks):
    alert = send_reply(shop, "not_now", notify, capture=django_capture_on_commit_callbacks)
    assert alert.call_args.kwargs["severity"] == severity


def test_reply_plan_none_sends_no_alert(shop, django_capture_on_commit_callbacks):
    assert not send_reply(shop, "not_now", "none", capture=django_capture_on_commit_callbacks).called


# --- receivers: finished sequence --------------------------------------------------------------------------------


def finish(company, capture) -> mock.MagicMock:
    with mock.patch("django_leads.tasks.rotate_thread.delay") as rotate, capture(execute=True):
        communicator_signals.sequence_finished.send(sender=Thread, thread=thread_of(company))
    company.refresh_from_db()
    return rotate


def test_sequence_finished_moves_then_rotates(channel, shop, django_capture_on_commit_callbacks):
    put_rule(channel, StageEvent.SEQUENCE_FINISHED, "contacted")
    rotate = finish(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "contacted"
    rotate.assert_called_once_with(5)


def test_sequence_finished_without_row_only_rotates(channel, shop, django_capture_on_commit_callbacks):
    rotate = finish(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "new"
    rotate.assert_called_once_with(5)


# --- API ---------------------------------------------------------------------------------------------------------


def test_effective_list_has_one_entry_per_event(channel, admin_api):
    results = admin_api.get(api_url("stage-events/")).json()["results"]
    assert [row["event"] for row in results] == StageEvent.values
    assert results[1] == {"event": "reply", "target_stage_key": "replied", "forward_only": False, "configured": True}
    assert results[0] == {"event": "first_sent", "target_stage_key": None, "forward_only": True, "configured": False}


def test_put_and_delete_an_event(channel, admin_api):
    url = api_url("stage-events/reply:out_of_office/")
    response = admin_api.put(url, {"target_stage_key": None, "forward_only": True}, format="json")
    assert response.status_code == 200 and response.json()["configured"] is True
    response = admin_api.put(url, {"target_stage_key": "contacted", "forward_only": False}, format="json")
    assert response.json() == {
        "event": "reply:out_of_office",
        "target_stage_key": "contacted",
        "forward_only": False,
        "configured": True,
    }
    assert admin_api.delete(url).status_code == 204
    assert not StageEventRule.objects.filter(event="reply:out_of_office").exists()


@pytest.mark.parametrize(
    ("event", "body", "status"),
    [
        ("nope", {"target_stage_key": None}, 404),
        ("reply", {"target_stage_key": "missing"}, 400),
        ("reply", {"forward_only": True}, 400),
        ("reply", {"target_stage_key": None, "stage": 1}, 400),
    ],
)
def test_put_rejects(channel, admin_api, event, body, status):
    assert admin_api.put(api_url(f"stage-events/{event}/"), body, format="json").status_code == status


def test_stage_requests_no_longer_take_on_reply(channel, admin_api):
    response = admin_api.post(api_url("stages/"), {"key": "x", "label": "X", "on_reply": True}, format="json")
    assert response.status_code == 400
    assert "on_reply" not in admin_api.get(api_url("stages/")).json()["results"][0]


def test_a_stage_event_target_cannot_be_deleted(channel, admin_api):
    response = admin_api.delete(api_url(f"stages/{stage('replied').pk}/"))
    assert response.status_code == 409 and Stage.objects.filter(key="replied").exists()


def test_upsert_refuses_a_stage_of_another_channel(channel, polish):
    other = type(channel).objects.create(idx="other")
    foreign = Stage.objects.create(channel=other, key="x", label="X")
    with pytest.raises(ValueError, match="another channel"):
        stage_event_service.upsert(channel, "reply", {"target_stage": foreign})


def test_upsert_refuses_unknown_fields(channel):
    with pytest.raises(ValueError, match="not editable"):
        stage_event_service.upsert(channel, "reply", {"channel": channel})


# --- data migration ----------------------------------------------------------------------------------------------


def migrate(target: str):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([("django_leads", target)])
    return executor.loader.project_state([("django_leads", target)]).apps


@pytest.mark.django_db(transaction=True)
def test_on_reply_becomes_stage_events_and_back(polish):
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes("django_leads")[0][1]
    try:
        old = migrate("0008_outbound_loop")
        channel = old.get_model("django_leads", "Channel").objects.create(idx="m")
        stages = old.get_model("django_leads", "Stage").objects
        stages.create(channel=channel, key="a", label="A", order=30, on_reply=True)
        first = stages.create(channel=channel, key="b", label="B", order=10, on_reply=True)
        new = migrate("0010_remove_stage_on_reply")
        rows = new.get_model("django_leads", "StageEventRule").objects.order_by("event")
        assert [(row.event, row.target_stage_id, row.forward_only) for row in rows] == [
            ("reply", first.pk, False),
            ("reply:out_of_office", None, True),
        ]
        old = migrate("0008_outbound_loop")
        flagged = old.get_model("django_leads", "Stage").objects.filter(on_reply=True)
        assert list(flagged.values_list("key", flat=True)) == ["b"]
    finally:
        migrate(latest)


def test_transition_stage_stays_the_only_stage_change(channel, company):
    with mock.patch.object(stage_service, "transition_stage", wraps=stage_service.transition_stage) as transition:
        put_rule(channel, StageEvent.SEQUENCE_FINISHED, "contacted")
        stage_event_service.apply(company, StageEvent.SEQUENCE_FINISHED)
    transition.assert_called_once()
