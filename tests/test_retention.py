# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import timedelta

import pytest
from django.utils import timezone
from django_agreements.enums import LegalBasis
from django_communicator.utils import emails as communicator_emails

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Contact, Stage
from django_leads.services import activity_service, retention_service
from django_leads.signals import contact_anonymised
from django_leads.tasks import anonymise_inactive
from django_leads.utils.emails import anonymised_address, email_hash

pytestmark = pytest.mark.django_db

OLD = timezone.now() - timedelta(days=400)


def stale_contact(company: Company, email: str = "stale@example-stale.test") -> Contact:
    contact = Contact.objects.create(
        company=company, email=email, first_name="Stale", last_name="Person", job_title="CEO", phone="+48 1",
        source=LeadSource.CSV, legal_basis=LegalBasis.CONSENT, consent_ref=7, is_primary=True,
    )  # fmt: skip
    return contact


def age(company: Company, when=OLD) -> None:
    Company.objects.filter(pk=company.pk).update(last_activity_at=when)


def test_email_hash_matches_communicator_copy():
    assert email_hash(" Foo@Bar.PL ") == communicator_emails.email_hash(" Foo@Bar.PL ") == email_hash("foo@bar.pl")
    assert anonymised_address(" Foo@Bar.PL ") == communicator_emails.anonymised_address("foo@bar.pl")
    assert anonymised_address("foo@bar.pl").endswith("@anonymised.invalid")


def test_L16_inactive_contact_anonymised_company_and_stats_kept(company, django_capture_on_commit_callbacks):
    contact = stale_contact(company)
    activity_service.record(company, ActivityKind.NOTE, "old note", contact=contact)
    Activity.objects.update(created_at=OLD)
    Company.objects.filter(pk=company.pk).update(hooks=[{"challenge": "c"}], rotation_count=1)
    age(company)
    with django_capture_on_commit_callbacks(execute=True):
        counts = anonymise_inactive(as_of=timezone.now().isoformat())
    contact.refresh_from_db()
    company.refresh_from_db()
    assert counts == {"default-europe": 1}
    assert contact.email == anonymised_address("stale@example-stale.test") and contact.anonymised_at
    assert (contact.first_name, contact.last_name, contact.job_title, contact.phone) == ("", "", "", "")
    assert contact.consent_ref == 7 and contact.is_primary
    assert company.stage.key == "new" and company.hooks == [{"challenge": "c"}] and company.rotation_count == 1
    anonymised = Activity.objects.get(company=company, kind=ActivityKind.ANONYMISED)
    assert anonymised.data == {"email_hash": email_hash("stale@example-stale.test")}
    assert Activity.objects.get(company=company, kind=ActivityKind.NOTE).message == "old note"
    assert retention_service.anonymise_inactive(timezone.now()) == {"default-europe": 0}


def test_L16_won_company_contact_untouched(company):
    contact = stale_contact(company)
    company.stage = Stage.objects.get(channel=company.channel, key="won")
    company.save(update_fields=["stage"])
    age(company)
    assert not retention_service.select_inactive_contacts(company.channel, as_of=timezone.now()).exists()
    anonymise_inactive()
    contact.refresh_from_db()
    assert contact.email == "stale@example-stale.test" and contact.anonymised_at is None


def test_L16_recent_contact_activity_blocks_anonymisation(company):
    contact = stale_contact(company)
    other = stale_contact(company, email="other@example-stale.test")
    activity_service.record(company, ActivityKind.NOTE, "recent", contact=contact)
    age(company)
    assert list(retention_service.select_inactive_contacts(company.channel, as_of=timezone.now())) == [other]


def test_L16_channel_retention_days_override_the_default(company):
    stale_contact(company)
    age(company, timezone.now() - timedelta(days=40))
    assert not retention_service.select_inactive_contacts(company.channel, as_of=timezone.now()).exists()
    company.channel.retention_days = 30
    company.channel.save()
    assert retention_service.select_inactive_contacts(company.channel, as_of=timezone.now()).count() == 1


def test_L16_signal_carries_hash_and_token_not_email(company, django_capture_on_commit_callbacks):
    contact = stale_contact(company)
    received = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    contact_anonymised.connect(receiver, dispatch_uid="test.retention")
    try:
        with django_capture_on_commit_callbacks(execute=True):
            retention_service.anonymise_contact(contact)
    finally:
        contact_anonymised.disconnect(dispatch_uid="test.retention")
    (kwargs,) = received
    assert kwargs.pop("signal") is contact_anonymised
    assert kwargs == {
        "email_hash": email_hash("stale@example-stale.test"),
        "anonymised_email": anonymised_address("stale@example-stale.test"),
        "subject_ref": f"leads.Company:{company.pk}",
    }
    assert "stale@example-stale.test" not in repr(kwargs)


# --- L-17: GDPR export and erasure across modules ---


@pytest.fixture
def subject(company, django_capture_on_commit_callbacks):
    """The stale contact with a timeline entry, a communicator thread with a reply and an objection in agreements."""
    from django_agreements.models import Channel as AgreementsChannel
    from django_agreements.services.objection_service import record_objection
    from django_communicator.models import Channel as CommunicatorChannel
    from django_communicator.models import Message, Thread

    contact = stale_contact(company)
    activity_service.record(
        company, ActivityKind.REPLY, "reply from Stale", contact=contact, data={"from": contact.email}
    )
    channel = CommunicatorChannel.objects.create(idx="default-europe", label="Default")
    thread = Thread.objects.create(
        channel=channel, subject_ref=f"leads.Company:{company.pk}", recipient_email=contact.email
    )
    Message.objects.create(thread=thread, status="sent", subject="Hello", body_text="Hello Stale")
    AgreementsChannel.objects.create(idx="default-europe", name="Default")
    record_objection(channel_idx="default-europe", email=contact.email, source="communicator")
    return contact


def test_L17_export_spans_discovered_modules(subject):
    from django_leads.gdpr import registry
    from django_leads.services import gdpr_service

    payload = gdpr_service.export(" Stale@Example-Stale.test ")

    assert {"django_leads", "django_communicator", "django_agreements"} <= set(registry.discover())
    assert payload["email"] == "stale@example-stale.test" and isinstance(payload["generated_at"], str)
    leads = payload["modules"]["django_leads"]
    assert [row["email"] for row in leads["Contact"]] == ["stale@example-stale.test"]
    assert [row["domain"] for row in leads["Company"]] == ["ogrod.pl"] and len(leads["Activity"]) == 1
    assert len(payload["modules"]["django_communicator"]["Thread"]) == 1
    assert len(payload["modules"]["django_agreements"]["ObjectionEvent"]) == 1


def test_L17_erase_scrubs_activities_and_calls_every_module(subject, django_capture_on_commit_callbacks):
    import json

    from django.core.serializers.json import DjangoJSONEncoder
    from django_agreements.models import ObjectionEvent
    from django_communicator.models import Message, Suppression, Thread

    from django_leads.services import gdpr_service

    with django_capture_on_commit_callbacks(execute=True):
        counts = gdpr_service.erase("stale@example-stale.test", actor="operator")

    assert counts["django_leads"] == {"contacts": 1, "activities": 2}
    assert counts["django_communicator"]["threads"] == 1 and counts["django_communicator"]["suppressions"] == 1
    assert counts["django_agreements"]["objection_events"] == 1
    assert set(Activity.objects.filter(contact=subject).values_list("message", flat=True)) == {"[erased]"}
    note = Activity.objects.get(company=subject.company, kind=ActivityKind.NOTE)
    assert (note.message, note.actor) == ("gdpr erase requested", "operator")
    stored = json.dumps(
        [list(model.objects.values()) for model in (Contact, Activity, Thread, Message, ObjectionEvent)],
        cls=DjangoJSONEncoder,
    )
    assert "stale@example-stale.test" not in stored
    assert Suppression.objects.get().value == "stale@example-stale.test"
    assert gdpr_service.export("stale@example-stale.test")["modules"]["django_leads"]["Contact"][0]["anonymised_at"]


def test_gdpr_registry_rejects_a_module_without_both_hooks(monkeypatch):
    import types

    from django.core.exceptions import ImproperlyConfigured

    from django_leads.gdpr import registry

    with pytest.raises(ImproperlyConfigured, match="gdpr_erase"):
        registry._validated(types.SimpleNamespace(__name__="x.gdpr", gdpr_export=lambda email: {}))


def test_gdpr_api_export_and_erase_admin_only(admin_api, customer_api, subject, django_capture_on_commit_callbacks):
    body = {"email": "stale@example-stale.test"}
    assert customer_api.post("/api/leads/v2/admin/gdpr/export/", body, format="json").status_code == 403
    assert admin_api.post("/api/leads/v2/admin/gdpr/export/", {"email": "nope"}, format="json").status_code == 400

    exported = admin_api.post("/api/leads/v2/admin/gdpr/export/", body, format="json")
    with django_capture_on_commit_callbacks(execute=True):
        erased = admin_api.post("/api/leads/v2/admin/gdpr/erase/", body, format="json")

    assert exported.status_code == 200 and "django_communicator" in exported.json()["modules"]
    assert erased.status_code == 200 and erased.json()["modules"]["django_leads"]["contacts"] == 1
    assert Activity.objects.get(kind=ActivityKind.NOTE).actor == "operator"


def test_gdpr_command_exports_to_a_private_file_and_erases_with_yes(subject, tmp_path, monkeypatch):
    import io
    import json

    from django.core.management import CommandError, call_command

    target = tmp_path / "out.json"
    out = io.StringIO()
    call_command("leads_gdpr", "--email", "stale@example-stale.test", "--export", str(target), stdout=out)
    assert json.loads(target.read_text())["modules"]["django_leads"]["Contact"] and target.stat().st_mode & 0o077 == 0
    assert "django_agreements" in out.getvalue()

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(CommandError):
        call_command("leads_gdpr", "--email", "stale@example-stale.test", "--erase")
    call_command("leads_gdpr", "--email", "stale@example-stale.test", "--erase", "--yes", stdout=out)
    assert "django_leads: " in out.getvalue() and Contact.objects.get().anonymised_at


def test_anonymise_now_endpoint_runs_retention_as_of(admin_api, company):
    stale_contact(company)
    age(company, timezone.now() - timedelta(days=179, hours=12))
    url = "/api/leads/v2/admin/default-europe/test/anonymise-now/"
    assert admin_api.post(url, {}, format="json").json() == {"anonymised": {"default-europe": 0}}
    as_of = (timezone.now() + timedelta(days=1)).isoformat()
    assert admin_api.post(url, {"as_of": as_of}, format="json").json() == {"anonymised": {"default-europe": 1}}


# --- Connector seam ---


def test_connector_registry_csv_matches_protocol(channel, tmp_path):
    from django_leads.connectors.base import CandidateRow
    from django_leads.connectors.csv import CsvConnector
    from django_leads.connectors.registry import get_connector

    path = tmp_path / "leads.csv"
    path.write_text("company_name,domain,email,extra\nShop, shop.pl ,Jan@Shop.pl,x\n", encoding="utf-8")
    connector = get_connector("csv", path=path)

    assert isinstance(connector, CsvConnector) and connector.key == "csv"
    (row,) = connector.fetch_candidates(channel)
    assert isinstance(row, CandidateRow)
    assert (row.company_name, row.domain, row.email, row.phone, row.external_ref) == (
        "Shop",
        "shop.pl",
        "Jan@Shop.pl",
        "",
        "",
    )
    assert connector.push_status(Company(channel=channel)) is None
    with pytest.raises(LookupError):
        get_connector("nope")


def test_connector_twenty_raises_not_implemented():
    from django_leads.connectors.registry import get_connector

    with pytest.raises(NotImplementedError, match="interface only in v1"):
        get_connector("twenty")
