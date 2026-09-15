# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""siteintel → leads: request an audit, turn a finished audit into hooks through the toolbox."""

import logging

from django.conf import settings
from django.utils import timezone
from django_siteintel.enums import REUSABLE_AUDIT_STATUSES, SUCCEEDED_REPORT_STATUSES
from django_siteintel.models import Audit
from django_siteintel.services.audit_service import request_audit
from django_utils.toolbox import (
    ToolboxClient,
    ToolboxConnectionError,
    ToolboxError,
    ToolboxStatus,
    ToolboxTimeoutError,
    status,
)
from django_utils.toolbox.schemas import CompletionRequest

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, ClaimState, CompanyType, RuleTrigger
from django_leads.models import AnalysisProfile, Claim, Company
from django_leads.services import activity_service, alert_service, claim_service, rule_service
from django_leads.settings import LEADS_ANALYSIS_MAX_HOOKS
from django_leads.utils.domains import registrable_domain
from django_leads.utils.prompts import json_summary, prompt_messages

logger = logging.getLogger(__name__)

ANALYSIS_KEY = "leads.analysis"
SOURCES = ("lighthouse", "urlscan", "heuristic")


class AnalysisFailed(Exception):
    """The analysis produced no usable result; `code` goes to the timeline, `transient` makes it retryable."""

    def __init__(self, code: str, *, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


def request_audit_for(company: Company, *, actor: str) -> Audit:
    audit = request_audit(domain_or_url=company.domain, channel_idx=company.channel.idx, requested_by=actor)
    activity_service.record(
        company, ActivityKind.INTEL, "audit requested", data={"audit_id": str(audit.pk)}, actor=actor
    )
    return audit


def company_for_audit(audit: Audit) -> Company | None:
    companies = Company.objects.select_related("channel")
    return companies.filter(channel__idx=audit.channel_idx, domain=registrable_domain(audit.domain)).first()


def analyse_audit(audit_id: str, succeeded_sources: list[str], *, run_id: str = "") -> Company | None:
    """Hooks from a finished audit, then the `intel_ready` rules; a failed analysis alerts and evaluates nothing.
    Claim `intel:<audit>:<run>` — a redelivered task finds it and never repeats the paid completion."""
    audit = Audit.objects.filter(pk=audit_id).first()
    company = company_for_audit(audit) if audit else None
    claim = claim_service.take(company, f"intel:{audit_id}:{run_id}") if company else None
    if claim is None:
        return None
    _run_claimed(claim, company, audit, succeeded_sources)
    return company


def retry_failed_analyses() -> dict[str, int]:
    """Beat: analyses that failed transiently (claim `retry`) run again once `status()` reports the toolbox
    reachable, while their audit is still valid."""
    counts = {"recovered": 0, "failed": 0}
    if status() != ToolboxStatus.CONFIGURED:
        return counts
    claims = Claim.objects.filter(key__startswith="intel:", state=ClaimState.RETRY).select_related("company__channel")
    for claim in claims.order_by("pk"):
        outcome = _retry(claim)
        if outcome is not None:
            counts["recovered" if outcome else "failed"] += 1
    return counts


def _retry(claim: Claim) -> bool | None:
    """True analysed, False failed again, None when the audit is no longer valid, superseded or another run took the
    claim. Invalid and superseded claims end `failed` without spending a retry."""
    audit = Audit.objects.filter(pk=claim.key.split(":")[1]).first()
    if audit is None or audit.status not in REUSABLE_AUDIT_STATUSES or audit.expires_at <= timezone.now():
        claim_service.finish(claim, ClaimState.FAILED, "audit_invalid")
        return None
    if _superseded(claim, audit):
        claim_service.finish(claim, ClaimState.FAILED, "superseded")
        return None
    taken = Claim.objects.filter(pk=claim.pk, state=ClaimState.RETRY).update(
        state=ClaimState.CLAIMED, attempted_at=timezone.now()
    )
    if not taken:
        return None
    sources = sorted(audit.reports.filter(status__in=SUCCEEDED_REPORT_STATUSES).values_list("source", flat=True))
    return _run_claimed(claim, claim.company, audit, sources)


def _superseded(claim: Claim, audit: Audit) -> bool:
    """A newer audit of the domain (manual re-audit during the outage) or a later successful analysis of the company."""
    newer_audits = Audit.objects.filter(
        domain=audit.domain, channel_idx=audit.channel_idx, created_at__gt=audit.created_at
    )
    later_analyses = Claim.objects.filter(
        company=claim.company_id, key__startswith="intel:", state=ClaimState.DONE, pk__gt=claim.pk
    )
    return newer_audits.exists() or later_analyses.exists()


def _run_claimed(claim: Claim, company: Company, audit: Audit, succeeded_sources: list[str]) -> bool:
    try:
        _analyse_sources(company, audit, succeeded_sources)
    except AnalysisFailed as error:
        state = _finish_failed(claim, error)
        _analysis_failed(company, error.code, alert=claim.failures == 1 or state == ClaimState.FAILED)
        return False
    claim_service.finish(claim, ClaimState.DONE)
    rule_service.evaluate_rules(company, RuleTrigger.INTEL_READY, event=f"audit:{audit.pk}")
    return True


def _finish_failed(claim: Claim, error: AnalysisFailed) -> str:
    """`retry` for a transient failure while retries remain (`LEADS_INTEL_RETRY_LIMIT`), else `failed` for good."""
    failures = claim.failures + 1
    retryable = error.transient and failures <= leads_settings.LEADS_INTEL_RETRY_LIMIT
    state = ClaimState.RETRY if retryable else ClaimState.FAILED
    claim_service.finish(claim, state, error.code[:64], failures=failures)
    return state


def _analyse_sources(company: Company, audit: Audit, succeeded_sources: list[str]) -> None:
    """No source succeeded → the hooks of an older audit are cleared, rules never draft on stale hooks."""
    if succeeded_sources:
        _analyse(company, audit)
        return
    company.hooks = []
    company.save(update_fields=["hooks", "modified_at"])
    activity_service.record(company, ActivityKind.INTEL_EMPTY, "no intel sources succeeded")


def _analyse(company: Company, audit: Audit) -> None:
    profile = AnalysisProfile.objects.filter(channel=company.channel, key=ANALYSIS_KEY, is_active=True).first()
    if profile is None:
        raise AnalysisFailed("no_profile")
    request = CompletionRequest(
        model=profile.model,
        messages=prompt_messages(profile.prompt_text, _prompt_values(company, audit)),
        json_schema=profile.json_schema or None,
        tags=[ANALYSIS_KEY, f"channel:{company.channel.idx}"],
    )
    try:
        with ToolboxClient(settings.AI_TOOLBOX_CHANNEL) as client:
            response = client.complete(request)
    except ToolboxError as error:
        raise AnalysisFailed(error.code or type(error).__name__, transient=is_transient(error)) from None
    _apply(company, response.parsed, usage=response.usage.model_dump())


def _prompt_values(company: Company, audit: Audit) -> dict[str, str]:
    processed = dict(audit.reports.filter(source__in=SOURCES).values_list("source", "processed"))
    summaries = {f"{source}_summary": json_summary(processed.get(source, {})) for source in SOURCES}
    return {"company_name": company.name, "website": company.website or company.domain, **summaries}


def _apply(company: Company, parsed: dict | None, *, usage: dict) -> None:
    hooks = (parsed or {}).get("hooks")
    if not isinstance(hooks, list) or not isinstance(parsed.get("platform"), str):
        raise AnalysisFailed("schema")
    company.hooks = [hook for hook in hooks if isinstance(hook, dict)][:LEADS_ANALYSIS_MAX_HOOKS]
    company.platform = parsed["platform"][:64]
    guess = parsed.get("company_type_guess")
    if company.company_type == CompanyType.UNKNOWN and guess in CompanyType.values:
        company.company_type = guess
    company.save(update_fields=["hooks", "platform", "company_type", "modified_at"])
    data = {"hooks": len(company.hooks), "platform": company.platform, "usage": usage}
    activity_service.record(company, ActivityKind.INTEL, "intel analysed", data=data)


def is_transient(error: ToolboxError) -> bool:
    """Toolbox unreachable, timed out or 5xx — worth a retry; budget, model, schema and auth errors are not."""
    return isinstance(error, ToolboxConnectionError | ToolboxTimeoutError) or error.status_code >= 500


def _analysis_failed(company: Company, code: str, *, alert: bool = True) -> None:
    logger.warning("leads: intel analysis of company %s failed: %s", company.pk, code)
    activity_service.record(company, ActivityKind.INTEL, f"analysis failed: {code}")
    if alert:
        alert_service.alert(company, severity="medium", title="Intel analysis failed")
