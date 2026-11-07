# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""T4 / S12 in leads: the golden set of sample Polish and English replies (`tests/fixtures/reply_golden.json`, a copy of
the communicator one — keep them equal) through a REAL communicator ingest over the FAKE toolbox client, asserting what
reaches leads (timeline, stage, contact flag, alert) with the default routes, and with routes set through the API.

Nothing here says how well a real model classifies; see communicator's opt-in `test_real_toolbox_matches_the_golden_set`."""

import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.utils import timezone

from django_leads.enums import ActivityKind, StageKind
from django_leads.models import Activity, Contact, Stage

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "reply_golden.json").read_text())
IDS = [f"{g['intent']}-{g['lang']}-{n}" for n, g in enumerate(GOLDEN)]
PIOTR = "piotr@example-shop-4.test"
ALERT = "django_leads.services.alert_service.alert"

# intent -> (stage key after the reply, leads alert severity, contact flagged) with the default routes; the company
# starts in `new`. A suspected opt-out (`unsubscribe`) is not a reply yet: leads hears nothing until a person decides.
EXPECTED = {
    "interested": ("replied", "high", False),
    "not_now": ("replied", "medium", False),
    "not_interested": ("replied", "medium", False),
    "out_of_office": ("new", "low", False),
    "wrong_person": ("replied", "medium", True),
    "unsubscribe": ("new", None, False),
    "unclear": ("replied", "high", False),
}
UNCLASSIFIED = ("", "replied", "high", False)


def text_of(entry: dict) -> str:
    body = entry["body"]
    for days in (10, 14, 21, 30):
        body = body.replace(f"{{in{days}}}", (timezone.now() + timedelta(days=days)).date().isoformat())
    return body


@pytest.mark.parametrize("entry", GOLDEN, ids=IDS)
def test_golden_reply_reaches_leads_with_the_default_routes(shop, fake_intent, inbox, entry):
    if entry.get("classified") is not False:
        intent = entry["intent"]
        stage, severity, flagged = EXPECTED[intent]
    else:
        intent, stage, severity, flagged = UNCLASSIFIED

    with mock.patch(ALERT) as alert:
        got = inbox(text_of(entry), subject=entry["subject"])

    assert got.reply.intent == intent, entry["body"]
    assert shop.stage.key == stage
    assert bool(Contact.objects.get(company=shop, email=PIOTR).wrong_person_at) is flagged
    replies = Activity.objects.filter(company=shop, kind=ActivityKind.REPLY)
    if severity is None:
        assert not replies.exists() and alert.call_count == 0
        assert got.reply.kind == "suspected_optout"
        return
    assert [r.message for r in replies] == [f"reply received: {intent}" if intent else "reply received"]
    assert alert.call_args.kwargs["severity"] == severity
    assert got.reply.needs_review is (intent in ("wrong_person", "unclear"))


def put_routes(admin_api, routes: dict) -> None:
    for intent, body in routes.items():
        response = admin_api.put(f"/api/leads/v2/admin/default-europe/reply-routes/{intent}/", body, format="json")
        assert response.status_code == 200, response.content


def test_not_interested_to_lost_and_the_sequence_stops(shop, channel, fake_intent, inbox, admin_api):
    lost = Stage.objects.create(
        channel=channel, key="lost", label="Lost", order=50, kind=StageKind.LOST, is_terminal=True
    )
    put_routes(admin_api, {"not_interested": {"action": "stage", "stage_id": lost.pk}})

    got = inbox("Dziękuję, nie jesteśmy zainteresowani.")

    assert shop.stage.key == "lost"
    assert got.state.stop_reason == "replied" and got.thread.status == "replied"


def test_not_now_pauses_the_sequence_and_the_company_is_replied(shop, fake_intent, inbox):
    got = inbox("Nie teraz, wróćmy do tematu w przyszłym kwartale.")
    assert got.state.stop_reason == "paused" and shop.stage.key == "replied"
    assert got.reply.remind_at is not None


def test_out_of_office_pauses_and_leaves_the_stage(shop, fake_intent, inbox):
    back = (timezone.now() + timedelta(days=9)).date().isoformat()
    got = inbox(f"Jestem poza biurem do {back}.")
    assert got.state.stop_reason == "paused" and shop.stage.key == "new" and got.reply.resume_at is not None


def test_wrong_person_stops_outreach_to_that_contact_only(shop, fake_intent, inbox):
    from django_leads.services import recipient_service

    inbox("To nie ta osoba. Proszę skontaktować się z działem e-commerce.")
    assert [c.email for c in recipient_service.candidates(shop)] == ["ola@example-shop-4.test"]
    assert Activity.objects.filter(company=shop, message="contact flagged: wrong person").count() == 1


def test_silenced_in_communicator_means_no_leads_alert_but_the_stage_still_moves(shop, fake_intent, inbox):
    from django_communicator.services import reply_action_service

    reply_action_service.upsert(inbox.channel, "interested", {"notify": "none"})
    with mock.patch(ALERT) as alert:
        got = inbox("Chętnie, proszę o cennik.")
    assert alert.call_count == 0 and shop.stage.key == "replied" and got.reply.intent == "interested"


def test_toolbox_off_every_reply_reads_as_before(shop, inbox, settings):
    settings.AI_TOOLBOX_ENABLED = False
    with mock.patch(ALERT) as alert:
        got = inbox("Chętnie, proszę o cennik.")
    assert got.reply.intent == "" and shop.stage.key == "replied"
    assert alert.call_args.kwargs == {
        "severity": "high",
        "title": "Reply from Example Shop 4",
        "body": got.reply.body_text[:200],
    }
    assert not Contact.objects.filter(wrong_person_at__isnull=False).exists()


def test_toolbox_off_a_refusal_is_still_a_suspected_optout_for_a_person(shop, inbox, settings):
    settings.AI_TOOLBOX_ENABLED = False  # the F4 phrase list decides, as before T4
    with mock.patch(ALERT) as alert:
        got = inbox("Nie jestem zainteresowany, dziękuję.")
    assert (got.reply.kind, got.reply.intent) == ("suspected_optout", "")
    assert got.state.stop_reason == "paused" and shop.stage.key == "new" and alert.call_count == 0
