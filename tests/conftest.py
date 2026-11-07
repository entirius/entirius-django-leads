# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from celery import current_app
from django.contrib.auth import get_user_model
from django_agreements.enums import LegalBasis
from django_regional.models import Language
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django_leads import settings as leads_settings
from django_leads.enums import LeadSource, RuleTrigger, StageKind
from django_leads.models import Channel, Company, Contact, LeadType, Stage, StageRule
from django_leads.services import company_service, verification_service
from django_leads.services.verification_service import DnsAnswer, Verdict

CHANNEL_IDX = "default-europe"

pytest_plugins = ["tests.reply_flow"]  # T4 fixtures: fake_intent, inbox


@pytest.fixture(autouse=True)
def eager_celery():
    """Tasks run in-process — no broker in the module suite."""
    current_app.conf.task_always_eager = True
    yield
    current_app.conf.task_always_eager = False


class StubResolver:
    """Q5: the only DNS the suite ever sees. Unknown domains get one MX; `answers` overrides per domain."""

    def __init__(self) -> None:
        self.answers: dict[str, DnsAnswer] = {}
        self.calls: list[str] = []

    def lookup(self, domain: str) -> DnsAnswer:
        self.calls.append(domain)
        return self.answers.get(domain, DnsAnswer(mx_hosts=("mx.stub.test",)))


class StubProber:
    def __init__(self) -> None:
        self.result: Verdict | None = None
        self.calls: list[tuple[str, str]] = []

    def probe(self, mx_host: str, email: str) -> Verdict | None:
        self.calls.append((mx_host, email))
        return self.result


@pytest.fixture(autouse=True)
def dns(monkeypatch) -> StubResolver:
    """No test reaches the network: resolver and SMTP prober are stubs, the loop is off unless a test enables it."""
    resolver = StubResolver()
    verification_service.use_resolver(resolver)
    verification_service.use_prober(StubProber())
    yield resolver
    verification_service.use_resolver(None)
    verification_service.use_prober(None)


@pytest.fixture
def prober() -> StubProber:
    stub = StubProber()
    verification_service.use_prober(stub)
    return stub


@pytest.fixture
def verify_on(monkeypatch) -> None:
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_ENABLED", True)


@pytest.fixture(autouse=True)
def import_tmp_dir(tmp_path, monkeypatch):
    """Uploads land in the test's own directory, never in the shared system temp dir."""
    directory = tmp_path / "imports"
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_TMP_DIR", str(directory))
    return directory


@pytest.fixture
def polish(db) -> Language:
    return Language.objects.get_or_create(
        iso2="pl", defaults={"iso3": "pol", "name_en": "Polish", "name_pl": "Polski"}
    )[0]


@pytest.fixture
def channel(polish) -> Channel:
    channel = Channel.objects.create(idx=CHANNEL_IDX, name="Default Europe", default_language=polish)
    Stage.objects.create(channel=channel, key="new", label="New", order=0)
    Stage.objects.create(channel=channel, key="contacted", label="Contacted", order=10, on_sent=True)
    Stage.objects.create(channel=channel, key="won", label="Won", order=20, kind=StageKind.WON, is_terminal=True)
    Stage.objects.create(channel=channel, key="replied", label="Replied", order=30, on_reply=True)
    Stage.objects.create(
        channel=channel, key="unresponsive", label="Unresponsive", order=40, kind=StageKind.UNRESPONSIVE
    )
    for order, (code, label) in enumerate(
        [("MANUFACTURER", "Manufacturer"), ("WHOLESALE", "Wholesale"), ("RETAILER", "Retailer")]
    ):
        LeadType.objects.create(channel=channel, code=code, label=label, order=order * 10)
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


@pytest.fixture
def shop(channel) -> Company:
    """A company with hooks and three contacts: primary with email, a second with email, one without."""
    company = company_service.upsert_company(
        channel, {"domain": "example-shop-4.test", "name": "Example Shop 4", "source": LeadSource.CSV}
    )[0]
    company.hooks = [{"challenge": "slow pages", "business_cost": "lost carts"}]
    company.save(update_fields=["hooks"])
    basis = LegalBasis.LEGITIMATE_INTEREST
    Contact.objects.create(company=company, email="piotr@example-shop-4.test", is_primary=True, legal_basis=basis)
    Contact.objects.create(company=company, email="ola@example-shop-4.test", legal_basis=basis)
    Contact.objects.create(company=company, email="", first_name="Marek", legal_basis=basis)
    return company


@pytest.fixture
def make_rule(channel):
    def make(**fields) -> StageRule:
        defaults = {"trigger": RuleTrigger.STAGE_ENTERED, "template_key": "lead.cold.b2b"}
        if fields.get("trigger", RuleTrigger.STAGE_ENTERED) == RuleTrigger.STAGE_ENTERED:
            defaults["stage"] = Stage.objects.get(channel=channel, key="contacted")
        return StageRule.objects.create(channel=channel, **{**defaults, **fields})

    return make
