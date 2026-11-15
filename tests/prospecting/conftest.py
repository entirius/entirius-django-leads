# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django_prospecting.enums import SearchReason
from django_prospecting.services import search_service

CHANNEL_IDX = "default-europe"
SUBJECT = "leads.Company:42"
DOMAIN = "example-shop.test"


@pytest.fixture(autouse=True)
def utc(settings):
    settings.TIME_ZONE = "UTC"


@pytest.fixture
def without_leads(monkeypatch):
    """django_leads left out of INSTALLED_APPS: prospecting runs on its own, accepting has nowhere to go."""
    from django.apps import apps

    installed = apps.is_installed
    monkeypatch.setattr(apps, "is_installed", lambda name: name != "django_leads" and installed(name))


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(username="anna", is_staff=True)


@pytest.fixture
def staff_api(staff) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(staff).access_token}")
    return client


@pytest.fixture
def fixed_provider(settings, monkeypatch):
    from django_prospecting import settings as prospecting_settings

    monkeypatch.setattr(prospecting_settings, "PROSPECTING_SEARCH_PROVIDER", "fixed")
    monkeypatch.setattr(prospecting_settings, "PROSPECTING_ADDRESS_TESTER", "stub")


def queued_search(**overrides):
    """A queued search without running it (the worker is not called)."""
    from django_prospecting.models import PeopleSearch

    fields = {
        "channel_idx": CHANNEL_IDX,
        "subject_ref": SUBJECT,
        "company_name": "Example Shop",
        "domain": DOMAIN,
        "reason": SearchReason.WRONG_PERSON,
        **overrides,
    }
    return PeopleSearch.objects.create(**fields)


def run_search(**overrides):
    return search_service.run(queued_search(**overrides))


def api_url(path: str, channel_idx: str = CHANNEL_IDX) -> str:
    return f"/api/prospecting/v2/admin/{channel_idx}/{path}"
