# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A referral request (communicator's answer to a `wrong_person` reply) may go to the wrong person, nobody else."""

from types import SimpleNamespace

import pytest
from django_agreements.enums import LegalBasis

from django_leads.enums import LeadSource
from django_leads.models import Contact
from django_leads.services import company_service, contact_service
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


def only_contact(company) -> Contact:
    return Contact.objects.select_related("company__channel").get(company=company)


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
