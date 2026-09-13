# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest

from django_leads.enums import ActivityKind, CompanyType, LeadSource
from django_leads.models import Activity, Company, Contact, Stage
from django_leads.services import company_service, contact_service, stage_service
from django_leads.signals import stage_entered
from django_leads.utils.domains import registrable_domain


def company_row(raw_domain: str, **fields) -> dict:
    return {"domain": registrable_domain(raw_domain), "source": LeadSource.CSV, **fields}


def test_L01_same_registrable_domain_two_rows_one_company(channel):
    first, created = company_service.upsert_company(channel, company_row("lesna-shop.pl", name="Lesna"))
    second, matched_created = company_service.upsert_company(
        channel, company_row("www.lesna-shop.pl/pl", industry="garden")
    )
    assert (created, matched_created) == (True, False)
    assert first.pk == second.pk and Company.objects.count() == 1
    assert second.industry == "garden" and second.name == "Lesna"
    activity = Activity.objects.get(company=first, kind=ActivityKind.IMPORT)
    assert activity.message == "import matched" and activity.data == {"filled": ["industry"]}


def test_L02_shop_pl_and_myshop_pl_are_two_companies(channel):
    company_service.upsert_company(channel, company_row("ogrod.pl"))
    company_service.upsert_company(channel, company_row("myogrod.pl"))
    assert sorted(Company.objects.values_list("domain", flat=True)) == ["myogrod.pl", "ogrod.pl"]


def test_L03_email_case_and_whitespace_one_contact_fills_empty_only(company):
    row = {"source": LeadSource.CSV}
    first, _ = contact_service.upsert_contact(company, {**row, "email": "Jan@Ogrod.pl", "job_title": "Owner"})
    second, created = contact_service.upsert_contact(
        company, {**row, "email": "  jan@ogrod.PL ", "job_title": "CEO", "first_name": "Jan"}
    )
    assert created is False and first.pk == second.pk and Contact.objects.count() == 1
    second.refresh_from_db()
    assert (second.email, second.job_title, second.first_name) == ("jan@ogrod.pl", "Owner", "Jan")


def test_new_company_enters_the_first_stage(company):
    assert company.stage.key == "new" and company.name == "ogrod.pl"


def test_update_company_rejects_non_whitelisted_fields(company):
    for field in ("stage", "domain", "hooks", "customer_uid"):
        with pytest.raises(ValueError, match="not editable"):
            company_service.update_company(company, {field: "x"})
    company_service.update_company(company, {"company_type": CompanyType.RETAILER})
    company.refresh_from_db()
    assert company.company_type == CompanyType.RETAILER


def test_transition_stage_writes_timeline_and_emits_stage_entered(company):
    received = []

    def receiver(sender, company, stage, **kwargs):
        received.append(stage.key)

    stage_entered.connect(receiver)
    try:
        contacted = Stage.objects.get(channel=company.channel, key="contacted")
        stage_service.transition_stage(company, contacted, actor="operator")
        stage_service.transition_stage(company, contacted, actor="operator")
    finally:
        stage_entered.disconnect(receiver)
    assert received == ["contacted"]
    assert Activity.objects.filter(company=company, kind=ActivityKind.STAGE).count() == 1
    company.refresh_from_db()
    assert company.stage == contacted and company.last_activity_at is not None


def test_L18_delete_stage_in_use_raises_stage_in_use(company):
    with pytest.raises(stage_service.StageInUse):
        stage_service.delete_stage(company.stage)
    empty = Stage.objects.get(channel=company.channel, key="won")
    stage_service.delete_stage(empty)
    assert not Stage.objects.filter(pk=empty.pk).exists()
