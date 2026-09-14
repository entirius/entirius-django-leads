# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stage rules: conditions in a fixed order, a RuleRun per evaluation, cooldown per (rule, company)."""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from django_leads.enums import ActivityKind, RuleAction, RuleOutcome, RuleTrigger
from django_leads.models import Company, RuleRun, Stage, StageRule
from django_leads.services import activity_service

logger = logging.getLogger(__name__)


def evaluate_rules(company: Company, trigger: str, *, stage: Stage | None = None) -> list[RuleRun]:
    rules = StageRule.objects.filter(channel_id=company.channel_id, trigger=trigger, is_active=True)
    if trigger == RuleTrigger.STAGE_ENTERED:
        rules = rules.filter(stage=stage)
    return [_run(rule, company) for rule in rules.order_by("order", "id")]


def _run(rule: StageRule, company: Company) -> RuleRun:
    """Never raises: an unexpected error is logged and becomes Activity `rule` plus a skipped run. The company row
    lock serialises concurrent evaluations (worker tasks), so the cooldown check sees the other run."""
    try:
        with transaction.atomic():
            Company.objects.select_for_update().filter(pk=company.pk).first()
            return _record_run(rule, company, *_evaluate(rule, company))
    except Exception as error:
        logger.exception("leads: rule %s failed for company %s", rule.pk, company.pk)
        message = f"rule error: {type(error).__name__}"
        activity_service.record(company, ActivityKind.RULE, message, data={"rule_id": rule.pk})
        return _record_run(rule, company, RuleOutcome.SKIPPED, message)


def _record_run(rule: StageRule, company: Company, outcome: str, detail: str) -> RuleRun:
    return RuleRun.objects.create(rule=rule, company=company, outcome=outcome, detail=detail[:255])


def _evaluate(rule: StageRule, company: Company) -> tuple[str, str]:
    from django_leads.services import intel_service, outreach_service, recipient_service

    actor = f"rule:{rule.pk}"
    if company.do_not_contact:
        return _stop(company, rule, RuleOutcome.BLOCKED, ActivityKind.BLOCKED, "blocked: do_not_contact")
    if _in_cooldown(rule, company):
        return _stop(company, rule, RuleOutcome.COOLDOWN, ActivityKind.RULE, "rule cooldown")
    if rule.action == RuleAction.REQUEST_AUDIT:
        intel_service.request_audit_for(company, actor=actor)
        return RuleOutcome.FIRED, "audit requested"
    if rule.require_hooks and not company.hooks:
        return _stop(company, rule, RuleOutcome.SKIPPED, ActivityKind.SKIPPED, "skipped: no hooks")
    contact, _ = recipient_service.pick_recipient(company, rule)
    if contact is None:
        return _stop(company, rule, RuleOutcome.SKIPPED, ActivityKind.SKIPPED, "skipped: no email")
    if rule.require_legal_basis and not contact.legal_basis:
        return _stop(company, rule, RuleOutcome.SKIPPED, ActivityKind.SKIPPED, "skipped: no legal basis")
    message = outreach_service.request_draft(company, contact, rule.template_key, actor=actor)
    if message is None:
        return RuleOutcome.SKIPPED, "no draft"
    return RuleOutcome.FIRED, f"message {message.pk} {message.status}"


def _in_cooldown(rule: StageRule, company: Company) -> bool:
    since = timezone.now() - timedelta(hours=rule.cooldown_hours)
    return RuleRun.objects.filter(rule=rule, company=company, fired_at__gt=since).exists()


def _stop(company: Company, rule: StageRule, outcome: str, kind: str, message: str) -> tuple[str, str]:
    activity_service.record(company, kind, message, data={"rule_id": rule.pk}, actor=f"rule:{rule.pk}")
    return outcome, message
