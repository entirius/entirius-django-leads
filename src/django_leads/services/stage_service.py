# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pipeline stages: CRUD and the single stage-change path `transition_stage`."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from django_leads.enums import ActivityKind
from django_leads.models import Channel, Company, Stage
from django_leads.services import activity_service
from django_leads.signals import stage_entered

STAGE_FIELDS = frozenset({"key", "label", "order", "kind", "is_terminal", "on_reply"})


class StageInUse(Exception):
    """The stage still holds companies."""


class StageExists(Exception):
    """The channel already has a stage with this key."""


class NoStages(ValueError):
    """The channel has no pipeline yet."""


def list_stages(channel: Channel) -> QuerySet[Stage]:
    return Stage.objects.filter(channel=channel).order_by("order", "id")


def first_stage(channel: Channel) -> Stage:
    stage = list_stages(channel).first()
    if stage is None:
        raise NoStages(f"channel {channel.idx} has no stages")
    return stage


def create_stage(channel: Channel, fields: dict[str, Any]) -> Stage:
    _check_fields(fields)
    try:
        with transaction.atomic():
            return Stage.objects.create(channel=channel, **fields)
    except IntegrityError:
        raise StageExists(f"stage {fields.get('key')!r} already exists") from None


def update_stage(stage: Stage, updates: dict[str, Any]) -> Stage:
    _check_fields(updates)
    for field, value in updates.items():
        setattr(stage, field, value)
    try:
        with transaction.atomic():
            stage.save()
    except IntegrityError:
        raise StageExists(f"stage {stage.key!r} already exists") from None
    return stage


def delete_stage(stage: Stage) -> None:
    if stage.companies.exists():
        raise StageInUse(f"stage {stage.key!r} still holds companies")
    stage.delete()


@transaction.atomic
def transition_stage(company: Company, stage: Stage, *, actor: str) -> Company:
    """Move a company; writes the timeline and sends `stage_entered`. The same stage is a no-op."""
    if stage.channel_id != company.channel_id:
        raise ValueError("stage belongs to another channel")
    if stage.pk == company.stage_id:
        return company
    previous = company.stage.key
    company.stage = stage
    company.stage_entered_at = timezone.now()
    company.save(update_fields=["stage", "stage_entered_at", "modified_at"])
    data = {"from": previous, "to": stage.key}
    activity_service.record(company, ActivityKind.STAGE, f"stage {previous} -> {stage.key}", data=data, actor=actor)
    stage_entered.send(sender=Company, company=company, stage=stage)
    return company


def _check_fields(fields: dict[str, Any]) -> None:
    invalid = set(fields) - STAGE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
