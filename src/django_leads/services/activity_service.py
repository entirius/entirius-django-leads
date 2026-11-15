# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The only writer of `Activity`; every company entry also moves `Company.last_activity_at`.

The channel's history (U11) is the list of every entry: company timelines plus entries about the channel itself
(settings changes, company null). The daily purge deletes entries older than `Channel.history_months`.
"""

import calendar
from datetime import date, datetime, time, timedelta

from django.db.models import QuerySet
from django.utils import timezone

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
) -> Activity:
    activity = Activity(
        company=company, contact=contact, deal=deal, kind=kind, message=message[:255], data=data or {}, actor=actor
    )
    return record_many([activity])[0]


def record_channel(channel: Channel, kind: str, message: str, *, data: dict | None = None, actor: str) -> Activity:
    """An entry about the channel itself (no company) — moves no `last_activity_at`."""
    return Activity.objects.create(channel=channel, kind=kind, message=message[:255], data=data or {}, actor=actor)


def record_many(activities: list[Activity]) -> list[Activity]:
    """Bulk variant for the import: one insert, one `last_activity_at` update."""
    if not activities:
        return []
    for activity in activities:
        if activity.channel_id is None:
            activity.channel_id = activity.company.channel_id
    Activity.objects.bulk_create(activities)
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
        activities = activities.filter(actor=actor)
    if kinds:
        activities = activities.filter(kind__in=kinds)
    if date_from:
        activities = activities.filter(created_at__gte=_day_start(date_from))
    if date_to:
        activities = activities.filter(created_at__lt=_day_start(date_to, days_after=1))
    return activities


def list_actors(channel: Channel) -> list[str]:
    """Every actor that wrote an entry of the channel, for the history's person filter."""
    return list(Activity.objects.filter(channel=channel).order_by("actor").values_list("actor", flat=True).distinct())


def purge_old(now: datetime | None = None) -> dict[str, int]:
    """Deletes entries older than each channel's `history_months`; deleted count per channel idx."""
    now = now or timezone.now()
    deleted = {}
    for channel in Channel.objects.all():
        cutoff = months_before(now, channel.history_months)
        count, _ = Activity.objects.filter(channel=channel, created_at__lt=cutoff).delete()
        deleted[channel.idx] = count
    return deleted


def months_before(moment: datetime, months: int) -> datetime:
    """The same day and time `months` earlier; a day the month lacks becomes its last day."""
    year, month = divmod(moment.year * 12 + moment.month - 1 - months, 12)
    month += 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def _day_start(day: date, days_after: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day + timedelta(days=days_after), time.min))


def list_for_deal(deal: Deal) -> QuerySet[Activity]:
    return Activity.objects.filter(deal=deal).select_related("contact", "company", "deal")
