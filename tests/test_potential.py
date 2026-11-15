# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The potential dot (U2): rules and their order, return days, events, the daily sweep, thresholds, manual colour,
the list filter and the timeline."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.utils import timezone
from django_communicator import signals as communicator_signals
from django_communicator.models import Message, Thread

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Company, DealStage
from django_leads.services import activity_service, company_service, contact_service, deal_service, potential_service
from django_leads.tasks import refresh_potential
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

PIOTR = "piotr@example-shop-4.test"
OLA = "ola@example-shop-4.test"


@pytest.fixture
def pipeline(channel) -> dict[str, DealStage]:
    stages = [("lead", 0, "open"), ("won", 20, "won"), ("lost", 30, "lost")]
    return {
        key: DealStage.objects.create(channel=channel, key=key, label=key.title(), order=order, kind=kind)
        for key, order, kind in stages
    }


def aged(activity: Activity, days: int) -> Activity:
    Activity.objects.filter(pk=activity.pk).update(created_at=timezone.now() - timedelta(days=days))
    return activity


def sent(company: Company, days_ago: int = 0) -> Activity:
    return aged(activity_service.record(company, ActivityKind.SENT, "message sent"), days_ago)


def replied(company: Company, intent: str, days_ago: int = 0, **data) -> Activity:
    activity = activity_service.record(company, ActivityKind.REPLY, "reply", data={"class": intent, **data})
    return aged(activity, days_ago)


def dot(company: Company, today: date | None = None) -> tuple:
    company.refresh_from_db()
    verdict = potential_service.compute(company, today=today)
    return verdict.colour, verdict.reason


def stored(company: Company) -> tuple:
    company.refresh_from_db()
    return company.potential, company.potential_reason


def thread_of(company: Company, email: str = PIOTR) -> SimpleNamespace:
    return SimpleNamespace(pk=5, subject_ref=f"leads.Company:{company.pk}", recipient_email=email)


# --- rules -----------------------------------------------------------------------------------------------------------


def test_a_company_not_written_to_has_no_dot(shop):
    assert dot(shop) == ("", "")


def test_a_mail_without_a_reply_is_orange_until_silence_turns_it_red(shop):
    sent(shop, days_ago=119)
    assert dot(shop) == ("orange", "awaiting_reply")
    sent_row = shop.activities.get(kind=ActivityKind.SENT)
    aged(sent_row, 120)
    verdict = potential_service.compute(shop)
    assert (verdict.colour, verdict.reason) == ("red", "silence")
    assert verdict.return_on == timezone.localdate(sent_row_time(sent_row)) + timedelta(days=210)


def sent_row_time(row: Activity):
    row.refresh_from_db()
    return row.created_at


def test_silence_comes_back_orange_after_the_red_return_days(shop):
    sent(shop, days_ago=211)
    assert dot(shop) == ("orange", "came_back")


@pytest.mark.parametrize(
    ("intent", "colour"),
    [
        ("interested", "green"),
        ("not_now", "orange"),
        ("out_of_office", "orange"),
        ("unclear", "orange"),
        ("wrong_person", "orange"),
        ("not_interested", "red"),
        ("", "orange"),
    ],
)
def test_the_last_reply_gives_its_colour(shop, intent, colour):
    sent(shop, days_ago=2)
    replied(shop, intent, days_ago=1)
    assert dot(shop)[0] == colour


def test_not_interested_is_red_for_90_days_then_comes_back(shop):
    sent(shop, days_ago=100)
    replied(shop, "not_interested", days_ago=89)
    assert dot(shop) == ("red", "reply:not_interested")
    replied(shop, "not_interested", days_ago=91)
    Activity.objects.filter(company=shop, kind=ActivityKind.REPLY).update(created_at=timezone.now() - timedelta(91))
    assert dot(shop) == ("orange", "came_back")


def test_not_now_with_a_return_date_carries_that_date(shop):
    sent(shop, days_ago=2)
    replied(shop, "not_now", return_date="2027-02-15")
    verdict = potential_service.compute(shop)
    assert (verdict.colour, verdict.return_on) == ("orange", date(2027, 2, 15))


def test_a_reply_older_than_the_last_mail_no_longer_counts(shop):
    sent(shop, days_ago=10)
    replied(shop, "not_now", days_ago=9)
    sent(shop, days_ago=1)
    assert dot(shop) == ("orange", "awaiting_reply")


def test_an_opt_out_blocks_only_that_person(shop):
    sent(shop, days_ago=2)
    replied(shop, "unsubscribe", days_ago=1)
    contact_service.opt_out(shop.contacts.get(email=PIOTR), reason="test", actor="test")
    assert dot(shop) == ("orange", "awaiting_reply")  # Ola may still be mailed


def test_nobody_left_to_mail_is_looking_for_another_person(shop):
    sent(shop, days_ago=2)
    replied(shop, "wrong_person", days_ago=1)
    shop.contacts.filter(email=PIOTR).update(wrong_person_at=timezone.now())
    assert dot(shop) == ("orange", "reply:wrong_person")
    shop.contacts.filter(email=OLA).update(opt_out_at=timezone.now())
    assert dot(shop) == ("orange", "looking_for_person")


def test_a_new_contact_ends_looking_for_another_person_at_once(shop):
    sent(shop, days_ago=2)
    replied(shop, "wrong_person", days_ago=1)
    shop.contacts.update(wrong_person_at=timezone.now())
    potential_service.refresh(shop)
    assert stored(shop) == ("orange", "looking_for_person")
    contact_service.create_contact(shop, {"email": "ewa@example-shop-4.test", "source": "manual"}, actor="test")
    assert stored(shop) == ("orange", "reply:wrong_person")


def test_a_skipped_draft_is_orange_and_comes_back_after_30_days(shop):
    potential_service.record_skipped(shop, message_id=1)
    verdict = potential_service.compute(shop)
    assert (verdict.colour, verdict.reason) == ("orange", "draft_skipped")
    assert verdict.return_on == timezone.localdate() + timedelta(days=30)
    Activity.objects.filter(company=shop, kind=ActivityKind.SKIPPED).update(created_at=timezone.now() - timedelta(30))
    assert dot(shop) == ("orange", "came_back")


def test_order_do_not_contact_beats_a_deal_and_a_deal_beats_a_reply(shop, pipeline):
    sent(shop, days_ago=2)
    replied(shop, "not_interested", days_ago=1)
    deal = deal_service.create(shop, {"title": "Audit"}, owner=None, actor="test")
    assert dot(shop) == ("green", "deal_open")
    deal_service.transition(deal, pipeline["lost"], actor="test", lost_reason="price")
    assert dot(shop) == ("red", "deals_lost")
    company_service.set_do_not_contact(shop, True, actor="test")
    assert dot(shop) == ("red", "do_not_contact")


def test_a_won_deal_is_green(shop, pipeline):
    deal = deal_service.create(shop, {"title": "Audit"}, owner=None, actor="test")
    deal_service.transition(deal, pipeline["won"], actor="test")
    assert stored(shop) == ("green", "deal_won")


def test_a_spam_complaint_is_red_forever(shop):
    sent(shop, days_ago=400)
    activity_service.record(shop, ActivityKind.NOTE, "address invalid: hard bounce", data={"reason": "complaint"})
    verdict = potential_service.compute(shop)
    assert (verdict.colour, verdict.reason, verdict.return_on) == ("red", "complaint", None)


# --- events ----------------------------------------------------------------------------------------------------------


def test_events_store_the_colour_and_write_the_timeline(shop, django_capture_on_commit_callbacks):
    message = SimpleNamespace(pk=3, thread=thread_of(shop))
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.message_sent.send(sender=Message, message=message)
    assert stored(shop) == ("orange", "awaiting_reply")
    reply = SimpleNamespace(
        pk=9, intent="interested", needs_review=False, plan_applied={"notify": "none"}, body_text="",
        received_at=timezone.now(), intent_return_date=None,
    )  # fmt: skip
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.reply_received.send(sender=Thread, thread=thread_of(shop), reply=reply)
    assert stored(shop) == ("green", "reply:interested")
    entries = list(shop.activities.filter(kind=ActivityKind.POTENTIAL).order_by("pk").values_list("data", flat=True))
    assert [(row["from"], row["to"]) for row in entries] == [("", "orange"), ("orange", "green")]


def test_message_skipped_signal_makes_the_company_orange(shop, django_capture_on_commit_callbacks):
    message = SimpleNamespace(pk=4, thread=thread_of(shop))
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.message_skipped.send(
            sender=Message, message=message, subject_ref=f"leads.Company:{shop.pk}"
        )
    assert stored(shop) == ("orange", "draft_skipped")
    assert shop.activities.filter(kind=ActivityKind.SKIPPED, data__event="draft_skipped").count() == 1


def test_the_skip_entry_names_the_user_who_clicked_skip(shop, django_capture_on_commit_callbacks):
    clicked = SimpleNamespace(pk=5, thread=thread_of(shop), reviewed_by=SimpleNamespace(username="editor"))
    unknown = SimpleNamespace(pk=6, thread=thread_of(shop), reviewed_by=None)
    with django_capture_on_commit_callbacks(execute=True):
        for message in (clicked, unknown):
            communicator_signals.message_skipped.send(
                sender=Message, message=message, subject_ref=f"leads.Company:{shop.pk}"
            )
    entries = shop.activities.filter(kind=ActivityKind.SKIPPED, data__event="draft_skipped").order_by("pk")
    assert list(entries.values_list("actor", flat=True)) == ["editor", "reviewer"]


def test_no_change_writes_no_second_entry(shop):
    sent(shop)
    potential_service.refresh(shop)
    potential_service.refresh(shop)
    assert shop.activities.filter(kind=ActivityKind.POTENTIAL).count() == 1


# --- daily sweep -----------------------------------------------------------------------------------------------------


def test_the_daily_sweep_turns_silence_red(shop):
    sent(shop)
    potential_service.refresh(shop)
    Activity.objects.filter(company=shop, kind=ActivityKind.SENT).update(created_at=timezone.now() - timedelta(130))
    assert refresh_potential.delay().get() == 1
    assert stored(shop) == ("red", "silence")


@pytest.mark.skipif(not apps.is_installed("django_todo"), reason="django_todo not installed")
def test_a_come_back_opens_a_task_for_today(shop):
    from django_todo.models import Task

    sent(shop)
    potential_service.refresh(shop)
    Activity.objects.filter(company=shop, kind=ActivityKind.SENT).update(created_at=timezone.now() - timedelta(220))
    potential_service.sweep()
    potential_service.sweep()
    assert stored(shop) == ("orange", "came_back")
    tasks = Task.objects.filter(dedup_key=f"potential:{shop.pk}")
    assert list(tasks.values_list("title", flat=True)) == ["Come back to Example Shop 4"]


# --- manual ----------------------------------------------------------------------------------------------------------


def test_manual_colour_needs_a_comment_and_holds_until_a_reply(admin_api, shop):
    url = api_url(f"companies/{shop.pk}/potential/")
    assert admin_api.post(url, {"colour": "green", "comment": "  "}, "json").status_code == 400
    response = admin_api.post(url, {"colour": "green", "comment": "Met at a fair"}, "json")
    body = response.json()
    assert (body["potential"], body["potential_manual"], body["potential_comment"]) == ("green", True, "Met at a fair")
    sent(shop)
    potential_service.refresh(shop)
    assert stored(shop) == ("green", "manual")
    entry = shop.activities.filter(kind=ActivityKind.POTENTIAL).first()
    assert (entry.actor, entry.data["comment"]) == ("operator", "Met at a fair")
    potential_service.on_event(shop, reset_manual=True)  # a new reply
    assert stored(shop) == ("orange", "awaiting_reply")


def test_back_to_the_rules(admin_api, shop):
    potential_service.set_manual(shop, "red", "Closed for the season", return_on=None, actor="x")
    response = admin_api.delete(api_url(f"companies/{shop.pk}/potential/"))
    assert (response.json()["potential"], response.json()["potential_manual"]) == ("", False)


def test_a_legal_block_allows_only_red(admin_api, shop):
    company_service.set_do_not_contact(shop, True, actor="test")
    url = api_url(f"companies/{shop.pk}/potential/")
    response = admin_api.post(url, {"colour": "green", "comment": "please"}, "json")
    assert (response.status_code, response.json()["error"]) == (409, "LEGAL_BLOCK")


def test_a_manual_return_day_ends_the_manual_colour(shop):
    sent(shop)
    potential_service.set_manual(shop, "red", "Budget in spring", return_on=timezone.localdate(), actor="x")
    potential_service.sweep()
    assert stored(shop) == ("orange", "awaiting_reply")


# --- list filter and thresholds --------------------------------------------------------------------------------------


def test_the_list_filters_by_potential(admin_api, shop, company):
    sent(shop)
    potential_service.refresh(shop)

    def rows(value: str) -> list[int]:
        return [row["id"] for row in admin_api.get(api_url(f"companies/?potential={value}")).json()["results"]]

    assert (rows("orange"), rows("none"), rows("green")) == ([shop.pk], [company.pk], [])
    assert admin_api.get(api_url("companies/?potential=blue")).status_code == 400


def test_thresholds_are_settings_and_a_change_recomputes(admin_api, shop, django_capture_on_commit_callbacks):
    sent(shop, days_ago=40)
    potential_service.refresh(shop)
    assert stored(shop) == ("orange", "awaiting_reply")
    body = admin_api.get(api_url("channel/")).json()["potential"]
    assert (body["silence_days"], body["skip_return_days"], body["red_return_days"]) == (120, 30, 90)
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_api.patch(
            api_url("channel/"), {"potential": {"silence_days": 30, "reply_colours": {"not_now": "red"}}}, "json"
        )
    assert response.json()["potential"]["silence_days"] == 30
    assert response.json()["potential"]["reply_colours"]["not_now"] == "red"
    assert stored(shop) == ("red", "silence")
    assert admin_api.patch(api_url("channel/"), {"potential": {"silence_days": 0}}, "json").status_code == 400
