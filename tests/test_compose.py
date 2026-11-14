# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""`POST companies/<id>/compose/`: the outreach gate, the legal footer, the owner's mailbox, answering a reply."""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_communicator.enums import MessageStatus, ReplyKind, ReplyMatch
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.models import Message, Reply, Thread
from django_communicator.services import compose_service

from django_leads.enums import ActivityKind
from django_leads.models import Activity
from django_leads.services import contact_service, outreach_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

PIOTR = "piotr@example-shop-4.test"
TEXT = {"subject": "Our call", "body": "Hello Piotr, as promised …"}


@pytest.fixture
def mailer(channel) -> CommunicatorChannel:
    return CommunicatorChannel.objects.create(idx=channel.idx, label="Europe")


@pytest.fixture
def footer():
    with mock.patch.object(outreach_service, "_legal_footer", return_value="footer"):
        yield


@pytest.fixture
def piotr(shop):
    return shop.contacts.get(email=PIOTR)


def compose(admin_api, company, body: dict):
    return admin_api.post(api_url(f"companies/{company.pk}/compose/"), body, format="json")


def reply_in(mailer, company, email: str = PIOTR) -> Reply:
    thread = Thread.objects.create(channel=mailer, subject_ref=f"leads.Company:{company.pk}", recipient_email=email)
    return Reply.objects.create(
        channel=mailer, thread=thread, from_email=email, kind=ReplyKind.REPLY, matched_by=ReplyMatch.HEADER,
        inbound_message_id=f"<{email}-{company.pk}@example.test>", received_at=timezone.now(),
    )  # fmt: skip


def test_compose_sends_through_the_owner_s_mailbox_after_the_gate(mailer, footer, shop, piotr, admin_api):
    owner = get_user_model().objects.create_user(username="seller", is_staff=True)
    shop.owner = owner
    shop.save(update_fields=["owner"])
    with mock.patch.object(compose_service, "compose", wraps=compose_service.compose) as spy:
        response = compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT})
    assert response.status_code == 201, response.json()
    kwargs = spy.call_args.kwargs
    assert (kwargs["mailbox_user_id"], kwargs["subject_ref"], kwargs["author"].username) == (
        owner.pk, f"leads.Company:{shop.pk}", "operator",
    )  # fmt: skip
    assert kwargs["recipient"].legal_footer == "footer" and kwargs["in_reply_to"] is None
    message = Message.objects.get(pk=response.json()["message_id"])
    assert (message.status, message.subject, message.created_by.username) == (
        MessageStatus.APPROVED,
        "Our call",
        "operator",
    )
    draft = Activity.objects.get(company=shop, kind=ActivityKind.DRAFT)
    assert (draft.message, draft.data["message_id"], draft.contact, draft.actor) == (
        "written by hand", message.pk, piotr, "operator",
    )  # fmt: skip


def test_compose_to_an_opted_out_contact_is_refused_once(mailer, footer, shop, piotr, admin_api):
    contact_service.opt_out(piotr, reason="manual", actor="operator")
    with mock.patch.object(compose_service, "compose") as spy:
        response = compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT})
    assert response.status_code == 409 and response.json()["error"] == "NOT_ELIGIBLE"
    spy.assert_not_called()
    blocked = Activity.objects.filter(company=shop, kind=ActivityKind.BLOCKED)
    assert list(blocked.values_list("message", flat=True)) == ["blocked: opted_out"]


def test_compose_answering_a_reply_passes_it_through(mailer, footer, shop, piotr, admin_api):
    answered = reply_in(mailer, shop)
    response = compose(admin_api, shop, {"contact_id": piotr.pk, "reply_id": answered.pk, **TEXT})
    assert response.status_code == 201, response.json()
    message = Message.objects.get(pk=response.json()["message_id"])
    assert (message.in_reply_to_id, message.thread_id) == (answered.pk, answered.thread_id)


def test_compose_refuses_a_reply_of_another_company(mailer, footer, shop, company, piotr, admin_api):
    foreign = reply_in(mailer, company, "jan@ogrod.pl")
    response = compose(admin_api, shop, {"contact_id": piotr.pk, "reply_id": foreign.pk, **TEXT})
    assert response.status_code == 400 and not Message.objects.exists()


def test_compose_refuses_a_reply_of_another_contact(mailer, footer, shop, piotr, admin_api):
    other = reply_in(mailer, shop, "ola@example-shop-4.test")
    response = compose(admin_api, shop, {"contact_id": piotr.pk, "reply_id": other.pk, **TEXT})
    assert response.status_code == 400 and not Message.objects.exists()


def test_compose_without_a_communicator_channel_is_409(footer, shop, piotr, admin_api):
    response = compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT})
    assert response.status_code == 409 and response.json()["error"] == "COMMUNICATOR_CHANNEL_MISSING"


def test_compose_without_a_clause_set_is_409(mailer, shop, piotr, admin_api):
    response = compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT})
    assert response.status_code == 409 and response.json()["error"] == "NO_LEGAL_BASIS"
    assert not Message.objects.exists()


@pytest.mark.parametrize(
    "body", [{"subject": ""}, {"subject": "   "}, {"body": " "}, {"contact_id": 999_999}, {"extra": 1}]
)
def test_compose_rejects_bad_requests(mailer, footer, shop, piotr, admin_api, body):
    response = compose(admin_api, shop, {"contact_id": piotr.pk, **TEXT, **body})
    assert response.status_code == 400 and not Message.objects.exists()


def test_compose_needs_staff(shop, piotr, customer_api):
    assert compose(customer_api, shop, {"contact_id": piotr.pk, **TEXT}).status_code == 403
