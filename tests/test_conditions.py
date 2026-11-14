# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from types import SimpleNamespace
from unittest import mock

import pytest

from django_leads.enums import ActivityKind, AttributeEntity, AttributeKind, RuleOutcome, RuleTrigger
from django_leads.models import Activity, Company, RuleRun
from django_leads.services import (
    attribute_service,
    company_service,
    condition_service,
    config_service,
    import_service,
    outreach_service,
    rule_service,
)
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

SIZES = [{"value": "micro", "label": "Micro"}, {"value": "big", "label": "Big"}]
FIELDS = {
    "industry": {"kind": AttributeKind.TEXT, "options": []},
    "hooks_count": {"kind": AttributeKind.NUMBER, "options": []},
    "attr.size": {"kind": AttributeKind.SELECT, "options": SIZES},
    "attr.tags": {"kind": AttributeKind.MULTISELECT, "options": SIZES},
    "attr.b2b": {"kind": AttributeKind.BOOL, "options": []},
    "attr.founded": {"kind": AttributeKind.DATE, "options": []},
    "attr.employees": {"kind": AttributeKind.NUMBER, "options": []},
}
FACTS = {
    "industry": "Garden Tools",
    "hooks_count": 2,
    "attr.size": "micro",
    "attr.tags": ["big"],
    "attr.b2b": True,
    "attr.founded": "2019-03-04",
    "attr.employees": "12.5",
    "attr.none": None,
}


@pytest.mark.parametrize(
    ("field", "op", "value", "expected"),
    [
        ("industry", "eq", "garden tools", True),
        ("industry", "ne", "Garden Tools", False),
        ("industry", "in", ["shoes", "garden tools"], True),
        ("industry", "contains", "TOOL", True),
        ("industry", "contains", "pots", False),
        ("industry", "empty", None, False),
        ("hooks_count", "eq", 2, True),
        ("hooks_count", "ne", "2.0", False),
        ("hooks_count", "in", [1, 3], False),
        ("hooks_count", "gte", 2, True),
        ("hooks_count", "lte", 1, False),
        ("attr.size", "eq", "micro", True),
        ("attr.size", "ne", "big", True),
        ("attr.size", "in", ["big"], False),
        ("attr.size", "empty", False, True),
        ("attr.tags", "contains", "big", True),
        ("attr.tags", "contains", "micro", False),
        ("attr.b2b", "eq", "true", True),
        ("attr.b2b", "ne", True, False),
        ("attr.founded", "gte", "2019-01-01", True),
        ("attr.founded", "lte", "2018-12-31", False),
        ("attr.employees", "gte", "12.5", True),
        ("attr.employees", "lte", 12, False),
        ("attr.employees", "eq", "12.50", True),
    ],
)
def test_every_op_on_every_kind(field, op, value, expected):
    condition = condition_service.check({"field": field, "op": op, "value": value}, FIELDS)
    assert condition_service.holds(condition, FACTS, FIELDS[field]["kind"]) is expected


@pytest.mark.parametrize(("op", "value", "expected"), [("eq", "micro", False), ("ne", "micro", True)])
def test_a_missing_value_holds_only_ne_and_empty(op, value, expected):
    fields = {"attr.none": FIELDS["attr.size"]}
    condition = condition_service.check({"field": "attr.none", "op": op, "value": value}, fields)
    assert condition_service.holds(condition, FACTS, AttributeKind.SELECT) is expected
    empty = condition_service.check({"field": "attr.none", "op": "empty"}, fields)
    assert condition_service.holds(empty, FACTS, AttributeKind.SELECT) is True


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ({"field": "attr.nope", "op": "eq", "value": "x"}, "unknown field"),
        ({"field": "attr.size", "op": "gte", "value": "micro"}, "does not apply"),
        ({"field": "attr.tags", "op": "eq", "value": "big"}, "does not apply"),
        ({"field": "attr.b2b", "op": "contains", "value": "t"}, "does not apply"),
        ({"field": "attr.size", "op": "eq", "value": "huge"}, "must be one of"),
        ({"field": "hooks_count", "op": "in", "value": 3}, "non-empty list"),
        ({"field": "attr.founded", "op": "gte", "value": "yesterday"}, "ISO date"),
        ({"field": "industry", "op": "eq", "value": ""}, "value is required"),
    ],
)
def test_check_refuses(condition, message):
    with pytest.raises(ValueError, match=message):
        condition_service.check(condition, FIELDS)


@pytest.fixture
def size(channel):
    attribute_service.create_definition(
        channel, {"entity": AttributeEntity.COMPANY, "key": "size", "label": "Size", "kind": "select", "options": SIZES}
    )


@pytest.fixture
def communicate():
    message = SimpleNamespace(pk=7, status="review_required")
    with (
        mock.patch("django_leads.services.outreach_service.communicate", return_value=message) as patched,
        mock.patch("django_leads.services.outreach_service.render_legal_footer", return_value="footer"),
        mock.patch("django_leads.services.outreach_service.resolve_clause_set"),
    ):
        yield patched


def test_rule_conditions_are_checked_at_save(admin_api, channel, make_rule, size):
    rule = make_rule()
    body = {"conditions": [{"field": "attr.size", "op": "eq", "value": "micro"}]}
    saved = admin_api.patch(api_url(f"rules/{rule.pk}/"), body, format="json")
    assert saved.status_code == 200 and saved.json()["conditions"] == body["conditions"]
    unknown = {"conditions": [{"field": "attr.colour", "op": "eq", "value": "red"}]}
    assert admin_api.patch(api_url(f"rules/{rule.pk}/"), unknown, format="json").status_code == 400
    bad_op = {"conditions": [{"field": "attr.size", "op": "like", "value": "m"}]}
    assert admin_api.patch(api_url(f"rules/{rule.pk}/"), bad_op, format="json").status_code == 400


def test_a_failing_condition_skips_without_cooldown(shop, make_rule, size, communicate):
    rule = make_rule()
    config_service.save_rule(rule, {"conditions": [{"field": "attr.size", "op": "eq", "value": "micro"}]})
    first = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [run.outcome for run in first] == [RuleOutcome.SKIPPED]
    skipped = Activity.objects.filter(company=shop, kind=ActivityKind.SKIPPED).values_list("message", flat=True)
    assert list(skipped) == ["skipped: condition attr.size"]
    company_service.update_company(shop, {"attributes": {"size": "micro"}})
    again = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [run.outcome for run in again] == [RuleOutcome.FIRED]
    communicate.assert_called_once()


def test_imported_select_attribute_drives_a_rule_for_matching_companies_only(channel, make_rule, size, communicate):
    header = "company_name,domain,email,legal_basis,attr.size"
    content = "\n".join(
        [header, "A,a-shop.pl,jan@a-shop.pl,legitimate_interest,micro", "B,b-shop.pl,ola@b-shop.pl,consent,big"]
    )
    batch = import_service.create_batch(channel, "leads.csv", len(content), "test")
    import_service.run_content(batch, content)
    rule = make_rule(require_hooks=False)
    config_service.save_rule(rule, {"conditions": [{"field": "attr.size", "op": "in", "value": ["micro"]}]})
    for company in Company.objects.order_by("domain"):
        rule_service.evaluate_rules(company, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    outcomes = dict(RuleRun.objects.values_list("company__domain", "outcome"))
    assert outcomes == {"a-shop.pl": RuleOutcome.FIRED, "b-shop.pl": RuleOutcome.SKIPPED}
    assert communicate.call_args.kwargs["recipient"].email == "jan@a-shop.pl"


def test_template_context_carries_attributes_and_a_missing_key_renders_empty(channel, shop, size):
    from django_communicator.services.render_service import render

    attribute_service.create_definition(
        channel, {"entity": "contact", "key": "role", "label": "Role", "kind": AttributeKind.TEXT}
    )
    contact = shop.contacts.get(is_primary=True)
    context = outreach_service._context(shop, contact)
    assert context["attr"] == {"size": ""} and context["contact_attr"] == {"role": ""}
    assert context["domain"] == shop.domain and context["hooks"] == shop.hooks  # flat keys unchanged
    company_service.update_company(shop, {"attributes": {"size": "micro"}})
    context = outreach_service._context(shop, contact)
    assert render("{attr[size]}/{attr[undefined]}/{contact_attr[role]}", context) == "micro//"
