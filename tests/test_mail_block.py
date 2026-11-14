# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Words the screens no longer guess: a contact's `mail_block` (the first reason of the send check behind
`mailable/`) and the company name of a rule run."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_agreements.enums import LegalBasis
from django_communicator.models import AddressStatus, Suppression
from django_communicator.models import Channel as CommunicatorChannel

from django_leads.enums import RuleOutcome
from django_leads.models import Company, Contact, RuleRun
from django_leads.services import company_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

BASIS = LegalBasis.LEGITIMATE_INTEREST


@pytest.fixture
def mailer(channel) -> CommunicatorChannel:
    return CommunicatorChannel.objects.create(idx=channel.idx, label="Europe")


def add(company: Company, email: str, **fields) -> Contact:
    return Contact.objects.create(company=company, email=email, **{"legal_basis": BASIS, **fields})


@pytest.fixture
def blocked(mailer, channel, shop) -> dict[str, Contact]:
    """One contact per reason of the send check, and one that can be mailed."""
    closed = company_service.upsert_company(channel, {"domain": "closed-shop.test", "name": "Closed", "source": "csv"})
    Company.objects.filter(pk=closed[0].pk).update(do_not_contact=True)
    now = timezone.now()
    Suppression.objects.create(channel=mailer, kind="email", value="listed@example-shop-4.test", reason="manual")
    AddressStatus.objects.create(email="bounced@example-shop-4.test", status="invalid", reason="hard_bounce")
    return {
        "do_not_contact": add(closed[0], "anna@closed-shop.test"),
        "no_email": shop.contacts.get(email=""),
        "opted_out": add(shop, "out@example-shop-4.test", opt_out_at=now),
        "anonymised": add(shop, "anon@example-shop-4.test", anonymised_at=now),
        "wrong_person": add(shop, "wrong@example-shop-4.test", wrong_person_at=now),
        "no_legal_basis": add(shop, "nobasis@example-shop-4.test", legal_basis=None),
        "suppressed": add(shop, "listed@example-shop-4.test"),
        "address_invalid": add(shop, "bounced@example-shop-4.test"),
        "": shop.contacts.get(email="piotr@example-shop-4.test"),
    }


def first_reason(admin_api, contact: Contact) -> str:
    url = api_url(f"companies/{contact.company_id}/contacts/{contact.pk}/mailable/")
    return next(iter(admin_api.get(url).json()["reasons"]), "")


def listed_blocks(admin_api, query: str = "") -> dict[int, str]:
    response = admin_api.get(api_url(f"contacts/?page_size=100{query}"))
    assert response.status_code == 200, response.content
    return {row["id"]: row["mail_block"] for row in response.json()["results"]}


def test_every_reason_of_the_send_check_is_a_mail_block_equal_to_its_first_reason(admin_api, blocked):
    blocks = listed_blocks(admin_api)

    for reason, contact in blocked.items():
        assert blocks[contact.pk] == reason == first_reason(admin_api, contact), reason


def test_the_first_reason_wins(admin_api, blocked):
    contact = blocked["wrong_person"]
    Contact.objects.filter(pk=contact.pk).update(legal_basis=None)

    assert listed_blocks(admin_api)[contact.pk] == "wrong_person" == first_reason(admin_api, contact)


def test_the_company_card_and_one_contact_carry_it_too(admin_api, blocked):
    contact = blocked["wrong_person"]
    card = admin_api.get(api_url(f"companies/{contact.company_id}/")).json()
    one = admin_api.get(api_url(f"contacts/{contact.pk}/")).json()

    assert {row["id"]: row["mail_block"] for row in card["contacts"]}[contact.pk] == "wrong_person"
    assert one["mail_block"] == "wrong_person"


def test_without_a_communicator_channel_no_address_is_refused(admin_api, shop):
    blocks = listed_blocks(admin_api)

    assert sorted(blocks.values()) == ["", "", "no_email"]


def test_a_page_of_contacts_costs_the_same_queries_for_any_number(admin_api, blocked, shop):
    with CaptureQueriesContext(connection) as few:
        listed_blocks(admin_api, f"&company={shop.pk}")
    for index in range(10):
        add(shop, f"more{index}@example-shop-4.test")
    with CaptureQueriesContext(connection) as many:
        listed_blocks(admin_api, f"&company={shop.pk}")

    assert len(many) == len(few)


# --- rule runs -----------------------------------------------------------------------------------------------------


def runs(admin_api) -> list[dict]:
    response = admin_api.get(api_url("rule-runs/"))
    assert response.status_code == 200, response.content
    return response.json()["results"]


def test_a_rule_run_names_its_company_in_one_query_for_the_page(admin_api, channel, shop, company, make_rule):
    rule = make_rule()
    RuleRun.objects.create(rule=rule, company=shop, outcome=RuleOutcome.FIRED)
    with CaptureQueriesContext(connection) as one:
        runs(admin_api)
    RuleRun.objects.create(rule=rule, company=company, outcome=RuleOutcome.SKIPPED)
    with CaptureQueriesContext(connection) as two:
        listed = runs(admin_api)

    assert [row["company_name"] for row in listed] == [company.name, "Example Shop 4"]
    assert len(two) == len(one)


def test_the_runs_of_a_deleted_company_go_with_it(admin_api, shop, make_rule):
    RuleRun.objects.create(rule=make_rule(), company=shop, outcome=RuleOutcome.FIRED)
    shop.delete()

    assert runs(admin_api) == []
