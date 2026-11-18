# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""U21 company brand: API read / write and filter, bulk brand, the import column (exact names, CSV connector,
mapping) and `mailbox_brand` on every outreach path."""

import io
from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.services import compose_service

from django_leads.connectors.csv import CSV_COLUMNS, parse_rows
from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Contact, Stage
from django_leads.services import company_service, import_service, outreach_service
from tests.conftest import api_url
from tests.test_import_mapping import columns_of, import_now, preview

pytestmark = pytest.mark.django_db


def new_company(channel, domain: str, brand: str = "") -> Company:
    return Company.objects.create(
        channel=channel,
        name=domain,
        domain=domain,
        brand=brand,
        stage=Stage.objects.get(channel=channel, key="new"),
        stage_entered_at=timezone.now(),
        source=LeadSource.MANUAL,
    )


def brand_notes(company: Company) -> list[tuple[str, dict]]:
    rows = Activity.objects.filter(company=company, kind=ActivityKind.NOTE, message__startswith="brand:")
    return list(rows.values_list("message", "data"))


# --- API read / write ----------------------------------------------------------------------------------------------


def test_create_takes_a_brand_and_the_response_carries_it(admin_api, channel):
    response = admin_api.post(api_url("companies/"), {"domain": "alfa.test", "brand": " Ecom.House "}, format="json")
    assert response.status_code == 201, response.json()
    assert response.json()["brand"] == "ecom.house"
    assert Company.objects.get(domain="alfa.test").brand == "ecom.house"


def test_a_company_without_a_brand_reads_blank(admin_api, company):
    assert admin_api.get(api_url(f"companies/{company.pk}/")).json()["brand"] == ""


@pytest.mark.parametrize("brand", ["ecom house", "ecom_house", "é", "x" * 33])
def test_a_brand_outside_the_slug_rule_is_400(admin_api, company, brand):
    assert admin_api.post(api_url("companies/"), {"domain": "b.test", "brand": brand}, format="json").status_code == 400
    response = admin_api.patch(api_url(f"companies/{company.pk}/"), {"brand": brand}, format="json")
    assert response.status_code == 400 and "brand" in str(response.json())


def test_patch_sets_and_clears_the_brand_with_a_note(admin_api, company):
    url = api_url(f"companies/{company.pk}/")
    assert admin_api.patch(url, {"brand": "entirius"}, format="json").json()["brand"] == "entirius"
    admin_api.patch(url, {"brand": "entirius"}, format="json")  # unchanged: no second note
    assert admin_api.patch(url, {"brand": ""}, format="json").json()["brand"] == ""
    assert brand_notes(company) == [
        ("brand: none", {"event": "brand_cleared", "brand": ""}),
        ("brand: entirius", {"event": "brand_set", "brand": "entirius"}),
    ]


def test_patch_brand_null_is_400(admin_api, company):
    assert admin_api.patch(api_url(f"companies/{company.pk}/"), {"brand": None}, format="json").status_code == 400


def test_list_filters_by_brand(admin_api, channel):
    new_company(channel, "e.test", "entirius")
    new_company(channel, "h.test", "ecom.house")
    new_company(channel, "n.test")

    def listed(**params) -> set[str]:
        return {row["domain"] for row in admin_api.get(api_url("companies/"), params).json()["results"]}

    assert listed(brand="ecom.house") == {"h.test"}
    assert listed(brand="none") == {"n.test"}
    assert listed(brand="") == listed() == {"e.test", "h.test", "n.test"}


# --- bulk -----------------------------------------------------------------------------------------------------------


def bulk(admin_api, body: dict):
    return admin_api.post(api_url("companies/bulk-brand/"), body, format="json")


def test_bulk_brand_changes_only_what_differs_one_note_each(admin_api, channel):
    done, todo = new_company(channel, "a.test", "ecom.house"), new_company(channel, "b.test")
    assert bulk(admin_api, {"ids": [done.pk, todo.pk], "brand": "ecom.house"}).json() == {"updated": 1}
    todo.refresh_from_db()
    assert todo.brand == "ecom.house" and brand_notes(done) == []
    assert brand_notes(todo) == [("brand: ecom.house", {"event": "brand_set", "brand": "ecom.house"})]
    assert Activity.objects.get(company=todo, message__startswith="brand:").actor == "operator"
    assert bulk(admin_api, {"ids": [done.pk, todo.pk], "brand": ""}).json() == {"updated": 2}
    assert set(Company.objects.values_list("brand", flat=True)) == {""}


def test_bulk_brand_over_the_limit_is_400(admin_api, channel):
    body = {"ids": list(range(1, company_service.BRAND_LIMIT + 2)), "brand": "entirius"}
    assert bulk(admin_api, body).status_code == 400


def test_bulk_brand_refuses_a_company_of_another_channel_and_changes_nothing(admin_api, channel, company):
    other = channel.__class__.objects.create(idx="other", name="Other", default_language=channel.default_language)
    Stage.objects.create(channel=other, key="new", label="New", order=0)
    foreign = new_company(other, "foreign.test")
    response = bulk(admin_api, {"ids": [company.pk, foreign.pk], "brand": "entirius"})
    assert response.status_code == 400 and "ids" in str(response.json())
    assert set(Company.objects.values_list("brand", flat=True)) == {""}


def test_bulk_brand_refuses_a_bad_brand(admin_api, company):
    assert bulk(admin_api, {"ids": [company.pk], "brand": "Ecom House"}).status_code == 400


def test_bulk_brand_needs_staff(customer_api, company):
    assert customer_api.post(api_url("companies/bulk-brand/"), {"ids": [company.pk], "brand": "x"}).status_code in (
        401,
        403,
    )


# --- import ---------------------------------------------------------------------------------------------------------

ROWS = "company_name,domain,brand\nAlfa,alfa.test,Ecom.House\nBeta,beta.test,\nGamma,gamma.test,bad brand\n"


def run(channel, content: str):
    batch = import_service.create_batch(channel, "leads.csv", len(content.encode()), "test")
    return import_service.run_content(batch, content)


def test_csv_connector_reads_the_brand_column():
    assert "brand" in CSV_COLUMNS
    rows = list(parse_rows(io.StringIO(ROWS)))
    assert [row["brand"] for row in rows] == ["Ecom.House", "", "bad brand"]


def test_exact_name_import_sets_the_brand_and_skips_a_bad_one(channel):
    batch = run(channel, ROWS)
    assert dict(Company.objects.values_list("domain", "brand")) == {"alfa.test": "ecom.house", "beta.test": ""}
    assert batch.report == [{"row": 4, "reason": "invalid_brand"}]


def test_import_fills_an_empty_brand_and_keeps_a_set_one(channel):
    new_company(channel, "alfa.test", "entirius")
    new_company(channel, "beta.test")
    run(channel, "domain,brand\nalfa.test,ecom.house\nbeta.test,ecom.house\n")
    assert dict(Company.objects.values_list("domain", "brand")) == {"alfa.test": "entirius", "beta.test": "ecom.house"}


def test_import_slow_path_sets_the_brand_too(channel, monkeypatch):
    def fail(writer):
        raise import_service.IntegrityError("race")

    monkeypatch.setattr(import_service._ChunkWriter, "flush", fail)
    run(channel, "domain,brand\nalfa.test,ecom.house\n")
    assert Company.objects.get().brand == "ecom.house"


def test_exact_brand_column_through_the_api_is_not_ignored(admin_api, channel):
    body = import_now(admin_api, "company_name,domain,brand\nAlfa,alfa.pl,entirius\n", mapping=None).json()
    assert body["ignored_columns"] == []
    assert Company.objects.get().brand == "entirius"


@pytest.mark.parametrize("header", ["brand", "Marka", "Brand", "Marke"])
def test_mapping_proposes_brand_even_for_domain_like_values(admin_api, channel, header):
    column = columns_of(preview(admin_api, f"domain,{header}\nalfa.pl,ecom.house\nbeta.pl,entirius\n"))[header]
    assert column["field"] == "brand"


def test_mapped_import_sets_the_brand(admin_api, channel):
    response = import_now(admin_api, "Domena,Marka\nalfa.pl,ecom.house\n")
    assert response.json()["created_count"] == 1
    assert Company.objects.get().brand == "ecom.house"


def test_brand_is_in_the_preview_field_catalogue(admin_api, channel):
    fields = preview(admin_api, "domain\na.pl\n").json()["fields"]
    assert {"key": "brand", "label": "Brand", "entity": "company", "custom": False, "kind": "text"} in fields


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


def test_draft_asks_for_the_mailbox_of_the_company_brand(shop):
    company_service.set_brand(shop, "ecom.house", actor="test")
    assert draft(shop).call_args.kwargs["mailbox_brand"] == "ecom.house"


def test_draft_of_a_company_without_a_brand_passes_blank(shop):
    kwargs = draft(shop).call_args.kwargs
    assert (kwargs["mailbox_brand"], kwargs["mailbox_user_id"]) == ("", None)


def test_compose_passes_the_company_brand_to_communicator(admin_api, shop):
    CommunicatorChannel.objects.create(idx=shop.channel.idx, label="Europe")
    owner = get_user_model().objects.create_user(username="seller", is_staff=True)
    Company.objects.filter(pk=shop.pk).update(owner=owner, brand="ecom.house")
    piotr = shop.contacts.get(email="piotr@example-shop-4.test")
    with (
        mock.patch.object(outreach_service, "_legal_footer", return_value="footer"),
        mock.patch.object(compose_service, "compose", wraps=compose_service.compose) as spy,
    ):
        body = {"contact_id": piotr.pk, "subject": "Our call", "body": "Hello Piotr"}
        response = admin_api.post(api_url(f"companies/{shop.pk}/compose/"), body, format="json")
    assert response.status_code == 201, response.json()
    kwargs = spy.call_args.kwargs
    assert (kwargs["mailbox_user_id"], kwargs["mailbox_brand"]) == (owner.pk, "ecom.house")
