# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest

from django_leads.enums import ActivityKind, RuleAction, RuleOutcome, RuleTrigger
from django_leads.models import Activity, ScoringProfile, Stage, StageRule
from django_leads.services import rule_service, scoring_service
from tests.conftest import api_url

PROFILE = {
    "key": "leads.scoring",
    "thresholds": {"S": 20, "M": 40, "L": 70},
    "rules": [
        {"field": "lead_type", "op": "in", "value": ["RETAILER", "WHOLESALE"], "points": 30, "label": "retail"},
        {"field": "hooks_count", "op": "gte", "value": 1, "points": 25, "label": "has hooks"},
        {"field": "emails_count", "op": "gte", "value": 2, "points": 20, "label": "two emails"},
    ],
    "disqualifiers": [{"field": "domain", "op": "contains", "value": "blocked", "label": "blocked domain"}],
}


@pytest.fixture
def profile(channel) -> ScoringProfile:
    return ScoringProfile.objects.create(channel=channel, **PROFILE)


def test_segment_from_points_and_reasons(shop, profile):
    shop.lead_type = "RETAILER"
    result = scoring_service.score(shop)
    assert (result.points, result.segment) == (75, "S1")
    assert result.reasons == ["retail: +30", "has hooks: +25", "two emails: +20"]
    assert [r["label"] for r in result.reasons_structured] == ["retail", "has hooks", "two emails"]
    assert result.reasons_structured[0]["label_key"] == "leads.scoring.rule.lead_type"
    shop.lead_type = "MANUFACTURER"
    again = scoring_service.score(shop)
    assert (again.points, again.segment) == ((45, "S2"))


@pytest.mark.parametrize(("points", "segment"), [(0, "S4"), (19, "S4"), (20, "S3"), (40, "S2"), (70, "S1")])
def test_threshold_edges(points, segment):
    assert scoring_service.segment_for(points, {"S": 20, "M": 40, "L": 70}) == segment


def test_disqualifier_forces_s4(shop, profile):
    shop.lead_type, shop.domain = "RETAILER", "blocked-shop.test"
    result = scoring_service.score(shop)
    assert (result.segment, result.disqualified, result.points) == ("S4", True, 75)
    assert result.reasons == ["disqualified: blocked domain"]
    assert result.reasons_structured == [
        {"code": "domain", "kind": "disqualifier", "points": 0, "label": "blocked domain",
         "label_key": "leads.scoring.disqualifier.domain"}
    ]  # fmt: skip


def test_no_profile_or_inactive_profile_scores_nothing(shop, profile):
    assert scoring_service.score(shop) is not None
    ScoringProfile.objects.update(is_active=False)
    assert scoring_service.score_and_record(shop, actor="t") is None
    assert not Activity.objects.filter(kind=ActivityKind.SCORE).exists()


def test_score_is_written_to_the_timeline(shop, profile):
    scoring_service.score_and_record(shop, actor="rule:1")
    entry = Activity.objects.get(company=shop, kind=ActivityKind.SCORE)
    assert entry.message == "score S2 (45 points)" and entry.actor == "rule:1"
    assert entry.data["segment"] == "S2" and entry.data["profile_id"] == profile.pk and entry.data["reasons"]


def test_score_rule_fires_on_stage_entered(channel, shop, profile):
    stage = Stage.objects.get(channel=channel, key="contacted")
    StageRule.objects.create(
        channel=channel, trigger=RuleTrigger.STAGE_ENTERED, stage=stage, action=RuleAction.SCORE, require_hooks=False
    )
    rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=stage, event="s1")
    assert Activity.objects.filter(company=shop, kind=ActivityKind.SCORE).count() == 1
    run = shop.rule_runs.get()
    assert run.outcome == RuleOutcome.FIRED and run.detail.startswith("scored S")


def test_score_rule_without_profile_is_skipped(channel, shop):
    rule = StageRule.objects.create(
        channel=channel, trigger=RuleTrigger.INTEL_READY, action=RuleAction.SCORE, require_hooks=False
    )
    (run,) = rule_service.evaluate_rules(shop, RuleTrigger.INTEL_READY, event="e1")
    assert run.rule_id == rule.pk and run.outcome == RuleOutcome.SKIPPED and run.detail == "skipped: no scoring profile"


def test_profile_api_crud(admin_api, channel):
    url = api_url("scoring-profiles/")
    created = admin_api.post(url, PROFILE, format="json")
    assert created.status_code == 201 and created.json()["thresholds"] == PROFILE["thresholds"]
    pk = created.json()["id"]
    assert admin_api.post(url, PROFILE, format="json").status_code == 409
    assert admin_api.post(url, PROFILE, format="json").json()["error"] == "PROFILE_EXISTS"
    assert len(admin_api.get(url).json()["results"]) == 1
    patched = admin_api.patch(
        f"{url}{pk}/", {"is_active": False, "thresholds": {"S": 1, "M": 2, "L": 3}}, format="json"
    )
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    assert admin_api.get(f"{url}{pk}/").json()["thresholds"] == {"S": 1, "M": 2, "L": 3}
    assert admin_api.delete(f"{url}{pk}/").status_code == 204
    assert admin_api.get(f"{url}{pk}/").status_code == 404


@pytest.mark.parametrize(
    "patch",
    [
        {"thresholds": {"S": 50, "M": 40, "L": 70}},
        {"rules": [{"field": "lead_type", "op": "gte", "value": 1, "points": 5}]},
        {"rules": [{"field": "hooks_count", "op": "in", "value": ["a"], "points": 5}]},
        {"rules": [{"field": "nope", "op": "eq", "value": "x", "points": 5}]},
        {"rules": [{"field": "industry", "op": "eq", "value": "x", "points": 500}]},
        {"disqualifiers": [{"field": "platform", "op": "in", "value": "wix"}]},
    ],
)
def test_profile_api_validates_shapes(admin_api, channel, patch):
    assert admin_api.post(api_url("scoring-profiles/"), {**PROFILE, **patch}, format="json").status_code == 400


def test_profile_api_needs_staff(customer_api, channel):
    assert customer_api.get(api_url("scoring-profiles/")).status_code == 403


def test_score_endpoint(admin_api, shop, profile):
    shop.lead_type = "RETAILER"
    shop.save(update_fields=["lead_type"])
    response = admin_api.post(api_url(f"companies/{shop.pk}/score/"))
    assert response.status_code == 200
    body = response.json()
    assert body | {"reasons": None, "reasons_structured": None} == {
        "segment": "S1", "points": 75, "disqualified": False, "reasons": None, "profile_id": profile.pk,
        "reasons_structured": None,
    }  # fmt: skip
    assert [(r["code"], r["points"], r["kind"]) for r in body["reasons_structured"]] == [
        ("lead_type", 30, "rule"), ("hooks_count", 25, "rule"), ("emails_count", 20, "rule"),
    ]  # fmt: skip
    assert len(body["reasons_structured"]) == len(body["reasons"])
    assert Activity.objects.filter(company=shop, kind=ActivityKind.SCORE).count() == 1


def test_score_endpoint_without_profile_is_409(admin_api, shop):
    response = admin_api.post(api_url(f"companies/{shop.pk}/score/"))
    assert response.status_code == 409 and response.json()["error"] == "NO_SCORING_PROFILE"


def test_rule_api_accepts_score_action(admin_api, channel):
    body = {"trigger": "intel_ready", "action": "score"}
    assert admin_api.post(api_url("rules/"), body, format="json").status_code == 201


def test_empty_profile_scores_by_thresholds_only(channel, company):
    ScoringProfile.objects.create(channel=channel, thresholds={"S": 0, "M": 10, "L": 20})
    result = scoring_service.score(company)  # no hooks, no contacts, no rules
    assert (result.points, result.segment, result.reasons) == (0, "S3", [])
