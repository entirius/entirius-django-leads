# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Deals: deal stages, open / update / transition / delete, list and board, company card, timeline, tasks."""

from datetime import date
from decimal import Decimal

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_regional.models import Currency

from django_leads.enums import ActivityKind, AttributeEntity, AttributeKind, DealStageKind
from django_leads.models import Activity, AttributeDefinition, Contact, Deal, DealStage
from django_leads.services import deal_service, owner_service
from django_leads.signals import deal_stage_changed
from tests.conftest import api_url

pytestmark = pytest.mark.django_db
needs_todo = pytest.mark.skipif(not apps.is_installed("django_todo"), reason="django_todo not installed")


def currency(iso3: str) -> Currency:
    return Currency.objects.get_or_create(iso3=iso3, defaults={"name_en": iso3, "name_pl": iso3})[0]


@pytest.fixture
def pln(db) -> Currency:
    return currency("PLN")


@pytest.fixture
def eur(db) -> Currency:
    return currency("EUR")


@pytest.fixture
def pipeline(channel) -> dict[str, DealStage]:
    stages = [("lead", 0, "open"), ("offer", 10, "open"), ("won", 20, "won"), ("lost", 30, "lost")]
    return {
        key: DealStage.objects.create(channel=channel, key=key, label=key.title(), order=order, kind=kind)
        for key, order, kind in stages
    }


@pytest.fixture
def seller(db):
    return get_user_model().objects.create_user(username="seller", is_staff=True)


def open_deal(admin_api, company, **fields) -> dict:
    response = admin_api.post(api_url("deals/"), {"company_id": company.pk, "title": "Shop audit", **fields}, "json")
    assert response.status_code == 201, response.json()
    return response.json()


def move(admin_api, deal_id: int, **body):
    return admin_api.post(api_url(f"deals/{deal_id}/transition/"), body, "json")


def error_fields(response) -> set[str]:
    return {detail["field"] for detail in response.json()["details"]}


def deal_timeline(company) -> list[str]:
    rows = company.activities.filter(kind=ActivityKind.DEAL).order_by("pk")
    return list(rows.values_list("message", flat=True))


# --- deal stages -----------------------------------------------------------------------------------------------------


def test_deal_stages_are_listed_in_pipeline_order(admin_api, pipeline):
    rows = admin_api.get(api_url("deal-stages/")).json()["results"]
    assert [(row["key"], row["kind"]) for row in rows] == [
        ("lead", "open"),
        ("offer", "open"),
        ("won", "won"),
        ("lost", "lost"),
    ]


def test_a_deal_stage_key_is_unique_in_the_channel(admin_api, pipeline):
    response = admin_api.post(api_url("deal-stages/"), {"key": "won", "label": "Won again", "kind": "won"}, "json")
    assert response.status_code == 409


def test_a_deal_stage_holding_deals_cannot_be_deleted_or_change_kind(admin_api, pipeline, company):
    open_deal(admin_api, company)
    lead = pipeline["lead"].pk
    assert admin_api.delete(api_url(f"deal-stages/{lead}/")).status_code == 409
    assert admin_api.patch(api_url(f"deal-stages/{lead}/"), {"kind": "won"}, "json").status_code == 409
    assert admin_api.patch(api_url(f"deal-stages/{lead}/"), {"label": "Lead in"}, "json").status_code == 200
    assert admin_api.delete(api_url(f"deal-stages/{pipeline['offer'].pk}/")).status_code == 204


def test_company_stages_and_deal_stages_are_separate(admin_api, pipeline):
    company_stages = [row["key"] for row in admin_api.get(api_url("stages/")).json()["results"]]
    assert "lead" not in company_stages


# --- open --------------------------------------------------------------------------------------------------------------


def test_a_new_deal_starts_in_the_first_open_stage_with_the_company_s_owner(admin_api, pipeline, company, seller):
    owner_service.set_owner(company, seller, actor="test")
    deal = open_deal(admin_api, company)
    assert deal["stage"] == {"key": "lead", "label": "Lead", "kind": "open"}
    assert deal["owner"]["username"] == "seller"
    assert deal["created_by"] == "operator" and deal["closed_at"] is None
    assert deal["subject_ref"] == f"leads.Deal:{deal['id']}"
    assert deal["subject_label"] == f"{company.domain} — Shop audit"


def test_without_a_company_owner_the_creator_owns_the_deal(admin_api, pipeline, company):
    assert open_deal(admin_api, company)["owner"]["username"] == "operator"


def test_an_explicit_owner_wins_and_null_means_none(admin_api, pipeline, company, seller):
    assert open_deal(admin_api, company, owner_id=seller.pk)["owner"]["username"] == "seller"
    assert open_deal(admin_api, company, owner_id=None)["owner"] is None


def test_the_currency_defaults_to_the_channel_s(admin_api, pipeline, company, pln):
    company.channel.default_currency = pln
    company.channel.save()
    deal = open_deal(admin_api, company, value_one_off="12000.00")
    assert (deal["currency"], deal["value_one_off"]) == ("PLN", "12000.00")


def test_opening_a_deal_writes_the_company_timeline(admin_api, pipeline, company):
    deal = open_deal(admin_api, company)
    activity = Activity.objects.get(kind=ActivityKind.DEAL)
    assert (activity.company_id, activity.deal_id) == (company.pk, deal["id"])
    assert activity.message == "deal opened: Shop audit"


def test_a_company_of_another_channel_is_refused(admin_api, pipeline, company):
    response = admin_api.post(api_url("deals/"), {"company_id": 999999, "title": "x"}, "json")
    assert response.status_code == 400


def test_a_channel_without_an_open_stage_refuses_a_deal(admin_api, company):
    response = admin_api.post(api_url("deals/"), {"company_id": company.pk, "title": "x"}, "json")
    assert response.status_code == 409


# --- amounts -----------------------------------------------------------------------------------------------------------


def test_amounts_round_trip_as_decimal_strings(admin_api, pipeline, company, pln):
    deal = open_deal(admin_api, company, value_one_off="12000.5", value_recurring="499.99", currency="PLN")
    assert (deal["value_one_off"], deal["value_recurring"]) == ("12000.50", "499.99")
    assert Deal.objects.get(pk=deal["id"]).value_one_off == Decimal("12000.50")


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"value_one_off": 12000.5, "currency": "PLN"}, "value_one_off"),
        ({"value_one_off": "12.345", "currency": "PLN"}, "value_one_off"),
        ({"value_one_off": "123456789012345", "currency": "PLN"}, "value_one_off"),
        ({"value_one_off": "-1.00", "currency": "PLN"}, "value_one_off"),
        ({"value_recurring": "10.00"}, "currency"),
        ({"value_one_off": "1.00", "currency": "XYZ"}, "currency"),
    ],
)
def test_amounts_are_validated(admin_api, pipeline, company, pln, fields, field):
    response = admin_api.post(api_url("deals/"), {"company_id": company.pk, "title": "x", **fields}, "json")
    assert response.status_code == 400
    assert error_fields(response) == {field}
    assert not Deal.objects.exists()


def test_clearing_the_currency_of_a_valued_deal_is_refused(admin_api, pipeline, company, pln):
    deal = open_deal(admin_api, company, value_one_off="10.00", currency="PLN")
    response = admin_api.patch(api_url(f"deals/{deal['id']}/"), {"currency": None}, "json")
    assert response.status_code == 400


# --- update ------------------------------------------------------------------------------------------------------------


def test_update_changes_whitelisted_fields(admin_api, pipeline, shop, seller):
    deal = open_deal(admin_api, shop)
    piotr = shop.contacts.get(email="piotr@example-shop-4.test")
    body = {"title": "Audit + care", "contact_id": piotr.pk, "expected_close_date": "2026-12-31", "owner_id": seller.pk}
    updated = admin_api.patch(api_url(f"deals/{deal['id']}/"), body, "json").json()
    assert (updated["title"], updated["contact"]["id"], updated["expected_close_date"]) == (
        "Audit + care",
        piotr.pk,
        "2026-12-31",
    )
    assert updated["owner"]["username"] == "seller"
    assert deal_timeline(shop)[-1] == "deal owner: seller"


def test_a_contact_of_another_company_is_refused(admin_api, pipeline, company, shop):
    deal = open_deal(admin_api, company)
    other = shop.contacts.first()
    response = admin_api.patch(api_url(f"deals/{deal['id']}/"), {"contact_id": other.pk}, "json")
    assert response.status_code == 400 and error_fields(response) == {"contact_id"}


@pytest.mark.parametrize("field", ["stage", "stage_id", "owner", "owner_id", "company", "company_id", "closed_at"])
def test_the_service_whitelist_refuses_relations_and_state(admin_api, pipeline, company, field):
    deal = Deal.objects.get(pk=open_deal(admin_api, company)["id"])
    with pytest.raises(ValueError, match="not editable"):
        deal_service.update(deal, {field: None}, actor="test")


def test_stage_is_not_a_patch_field(admin_api, pipeline, company):
    deal = open_deal(admin_api, company)
    assert admin_api.patch(api_url(f"deals/{deal['id']}/"), {"stage": "won"}, "json").status_code == 400


# --- transition --------------------------------------------------------------------------------------------------------


def test_a_deal_goes_open_won_reopened_lost(admin_api, pipeline, company, django_capture_on_commit_callbacks):
    deal_id = open_deal(admin_api, company)["id"]
    moves = []
    deal_stage_changed.connect(handler := lambda sender, deal, previous, stage, **kw: moves.append(stage.key))
    try:
        with django_capture_on_commit_callbacks(execute=True):
            won = move(admin_api, deal_id, kind="won").json()
        assert won["stage"]["kind"] == "won" and won["closed_at"] is not None
        with django_capture_on_commit_callbacks(execute=True):
            reopened = move(admin_api, deal_id, stage_key="offer").json()
        assert reopened["closed_at"] is None and reopened["lost_reason"] == ""
        assert move(admin_api, deal_id, kind="lost").status_code == 400
        with django_capture_on_commit_callbacks(execute=True):
            lost = move(admin_api, deal_id, kind="lost", lost_reason="budget cut").json()
    finally:
        deal_stage_changed.disconnect(handler)
    assert (lost["stage"]["key"], lost["lost_reason"]) == ("lost", "budget cut") and lost["closed_at"]
    assert moves == ["won", "offer", "lost"]
    assert deal_timeline(company) == [
        "deal opened: Shop audit",
        "deal lead -> won",
        "deal won -> offer",
        "deal offer -> lost",
    ]
    assert Activity.objects.filter(deal_id=deal_id).order_by("pk").last().data == {
        "deal_id": deal_id,
        "from": "offer",
        "to": "lost",
    }


def test_a_lost_stage_without_a_reason_is_400(admin_api, pipeline, company):
    deal_id = open_deal(admin_api, company)["id"]
    response = move(admin_api, deal_id, stage_key="lost")
    assert response.status_code == 400 and error_fields(response) == {"lost_reason"}


def test_a_channel_without_a_lost_stage_refuses_losing_with_409(admin_api, pipeline, company):
    deal_id = open_deal(admin_api, company)["id"]
    pipeline["lost"].delete()
    response = move(admin_api, deal_id, kind="lost", lost_reason="budget")
    assert response.status_code == 409
    assert "no lost deal stage" in response.json()["message"]


def test_an_unknown_stage_key_is_404_and_a_target_is_required(admin_api, pipeline, company):
    deal_id = open_deal(admin_api, company)["id"]
    assert move(admin_api, deal_id, stage_key="nope").status_code == 404
    assert move(admin_api, deal_id).status_code == 400
    assert move(admin_api, deal_id, stage_key="won", kind="won").status_code == 400


def test_a_won_deal_does_not_move_the_company(admin_api, pipeline, company):
    stage_before = company.stage_id
    move(admin_api, open_deal(admin_api, company)["id"], kind="won")
    company.refresh_from_db()
    assert company.stage_id == stage_before


# --- delete ------------------------------------------------------------------------------------------------------------


def test_only_an_open_deal_can_be_deleted(admin_api, pipeline, company):
    first, second = open_deal(admin_api, company)["id"], open_deal(admin_api, company, title="Care")["id"]
    move(admin_api, second, kind="won")
    assert admin_api.delete(api_url(f"deals/{second}/")).status_code == 409
    assert admin_api.delete(api_url(f"deals/{first}/")).status_code == 204
    deleted = Activity.objects.get(message="deal deleted: Shop audit")
    assert deleted.data == {"deal_id": first}
    assert not Activity.objects.filter(deal_id=first).exists()  # entries stay, the link is cleared


def test_deleting_a_contact_keeps_the_deal(admin_api, pipeline, shop):
    piotr = shop.contacts.get(email="piotr@example-shop-4.test")
    deal_id = open_deal(admin_api, shop, contact_id=piotr.pk)["id"]
    Contact.objects.filter(pk=piotr.pk).delete()
    deal = admin_api.get(api_url(f"deals/{deal_id}/")).json()
    assert deal["contact"] is None and deal["title"] == "Shop audit"


# --- list ---------------------------------------------------------------------------------------------------------------


def test_filters_owner_kind_and_close_range(admin_api, pipeline, company, seller):
    mine = open_deal(admin_api, company, expected_close_date="2026-12-01")["id"]
    theirs = open_deal(admin_api, company, owner_id=seller.pk, expected_close_date="2027-02-01")["id"]
    move(admin_api, theirs, kind="won")

    def ids(**params) -> list[int]:
        return [row["id"] for row in admin_api.get(api_url("deals/"), params).json()["results"]]

    assert ids(owner="me") == [mine]
    assert ids(owner=str(seller.pk)) == [theirs]
    assert ids(kind="won") == [theirs]
    assert ids(kind="open") == [mine]
    assert ids(close_from="2027-01-01") == [theirs]
    assert ids(close_to="2026-12-31") == [mine]
    assert ids(sort="expected_close_date") == [mine, theirs]
    assert ids(search="ogrod") == [theirs, mine]


@pytest.mark.parametrize("params", [{"sort": "title"}, {"owner": "x"}, {"kind": "closed"}, {"close_from": "soon"}])
def test_bad_list_parameters_are_400(admin_api, pipeline, params):
    assert admin_api.get(api_url("deals/"), params).status_code == 400


def test_the_list_does_not_query_per_deal(admin_api, pipeline, company, seller, pln):
    open_deal(admin_api, company, owner_id=seller.pk, value_one_off="1.00", currency="PLN")
    with CaptureQueriesContext(connection) as one:
        admin_api.get(api_url("deals/"))
    for _ in range(4):
        open_deal(admin_api, company, owner_id=seller.pk, value_one_off="1.00", currency="PLN")
    with CaptureQueriesContext(connection) as five:
        assert admin_api.get(api_url("deals/")).json()["count"] == 5
    assert len(five) == len(one)


# --- board -------------------------------------------------------------------------------------------------------------


def test_the_board_has_stages_in_order_with_totals_per_currency(admin_api, pipeline, company, pln, eur):
    open_deal(admin_api, company, value_one_off="1000.00", value_recurring="100.00", currency="PLN")
    open_deal(admin_api, company, value_one_off="500.50", value_recurring="1200.00", recurring_period="year", currency="PLN")  # fmt: skip
    open_deal(admin_api, company, value_one_off="300.00", currency="EUR")
    open_deal(admin_api, company, title="No value yet")
    with CaptureQueriesContext(connection) as queries:
        columns = admin_api.get(api_url("deals/board/")).json()["results"]
    assert [column["stage"]["key"] for column in columns] == ["lead", "offer", "won", "lost"]
    lead = columns[0]
    assert lead["count"] == 4 and len(lead["deals"]) == 4
    assert lead["totals"] == [
        {"currency": "EUR", "one_off": "300.00", "recurring": "0.00"},
        {"currency": "PLN", "one_off": "1500.50", "recurring": "200.00"},
    ]
    assert columns[1] == {**columns[1], "count": 0, "totals": [], "deals": []}
    assert len(queries) <= 8, [query["sql"] for query in queries]


def test_the_board_query_count_does_not_grow_with_deals_or_stages(admin_api, pipeline, company, pln):
    open_deal(admin_api, company, value_one_off="1.00", currency="PLN")
    with CaptureQueriesContext(connection) as before:
        admin_api.get(api_url("deals/board/"))
    for key in ("demo", "trial"):
        DealStage.objects.create(channel=company.channel, key=key, label=key, order=15)
    deal_id = open_deal(admin_api, company, value_one_off="1.00", currency="PLN")["id"]
    move(admin_api, deal_id, kind="won")
    open_deal(admin_api, company)
    with CaptureQueriesContext(connection) as after:
        admin_api.get(api_url("deals/board/"))
    assert len(after) == len(before)


def test_a_board_column_holds_at_most_25_deals_oldest_in_stage_first(admin_api, pipeline, company):
    for number in range(27):
        open_deal(admin_api, company, title=f"Deal {number}")
    lead = admin_api.get(api_url("deals/board/")).json()["results"][0]
    assert lead["count"] == 27 and len(lead["deals"]) == 25
    assert lead["deals"][0]["title"] == "Deal 0"


def test_the_board_filters_by_owner(admin_api, pipeline, company, seller):
    open_deal(admin_api, company, owner_id=seller.pk)
    open_deal(admin_api, company)
    lead = admin_api.get(api_url("deals/board/"), {"owner": "me"}).json()["results"][0]
    assert lead["count"] == 1 and lead["deals"][0]["owner"]["username"] == "operator"


# --- company card, timeline, attributes ---------------------------------------------------------------------------------


def test_the_company_card_counts_deals_and_lists_them(admin_api, pipeline, company):
    first = open_deal(admin_api, company)["id"]
    open_deal(admin_api, company, title="Care")
    move(admin_api, first, kind="won")
    card = admin_api.get(api_url(f"companies/{company.pk}/")).json()
    assert card["deals"] == {"open": 1, "won": 1, "lost": 0}
    rows = admin_api.get(api_url(f"companies/{company.pk}/deals/")).json()["results"]
    assert {row["title"] for row in rows} == {"Shop audit", "Care"}
    assert admin_api.get(api_url("companies/999999/deals/")).status_code == 404


def test_the_deal_timeline_lists_only_its_entries(admin_api, pipeline, company):
    first = open_deal(admin_api, company)["id"]
    open_deal(admin_api, company, title="Care")
    move(admin_api, first, kind="won")
    rows = admin_api.get(api_url(f"deals/{first}/activities/")).json()["results"]
    assert [row["message"] for row in rows] == ["deal lead -> won", "deal opened: Shop audit"]
    assert {row["deal_id"] for row in rows} == {first}


def test_deal_attributes_are_validated_and_listed(admin_api, pipeline, company):
    AttributeDefinition.objects.create(
        channel=company.channel, entity=AttributeEntity.DEAL, key="seats", label="Seats", kind=AttributeKind.NUMBER
    )
    fields = admin_api.get(api_url("fields/"), {"entity": "deal"}).json()["results"]
    assert [field["name"] for field in fields] == ["attr.seats"]
    assert "attr.seats" not in [field["name"] for field in admin_api.get(api_url("fields/")).json()["results"]]
    deal = open_deal(admin_api, company, attributes={"seats": "12"})
    assert deal["attributes"] == {"seats": "12"}
    bad = admin_api.patch(api_url(f"deals/{deal['id']}/"), {"attributes": {"seats": "many"}}, "json")
    assert bad.status_code == 400
    definition = AttributeDefinition.objects.get(key="seats")
    assert admin_api.delete(api_url(f"attributes/{definition.pk}/")).status_code == 409


def test_the_channel_sets_a_default_currency(admin_api, channel, pln):
    response = admin_api.patch(api_url("channel/"), {"default_currency": "PLN"}, "json")
    assert response.json()["default_currency"] == "PLN"
    assert admin_api.patch(api_url("channel/"), {"default_currency": "XYZ"}, "json").status_code == 400
    assert admin_api.patch(api_url("channel/"), {"default_currency": None}, "json").json()["default_currency"] is None


def test_deals_need_the_companies_area(customer_api, pipeline):
    assert customer_api.get(api_url("deals/")).status_code == 403


def test_the_service_refuses_a_stage_of_another_channel(admin_api, pipeline, company):
    from django_leads.models import Channel

    other = Channel.objects.create(idx="other")
    stage = DealStage.objects.create(channel=other, key="won", label="Won", kind=DealStageKind.WON)
    deal = Deal.objects.get(pk=open_deal(admin_api, company)["id"])
    with pytest.raises(ValueError, match="another channel"):
        deal_service.transition(deal, stage, actor="test")


# --- tasks -------------------------------------------------------------------------------------------------------------


@needs_todo
def test_a_task_about_a_deal_shows_on_the_company_timeline(
    admin_api, pipeline, company, django_capture_on_commit_callbacks
):
    from django_todo.services import task_service

    deal = open_deal(admin_api, company)
    with django_capture_on_commit_callbacks(execute=True):
        task_service.create(
            channel_idx=company.channel.idx, subject_ref=deal["subject_ref"], subject_label=deal["subject_label"],
            kind="todo", title="Send the offer", created_by="seller",
        )  # fmt: skip
    activity = Activity.objects.get(kind=ActivityKind.TASK)
    assert (activity.company_id, activity.deal_id, activity.message) == (company.pk, deal["id"], "task: Send the offer")
    assert admin_api.get(api_url(f"deals/{deal['id']}/")).json()["open_tasks"] == 1


@needs_todo
def test_a_deal_task_of_another_channel_is_ignored(admin_api, pipeline, company, django_capture_on_commit_callbacks):
    from django_todo.services import task_service

    deal = open_deal(admin_api, company)
    with django_capture_on_commit_callbacks(execute=True):
        task_service.create(
            channel_idx="other", subject_ref=deal["subject_ref"], subject_label="x", kind="todo", title="t",
            created_by="a",
        )  # fmt: skip
    assert not Activity.objects.filter(kind=ActivityKind.TASK).exists()
    assert admin_api.get(api_url(f"deals/{deal['id']}/")).json()["open_tasks"] == 0


def test_dates_are_iso(admin_api, pipeline, company):
    deal = open_deal(admin_api, company, expected_close_date=date(2026, 12, 31).isoformat())
    assert deal["expected_close_date"] == "2026-12-31"


# --- review findings ---------------------------------------------------------------------------------------------------


def test_an_update_does_not_write_back_a_stale_stage(admin_api, pipeline, company):
    deal_id = open_deal(admin_api, company)["id"]
    stale = Deal.objects.select_related("company").get(pk=deal_id)
    move(admin_api, deal_id, kind="won")
    deal_service.update(stale, {"title": "Renamed"}, actor="test")
    fresh = Deal.objects.get(pk=deal_id)
    assert (fresh.title, fresh.stage.key) == ("Renamed", "won") and fresh.closed_at is not None


@pytest.mark.parametrize("field", ["title", "recurring_period"])
def test_the_service_refuses_null_for_required_fields(admin_api, pipeline, company, field):
    deal = Deal.objects.get(pk=open_deal(admin_api, company)["id"])
    with pytest.raises(deal_service.InvalidDeal, match=field):
        deal_service.update(deal, {field: None}, actor="test")


def test_anonymising_a_contact_takes_it_off_its_deals(admin_api, pipeline, shop):
    from django_leads.services import retention_service

    piotr = shop.contacts.get(email="piotr@example-shop-4.test")
    deal_id = open_deal(admin_api, shop, contact_id=piotr.pk)["id"]
    retention_service.anonymise_contact(piotr)
    deal = Deal.objects.get(pk=deal_id)
    assert deal.contact_id is None and deal.title == "Shop audit"


@needs_todo
def test_deleting_a_deal_cancels_its_open_tasks(admin_api, pipeline, company, django_capture_on_commit_callbacks):
    from django_todo.models import Task
    from django_todo.services import task_service

    deal = open_deal(admin_api, company)
    with django_capture_on_commit_callbacks(execute=True):
        task_service.create(
            channel_idx=company.channel.idx, subject_ref=deal["subject_ref"], subject_label="x", kind="todo",
            title="Send the offer", created_by="seller",
        )  # fmt: skip
    with django_capture_on_commit_callbacks(execute=True):
        assert admin_api.delete(api_url(f"deals/{deal['id']}/")).status_code == 204
    assert Task.objects.get(subject_ref=deal["subject_ref"]).status == "cancelled"
