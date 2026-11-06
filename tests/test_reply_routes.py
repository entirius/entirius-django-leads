# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""T4 / S11 in leads: the stage and contact handling per reply intent class (defaults, admin API, application), and the
reminder entry in the timeline. End to end tests (a real communicator ingest over the FAKE toolbox) are in
`test_reply_golden.py`."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django_communicator import signals as communicator_signals
from django_communicator.models import Thread
from rest_framework.test import APIClient

from django_leads.enums import ActivityKind, ReplyIntent, StageKind
from django_leads.models import Activity, Channel, Contact, ReplyIntentRoute, Stage
from django_leads.services import recipient_service, reply_route_service

URL = "/api/leads/v2/admin/default-europe/reply-routes/"
PIOTR = "piotr@example-shop-4.test"


@pytest.fixture
def lost(channel) -> Stage:
    return Stage.objects.create(
        channel=channel, key="lost", label="Lost", order=50, kind=StageKind.LOST, is_terminal=True
    )


def reply(shop, django_capture_on_commit_callbacks, intent="", plan=None, email=PIOTR, **extra):
    thread = SimpleNamespace(pk=5, subject_ref=f"leads.Company:{shop.pk}", recipient_email=email)
    fields = {"intent": intent, "action_applied": plan or {}, "body_text": "Hello"} | extra
    with mock.patch("django_leads.services.alert_service.alert") as alert:
        with django_capture_on_commit_callbacks(execute=True):
            communicator_signals.reply_received.send(
                sender=Thread, thread=thread, reply=SimpleNamespace(pk=9, **fields)
            )
    shop.refresh_from_db()
    return alert


# --- defaults and the admin API ---------------------------------------------------------------------------------------


def test_defaults_keep_the_old_behaviour_except_out_of_office_and_wrong_person(channel, admin_api):
    rows = admin_api.get(URL).json()["results"]
    assert [row["intent"] for row in rows] == ReplyIntent.values and all(row["is_default"] for row in rows)
    by_intent = {row["intent"]: row for row in rows}
    assert {i: r["action"] for i, r in by_intent.items()} == {
        "interested": "reply_stage",
        "not_now": "reply_stage",
        "not_interested": "reply_stage",
        "out_of_office": "none",
        "wrong_person": "reply_stage",
        "unsubscribe": "reply_stage",
        "unclear": "reply_stage",
    }
    assert [i for i, r in by_intent.items() if r["flag_contact"]] == ["wrong_person"]


def test_put_a_stage_route_and_delete_it(channel, admin_api, lost):
    saved = admin_api.put(f"{URL}not_interested/", {"action": "stage", "stage_id": lost.pk}, format="json")
    assert saved.status_code == 200
    assert saved.json() == {
        "intent": "not_interested",
        "action": "stage",
        "stage_id": lost.pk,
        "stage_key": "lost",
        "flag_contact": False,
        "is_default": False,
    }
    rows = {r["intent"]: r for r in admin_api.get(URL).json()["results"]}
    assert rows["not_interested"]["stage_key"] == "lost" and rows["interested"]["is_default"]
    assert admin_api.put(f"{URL}not_interested/", {"action": "none"}, format="json").json()["stage_id"] is None
    assert ReplyIntentRoute.objects.count() == 1
    assert admin_api.delete(f"{URL}not_interested/").json()["is_default"]
    assert admin_api.delete(f"{URL}not_interested/").status_code == 200
    assert not ReplyIntentRoute.objects.exists()


def test_put_refuses_nonsense(channel, admin_api, lost):
    other = Channel.objects.create(idx="other", name="Other", default_language=channel.default_language)
    foreign = Stage.objects.create(channel=other, key="x", label="X", order=0)
    for intent, body in [
        ("not_interested", {"action": "stage"}),
        ("not_interested", {"action": "none", "stage_id": lost.pk}),
        ("not_interested", {"action": "stage", "stage_id": foreign.pk}),
        ("not_interested", {"action": "stage", "stage_id": 99999}),
        ("not_interested", {"action": "teleport"}),
        ("not_interested", {"surprise": 1}),
    ]:
        assert admin_api.put(f"{URL}{intent}/", body, format="json").status_code == 400, body
    assert not ReplyIntentRoute.objects.exists()


def test_unknown_intent_and_channel_are_404_and_only_staff_may_use_it(channel, admin_api, customer_api):
    assert admin_api.put(f"{URL}banana/", {}, format="json").status_code == 404
    assert admin_api.delete(f"{URL}banana/").status_code == 404
    assert admin_api.get("/api/leads/v2/admin/nope/reply-routes/").status_code == 404
    assert customer_api.get(URL).status_code == 403
    assert customer_api.put(f"{URL}interested/", {}, format="json").status_code == 403
    assert APIClient().get(URL).status_code == 401


def test_route_is_per_channel(channel, lost):
    other = Channel.objects.create(idx="other", name="Other", default_language=channel.default_language)
    reply_route_service.upsert(channel, "interested", action="stage", stage_id=lost.pk, flag_contact=False)
    assert reply_route_service.route_for(channel, "interested").stage_key == "lost"
    assert reply_route_service.route_for(other, "interested").is_default


def test_deleting_the_stage_drops_its_route(channel, lost):
    reply_route_service.upsert(channel, "interested", action="stage", stage_id=lost.pk, flag_contact=False)
    lost.delete()
    assert reply_route_service.route_for(channel, "interested").is_default


# --- application ------------------------------------------------------------------------------------------------------


def test_unclassified_reply_is_the_old_path(shop, django_capture_on_commit_callbacks):
    alert = reply(shop, django_capture_on_commit_callbacks)
    assert shop.stage.key == "replied"
    assert (
        alert.call_args.kwargs["severity"] == "high" and alert.call_args.kwargs["title"] == "Reply from Example Shop 4"
    )


def test_alert_follows_the_communicator_plan(shop, django_capture_on_commit_callbacks):
    alert = reply(shop, django_capture_on_commit_callbacks, intent="not_now", plan={"notify": "medium"})
    assert alert.call_args.kwargs["severity"] == "medium"
    assert alert.call_args.kwargs["title"] == "Reply from Example Shop 4 (not_now)"
    assert shop.stage.key == "replied"


def test_silent_plan_sends_no_alert(shop, django_capture_on_commit_callbacks):
    alert = reply(shop, django_capture_on_commit_callbacks, intent="interested", plan={"notify": "none"})
    assert alert.call_count == 0 and shop.stage.key == "replied"


def test_out_of_office_leaves_the_stage(shop, django_capture_on_commit_callbacks):
    alert = reply(shop, django_capture_on_commit_callbacks, intent="out_of_office", plan={"notify": "low"})
    assert shop.stage.key == "new" and alert.call_args.kwargs["severity"] == "low"
    assert Activity.objects.get(company=shop, kind=ActivityKind.REPLY).message == "reply received: out_of_office"


def test_stage_route_moves_to_the_configured_stage(shop, channel, lost, django_capture_on_commit_callbacks):
    reply_route_service.upsert(channel, "not_interested", action="stage", stage_id=lost.pk, flag_contact=False)
    reply(shop, django_capture_on_commit_callbacks, intent="not_interested")
    assert shop.stage.key == "lost"
    assert list(Activity.objects.filter(company=shop, kind=ActivityKind.STAGE).values_list("message", flat=True)) == [
        "stage new -> lost"
    ]


def test_stage_route_never_leaves_a_terminal_stage(shop, channel, lost, django_capture_on_commit_callbacks):
    reply_route_service.upsert(channel, "not_interested", action="stage", stage_id=lost.pk, flag_contact=False)
    type(shop).objects.filter(pk=shop.pk).update(stage=Stage.objects.get(channel=channel, key="won"))
    reply(shop, django_capture_on_commit_callbacks, intent="not_interested")
    assert shop.stage.key == "won"


def test_none_route_keeps_the_stage(shop, channel, django_capture_on_commit_callbacks):
    reply_route_service.upsert(channel, "interested", action="none", stage_id=None, flag_contact=False)
    reply(shop, django_capture_on_commit_callbacks, intent="interested")
    assert shop.stage.key == "new"


def test_wrong_person_flags_the_contact_and_blocks_outreach(shop, django_capture_on_commit_callbacks):
    reply(shop, django_capture_on_commit_callbacks, intent="wrong_person")
    contact = Contact.objects.get(company=shop, email=PIOTR)
    assert contact.wrong_person_at is not None
    assert [c.email for c in recipient_service.candidates(shop)] == ["ola@example-shop-4.test"]
    contact.company = shop
    assert recipient_service.block_reason(contact) == "wrong_person"
    notes = Activity.objects.filter(company=shop, kind=ActivityKind.NOTE)
    assert [n.message for n in notes] == ["contact flagged: wrong person"] and notes[0].contact == contact


def test_flag_is_set_once_and_only_for_the_replying_contact(shop, django_capture_on_commit_callbacks):
    reply(shop, django_capture_on_commit_callbacks, intent="wrong_person")
    first = Contact.objects.get(company=shop, email=PIOTR).wrong_person_at
    reply(shop, django_capture_on_commit_callbacks, intent="wrong_person")
    assert Contact.objects.get(company=shop, email=PIOTR).wrong_person_at == first
    assert Activity.objects.filter(company=shop, kind=ActivityKind.NOTE).count() == 1
    assert not Contact.objects.get(company=shop, email="ola@example-shop-4.test").wrong_person_at


def test_wrong_person_route_can_be_switched_off(shop, channel, django_capture_on_commit_callbacks):
    reply_route_service.upsert(channel, "wrong_person", action="reply_stage", stage_id=None, flag_contact=False)
    reply(shop, django_capture_on_commit_callbacks, intent="wrong_person")
    assert not Contact.objects.get(company=shop, email=PIOTR).wrong_person_at


def test_flag_on_a_reply_from_an_unknown_address_is_harmless(shop, django_capture_on_commit_callbacks):
    reply(shop, django_capture_on_commit_callbacks, intent="wrong_person", email="stranger@example-shop-4.test")
    assert not Contact.objects.filter(company=shop, wrong_person_at__isnull=False).exists()
    assert shop.stage.key == "replied"


def test_contact_api_shows_the_flag(shop, admin_api, django_capture_on_commit_callbacks):
    reply(shop, django_capture_on_commit_callbacks, intent="wrong_person")
    contact = Contact.objects.get(company=shop, email=PIOTR)
    body = admin_api.get(f"/api/leads/v2/admin/default-europe/contacts/{contact.pk}/")
    assert body.status_code == 200 and body.json()["wrong_person_at"] is not None


def test_reminder_becomes_a_timeline_note(shop, django_capture_on_commit_callbacks):
    thread = SimpleNamespace(pk=5, subject_ref=f"leads.Company:{shop.pk}", recipient_email=PIOTR)
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.reply_followup_due.send(
            sender=None, thread=thread, reply=SimpleNamespace(pk=9, intent="not_now"), kind="reminder"
        )
    note = Activity.objects.get(company=shop, kind=ActivityKind.NOTE)
    assert note.message == "follow-up reminder: not_now" and note.data["reply_id"] == 9
    assert note.contact.email == PIOTR


def test_reminder_for_another_module_is_ignored(shop, django_capture_on_commit_callbacks):
    thread = SimpleNamespace(pk=5, subject_ref="pim.Product:1", recipient_email=PIOTR)
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.reply_followup_due.send(
            sender=None, thread=thread, reply=SimpleNamespace(pk=9, intent=""), kind="reminder"
        )
    assert not Activity.objects.exists()
