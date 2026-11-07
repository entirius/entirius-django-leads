# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import date

import pytest
from django.utils import timezone

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Pilot
from django_leads.services import pilot_service
from tests.conftest import api_url

TODAY = date(2026, 11, 10)


def make(company, **fields) -> Pilot:
    return Pilot.objects.create(company=company, **{"start_date": date(2026, 11, 1), **fields})


def test_create_pilot_over_api_writes_timeline(admin_api, company):
    body = {"company_id": company.pk, "start_date": "2026-11-10", "success_criterion": "10 orders in 30 days"}
    response = admin_api.post(api_url("pilots/"), body, format="json")
    assert response.status_code == 201
    assert response.json()["result"] == "pending" and response.json()["license_switch_date"] is None
    assert Activity.objects.filter(company=company, kind=ActivityKind.NOTE, message="pilot started 2026-11-10").exists()


def test_create_rejects_foreign_company_and_bad_dates(admin_api, company):
    url = api_url("pilots/")
    assert admin_api.post(url, {"company_id": 999999, "start_date": "2026-11-10"}, format="json").status_code == 404
    bad = {"company_id": company.pk, "start_date": "2026-11-10", "end_date": "2026-11-01"}
    assert admin_api.post(url, bad, format="json").status_code == 400
    bad = {"company_id": company.pk, "start_date": "2026-11-10", "license_switch_date": "2026-11-01"}
    assert admin_api.post(url, bad, format="json").status_code == 400
    assert admin_api.post(url, {"company_id": company.pk, "start_date": "nope"}, format="json").status_code == 400


def test_patch_result_writes_note_and_clears_dates(admin_api, company):
    pilot = make(company, end_date=date(2026, 12, 1), license_switch_date=date(2026, 12, 15))
    url = api_url(f"pilots/{pilot.pk}/")
    response = admin_api.patch(url, {"result": "success", "end_date": None, "license_switch_date": None}, format="json")
    assert response.status_code == 200 and response.json()["end_date"] is None
    assert Activity.objects.filter(company=company, message="pilot pending -> success").exists()
    assert admin_api.patch(url, {"result": None}, format="json").status_code == 400
    assert admin_api.patch(url, {"end_date": "2026-10-01"}, format="json").status_code == 400  # before start


def test_list_filters_and_delete(admin_api, company):
    first, second = make(company), make(company, result="failure")
    url = api_url("pilots/")
    assert admin_api.get(url).json()["count"] == 2
    assert [row["id"] for row in admin_api.get(url, {"result": "failure"}).json()["results"]] == [second.pk]
    assert admin_api.get(url, {"company": company.pk + 1}).json()["count"] == 0
    assert admin_api.delete(f"{url}{first.pk}/").status_code == 204
    assert admin_api.get(f"{url}{first.pk}/").status_code == 404


def test_report(admin_api, channel, company):
    make(company)  # running, open-ended
    make(company, end_date=date(2026, 11, 20))  # running
    make(company, end_date=date(2026, 11, 5))  # overdue
    make(company, result="success", end_date=date(2026, 11, 21), license_switch_date=date(2026, 11, 30))  # 20 days
    make(company, result="success", end_date=date(2026, 11, 11), license_switch_date=date(2027, 3, 1))  # far
    make(company, result="failure", end_date=date(2026, 11, 7))  # 6 days
    make(company, start_date=date(2026, 12, 1))  # not started yet: counted in total only
    report = pilot_service.report(channel, as_of=TODAY)
    assert report["total"] == 7
    assert report["by_result"] == {"pending": 4, "success": 2, "failure": 1, "cancelled": 0}
    assert (report["running"], report["overdue"]) == (2, 1)
    assert report["avg_length_days"] == pytest.approx((20 + 10 + 6) / 3)
    assert [row["switch_date"] for row in report["license_switch_due"]] == [date(2026, 11, 30)]

    response = admin_api.get(api_url("pilots/report/"), {"as_of": "2026-11-10"})
    assert response.status_code == 200 and response.json()["license_switch_due"][0]["domain"] == "ogrod.pl"
    assert admin_api.get(api_url("pilots/report/"), {"as_of": "x"}).status_code == 400


def test_empty_report(admin_api, channel):
    body = admin_api.get(api_url("pilots/report/")).json()
    assert body["total"] == 0 and body["avg_length_days"] is None and body["license_switch_due"] == []


def test_pilots_are_scoped_to_the_channel(admin_api, company, polish):
    from django_leads.models import Channel, Company, Stage

    other = Channel.objects.create(idx="other", name="Other", default_language=polish)
    stage = Stage.objects.create(channel=other, key="new", label="New", order=0)
    foreign = Company.objects.create(
        channel=other,
        domain="x.test",
        name="x",
        source="csv",
        stage=stage,
        stage_entered_at=timezone.now(),
    )
    pilot = make(foreign)
    assert admin_api.get(api_url(f"pilots/{pilot.pk}/")).status_code == 404
    assert admin_api.get(api_url("pilots/")).json()["count"] == 0
    body = {"company_id": foreign.pk, "start_date": "2026-11-10"}
    assert admin_api.post(api_url("pilots/"), body, format="json").status_code == 404


def test_pilots_need_staff(customer_api, channel):
    assert customer_api.get(api_url("pilots/")).status_code == 403
    assert customer_api.get(api_url("pilots/report/")).status_code == 403
