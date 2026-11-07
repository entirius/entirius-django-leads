# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pilots of companies: CRUD with date checks, timeline notes, and the pilot report."""

from datetime import date, timedelta
from typing import Any

from django.db.models import Count, QuerySet

from django_leads.enums import ActivityKind, PilotResult
from django_leads.models import Channel, Company, Pilot
from django_leads.services import activity_service

PILOT_FIELDS = frozenset(
    {"start_date", "end_date", "success_criterion", "result", "result_note", "license_switch_date"}
)
LICENSE_WINDOW_DAYS = 30


def list_pilots(channel: Channel, *, company_id: int | None = None, result: str = "") -> QuerySet[Pilot]:
    pilots = Pilot.objects.filter(company__channel=channel).select_related("company")
    if company_id:
        pilots = pilots.filter(company_id=company_id)
    return pilots.filter(result=result) if result else pilots


def create_pilot(company: Company, fields: dict[str, Any], *, actor: str) -> Pilot:
    """Raises ValueError (field not editable, inconsistent dates)."""
    _check(fields)
    pilot = Pilot(company=company, **fields)
    _check_dates(pilot)
    pilot.save()
    activity_service.record(company, ActivityKind.NOTE, f"pilot started {pilot.start_date}", actor=actor)
    return pilot


def update_pilot(pilot: Pilot, updates: dict[str, Any], *, actor: str) -> Pilot:
    _check(updates)
    previous = pilot.result
    for field, value in updates.items():
        setattr(pilot, field, value)
    _check_dates(pilot)
    pilot.save()
    if pilot.result != previous:
        message = f"pilot {previous} -> {pilot.result}"
        activity_service.record(pilot.company, ActivityKind.NOTE, message, actor=actor)
    return pilot


def report(channel: Channel, *, as_of: date) -> dict[str, Any]:
    """Counts per result, running and overdue pilots, licence switches due soon, mean length of ended pilots."""
    pilots = Pilot.objects.filter(company__channel=channel)
    by_result = {result.value: 0 for result in PilotResult}
    by_result.update({row["result"]: row["n"] for row in pilots.values("result").annotate(n=Count("id"))})
    pending = pilots.filter(result=PilotResult.PENDING, start_date__lte=as_of)
    ended = pilots.exclude(result=PilotResult.PENDING).filter(end_date__isnull=False)
    lengths = [(pilot.end_date - pilot.start_date).days for pilot in ended]
    due = pilots.filter(
        result=PilotResult.SUCCESS,
        license_switch_date__gte=as_of,
        license_switch_date__lte=as_of + timedelta(days=LICENSE_WINDOW_DAYS),
    ).select_related("company")
    return {
        "as_of": as_of,
        "total": pilots.count(),
        "by_result": by_result,
        "running": pending.filter(end_date__isnull=True).count() + pending.filter(end_date__gte=as_of).count(),
        "overdue": pending.filter(end_date__lt=as_of).count(),
        "avg_length_days": round(sum(lengths) / len(lengths), 1) if lengths else None,
        "license_switch_due": [
            {
                "pilot_id": p.pk,
                "company_id": p.company_id,
                "domain": p.company.domain,
                "switch_date": p.license_switch_date,
            }
            for p in due.order_by("license_switch_date", "id")
        ],
    }


def _check(fields: dict[str, Any]) -> None:
    invalid = set(fields) - PILOT_FIELDS
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")


def _check_dates(pilot: Pilot) -> None:
    if pilot.end_date and pilot.end_date < pilot.start_date:
        raise ValueError("end_date is before start_date")
    if pilot.license_switch_date and pilot.license_switch_date < pilot.start_date:
        raise ValueError("license_switch_date is before start_date")
