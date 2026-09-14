# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin configuration of rules and prompt profiles — field whitelists live here."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Model, QuerySet

from django_leads.models import AnalysisProfile, Channel, RecipientPickProfile, RuleRun, Stage, StageRule

RULE_FIELDS = frozenset(
    {
        "trigger", "stage_id", "action", "template_key", "contact_strategy", "require_hooks",
        "require_email", "cooldown_hours", "is_active", "order",
    }
)  # fmt: skip
PROFILE_FIELDS = {
    AnalysisProfile: frozenset({"key", "prompt_text", "json_schema", "model", "is_active"}),
    RecipientPickProfile: frozenset({"key", "prompt_text", "json_schema", "model"}),
}


class ProfileExists(Exception):
    """The channel already has a profile with this key."""


def list_rules(channel: Channel) -> QuerySet[StageRule]:
    return StageRule.objects.filter(channel=channel).order_by("order", "id")


def list_rule_runs(channel: Channel, company_id: int | None = None) -> QuerySet[RuleRun]:
    runs = RuleRun.objects.filter(rule__channel=channel)
    return runs.filter(company_id=company_id) if company_id else runs


def save_rule(rule: StageRule, fields: dict[str, Any]) -> StageRule:
    """Raises ValueError (field not editable, unknown stage) and Django ValidationError (`clean()`)."""
    _check(fields, RULE_FIELDS)
    if fields.get("stage_id") and not Stage.objects.filter(pk=fields["stage_id"], channel_id=rule.channel_id).exists():
        raise ValueError("stage not found in this channel")
    for field, value in fields.items():
        setattr(rule, field, value)
    rule.full_clean(exclude=["channel"])
    rule.save()
    return rule


def save_profile(profile: Model, fields: dict[str, Any]) -> Model:
    _check(fields, PROFILE_FIELDS[type(profile)])
    for field, value in fields.items():
        setattr(profile, field, value)
    try:
        with transaction.atomic():
            profile.save()
    except IntegrityError:
        raise ProfileExists(f"profile {profile.key!r} already exists") from None
    return profile


def _check(fields: dict[str, Any], allowed: frozenset[str]) -> None:
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
