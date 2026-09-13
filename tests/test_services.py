# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import threading
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import connection, transaction
from django_agreements.enums import LegalBasis

from django_leads.enums import ActivityKind, CompanyType, LeadSource
from django_leads.models import Activity, Company, Contact, Stage
from django_leads.services import company_service, contact_service, form_service, stage_service
from django_leads.signals import stage_entered
from django_leads.utils.domains import registrable_domain


def company_row(raw_domain: str, **fields) -> dict:
    return {"domain": registrable_domain(raw_domain), "source": LeadSource.CSV, **fields}


def test_upsert_company_same_registrable_domain_fills_empty_and_writes_no_activity(channel):
    first, created = company_service.upsert_company(channel, company_row("lesna-shop.pl", name="Lesna"))
    second, matched_created = company_service.upsert_company(
        channel, company_row("www.lesna-shop.pl/pl", industry="garden")
    )
    assert (created, matched_created) == (True, False)
    assert first.pk == second.pk and Company.objects.count() == 1
    assert second.industry == "garden" and second.name == "Lesna"
    assert not Activity.objects.exists()


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


def test_transition_stage_writes_timeline_and_emits_stage_entered(company, django_capture_on_commit_callbacks):
    received = []

    def receiver(sender, company, stage, **kwargs):
        received.append(stage.key)

    stage_entered.connect(receiver)
    try:
        contacted = Stage.objects.get(channel=company.channel, key="contacted")
        with django_capture_on_commit_callbacks(execute=True):
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


def test_stage_entered_not_sent_on_rollback(company, django_capture_on_commit_callbacks):
    received = []
    stage_entered.connect(received.append, weak=False, dispatch_uid="test_rollback")
    contacted = Stage.objects.get(channel=company.channel, key="contacted")
    try:
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError), transaction.atomic():
            stage_service.transition_stage(company, contacted, actor="operator")
            raise RuntimeError("rolled back")
    finally:
        stage_entered.disconnect(dispatch_uid="test_rollback")
    assert received == [] and not Activity.objects.filter(kind=ActivityKind.STAGE).exists()


def test_consent_without_ref_refused(company):
    contact, _ = contact_service.upsert_contact(company, {"email": "jan@ogrod.pl", "source": LeadSource.CSV})
    with pytest.raises(contact_service.ConsentRefRequired):
        contact_service.set_legal_basis(contact, LegalBasis.CONSENT, source="admin", consent_ref="", actor="x")
    with pytest.raises(contact_service.ConsentRefRequired):
        contact_service.propose_legal_basis(contact, LegalBasis.CONSENT, source="form", consent_ref="", actor="x")
    contact.refresh_from_db()
    assert contact.legal_basis is None and not Activity.objects.exists()


def fake_lead(pk: int, email: str) -> SimpleNamespace:
    form = SimpleNamespace(language=None)
    raw = {"website": "https://www.race-shop.pl/"}
    return SimpleNamespace(
        pk=pk,
        email=email,
        name="Jan Race",
        phone="",
        company="Race",
        raw_data=raw,
        contact_form=form,
        contact_form_id=1,
    )


@pytest.mark.skipif(connection.vendor != "postgresql", reason="needs concurrent PostgreSQL transactions")
@pytest.mark.django_db(transaction=True)
def test_concurrent_form_submissions_same_domain_create_one_company(channel):
    """Both threads pass the existence check before either inserts; the loser re-reads and merges."""
    barrier, errors = threading.Barrier(2, timeout=10), []
    build = company_service.build_company

    def build_after_both_checked(*args, **kwargs):
        barrier.wait()
        return build(*args, **kwargs)

    def submit(pk: int) -> None:
        try:
            with transaction.atomic():
                form_service.import_form_lead(fake_lead(pk, f"buyer{pk}@race-shop.pl"), channel.idx)
        except Exception as error:
            errors.append(error)
        finally:
            connection.close()

    with mock.patch.object(company_service, "build_company", build_after_both_checked):
        threads = [threading.Thread(target=submit, args=(pk,)) for pk in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert errors == []
    assert Company.objects.filter(domain="race-shop.pl").count() == 1 and Contact.objects.count() == 2
