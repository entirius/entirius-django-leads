# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import timedelta

import pytest
from django.utils import timezone

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Stage
from django_leads.services import company_service, funnel_service, import_service
from tests.conftest import api_url
from tests.test_import import HEADER

NOW = timezone.now()


def make_company(channel, domain: str, origin: str = "") -> Company:
    return company_service.upsert_company(
        channel, {"domain": domain, "name": domain, "source": LeadSource.CSV, "origin": origin}
    )[0]


def move(company: Company, to: str, at, *, previous: str | None = None) -> None:
    """A stage transition at a fixed time, written the way `transition_stage` writes it."""
    stage = Stage.objects.get(channel=company.channel, key=to)
    data = {"from": previous or company.stage.key, "to": to}
    activity = Activity.objects.create(company=company, kind=ActivityKind.STAGE, message="stage", data=data)
    Activity.objects.filter(pk=activity.pk).update(created_at=at)
    Company.objects.filter(pk=company.pk).update(stage=stage)
    company.refresh_from_db()


@pytest.fixture
def funnel(channel):
    """a: new -> contacted (1h) -> replied (3h), origin ecom.house. b: new -> contacted (2h), no origin. c: untouched."""
    start = NOW - timedelta(days=2)
    a, b, c = (
        make_company(channel, "a.test", "ecom.house"),
        make_company(channel, "b.test"),
        make_company(channel, "c.test"),
    )
    Company.objects.filter(pk__in=[a.pk, b.pk, c.pk]).update(created_at=start)
    move(a, "contacted", start + timedelta(hours=1))
    move(a, "replied", start + timedelta(hours=4))
    move(b, "contacted", start + timedelta(hours=2))
    return a, b, c


def by_pair(body):
    return {(row["from_stage"], row["to_stage"]): row for row in body["transitions"]}


def test_counts_and_average_time(channel, funnel):
    body = funnel_service.report(channel)
    pairs = by_pair(body)
    assert body["companies"] == 3
    assert pairs[("new", "contacted")]["count"] == 2
    assert pairs[("new", "contacted")]["avg_seconds_in_from_stage"] == 5400.0  # (1h + 2h) / 2
    assert pairs[("contacted", "replied")]["avg_seconds_in_from_stage"] == 10800.0
    stages = {row["stage"]: row for row in body["stages"]}
    assert (stages["contacted"]["entered"], stages["contacted"]["left"]) == (2, 1)
    assert stages["contacted"]["current"] == 1 and stages["new"]["current"] == 1  # b, c
    assert stages["replied"]["avg_seconds_in_stage"] is None  # nobody left it yet


def test_filter_by_origin(channel, funnel):
    body = funnel_service.report(channel, origin="ecom.house")
    assert body["companies"] == 1 and by_pair(body)[("new", "contacted")]["count"] == 1
    none = funnel_service.report(channel, origin="")
    assert none["companies"] == 2 and ("contacted", "replied") not in by_pair(none)
    assert funnel_service.report(channel, origin="nobody")["transitions"] == []
    assert {row["origin"]: row["companies"] for row in body["origins"]} == {"": 2, "ecom.house": 1}


def test_date_window_uses_transition_time_but_full_history(channel, funnel):
    start = NOW - timedelta(days=2)
    body = funnel_service.report(channel, date_from=start + timedelta(hours=3))
    assert list(by_pair(body)) == [("contacted", "replied")]
    assert by_pair(body)[("contacted", "replied")]["avg_seconds_in_from_stage"] == 10800.0  # entry was before window
    assert funnel_service.report(channel, date_to=start)["transitions"] == []


def test_empty_channel_and_company_without_activity(channel, company):
    body = funnel_service.report(channel)
    assert body["companies"] == 1 and body["transitions"] == []
    assert [(row["stage"], row["current"], row["avg_seconds_in_stage"]) for row in body["stages"]] == [("new", 1, None)]


def test_unknown_stage_in_history_is_reported_not_fatal(channel, company):
    """A deleted or renamed stage leaves its key in old timeline data."""
    activity = Activity.objects.create(
        company=company, kind=ActivityKind.STAGE, message="x", data={"from": "gone", "to": "new"}
    )
    body = funnel_service.report(channel)
    assert by_pair(body)[("gone", "new")]["count"] == 1 and activity.pk
    Activity.objects.create(company=company, kind=ActivityKind.STAGE, message="x", data={})  # malformed: ignored
    assert funnel_service.report(channel)["transitions"][0]["count"] == 1


def test_funnel_endpoint(admin_api, funnel):
    url = api_url("reports/funnel/")
    body = admin_api.get(url, {"origin": "ecom.house"}).json()
    assert body["companies"] == 1
    assert admin_api.get(url).json()["companies"] == 3
    assert admin_api.get(url, {"origin": ""}).json()["companies"] == 2
    window = {"date_from": (NOW - timedelta(days=1)).isoformat(), "date_to": (NOW - timedelta(days=3)).isoformat()}
    assert admin_api.get(url, window).status_code == 400
    assert admin_api.get(url, {"date_from": "yesterday"}).status_code == 400
    assert admin_api.get(api_url("reports/funnel/").replace("default-europe", "nope")).status_code == 404


def test_funnel_endpoint_needs_staff(customer_api, channel):
    assert customer_api.get(api_url("reports/funnel/")).status_code == 403


def test_origin_on_create_patch_list_and_response(admin_api, channel):
    created = admin_api.post(api_url("companies/"), {"domain": "shop.test", "origin": "partner-x"}, format="json")
    assert created.status_code == 201 and created.json()["origin"] == "partner-x"
    pk = created.json()["id"]
    assert (
        admin_api.patch(api_url(f"companies/{pk}/"), {"origin": "ecom.house"}, format="json").json()["origin"]
        == "ecom.house"
    )
    assert admin_api.patch(api_url(f"companies/{pk}/"), {"origin": None}, format="json").status_code == 400
    assert (
        admin_api.post(api_url("companies/"), {"domain": "x.test", "origin": "o" * 65}, format="json").status_code
        == 400
    )
    listing = admin_api.get(api_url("companies/"), {"origin": "ecom.house"}).json()
    assert [row["id"] for row in listing["results"]] == [pk]
    assert admin_api.get(api_url("companies/"), {"origin": "other"}).json()["count"] == 0


def test_csv_import_sets_origin_and_fills_only_empty(channel):
    content = f"{HEADER},origin\nA,a-shop.pl,,,,,,,,,,,ecom.house\nB,b-shop.pl,,,,,,,,,,,\n"
    batch = import_service.create_batch(channel, "leads.csv", len(content), "test")
    import_service.run_content(batch, content)
    assert Company.objects.get(domain="a-shop.pl").origin == "ecom.house"
    assert Company.objects.get(domain="b-shop.pl").origin == ""
    again = f"{HEADER},origin\nA,a-shop.pl,,,,,,,,,,,other\nB,b-shop.pl,,,,,,,,,,,partner\n"
    import_service.run_content(import_service.create_batch(channel, "again.csv", len(again), "test"), again)
    assert Company.objects.get(domain="a-shop.pl").origin == "ecom.house"  # never overwritten
    assert Company.objects.get(domain="b-shop.pl").origin == "partner"  # empty filled


def test_csv_import_overlong_origin_skips_row(channel):
    content = f"{HEADER},origin\nA,a-shop.pl,,,,,,,,,,,{'o' * 65}\n"
    batch = import_service.run_content(import_service.create_batch(channel, "x.csv", len(content), "test"), content)
    assert batch.report == [{"row": 2, "reason": "too_long:origin"}]
