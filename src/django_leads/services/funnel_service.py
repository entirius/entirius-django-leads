# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Funnel report from the `stage` Activity rows: transitions between stages and the time spent before each one.

Time in a stage runs from the entry (the previous `stage` activity into it, or the company's creation for the first
stage) to the transition that leaves it. A company still sitting in its stage has no exit yet and is not averaged.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from django.db.models import Count

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel, Company


def report(
    channel: Channel, *, origin: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None
) -> dict[str, Any]:
    """`origin` None = any, "" = companies without an origin. The date window applies to the transition's time."""
    companies = Company.objects.filter(channel=channel)
    if origin is not None:
        companies = companies.filter(origin=origin)
    created = dict(companies.values_list("pk", "created_at"))
    rows = (
        Activity.objects.filter(company_id__in=created, kind=ActivityKind.STAGE)
        .order_by("company_id", "created_at", "id")
        .values_list("company_id", "created_at", "data")
    )
    pairs: dict[tuple[str, str], list[float]] = defaultdict(list)
    exits: dict[str, list[float]] = defaultdict(list)
    entries: dict[str, int] = defaultdict(int)
    entered_at: dict[int, datetime] = {}
    for company_id, at, data in rows.iterator():
        origin_stage, target = data.get("from"), data.get("to")
        if not origin_stage or not target:
            continue
        since = entered_at.get(company_id, created[company_id])
        entered_at[company_id] = at
        if (date_from and at < date_from) or (date_to and at > date_to):
            continue
        seconds = max((at - since).total_seconds(), 0.0)
        pairs[(origin_stage, target)].append(seconds)
        exits[origin_stage].append(seconds)
        entries[target] += 1
    current = dict(companies.values_list("stage__key").annotate(n=Count("id")))
    stages = sorted(set(current) | set(entries) | set(exits))
    return {
        "companies": len(created),
        "transitions": sorted(
            (
                {"from_stage": a, "to_stage": b, "count": len(times), "avg_seconds_in_from_stage": _mean(times)}
                for (a, b), times in pairs.items()
            ),
            key=lambda row: (-row["count"], row["from_stage"], row["to_stage"]),
        ),
        "stages": [
            {
                "stage": key,
                "current": current.get(key, 0),
                "entered": entries.get(key, 0),
                "left": len(exits.get(key, [])),
                "avg_seconds_in_stage": _mean(exits.get(key, [])),
            }
            for key in stages
        ],
        "origins": [
            {"origin": row["origin"], "companies": row["n"]}
            for row in Company.objects.filter(channel=channel)
            .values("origin")
            .annotate(n=Count("id"))
            .order_by("-n", "origin")
        ],
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None
