# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Scoring: points from the channel's `ScoringProfile` rules on `fact_service` facts, a segment S1 (best) to S4.

Pure arithmetic on stored data — no toolbox, no network. `score_and_record` stores the result on the company and
writes Activity `score` with the reasons (text and structured). A disqualifier forces S4 whatever the points.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from django_leads.enums import ActivityKind, ScoreSegment
from django_leads.models import Channel, Company, ScoringProfile
from django_leads.services import activity_service, condition_service, fact_service

PROFILE_FIELDS = frozenset({"key", "thresholds", "rules", "disqualifiers", "is_active"})
THRESHOLD_KEYS = ("S", "M", "L")


class ProfileExists(Exception):
    """The channel already has a scoring profile with this key."""


class NoScoringProfile(Exception):
    """The channel has no active scoring profile."""


@dataclass
class ScoreResult:
    segment: str
    points: int
    disqualified: bool = False
    reasons: list[str] = field(default_factory=list)
    reasons_structured: list[dict[str, Any]] = field(default_factory=list)
    profile_id: int | None = None


def list_profiles(channel: Channel) -> QuerySet[ScoringProfile]:
    return ScoringProfile.objects.filter(channel=channel).order_by("key")


def active_profile(channel_id: int) -> ScoringProfile | None:
    return ScoringProfile.objects.filter(channel_id=channel_id, is_active=True).order_by("key").first()


def save_profile(profile: ScoringProfile, fields: dict[str, Any]) -> ScoringProfile:
    """Whitelisted fields; thresholds ordered S ≤ M ≤ L; every rule and disqualifier names a known field with an op
    of its kind (ValueError) — on create all of them, on update only the fields given, so a profile whose field was
    deactivated can still be switched off. A duplicate key raises `ProfileExists`."""
    invalid = set(fields) - PROFILE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
    for name, value in fields.items():
        setattr(profile, name, value)
    _check_profile(profile, set(fields) if profile.pk else set(PROFILE_FIELDS))
    try:
        with transaction.atomic():
            profile.save()
    except IntegrityError:
        raise ProfileExists(f"scoring profile {profile.key!r} already exists") from None
    return profile


def _check_profile(profile: ScoringProfile, changed: set[str]) -> None:
    thresholds = [profile.thresholds.get(key) for key in THRESHOLD_KEYS]
    valid = all(isinstance(value, int) for value in thresholds) and thresholds == sorted(thresholds)
    if "thresholds" in changed and not valid:
        raise ValueError("thresholds need integer S <= M <= L")
    for name in ("rules", "disqualifiers"):
        if name in changed:
            setattr(profile, name, condition_service.check_all(profile.channel, getattr(profile, name)))


def segment_for(points: int, thresholds: dict[str, int]) -> str:
    if points >= thresholds["L"]:
        return ScoreSegment.S1
    if points >= thresholds["M"]:
        return ScoreSegment.S2
    if points >= thresholds["S"]:
        return ScoreSegment.S3
    return ScoreSegment.S4


def score(company: Company, profile: ScoringProfile | None = None) -> ScoreResult | None:
    """`None` when the channel has no active profile."""
    profile = profile or active_profile(company.channel_id)
    if profile is None:
        return None
    facts = fact_service.facts(company)
    fields = fact_service.field_map(company.channel)
    rules = [rule for rule in profile.rules if _matches(rule, facts, fields)]
    points = sum(rule["points"] for rule in rules)
    disqualifiers = [rule for rule in profile.disqualifiers if _matches(rule, facts, fields)]
    if disqualifiers:
        reasons = [f"disqualified: {_label(rule)}" for rule in disqualifiers]
        structured = [_reason("disqualifier", rule, 0) for rule in disqualifiers]
        return ScoreResult(ScoreSegment.S4, points, True, reasons, structured, profile.pk)
    reasons = [f"{_label(rule)}: {rule['points']:+d}" for rule in rules]
    structured = [_reason("rule", rule, rule["points"]) for rule in rules]
    return ScoreResult(segment_for(points, profile.thresholds), points, False, reasons, structured, profile.pk)


def score_and_record(company: Company, *, actor: str) -> ScoreResult:
    """Score, store `score_points` / `score_segment` / `scored_at` and write Activity `score`; raises
    `NoScoringProfile` when the channel has none."""
    result = score(company)
    if result is None:
        raise NoScoringProfile(f"channel {company.channel_id} has no active scoring profile")
    stored = {"score_points": result.points, "score_segment": result.segment, "scored_at": timezone.now()}
    message = f"score {result.segment} ({result.points} points)"
    with transaction.atomic():  # the stored score and its timeline entry commit together
        Company.objects.filter(pk=company.pk).update(**stored)
        activity_service.record(company, ActivityKind.SCORE, message, data=asdict(result), actor=actor)
    for name, value in stored.items():
        setattr(company, name, value)
    return result


def _matches(rule: dict[str, Any], facts: dict[str, Any], fields: dict[str, dict[str, Any]]) -> bool:
    return condition_service.first_failing([rule], facts, fields) is None


def _label(rule: dict[str, Any]) -> str:
    return rule.get("label") or rule["field"]


def _reason(kind: str, rule: dict[str, Any], points: int) -> dict[str, Any]:
    return {"field": rule["field"], "kind": kind, "points": points, "label": rule.get("label") or ""}
