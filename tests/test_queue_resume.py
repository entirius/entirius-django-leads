# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Q16 (the "what to do today" queue and the daily digest) and Q20 (the "not now" loop)."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone
from django_communicator.enums import MessageStatus
from django_communicator.models import Message, Thread
from django_utils.toolbox import ToolboxDisabledError

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind
from django_leads.models import Activity, Contact
from django_leads.services import digest_service, dnc_service, queue_service, resume_service
from django_leads.services.verification_service import DnsAnswer
from django_leads.signals import communicator_receivers
from django_leads.tasks import resume_contacts, send_digest
from tests.conftest import api_url
from tests.reply_flow import NOTIFY

pytestmark = pytest.mark.django_db

PIOTR = "piotr@example-shop-4.test"


@pytest.fixture
def communicate():
    message = SimpleNamespace(pk=7, status="review_required")
    with (
        mock.patch("django_leads.services.outreach_service.communicate", return_value=message) as patched,
        mock.patch("django_leads.services.outreach_service.render_legal_footer", return_value="footer"),
        mock.patch("django_leads.services.outreach_service.resolve_clause_set"),
    ):
        yield patched


@pytest.fixture
def piotr(shop) -> Contact:
    contact = Contact.objects.get(company=shop, email=PIOTR)
    contact.company = shop
    return contact


@pytest.fixture
def earlier_draft(shop, piotr):
    Activity.objects.create(
        company=shop, contact=piotr, kind=ActivityKind.DRAFT, message="draft", data={"template_key": "lead.cold.b2b"}
    )


def fake_reply(intent: str, *, return_date: date | None = None, resume_at=None):
    return SimpleNamespace(
        pk=1, intent=intent, intent_confidence=0.9, intent_return_date=return_date, resume_at=resume_at,
        action_applied={"notify": "none"}, body_text="",
    )  # fmt: skip


def due(contact: Contact, days: int = 0) -> None:
    Contact.objects.filter(pk=contact.pk).update(
        resume_at=timezone.now() - timedelta(days=days, minutes=1), resume_intent="not_now"
    )


# --- Q20 -------------------------------------------------------------------------------------------------------------


def test_not_now_reply_plans_the_default_window(shop, piotr):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    piotr.refresh_from_db()
    assert piotr.resume_intent == "not_now"
    assert abs(piotr.resume_at - (timezone.now() + timedelta(days=30))) < timedelta(minutes=1)
    assert (
        Activity.objects.filter(company=shop, kind=ActivityKind.RESUME, message__startswith="resume planned").count()
        == 1
    )


def test_window_is_configurable_per_channel_and_class(shop, piotr, channel):
    channel.resume_days = {"not_now": 45}
    channel.save()
    shop.refresh_from_db()
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    piotr.refresh_from_db()
    assert abs(piotr.resume_at - (timezone.now() + timedelta(days=45))) < timedelta(minutes=1)
    resume_service.on_reply(shop, piotr, fake_reply("out_of_office"))  # class without an entry: module default 7
    piotr.refresh_from_db()
    assert abs(piotr.resume_at - (timezone.now() + timedelta(days=7))) < timedelta(minutes=1)


def test_out_of_office_with_a_date_resumes_the_day_after(shop, piotr):
    back = timezone.now().date() + timedelta(days=12)
    resume_service.on_reply(shop, piotr, fake_reply("out_of_office", return_date=back))
    piotr.refresh_from_db()
    assert piotr.resume_at.date() == back + timedelta(days=1) and piotr.resume_at.hour == 0
    planned = Activity.objects.get(company=shop, kind=ActivityKind.RESUME)
    assert planned.data["from_return_date"] is True


def test_a_return_date_in_the_past_falls_back_to_the_window(shop, piotr):
    resume_service.on_reply(shop, piotr, fake_reply("out_of_office", return_date=date(2020, 1, 1)))
    piotr.refresh_from_db()
    assert piotr.resume_at > timezone.now() + timedelta(days=6)


def test_communicator_resumes_a_dated_out_of_office_so_leads_plans_nothing(shop, piotr):
    reply = fake_reply("out_of_office", return_date=date.today() + timedelta(days=3), resume_at=timezone.now())
    resume_service.on_reply(shop, piotr, reply)
    piotr.refresh_from_db()
    assert piotr.resume_at is None


@pytest.mark.parametrize("intent", ["not_interested", "unsubscribe", "interested", "unclear", ""])
def test_a_newer_reply_cancels_the_plan(shop, piotr, intent):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    resume_service.on_reply(shop, piotr, fake_reply(intent))
    piotr.refresh_from_db()
    assert piotr.resume_at is None
    assert Activity.objects.filter(company=shop, message="resume cancelled: newer reply").count() == 1


def test_real_reply_flow_plans_the_resume(shop, fake_intent, inbox):
    got = inbox("Nie teraz, wróćmy do tematu w przyszłym kwartale.")
    contact = Contact.objects.get(company=got.company, email=PIOTR)
    assert contact.resume_intent == "not_now" and contact.resume_at is not None


def test_receiver_plans_through_record_reply(shop, piotr):
    thread = SimpleNamespace(pk=3, subject_ref=f"leads.Company:{shop.pk}", recipient_email=PIOTR)
    with mock.patch(NOTIFY):
        communicator_receivers.record_reply(thread, fake_reply("not_now"))
    piotr.refresh_from_db()
    assert piotr.resume_at is not None


def test_due_contact_gets_a_draft_through_the_gate(shop, piotr, earlier_draft, communicate):
    due(piotr)
    assert resume_service.run_due() == {"resumed": 1, "cancelled": 0, "waiting": 0}
    piotr.refresh_from_db()
    assert piotr.resume_at is None and communicate.call_args.kwargs["template_key"] == "lead.cold.b2b"
    assert Activity.objects.filter(company=shop, message="resumed: draft requested", kind=ActivityKind.RESUME).exists()


def test_a_contact_not_yet_due_is_left_alone(shop, piotr, earlier_draft, communicate):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    assert resume_service.run_due() == {"resumed": 0, "cancelled": 0, "waiting": 0}
    communicate.assert_not_called()


def test_never_resumes_after_an_unsubscribe(shop, piotr, earlier_draft, communicate):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    dnc_service.add(email=PIOTR, reason="opt-out confirmed", actor="test")
    due(piotr)
    assert resume_service.run_due()["cancelled"] == 1
    communicate.assert_not_called()
    piotr.refresh_from_db()
    assert piotr.resume_at is None
    assert Activity.objects.filter(company=shop, message="resume cancelled: blocked: opted_out").exists()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"wrong_person_at": timezone.now()}, "wrong_person"),
        ({"email_status": "invalid"}, "email_invalid"),
        ({"legal_basis": None}, "no_legal_basis"),
    ],
)
def test_other_gate_refusals_end_the_loop(shop, piotr, earlier_draft, communicate, change, reason):
    Contact.objects.filter(pk=piotr.pk).update(**change)
    due(piotr)
    assert resume_service.run_due()["cancelled"] == 1
    communicate.assert_not_called()
    assert Activity.objects.filter(company=shop, message=f"resume cancelled: blocked: {reason}").exists()


def test_do_not_contact_company_is_not_resumed(shop, piotr, earlier_draft, communicate):
    shop.do_not_contact = True
    shop.save()
    due(piotr)
    assert resume_service.run_due()["cancelled"] == 1
    communicate.assert_not_called()


def test_the_address_is_verified_again_before_the_draft(shop, piotr, earlier_draft, communicate, verify_on, dns):
    dns.answers["example-shop-4.test"] = DnsAnswer(exists=False)
    due(piotr)
    assert resume_service.run_due()["cancelled"] == 1
    communicate.assert_not_called()
    piotr.refresh_from_db()
    assert piotr.email_status == "invalid"


def test_nothing_to_repeat_without_an_earlier_draft(shop, piotr, communicate):
    due(piotr)
    assert resume_service.run_due()["cancelled"] == 1
    communicate.assert_not_called()


def test_toolbox_off_keeps_the_day_for_the_next_run(shop, piotr, earlier_draft, communicate):
    communicate.side_effect = ToolboxDisabledError()
    due(piotr)
    assert resume_service.run_due() == {"resumed": 0, "cancelled": 0, "waiting": 1}
    piotr.refresh_from_db()
    assert piotr.resume_at is not None


def test_a_run_is_limited_to_the_batch(shop, piotr, earlier_draft, communicate, monkeypatch):
    other = Contact.objects.get(company=shop, email="ola@example-shop-4.test")
    due(piotr)
    due(other)
    monkeypatch.setattr(leads_settings, "LEADS_RESUME_BATCH", 1)
    assert resume_service.run_due()["resumed"] == 1
    assert Contact.objects.filter(resume_at__isnull=False).count() == 1  # the second waits for the next run


def test_task_is_off_by_default_and_does_nothing(shop, piotr, earlier_draft, communicate):
    due(piotr)
    assert resume_contacts() == {"enabled": False}
    communicate.assert_not_called()
    piotr.refresh_from_db()
    assert piotr.resume_at is not None


def test_task_runs_when_enabled(shop, piotr, earlier_draft, communicate, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_RESUME_ENABLED", True)
    due(piotr)
    assert resume_contacts() == {"enabled": True, "resumed": 1, "cancelled": 0, "waiting": 0}


def test_channel_settings_api_sets_the_windows(admin_api, channel):
    body = admin_api.get(api_url("settings/")).json()
    assert body["resume_days"] == {} and body["resume_defaults"] == {"not_now": 30, "out_of_office": 7}
    patched = admin_api.patch(api_url("settings/"), {"resume_days": {"not_now": 60}}, format="json")
    assert patched.status_code == 200 and patched.json()["resume_days"] == {"not_now": 60}
    assert patched.json()["default_sender_route"] == ""
    assert admin_api.patch(api_url("settings/"), {"resume_days": {"not_now": 0}}, format="json").status_code == 400
    assert admin_api.patch(api_url("settings/"), {"resume_days": {"nope": 5}}, format="json").status_code == 400


def test_contact_api_shows_the_planned_day(admin_api, shop, piotr):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    body = admin_api.get(api_url(f"contacts/{piotr.pk}/")).json()
    assert body["resume_intent"] == "not_now" and body["resume_at"]


# --- Q16 -------------------------------------------------------------------------------------------------------------


def make_reply(shop, piotr, *, intent="interested", needs_review=True, days_ago=1):
    from django_communicator.enums import ReplyKind
    from django_communicator.models import Channel as CommChannel
    from django_communicator.models import Reply

    channel, _ = CommChannel.objects.get_or_create(idx=shop.channel.idx, defaults={"label": "Europe"})
    thread, _ = Thread.objects.get_or_create(
        channel=channel, subject_ref=f"leads.Company:{shop.pk}", recipient_email=piotr.email
    )
    return Reply.objects.create(
        channel=channel, thread=thread, from_email=piotr.email, kind=ReplyKind.REPLY, matched_by="in_reply_to",
        inbound_message_id=f"<r{Reply.objects.count()}@x>", received_at=timezone.now() - timedelta(days=days_ago),
        intent=intent, needs_review=needs_review, body_text="Send me the offer",
    )  # fmt: skip


def test_queue_collects_every_section_sorted_by_value(admin_api, shop, piotr, channel):
    make_reply(shop, piotr, intent="interested")
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    due(piotr, days=2)
    flagged = Contact.objects.get(company=shop, email="ola@example-shop-4.test")
    Contact.objects.filter(pk=flagged.pk).update(wrong_person_at=timezone.now())
    hot = shop.__class__.objects.create(
        channel=channel, name="Hot", domain="hot.test", stage=shop.stage, stage_entered_at=timezone.now(), source="csv"
    )
    Activity.objects.create(company=hot, kind=ActivityKind.SCORE, message="score", data={"segment": "S1", "points": 55})
    cold = shop.__class__.objects.create(
        channel=channel,
        name="Cold",
        domain="cold.test",
        stage=shop.stage,
        stage_entered_at=timezone.now(),
        source="csv",
    )
    Activity.objects.create(company=cold, kind=ActivityKind.SCORE, message="score", data={"segment": "S3", "points": 5})
    thread = Thread.objects.get(subject_ref=f"leads.Company:{shop.pk}")
    Message.objects.create(thread=thread, status=MessageStatus.REVIEW_REQUIRED, message_id="<d1@x>")
    Message.objects.create(thread=thread, status=MessageStatus.SENT, message_id="<d2@x>")

    body = admin_api.get(api_url("queue/")).json()
    assert body["counts"] == {"reply": 1, "task": 1, "hot_company": 1, "decision": 1, "drafts": 1}
    assert body["drafts"] == {"count": 1, "approvals_path": "/leads/approvals"}
    assert [row["kind"] for row in body["items"]] == ["reply", "hot_company", "drafts", "task", "decision"]
    values = [row["value"] for row in body["items"]]
    assert values == sorted(values, reverse=True)
    reply = body["items"][0]
    assert (
        reply["reason"] == "interested" and reply["excerpt"] == "Send me the offer" and reply["company_id"] == shop.pk
    )
    assert body["items"][1]["company_name"] == "Hot" and body["items"][1]["reason"] == "S1"
    assert body["items"][3]["value"] == queue_service.TASK_VALUE + 2


def test_a_contacted_or_blocked_company_is_not_a_hot_company(shop, channel):
    Activity.objects.create(
        company=shop, kind=ActivityKind.SCORE, message="score", data={"segment": "S1", "points": 80}
    )
    assert queue_service.build(channel)["counts"]["hot_company"] == 1
    Activity.objects.create(company=shop, kind=ActivityKind.DRAFT, message="draft")
    assert queue_service.build(channel)["counts"]["hot_company"] == 0


def test_disqualified_or_lower_segments_stay_out(shop, channel):
    data = {"segment": "S1", "points": 80, "disqualified": True}
    Activity.objects.create(company=shop, kind=ActivityKind.SCORE, message="score", data=data)
    assert queue_service.build(channel)["counts"]["hot_company"] == 0


def test_the_latest_score_decides(shop, channel):
    Activity.objects.create(company=shop, kind=ActivityKind.SCORE, message="a", data={"segment": "S1", "points": 80})
    Activity.objects.create(company=shop, kind=ActivityKind.SCORE, message="b", data={"segment": "S4", "points": 0})
    assert queue_service.build(channel)["counts"]["hot_company"] == 0


def test_a_reply_leaves_the_queue_when_a_draft_followed_or_it_is_old(shop, piotr, channel):
    reply = make_reply(shop, piotr)
    make_reply(shop, piotr, days_ago=30)
    make_reply(shop, piotr, needs_review=False)
    assert queue_service.build(channel)["counts"]["reply"] == 1
    Activity.objects.create(company=shop, kind=ActivityKind.DRAFT, message="draft")
    assert reply.pk and queue_service.build(channel)["counts"]["reply"] == 0


def test_replies_are_valued_by_class(shop, piotr, channel):
    make_reply(shop, piotr, intent="not_interested")
    make_reply(shop, piotr, intent="interested")
    make_reply(shop, piotr, intent="")
    rows = queue_service.build(channel)["items"]
    assert [row["reason"] for row in rows] == ["interested", "unclassified", "not_interested"]


def test_task_due_tomorrow_is_not_in_todays_queue(shop, piotr, channel):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    assert queue_service.build(channel)["counts"]["task"] == 0


def test_queue_is_empty_for_an_empty_channel(admin_api, channel):
    body = admin_api.get(api_url("queue/")).json()
    assert body["items"] == [] and body["counts"]["drafts"] == 0


def test_queue_staff_only_and_channel_scoped(admin_api, customer_api, shop):
    assert customer_api.get(api_url("queue/")).status_code == 403
    assert admin_api.get("/api/leads/v2/admin/nope/queue/").status_code == 404


# --- digest ----------------------------------------------------------------------------------------------------------


def test_digest_summarises_what_was_done_and_refused(shop, piotr, channel):
    for kind, message, data in [
        (ActivityKind.DRAFT, "draft review_required", {}),
        (ActivityKind.SENT, "message sent", {}),
        (ActivityKind.BLOCKED, "blocked: opted_out", {}),
        (ActivityKind.BLOCKED, "blocked: opted_out", {}),
        (ActivityKind.BLOCKED, "blocked: email_invalid", {}),
        (ActivityKind.VERIFICATION, "address invalid: hard_bounce", {"to": "invalid"}),
        (ActivityKind.OPTOUT, "do not contact: x", {}),
        (ActivityKind.RESUME, "resumed: draft requested", {}),
    ]:
        Activity.objects.create(company=shop, kind=kind, message=message, data=data)
    old = Activity.objects.create(company=shop, kind=ActivityKind.SENT, message="old")
    Activity.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
    digest = digest_service.build(channel)
    assert digest["done"] == {"drafts": 1, "sent": 1, "resumed": 1, "replies": 0}
    assert digest["refused"] == {"blocked": {"opted_out": 2, "email_invalid": 1}, "bad_addresses": 1, "opt_outs": 1}
    title, body = digest_service.render(digest)
    assert "1 sent" in title and "opted_out x2" in body and "bad addresses: 1" in body
    assert PIOTR not in body


def test_digest_task_is_off_by_default(shop, channel):
    with mock.patch(NOTIFY) as notify:
        assert send_digest() == {"enabled": False}
    notify.assert_not_called()


def test_digest_goes_through_notifications_when_enabled(shop, channel, monkeypatch):
    from django_notifications.models import Channel as NotifyChannel

    monkeypatch.setattr(leads_settings, "LEADS_DIGEST_ENABLED", True)
    NotifyChannel.objects.get_or_create(idx=channel.idx, defaults={"label": "Europe"})
    Activity.objects.create(company=shop, kind=ActivityKind.SENT, message="message sent")
    assert send_digest() == {"enabled": True, "sent": {channel.idx: 1}}
    from django_notifications.models import Notification

    note = Notification.objects.get()
    assert note.title.startswith("Outbound digest") and note.subject_ref == f"leads.Channel:{channel.pk}"


def test_digest_says_nothing_on_a_quiet_day(channel, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_DIGEST_ENABLED", True)
    with mock.patch(NOTIFY) as notify:
        assert send_digest() == {"enabled": True, "sent": {channel.idx: 0}}
    notify.assert_not_called()


def test_dev_endpoints_run_the_resume_and_the_digest(admin_api, shop, piotr, earlier_draft, communicate):
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    later = (timezone.now() + timedelta(days=31)).isoformat()
    body = admin_api.post(api_url("test/resume-now/"), {"as_of": later}, format="json").json()
    assert body == {"resumed": 1, "cancelled": 0, "waiting": 0}
    resume_service.on_reply(shop, piotr, fake_reply("not_now"))
    other = admin_api.post(api_url("test/resume-now/"), {"as_of": later, "company_id": shop.pk + 99}, format="json")
    assert other.json() == {"resumed": 0, "cancelled": 0, "waiting": 0}
    from django_notifications.models import Channel as NotifyChannel

    NotifyChannel.objects.get_or_create(idx=shop.channel.idx, defaults={"label": "Europe"})
    assert admin_api.post(api_url("test/digest-now/"), {}, format="json").json() == {"sent": {shop.channel.idx: 1}}
