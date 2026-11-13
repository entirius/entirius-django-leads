# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The funnel report on a fixed timeline: moves, per-stage numbers, origins, filters and the query count."""

from datetime import UTC, date, datetime, timedelta

import pytest

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Stage
from django_leads.services import funnel_service, stage_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

# Noon: the date filters read the local day, the same day in any time zone within ±11 h.
T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)
DAY = 86400.0


def make_company(channel, domain: str, origin: str, created: datetime) -> Company:
    company = Company.objects.create(
        channel=channel,
        name=domain,
        domain=domain,
        origin=origin,
        stage=Stage.objects.get(key="new"),
        stage_entered_at=created,
        source=LeadSource.MANUAL,
    )
    Company.objects.filter(pk=company.pk).update(created_at=created)
    return company


def move(company: Company, key: str, at: datetime) -> None:
    stage_service.transition_stage(company, Stage.objects.get(key=key), actor="test")
    latest = Activity.objects.filter(company=company, kind=ActivityKind.STAGE).order_by("-id").first()
    Activity.objects.filter(pk=latest.pk).update(created_at=at)


@pytest.fixture
def funnel(channel):
    a = make_company(channel, "a.test", "fair", T0)
    b = make_company(channel, "b.test", "fair", T0)
    make_company(channel, "c.test", "list", T0 + timedelta(days=1))
    move(a, "contacted", T0 + timedelta(days=1))
    move(a, "replied", T0 + timedelta(days=3))
    move(b, "contacted", T0 + timedelta(days=2))
    return channel


def by_key(report: dict) -> dict[str, dict]:
    return {row["key"]: row for row in report["stages"]}


def test_transitions_with_mean_time_in_the_source_stage(funnel):
    report = funnel_service.funnel(funnel)
    assert report["transitions"] == [
        {"from_stage": "new", "to_stage": "contacted", "count": 2, "mean_seconds": 1.5 * DAY},
        {"from_stage": "contacted", "to_stage": "replied", "count": 1, "mean_seconds": 2 * DAY},
    ]


def test_per_stage_numbers(funnel):
    stages = by_key(funnel_service.funnel(funnel))
    assert list(stages) == ["new", "contacted", "won", "replied", "unresponsive"]
    pick = ("current", "entered", "left", "mean_seconds")
    assert [stages["new"][name] for name in pick] == [1, 0, 2, 1.5 * DAY]
    assert [stages["contacted"][name] for name in pick] == [1, 2, 1, 2 * DAY]
    assert [stages["replied"][name] for name in pick] == [1, 1, 0, None]
    assert [stages["won"][name] for name in pick] == [0, 0, 0, None]


def test_companies_per_origin(funnel):
    assert funnel_service.funnel(funnel)["origins"] == [
        {"origin": "fair", "companies": 2},
        {"origin": "list", "companies": 1},
    ]


def test_origin_filter(funnel):
    report = funnel_service.funnel(funnel, origin="list")
    assert report["transitions"] == [] and report["origins"] == [{"origin": "list", "companies": 1}]
    assert by_key(report)["new"]["current"] == 1 and by_key(report)["contacted"]["current"] == 0


def test_date_window(funnel):
    report = funnel_service.funnel(funnel, date_from=date(2026, 11, 2), date_to=date(2026, 11, 2))
    assert report["transitions"] == [
        {"from_stage": "new", "to_stage": "contacted", "count": 1, "mean_seconds": DAY},
    ]
    assert report["origins"] == [{"origin": "list", "companies": 1}]


def test_fixed_query_count(funnel, channel, django_assert_num_queries):
    for n in range(5):
        make_company(channel, f"more-{n}.test", "fair", T0)
    with django_assert_num_queries(4):
        funnel_service.funnel(channel, origin="fair", date_from=date(2026, 11, 1))


def test_funnel_endpoint(funnel, admin_api):
    response = admin_api.get(api_url("reports/funnel/?origin=fair&date_from=2026-11-01&date_to=2026-11-30"))
    body = response.json()
    assert response.status_code == 200 and body["transitions"][0]["count"] == 2
    assert body["origins"] == [{"origin": "fair", "companies": 2}]
    assert admin_api.get(api_url("reports/funnel/?date_from=nope")).status_code == 400
