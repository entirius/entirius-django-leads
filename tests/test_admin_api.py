# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.contrib.admin import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile

from django_leads.admin import CompanyAdmin
from django_leads.enums import ActivityKind, ImportStatus
from django_leads.models import Activity, Channel, Company, Contact, ImportBatch, LeadType, Stage
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


def test_company_list_filters_by_lead_type(admin_api, company, shop):
    Company.objects.filter(pk=shop.pk).update(lead_type="RETAILER")
    body = admin_api.get(api_url("companies/?lead_type=RETAILER")).json()
    assert body["count"] == 1 and body["results"][0]["id"] == shop.pk


def test_company_list_filters_do_not_contact(admin_api, company, shop):
    Company.objects.filter(pk=company.pk).update(do_not_contact=True)
    assert [row["id"] for row in admin_api.get(api_url("companies/?do_not_contact=true")).json()["results"]] == [
        company.pk
    ]
    assert admin_api.get(api_url("companies/?do_not_contact=false")).json()["count"] == 1


def test_company_list_has_reply_counts_each_company_once(admin_api, company, shop):
    Activity.objects.create(company=shop, kind=ActivityKind.REPLY, message="reply 1")
    Activity.objects.create(company=shop, kind=ActivityKind.REPLY, message="reply 2")
    Activity.objects.create(company=company, kind=ActivityKind.NOTE, message="note")
    replied = admin_api.get(api_url(f"companies/?stage={shop.stage.key}&has_reply=true")).json()
    assert replied["count"] == 1 and len(replied["results"]) == 1 and replied["results"][0]["id"] == shop.pk
    not_replied = admin_api.get(api_url("companies/?has_reply=false")).json()
    assert [row["id"] for row in not_replied["results"]] == [company.pk]


def test_company_list_rejects_unknown_lead_type(admin_api, company):
    response = admin_api.get(api_url("companies/?lead_type=BANK"))
    assert response.status_code == 400
    assert admin_api.get(api_url("companies/?has_reply=maybe")).status_code == 400


def test_create_company_conflict_on_same_registrable_domain(admin_api, company):
    response = admin_api.post(api_url("companies/"), {"domain": "https://www.ogrod.pl/x"}, format="json")
    assert response.status_code == 409 and response.json()["error"] == "DOMAIN_EXISTS"


def test_item5_no_stages_conflict_code(admin_api):
    Channel.objects.create(idx="no-stages")
    response = admin_api.post("/api/leads/v2/admin/no-stages/companies/", {"domain": "a.pl"}, format="json")
    assert response.status_code == 409 and response.json()["error"] == "NO_STAGES"


def test_item5_stage_exists_conflict_code(admin_api, channel):
    response = admin_api.post(api_url("stages/"), {"key": "new", "label": "Duplicate"}, format="json")
    assert response.status_code == 409 and response.json()["error"] == "STAGE_EXISTS"
    stage = Stage.objects.get(channel=channel, key="contacted")
    response = admin_api.patch(api_url(f"stages/{stage.pk}/"), {"key": "new"}, format="json")
    assert response.status_code == 409 and response.json()["error"] == "STAGE_EXISTS"


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
    response = admin_api.delete(api_url(f"stages/{company.stage_id}/"))
    assert response.status_code == 409 and response.json()["error"] == "STAGE_NOT_EMPTY"
    won = admin_api.get(api_url("stages/")).json()["results"][-1]
    assert admin_api.delete(api_url(f"stages/{won['id']}/")).status_code == 204


def test_contact_create_and_email_is_immutable(admin_api, company):
    body = {
        "company_id": company.pk,
        "email": "Jan@Ogrod.pl",
        "language": "pl",
        "legal_basis": "consent",
        "consent_ref": "signed form 2026-09",
    }
    created = admin_api.post(api_url("contacts/"), body, format="json")
    assert created.status_code == 201 and created.json()["email"] == "jan@ogrod.pl"
    dup = admin_api.post(api_url("contacts/"), body, format="json")
    assert dup.status_code == 409 and dup.json()["error"] == "CONTACT_EXISTS"
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
    assert {"row": 7, "reason": "no_domain_no_email"} in detail["report"]
    assert (detail["row_count"], detail["size_bytes"]) == (8, len(CSV.encode()))
    assert admin_api.get(api_url("imports/")).json()["count"] == ImportBatch.objects.count() == 1


def test_activities_filtered_by_company(admin_api, company):
    admin_api.post(api_url(f"companies/{company.pk}/transition/"), {"stage_key": "won"}, format="json")
    assert admin_api.get(api_url(f"activities/?company={company.pk}")).json()["count"] == 1


def test_company_admin_cannot_add(rf):
    assert CompanyAdmin(Company, AdminSite()).has_add_permission(rf.get("/")) is False


def test_admin_consent_without_ref_is_400(admin_api, company):
    body = {"company_id": company.pk, "email": "jan@ogrod.pl", "legal_basis": "consent"}
    assert admin_api.post(api_url("contacts/"), body, format="json").status_code == 400
    assert admin_api.post(api_url("contacts/"), {**body, "consent_ref": "  "}, format="json").status_code == 400
    created = admin_api.post(api_url("contacts/"), {**body, "legal_basis": None}, format="json")
    url = api_url(f"contacts/{created.json()['id']}/")
    assert admin_api.patch(url, {"legal_basis": "consent"}, format="json").status_code == 400
    assert Contact.objects.get().legal_basis is None
    assert not Activity.objects.filter(kind=ActivityKind.LEGAL_BASIS).exists()


def test_admin_consent_with_ref_writes_activity(admin_api, company):
    body = {"company_id": company.pk, "email": "jan@ogrod.pl", "legal_basis": "legitimate_interest"}
    created = admin_api.post(api_url("contacts/"), body, format="json")
    url = api_url(f"contacts/{created.json()['id']}/")
    patch = {"legal_basis": "consent", "consent_ref": "call 2026-09-14"}
    assert admin_api.patch(url, patch, format="json").json()["legal_basis"] == "consent"
    refs = Activity.objects.filter(kind=ActivityKind.LEGAL_BASIS).order_by("id").values_list("data", flat=True)
    assert [data["consent_ref"] for data in refs] == ["admin:operator", "admin:operator:call 2026-09-14"]


def test_dev_import_now_runs_in_the_request(admin_api, channel):
    upload = SimpleUploadedFile("leads.csv", CSV.encode(), content_type="text/csv")
    response = admin_api.post(api_url("test/import-now/"), {"file": upload}, format="multipart")
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["created_count"]) == (ImportStatus.DONE, 3)


# UX-003: lead types — configuration per channel, CRUD like stages, a type in use cannot be deleted.
def test_lead_types_list_create_and_code_is_fixed(admin_api, channel):
    assert [row["code"] for row in admin_api.get(api_url("lead-types/")).json()["results"]] == [
        "MANUFACTURER",
        "WHOLESALE",
        "RETAILER",
    ]
    created = admin_api.post(api_url("lead-types/"), {"code": "AGENCY", "label": "Agency", "order": 40}, format="json")
    assert created.status_code == 201 and created.json()["is_active"] is True
    url = api_url(f"lead-types/{created.json()['id']}/")
    assert admin_api.patch(url, {"label": "Web agency"}, format="json").json()["label"] == "Web agency"
    assert admin_api.patch(url, {"code": "SHOP"}, format="json").status_code == 400
    assert admin_api.post(api_url("lead-types/"), {"code": "AGENCY", "label": "x"}, format="json").status_code == 409
    assert admin_api.post(api_url("lead-types/"), {"code": "UNKNOWN", "label": "x"}, format="json").status_code == 409
    assert admin_api.post(api_url("lead-types/"), {"code": "agency", "label": "x"}, format="json").status_code == 400


def test_lead_type_in_use_cannot_be_deleted(admin_api, company):
    retailer = LeadType.objects.get(channel=company.channel, code="RETAILER")
    Company.objects.filter(pk=company.pk).update(lead_type="RETAILER")
    response = admin_api.delete(api_url(f"lead-types/{retailer.pk}/"))
    assert response.status_code == 409 and LeadType.objects.filter(pk=retailer.pk).exists()
    Company.objects.filter(pk=company.pk).update(lead_type="UNKNOWN")
    assert admin_api.delete(api_url(f"lead-types/{retailer.pk}/")).status_code == 204


def test_filter_and_edit_accept_only_active_lead_types(admin_api, company):
    LeadType.objects.filter(channel=company.channel, code="WHOLESALE").update(is_active=False)
    assert admin_api.get(api_url("companies/?lead_type=WHOLESALE")).status_code == 400
    assert admin_api.get(api_url("companies/?lead_type=UNKNOWN")).json()["count"] == 1
    url = api_url(f"companies/{company.pk}/")
    assert admin_api.patch(url, {"lead_type": "WHOLESALE"}, format="json").status_code == 400
    assert admin_api.patch(url, {"lead_type": "RETAILER"}, format="json").json()["lead_type"] == "RETAILER"
    created = admin_api.post(api_url("companies/"), {"domain": "new-shop.pl", "lead_type": "BANK"}, format="json")
    assert created.status_code == 400


def test_a_deactivated_lead_type_does_not_block_other_edits(admin_api, company):
    Company.objects.filter(pk=company.pk).update(lead_type="WHOLESALE")
    LeadType.objects.filter(channel=company.channel, code="WHOLESALE").update(is_active=False)
    url = api_url(f"companies/{company.pk}/")
    response = admin_api.patch(url, {"name": "Renamed", "lead_type": "WHOLESALE"}, format="json")
    assert response.status_code == 200 and response.json()["name"] == "Renamed"


def test_lead_type_of_another_channel_is_not_found(admin_api, company, polish):
    other = Channel.objects.create(idx="other-europe", name="Other", default_language=polish)
    foreign = LeadType.objects.create(channel=other, code="AGENCY", label="Agency", order=0)
    assert admin_api.get(api_url(f"lead-types/{foreign.pk}/")).status_code == 404
    assert admin_api.patch(api_url(f"lead-types/{foreign.pk}/"), {"label": "x"}, format="json").status_code == 404
    assert admin_api.delete(api_url(f"lead-types/{foreign.pk}/")).status_code == 404
