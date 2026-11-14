# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The outreach gate answers communicator's `recipient_check`: every reason of `block_reasons` refuses an approval
with its sentence, and a contact flagged as the wrong person leaves no draft waiting."""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.models import Message, Suppression, Thread
from django_communicator.services import preflight_service, review_service

from django_leads.models import Company, Contact
from django_leads.services import contact_service
from django_leads.services.recipient_service import REFUSAL_SENTENCES
from django_leads.signals import communicator_receivers

pytestmark = pytest.mark.django_db

ADDRESS = "anna@example-shop-4.test"


@pytest.fixture
def mailer(channel) -> CommunicatorChannel:
    return CommunicatorChannel.objects.create(idx=channel.idx, label="Europe")


def waiting_draft(mailer, email: str, ref: str) -> Message:
    thread = Thread.objects.create(channel=mailer, subject_ref=ref, recipient_email=email)
    return Message.objects.create(thread=thread, status="review_required", legal_footer="Seller.", subject="Hello")


def contact(shop: Company, **fields) -> Contact:
    return Contact.objects.create(company=shop, email=ADDRESS, **{"legal_basis": "legitimate_interest", **fields})


REASONS = {
    "opted_out": {"opt_out_at": timezone.now()},
    "anonymised": {"anonymised_at": timezone.now()},
    "wrong_person": {"wrong_person_at": timezone.now()},
    "no_legal_basis": {"legal_basis": None},
}


@pytest.mark.parametrize("reason", [*REASONS, "do_not_contact"])
def test_every_reason_of_the_gate_refuses_an_approval_with_its_sentence(mailer, shop, reason):
    contact(shop, **REASONS.get(reason, {}))
    Company.objects.filter(pk=shop.pk).update(do_not_contact=reason == "do_not_contact")
    message = waiting_draft(mailer, ADDRESS.upper(), f"leads.Company:{shop.pk}")

    with pytest.raises(review_service.ReviewError) as refused:
        review_service.accept(message, user=get_user_model().objects.create_user("ewa"))

    assert refused.value.client_message == REFUSAL_SENTENCES[reason]
    assert preflight_service.problem(message) == ("recipient", REFUSAL_SENTENCES[reason])


def test_a_contact_that_may_be_mailed_is_accepted(mailer, shop):
    contact(shop)

    assert preflight_service.problem(waiting_draft(mailer, ADDRESS, f"leads.Company:{shop.pk}")) is None


def test_a_do_not_contact_company_refuses_also_an_address_that_is_no_contact(mailer, shop):
    Company.objects.filter(pk=shop.pk).update(do_not_contact=True)
    message = waiting_draft(mailer, "stranger@example.test", f"leads.Company:{shop.pk}")

    assert communicator_receivers.on_recipient_check(Message, message) == REFUSAL_SENTENCES["do_not_contact"]


@pytest.mark.parametrize("ref", ["other.Subject:1", "leads.Company:{pk}"])
def test_a_foreign_thread_or_an_unknown_address_is_not_refused(mailer, shop, ref):
    message = waiting_draft(mailer, "stranger@example.test", ref.format(pk=shop.pk))

    assert communicator_receivers.on_recipient_check(Message, message) is None


def test_a_suppressed_address_is_communicators_answer_not_repeated(mailer, shop):
    contact(shop)
    Suppression.objects.create(channel=mailer, kind="email", value=ADDRESS, reason="manual")
    message = waiting_draft(mailer, ADDRESS, f"leads.Company:{shop.pk}")

    assert communicator_receivers.on_recipient_check(Message, message) is None
    assert preflight_service.problem(message)[1] == "the recipient opted out or is on the do-not-contact list"


def test_the_receiver_asks_in_two_queries(mailer, shop):
    contact(shop, wrong_person_at=timezone.now())
    message = Message.objects.select_related("thread").get(
        pk=waiting_draft(mailer, ADDRESS, f"leads.Company:{shop.pk}").pk
    )

    with CaptureQueriesContext(connection) as captured:
        communicator_receivers.on_recipient_check(Message, message)

    assert len(captured) == 2


def test_flagging_the_wrong_person_withdraws_the_waiting_drafts_of_the_address(mailer, shop):
    person = contact(shop)
    waiting = waiting_draft(mailer, ADDRESS, f"leads.Company:{shop.pk}")
    other = waiting_draft(mailer, "piotr@example-shop-4.test", f"leads.Company:{shop.pk}")

    contact_service.flag_wrong_person(person, actor="system")

    waiting.refresh_from_db()
    assert (waiting.status, waiting.reject_reason, waiting.reviewed_by) == (
        "rejected",
        REFUSAL_SENTENCES["wrong_person"],
        None,
    )
    assert Message.objects.get(pk=other.pk).status == "review_required"
