# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""An address in the erased-address memory never becomes a contact again: every writer of a contact email refuses it."""

from unittest import mock

import pytest
from django.db import IntegrityError

from django_leads.enums import LeadSource
from django_leads.models import Contact
from django_leads.services import contact_service, erased_address_service, import_service
from tests.conftest import api_url
from tests.test_import import csv_of, reasons, run

ERASED = "gone@ogrod.pl"


@pytest.fixture
def erased(db):
    erased_address_service.remember(ERASED)


def test_post_contact_with_an_erased_address_is_409(admin_api, company, erased):
    body = {"company_id": company.pk, "email": " Gone@Ogrod.pl "}
    response = admin_api.post(api_url("contacts/"), body, format="json")
    assert response.status_code == 409 and response.json()["error"] == "ADDRESS_ERASED"
    assert not Contact.objects.exists()


def test_patch_contact_email_to_an_erased_address_is_409(admin_api, company, erased):
    contact = Contact.objects.create(company=company, email="", first_name="Ewa", source=LeadSource.MANUAL)
    response = admin_api.patch(api_url(f"contacts/{contact.pk}/"), {"email": "GONE@ogrod.pl"}, format="json")
    assert response.status_code == 409 and response.json()["error"] == "ADDRESS_ERASED"
    contact.refresh_from_db()
    assert contact.email == ""


def test_other_addresses_still_work(admin_api, company, erased):
    response = admin_api.post(api_url("contacts/"), {"company_id": company.pk, "email": "ok@ogrod.pl"}, format="json")
    assert response.status_code == 201


def test_create_contact_service_refuses(company, erased):
    row = {"email": ERASED, "source": LeadSource.MANUAL}
    with pytest.raises(erased_address_service.AddressErased):
        contact_service.create_contact(company, row, actor="op")


def test_update_contact_service_refuses(company, erased):
    contact = Contact.objects.create(company=company, email="", first_name="Ewa", source=LeadSource.MANUAL)
    with pytest.raises(erased_address_service.AddressErased):
        contact_service.update_contact(contact, {"email": ERASED}, actor="op")


def test_upsert_contact_refuses_a_new_contact(company, erased):
    """The form path and the import's one-by-one path both create through `upsert_contact`."""
    with pytest.raises(erased_address_service.AddressErased):
        contact_service.upsert_contact(company, {"email": ERASED, "source": LeadSource.FORM})
    assert not Contact.objects.exists()


def test_import_one_by_one_path_skips_an_erased_address(channel, erased, monkeypatch):
    """The chunk filter is bypassed and the bulk write fails: the row-by-row redo still refuses the address."""
    monkeypatch.setattr(import_service._ChunkWriter, "_without_erased", lambda self, prepared: prepared)
    monkeypatch.setattr(import_service._ChunkWriter, "flush", mock.Mock(side_effect=IntegrityError("race")))
    batch = run(channel, csv_of("Ogrod,ogrod.test,,,,Ewa,Gone,gone@ogrod.pl,,,consent,"))
    assert reasons(batch) == {2: "erased_address"}
    assert not Contact.objects.exists()


def test_import_chunk_path_skips_an_erased_address(channel, erased):
    batch = run(channel, csv_of("Ogrod,ogrod.test,,,,Ewa,Gone,gone@ogrod.pl,,,consent,"))
    assert reasons(batch) == {2: "erased_address"}
    assert not Contact.objects.exists()
