# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""U3: a company whose only person is the wrong one or opted out looks for another person (`contact_needed`); an
accepted candidate becomes a contact that the gate lets through; a referral request may go to the wrong person."""

from types import SimpleNamespace

import pytest
from django_agreements.enums import LegalBasis

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Contact
from django_leads.services import candidate_service, company_service, contact_service, recipient_service
from django_leads.signals import contact_needed
from django_leads.signals.communicator_receivers import on_recipient_check

pytestmark = pytest.mark.django_db

EWA = "ewa@solo-shop.test"


@pytest.fixture
def solo(channel):
    company = company_service.upsert_company(
        channel, {"domain": "solo-shop.test", "name": "Solo Shop", "source": LeadSource.CSV}
    )[0]
    Contact.objects.create(company=company, email=EWA, is_primary=True, legal_basis=LegalBasis.LEGITIMATE_INTEREST)
    return company


@pytest.fixture
def received():
    calls = []

    def receiver(sender, **kwargs):
        calls.append(kwargs)

    contact_needed.connect(receiver, dispatch_uid="test.contact_needed")
    yield calls
    contact_needed.disconnect(dispatch_uid="test.contact_needed")


def only_contact(company) -> Contact:
    return Contact.objects.select_related("company__channel").get(company=company)


def looking(company) -> list[dict]:
    rows = Activity.objects.filter(company=company, kind=ActivityKind.NOTE, message="looking for another person")
    return [row.data for row in rows]


@pytest.mark.parametrize(
    ("action", "reason"),
    [
        (lambda c: contact_service.flag_wrong_person(c, actor="system"), "wrong_person"),
        (lambda c: contact_service.opt_out(c, reason="optout_confirmed", actor="communicator"), "opted_out"),
    ],
)
def test_the_only_person_lost_starts_a_search(solo, received, action, reason, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        action(only_contact(solo))
    assert looking(solo) == [{"event": "contact_needed", "reason": reason}]
    assert received == [
        {
            "signal": contact_needed,
            "subject_ref": f"leads.Company:{solo.pk}",
            "channel_idx": solo.channel.idx,
            "company_name": "Solo Shop",
            "domain": "solo-shop.test",
            "reason": reason,
        }
    ]
    solo.refresh_from_db()
    assert not solo.do_not_contact  # only the person is blocked, never the company


def test_a_company_with_another_person_does_not_search(shop, received, django_capture_on_commit_callbacks):
    piotr = Contact.objects.select_related("company__channel").get(email="piotr@example-shop-4.test")
    with django_capture_on_commit_callbacks(execute=True):
        contact_service.flag_wrong_person(piotr, actor="system")
    assert received == [] and looking(shop) == []


def test_a_do_not_contact_company_does_not_search(solo, received, django_capture_on_commit_callbacks):
    company_service.set_do_not_contact(solo, True, actor="reviewer")
    with django_capture_on_commit_callbacks(execute=True):
        contact_service.flag_wrong_person(only_contact(solo), actor="system")
    assert received == []


def test_a_second_wrong_person_flag_sends_nothing(solo, received, django_capture_on_commit_callbacks):
    contact = only_contact(solo)
    with django_capture_on_commit_callbacks(execute=True):
        contact_service.flag_wrong_person(contact, actor="system")
        contact_service.flag_wrong_person(only_contact(solo), actor="system")
    assert len(received) == 1


def test_accept_candidate_adds_a_mailable_contact(solo, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        contact_service.flag_wrong_person(only_contact(solo), actor="system")
    ref = candidate_service.accept_candidate(
        subject_ref=f"leads.Company:{solo.pk}", email="Tomasz.Lis@solo-shop.test", first_name="Tomasz",
        last_name="Lis", job_title="CEO", source_note="prospecting: public register", actor="anna",
    )  # fmt: skip
    contact = Contact.objects.select_related("company__channel").get(email="tomasz.lis@solo-shop.test")
    assert ref == f"leads.Contact:{contact.pk}"
    assert (contact.source, contact.legal_basis, contact.job_title) == ("prospecting", "legitimate_interest", "CEO")
    assert recipient_service.block_reason(contact) is None
    assert not candidate_service.needs_another_person(solo)
    note = Activity.objects.get(company=solo, message="contact added from prospecting: Tomasz Lis")
    assert (note.actor, note.data["source"], note.contact_id) == ("anna", "prospecting: public register", contact.pk)


def test_accept_candidate_refuses_unknown_company_and_existing_address(solo):
    with pytest.raises(ValueError, match="no company"):
        candidate_service.accept_candidate(subject_ref="leads.Company:0", email="a@b.test", first_name="A",
                                           last_name="B", actor="anna")  # fmt: skip
    with pytest.raises(ValueError, match="already exists"):
        candidate_service.accept_candidate(subject_ref=f"leads.Company:{solo.pk}", email=EWA, first_name="Ewa",
                                           last_name="Kot", actor="anna")  # fmt: skip


def test_a_referral_request_may_go_to_the_wrong_person(solo):
    contact = only_contact(solo)
    contact_service.flag_wrong_person(contact, actor="system")
    thread = SimpleNamespace(subject_ref=f"leads.Company:{solo.pk}", recipient_email=EWA)
    plain = SimpleNamespace(thread=thread)
    referral = SimpleNamespace(thread=thread, referral_request=True)
    assert on_recipient_check(None, plain) == "the contact is flagged as the wrong person"
    assert on_recipient_check(None, referral) is None
    contact_service.opt_out(only_contact(solo), reason="x", actor="a")
    assert on_recipient_check(None, referral) == "the contact opted out"
