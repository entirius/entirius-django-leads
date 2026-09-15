# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from django_leads.enums import ActivityKind, RuleAction, RuleOutcome, RuleTrigger
from django_leads.models import Activity, Contact, RuleRun, Stage
from django_leads.services import recipient_service, rule_service, stage_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def communicate():
    message = SimpleNamespace(pk=7, status="review_required")
    with (
        mock.patch("django_leads.services.outreach_service.communicate", return_value=message) as patched,
        mock.patch("django_leads.services.outreach_service.render_legal_footer", return_value="footer"),
        mock.patch("django_leads.services.outreach_service.resolve_clause_set"),
    ):
        yield patched


def activity_messages(company, kind: str) -> list[str]:
    return list(Activity.objects.filter(company=company, kind=kind).values_list("message", flat=True))


def test_rule_fires_communicate_for_primary_contact(shop, make_rule, communicate):
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=make_rule().stage)
    assert [run.outcome for run in runs] == [RuleOutcome.FIRED]
    kwargs = communicate.call_args.kwargs
    assert kwargs["recipient"].email == "piotr@example-shop-4.test" and kwargs["requires_review"] is True
    assert kwargs["subject_ref"] == f"leads.Company:{shop.pk}" and kwargs["recipient"].legal_footer == "footer"
    assert kwargs["context"]["hooks"] == shop.hooks
    assert activity_messages(shop, ActivityKind.DRAFT)


def test_L08_rule_skips_contact_without_email_and_does_not_loop(company, make_rule, communicate):
    Contact.objects.create(company=company, email="", first_name="No", legal_basis="consent")
    rule = make_rule(require_hooks=False)
    first = rule_service.evaluate_rules(company, RuleTrigger.STAGE_ENTERED, stage=rule.stage, event="e1")
    again = rule_service.evaluate_rules(company, RuleTrigger.STAGE_ENTERED, stage=rule.stage, event="e1")
    assert [run.outcome for run in first + again] == [RuleOutcome.SKIPPED, RuleOutcome.SKIPPED]
    assert RuleRun.objects.count() == 1
    assert activity_messages(company, ActivityKind.SKIPPED) == ["skipped: no email"]
    communicate.assert_not_called()


def test_L09_cooldown_ignores_skipped_and_cooldown_runs(shop, make_rule, communicate):
    rule = make_rule()
    for outcome in (RuleOutcome.SKIPPED, RuleOutcome.COOLDOWN, RuleOutcome.BLOCKED):
        RuleRun.objects.create(rule=rule, company=shop, outcome=outcome)
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [run.outcome for run in runs] == [RuleOutcome.FIRED]
    assert rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)[0].outcome == "cooldown"


def test_zero_cooldown_is_still_idempotent(shop, make_rule, communicate):
    from django_leads.tasks import evaluate_rules

    rule = make_rule(cooldown_hours=0)
    for event in ("stage:1:a", "stage:1:a", "stage:1:b"):
        evaluate_rules.apply(args=(shop.pk, RuleTrigger.STAGE_ENTERED, rule.stage_id, event))
    assert communicate.call_count == 2
    assert list(RuleRun.objects.values_list("event_key", flat=True).order_by("id")) == ["stage:1:a", "stage:1:b"]


@pytest.mark.parametrize(
    ("require_email", "outcome", "message"),
    [(True, "skipped", "skipped: no email"), (False, "blocked", "blocked: no_eligible_contact")],
)
def test_require_email_is_honoured(company, make_rule, communicate, require_email, outcome, message):
    rule = make_rule(require_hooks=False, require_email=require_email)
    runs = rule_service.evaluate_rules(company, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [run.outcome for run in runs] == [outcome]
    assert message in activity_messages(company, outcome)
    communicate.assert_not_called()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"opt_out_at": timezone.now()}, "opted_out"),
        ({"anonymised_at": timezone.now()}, "anonymised"),
        ({"legal_basis": None}, "no_legal_basis"),
    ],
)
def test_L13_manual_communicate_refuses_opted_out_anonymised_or_no_basis(shop, communicate, admin_api, change, reason):
    contact = shop.contacts.get(is_primary=True)
    Contact.objects.filter(pk=contact.pk).update(**change)
    body = {"template_key": "lead.cold.b2b", "contact_id": contact.pk}
    response = admin_api.post(f"/api/leads/v2/admin/default-europe/companies/{shop.pk}/communicate/", body)
    assert response.status_code == 409 and reason in json.dumps(response.json())
    assert activity_messages(shop, ActivityKind.BLOCKED) == [f"blocked: {reason}"]
    communicate.assert_not_called()


def test_L09_do_not_contact_blocks_before_communicate(shop, make_rule, communicate, admin_api):
    shop.do_not_contact = True
    shop.save(update_fields=["do_not_contact"])
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=make_rule().stage)
    assert [run.outcome for run in runs] == [RuleOutcome.BLOCKED]
    assert activity_messages(shop, ActivityKind.BLOCKED) == ["blocked: do_not_contact"]
    contact = shop.contacts.get(is_primary=True)
    body = {"template_key": "lead.cold.b2b", "contact_id": contact.pk}
    response = admin_api.post(f"/api/leads/v2/admin/default-europe/companies/{shop.pk}/communicate/", body)
    assert response.status_code == 409
    communicate.assert_not_called()


def test_L10_same_stage_twice_within_cooldown_fires_once(
    shop, make_rule, communicate, django_capture_on_commit_callbacks
):
    make_rule()
    new, contacted = (Stage.objects.get(channel=shop.channel, key=key) for key in ("new", "contacted"))
    for stage in (contacted, new, contacted):
        with django_capture_on_commit_callbacks(execute=True):
            stage_service.transition_stage(shop, stage, actor="operator")
    assert list(RuleRun.objects.values_list("outcome", flat=True)) == [RuleOutcome.COOLDOWN, RuleOutcome.FIRED]
    assert activity_messages(shop, ActivityKind.RULE) == ["rule cooldown"]
    assert communicate.call_count == 1


def test_two_rules_on_one_stage_fire_independently(shop, make_rule, communicate):
    stage = make_rule().stage
    make_rule(template_key="lead.cold.shop", order=1)
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=stage)
    assert [run.outcome for run in runs] == [RuleOutcome.FIRED, RuleOutcome.FIRED]


def test_L11_intel_ready_zero_hooks_skips_unless_require_hooks_false(shop, make_rule, communicate):
    shop.hooks = []
    shop.save(update_fields=["hooks"])
    strict = make_rule(trigger=RuleTrigger.INTEL_READY)
    lenient = make_rule(trigger=RuleTrigger.INTEL_READY, require_hooks=False, order=1)
    runs = rule_service.evaluate_rules(shop, RuleTrigger.INTEL_READY)
    assert [(run.rule_id, run.outcome) for run in runs] == [
        (strict.pk, RuleOutcome.SKIPPED),
        (lenient.pk, RuleOutcome.FIRED),
    ]
    assert activity_messages(shop, ActivityKind.SKIPPED) == ["skipped: no hooks"]


def test_contact_without_legal_basis_is_blocked_by_the_gate_only(shop, make_rule, communicate):
    shop.contacts.update(legal_basis=None)
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=make_rule().stage)
    assert [run.outcome for run in runs] == [RuleOutcome.BLOCKED]
    assert activity_messages(shop, ActivityKind.BLOCKED) == ["blocked: no_eligible_contact"]
    assert not activity_messages(shop, ActivityKind.SKIPPED)
    communicate.assert_not_called()


def test_manual_communicate_single_gate_evaluation(shop, communicate, admin_api):
    contact = shop.contacts.get(is_primary=True)
    Contact.objects.filter(pk=contact.pk).update(opt_out_at=timezone.now())
    body = {"template_key": "lead.cold.b2b", "contact_id": contact.pk}
    with mock.patch.object(recipient_service, "block_reason", wraps=recipient_service.block_reason) as gate:
        response = admin_api.post(f"/api/leads/v2/admin/default-europe/companies/{shop.pk}/communicate/", body)
    assert response.status_code == 409 and response.json()["error"] == "NOT_ELIGIBLE"
    assert gate.call_count == 1
    assert activity_messages(shop, ActivityKind.BLOCKED) == ["blocked: opted_out"]
    communicate.assert_not_called()


def test_request_audit_rule_calls_siteintel(shop, make_rule):
    rule = make_rule(action=RuleAction.REQUEST_AUDIT, template_key="")
    with mock.patch("django_leads.services.intel_service.request_audit") as request_audit:
        runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=rule.stage)
    assert [run.outcome for run in runs] == [RuleOutcome.FIRED]
    assert request_audit.call_args.kwargs == {
        "domain_or_url": "example-shop-4.test",
        "channel_idx": "default-europe",
        "requested_by": f"rule:{rule.pk}",
    }


def test_rule_error_becomes_activity_and_never_raises(shop, make_rule, communicate):
    communicate.side_effect = RuntimeError("boom")
    runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=make_rule().stage)
    assert [run.outcome for run in runs] == [RuleOutcome.SKIPPED]
    assert "rule error: RuntimeError" in activity_messages(shop, ActivityKind.RULE)


def test_missing_clause_set_skips_without_communicate(shop, make_rule):
    with mock.patch("django_leads.services.outreach_service.communicate") as communicate:
        runs = rule_service.evaluate_rules(shop, RuleTrigger.STAGE_ENTERED, stage=make_rule().stage)
    assert [run.outcome for run in runs] == [RuleOutcome.SKIPPED]
    assert activity_messages(shop, ActivityKind.SKIPPED) == ["skipped: no clause set"]
    communicate.assert_not_called()
