# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Q5: address verification — the check, the gate, the schedule, hard bounces. Resolver and prober are stubs."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.cache import cache
from django.utils import timezone

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, EmailStatus
from django_leads.models import Activity, Contact
from django_leads.services import outreach_service, verification_service
from django_leads.services.verification_service import DnsAnswer, SmtpProber, Verdict, verify_address
from django_leads.tasks import reverify_addresses
from tests.conftest import api_url

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "email",
    ["", "no-at.test", "two@@x.test", "a b@x.test", "x@nodot", ".lead@x.test", "dou..ble@x.test", "x@-bad-.test"],
)
def test_syntax_is_invalid_without_any_dns_query(email, dns):
    assert verify_address(email) == Verdict(EmailStatus.INVALID, "syntax")
    assert dns.calls == []


def test_disposable_domain_and_its_subdomain_are_invalid(dns):
    assert verify_address("x@mailinator.com") == Verdict(EmailStatus.INVALID, "disposable")
    assert verify_address("x@box.yopmail.com") == Verdict(EmailStatus.INVALID, "disposable")
    assert dns.calls == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (DnsAnswer(mx_hosts=("mx.a.test",)), (EmailStatus.VALID, "mx_ok")),
        (DnsAnswer(exists=False), (EmailStatus.INVALID, "domain_not_found")),
        (DnsAnswer(null_mx=True), (EmailStatus.INVALID, "null_mx")),
        (DnsAnswer(has_address=True), (EmailStatus.RISKY, "no_mx_address_fallback")),
        (DnsAnswer(), (EmailStatus.INVALID, "no_mail_server")),
        (DnsAnswer(error="dns_error"), (EmailStatus.UNKNOWN, "dns_error")),
    ],
)
def test_dns_outcomes(answer, expected, dns):
    dns.answers["shop.test"] = answer
    assert verify_address("Jan@Shop.test") == Verdict(*expected)
    assert dns.calls == ["shop.test"]


def test_probe_is_off_by_default_and_decides_when_on(dns, prober, monkeypatch):
    assert verify_address("jan@shop.test").reason == "mx_ok" and prober.calls == []
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_SMTP_PROBE", True)
    prober.result = Verdict(EmailStatus.INVALID, "mailbox_rejected")
    assert verify_address("jan@shop.test") == Verdict(EmailStatus.INVALID, "mailbox_rejected")
    assert prober.calls == [("mx.stub.test", "jan@shop.test")]
    prober.result = None  # inconclusive: the DNS verdict stands
    assert verify_address("jan@shop.test").reason == "mx_ok"


def test_probe_rate_limit_and_missing_sender(monkeypatch):
    cache.clear()
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_SMTP_PER_MINUTE", 2)
    assert [verification_service._take_probe_slot() for _ in range(3)] == [True, True, False]
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_SMTP_FROM", "")
    with mock.patch("smtplib.SMTP") as smtp:  # no sender address configured: nothing is even opened
        assert SmtpProber().probe("mx.a.test", "x@a.test") is None
    smtp.assert_not_called()


def test_smtp_prober_reads_the_rcpt_code(monkeypatch):
    cache.clear()
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_SMTP_FROM", "probe@sender.test")
    for code, expected in [(250, EmailStatus.VALID), (550, EmailStatus.INVALID), (450, None)]:
        with mock.patch("smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.rcpt.return_value = (code, b"")
            got = SmtpProber().probe("mx.a.test", "x@a.test")
        assert (got.status if got else None) == expected


def test_external_hook_refines_but_never_overrides_invalid(dns, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_EXTERNAL", "tests.test_verification.vendor")
    assert verify_address("jan@shop.test") == Verdict(EmailStatus.RISKY, "catch_all")
    dns.answers["gone.test"] = DnsAnswer(exists=False)
    assert verify_address("jan@gone.test").reason == "domain_not_found"
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_EXTERNAL", "tests.test_verification.broken")
    assert verify_address("jan@shop.test").reason == "mx_ok"  # a vendor outage changes nothing


def vendor(email):
    return ("risky", "catch_all")


def broken(email):
    raise RuntimeError("vendor down")


def test_verify_contact_stores_result_and_logs_only_changes_that_matter(shop, dns):
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    verification_service.verify_contact(contact)
    contact.refresh_from_db()
    assert (contact.email_status, contact.email_status_reason) == ("valid", "mx_ok") and contact.email_verified_at
    assert not Activity.objects.filter(kind=ActivityKind.VERIFICATION).exists()  # unknown -> valid is not news
    dns.answers["example-shop-4.test"] = DnsAnswer(exists=False)
    verification_service.verify_contact(contact)
    note = Activity.objects.get(kind=ActivityKind.VERIFICATION)
    assert note.data == {"from": "valid", "to": "invalid", "reason": "domain_not_found"} and note.contact == contact


def test_stored_invalid_blocks_the_gate_even_with_the_loop_off(shop, dns):
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    Contact.objects.filter(pk=contact.pk).update(email_status="invalid", email_status_reason="syntax")
    with mock.patch.object(outreach_service, "communicate") as communicate:
        result = outreach_service.request_draft(shop, Contact.objects.get(pk=contact.pk), "t", actor="test")
    assert result.reason == "email_invalid" and not communicate.called and dns.calls == []
    assert Activity.objects.filter(kind=ActivityKind.BLOCKED, message="blocked: email_invalid").exists()


def test_loop_on_verifies_before_the_draft_and_a_bad_address_blocks(shop, dns, verify_on):
    dns.answers["example-shop-4.test"] = DnsAnswer(exists=False)
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    with mock.patch.object(outreach_service, "communicate") as communicate:
        result = outreach_service.request_draft(shop, contact, "t", actor="test")
    assert result.reason == "email_invalid" and not communicate.called
    contact.refresh_from_db()
    assert contact.email_status == "invalid" and contact.email_status_reason == "domain_not_found"


def test_loop_on_good_address_goes_through_and_is_not_rechecked_while_fresh(shop, dns, verify_on):
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    message = SimpleNamespace(pk=1, status="review_required")
    patches = ("communicate", "render_legal_footer", "resolve_clause_set")
    with (
        mock.patch.object(outreach_service, "communicate", return_value=message),
        mock.patch.object(outreach_service, "render_legal_footer", return_value="f"),
        mock.patch.object(outreach_service, "resolve_clause_set"),
    ):
        assert outreach_service.request_draft(shop, contact, "t", actor="a") is message
        outreach_service.request_draft(shop, contact, "t", actor="a")
    assert len(dns.calls) == 1 and patches


def test_loop_off_never_queries_dns(shop, dns):
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    verification_service.ensure_fresh(contact)
    assert verification_service.reverify_stale() == {} and dns.calls == []


def test_scheduled_run_checks_stale_first_and_skips_fresh_and_hard_bounces(shop, dns, verify_on):
    old = timezone.now() - timedelta(days=60)
    piotr, ola = (shop.contacts.get(email=f"{n}@example-shop-4.test") for n in ("piotr", "ola"))
    Contact.objects.filter(pk=ola.pk).update(email_verified_at=timezone.now(), email_status="valid")
    Contact.objects.filter(pk=piotr.pk).update(email_verified_at=old, email_status="valid")
    marek = shop.contacts.get(first_name="Marek")  # no address: never verified
    assert verification_service.stale_contacts().count() == 1
    Contact.objects.filter(pk=piotr.pk).update(email_status="invalid", email_status_reason="hard_bounce")
    assert verification_service.stale_contacts().count() == 0
    Contact.objects.filter(pk=piotr.pk).update(email_status_reason="syntax")
    result = reverify_addresses()
    assert result["checked"] == {"valid": 1} and dns.calls == ["example-shop-4.test"] and marek.email == ""


def test_scheduled_run_respects_the_batch_limit(shop, dns, verify_on, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_VERIFY_BATCH", 1)
    assert sum(verification_service.reverify_stale().values()) == 1


def bounce(shop, email="piotr@example-shop-4.test", kind="bounce_hard"):
    from django_communicator.models import Channel, Reply, Thread

    channel = Channel.objects.get_or_create(idx=shop.channel.idx, defaults={"label": "x"})[0]
    thread = Thread.objects.create(channel=channel, subject_ref=f"leads.Company:{shop.pk}", recipient_email=email)
    return Reply.objects.create(
        channel=channel, thread=thread, from_email="mailer-daemon@x.test", kind=kind, matched_by="dsn",
        inbound_message_id=f"<b-{email}-{kind}>", received_at=timezone.now(),
    )  # fmt: skip


def test_hard_bounce_marks_the_contact_invalid_for_good_and_is_idempotent(shop):
    bounce(shop)
    bounce(shop, email="ola@example-shop-4.test", kind="bounce_soft")
    assert verification_service.sync_bounces() == 1 and verification_service.sync_bounces() == 0
    piotr = shop.contacts.get(email="piotr@example-shop-4.test")
    assert (piotr.email_status, piotr.email_status_reason) == ("invalid", "hard_bounce")
    assert shop.contacts.get(email="ola@example-shop-4.test").email_status == "unknown"
    assert Activity.objects.filter(kind=ActivityKind.VERIFICATION, data__reason="hard_bounce").count() == 1
    assert not verification_service.needs_check(piotr)


def test_old_bounces_are_outside_the_sync_window(shop):
    reply = bounce(shop)
    type(reply).objects.filter(pk=reply.pk).update(received_at=timezone.now() - timedelta(days=60))
    assert verification_service.sync_bounces() == 0


def test_presend_check_for_the_sender(shop, dns, verify_on):
    assert verification_service.presend_check("nobody@else.test") is None
    assert verification_service.presend_check("piotr@example-shop-4.test") is None
    dns.answers["example-shop-4.test"] = DnsAnswer(exists=False)
    Contact.objects.filter(email="piotr@example-shop-4.test").update(email_verified_at=None)
    assert verification_service.presend_check("PIOTR@example-shop-4.test") == "email_invalid"


def test_api_verify_report_filter_and_response_fields(shop, admin_api, dns):
    contact = shop.contacts.get(email="piotr@example-shop-4.test")
    dns.answers["example-shop-4.test"] = DnsAnswer(exists=False)
    body = admin_api.post(api_url(f"contacts/{contact.pk}/verify/")).json()
    assert (
        body["email_status"] == "invalid"
        and body["email_status_reason"] == "domain_not_found"
        and body["email_verified_at"]
    )
    listed = admin_api.get(api_url("contacts/?email_status=invalid")).json()
    assert [row["id"] for row in listed["results"]] == [contact.pk]
    report = admin_api.get(api_url("reports/verification/")).json()
    assert report["counts"] == {"invalid": 1, "unknown": 1} and report["enabled"] is False and report["stale"] == 1
    empty = shop.contacts.get(first_name="Marek")
    assert admin_api.post(api_url(f"contacts/{empty.pk}/verify/")).status_code == 400
    assert admin_api.get(api_url("contacts/?email_status=bogus")).status_code == 400


def test_verification_endpoints_are_staff_only(shop, customer_api):
    contact = shop.contacts.first()
    for path in (f"contacts/{contact.pk}/verify/", f"contacts/{contact.pk}/send-check/"):
        assert customer_api.post(api_url(path)).status_code == 403
    assert customer_api.get(api_url("reports/verification/")).status_code == 403
