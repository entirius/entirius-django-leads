# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The funnel report: stage moves, per-stage counts and companies per origin, aggregated by the database from the
`stage` activities that `transition_stage` writes. A company created in its first stage has no `stage` activity:
time in that stage is counted from the company's creation, and its creation counts as entering that stage (the stage
it is in without a move, else the `from` stage of its earliest move). Five queries whatever the number of companies."""

from datetime import date, timedelta
from typing import Any

from django.db.models import (
    Avg,
    CharField,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    OuterRef,
    QuerySet,
    Subquery,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel, Company
from django_leads.services import stage_service


def funnel(channel: Channel, *, origin: str = "", date_from: date | None = None, date_to: date | None = None) -> dict:
    window = {"origin": origin, "date_from": date_from, "date_to": date_to}
    transitions = _transitions(channel, window)
    return {
        "transitions": transitions,
        "stages": _stages(channel, window, transitions),
        "origins": _origins(channel, window),
    }


def _in_window(rows: QuerySet, window: dict[str, Any], prefix: str = "") -> QuerySet:
    if window["origin"]:
        rows = rows.filter(**{f"{prefix}origin": window["origin"]})
    if window["date_from"]:
        rows = rows.filter(created_at__date__gte=window["date_from"])
    if window["date_to"]:
        rows = rows.filter(created_at__date__lte=window["date_to"])
    return rows


def _transitions(channel: Channel, window: dict[str, Any]) -> list[dict[str, Any]]:
    """One query: moves grouped by (from, to) with the mean time since the company entered the source stage."""
    earlier = Activity.objects.filter(
        company=OuterRef("company"), kind=ActivityKind.STAGE, created_at__lt=OuterRef("created_at")
    ).order_by("-created_at", "-id")
    entered = Coalesce(Subquery(earlier.values("created_at")[:1]), F("company__created_at"))
    spent = ExpressionWrapper(F("created_at") - entered, output_field=DurationField())
    moves = Activity.objects.filter(company__channel=channel, kind=ActivityKind.STAGE)
    moves = _in_window(moves, window, prefix="company__")
    grouped = moves.values("data__from", "data__to").annotate(count=Count("id"), mean=Avg(spent))
    rows = grouped.order_by("-count", "data__from", "data__to")
    return [
        {"from_stage": row["data__from"], "to_stage": row["data__to"], "count": row["count"], "mean_seconds": _sec(row)}
        for row in rows
    ]


def _sec(row: dict[str, Any]) -> float | None:
    mean = row["mean"]
    return mean.total_seconds() if isinstance(mean, timedelta) else None


def _stages(channel: Channel, window: dict[str, Any], transitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Three queries (stages, current counts, starts); entered / left / mean come from the grouped moves, entered
    also from the companies created in the window that started in the stage."""
    origin = window["origin"]
    companies = Company.objects.filter(channel=channel, **({"origin": origin} if origin else {}))
    current = dict(companies.values_list("stage__key").annotate(count=Count("id")).order_by())
    started = _started(channel, window)
    rows = [_stage(stage, current.get(stage.key, 0), transitions) for stage in stage_service.list_stages(channel)]
    return [row | {"entered": row["entered"] + started.get(row["key"], 0)} for row in rows]


def _started(channel: Channel, window: dict[str, Any]) -> dict[str, int]:
    """One query: companies created in the window per the stage they started in — the `from` stage of their earliest
    move, else the stage they are in."""
    first_move = Activity.objects.filter(company=OuterRef("pk"), kind=ActivityKind.STAGE).order_by("created_at", "id")
    first_from = Subquery(first_move.values(key=KT("data__from"))[:1], output_field=CharField())
    companies = _in_window(Company.objects.filter(channel=channel), window)
    rows = (
        companies.annotate(start=Coalesce(first_from, F("stage__key"), output_field=CharField()))
        .values("start")
        .annotate(count=Count("id"))
    )
    return {row["start"]: row["count"] for row in rows.order_by()}


def _stage(stage, current: int, transitions: list[dict[str, Any]]) -> dict[str, Any]:
    out = [row for row in transitions if row["from_stage"] == stage.key]
    left = sum(row["count"] for row in out)
    timed = [(row["mean_seconds"], row["count"]) for row in out if row["mean_seconds"] is not None]
    weight = sum(count for _, count in timed)
    return {
        "key": stage.key,
        "label": stage.label,
        "current": current,
        "entered": sum(row["count"] for row in transitions if row["to_stage"] == stage.key),
        "left": left,
        "mean_seconds": sum(mean * count for mean, count in timed) / weight if weight else None,
    }


def _origins(channel: Channel, window: dict[str, Any]) -> list[dict[str, Any]]:
    companies = _in_window(Company.objects.filter(channel=channel), window)
    rows = companies.values("origin").annotate(companies=Count("id")).order_by("-companies", "origin")
    return [{"origin": row["origin"], "companies": row["companies"]} for row in rows]
