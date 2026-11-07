# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Q3: the sender route key — on the company, on an import batch, as the channel default, passed to communicate()."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from django_leads.models import Activity, Company
from django_leads.services import import_service, outreach_service, sender_route_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

CSV = (
    "company_name,domain,email,legal_basis\n"
    "Alfa,alfa.test,jan@alfa.test,legitimate_interest\n"
    "Beta,beta.test,ola@beta.test,legitimate_interest\n"
)


def test_import_batch_route_is_set_on_every_created_company(channel):
    batch = import_service.create_batch(channel, "friends.csv", len(CSV), "t", sender_route="ecomhouse")
    import_service.run_content(batch, CSV)
    assert set(Company.objects.values_list("sender_route", flat=True)) == {"ecomhouse"}


def test_import_without_route_leaves_it_blank_and_fills_blank_on_match_only(channel):
    import_service.run_content(import_service.create_batch(channel, "a.csv", 1, "t"), CSV)
    assert set(Company.objects.values_list("sender_route", flat=True)) == {""}
    Company.objects.filter(domain="beta.test").update(sender_route="entirius")
    import_service.run_content(import_service.create_batch(channel, "b.csv", 1, "t", sender_route="ecomhouse"), CSV)
    routes = dict(Company.objects.values_list("domain", "sender_route"))
    assert routes == {"alfa.test": "ecomhouse", "beta.test": "entirius"}  # never overwritten


def test_upload_endpoint_takes_the_route(channel, admin_api, django_capture_on_commit_callbacks):
    upload = SimpleUploadedFile("friends.csv", CSV.encode(), content_type="text/csv")
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_api.post(
            api_url("imports/"), {"file": upload, "sender_route": "ecomhouse"}, format="multipart"
        )
    assert response.status_code == 202 and response.json()["sender_route"] == "ecomhouse"
    assert set(Company.objects.values_list("sender_route", flat=True)) == {"ecomhouse"}
    bad = SimpleUploadedFile("x.csv", CSV.encode(), content_type="text/csv")
    assert (
        admin_api.post(api_url("imports/"), {"file": bad, "sender_route": "Bad Route!"}, format="multipart").status_code
        == 400
    )


def test_effective_route_company_then_channel_default_then_blank(company):
    assert sender_route_service.effective_route(company) == ""
    sender_route_service.set_default(company.channel, "entirius")
    assert sender_route_service.effective_route(company) == "entirius"
    company.sender_route = "ecomhouse"
    assert sender_route_service.effective_route(company) == "ecomhouse"


def test_bulk_set_changes_only_chosen_companies_of_the_channel_and_logs(channel, admin_api):
    import_service.run_content(import_service.create_batch(channel, "a.csv", 1, "t"), CSV)
    alfa, beta = (Company.objects.get(domain=d) for d in ("alfa.test", "beta.test"))
    body = {"company_ids": [alfa.pk, 999999], "sender_route": "ecomhouse"}
    assert admin_api.post(api_url("companies/bulk-sender-route/"), body, format="json").json() == {"updated": 1}
    alfa.refresh_from_db()
    beta.refresh_from_db()
    assert (alfa.sender_route, beta.sender_route) == ("ecomhouse", "")
    assert Activity.objects.filter(company=alfa, message="sender route: ecomhouse").exists()
    admin_api.post(
        api_url("companies/bulk-sender-route/"), {"company_ids": [alfa.pk], "sender_route": ""}, format="json"
    )
    alfa.refresh_from_db()
    assert alfa.sender_route == ""
    assert (
        admin_api.post(
            api_url("companies/bulk-sender-route/"), {"company_ids": [], "sender_route": "x"}, format="json"
        ).status_code
        == 400
    )


def test_company_patch_filter_and_channel_settings_api(company, admin_api):
    assert (
        admin_api.patch(api_url(f"companies/{company.pk}/"), {"sender_route": "ecomhouse"}, format="json").json()[
            "sender_route"
        ]
        == "ecomhouse"
    )
    assert [c["id"] for c in admin_api.get(api_url("companies/?sender_route=ecomhouse")).json()["results"]] == [
        company.pk
    ]
    assert admin_api.get(api_url("companies/?sender_route=")).json()["count"] == 0
    assert admin_api.get(api_url("settings/")).json()["default_sender_route"] == ""
    patched = admin_api.patch(api_url("settings/"), {"default_sender_route": "entirius"}, format="json")
    assert patched.json()["default_sender_route"] == "entirius"
    assert admin_api.patch(api_url("settings/"), {"default_sender_route": "A B"}, format="json").status_code == 400


def draft(shop, contact):
    message = SimpleNamespace(pk=1, status="review_required")
    with (
        mock.patch.object(outreach_service, "communicate", return_value=message) as communicate,
        mock.patch.object(outreach_service, "render_legal_footer", return_value="f"),
        mock.patch.object(outreach_service, "resolve_clause_set"),
    ):
        outreach_service.request_draft(shop, contact, "t", actor="a")
    return communicate.call_args.kwargs


def test_communicate_gets_the_route_only_when_communicator_accepts_it(shop):
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    shop.sender_route = "ecomhouse"
    shop.save()
    with mock.patch.object(sender_route_service, "communicate", lambda *, sender_route="", **kw: None):
        assert draft(shop, contact)["sender_route"] == "ecomhouse"
    with mock.patch.object(sender_route_service, "communicate", lambda **kw: None):
        assert "sender_route" not in draft(shop, contact)  # an older communicator has no such argument
    shop.sender_route = ""
    shop.save()
    with mock.patch.object(sender_route_service, "communicate", lambda *, sender_route="", **kw: None):
        assert "sender_route" not in draft(shop, contact)  # no key anywhere = communicator's default
