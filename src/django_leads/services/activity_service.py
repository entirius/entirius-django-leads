# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The only writer of `Activity`; every entry also moves `Company.last_activity_at`."""

from django.db.models import QuerySet
from django.utils import timezone

from django_leads.models import Activity, Company, Contact


def record(
    company: Company,
    kind: str,
    message: str,
    *,
    contact: Contact | None = None,
    data: dict | None = None,
    actor: str = "system",
) -> Activity:
    activity = Activity(
        company=company, contact=contact, kind=kind, message=message[:255], data=data or {}, actor=actor
    )
    return record_many([activity])[0]


def record_many(activities: list[Activity]) -> list[Activity]:
    """Bulk variant for the import: one insert, one `last_activity_at` update."""
    if not activities:
        return []
    Activity.objects.bulk_create(activities)
    now = timezone.now()
    company_ids = {activity.company_id for activity in activities}
    Company.objects.filter(pk__in=company_ids).update(last_activity_at=now)
    for activity in activities:
        activity.company.last_activity_at = now
    return activities


def list_activities(channel, company_id: int | None = None) -> QuerySet[Activity]:
    activities = Activity.objects.filter(company__channel=channel).select_related("contact")
    return activities.filter(company_id=company_id) if company_id else activities
