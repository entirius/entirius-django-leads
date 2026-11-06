# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stage and contact handling per reply intent class and channel (T4). The sequence, alerts and reminders of the same
class live in communicator (`reply-actions/`); this is the part only leads can do.

Defaults are safe: every class keeps doing what a reply always did (move to the `on_reply` stage), except an
out-of-office reply, which is not a real answer and leaves the stage, and a wrong-person reply, which also flags the
contact. A reply with no class (toolbox off or failed) takes the pre-T4 path unchanged."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from django_leads.enums import ReplyIntent, ReplyStageAction
from django_leads.models import Channel, ReplyIntentRoute, Stage


class ReplyRouteError(ValueError):
    """The requested route is not allowed."""


@dataclass(frozen=True)
class Route:
    intent: str
    action: str
    stage_id: int | None
    stage_key: str | None
    flag_contact: bool
    is_default: bool = True


def _default(intent: str, action=ReplyStageAction.REPLY_STAGE, flag=False) -> Route:
    return Route(intent, action, None, None, flag)


DEFAULTS: dict[str, Route] = {
    ReplyIntent.INTERESTED: _default(ReplyIntent.INTERESTED),
    ReplyIntent.NOT_NOW: _default(ReplyIntent.NOT_NOW),
    ReplyIntent.NOT_INTERESTED: _default(ReplyIntent.NOT_INTERESTED),
    ReplyIntent.OUT_OF_OFFICE: _default(ReplyIntent.OUT_OF_OFFICE, ReplyStageAction.NONE),
    ReplyIntent.WRONG_PERSON: _default(ReplyIntent.WRONG_PERSON, flag=True),
    ReplyIntent.UNSUBSCRIBE: _default(ReplyIntent.UNSUBSCRIBE),
    ReplyIntent.UNCLEAR: _default(ReplyIntent.UNCLEAR),
}
UNCLASSIFIED = _default("")


def _route_of(row: ReplyIntentRoute) -> Route:
    return Route(row.intent, row.action, row.stage_id, row.stage.key if row.stage_id else None, row.flag_contact, False)


def route_for(channel: Channel, intent: str) -> Route:
    if intent not in DEFAULTS:
        return UNCLASSIFIED
    row = ReplyIntentRoute.objects.select_related("stage").filter(channel=channel, intent=intent).first()
    return _route_of(row) if row else DEFAULTS[intent]


def effective(channel: Channel) -> list[Route]:
    rows = {row.intent: row for row in ReplyIntentRoute.objects.select_related("stage").filter(channel=channel)}
    return [_route_of(rows[i]) if i in rows else DEFAULTS[i] for i in ReplyIntent.values]


@transaction.atomic
def upsert(channel: Channel, intent: str, *, action: str, stage_id: int | None, flag_contact: bool) -> Route:
    """The whole route of one class is replaced (a PUT). Raises `ReplyRouteError`."""
    if intent not in DEFAULTS:
        raise ReplyRouteError(f"unknown intent {intent!r}")
    stage = None
    if stage_id is not None:
        stage = Stage.objects.filter(channel=channel, pk=stage_id).first()
        if stage is None:
            raise ReplyRouteError("stage not found in this channel")
    row = ReplyIntentRoute(channel=channel, intent=intent, action=action, stage=stage, flag_contact=flag_contact)
    try:
        row.clean()
    except ValidationError as error:
        raise ReplyRouteError("; ".join(m for messages in error.message_dict.values() for m in messages)) from None
    ReplyIntentRoute.objects.update_or_create(
        channel=channel, intent=intent, defaults={"action": action, "stage": stage, "flag_contact": flag_contact}
    )
    return route_for(channel, intent)


def reset(channel: Channel, intent: str) -> Route:
    if intent not in DEFAULTS:
        raise ReplyRouteError(f"unknown intent {intent!r}")
    ReplyIntentRoute.objects.filter(channel=channel, intent=intent).delete()
    return DEFAULTS[intent]
