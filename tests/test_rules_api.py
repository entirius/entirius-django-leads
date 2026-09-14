# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from unittest import mock

import pytest

from django_leads.models import Stage
from tests.conftest import api_url

pytestmark = pytest.mark.django_db


def test_rule_crud_enforces_conditionals(admin_api, channel):
    contacted = Stage.objects.get(channel=channel, key="contacted")
    assert admin_api.post(api_url("rules/"), {"trigger": "stage_entered"}, format="json").status_code == 400
    body = {"trigger": "stage_entered", "stage_id": contacted.pk, "template_key": "lead.cold.b2b"}
    created = admin_api.post(api_url("rules/"), body, format="json")
    assert created.status_code == 201 and created.json()["contact_strategy"] == "primary"
    rule_url = api_url(f"rules/{created.json()['id']}/")
    assert admin_api.patch(rule_url, {"template_key": ""}, format="json").status_code == 400
    assert admin_api.patch(rule_url, {"channel": 5}, format="json").status_code == 400
    assert admin_api.patch(rule_url, {"cooldown_hours": 0}, format="json").json()["cooldown_hours"] == 0
    assert admin_api.get(api_url("rules/")).json()["results"][0]["id"] == created.json()["id"]
    assert admin_api.delete(rule_url).status_code == 204


def test_profiles_never_list_the_prompt(admin_api, channel):
    body = {"key": "leads.analysis", "prompt_text": "secret {company_name}", "model": "fake-chat"}
    created = admin_api.post(api_url("analysis-profiles/"), body, format="json")
    assert created.status_code == 201 and created.json()["prompt_text"] == body["prompt_text"]
    assert admin_api.post(api_url("analysis-profiles/"), body, format="json").status_code == 409
    listed = admin_api.get(api_url("analysis-profiles/")).json()["results"]
    assert listed and "prompt_text" not in listed[0]
    assert (
        admin_api.post(
            api_url("recipient-profiles/"), {**body, "key": "leads.pick_recipient"}, format="json"
        ).status_code
        == 201
    )
    assert "prompt_text" not in admin_api.get(api_url("recipient-profiles/")).json()["results"][0]


def test_admin_endpoints_refuse_customers(customer_api, channel):
    assert customer_api.get(api_url("rules/")).status_code == 403
    assert customer_api.post(api_url("test/evaluate/"), {}, format="json").status_code == 403


def test_dev_evaluate_runs_rules_and_rotate_now(admin_api, shop, make_rule):
    make_rule(require_hooks=False, template_key="lead.cold.b2b")
    body = {"company_id": shop.pk, "trigger": "stage_entered", "stage_key": "contacted"}
    with mock.patch("django_leads.services.outreach_service.request_draft", return_value=None):
        response = admin_api.post(api_url("test/evaluate/"), body, format="json")
    assert response.status_code == 200 and response.json()["runs"][0]["outcome"] == "skipped"
    assert admin_api.post(api_url("test/rotate-now/")).json() == {"rotated": 0}


def test_dev_endpoints_404_outside_development(admin_api, shop, settings):
    settings.ENVIRONMENT = "production"
    assert admin_api.post(api_url("test/rotate-now/")).status_code == 404


def test_manual_communicate_rejects_foreign_contact(admin_api, shop, company):
    foreign = company.contacts.create(email="x@ogrod.pl", source="csv")
    body = {"template_key": "lead.cold.b2b", "contact_id": foreign.pk}
    assert admin_api.post(api_url(f"companies/{shop.pk}/communicate/"), body, format="json").status_code == 400
