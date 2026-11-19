# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The sender brand is the company attribute `sender_brand`: `mailbox_brand` of outreach, bulk set / clear of one
attribute, and the import that drops a bad cell instead of the row."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.services import compose_service

from django_leads import settings as leads_settings
from django_leads.enums import AttributeKind, ImportStatus, LeadSource
from django_leads.models import Company, Contact, Stage
from django_leads.services import attribute_service, company_service, import_service, outreach_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

KEY = leads_settings.LEADS_SENDER_BRAND_ATTRIBUTE
BRANDS = [{"value": "acme", "label": "Acme"}, {"value": "beta", "label": "Beta"}]


@pytest.fixture
def brand_attribute(channel):
    return attribute_service.create_definition(
        channel,
        {"entity": "company", "key": KEY, "label": "Sender brand", "kind": AttributeKind.SELECT, "options": BRANDS},
    )


def new_company(channel, domain: str, **fields) -> Company:
    return Company.objects.create(
        channel=channel,
        name=domain,
        domain=domain,
        stage=Stage.objects.get(channel=channel, key="new"),
        stage_entered_at=timezone.now(),
        source=LeadSource.MANUAL,
        **fields,
    )


def bulk(admin_api, ids: list[int], value, key: str = KEY):
    body = {"company_ids": ids, "key": key, "value": value}
    return admin_api.post(api_url("companies/bulk-attribute/"), body, format="json")


def test_the_setting_defaults_to_sender_brand():
    assert KEY == "sender_brand"


# --- outreach -------------------------------------------------------------------------------------------------------


def draft(company: Company) -> mock.MagicMock:
    contact = Contact.objects.filter(company=company).exclude(email="").first()
    message = SimpleNamespace(pk=7, status="review_required")
    with (
        mock.patch.object(outreach_service, "communicate", return_value=message) as communicate,
        mock.patch.object(outreach_service, "render_legal_footer", return_value="footer"),
        mock.patch.object(outreach_service, "resolve_clause_set"),
    ):
        outreach_service.request_draft(company, contact, "lead.cold.b2b", actor="test")
    return communicate


def test_draft_asks_for_the_mailbox_of_the_sender_brand_attribute(shop):
    Company.objects.filter(pk=shop.pk).update(attributes={KEY: "acme"})
    shop.refresh_from_db()
    assert draft(shop).call_args.kwargs["mailbox_brand"] == "acme"


def test_draft_of_a_company_without_the_attribute_passes_blank(shop):
    kwargs = draft(shop).call_args.kwargs
    assert (kwargs["mailbox_brand"], kwargs["mailbox_user_id"]) == ("", None)


def test_compose_passes_owner_and_brand_to_communicator(admin_api, shop):
    CommunicatorChannel.objects.create(idx=shop.channel.idx, label="Europe")
    owner = get_user_model().objects.create_user(username="seller", is_staff=True)
    Company.objects.filter(pk=shop.pk).update(owner=owner, attributes={KEY: "acme"})
    piotr = shop.contacts.get(email="piotr@example-shop-4.test")
    with (
        mock.patch.object(outreach_service, "_legal_footer", return_value="footer"),
        mock.patch.object(compose_service, "compose", wraps=compose_service.compose) as spy,
    ):
        body = {"contact_id": piotr.pk, "subject": "Our call", "body": "Hello Piotr"}
        response = admin_api.post(api_url(f"companies/{shop.pk}/compose/"), body, format="json")
    assert response.status_code == 201, response.json()
    assert (spy.call_args.kwargs["mailbox_user_id"], spy.call_args.kwargs["mailbox_brand"]) == (owner.pk, "acme")


# --- bulk attribute -------------------------------------------------------------------------------------------------


def test_bulk_sets_and_clears_an_attribute_counting_only_changes(admin_api, channel, brand_attribute):
    has, lacks = new_company(channel, "a.test", attributes={KEY: "acme"}), new_company(channel, "b.test")
    assert bulk(admin_api, [has.pk, lacks.pk], "acme").json() == {"updated": 1}
    assert dict(Company.objects.values_list("domain", "attributes")) == {
        "a.test": {KEY: "acme"},
        "b.test": {KEY: "acme"},
    }
    assert bulk(admin_api, [has.pk, lacks.pk], None).json() == {"updated": 2}
    assert not Company.objects.exclude(attributes={}).exists()
    assert bulk(admin_api, [has.pk], "").json() == {"updated": 0}


def test_bulk_keeps_the_other_attributes_of_a_company(admin_api, channel, brand_attribute):
    attribute_service.create_definition(channel, {"entity": "company", "key": "size", "label": "S", "kind": "text"})
    company = new_company(channel, "a.test", attributes={"size": "big"})
    assert bulk(admin_api, [company.pk], "beta").status_code == 200
    company.refresh_from_db()
    assert company.attributes == {"size": "big", KEY: "beta"}


def test_bulk_refuses_a_value_the_kind_refuses(admin_api, channel, brand_attribute):
    company = new_company(channel, "a.test")
    response = bulk(admin_api, [company.pk], "nike")
    assert response.status_code == 400 and KEY in str(response.json())
    company.refresh_from_db()
    assert company.attributes == {}


def test_bulk_refuses_an_unknown_or_inactive_key(admin_api, channel, brand_attribute):
    company = new_company(channel, "a.test")
    assert bulk(admin_api, [company.pk], "x", key="nothing").status_code == 400
    definition = attribute_service.list_definitions(channel, "company").get(key=KEY)
    attribute_service.update_definition(definition, {"is_active": False})
    assert bulk(admin_api, [company.pk], "acme").status_code == 400


def test_bulk_refuses_an_attribute_of_another_entity(admin_api, channel):
    attribute_service.create_definition(channel, {"entity": "contact", "key": "role", "label": "R", "kind": "text"})
    assert bulk(admin_api, [new_company(channel, "a.test").pk], "x", key="role").status_code == 400


def test_bulk_cannot_clear_a_required_attribute(admin_api, channel):
    attribute_service.create_definition(
        channel, {"entity": "company", "key": "size", "label": "S", "kind": "text", "required": True}
    )
    company = new_company(channel, "a.test", attributes={"size": "big"})
    assert bulk(admin_api, [company.pk], None, key="size").status_code == 400


def test_bulk_over_the_limit_is_400(admin_api, channel, brand_attribute):
    ids = list(range(1, company_service.ATTRIBUTE_BULK_LIMIT + 2))
    assert bulk(admin_api, ids, "acme").status_code == 400
    assert bulk(admin_api, [], "acme").status_code == 400


def test_bulk_refuses_a_company_of_another_channel_and_changes_nothing(admin_api, channel, brand_attribute):
    other = channel.__class__.objects.create(idx="other", name="Other", default_language=channel.default_language)
    Stage.objects.create(channel=other, key="new", label="New", order=0)
    own, foreign = new_company(channel, "a.test"), new_company(other, "f.test")
    assert bulk(admin_api, [own.pk, foreign.pk], "acme").status_code == 400
    assert not Company.objects.exclude(attributes={}).exists()


def test_bulk_is_staff_only(customer_api, channel, brand_attribute):
    assert bulk(customer_api, [new_company(channel, "a.test").pk], "acme").status_code == 403


# --- import ---------------------------------------------------------------------------------------------------------


def test_import_drops_a_bad_brand_cell_and_keeps_the_company(channel, brand_attribute):
    content = f"company_name,domain,attr.{KEY}\nA,a-shop.pl,acme\nB,b-shop.pl,Nike\nC,c-shop.pl,New Balance\n"
    batch = import_service.create_batch(channel, "leads.csv", len(content), "test")
    batch = import_service.run_content(batch, content)
    assert batch.status == ImportStatus.DONE and (batch.created_count, batch.skipped_count) == (3, 0)
    assert batch.warnings == {f"bad_attribute:{KEY}": 2}
    attributes = dict(Company.objects.values_list("domain", "attributes"))
    assert attributes == {"a-shop.pl": {KEY: "acme"}, "b-shop.pl": {}, "c-shop.pl": {}}
