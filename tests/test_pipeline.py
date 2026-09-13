# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import importlib
import json
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
from django.urls import clear_url_caches
from django_communicator import signals as communicator_signals
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.models import Thread
from django_siteintel.models import Audit, Report
from django_utils.toolbox.testing import error_response, mock_toolbox

from django_leads.enums import ActivityKind, ContactStrategy, RuleTrigger, StageKind
from django_leads.models import Activity, AnalysisProfile, Company, RecipientPickProfile, RuleRun, Stage
from django_leads.services import customer_link_service, intel_service, recipient_service, rotation_service
from django_leads.signals import communicator_receivers

pytestmark = pytest.mark.django_db

ANALYSIS = {
    "hooks": [{"challenge": f"c{n}", "business_cost": f"b{n}"} for n in range(3)],
    "platform": "Magento 2",
    "company_type_guess": "RETAILER",
}


def completion(parsed: dict) -> httpx.Response:
    body = {"output": json.dumps(parsed), "parsed": parsed, "usage": {"input_tokens": 1, "output_tokens": 1}}
    return httpx.Response(200, json={**body, "cost": "0", "model": "fake-chat", "attempts": 1, "request_id": "r"})


def messages(company, kind: str) -> list[str]:
    return list(Activity.objects.filter(company=company, kind=kind).values_list("message", flat=True))


@pytest.fixture
def pick_rule(make_rule):
    RecipientPickProfile.objects.create(
        channel=Stage.objects.first().channel,
        prompt_text="Pick for {company_name}: {contacts_json}",
        json_schema={"type": "object"},
        model="fake-chat",
    )
    return make_rule(trigger=RuleTrigger.INTEL_READY, contact_strategy=ContactStrategy.AI_PICK)


@pytest.fixture
def audit(shop) -> Audit:
    audit = Audit.objects.create(
        domain="www.example-shop-4.test", url="https://www.example-shop-4.test/", channel_idx="default-europe",
        requested_by="test", expires_at="2030-01-01T00:00:00Z",
    )  # fmt: skip
    Report.objects.create(audit=audit, source="lighthouse", status="done", processed={"performance": 41})
    AnalysisProfile.objects.create(
        channel=shop.channel, prompt_text="{company_name} {lighthouse_summary}", json_schema={}, model="fake-chat"
    )
    return audit


def test_L12_ai_pick_stores_contact_id_and_reason(shop, pick_rule):
    second = shop.contacts.get(email="ola@example-shop-4.test")
    with mock_toolbox() as router:
        router["complete"].mock(return_value=completion({"contact_id": second.pk, "reason": "owns the shop"}))
        contact, data = recipient_service.pick_recipient(shop, pick_rule)
        sent = json.loads(router["complete"].calls.last.request.content)
    assert sent["tags"] == ["leads.pick_recipient", "channel:default-europe"]
    block = json.loads(sent["messages"][-1]["content"].split(": ", 1)[1])
    assert [row["email"] for row in block["candidates"]] == ["piotr@example-shop-4.test", "ola@example-shop-4.test"]
    assert contact == second and data == {"contact_id": second.pk, "reason": "owns the shop"}
    activity = Activity.objects.get(company=shop, message="recipient picked")
    assert activity.data == data and "Pick for" not in json.dumps(activity.data)


@pytest.mark.parametrize("answer", [{"contact_id": 999999, "reason": "x"}, {"reason": "no id"}])
def test_L13_ai_pick_invalid_contact_falls_back_to_primary(shop, pick_rule, answer):
    without_email = shop.contacts.get(email="")
    answer = answer if answer["reason"] != "x" else {"contact_id": without_email.pk, "reason": "x"}
    with mock_toolbox() as router:
        router["complete"].mock(return_value=completion(answer))
        contact, data = recipient_service.pick_recipient(shop, pick_rule)
    assert contact.is_primary and data == {"reason": "fallback"}
    assert messages(shop, ActivityKind.RULE) == ["recipient pick rejected: unknown contact"]


def test_primary_strategy_never_calls_toolbox(shop, make_rule):
    with mock_toolbox() as router:
        contact, data = recipient_service.pick_recipient(shop, make_rule())
        assert not router["complete"].called
    assert contact.is_primary and data == {"reason": "primary"}


def test_analysis_sets_hooks_platform_type_then_intel_ready_rules(shop, audit):
    with mock_toolbox() as router, mock.patch("django_leads.services.rule_service.evaluate_rules") as evaluate:
        router["complete"].mock(return_value=completion(ANALYSIS))
        intel_service.analyse_audit(str(audit.pk), ["lighthouse"])
        sent = json.loads(router["complete"].calls.last.request.content)
    shop.refresh_from_db()
    assert sent["tags"][0] == "leads.analysis" and '{"performance":41}' in sent["messages"][0]["content"]
    assert len(shop.hooks) == 3 and shop.platform == "Magento 2" and shop.company_type == "RETAILER"
    assert Activity.objects.get(company=shop, kind=ActivityKind.INTEL).data["hooks"] == 3
    evaluate.assert_called_once_with(shop, RuleTrigger.INTEL_READY)


def test_L11_empty_sources_no_toolbox_call_rules_still_evaluated(shop, audit):
    with mock_toolbox() as router, mock.patch("django_leads.services.rule_service.evaluate_rules") as evaluate:
        intel_service.analyse_audit(str(audit.pk), [])
        assert not router["complete"].called
    assert evaluate.called
    assert messages(shop, ActivityKind.INTEL) == ["no intel sources succeeded"]


def test_analysis_error_writes_activity_no_retry_on_budget(shop, audit):
    with mock_toolbox() as router, mock.patch("django_leads.services.alert_service.alert") as alert:
        router["complete"].mock(return_value=error_response(402, "BUDGET_EXCEEDED"))
        from django_leads.tasks import analyse_intel

        analyse_intel.delay(str(audit.pk), ["lighthouse"])
        assert router["complete"].call_count == 1
    assert messages(shop, ActivityKind.INTEL) == ["analysis failed: BUDGET_EXCEEDED"]
    assert alert.call_args.kwargs["title"] == "Intel analysis failed"
    assert not RuleRun.objects.exists()


def test_report_ready_receiver_enqueues_analysis(audit, django_capture_on_commit_callbacks):
    from django_siteintel.signals import report_ready

    with mock.patch("django_leads.tasks.analyse_intel.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            report_ready.send(sender=Audit, audit=audit, succeeded_sources=["lighthouse"])
    delay.assert_called_once_with(str(audit.pk), ["lighthouse"])


@pytest.fixture
def threads(shop):
    channel = CommunicatorChannel.objects.create(idx="default-europe", label="Europe")

    def draft(company, contact, template_key, *, actor, thread=None):
        Thread.objects.create(channel=channel, subject_ref=f"leads.Company:{company.pk}", recipient_email=contact.email)
        return SimpleNamespace(pk=1, status="review_required")

    with mock.patch("django_leads.services.outreach_service.request_draft", side_effect=draft) as patched:
        patched.channel = channel
        yield patched


def test_L14_rotation_next_contact_then_unresponsive_after_max(shop, threads):
    shop.contacts.create(email="third@example-shop-4.test", legal_basis="consent", source="csv")
    first = Thread.objects.create(
        channel=threads.channel, subject_ref=f"leads.Company:{shop.pk}", recipient_email="piotr@example-shop-4.test"
    )
    assert rotation_service.rotate_thread(first).email == "ola@example-shop-4.test"
    assert rotation_service.rotate_thread(first) is None
    second = Thread.objects.get(recipient_email="ola@example-shop-4.test")
    assert rotation_service.rotate_thread(second).email == "third@example-shop-4.test"
    third = Thread.objects.get(recipient_email="third@example-shop-4.test")
    assert rotation_service.rotate_thread(third) is None
    shop.refresh_from_db()
    assert shop.rotation_count == 2 and shop.stage.kind == StageKind.UNRESPONSIVE
    assert messages(shop, ActivityKind.ROTATION)[0] == "rotation exhausted"


def test_rotation_without_unresponsive_stage_raises(shop, threads):
    Stage.objects.filter(kind=StageKind.UNRESPONSIVE).delete()
    shop.rotation_count = 2
    with pytest.raises(rotation_service.ConfigurationError):
        rotation_service.rotate_company(shop)
    assert messages(shop, ActivityKind.ROTATION) == ["no unresponsive stage"]


def test_reply_received_moves_to_on_reply_stage_and_notifies(shop, django_capture_on_commit_callbacks):
    thread = SimpleNamespace(pk=5, subject_ref=f"leads.Company:{shop.pk}", recipient_email="OLA@example-shop-4.test")
    reply = SimpleNamespace(pk=9, body_text="x" * 500)
    with mock.patch("django_leads.services.alert_service.alert") as alert:
        with django_capture_on_commit_callbacks(execute=True):
            communicator_signals.reply_received.send(sender=Thread, thread=thread, reply=reply)
    shop.refresh_from_db()
    assert shop.stage.key == "replied"
    assert Activity.objects.get(company=shop, kind=ActivityKind.REPLY).contact.email == "ola@example-shop-4.test"
    assert alert.call_args.kwargs == {"severity": "high", "title": "Reply from Example Shop 4", "body": "x" * 200}


def test_company_skipped_sets_do_not_contact(shop, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.company_skipped.send(sender=None, subject_ref=f"leads.Company:{shop.pk}", message=None)
    shop.refresh_from_db()
    assert shop.do_not_contact and messages(shop, ActivityKind.BLOCKED) == ["skipped by reviewer"]


@pytest.mark.parametrize("ref", ["pim.Product:1", "leads.Company:abc", "leads.Company:1 ", ""])
def test_subject_ref_of_other_module_ignored(shop, ref, django_capture_on_commit_callbacks):
    thread = SimpleNamespace(pk=1, subject_ref=ref, recipient_email="a@b.test")
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.reply_received.send(
            sender=Thread, thread=thread, reply=SimpleNamespace(pk=1, body_text="")
        )
        communicator_signals.company_skipped.send(sender=None, subject_ref=ref)
    assert not Activity.objects.exists() and not Company.objects.filter(do_not_contact=True).exists()


def test_receiver_failure_never_raises_into_sender(shop, django_capture_on_commit_callbacks):
    with mock.patch.object(communicator_receivers, "record_sent", side_effect=RuntimeError("boom")):
        with django_capture_on_commit_callbacks(execute=True):
            communicator_signals.message_sent.send(sender=None, message=SimpleNamespace())


def test_L15_create_customer_absent_without_accounts_and_links_by_email_address(shop, admin_api, settings):
    url = f"/api/leads/v2/admin/default-europe/companies/{shop.pk}/create-customer/"
    assert admin_api.post(url).status_code == 404
    try:
        with mock.patch("django.apps.apps.is_installed", return_value=True):
            reload_urls()
        with mock.patch.object(customer_link_service, "find_customer_uid", return_value=None) as find:
            response = admin_api.post(url)
        assert response.status_code == 409 and response.json()["error"] == "NotImplemented"
        find.assert_called_once_with("piotr@example-shop-4.test")
        uid = "0b8f5d3e-7c1a-4d2b-9a55-3f1e2d4c5b6a"
        with mock.patch.object(customer_link_service, "find_customer_uid", return_value=uid):
            response = admin_api.post(url)
        assert response.status_code == 200 and response.json() == {"customer_uid": uid}
        assert messages(shop, ActivityKind.NOTE) == [f"linked customer {uid}"]
    finally:
        reload_urls()


def reload_urls() -> None:
    import tests.urls
    from django_leads import urls
    from django_leads.api.admin import urls as admin_urls

    for module in (admin_urls, urls, tests.urls):
        importlib.reload(module)
    clear_url_caches()
