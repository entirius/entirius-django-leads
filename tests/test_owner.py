# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Company owner: picker, set / clear / bulk assign, list filter and sort, import, alerts and the draft mailbox."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_communicator import signals as communicator_signals
from django_communicator.models import Thread
from django_notifications.models import Channel as NotificationChannel
from django_notifications.models import Notification

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Contact, ImportBatch, Stage
from django_leads.services import alert_service, company_service, import_service, outreach_service, owner_service
from tests.conftest import CHANNEL_IDX, api_url
from tests.test_import_rules import ROWS
from tests.test_stage_events import reply, thread_of

pytestmark = pytest.mark.django_db


def staff(username: str, **fields):
    return get_user_model().objects.create_user(username=username, **{"is_staff": True, **fields})


def operator():
    return get_user_model().objects.get(username="operator")


def new_company(channel, domain: str, owner=None) -> Company:
    return Company.objects.create(
        channel=channel,
        name=domain,
        domain=domain,
        stage=Stage.objects.get(channel=channel, key="new"),
        stage_entered_at=timezone.now(),
        source=LeadSource.MANUAL,
        owner=owner,
    )


def owner_notes(company: Company) -> list[str]:
    rows = Activity.objects.filter(company=company, kind=ActivityKind.NOTE, message__startswith="owner:")
    return list(rows.values_list("message", flat=True))


# --- picker ------------------------------------------------------------------------------------------------------


def test_picker_lists_only_active_staff_people(admin_api, channel):
    staff("anna", first_name="Anna", last_name="Nowak", email="anna@example.test")
    staff("gone", is_active=False)
    staff("customer", is_staff=False)
    body = admin_api.get(api_url("assignable-users/")).json()
    assert [user["username"] for user in body["results"]] == ["operator", "anna"]
    assert body["results"][1] == {
        "id": get_user_model().objects.get(username="anna").pk,
        "username": "anna",
        "name": "Anna Nowak",
    }


@pytest.mark.skipif(not apps.is_installed("django_access"), reason="service users come with django_access")
def test_picker_and_owner_leave_out_service_users(admin_api, channel):
    from django_access.models import Application

    service = staff("access-app-1")
    Application.objects.create(name="Shop", service_user=service)
    assert "access-app-1" not in [u["username"] for u in admin_api.get(api_url("assignable-users/")).json()["results"]]
    with pytest.raises(owner_service.UnknownOwner):
        owner_service.resolve_owner(service.pk)


def test_picker_searches_and_caps(admin_api, channel):
    for number in range(owner_service.PICKER_LIMIT + 5):
        staff(f"seller{number:02d}")
    assert len(admin_api.get(api_url("assignable-users/")).json()["results"]) == owner_service.PICKER_LIMIT
    staff("zofia", last_name="Kowalska")
    found = admin_api.get(api_url("assignable-users/"), {"search": "kowal"}).json()["results"]
    assert [user["username"] for user in found] == ["zofia"]


def test_picker_needs_staff(customer_api, channel):
    assert customer_api.get(api_url("assignable-users/")).status_code == 403


# --- set / clear -------------------------------------------------------------------------------------------------


def test_manual_create_defaults_to_the_requesting_user(admin_api, channel):
    body = admin_api.post(api_url("companies/"), {"domain": "own.test"}, format="json").json()
    assert body["owner"]["username"] == "operator"
    assert owner_notes(Company.objects.get(pk=body["id"])) == ["owner: operator"]


def test_manual_create_by_a_caller_who_cannot_own_has_no_owner(company):
    service = staff("access-app-1")
    with mock.patch.object(owner_service, "_people", return_value=get_user_model().objects.none()):
        assert owner_service.default_owner(service) is None
    assert owner_service.default_owner(staff("anna")).username == "anna"


def test_manual_create_with_null_owner_has_none(admin_api, channel):
    body = admin_api.post(api_url("companies/"), {"domain": "own.test", "owner_id": None}, format="json").json()
    assert body["owner"] is None and owner_notes(Company.objects.get(pk=body["id"])) == []


def test_manual_create_names_an_owner(admin_api, channel):
    anna = staff("anna")
    body = admin_api.post(api_url("companies/"), {"domain": "own.test", "owner_id": anna.pk}, format="json").json()
    assert body["owner"] == {"id": anna.pk, "username": "anna", "name": ""}


def test_patch_sets_and_clears_the_owner(admin_api, company):
    anna = staff("anna")
    url = api_url(f"companies/{company.pk}/")
    assert admin_api.patch(url, {"owner_id": anna.pk}, format="json").json()["owner"]["id"] == anna.pk
    assert admin_api.patch(url, {"owner_id": anna.pk}, format="json").status_code == 200  # unchanged: no note
    assert admin_api.patch(url, {"owner_id": None}, format="json").json()["owner"] is None
    assert owner_notes(company) == ["owner: none", "owner: anna"]  # newest first


@pytest.mark.parametrize("flags", [{"is_staff": False}, {"is_active": False}])
def test_patch_refuses_a_user_who_cannot_own(admin_api, company, flags):
    user = staff("someone", **flags)
    response = admin_api.patch(api_url(f"companies/{company.pk}/"), {"owner_id": user.pk}, format="json")
    assert response.status_code == 400
    company.refresh_from_db()
    assert company.owner_id is None


def test_owner_is_not_a_generic_editable_field(company):
    with pytest.raises(ValueError, match="not editable"):
        company_service.update_company(company, {"owner": staff("anna")})


# --- bulk assign -------------------------------------------------------------------------------------------------


def test_bulk_assign_changes_only_what_differs(admin_api, channel):
    anna = staff("anna")
    owned, free = new_company(channel, "a.test", anna), new_company(channel, "b.test")
    body = {"company_ids": [owned.pk, free.pk], "owner_id": anna.pk}
    assert admin_api.post(api_url("companies/assign/"), body, format="json").json() == {"updated": 1}
    free.refresh_from_db()
    assert free.owner_id == anna.pk and owner_notes(free) == ["owner: anna"] and owner_notes(owned) == []
    body["owner_id"] = None
    assert admin_api.post(api_url("companies/assign/"), body, format="json").json() == {"updated": 2}
    assert not Company.objects.filter(owner__isnull=False).exists()


def test_bulk_assign_over_the_limit_is_400(admin_api, channel):
    body = {"company_ids": list(range(1, owner_service.ASSIGN_LIMIT + 2)), "owner_id": None}
    response = admin_api.post(api_url("companies/assign/"), body, format="json")
    assert response.status_code == 400 and "company_ids" in str(response.json())


def test_bulk_assign_refuses_a_company_of_another_channel(admin_api, channel, company):
    other = channel.__class__.objects.create(idx="other", name="Other", default_language=channel.default_language)
    Stage.objects.create(channel=other, key="new", label="New", order=0)
    foreign = new_company(other, "foreign.test")
    body = {"company_ids": [company.pk, foreign.pk], "owner_id": None}
    assert admin_api.post(api_url("companies/assign/"), body, format="json").status_code == 400


def test_an_out_of_range_owner_id_is_400(admin_api, company):
    body = {"company_ids": [company.pk], "owner_id": 2**40}
    assert admin_api.post(api_url("companies/assign/"), body, format="json").status_code == 400


def test_bulk_assign_refuses_a_non_staff_owner(admin_api, company):
    body = {"company_ids": [company.pk], "owner_id": staff("someone", is_staff=False).pk}
    assert admin_api.post(api_url("companies/assign/"), body, format="json").status_code == 400


# --- list --------------------------------------------------------------------------------------------------------


def listed(admin_api, **params) -> list[str]:
    return [row["domain"] for row in admin_api.get(api_url("companies/"), params).json()["results"]]


def test_list_filters_by_owner(admin_api, channel):
    anna = staff("anna")
    new_company(channel, "mine.test", operator())
    new_company(channel, "anna.test", anna)
    new_company(channel, "free.test")
    assert listed(admin_api, owner="me") == ["mine.test"]
    assert listed(admin_api, owner="none") == ["free.test"]
    assert listed(admin_api, owner=str(anna.pk)) == ["anna.test"]
    assert admin_api.get(api_url("companies/"), {"owner": "someone"}).status_code == 400
    assert admin_api.get(api_url("companies/"), {"owner": "9" * 12}).status_code == 400


def test_list_sorts_by_owner_and_shows_the_owner(admin_api, channel):
    new_company(channel, "z.test", staff("adam", first_name="Adam"))
    new_company(channel, "a.test", staff("zenon"))
    new_company(channel, "free.test")
    assert listed(admin_api, sort="owner__username") == ["z.test", "a.test", "free.test"]
    assert listed(admin_api, sort="-owner__username") == ["a.test", "z.test", "free.test"]
    row = admin_api.get(api_url("companies/"), {"sort": "owner__username"}).json()["results"][0]
    assert row["owner"]["username"] == "adam" and set(row["owner"]) == {"id", "username", "name"}


def test_list_query_count_is_constant(admin_api, channel):
    owners = [staff(f"seller{number}") for number in range(5)]
    for number in range(50):
        new_company(channel, f"shop{number:02d}.test", owners[number % 5])

    def count(size: int) -> int:
        with CaptureQueriesContext(connection) as queries:
            assert len(listed(admin_api, page_size=size)) == size
        return len(queries)

    assert count(5) == count(50)


# --- import ------------------------------------------------------------------------------------------------------


def imported(channel, capture, owner=None) -> dict[str, int | None]:
    batch = import_service.create_batch(channel, "leads.csv", len(ROWS), "test", owner=owner)
    with capture(execute=True):
        import_service.run_content(batch, ROWS)
    return dict(Company.objects.values_list("domain", "owner_id"))


def test_import_with_an_owner_gives_it_to_created_and_unowned_matched(channel, django_capture_on_commit_callbacks):
    anna, adam = staff("anna"), staff("adam")
    new_company(channel, "a-shop.test", adam)
    new_company(channel, "b-shop.test")
    owners = imported(channel, django_capture_on_commit_callbacks, owner=anna)
    assert owners == {"a-shop.test": adam.pk, "b-shop.test": anna.pk, "c-shop.test": anna.pk}


def test_import_slow_path_gives_the_owner_too(channel, monkeypatch, django_capture_on_commit_callbacks):
    def fail(writer):
        raise import_service.IntegrityError("race")

    monkeypatch.setattr(import_service._ChunkWriter, "flush", fail)
    anna = staff("anna")
    new_company(channel, "b-shop.test")
    owners = imported(channel, django_capture_on_commit_callbacks, owner=anna)
    assert set(owners.values()) == {anna.pk}


def test_import_without_an_owner_leaves_companies_unowned(channel, django_capture_on_commit_callbacks):
    assert set(imported(channel, django_capture_on_commit_callbacks).values()) == {None}


def test_upload_takes_owner_id(channel, admin_api):
    anna = staff("anna")

    def upload(**fields):
        file = SimpleUploadedFile("leads.csv", ROWS.encode(), content_type="text/csv")
        with mock.patch("django_leads.api.admin.views.import_views.enqueue_import"):
            return admin_api.post(api_url("imports/"), {"file": file, **fields}, format="multipart")

    body = upload(owner_id=str(anna.pk)).json()
    assert body["owner_id"] == anna.pk and ImportBatch.objects.get(pk=body["id"]).owner_id == anna.pk
    assert upload().json()["owner_id"] is None
    assert upload(owner_id=str(staff("someone", is_staff=False).pk)).status_code == 400


# --- alerts ------------------------------------------------------------------------------------------------------


@pytest.fixture
def notifications(channel):
    return NotificationChannel.objects.create(idx=CHANNEL_IDX, label="Default Europe")


def send_reply(company: Company, capture) -> None:
    with capture(execute=True):
        communicator_signals.reply_received.send(sender=Thread, thread=thread_of(company), reply=reply("not_now"))


def test_reply_to_an_owned_company_notifies_only_the_owner(shop, notifications, django_capture_on_commit_callbacks):
    anna = staff("anna")
    owner_service.set_owner(shop, anna, actor="test")
    send_reply(shop, django_capture_on_commit_callbacks)
    assert list(Notification.objects.values_list("recipient_user_id", flat=True)) == [anna.pk]


def test_reply_without_an_owner_goes_to_the_role(shop, notifications, django_capture_on_commit_callbacks):
    send_reply(shop, django_capture_on_commit_callbacks)
    rows = list(Notification.objects.values_list("recipient_user_id", "recipient_role"))
    assert rows == [(None, alert_service.LEADS_NOTIFY_ROLE)]


def test_a_deactivated_owner_leaves_the_alert_to_the_role(shop, notifications, django_capture_on_commit_callbacks):
    owner_service.set_owner(shop, staff("anna", is_active=False), actor="test")
    send_reply(shop, django_capture_on_commit_callbacks)
    assert list(Notification.objects.values_list("recipient_user_id", flat=True)) == [None]


def test_without_notifications_the_alert_is_a_no_op(shop, django_capture_on_commit_callbacks):
    with (
        mock.patch.object(alert_service, "_notifications_available", return_value=False),
        mock.patch("django_notifications.services.notify_service.notify") as notify,
    ):
        send_reply(shop, django_capture_on_commit_callbacks)
    notify.assert_not_called()


# --- mailbox -----------------------------------------------------------------------------------------------------


def draft(company: Company) -> mock.MagicMock:
    contact = Contact.objects.filter(company=company).exclude(email="").first()
    message = SimpleNamespace(pk=7, status="review_required")
    with (
        mock.patch.object(outreach_service, "communicate", return_value=message) as communicate,
        mock.patch.object(outreach_service, "render_legal_footer", return_value="footer"),
        mock.patch.object(outreach_service, "resolve_clause_set"),
    ):
        outreach_service.request_draft(company, contact, "lead.cold.b2b", actor="test")
    return communicate


def test_draft_of_an_owned_company_uses_the_owner_mailbox(shop):
    anna = staff("anna")
    owner_service.set_owner(shop, anna, actor="test")
    assert draft(shop).call_args.kwargs["mailbox_user_id"] == anna.pk


def test_draft_of_an_unowned_company_uses_the_default_mailbox(shop):
    assert draft(shop).call_args.kwargs["mailbox_user_id"] is None
