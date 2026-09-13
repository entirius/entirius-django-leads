# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from celery import current_app
from django.contrib.auth import get_user_model
from django_regional.models import Language
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django_leads.enums import LeadSource, StageKind
from django_leads.models import Channel, Company, Stage
from django_leads.services import company_service

CHANNEL_IDX = "default-europe"


@pytest.fixture(autouse=True)
def eager_celery():
    """Tasks run in-process — no broker in the module suite."""
    current_app.conf.task_always_eager = True
    yield
    current_app.conf.task_always_eager = False


@pytest.fixture
def polish(db) -> Language:
    return Language.objects.get_or_create(
        iso2="pl", defaults={"iso3": "pol", "name_en": "Polish", "name_pl": "Polski"}
    )[0]


@pytest.fixture
def channel(polish) -> Channel:
    channel = Channel.objects.create(idx=CHANNEL_IDX, name="Default Europe", default_language=polish)
    Stage.objects.create(channel=channel, key="new", label="New", order=0)
    Stage.objects.create(channel=channel, key="contacted", label="Contacted", order=10)
    Stage.objects.create(channel=channel, key="won", label="Won", order=20, kind=StageKind.WON, is_terminal=True)
    return channel


@pytest.fixture
def company(channel) -> Company:
    return company_service.upsert_company(channel, {"domain": "ogrod.pl", "name": "", "source": LeadSource.CSV})[0]


def _client_for(username: str, **flags) -> APIClient:
    user = get_user_model().objects.create_user(username=username, **flags)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


@pytest.fixture
def admin_api(db) -> APIClient:
    return _client_for("operator", is_staff=True)


@pytest.fixture
def customer_api(db) -> APIClient:
    return _client_for("customer")


def api_url(path: str) -> str:
    return f"/api/leads/v2/admin/{CHANNEL_IDX}/{path}"
