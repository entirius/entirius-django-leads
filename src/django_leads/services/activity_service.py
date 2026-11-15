# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The only writer of `Activity`; every company entry also moves `Company.last_activity_at`.

The channel's history (U11) is the list of every entry: company timelines plus entries about the channel itself
(settings changes, company null). The daily purge deletes entries older than `Channel.history_months`, except the
ones other code reads as state (`PURGE_KEEPS`).
"""

import calendar
from datetime import date, datetime, time, timedelta

from django.db.models import OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel, Company, Contact, Deal


def record(
    company: Company,
    kind: str,
    message: str,
    *,
    contact: Contact | None = None,
    deal: Deal | None = None,
    data: dict | None = None,
    actor: str = "system",
    touch: bool = True,
) -> Activity:
    """`touch=False` leaves `Company.last_activity_at` (the retention clock) alone: for entries nobody at the
    company caused (the potential dot's own changes)."""
    activity = Activity(
        company=company, contact=contact, deal=deal, kind=kind, message=message[:255], data=data or {}, actor=actor
    )
    return record_many([activity], touch=touch)[0]


def record_channel(channel: Channel, kind: str, message: str, *, data: dict | None = None, actor: str) -> Activity:
    """An entry about the channel itself (no company) — moves no `last_activity_at`."""
    return Activity.objects.create(channel=channel, kind=kind, message=message[:255], data=data or {}, actor=actor)


def record_many(activities: list[Activity], *, touch: bool = True) -> list[Activity]:
    """Bulk variant for the import: one insert, one `last_activity_at` update (none with `touch=False`)."""
    if not activities:
        return []
    for activity in activities:
        if activity.channel_id is None:
            activity.channel_id = activity.company.channel_id
    Activity.objects.bulk_create(activities)
    if not touch:
        return activities
    now = timezone.now()
    company_ids = {activity.company_id for activity in activities}
    Company.objects.filter(pk__in=company_ids).update(last_activity_at=now)
    for activity in activities:
        activity.company.last_activity_at = now
    return activities


def list_activities(
    channel: Channel,
    company_id: int | None = None,
    *,
    actor: str = "",
    kinds: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> QuerySet[Activity]:
    """Newest first. `date_from` / `date_to` are whole days in the current time zone, both included."""
    activities = Activity.objects.filter(channel=channel).select_related("contact", "company", "deal")
    if company_id:
        activities = activities.filter(company_id=company_id)
    if actor:
        activities = activities.filter(_actor_filter(actor))
    if kinds:
        activities = activities.filter(kind__in=kinds)
    if date_from:
        activities = activities.filter(created_at__gte=_day_start(date_from))
    if date_to:
        activities = activities.filter(created_at__lt=_day_start(date_to, days_after=1))
    return activities


IMPORT_ACTOR = "import:"  # every import batch ("import:<id>") is one entry of the person filter
VIA = " via "  # a person acting through another module: "anna via ai-crm (cms)"


def _actor_filter(actor: str) -> Q:
    """`import:` = every import batch; a person = their own entries and the ones they made through a module."""
    if actor == IMPORT_ACTOR:
        return Q(actor__startswith=IMPORT_ACTOR)
    return Q(actor=actor) | Q(actor__startswith=f"{actor}{VIA}")


def person_of(actor: str) -> str:
    """The person filter's entry of an actor: the person before " via ", `import:` for every import batch."""
    if actor.startswith(IMPORT_ACTOR):
        return IMPORT_ACTOR
    return actor.split(VIA, 1)[0]


def list_actors(channel: Channel) -> list[str]:
    """Every person of the channel history (the person filter); `actor` of `list_activities` takes each one."""
    actors = Activity.objects.filter(channel=channel).values_list("actor", flat=True).distinct()
    return sorted({person_of(actor) for actor in actors})


# Kinds the purge never deletes: legal basis (accountability: source and date; `mailable_service`), opt-out and
# anonymisation (the person's state). Also kept: an entry of a spam complaint (`legal_block` of the potential dot)
# and a company's first `sent` (`communicator_receivers.record_sent` applies `first_sent` on it).
PURGE_KEEPS = (
    ActivityKind.LEGAL_BASIS,
    ActivityKind.LEGAL_BASIS_CONFLICT,
    ActivityKind.OPTOUT,
    ActivityKind.ANONYMISED,
)
COMPLAINT_REASON = "complaint"


def purge_old(now: datetime | None = None) -> dict[str, int]:
    """Deletes entries older than each channel's `history_months`, except `PURGE_KEEPS`; count per channel idx."""
    now = now or timezone.now()
    deleted = {}
    for channel in Channel.objects.all():
        old = Activity.objects.filter(channel=channel, created_at__lt=months_before(now, channel.history_months))
        count, _ = _purgeable(old).delete()
        deleted[channel.idx] = count
    return deleted


def _purgeable(old: QuerySet[Activity]) -> QuerySet[Activity]:
    first_sent = Activity.objects.filter(company=OuterRef("company"), kind=ActivityKind.SENT)
    first_sent = first_sent.order_by("created_at", "id").values("pk")[:1]
    state = (
        Q(kind__in=PURGE_KEEPS) | Q(data__reason=COMPLAINT_REASON) | Q(kind=ActivityKind.SENT, pk=Subquery(first_sent))
    )
    return old.exclude(
        pk__in=old.filter(state).values("pk")
    )  # not `exclude(state)`: a JSON key test is NULL, not false


def months_before(moment: datetime, months: int) -> datetime:
    """The same day and time `months` earlier; a day the month lacks becomes its last day."""
    year, month = divmod(moment.year * 12 + moment.month - 1 - months, 12)
    month += 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def _day_start(day: date, days_after: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day + timedelta(days=days_after), time.min))


def list_for_deal(deal: Deal) -> QuerySet[Activity]:
    return Activity.objects.filter(deal=deal).select_related("contact", "company", "deal")
