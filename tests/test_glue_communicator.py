# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Outbound glue: leads and communicator together, nothing mocked between them. Needs a communicator with
sender routes, `message_bounced` and the pre-send hook; older communicators skip the whole module."""

import smtplib
from datetime import datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import override_settings

from django_leads.enums import EmailStatus
from django_leads.models import Contact
from django_leads.services import outreach_service, verification_service

fakeredis = pytest.importorskip("fakeredis")  # the communicator's daily counter runs on it, as in its own suite
pytest.importorskip("django_communicator.signals")
from django_communicator import settings as communicator_settings  # noqa: E402
from django_communicator import signals as communicator_signals  # noqa: E402

if not hasattr(communicator_signals, "message_bounced") or not hasattr(
    communicator_settings, "COMMUNICATOR_PRESEND_CHECK"
):
    pytest.skip("communicator without the outbound glue", allow_module_level=True)

from django_communicator.enums import ChannelMode, FailureCode, MessageStatus, TemplateKind  # noqa: E402
from django_communicator.models import Channel as CommChannel  # noqa: E402
from django_communicator.models import (  # noqa: E402
    Message,
    MessageTemplate,
    SendPolicy,
    SendWindow,
    Suppression,
    Thread,
)
from django_communicator.services import (  # noqa: E402
    channel_service,
    clock_service,
    counter_service,
    send_service,
    sender_route_service,
    template_service,
    unsubscribe_service,
)
from django_communicator.services.communicate_service import RecipientData, communicate  # noqa: E402

PRESEND = "django_leads.services.verification_service.presend_check"
MONDAY_10 = datetime(2026, 9, 14, 10, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
PIOTR = "piotr@example-shop-4.test"
OLA = "ola@example-shop-4.test"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    server = fakeredis.FakeRedis()
    monkeypatch.setattr(counter_service, "_client", lambda: server)
    cache.clear()
    yield server
    cache.clear()


@pytest.fixture
def comm(shop):
    """A communicator channel (same idx as the leads channel) with a static auto-approve template, business hours and
    the live double gate open; the sender route `ecomhouse` exists."""
    language = shop.channel.default_language
    channel = CommChannel.objects.create(idx=shop.channel.idx, label="Europe", default_language=language)
    fields = {"key": "followup", "kind": TemplateKind.STATIC, "subject": "Hi {first_name}", "body": "Following up."}
    template_service.save_template(
        MessageTemplate(
            channel=channel,
            language=language,
            auto_approve=True,
            requires_legal_footer=False,
            json_schema=None,
            model="",
            **fields,
        )
    )
    policy = SendPolicy.objects.create(channel=channel, daily_cap=10, spread=False)
    SendWindow.objects.create(policy=policy, start_time=time(8), end_time=time(17))
    clock_service.set_override(channel, MONDAY_10)
    sender_route_service.create_route(channel, key="ecomhouse", from_email="wiktorw@ecom.house", cc_email="")
    return channel_service.set_mode(channel, mode=ChannelMode.LIVE, live_enabled=True)


def _queue(shop, email: str) -> Message:
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=shop.channel.idx,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=f"leads.Company:{shop.pk}",
        requires_review=False,
    )


def _live_run() -> dict:
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        return dict(send_service.run_send_due())


@pytest.fixture
def hook():
    with mock.patch.object(communicator_settings, "COMMUNICATOR_PRESEND_CHECK", PRESEND):
        yield


def test_glue_sender_route_set_on_the_company_reaches_the_thread_and_the_mail(shop, comm):
    shop.sender_route = "ecomhouse"
    shop.save()
    contact = shop.contacts.get(email=PIOTR)
    with (
        mock.patch.object(outreach_service, "render_legal_footer", return_value="footer"),
        mock.patch.object(outreach_service, "resolve_clause_set"),
    ):
        message = outreach_service.request_draft(shop, contact, "followup", actor="test")

    assert Thread.objects.get(pk=message.thread_id).sender_route == "ecomhouse"


def test_glue_channel_default_route_is_passed_when_the_company_has_none(shop, comm):
    shop.channel.default_sender_route = "ecomhouse"
    shop.channel.save()
    contact = shop.contacts.get(email=PIOTR)
    with (
        mock.patch.object(outreach_service, "render_legal_footer", return_value="footer"),
        mock.patch.object(outreach_service, "resolve_clause_set"),
    ):
        message = outreach_service.request_draft(shop, contact, "followup", actor="test")

    assert Thread.objects.get(pk=message.thread_id).sender_route == "ecomhouse"


def test_glue_invalid_address_is_not_sent_valid_one_is(shop, comm, hook):
    Contact.objects.filter(email=PIOTR).update(email_status=EmailStatus.INVALID, email_status_reason="no_mx")
    dead, fine = _queue(shop, PIOTR), _queue(shop, OLA)

    counts = _live_run()

    dead.refresh_from_db(), fine.refresh_from_db()
    assert (counts["failed"], counts["sent"]) == (1, 1)
    assert (dead.status, dead.failure_code, dead.failure_detail) == (
        MessageStatus.FAILED,
        FailureCode.PRESEND_CHECK,
        "email_invalid",
    )
    assert [m.to for m in mail.outbox] == [[OLA]]


def test_glue_without_the_hook_an_invalid_address_is_still_mailed(shop, comm):
    Contact.objects.filter(email=PIOTR).update(email_status=EmailStatus.INVALID)
    _queue(shop, PIOTR)

    assert _live_run()["sent"] == 1


def test_glue_hard_bounce_marks_the_contact_invalid_at_once(shop, comm, django_capture_on_commit_callbacks):
    _queue(shop, PIOTR)
    refusal = smtplib.SMTPRecipientsRefused({PIOTR: (550, b"no such user")})

    with (
        django_capture_on_commit_callbacks(execute=True),
        mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=refusal),
    ):
        _live_run()

    contact = shop.contacts.get(email=PIOTR)
    assert (contact.email_status, contact.email_status_reason) == (
        EmailStatus.INVALID,
        verification_service.HARD_BOUNCE,
    )
    assert shop.contacts.get(email=OLA).email_status == EmailStatus.UNKNOWN


def test_glue_unsubscribe_link_puts_the_address_on_the_shared_list(shop, comm, django_capture_on_commit_callbacks):
    other = CommChannel.objects.create(idx="other-channel", label="Other", default_language=comm.default_language)
    _queue(shop, PIOTR)
    token = unsubscribe_service.make_token(comm.idx, PIOTR)

    with django_capture_on_commit_callbacks(execute=True):
        assert unsubscribe_service.unsubscribe(token) == PIOTR

    assert shop.contacts.get(email=PIOTR).opt_out_at is not None
    assert shop.contacts.get(email=OLA).opt_out_at is None
    assert set(Suppression.objects.filter(value=PIOTR).values_list("channel__idx", flat=True)) == {comm.idx, other.idx}
