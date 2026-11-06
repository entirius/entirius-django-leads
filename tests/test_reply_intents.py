# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""T4 / S10 in leads: a classified reply shows its intent in the timeline; unclassified replies read as before."""

from datetime import date
from types import SimpleNamespace
from unittest import mock

from django_communicator import signals as communicator_signals
from django_communicator.models import Thread

from django_leads.enums import ActivityKind
from django_leads.models import Activity


def send_reply(shop, django_capture_on_commit_callbacks, **reply_fields):
    thread = SimpleNamespace(pk=5, subject_ref=f"leads.Company:{shop.pk}", recipient_email="ola@example-shop-4.test")
    reply = SimpleNamespace(pk=9, body_text="Hello", **reply_fields)
    with mock.patch("django_leads.services.alert_service.alert"):
        with django_capture_on_commit_callbacks(execute=True):
            communicator_signals.reply_received.send(sender=Thread, thread=thread, reply=reply)
    return Activity.objects.get(company=shop, kind=ActivityKind.REPLY)


def test_classified_reply_shows_intent_in_the_timeline(shop, django_capture_on_commit_callbacks):
    activity = send_reply(
        shop,
        django_capture_on_commit_callbacks,
        intent="out_of_office",
        intent_confidence=0.9,
        intent_return_date=date(2026, 12, 1),
    )
    assert activity.message == "reply received: out_of_office"
    assert activity.data == {
        "thread_id": 5,
        "reply_id": 9,
        "intent": "out_of_office",
        "intent_confidence": 0.9,
        "intent_return_date": "2026-12-01",
    }


def test_unclassified_reply_reads_as_before(shop, django_capture_on_commit_callbacks):
    for fields in ({}, {"intent": ""}):
        Activity.objects.filter(company=shop, kind=ActivityKind.REPLY).delete()
        activity = send_reply(shop, django_capture_on_commit_callbacks, **fields)
        assert activity.message == "reply received" and activity.data == {"thread_id": 5, "reply_id": 9}


# --- end to end: a real communicator ingest over the fake toolbox -----------------------------------------------------


def test_e2e_classified_reply_reaches_the_timeline_and_the_stage(shop, fake_intent, inbox):
    got = inbox("Dzień dobry, chętnie porozmawiamy. Proszę o cennik.")

    assert (got.reply.kind, got.reply.intent) == ("reply", "interested")
    activity = Activity.objects.get(company=shop, kind=ActivityKind.REPLY)
    assert activity.message == "reply received: interested"
    assert activity.data["intent"] == "interested" and activity.data["intent_confidence"] == 0.9
    assert shop.stage.key == "replied"


def test_e2e_toolbox_off_reply_reads_as_before(shop, inbox, settings):
    settings.AI_TOOLBOX_ENABLED = False
    got = inbox("Dzień dobry, chętnie porozmawiamy.")

    assert got.reply.intent == "" and shop.stage.key == "replied"
    assert Activity.objects.get(company=shop, kind=ActivityKind.REPLY).message == "reply received"
