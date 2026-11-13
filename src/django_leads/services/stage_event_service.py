# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The event → stage map: which stage an event (first mail sent, a reply of a class, a finished sequence) moves a
company to. `reply:<class>` falls back to `reply`; a row with a null target keeps the stage; no row = no move. A
move never leaves a `won` or `lost` stage and, with `forward_only`, only goes to a higher `order`."""

from dataclasses import dataclass
from typing import Any

from django_leads.enums import StageEvent, StageKind
from django_leads.models import Channel, Company, Stage, StageEventRule
from django_leads.services import stage_service

EDITABLE_FIELDS = frozenset({"target_stage", "forward_only"})
CLOSED_KINDS = (StageKind.WON, StageKind.LOST)


class UnknownEvent(ValueError):
    """Not an event of the map."""


@dataclass(frozen=True)
class EffectiveEvent:
    """One event of the channel: its row, or the default (no row, no move)."""

    event: str
    target_stage: Stage | None = None
    forward_only: bool = True
    configured: bool = False


def reply_event(intent: str) -> str:
    return f"{StageEvent.REPLY}:{intent}" if intent else StageEvent.REPLY.value


def _lookup(event: str) -> tuple[str, ...]:
    return (event, StageEvent.REPLY.value) if event.startswith(f"{StageEvent.REPLY}:") else (event,)


def resolve(channel: Channel, event: str) -> StageEventRule | None:
    """The row that decides the event: its own, or for `reply:<class>` the `reply` row. One query."""
    names = _lookup(event)
    rows = StageEventRule.objects.filter(channel=channel, event__in=names).select_related("target_stage")
    by_event = {row.event: row for row in rows}
    return next((by_event[name] for name in names if name in by_event), None)


def may_move(current: Stage, target: Stage, *, forward_only: bool) -> bool:
    if current.kind in CLOSED_KINDS or current.pk == target.pk:
        return False
    return not forward_only or target.order > current.order


def apply(company: Company, event: str) -> Company:
    """Moves the company through `transition_stage` when the map says so; otherwise returns it unchanged."""
    rule = resolve(company.channel, event)
    if rule is None or rule.target_stage is None:
        return company
    if not may_move(company.stage, rule.target_stage, forward_only=rule.forward_only):
        return company
    return stage_service.transition_stage(company, rule.target_stage, actor="system")


def effective(channel: Channel) -> list[EffectiveEvent]:
    """One entry per event, in the order of `StageEvent`."""
    rows = {row.event: row for row in StageEventRule.objects.filter(channel=channel).select_related("target_stage")}
    return [_effective(rows[event]) if event in rows else EffectiveEvent(event) for event in StageEvent.values]


def _effective(row: StageEventRule) -> EffectiveEvent:
    return EffectiveEvent(row.event, row.target_stage, row.forward_only, configured=True)


def _check(event: str, fields: dict[str, Any]) -> None:
    if event not in StageEvent.values:
        raise UnknownEvent(f"unknown event {event!r}")
    invalid = set(fields) - EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")


def upsert(channel: Channel, event: str, fields: dict[str, Any]) -> EffectiveEvent:
    """Replaces the event's row (a PUT); `target_stage` must be a stage of the channel or None."""
    _check(event, fields)
    target = fields.get("target_stage")
    if target is not None and target.channel_id != channel.pk:
        raise ValueError("stage belongs to another channel")
    row, _ = StageEventRule.objects.update_or_create(channel=channel, event=event, defaults=fields)
    return _effective(row)


def reset(channel: Channel, event: str) -> None:
    """Removes the row: the event no longer moves a company."""
    _check(event, {})
    StageEventRule.objects.filter(channel=channel, event=event).delete()


def stage_by_key(channel: Channel, key: str) -> Stage:
    """Raises `Stage.DoesNotExist`."""
    return stage_service.list_stages(channel).get(key=key)
