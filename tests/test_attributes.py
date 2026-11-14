# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest
from django.db import connection

from django_leads.enums import AttributeEntity, AttributeKind
from django_leads.models import AttributeDefinition, Company, Contact
from django_leads.services import attribute_service, company_service, contact_service, fact_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

SIZES = [{"value": "micro", "label": "Micro"}, {"value": "big", "label": "Big"}]


def define(channel, key: str, kind: str, **fields) -> AttributeDefinition:
    values = {"entity": AttributeEntity.COMPANY, "key": key, "label": key.title(), "kind": kind, **fields}
    return attribute_service.create_definition(channel, values)


@pytest.fixture
def every_kind(channel) -> None:
    for key, kind in [
        ("note", AttributeKind.TEXT),
        ("employees", AttributeKind.NUMBER),
        ("b2b", AttributeKind.BOOL),
        ("founded", AttributeKind.DATE),
        ("size", AttributeKind.SELECT),
        ("tags", AttributeKind.MULTISELECT),
        ("shop", AttributeKind.URL),
    ]:
        define(channel, key, kind, options=SIZES if kind in attribute_service.SELECT_KINDS else [])


@pytest.mark.parametrize(
    ("key", "value", "stored"),
    [
        ("note", "<b>hi</b>", "<b>hi</b>"),
        ("employees", 12.50, "12.5"),
        ("employees", "007", "7"),
        ("b2b", "yes", True),
        ("b2b", False, False),
        ("founded", "2019-03-04", "2019-03-04"),
        ("size", "micro", "micro"),
        ("tags", "micro, big,micro", ["micro", "big"]),
        ("tags", ["big"], ["big"]),
        ("shop", "https://shop.example.test/x", "https://shop.example.test/x"),
    ],
)
def test_every_kind_accepts_and_normalises(channel, every_kind, key, value, stored):
    assert attribute_service.clean(channel, AttributeEntity.COMPANY, {key: value}) == {key: stored}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("note", "x" * 2001),
        ("note", 5),
        ("employees", "many"),
        ("employees", True),
        ("employees", "NaN"),
        ("b2b", "maybe"),
        ("founded", "04.03.2019"),
        ("size", "huge"),
        ("tags", ["micro", "huge"]),
        ("shop", "ftp://shop.example.test"),
        ("shop", "javascript:alert(1)"),
        ("unknown", "x"),
    ],
)
def test_every_kind_refuses_a_bad_value(channel, every_kind, key, value):
    with pytest.raises(attribute_service.InvalidAttribute) as error:
        attribute_service.clean(channel, AttributeEntity.COMPANY, {key: value})
    assert error.value.key == key


@pytest.mark.parametrize("key", ["note", "employees", "b2b", "founded", "size", "tags", "shop"])
def test_null_removes_the_key_of_every_kind(channel, every_kind, key):
    current = {key: "kept-by-test", "other": 1}
    assert attribute_service.apply(channel, AttributeEntity.COMPANY, current, {key: None}) == {"other": 1}


def test_inactive_key_is_refused(channel):
    define(channel, "size", AttributeKind.SELECT, options=SIZES, is_active=False)
    with pytest.raises(attribute_service.InvalidAttribute):
        attribute_service.clean(channel, AttributeEntity.COMPANY, {"size": "micro"})


def test_required_enforced_on_create_and_on_a_patch_touching_the_key_only(channel):
    define(channel, "size", AttributeKind.SELECT, options=SIZES, required=True)
    define(channel, "note", AttributeKind.TEXT)
    with pytest.raises(attribute_service.InvalidAttribute, match="size: is required"):
        attribute_service.initial(channel, AttributeEntity.COMPANY, {"note": "x"})
    assert attribute_service.apply(channel, AttributeEntity.COMPANY, {}, {"note": "x"}) == {"note": "x"}
    with pytest.raises(attribute_service.InvalidAttribute):
        attribute_service.apply(channel, AttributeEntity.COMPANY, {"size": "big"}, {"size": None})


def test_definition_rules(channel, company):
    with pytest.raises(ValueError, match="built-in"):
        define(channel, "industry", AttributeKind.TEXT)
    with pytest.raises(ValueError, match="options are required"):
        define(channel, "size", AttributeKind.SELECT)
    with pytest.raises(ValueError, match="only allowed"):
        define(channel, "note", AttributeKind.TEXT, options=SIZES)
    size = define(channel, "size", AttributeKind.SELECT, options=SIZES)
    with pytest.raises(attribute_service.DefinitionExists):
        define(channel, "size", AttributeKind.TEXT)
    assert attribute_service.update_definition(size, {"kind": AttributeKind.MULTISELECT}).kind == "multiselect"
    company_service.update_company(company, {"attributes": {"size": ["micro"]}})
    with pytest.raises(attribute_service.DefinitionInUse):
        attribute_service.update_definition(size, {"kind": AttributeKind.SELECT})
    assert attribute_service.update_definition(size, {"is_active": False}).is_active is False
    with pytest.raises(attribute_service.DefinitionInUse):
        attribute_service.delete_definition(size)


def test_attributes_api_crud_and_conflicts(admin_api, channel, company):
    body = {"entity": "company", "key": "size", "label": "Size", "kind": "select", "options": SIZES}
    created = admin_api.post(api_url("attributes/"), body, format="json")
    assert created.status_code == 201 and created.json()["options"] == SIZES
    assert admin_api.post(api_url("attributes/"), body, format="json").json()["error"] == "ATTRIBUTE_EXISTS"
    assert admin_api.post(api_url("attributes/"), {**body, "key": "Bad Key"}, format="json").status_code == 400
    assert admin_api.post(api_url("attributes/"), {**body, "key": "x", "options": []}, format="json").status_code == 400
    url = api_url(f"attributes/{created.json()['id']}/")
    assert admin_api.patch(url, {"key": "other"}, format="json").status_code == 400
    company_service.update_company(company, {"attributes": {"size": "big"}})
    refused = admin_api.patch(url, {"kind": "text", "options": []}, format="json")
    assert refused.status_code == 409 and refused.json()["error"] == "ATTRIBUTE_IN_USE"
    assert admin_api.delete(url).status_code == 409
    assert admin_api.get(api_url("attributes/?entity=contact")).json() == {"results": []}
    assert admin_api.patch(url, {"is_active": False}, format="json").json()["is_active"] is False


def test_company_create_and_patch_take_attributes(admin_api, channel):
    define(channel, "size", AttributeKind.SELECT, options=SIZES, required=True)
    define(channel, "note", AttributeKind.TEXT)
    missing = admin_api.post(api_url("companies/"), {"domain": "lesna.pl"}, format="json")
    assert missing.status_code == 400 and "attributes" in str(missing.json())
    body = {"domain": "lesna.pl", "attributes": {"size": "micro", "note": "hi"}}
    created = admin_api.post(api_url("companies/"), body, format="json")
    assert created.status_code == 201 and created.json()["attributes"] == {"size": "micro", "note": "hi"}
    url = api_url(f"companies/{created.json()['id']}/")
    patched = admin_api.patch(url, {"attributes": {"note": None, "size": "big"}}, format="json")
    assert patched.json()["attributes"] == {"size": "big"}
    bad = admin_api.patch(url, {"attributes": {"size": "huge"}}, format="json")
    assert bad.status_code == 400 and bad.json()["error"]
    assert admin_api.patch(url, {"attributes": None}, format="json").status_code == 400
    assert Company.objects.get().attributes == {"size": "big"}


def test_contact_create_and_patch_take_attributes(admin_api, channel, company):
    attribute_service.create_definition(
        channel, {"entity": "contact", "key": "role", "label": "Role", "kind": AttributeKind.TEXT}
    )
    body = {"company_id": company.pk, "email": "jan@ogrod.pl", "attributes": {"role": "buyer"}}
    created = admin_api.post(api_url("contacts/"), body, format="json")
    assert created.status_code == 201 and created.json()["attributes"] == {"role": "buyer"}
    url = api_url(f"contacts/{created.json()['id']}/")
    assert admin_api.patch(url, {"attributes": {"role": None}}, format="json").json()["attributes"] == {}
    assert admin_api.patch(url, {"attributes": {"size": "x"}}, format="json").status_code == 400


def test_company_list_filters_on_list_attributes(admin_api, channel, company, django_assert_max_num_queries):
    define(channel, "size", AttributeKind.SELECT, options=SIZES, show_in_list=True)
    define(channel, "b2b", AttributeKind.BOOL, show_in_list=True)
    define(channel, "note", AttributeKind.TEXT)
    company_service.update_company(company, {"attributes": {"size": "micro", "b2b": True, "note": "x"}})
    other = company_service.upsert_company(channel, {"domain": "inny.pl", "source": "csv"})[0]
    company_service.update_company(other, {"attributes": {"size": "big", "b2b": True}})
    with django_assert_max_num_queries(5):  # user, channel, definitions, count, page
        response = admin_api.get(api_url("companies/?attr.size=micro&attr.b2b=true"))
    assert [row["domain"] for row in response.json()["results"]] == ["ogrod.pl"]
    assert response.json()["results"][0]["attributes"]["size"] == "micro"
    assert admin_api.get(api_url("companies/?attr.note=x")).status_code == 400  # not show_in_list
    assert admin_api.get(api_url("companies/?attr.size=huge")).status_code == 400


@pytest.mark.skipif(connection.vendor != "postgresql", reason="GIN index is PostgreSQL only")
def test_attribute_filter_hits_the_gin_index(channel):
    define(channel, "size", AttributeKind.SELECT, options=SIZES, show_in_list=True)
    # The attribute condition alone: with the channel filter the planner may prefer the (channel, domain) index.
    companies = company_service.filter_attributes(channel, Company.objects.all(), {"size": "micro"})
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        sql, params = companies.query.sql_with_params()
        cursor.execute(f"EXPLAIN {sql}", params)
        plan = "\n".join(row[0] for row in cursor.fetchall())
    assert "leads_company_attributes_gin" in plan


def test_fields_lists_builtins_and_attributes(admin_api, channel):
    define(channel, "size", AttributeKind.SELECT, options=SIZES)
    define(channel, "gone", AttributeKind.TEXT, is_active=False)
    attribute_service.create_definition(
        channel, {"entity": "contact", "key": "role", "label": "Role", "kind": AttributeKind.TEXT}
    )
    results = admin_api.get(api_url("fields/")).json()["results"]
    by_name = {row["name"]: row for row in results}
    assert list(by_name)[: len(fact_service.BUILTIN_KINDS)] == list(fact_service.BUILTIN_KINDS)
    assert by_name["attr.size"] == {"name": "attr.size", "kind": "select", "label": "Size", "options": SIZES}
    assert "attr.gone" not in by_name and "contact.attr.role" not in by_name
    assert {"value": "RETAILER", "label": "Retailer"} in by_name["lead_type"]["options"]
    assert [option["value"] for option in by_name["score_segment"]["options"]] == ["S1", "S2", "S3", "S4"]


def test_facts_read_builtins_and_attributes(channel, shop):
    define(channel, "size", AttributeKind.SELECT, options=SIZES)
    attribute_service.create_definition(
        channel, {"entity": "contact", "key": "role", "label": "Role", "kind": AttributeKind.TEXT}
    )
    contact = shop.contacts.get(is_primary=True)
    contact_service.update_contact(contact, {"attributes": {"role": "buyer"}}, actor="test")
    values = fact_service.facts(shop, contact)
    assert values["hooks_count"] == 1 and values["emails_count"] == 2 and values["stage"] == "new"
    assert values["attr.size"] is None and values["contact.attr.role"] == "buyer"
    assert "contact.attr.role" not in fact_service.facts(shop)


def test_contact_attributes_are_personal_data(channel, company, django_capture_on_commit_callbacks):
    from django_leads.gdpr.hooks import gdpr_erase, gdpr_export
    from django_leads.services import retention_service

    attribute_service.create_definition(
        channel, {"entity": "contact", "key": "role", "label": "Role", "kind": AttributeKind.TEXT}
    )
    row = {"email": "jan@ogrod.pl", "source": "manual", "attributes": {"role": "buyer"}}
    define(channel, "size", AttributeKind.SELECT, options=SIZES)
    jan = contact_service.create_contact(company, row, actor="test")
    ewa = contact_service.create_contact(company, {**row, "email": "ewa@ogrod.pl"}, actor="test")
    company_service.update_company(company, {"attributes": {"size": "big"}})
    assert gdpr_export("jan@ogrod.pl")["Contact"][0]["attributes"] == {"role": "buyer"}
    with django_capture_on_commit_callbacks(execute=True):
        gdpr_erase("jan@ogrod.pl")
        retention_service.anonymise_contact(ewa)
    assert list(Contact.objects.filter(pk__in=[jan.pk, ewa.pk]).values_list("attributes", flat=True)) == [{}, {}]
    assert Company.objects.get(pk=company.pk).attributes == {"size": "big"}  # company data stays


def test_a_number_is_capped_in_digits(channel, every_kind):
    with pytest.raises(attribute_service.InvalidAttribute, match="digits"):
        attribute_service.clean(channel, AttributeEntity.COMPANY, {"employees": "1e999999999"})
    assert attribute_service.clean(channel, AttributeEntity.COMPANY, {"employees": "1e3"}) == {"employees": "1000"}


def test_an_attribute_named_by_a_rule_keeps_its_kind_and_cannot_be_deleted(channel, make_rule):
    from django_leads.services import config_service

    size = define(channel, "size", AttributeKind.SELECT, options=SIZES)
    config_service.save_rule(make_rule(), {"conditions": [{"field": "attr.size", "op": "eq", "value": "micro"}]})
    with pytest.raises(attribute_service.DefinitionInUse, match="rule or scoring profile"):
        attribute_service.update_definition(size, {"kind": AttributeKind.TEXT, "options": []})
    with pytest.raises(attribute_service.DefinitionInUse):
        attribute_service.delete_definition(size)
