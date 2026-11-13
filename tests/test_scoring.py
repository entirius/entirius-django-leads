# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_leads.enums import ActivityKind, AttributeKind, RuleAction, RuleOutcome, RuleTrigger
from django_leads.models import Activity, Company, ScoringProfile
from django_leads.services import attribute_service, company_service, rule_service, scoring_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

THRESHOLDS = {"S": 20, "M": 40, "L": 70}
SIZES = [{"value": "micro", "label": "Micro"}, {"value": "big", "label": "Big"}]


@pytest.fixture
def profile(channel) -> ScoringProfile:
    attribute_service.create_definition(
        channel, {"entity": "company", "key": "size", "label": "Size", "kind": AttributeKind.SELECT, "options": SIZES}
    )
    rules = [
        {"field": "lead_type", "op": "in", "value": ["RETAILER"], "points": 30, "label": "Retailer"},
        {"field": "attr.size", "op": "eq", "value": "big", "points": 40, "label": "Big"},
        {"field": "hooks_count", "op": "gte", "value": 1, "points": 10, "label": ""},
    ]
    disqualifiers = [{"field": "industry", "op": "contains", "value": "casino", "label": "Gambling"}]
    fields = {"thresholds": THRESHOLDS, "rules": rules, "disqualifiers": disqualifiers}
    return scoring_service.save_profile(ScoringProfile(channel=channel), fields)


@pytest.mark.parametrize(
    ("points", "segment"), [(70, "S1"), (69, "S2"), (40, "S2"), (39, "S3"), (20, "S3"), (19, "S4"), (-5, "S4")]
)
def test_threshold_boundaries(points, segment):
    assert scoring_service.segment_for(points, THRESHOLDS) == segment


def test_score_sums_matching_rules_on_facts(shop, profile):
    Company.objects.filter(pk=shop.pk).update(lead_type="RETAILER")
    shop.refresh_from_db()
    company_service.update_company(shop, {"attributes": {"size": "big"}})
    result = scoring_service.score(shop)
    assert (result.points, result.segment, result.disqualified) == (80, "S1", False)
    assert result.reasons == ["Retailer: +30", "Big: +40", "hooks_count: +10"]
    assert result.reasons_structured[1] == {"field": "attr.size", "kind": "rule", "points": 40, "label": "Big"}


def test_a_disqualifier_forces_s4(shop, profile):
    company_service.update_company(shop, {"industry": "Online Casino", "attributes": {"size": "big"}})
    result = scoring_service.score(shop)
    assert (result.segment, result.disqualified, result.reasons) == ("S4", True, ["disqualified: Gambling"])


def test_no_profile_scores_none(company):
    assert scoring_service.score(company) is None


def test_profile_validation_refuses_unknown_fields_and_bad_thresholds(admin_api, channel):
    base = {"thresholds": THRESHOLDS, "rules": []}
    unknown = {**base, "rules": [{"field": "attr.colour", "op": "eq", "value": "red", "points": 5}]}
    assert admin_api.post(api_url("scoring-profiles/"), unknown, format="json").status_code == 400
    disordered = {**base, "thresholds": {"S": 50, "M": 40, "L": 70}}
    assert admin_api.post(api_url("scoring-profiles/"), disordered, format="json").status_code == 400
    created = admin_api.post(api_url("scoring-profiles/"), base, format="json")
    assert created.status_code == 201 and created.json()["key"] == "leads.scoring"
    assert admin_api.post(api_url("scoring-profiles/"), base, format="json").json()["error"] == "PROFILE_EXISTS"
    url = api_url(f"scoring-profiles/{created.json()['id']}/")
    patched = admin_api.patch(
        url, {"rules": [{"field": "origin", "op": "eq", "value": "fair", "points": 25}]}, format="json"
    )
    assert patched.status_code == 200 and patched.json()["rules"][0]["points"] == 25
    assert admin_api.delete(url).status_code == 204


def test_score_endpoint_stores_result_and_timeline(admin_api, shop, profile):
    response = admin_api.post(api_url(f"companies/{shop.pk}/score/"))
    assert response.status_code == 200 and response.json()["segment"] == "S4" and response.json()["points"] == 10
    shop.refresh_from_db()
    assert (shop.score_points, shop.score_segment) == (10, "S4") and shop.scored_at is not None
    activity = Activity.objects.get(company=shop, kind=ActivityKind.SCORE)
    assert activity.message == "score S4 (10 points)"
    assert activity.data["reasons"] == ["hooks_count: +10"]
    assert activity.data["reasons_structured"] == [{"field": "hooks_count", "kind": "rule", "points": 10, "label": ""}]


def test_score_endpoint_without_profile_is_409(admin_api, company):
    response = admin_api.post(api_url(f"companies/{company.pk}/score/"))
    assert response.status_code == 409 and response.json()["error"] == "NO_SCORING_PROFILE"


def test_rule_action_score(shop, make_rule, channel):
    rule = make_rule(action=RuleAction.SCORE, template_key="")
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [(run.outcome, run.detail) for run in runs] == [(RuleOutcome.SKIPPED, "skipped: no scoring profile")]
    scoring_service.save_profile(ScoringProfile(channel=channel), {"thresholds": THRESHOLDS, "rules": []})
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [(run.outcome, run.detail) for run in runs] == [(RuleOutcome.FIRED, "score S4 (0 points)")]
    assert Company.objects.get(pk=shop.pk).score_segment == "S4"


def test_list_by_segment_sorted_by_points(admin_api, channel, django_assert_max_num_queries):
    for domain, points, segment in [("a.pl", 50, "S2"), ("b.pl", 90, "S1"), ("c.pl", 75, "S1"), ("d.pl", None, "")]:
        company = company_service.upsert_company(channel, {"domain": domain, "source": "csv"})[0]
        Company.objects.filter(pk=company.pk).update(score_points=points, score_segment=segment)
    with django_assert_max_num_queries(4):  # user, channel, count, page
        response = admin_api.get(api_url("companies/?segment=S1&sort=-score_points"))
    assert [row["domain"] for row in response.json()["results"]] == ["b.pl", "c.pl"]
    ascending = admin_api.get(api_url("companies/?sort=score_points")).json()["results"]
    assert [row["domain"] for row in ascending] == ["a.pl", "c.pl", "b.pl", "d.pl"]  # never scored last
    assert admin_api.get(api_url("companies/?segment=S9")).status_code == 400


def test_a_profile_on_a_deactivated_field_can_still_be_switched_off(profile, channel):
    size = attribute_service.list_definitions(channel).get(key="size")
    attribute_service.update_definition(size, {"is_active": False})
    assert scoring_service.save_profile(profile, {"is_active": False}).is_active is False
    with pytest.raises(ValueError, match="unknown field"):
        scoring_service.save_profile(profile, {"rules": profile.rules})


def test_a_value_stored_for_another_kind_never_matches(shop, profile):
    from django_leads.services import condition_service

    condition = {"field": "attr.size", "op": "gte", "value": "5"}
    assert condition_service.holds(condition, {"attr.size": "micro"}, AttributeKind.NUMBER) is False
