# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Scoring: points from the channel's `ScoringProfile` rules, a segment S1 (best) to S4, reasons for the timeline.

Pure arithmetic on stored company data — no toolbox, no network. The result goes to the timeline as Activity `score`.
"""

from dataclasses import dataclass, field
from typing import Any

from django.db.models import QuerySet

from django_leads.enums import ActivityKind
from django_leads.models import Company, ScoringProfile
from django_leads.services import activity_service

SEGMENTS = ("S1", "S2", "S3", "S4")
FIELDS = ("lead_type", "industry", "platform", "domain", "hooks_count", "emails_count", "rotation_count")
OPS = ("eq", "in", "contains", "gte", "lte")


@dataclass
class ScoreResult:
    segment: str
    points: int
    disqualified: bool = False
    reasons: list[str] = field(default_factory=list)
    profile_id: int | None = None
    reasons_structured: list[dict[str, Any]] = field(default_factory=list)

    def as_data(self) -> dict[str, Any]:
        return {
            "segment": self.segment,
            "points": self.points,
            "disqualified": self.disqualified,
            "reasons": self.reasons,
            "profile_id": self.profile_id,
            "reasons_structured": self.reasons_structured,
        }


def active_profile(company: Company) -> ScoringProfile | None:
    profiles: QuerySet[ScoringProfile] = ScoringProfile.objects.filter(channel_id=company.channel_id, is_active=True)
    return profiles.order_by("key").first()


def facts(company: Company) -> dict[str, Any]:
    return {
        "lead_type": company.lead_type,
        "industry": company.industry,
        "platform": company.platform,
        "domain": company.domain,
        "hooks_count": len(company.hooks or []),
        "emails_count": company.contacts.exclude(email="").count(),
        "rotation_count": company.rotation_count,
    }


def matches(condition: dict[str, Any], values: dict[str, Any]) -> bool:
    actual, expected, op = values.get(condition["field"]), condition["value"], condition["op"]
    if isinstance(actual, str):
        actual = actual.lower()
        expected = [v.lower() for v in expected] if isinstance(expected, list) else str(expected).lower()
    if op == "eq":
        return actual == expected
    if op == "in":
        return actual in expected
    if op == "contains":
        return isinstance(actual, str) and expected in actual
    if op == "gte":
        return isinstance(actual, int) and actual >= expected
    return isinstance(actual, int) and actual <= expected  # lte


def segment_for(points: int, thresholds: dict[str, int]) -> str:
    if points >= thresholds["L"]:
        return "S1"
    if points >= thresholds["M"]:
        return "S2"
    if points >= thresholds["S"]:
        return "S3"
    return "S4"


def _reason(kind: str, condition: dict[str, Any], points: int) -> dict[str, Any]:
    """Machine-readable twin of a reason string: `code` is the fact field, `label_key` a stable i18n key."""
    return {
        "code": condition["field"],
        "kind": kind,
        "points": points,
        "label": condition.get("label") or "",
        "label_key": f"leads.scoring.{kind}.{condition['field']}",
    }


def score(company: Company, profile: ScoringProfile | None = None) -> ScoreResult | None:
    """`None` when the channel has no active profile. A disqualifier forces S4 whatever the points."""
    profile = profile or active_profile(company)
    if profile is None:
        return None
    values = facts(company)
    points, reasons, structured = 0, [], []
    for rule in profile.rules:
        if matches(rule, values):
            points += rule["points"]
            reasons.append(f"{rule.get('label') or rule['field']}: {rule['points']:+d}")
            structured.append(_reason("rule", rule, rule["points"]))
    matched = [d for d in profile.disqualifiers if matches(d, values)]
    if matched:
        labels = [d.get("label") or d["field"] for d in matched]
        return ScoreResult(
            "S4",
            points,
            True,
            [f"disqualified: {label}" for label in labels],
            profile.pk,
            [_reason("disqualifier", d, 0) for d in matched],
        )
    return ScoreResult(segment_for(points, profile.thresholds), points, False, reasons, profile.pk, structured)


def score_and_record(company: Company, *, actor: str) -> ScoreResult | None:
    result = score(company)
    if result is not None:
        message = f"score {result.segment} ({result.points} points)"
        activity_service.record(company, ActivityKind.SCORE, message, data=result.as_data(), actor=actor)
    return result
