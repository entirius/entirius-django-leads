# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""U11 — the channel history: every activity, filters, settings entries, purge."""

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel
from django_leads.services import activity_service
from django_leads.tasks import purge_history
from tests.conftest import api_url

pytestmark = pytest.mark.django_db


def _entry(company, kind, actor, created_at=None):
    activity = activity_service.record(company, kind, f"{kind} by {actor}", actor=actor)
    if created_at:
        Activity.objects.filter(pk=activity.pk).update(created_at=created_at)
    return activity


def test_every_entry_carries_its_channel(company, channel):
    assert _entry(company, ActivityKind.NOTE, "anna").channel_id == channel.pk
    assert Activity.objects.create(company=company, kind=ActivityKind.NOTE, message="x").channel_id == channel.pk


def test_history_lists_the_channel_with_company_names(admin_api, company, shop):
    _entry(company, ActivityKind.NOTE, "anna")
    _entry(shop, ActivityKind.TASK, "piotr")
    other = Channel.objects.create(idx="other", name="Other")
    activity_service.record_channel(other, ActivityKind.SETTINGS, "settings: x", actor="anna")
    body = admin_api.get(api_url("activities/")).json()
    assert body["count"] == 2
    assert {row["company_name"] for row in body["results"]} == {company.name, shop.name}
    assert body["results"][0]["deal_title"] is None


def test_history_filters_by_actor_kind_and_company(admin_api, company, shop):
    _entry(company, ActivityKind.NOTE, "anna")
    _entry(company, ActivityKind.TASK, "anna")
    _entry(shop, ActivityKind.TASK, "piotr")
    assert admin_api.get(api_url("activities/?actor=anna")).json()["count"] == 2
    assert admin_api.get(api_url("activities/?kind=task")).json()["count"] == 2
    assert admin_api.get(api_url("activities/?kind=task,note&actor=anna")).json()["count"] == 2
    assert admin_api.get(api_url(f"activities/?company={shop.pk}&kind=note")).json()["count"] == 0
    assert admin_api.get(api_url("activities/?kind=Task;drop")).status_code == 400


def test_history_filters_by_day_both_ends_included(admin_api, company):
    today = timezone.localdate()
    noon = timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=12)
    _entry(company, ActivityKind.NOTE, "anna", noon - timedelta(days=3))
    _entry(company, ActivityKind.NOTE, "anna", noon - timedelta(days=1))
    _entry(company, ActivityKind.NOTE, "anna", noon)
    day = (today - timedelta(days=1)).isoformat()
    assert admin_api.get(api_url(f"activities/?date_from={day}&date_to={day}")).json()["count"] == 1
    assert admin_api.get(api_url(f"activities/?date_from={day}")).json()["count"] == 2
    assert admin_api.get(api_url(f"activities/?date_to={day}")).json()["count"] == 2
    assert admin_api.get(api_url("activities/?date_from=yesterday")).status_code == 400


def test_history_pages(admin_api, company):
    for number in range(3):
        _entry(company, ActivityKind.NOTE, f"user{number}")
    body = admin_api.get(api_url("activities/?page_size=2")).json()
    assert (body["count"], len(body["results"])) == (3, 2)


def test_actors_list_the_person_filter(admin_api, company, shop):
    _entry(company, ActivityKind.NOTE, "piotr")
    _entry(shop, ActivityKind.NOTE, "anna")
    _entry(shop, ActivityKind.NOTE, "anna")
    assert admin_api.get(api_url("activities/actors/")).json() == {"results": ["anna", "piotr"]}


def test_a_person_takes_their_entries_through_a_module_and_imports_are_one(admin_api, company, shop):
    _entry(company, ActivityKind.NOTE, "manager")
    _entry(company, ActivityKind.NOTE, "manager via ai-crm (cms)")
    _entry(shop, ActivityKind.NOTE, "manager via ai-crm (mcp)")
    _entry(shop, ActivityKind.NOTE, "managers")
    _entry(company, ActivityKind.IMPORT, "import:1")
    _entry(shop, ActivityKind.IMPORT, "import:2")
    assert admin_api.get(api_url("activities/actors/")).json() == {"results": ["import:", "manager", "managers"]}
    assert admin_api.get(api_url("activities/?actor=manager")).json()["count"] == 3
    assert admin_api.get(api_url("activities/?actor=managers")).json()["count"] == 1
    assert admin_api.get(api_url("activities/?actor=import:")).json()["count"] == 2
    assert admin_api.get(api_url("activities/?actor=import:1")).json()["count"] == 1


def test_settings_change_is_in_the_history(admin_api, channel):
    url = api_url("channel/")
    response = admin_api.patch(url, {"history_months": 12, "resume_days": {"not_now": 10}}, format="json")
    assert response.status_code == 200 and response.json()["history_months"] == 12
    entry = admin_api.get(api_url("activities/?kind=settings")).json()["results"][0]
    assert entry["company_id"] is None and entry["actor"] == "operator"
    assert entry["data"]["changes"]["history_months"] == {"from": 24, "to": 12}
    assert entry["data"]["changes"]["resume_days"] == {"from": {}, "to": {"not_now": 10}}
    admin_api.patch(url, {"history_months": 12}, format="json")
    assert Activity.objects.filter(kind=ActivityKind.SETTINGS).count() == 1


@pytest.mark.parametrize("months", [0, 121])
def test_history_months_out_of_range_400(admin_api, channel, months):
    assert admin_api.patch(api_url("channel/"), {"history_months": months}, format="json").status_code == 400


def test_purge_deletes_entries_older_than_the_channel_months(company, channel):
    now = timezone.now()
    old = _entry(company, ActivityKind.NOTE, "anna", activity_service.months_before(now, 24) - timedelta(days=1))
    kept = _entry(company, ActivityKind.NOTE, "anna", activity_service.months_before(now, 24) + timedelta(days=1))
    assert purge_history(as_of=now.isoformat()) == {channel.idx: 1}
    assert not Activity.objects.filter(pk=old.pk).exists() and Activity.objects.filter(pk=kept.pk).exists()
    channel.history_months = 1
    channel.save()
    assert purge_history(as_of=now.isoformat()) == {channel.idx: 1}


@pytest.mark.parametrize(
    ("moment", "months", "expected"),
    [
        (datetime(2026, 3, 31, 8), 1, datetime(2026, 2, 28, 8)),
        (datetime(2026, 11, 15), 24, datetime(2024, 11, 15)),
        (datetime(2026, 1, 10), 1, datetime(2025, 12, 10)),
    ],
)
def test_months_before(moment, months, expected):
    assert activity_service.months_before(moment, months) == expected
