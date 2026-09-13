# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.core.files.uploadedfile import SimpleUploadedFile

from django_leads.enums import ImportStatus
from django_leads.models import Company, ImportBatch
from tests.conftest import api_url
from tests.test_import import CSV


def test_customer_is_forbidden(customer_api, channel):
    assert customer_api.get(api_url("companies/")).status_code == 403


def test_sort_param_outside_allowlist_400(admin_api, company):
    assert admin_api.get(api_url("companies/?sort=-name")).status_code == 200
    assert admin_api.get(api_url("companies/?sort=description")).status_code == 400


def test_search_and_stage_filter(admin_api, company, channel):
    Company.objects.create(
        channel=channel,
        name="My",
        domain="myogrod.pl",
        stage=company.stage,
        stage_entered_at=company.stage_entered_at,
        source="csv",
    )
    assert admin_api.get(api_url("companies/?search=myogrod")).json()["count"] == 1
    assert admin_api.get(api_url("companies/?stage=contacted")).json()["count"] == 0


def test_create_company_conflict_on_same_registrable_domain(admin_api, company):
    response = admin_api.post(api_url("companies/"), {"domain": "https://www.ogrod.pl/x"}, format="json")
    assert response.status_code == 409


def test_patch_company_rejects_stage_and_domain(admin_api, company):
    url = api_url(f"companies/{company.pk}/")
    assert admin_api.patch(url, {"domain": "x.pl"}, format="json").status_code == 400
    assert admin_api.patch(url, {"stage": 1}, format="json").status_code == 400
    response = admin_api.patch(url, {"name": "Ogrod", "do_not_contact": True}, format="json")
    assert response.status_code == 200 and response.json()["do_not_contact"] is True


def test_transition_and_detail(admin_api, company):
    response = admin_api.post(api_url(f"companies/{company.pk}/transition/"), {"stage_key": "contacted"}, format="json")
    assert response.status_code == 200 and response.json()["stage"]["key"] == "contacted"
    assert response.json()["activities"][0]["kind"] == "stage"


def test_L18_delete_stage_in_use_returns_409(admin_api, company):
    assert admin_api.delete(api_url(f"stages/{company.stage_id}/")).status_code == 409
    won = admin_api.get(api_url("stages/")).json()["results"][-1]
    assert admin_api.delete(api_url(f"stages/{won['id']}/")).status_code == 204


def test_contact_create_and_email_is_immutable(admin_api, company):
    body = {"company_id": company.pk, "email": "Jan@Ogrod.pl", "language": "pl", "legal_basis": "consent"}
    created = admin_api.post(api_url("contacts/"), body, format="json")
    assert created.status_code == 201 and created.json()["email"] == "jan@ogrod.pl"
    assert admin_api.post(api_url("contacts/"), body, format="json").status_code == 409
    url = api_url(f"contacts/{created.json()['id']}/")
    assert admin_api.patch(url, {"email": "x@ogrod.pl"}, format="json").status_code == 400
    assert admin_api.patch(url, {"job_title": "CEO"}, format="json").json()["job_title"] == "CEO"


def test_import_upload_runs_and_reports(admin_api, channel, django_capture_on_commit_callbacks):
    upload = SimpleUploadedFile("leads.csv", CSV.encode(), content_type="text/csv")
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_api.post(api_url("imports/"), {"file": upload}, format="multipart")
    assert response.status_code == 202
    detail = admin_api.get(api_url(f"imports/{response.json()['id']}/")).json()
    assert detail["status"] == ImportStatus.DONE and detail["created_count"] == 3
    assert {"row": 7, "action": "skipped", "reason": "no_domain_no_email"} in detail["report"]
    assert admin_api.get(api_url("imports/")).json()["count"] == ImportBatch.objects.count() == 1


def test_activities_filtered_by_company(admin_api, company):
    admin_api.post(api_url(f"companies/{company.pk}/transition/"), {"stage_key": "won"}, format="json")
    assert admin_api.get(api_url(f"activities/?company={company.pk}")).json()["count"] == 1
