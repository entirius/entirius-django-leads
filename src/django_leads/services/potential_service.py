# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The potential dot of a company: green "going on", orange "in progress", red "come back later", "" = not written
to yet. The only writer of the `potential*` fields; every change leaves one `potential` Activity.

The colour is a function of what leads already stores (`compute`), first match wins:
manual → do not contact (red, forever) → spam complaint (red, forever) → an open or won deal (green) → every deal
lost (red, back after `red_return_days`) → the last reply newer than the last mail (its colour; not interested = red,
back after `red_return_days`) → nobody left to mail (orange `looking_for_person`) → `silence_days` without a reply
after the last mail (red, back after `red_return_days`) → a skipped draft newer than the last mail (orange, back after
`skip_return_days`) → a mail sent (orange `awaiting_reply`).

A red whose return day has come is orange `came_back`, and so is a skipped draft whose return day has come; the
change opens a "Come back" task for the owner (Today). An opt-out or a wrong person blocks only that person.
A manual colour holds until a new reply or a deal change (`refresh(reset_manual=True)`), or until its own return
day; it is refused (`LegalBlock`) for anything but red on a company that may never be mailed.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from django_leads.enums import ActivityKind, DealStageKind, Potential
from django_leads.models import Activity, Channel, Company, Deal
from django_leads.services import activity_service, task_bridge
from django_leads.services.task_bridge import TaskSpec

logger = logging.getLogger(__name__)

DEFAULT_REPLY_COLOURS = {
    "interested": Potential.GREEN.value,
    "not_now": Potential.ORANGE.value,
    "out_of_office": Potential.ORANGE.value,
    "unclear": Potential.ORANGE.value,
    "wrong_person": Potential.ORANGE.value,
    "not_interested": Potential.RED.value,
}
DEFAULTS: dict[str, Any] = {
    "silence_days": 120,
    "skip_return_days": 30,
    "red_return_days": 90,
    "reply_colours": DEFAULT_REPLY_COLOURS,
}
DAY_KEYS = ("silence_days", "skip_return_days", "red_return_days")
UNSUBSCRIBE = "unsubscribe"
WRONG_PERSON = "wrong_person"
DRAFT_SKIPPED = "draft_skipped"
COMPLAINT = "complaint"
CAME_BACK = "came_back"
MANUAL = "manual"
LEGAL_REASONS = frozenset({"do_not_contact", COMPLAINT})
TASK_SOURCE = "leads.potential.came_back"


class LegalBlock(ValueError):
    """The company may never be mailed: only red."""


@dataclass(frozen=True)
class Verdict:
    colour: str
    reason: str
    return_on: date | None = None


NO_DOT = Verdict("", "")


def thresholds(channel: Channel) -> dict[str, Any]:
    """The channel's values over the defaults; `reply_colours` merged per class."""
    own = channel.potential_settings or {}
    values = {**DEFAULTS, **{key: own[key] for key in DAY_KEYS if own.get(key)}}
    values["reply_colours"] = {**DEFAULT_REPLY_COLOURS, **(own.get("reply_colours") or {})}
    return values


def update_thresholds(channel: Channel, values: dict[str, Any] | None, *, actor: str) -> Channel:
    """Replaces the channel's thresholds (None = the defaults), then recomputes every company of the channel
    (a manual colour holds)."""
    channel.potential_settings = values or {}
    channel.save(update_fields=["potential_settings", "modified_at"])
    from django_leads.tasks import refresh_channel_potential

    transaction.on_commit(lambda: refresh_channel_potential.delay(channel.pk))
    return channel


def legal_block(company: Company) -> str | None:
    if company.do_not_contact:
        return "do_not_contact"
    return COMPLAINT if _complained(company) else None


def compute(company: Company, *, today: date | None = None) -> Verdict:
    """The automatic colour (the manual one is the caller's business)."""
    today = today or timezone.localdate()
    days = thresholds(company.channel)
    if reason := legal_block(company):
        return Verdict(Potential.RED, reason)
    if verdict := _deals(company, days, today):
        return verdict
    last_sent = _last(company, Q(kind=ActivityKind.SENT))
    if verdict := _reply(company, last_sent, days, today):
        return verdict
    if last_sent and not _reachable(company):
        return Verdict(Potential.ORANGE, "looking_for_person")
    if last_sent and _day(last_sent.created_at) + timedelta(days=days["silence_days"]) <= today:
        back = _day(last_sent.created_at) + timedelta(days=days["silence_days"] + days["red_return_days"])
        return _red_or_back("silence", back, today)
    skipped = _last(company, Q(kind=ActivityKind.SKIPPED, data__event=DRAFT_SKIPPED))
    if skipped and (last_sent is None or skipped.created_at > last_sent.created_at):
        back = _day(skipped.created_at) + timedelta(days=days["skip_return_days"])
        return Verdict(Potential.ORANGE, CAME_BACK) if back <= today else Verdict(Potential.ORANGE, DRAFT_SKIPPED, back)
    if last_sent:
        return Verdict(Potential.ORANGE, "awaiting_reply")
    return NO_DOT


def refresh(company: Company, *, reset_manual: bool = False, actor: str = "system") -> Company:
    """Recomputes and stores the colour; a manual colour holds unless `reset_manual` or a legal block."""
    with transaction.atomic():
        locked = Company.objects.select_for_update(of=("self",)).select_related("channel").get(pk=company.pk)
        if locked.potential_manual and not reset_manual and not legal_block(locked):
            return _copy(company, locked)
        _store(locked, compute(locked), manual=False, comment="", actor=actor)
    return _copy(company, locked)


def on_event(company: Company | None, *, reset_manual: bool = False) -> None:
    """`refresh` after an event of another flow; never raises to it (the colour is advice), a failure is logged."""
    if company is None:
        return
    try:
        refresh(company, reset_manual=reset_manual)
    except Exception:
        logger.exception("leads: potential of company %s not refreshed", company.pk)


def set_manual(company: Company, colour: str, comment: str, *, return_on: date | None, actor: str) -> Company:
    """A salesperson's colour with a required comment (why it differs from the rules)."""
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("comment is required")
    if colour not in Potential.values:
        raise ValueError(f"unknown colour {colour!r}")
    with transaction.atomic():
        locked = Company.objects.select_for_update(of=("self",)).select_related("channel").get(pk=company.pk)
        if colour != Potential.RED and (reason := legal_block(locked)):
            raise LegalBlock(f"the company may never be mailed ({reason}): only red")
        _store(locked, Verdict(colour, MANUAL, return_on), manual=True, comment=comment[:255], actor=actor)
    return _copy(company, locked)


def clear_manual(company: Company, *, actor: str) -> Company:
    """Back to the rules."""
    return refresh(company, reset_manual=True, actor=actor)


def sweep(*, today: date | None = None) -> int:
    """Daily beat: silence, return days and manual colours whose return day has come. Returns the companies whose
    colour changed."""
    today = today or timezone.localdate()
    changed = 0
    for company in candidates().select_related("channel").iterator(chunk_size=200):
        manual_due = company.potential_manual and company.potential_return_on and company.potential_return_on <= today
        before = (company.potential, company.potential_reason)
        refreshed = refresh(company, reset_manual=bool(manual_due))
        changed += (refreshed.potential, refreshed.potential_reason) != before
    return changed


def refresh_channel(channel: Channel) -> int:
    changed = 0
    for company in candidates().filter(channel=channel).select_related("channel").iterator(chunk_size=200):
        before = (company.potential, company.potential_reason)
        refreshed = refresh(company)
        changed += (refreshed.potential, refreshed.potential_reason) != before
    return changed


def candidates() -> QuerySet[Company]:
    """Companies that have a dot or may get one: a mail, a reply, a skipped draft, a deal or do not contact."""
    mailed = Activity.objects.filter(
        company=OuterRef("pk"), kind__in=(ActivityKind.SENT, ActivityKind.REPLY, ActivityKind.SKIPPED)
    )
    dealt = Deal.objects.filter(company=OuterRef("pk"))
    return Company.objects.filter(~Q(potential="") | Q(do_not_contact=True) | Exists(mailed) | Exists(dealt))


def filter_companies(companies: QuerySet[Company], potential: str) -> QuerySet[Company]:
    """`green` · `orange` · `red` · `none` (no dot)."""
    return companies.filter(potential="" if potential == "none" else potential)


def record_skipped(
    company: Company, *, contact=None, message_id: int | None = None, reason: str = "", actor: str = "reviewer"
) -> None:
    """A reviewer skipped a draft (communicator `message_skipped`): timeline (who clicked Skip), then the colour."""
    data = {"event": DRAFT_SKIPPED, "message_id": message_id, "reason": reason}
    activity_service.record(company, ActivityKind.SKIPPED, "draft skipped", contact=contact, data=data, actor=actor)
    on_event(company)


def _store(company: Company, verdict: Verdict, *, manual: bool, comment: str, actor: str) -> None:
    previous = (company.potential, company.potential_reason, company.potential_return_on)
    current = (str(verdict.colour), verdict.reason, verdict.return_on)
    if previous == current and company.potential_manual == manual and company.potential_comment == comment:
        return
    company.potential, company.potential_reason, company.potential_return_on = current
    company.potential_manual, company.potential_comment = manual, comment
    company.potential_changed_at = timezone.now()
    fields = ["potential", "potential_reason", "potential_return_on", "potential_manual", "potential_comment"]
    company.save(update_fields=[*fields, "potential_changed_at", "modified_at"])
    data = {
        "event": "potential",
        "from": previous[0],
        "to": current[0],
        "reason": verdict.reason,
        "return_on": verdict.return_on.isoformat() if verdict.return_on else None,
        "manual": manual,
        "comment": comment,
    }
    message = f"potential: {current[0] or 'none'} ({verdict.reason or 'not written to yet'})"
    if comment:
        message = f"{message} — {comment}"
    activity_service.record(company, ActivityKind.POTENTIAL, message, data=data, actor=actor)
    if verdict.reason == CAME_BACK and previous[1] != CAME_BACK:
        _come_back_task(company)


def _come_back_task(company: Company) -> None:
    title = f"Come back to {company.name or company.domain}"
    spec = TaskSpec("todo", title, timezone.now(), TASK_SOURCE, f"potential:{company.pk}")
    task_bridge.create(company, None, spec)


def _deals(company: Company, days: dict[str, Any], today: date) -> Verdict | None:
    kinds = list(company.deals.values_list("stage__kind", "closed_at"))
    if not kinds:
        return None
    if any(kind == DealStageKind.OPEN for kind, _ in kinds):
        return Verdict(Potential.GREEN, "deal_open")
    if any(kind == DealStageKind.WON for kind, _ in kinds):
        return Verdict(Potential.GREEN, "deal_won")
    closed = max((closed_at for _, closed_at in kinds if closed_at), default=timezone.now())
    return _red_or_back("deals_lost", _day(closed) + timedelta(days=days["red_return_days"]), today)


def _reply(company: Company, last_sent: Activity | None, days: dict[str, Any], today: date) -> Verdict | None:
    reply = _last(company, Q(kind=ActivityKind.REPLY))
    if reply is None or (last_sent is not None and reply.created_at < last_sent.created_at):
        return None
    intent = (reply.data or {}).get("class") or ""
    if intent in (UNSUBSCRIBE, WRONG_PERSON) and not _reachable(company):
        return Verdict(Potential.ORANGE, "looking_for_person")
    if intent == UNSUBSCRIBE:
        return None  # the opt-out blocks only that person
    colour = days["reply_colours"].get(intent, Potential.ORANGE) if intent else Potential.ORANGE
    reason = f"reply:{intent}" if intent else "reply"
    if colour == Potential.RED:
        return _red_or_back(reason, _day(reply.created_at) + timedelta(days=days["red_return_days"]), today)
    back = _parse_day((reply.data or {}).get("return_date"))
    return Verdict(colour, reason, back if colour == Potential.ORANGE else None)


def _red_or_back(reason: str, back: date, today: date) -> Verdict:
    return Verdict(Potential.ORANGE, CAME_BACK) if back <= today else Verdict(Potential.RED, reason, back)


def _reachable(company: Company) -> bool:
    """Someone at the company may still be mailed (an opt-out or a wrong person blocks only that person)."""
    contacts = company.contacts.exclude(email="").filter(
        opt_out_at__isnull=True, anonymised_at__isnull=True, wrong_person_at__isnull=True
    )
    return contacts.exists()


def _complained(company: Company) -> bool:
    return company.activities.filter(data__reason=COMPLAINT).exists()


def _last(company: Company, where: Q) -> Activity | None:
    return company.activities.filter(where).order_by("-created_at", "-id").first()


def _day(moment: datetime) -> date:
    return timezone.localdate(moment)


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _copy(target: Company, source: Company) -> Company:
    for field in (
        "potential",
        "potential_reason",
        "potential_return_on",
        "potential_manual",
        "potential_comment",
        "potential_changed_at",
    ):
        setattr(target, field, getattr(source, field))
    return target
