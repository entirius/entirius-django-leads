# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django_access.models import Grant, Role
from django_access.services import permissions
from django_regional.models import Language
from django_todo.services import task_service
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django_ai_crm.services.caller import Caller
from django_leads.enums import LeadSource
from django_leads.models import Channel, DealStage, Stage
from django_leads.services import company_service, deal_service, owner_service

CHANNEL_IDX = "default-europe"
GATE = "django_access.middleware.AccessGateMiddleware"


@pytest.fixture(autouse=True)
def agent_settings(settings):
    """The agent's requests pass the access gate (enforce) and its day is a Warsaw day; the leads suite runs without
    either."""
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, GATE]
    settings.ACCESS_GATE_MODE = "enforce"
    settings.TIME_ZONE = "Europe/Warsaw"
    settings.STOREFRONT_BASE_URL = "http://storefront.test"
    cache.clear()  # access caches a user's permissions by pk; sqlite hands the same pk to the next test's user


@pytest.fixture(autouse=True)
def on_commit_now(monkeypatch):
    """Signals sent on commit (task closed → leads timeline) run at once inside the test transaction."""
    monkeypatch.setattr(transaction, "on_commit", lambda func, using=None, robust=False: func())


def make_user(username: str, role: str | None = "manager", **flags):
    user = get_user_model().objects.create_user(username=username, is_staff=True, **flags)
    if role:
        Grant.objects.create(role=Role.objects.get(key=role), user=user)
        permissions.bump_version()
    return user


@pytest.fixture
def anna(db):
    return make_user("anna", first_name="Anna", last_name="Kowalska")


@pytest.fixture
def piotr(db):
    return make_user("piotr")


@pytest.fixture
def viewer(db):
    return make_user("vera", role="viewer")


def api_for(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def url(path: str) -> str:
    return f"/api/ai-crm/v2/admin/{CHANNEL_IDX}/{path}"


@pytest.fixture
def channel(db) -> Channel:
    polish = Language.objects.get_or_create(
        iso2="pl", defaults={"iso3": "pol", "name_en": "Polish", "name_pl": "Polski"}
    )[0]
    channel = Channel.objects.create(idx=CHANNEL_IDX, name="Default Europe", default_language=polish)
    Stage.objects.create(channel=channel, key="new", label="New", order=0)
    for key, order, kind in [("lead", 0, "open"), ("offer", 10, "open"), ("won", 20, "won"), ("lost", 30, "lost")]:
        DealStage.objects.create(channel=channel, key=key, label=key.title(), order=order, kind=kind)
    return channel


def make_company(channel, domain: str, owner):
    company = company_service.upsert_company(
        channel, {"domain": domain, "name": domain.title(), "source": LeadSource.CSV}
    )[0]
    owner_service.set_owner(company, owner, actor="test")
    company.refresh_from_db()
    return company


@pytest.fixture
def shop(channel, anna):
    return make_company(channel, "shop.test", anna)


@pytest.fixture
def foreign(channel, piotr):
    """Another salesperson's company."""
    return make_company(channel, "foreign.test", piotr)


def make_task(company, owner, title: str, *, days: int = 0):
    return task_service.create(
        channel_idx=CHANNEL_IDX, subject_ref=f"leads.Company:{company.pk}", subject_label=company.name, kind="phone",
        title=title, due_at=timezone.now() + timedelta(days=days), owner_id=owner.pk, created_by="test",
    )  # fmt: skip


def make_deal(company, owner, title: str = "Shop deal"):
    return deal_service.create(company, {"title": title}, owner=owner, actor="test")


def caller(user, source: str = "api") -> Caller:
    return Caller(user=user, channel_idx=CHANNEL_IDX, source=source)
