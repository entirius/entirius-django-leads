# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stage rules: conditions in a fixed order, a RuleRun per evaluation, cooldown per (rule, company).

Three phases per rule: (a) under the company row lock the company is re-read, the conditions checked and the run
written — `claimed` when outreach follows; (b) recipient pick (toolbox) among gate-eligible contacts outside any
transaction, then `request_draft` (its own short transaction: gate on locked rows + draft); (c) the run completed. A redelivered evaluation of the same event finds its run and pays nothing again.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from django_leads.enums import ActivityKind, ClaimState, RuleAction, RuleOutcome, RuleTrigger
from django_leads.models import Company, RuleRun, Stage, StageRule
from django_leads.services import activity_service, claim_service

logger = logging.getLogger(__name__)


def evaluate_rules(company: Company, trigger: str, *, stage: Stage | None = None, event: str = "") -> list[RuleRun]:
    """`event` identifies the trigger occurrence (claim key with rule and company); empty = no dedupe."""
    rules = StageRule.objects.filter(channel_id=company.channel_id, trigger=trigger, is_active=True)
    if trigger == RuleTrigger.STAGE_ENTERED:
        rules = rules.filter(stage=stage)
    return [_run(rule, company, event) for rule in rules.order_by("order", "id")]


def _run(rule: StageRule, company: Company, event: str) -> RuleRun:
    """Never raises: an unexpected error is logged and becomes Activity `rule` plus a skipped run."""
    run = None
    try:
        run, created = _claim(rule, company, event)
        if created and run.state == ClaimState.CLAIMED:
            _complete(run, *_outreach(rule, run.company))
        return run
    except Exception as error:
        logger.exception("leads: rule %s failed for company %s", rule.pk, company.pk)
        message = f"rule error: {type(error).__name__}"
        activity_service.record(company, ActivityKind.RULE, message, data={"rule_id": rule.pk})
        if run is None:
            return _create_run(rule, company, event, RuleOutcome.SKIPPED, message)
        return _complete(run, RuleOutcome.SKIPPED, message, state=ClaimState.FAILED)


def _claim(rule: StageRule, company: Company, event: str) -> tuple[RuleRun, bool]:
    with transaction.atomic():
        locked = Company.objects.select_for_update().get(pk=company.pk)
        existing = _existing_run(rule, locked, event)
        if existing is not None:
            return existing, False
        outcome, detail = _check(rule, locked)
        run = _create_run(rule, locked, event, outcome, detail)
        return run, True


def _existing_run(rule: StageRule, company: Company, event: str) -> RuleRun | None:
    run = RuleRun.objects.filter(rule=rule, company=company, event_key=event).first() if event else None
    if run is not None and run.state == ClaimState.CLAIMED and run.fired_at < claim_service.stale_before():
        _complete(run, RuleOutcome.SKIPPED, claim_service.OUTCOME_UNKNOWN, state=ClaimState.FAILED)
    return run


def _create_run(rule: StageRule, company: Company, event: str, outcome: str | None, detail: str) -> RuleRun:
    state = ClaimState.CLAIMED if outcome is None else ClaimState.DONE
    fields = {"outcome": outcome or "", "detail": detail[:255], "state": state, "event_key": event}
    return RuleRun.objects.create(rule=rule, company=company, **fields)


def _complete(run: RuleRun, outcome: str, detail: str, *, state: str = ClaimState.DONE) -> RuleRun:
    run.outcome, run.detail, run.state = outcome, detail[:255], state
    run.save(update_fields=["outcome", "detail", "state"])
    return run


def _check(rule: StageRule, company: Company) -> tuple[str | None, str]:
    """Conditions on the locked row; `None` outcome = outreach follows outside the lock."""
    from django_leads.services import intel_service, recipient_service

    if company.do_not_contact:
        return _stop(company, rule, RuleOutcome.BLOCKED, ActivityKind.BLOCKED, "blocked: do_not_contact")
    if _in_cooldown(rule, company):
        return _stop(company, rule, RuleOutcome.COOLDOWN, ActivityKind.RULE, "rule cooldown")
    if rule.action == RuleAction.REQUEST_AUDIT:
        intel_service.request_audit_for(company, actor=f"rule:{rule.pk}")
        return RuleOutcome.FIRED, "audit requested"
    if rule.require_hooks and not company.hooks:
        return _stop(company, rule, RuleOutcome.SKIPPED, ActivityKind.SKIPPED, "skipped: no hooks")
    if rule.require_email and not recipient_service.candidates(company):
        return _stop(company, rule, RuleOutcome.SKIPPED, ActivityKind.SKIPPED, "skipped: no email")
    return None, "claimed"


def _outreach(rule: StageRule, company: Company) -> tuple[str, str]:
    """No lock held: the pick may call the toolbox; `request_draft` takes the outreach gate on locked rows."""
    from django_leads.services import outreach_service, recipient_service

    contact, _ = recipient_service.pick_recipient(company, rule)
    if contact is None:
        return _stop(company, rule, RuleOutcome.BLOCKED, ActivityKind.BLOCKED, "blocked: no_eligible_contact")
    result = outreach_service.request_draft(company, contact, rule.template_key, actor=f"rule:{rule.pk}")
    if isinstance(result, outreach_service.Blocked):
        return RuleOutcome.BLOCKED, f"blocked: {result.reason}"
    if result is None:
        return RuleOutcome.SKIPPED, "no draft"
    return RuleOutcome.FIRED, f"message {result.pk} {result.status}"


def _in_cooldown(rule: StageRule, company: Company) -> bool:
    """Only fired runs and runs still in flight count — skipped, blocked and cooldown runs never do."""
    since = timezone.now() - timedelta(hours=rule.cooldown_hours)
    runs = RuleRun.objects.filter(rule=rule, company=company, fired_at__gt=since)
    return runs.filter(Q(outcome=RuleOutcome.FIRED) | Q(state=ClaimState.CLAIMED)).exists()


def _stop(company: Company, rule: StageRule, outcome: str, kind: str, message: str) -> tuple[str, str]:
    activity_service.record(company, kind, message, data={"rule_id": rule.pk}, actor=f"rule:{rule.pk}")
    return outcome, message
