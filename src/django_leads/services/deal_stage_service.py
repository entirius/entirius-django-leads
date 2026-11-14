# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Deal stages: the channel's one sales pipeline — CRUD and lookups. Company stages are `stage_service`."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_leads.enums import DealStageKind
from django_leads.models import Channel, DealStage

STAGE_FIELDS = frozenset({"key", "label", "order", "kind"})


class DealStageInUse(Exception):
    """The stage still holds deals."""


class DealStageExists(Exception):
    """The channel already has a deal stage with this key."""


class MissingDealStage(Exception):
    """The channel has no deal stage of the kind (or key) asked for."""


def list_stages(channel: Channel) -> QuerySet[DealStage]:
    return DealStage.objects.filter(channel=channel).order_by("order", "id")


def first_of_kind(channel: Channel, kind: str) -> DealStage:
    """The first stage of the kind by `order`; a new deal starts in the first `open` one."""
    stage = list_stages(channel).filter(kind=kind).first()
    if stage is None:
        raise MissingDealStage(f"channel {channel.idx} has no {kind} deal stage")
    return stage


def by_key(channel: Channel, key: str) -> DealStage:
    stage = list_stages(channel).filter(key=key).first()
    if stage is None:
        raise MissingDealStage(f"deal stage {key!r} does not exist")
    return stage


def create_stage(channel: Channel, fields: dict[str, Any]) -> DealStage:
    _check_fields(fields)
    try:
        with transaction.atomic():
            return DealStage.objects.create(channel=channel, **fields)
    except IntegrityError:
        raise DealStageExists(f"deal stage {fields.get('key')!r} already exists") from None


def update_stage(stage: DealStage, updates: dict[str, Any]) -> DealStage:
    """The kind of a stage holding deals is fixed: closed deals would sit in an open stage, or the reverse."""
    _check_fields(updates)
    if updates.get("kind", stage.kind) != stage.kind and stage.deals.exists():
        raise DealStageInUse(f"deal stage {stage.key!r} holds deals — its kind is fixed")
    for field, value in updates.items():
        setattr(stage, field, value)
    try:
        with transaction.atomic():
            stage.save()
    except IntegrityError:
        raise DealStageExists(f"deal stage {stage.key!r} already exists") from None
    return stage


def delete_stage(stage: DealStage) -> None:
    if stage.deals.exists():
        raise DealStageInUse(f"deal stage {stage.key!r} still holds deals")
    stage.delete()


def is_closed(stage: DealStage) -> bool:
    return stage.kind != DealStageKind.OPEN


def _check_fields(fields: dict[str, Any]) -> None:
    invalid = set(fields) - STAGE_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
