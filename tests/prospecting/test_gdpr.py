# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest

from django_leads.gdpr import registry
from django_prospecting import gdpr
from django_prospecting.models import Candidate, CandidateCheck
from tests.prospecting.conftest import DOMAIN, run_search

pytestmark = pytest.mark.django_db

ADDRESS = f"ewa.zielinska@{DOMAIN}"


def test_leads_discovers_the_prospecting_hooks():
    assert "django_prospecting" in registry.discover()


def test_export_lists_the_candidate_with_the_address_and_its_checks(fixed_provider):
    run_search()
    data = gdpr.gdpr_export(ADDRESS.upper())
    assert [row["last_name"] for row in data["Candidate"]] == ["Zielińska"]
    assert {row["kind"] for row in data["CandidateCheck"]} == {"role", "email", "employed", "mailbox"}


def test_erase_deletes_the_candidate_and_only_that_one(fixed_provider):
    run_search()
    assert gdpr.gdpr_erase(ADDRESS)["candidates"] == 1
    assert not Candidate.objects.filter(last_name="Zielińska").exists()
    assert Candidate.objects.count() == 2
    assert not CandidateCheck.objects.filter(value__iexact=ADDRESS).exists()


def test_a_guessed_address_in_the_options_is_found(fixed_provider):
    run_search()
    guess = next(o["email"] for o in Candidate.objects.get(last_name="Wójcik").email_options)
    assert [row["last_name"] for row in gdpr.gdpr_export(guess)["Candidate"]] == ["Wójcik"]


def test_a_blank_address_is_refused(db):
    with pytest.raises(ValueError):
        gdpr.gdpr_erase("  ")
