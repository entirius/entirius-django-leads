# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Q7: the shared do-not-contact list across channels and mailboxes, confirmed opt-outs reaching leads, and the
"why is this contact (not) mailable" report."""

from datetime import timedelta

import pytest
from django.utils import timezone
from django_agreements.enums import LegalBasis
from django_communicator.enums import SuppressionKind
from django_communicator.models import Channel as CommChannel
from django_communicator.models import Suppression

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Channel, Company, Contact, Stage
from django_leads.services import company_service, dnc_service, recipient_service
from django_leads.signals import communicator_receivers
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

EMAIL = "piotr@example-shop-4.test"


@pytest.fixture
def other(polish) -> Channel:
    """A second leads channel (another brand/mailbox set) with the same prospect on it."""
    channel = Channel.objects.create(idx="second", name="Second", default_language=polish)
    Stage.objects.create(channel=channel, key="new", label="New", order=0)
    return channel


@pytest.fixture
def twin(other) -> Contact:
    company = company_service.upsert_company(
        other, {"domain": "example-shop-4.test", "name": "Shop", "source": LeadSource.CSV}
    )[0]
    return Contact.objects.create(company=company, email=EMAIL, legal_basis=LegalBasis.LEGITIMATE_INTEREST)


def mine(shop) -> Contact:
    return shop.contacts.select_related("company__channel").get(email=EMAIL)


def test_opt_out_in_another_channel_stops_the_same_address_here(shop, twin):
    assert recipient_service.block_reason(mine(shop)) is None
    Contact.objects.filter(pk=twin.pk).update(opt_out_at=timezone.now())
    assert recipient_service.block_reason(mine(shop)) == "opted_out_elsewhere"


def test_do_not_contact_company_with_the_same_domain_elsewhere_stops_all_its_contacts(shop, twin):
    Company.objects.filter(pk=twin.company_id).update(do_not_contact=True)
    assert recipient_service.block_reason(mine(shop)) == "domain_do_not_contact_elsewhere"


def test_communicator_suppression_of_another_channel_stops_here_but_own_channel_is_left_to_communicator(shop, twin):
    own = CommChannel.objects.create(idx=shop.channel.idx, label="own")
    theirs = CommChannel.objects.create(idx="second", label="second")
    Suppression.objects.create(channel=own, kind=SuppressionKind.EMAIL, value=EMAIL)
    assert recipient_service.block_reason(mine(shop)) is None  # communicator marks the draft suppressed itself
    Suppression.objects.create(channel=theirs, kind=SuppressionKind.EMAIL, value=EMAIL)
    assert recipient_service.block_reason(mine(shop)) == "suppressed_elsewhere"


def test_add_email_marks_contacts_everywhere_and_every_communicator_channel(shop, twin):
    a, b = (
        CommChannel.objects.create(idx=shop.channel.idx, label="a"),
        CommChannel.objects.create(idx="second", label="b"),
    )
    assert dnc_service.add(email=" Piotr@Example-Shop-4.test ", reason="asked by phone", actor="op") == {
        "contacts": 2, "companies": 0,
    }  # fmt: skip
    assert Contact.objects.filter(email=EMAIL, opt_out_at__isnull=False).count() == 2
    assert set(Suppression.objects.filter(kind="email").values_list("channel_id", flat=True)) == {a.pk, b.pk}
    assert Activity.objects.filter(kind=ActivityKind.OPTOUT, actor="op").count() == 2
    assert dnc_service.add(email=EMAIL, reason="again", actor="op")["contacts"] == 0  # idempotent


def test_add_domain_blocks_companies_of_every_channel(shop, twin):
    CommChannel.objects.create(idx=shop.channel.idx, label="a")
    assert dnc_service.add(domain="https://www.example-shop-4.test/x", reason="legal request", actor="op") == {
        "contacts": 0, "companies": 2,
    }  # fmt: skip
    assert not Company.objects.filter(domain="example-shop-4.test", do_not_contact=False).exists()
    assert Suppression.objects.filter(kind="domain", value="example-shop-4.test").count() == 1


def test_add_needs_exactly_one_valid_value():
    with pytest.raises(ValueError):
        dnc_service.add(reason="x", actor="op")
    with pytest.raises(ValueError):
        dnc_service.add(email="a@b.test", domain="b.test", reason="x", actor="op")
    with pytest.raises(ValueError):
        dnc_service.add(email="not-an-address", reason="x", actor="op")


def test_confirmed_optout_in_communicator_reaches_every_leads_channel(shop, twin):
    CommChannel.objects.create(idx=shop.channel.idx, label="a")
    communicator_receivers.record_optout(f"leads.Company:{shop.pk}", EMAIL)
    assert Contact.objects.filter(email=EMAIL, opt_out_at__isnull=False).count() == 2
    assert recipient_service.block_reason(mine(shop)) == "opted_out"


def test_confirmed_optout_signal_is_connected(shop, django_capture_on_commit_callbacks):
    from django.db import transaction
    from django_communicator.signals import optout_confirmed

    CommChannel.objects.create(idx=shop.channel.idx, label="a")
    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        optout_confirmed.send(sender=None, subject_ref=f"leads.Company:{shop.pk}", email=EMAIL, channel_idx="x")
    assert mine(shop).opt_out_at is not None


def test_api_add_list_and_no_delete(shop, twin, admin_api):
    CommChannel.objects.create(idx=shop.channel.idx, label="a")
    created = admin_api.post(api_url("do-not-contact/"), {"email": EMAIL, "reason": "complaint"}, format="json")
    assert created.status_code == 201 and created.json() == {"contacts": 2, "companies": 0}
    body = admin_api.get(api_url("do-not-contact/")).json()
    assert {row["channel"] for row in body["contacts"]} == {shop.channel.idx, "second"}
    assert [row["value"] for row in body["suppressions"]] == [EMAIL]
    assert admin_api.post(api_url("do-not-contact/"), {"reason": "x"}, format="json").status_code == 400
    assert (
        admin_api.post(api_url("do-not-contact/"), {"email": "nonsense", "reason": "x"}, format="json").status_code
        == 400
    )
    assert admin_api.delete(api_url("do-not-contact/")).status_code == 405


def test_do_not_contact_endpoints_are_staff_only(shop, customer_api):
    assert customer_api.get(api_url("do-not-contact/")).status_code == 403
    assert (
        customer_api.post(api_url("do-not-contact/"), {"email": EMAIL, "reason": "x"}, format="json").status_code == 403
    )


def test_send_check_lists_every_reason_and_the_facts(shop, twin, admin_api):
    contact = mine(shop)
    Contact.objects.filter(pk=twin.pk).update(opt_out_at=timezone.now())
    Contact.objects.filter(pk=contact.pk).update(legal_basis=None, email_status="invalid", email_status_reason="syntax")
    body = admin_api.get(api_url(f"contacts/{contact.pk}/send-check/")).json()
    assert body["eligible"] is False
    assert body["reasons"] == ["no_legal_basis", "email_invalid", "opted_out_elsewhere"]
    assert body["email"]["status"] == "invalid" and body["legal_basis"]["value"] is None
    assert body["retention"]["days"] == 180 and body["sender_route"] == ""


def test_send_check_eligible_contact_with_basis_origin_and_retention(shop, admin_api):
    contact = mine(shop)
    from django_leads.services import contact_service

    contact_service.set_legal_basis(contact, LegalBasis.CONTRACT, source="admin", consent_ref="admin:op", actor="op")
    Company.objects.filter(pk=shop.pk).update(last_activity_at=timezone.now())
    shop.channel.retention_days = 90
    shop.channel.save()
    body = admin_api.get(api_url(f"contacts/{contact.pk}/send-check/")).json()
    assert body["eligible"] is True and body["reasons"] == []
    assert body["legal_basis"]["source"] == "admin" and body["legal_basis"]["set_at"]
    assert body["retention"]["days"] == 90 and body["retention"]["exempt_won"] is False
    assert timezone.now() + timedelta(days=89) < timezone.datetime.fromisoformat(body["retention"]["due_at"])


def test_send_check_won_company_is_exempt_from_retention(shop, admin_api):
    won = Stage.objects.get(channel=shop.channel, key="won")
    Company.objects.filter(pk=shop.pk).update(stage=won)
    body = admin_api.get(api_url(f"contacts/{mine(shop).pk}/send-check/")).json()
    assert body["retention"]["exempt_won"] is True and body["retention"]["due_at"] is None


def test_send_check_is_scoped_to_the_channel(shop, twin, admin_api):
    assert admin_api.get(api_url(f"contacts/{twin.pk}/send-check/")).status_code == 404
