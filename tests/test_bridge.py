# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from unittest import mock

import pytest
from django.core.cache import cache
from django.db import OperationalError
from django_contact_forms.models import APIKey, ContactForm, Lead, LeadCreationRule
from django_contact_forms.models import Channel as FormsChannel
from django_contact_forms.services import lead_service
from rest_framework.test import APIClient

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Contact
from django_leads.services import company_service, contact_service, form_service
from django_leads.signals import contact_forms_bridge
from django_leads.tasks import import_form_lead


@pytest.fixture
def forms_channel(polish) -> FormsChannel:
    return FormsChannel.objects.create(
        idx="default-europe", label="Forms", admin_email="forms@example.test", default_language=polish
    )


def submit(forms_channel: FormsChannel, body: dict, email: str = "Ewa@Ogrod.pl") -> Lead:
    form = ContactForm.objects.create(
        channel=forms_channel, email=email, body=body, language=forms_channel.default_language
    )
    return lead_service.create_lead(
        contact_form=form, source_type="form", email=email, name="Ewa Maria Lis", phone="600", raw_data=body
    )


def test_L06_form_lead_creates_contact_on_existing_company_after_commit(
    company, forms_channel, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks() as callbacks:
        lead = submit(forms_channel, {"marketing_consent": True, "website": "https://www.ogrod.pl/kontakt"})
        assert not Contact.objects.exists()
    for callback in callbacks:
        callback()
    contact = Contact.objects.get()
    assert contact.company == company and Company.objects.count() == 1
    assert (contact.email, contact.first_name, contact.last_name, contact.phone) == (
        "ewa@ogrod.pl",
        "Ewa",
        "Maria Lis",
        "600",
    )
    assert (contact.source, contact.legal_basis, contact.language.iso2) == (LeadSource.FORM, "consent", "pl")
    activity = Activity.objects.get(company=company, kind=ActivityKind.FORM)
    assert activity.data == {"lead_id": lead.pk, "contact_form_id": lead.contact_form_id}
    lead.refresh_from_db()
    assert lead.status == Lead.Status.NEW


def test_L07_form_without_consent_contact_without_legal_basis(
    channel, forms_channel, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        submit(forms_channel, {"message": "hello"})
    contact = Contact.objects.get()
    assert contact.legal_basis is None and contact.company.domain == "ogrod.pl"
    messages = set(Activity.objects.filter(kind=ActivityKind.FORM).values_list("message", flat=True))
    assert messages == {"no legal basis", "form submission"}


def test_freemail_submission_without_website_is_skipped(channel, forms_channel, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        submit(forms_channel, {"marketing_consent": True}, email="ewa@gmail.com")
    assert not Company.objects.exists()


def test_status_change_is_ignored(channel, forms_channel, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        lead = submit(forms_channel, {})
    with django_capture_on_commit_callbacks() as callbacks:
        lead_service.transition_status(lead=lead, new_status=Lead.Status.CONTACTED)
    assert callbacks == []


def test_unknown_leads_channel_writes_nothing(db, polish, django_capture_on_commit_callbacks):
    other = FormsChannel.objects.create(idx="default-local", label="Local", admin_email="l@example.test")
    with django_capture_on_commit_callbacks(execute=True):
        submit(other, {"marketing_consent": True})
    assert not Company.objects.exists()


def test_L19_channel_without_rule_bridge_never_called(channel, forms_channel, django_capture_on_commit_callbacks):
    cache.clear()
    key = APIKey.objects.create(channel=forms_channel)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.key)
    url = f"/api/contact-forms/v2/{forms_channel.idx}/submit/"
    with mock.patch("django_leads.services.form_service.import_form_lead") as bridge:
        with django_capture_on_commit_callbacks(execute=True):
            assert (
                client.post(
                    url, {"email": "ewa@ogrod.pl", "body": {"marketing_consent": True}}, format="json"
                ).status_code
                == 201
            )
        bridge.assert_not_called()
        LeadCreationRule.objects.create(channel=forms_channel, form_type="", slug="", enabled=True)
        with django_capture_on_commit_callbacks(execute=True):
            assert (
                client.post(
                    url, {"email": "ewa@ogrod.pl", "body": {"marketing_consent": True}}, format="json"
                ).status_code
                == 201
            )
        bridge.assert_called_once()


def test_bridge_connects_once_with_dispatch_uid():
    from django_contact_forms.signals import lead_status_changed

    assert contact_forms_bridge.connect() is True
    uids = [receiver[0][0] for receiver in lead_status_changed.receivers]
    assert uids.count(contact_forms_bridge.DISPATCH_UID) == 1


@pytest.mark.parametrize("value", ["false", "False", "0", "off", "no", "", "nope", 0, 2, False, None, [], {}])
def test_L03_string_false_is_not_consent(channel, forms_channel, django_capture_on_commit_callbacks, value):
    with django_capture_on_commit_callbacks(execute=True):
        submit(forms_channel, {"marketing_consent": value, "website": "https://ogrod.pl"})
    assert Contact.objects.get().legal_basis is None
    assert Activity.objects.filter(message="no legal basis").exists()


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", "y", "tak", " Tak ", True, 1])
def test_L03_string_true_values_are_consent(value):
    assert form_service.has_consent({"marketing_consent": value}) is True


def test_consent_from_form_writes_legal_basis_activity_with_submission_ref(
    channel, forms_channel, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        lead = submit(forms_channel, {"marketing_consent": "on"})
    activity = Activity.objects.get(kind=ActivityKind.LEGAL_BASIS)
    assert activity.data == {"from": None, "to": "consent", "source": "form", "consent_ref": f"form:{lead.pk}"}


def test_no_legal_basis_activity_only_when_contact_has_none(company, forms_channel, django_capture_on_commit_callbacks):
    contact_service.upsert_contact(company, {"email": "ewa@ogrod.pl", "source": LeadSource.CSV})
    Contact.objects.update(legal_basis="legitimate_interest")
    with django_capture_on_commit_callbacks(execute=True):
        submit(forms_channel, {"marketing_consent": True})
    assert Contact.objects.get().legal_basis == "legitimate_interest"
    kinds = set(Activity.objects.values_list("kind", "message"))
    assert kinds == {
        (ActivityKind.LEGAL_BASIS_CONFLICT, "legal basis legitimate_interest -> consent"),
        (ActivityKind.FORM, "form submission"),
    }


def test_form_path_writes_no_import_activities(company, forms_channel, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        submit(forms_channel, {"marketing_consent": True})
        submit(forms_channel, {"marketing_consent": True})
    assert Contact.objects.count() == 1
    assert not Activity.objects.filter(kind=ActivityKind.IMPORT).exists()
    assert Activity.objects.filter(kind=ActivityKind.FORM, message="form submission").count() == 2


def test_bridge_failure_leaves_no_partial_company(channel, forms_channel, django_capture_on_commit_callbacks):
    with mock.patch.object(contact_service, "upsert_contact", side_effect=RuntimeError("boom")):
        with django_capture_on_commit_callbacks(execute=True):
            submit(forms_channel, {"marketing_consent": True})
    assert not Company.objects.exists() and not Activity.objects.exists()


def test_bridge_retry_is_idempotent(channel, forms_channel, django_capture_on_commit_callbacks):
    real, calls = company_service.upsert_company, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("connection lost")
        return real(*args, **kwargs)

    with mock.patch.object(company_service, "upsert_company", flaky):
        with django_capture_on_commit_callbacks(execute=True):
            lead = submit(forms_channel, {"marketing_consent": True})
    assert len(calls) == 2 and Contact.objects.count() == 1
    assert import_form_lead.delay(lead.pk, forms_channel.idx).get() is False
    assert Contact.objects.count() == 1
    assert Activity.objects.filter(kind=ActivityKind.FORM, message="form submission").count() == 1
