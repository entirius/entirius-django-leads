# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The deal board: one column per deal stage in pipeline order, totals per currency from one aggregate query and the
first deals of every column from one windowed query — never a query per stage, never a Python sum of amounts."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, QuerySet, Sum, Value, When, Window
from django.db.models.functions import RowNumber

from django_leads.enums import RecurringPeriod
from django_leads.models import Channel, Deal, DealStage
from django_leads.services import deal_service, deal_stage_service, owner_service

COLUMN_LIMIT = 25
CENT = Decimal("0.01")
MONTHS_PER_YEAR = 12
_MONEY = DecimalField(max_digits=20, decimal_places=6)
# A yearly recurring value counts as a twelfth per month: the board's `recurring` total is monthly.
MONTHLY_RECURRING = Case(
    When(recurring_period=RecurringPeriod.YEAR, then=F("value_recurring") / Value(MONTHS_PER_YEAR)),
    default=F("value_recurring"),
    output_field=_MONEY,
)


@dataclass(frozen=True)
class Total:
    currency: str
    one_off: Decimal
    recurring: Decimal


@dataclass
class Column:
    stage: DealStage
    count: int = 0
    totals: list[Total] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)


def board(channel: Channel, *, owner: str = "", user_id: int | None = None) -> list[Column]:
    """`owner` as in the list (me / none / id). Three queries: stages, aggregates, the first deals."""
    deals = owner_service.filter_by_owner(Deal.objects.filter(channel=channel), owner, user_id)
    columns = {stage.pk: Column(stage) for stage in deal_stage_service.list_stages(channel)}
    for row in _aggregates(deals):
        column = columns[row["stage_id"]]
        column.count += row["count"]
        if row["currency__iso3"]:
            column.totals.append(_total(row))
    for deal in _first_deals(deals):
        columns[deal.stage_id].deals.append(deal)
    return list(columns.values())


def _aggregates(deals: QuerySet) -> QuerySet:
    rows = deals.values("stage_id", "currency__iso3")
    rows = rows.annotate(count=Count("id"), one_off=Sum("value_one_off"), recurring=Sum(MONTHLY_RECURRING))
    return rows.order_by("stage_id", "currency__iso3")


def _first_deals(deals: QuerySet) -> list[Deal]:
    rank = Window(RowNumber(), partition_by=[F("stage_id")], order_by=[F("stage_entered_at").asc(), F("id").asc()])
    ranked = deals.select_related(*deal_service.RELATED).annotate(rank=rank).filter(rank__lte=COLUMN_LIMIT)
    return list(ranked.order_by("stage_id", "rank"))


def _total(row: dict) -> Total:
    one_off, recurring = (row[name] or Decimal(0) for name in ("one_off", "recurring"))
    return Total(row["currency__iso3"], one_off.quantize(CENT), Decimal(recurring).quantize(CENT))
