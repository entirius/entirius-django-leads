# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""End-to-end helpers for T4: a real communicator inbound ingest (thread, sent message, running sequence) over the
FAKE toolbox, and the leads receivers reacting on commit. Import the fixtures into a test module."""

from email.message import EmailMessage
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from tests.fake_toolbox import FakeToolboxClient

OUR_ID = "<t4-out-1@mail.example.test>"
NOTIFY = "django_notifications.services.notify_service.notify"


@pytest.fixture
def fake_intent(monkeypatch):
    """Reply intent classification is on and answered by the deterministic fake toolbox client."""
    from django_communicator import settings as communicator_settings
    from django_communicator.services import reply_intent_service

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_REPLY_INTENT_MODEL", "fake-model")
    monkeypatch.setattr(reply_intent_service, "ToolboxClient", FakeToolboxClient)


def raw_reply(body: str, *, sender: str, subject: str = "Re: Your shop audit", message_id: str = OUR_ID) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"Piotr <{sender}>"
    msg["To"] = "outreach@mail.example.test"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 14 Sep 2026 10:15:00 +0200"
    msg["Message-ID"] = f"<t4-{abs(hash((body, subject, sender)))}@example-shop-4.test>"
    msg["In-Reply-To"] = message_id
    msg["References"] = message_id
    msg.set_content(body)
    return msg.as_bytes()


@pytest.fixture
def inbox(shop, django_capture_on_commit_callbacks):
    """`inbox(body)` ingests one reply from the shop's primary contact into a thread with a running sequence.
    Returns SimpleNamespace(reply, thread, state, notify, company)."""
    from django_communicator.enums import MessageStatus
    from django_communicator.models import Channel, Message, Sequence, Thread, ThreadSequenceState
    from django_communicator.services.inbound_service import ingest

    channel = Channel.objects.create(
        idx=shop.channel.idx, label="Europe", default_language=shop.channel.default_language
    )
    email = "piotr@example-shop-4.test"
    thread = Thread.objects.create(
        channel=channel,
        subject_ref=f"leads.Company:{shop.pk}",
        recipient_email=email,
        recipient_language=shop.channel.default_language,
    )
    Message.objects.create(
        thread=thread, status=MessageStatus.SENT, message_id=OUR_ID, sent_at=timezone.now(), subject="Audit"
    )
    sequence = Sequence.objects.create(channel=channel, key="followup")
    ThreadSequenceState.objects.create(thread=thread, sequence=sequence, next_due_at=timezone.now())

    def receive(body: str, **kwargs):
        with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
            reply = ingest(channel, raw_reply(body, sender=email, **kwargs))
        shop.refresh_from_db()
        return SimpleNamespace(
            reply=reply,
            thread=Thread.objects.get(pk=thread.pk),
            state=ThreadSequenceState.objects.get(thread=thread),
            notify=notify,
            company=shop,
        )

    return receive
