# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from unittest import mock

import pytest
from django.core.cache import cache
from django_contact_forms.models import APIKey, ContactForm, Lead, LeadCreationRule
from django_contact_forms.models import Channel as FormsChannel
from django_contact_forms.services import lead_service
from rest_framework.test import APIClient

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Company, Contact
from django_leads.signals import contact_forms_bridge


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
