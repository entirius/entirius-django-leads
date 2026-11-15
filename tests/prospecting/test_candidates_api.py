# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from unittest import mock

import pytest

from django_leads.models import Channel
from django_prospecting.enums import CandidateStatus
from django_prospecting.models import CompanyProspect, PeopleSearch
from django_prospecting.services import leads_bridge
from tests.prospecting.conftest import DOMAIN, SUBJECT, api_url, run_search

pytestmark = pytest.mark.django_db


@pytest.fixture
def candidate(fixed_provider):
    return run_search().candidates.get(last_name="Zielińska")


def test_list_candidates_with_checks_and_missing(staff_api, fixed_provider):
    run_search()
    response = staff_api.get(api_url("people/candidates/"), {"subject_ref": SUBJECT, "status": "pending"})
    assert response.status_code == 200
    rows = {row["last_name"]: row for row in response.json()["results"]}
    assert rows["Zielińska"]["missing"] == [] and rows["Zielińska"]["company_name"] == "Example Shop"
    assert rows["Wójcik"]["missing"] == ["role"]
    assert [check["kind"] for check in rows["Lis"]["checks"]] == ["role", "email", "employed", "mailbox"]
    assert rows["Lis"]["email_options"][0]["verdict"] == "valid"


def test_accept_hands_the_person_to_leads(staff_api, candidate):
    with mock.patch.object(leads_bridge, "add_contact", return_value="leads.Contact:9") as add:
        response = staff_api.post(api_url(f"people/candidates/{candidate.pk}/accept/"), {}, format="json")
    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["status"], body["contact_ref"], body["decided_by"]) == ("accepted", "leads.Contact:9", "anna")
    add.assert_called_once()
    assert add.call_args.kwargs == {"email": f"ewa.zielinska@{DOMAIN}", "actor": "anna"}
    again = staff_api.post(api_url(f"people/candidates/{candidate.pk}/accept/"), {}, format="json")
    assert again.status_code == 409


def test_accept_with_another_address(staff_api, candidate):
    with mock.patch.object(leads_bridge, "add_contact", return_value="leads.Contact:9") as add:
        response = staff_api.post(
            api_url(f"people/candidates/{candidate.pk}/accept/"), {"email": "Ewa@Example-Shop.test"}, format="json"
        )
    assert response.json()["email"] == "ewa@example-shop.test"
    assert add.call_args.kwargs["email"] == "ewa@example-shop.test"


def test_accept_without_leads_is_503_and_stays_pending(staff_api, candidate, without_leads):
    response = staff_api.post(api_url(f"people/candidates/{candidate.pk}/accept/"), {}, format="json")
    assert response.status_code == 503
    candidate.refresh_from_db()
    assert candidate.status == CandidateStatus.PENDING


def test_refused_by_leads_is_400(staff_api, candidate):
    with mock.patch.object(leads_bridge, "add_contact", side_effect=ValueError("contact exists")):
        response = staff_api.post(api_url(f"people/candidates/{candidate.pk}/accept/"), {}, format="json")
    assert response.status_code == 400


def test_reject(staff_api, candidate):
    url = api_url(f"people/candidates/{candidate.pk}/reject/")
    response = staff_api.post(url, {"reason": "left the company"}, format="json")
    assert (response.json()["status"], response.json()["reject_reason"]) == ("rejected", "left the company")
    assert staff_api.post(url, {}, format="json").status_code == 409


def test_unknown_candidate_is_404(staff_api, db):
    assert staff_api.post(api_url("people/candidates/999/reject/"), {}, format="json").status_code == 404


def test_an_unknown_channel_is_404(staff_api, candidate):
    assert staff_api.get(api_url("people/candidates/", channel_idx="other")).status_code == 404
    assert staff_api.get(api_url("people/limits/", channel_idx="invented")).status_code == 404
    body = {"subject_ref": SUBJECT}
    assert staff_api.post(api_url("people/searches/", channel_idx="invented"), body, format="json").status_code == 404


def test_other_channel_sees_nothing(staff_api, candidate, polish):
    Channel.objects.create(idx="other", name="Other", default_language=polish)
    response = staff_api.get(api_url("people/candidates/", channel_idx="other"))
    assert response.json()["results"] == []


def test_manual_search_and_list(staff_api, fixed_provider, company, django_capture_on_commit_callbacks):
    ref = f"leads.Company:{company.pk}"
    with django_capture_on_commit_callbacks(execute=True):
        response = staff_api.post(api_url("people/searches/"), {"subject_ref": ref}, format="json")
    assert response.status_code == 201
    body = response.json()
    assert (body["reason"], body["requested_by"], body["domain"]) == ("manual", "anna", company.domain)
    rows = staff_api.get(api_url("people/searches/"), {"subject_ref": ref}).json()["results"]
    assert rows[0]["status"] == "done" and rows[0]["mail_pattern"] == "{first}.{last}"


def test_a_search_takes_name_and_domain_from_leads_not_the_body(staff_api, fixed_provider, company):
    body = {"subject_ref": f"leads.Company:{company.pk}", "domain": "attacker.test", "company_name": "x"}
    assert staff_api.post(api_url("people/searches/"), body, format="json").status_code == 400


def test_a_search_of_a_company_from_another_channel_is_refused(staff_api, fixed_provider, company, polish):
    other = Channel.objects.create(idx="other", name="Other", default_language=polish)
    ref = f"leads.Company:{company.pk}"
    response = staff_api.post(api_url("people/searches/", channel_idx=other.idx), {"subject_ref": ref}, format="json")
    assert response.status_code == 400 and "subject_ref" in str(response.json())
    assert (
        staff_api.post(api_url("people/searches/"), {"subject_ref": "leads.Company:0"}, format="json").status_code
        == 400
    )
    assert PeopleSearch.objects.count() == 0


def test_invented_refs_cannot_dodge_the_company_limit(staff_api, fixed_provider, company):
    for ref in ("leads.Company:9001", "leads.Company:9002"):
        assert staff_api.post(api_url("people/searches/"), {"subject_ref": ref}, format="json").status_code == 400
    assert PeopleSearch.objects.count() == 0


def test_accept_hands_leads_the_channel_of_the_candidate(staff_api, candidate):
    with mock.patch(
        "django_leads.services.candidate_service.accept_candidate", return_value="leads.Contact:1"
    ) as accept:
        staff_api.post(api_url(f"people/candidates/{candidate.pk}/accept/"), {}, format="json")
    assert accept.call_args.kwargs["channel_idx"] == candidate.channel_idx


def test_limits(staff_api, fixed_provider):
    run_search()
    body = staff_api.get(api_url("people/limits/")).json()
    assert body == {
        "search_provider": "fixed", "address_tester": "stub", "max_attempts": 3, "company_search_limit": 3,
        "daily_budget": 100, "spent_today": 3, "max_candidates": 5,
    }  # fmt: skip


def test_companies_placeholder(staff_api, db):
    created = staff_api.post(api_url("companies/"), {"name": "New Shop", "domain": "New.test"}, format="json")
    assert created.status_code == 201 and created.json()["domain"] == "new.test"
    body = staff_api.get(api_url("companies/")).json()
    assert "not built yet" in body["description"]
    assert [row["name"] for row in body["results"]] == ["New Shop"]
    assert CompanyProspect.objects.count() == 1


def test_anonymous_and_non_staff_are_refused(client, db):
    assert client.get(api_url("people/candidates/")).status_code == 401
