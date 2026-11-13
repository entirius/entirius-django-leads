# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Do not contact: one writer for `opt_out_at` and `do_not_contact`, communicator suppressions, confirmed opt-outs
under both scopes, hard bounces, the wrong-person flag, the gate's reasons and the `mailable` endpoint."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone
from django_communicator import settings as communicator_settings
from django_communicator import signals as communicator_signals
from django_communicator.models import AddressStatus, Message, Reply, Suppression, Thread
from django_communicator.models import Channel as CommunicatorChannel

from django_leads.enums import ActivityKind, LeadSource
from django_leads.models import Activity, Channel, Company, Contact, Stage
from django_leads.services import company_service, contact_service, outreach_service, recipient_service
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

PIOTR = "piotr@example-shop-4.test"


@pytest.fixture
def mailer(channel) -> CommunicatorChannel:
    return CommunicatorChannel.objects.create(idx=channel.idx, label="Europe")


@pytest.fixture
def piotr(shop) -> Contact:
    return Contact.objects.select_related("company__channel").get(email=PIOTR)


def suppressed(kind: str = "email") -> list[tuple[str, str]]:
    return list(Suppression.objects.filter(kind=kind).values_list("value", "source"))


def notes(company) -> list[str]:
    return list(Activity.objects.filter(company=company, kind=ActivityKind.NOTE).values_list("message", flat=True))


# --- opt-out ---------------------------------------------------------------------------------------------------------


def test_opted_out_address_is_refused_and_suppressed(mailer, piotr):
    contact_service.opt_out(piotr, reason="phone call", actor="operator")
    piotr.refresh_from_db()
    assert piotr.opt_out_at is not None and suppressed() == [(PIOTR, "manual")]
    assert recipient_service.block_reasons(piotr) == ["opted_out", "suppressed"]
    activity = Activity.objects.get(kind=ActivityKind.OPTOUT)
    assert (activity.contact_id, activity.data, activity.actor) == (piotr.pk, {"reason": "phone call"}, "operator")


def test_opted_out_contact_gets_no_draft(mailer, piotr):
    contact_service.opt_out(piotr, reason="x", actor="operator")
    with mock.patch.object(outreach_service, "communicate") as communicate:
        result = outreach_service.request_draft(piotr.company, piotr, "lead.cold.b2b", actor="operator")
    assert result == outreach_service.Blocked("opted_out") and not communicate.called


def test_opt_out_is_written_once(mailer, piotr):
    contact_service.opt_out(piotr, reason="x", actor="a")
    first = Contact.objects.get(pk=piotr.pk).opt_out_at
    contact_service.opt_out(Contact.objects.get(pk=piotr.pk), reason="y", actor="b")
    assert Contact.objects.get(pk=piotr.pk).opt_out_at == first
    assert Activity.objects.filter(kind=ActivityKind.OPTOUT).count() == 1


def test_opt_out_without_communicator_channel_still_blocks(piotr):
    contact_service.opt_out(piotr, reason="x", actor="a")
    assert recipient_service.block_reasons(piotr) == ["opted_out"] and not suppressed()


def other_channel_contact(polish) -> Contact:
    other = Channel.objects.create(idx="other", default_language=polish)
    stage = Stage.objects.create(channel=other, key="new", label="New")
    company = Company.objects.create(
        channel=other, name="Other", domain="other.test", stage=stage, stage_entered_at=timezone.now(), source="csv"
    )
    return Contact.objects.create(company=company, email=PIOTR, source=LeadSource.CSV)


def confirm_optout(channel_idx: str, capture) -> None:
    with capture(execute=True):
        communicator_signals.optout_confirmed.send(
            sender=Reply, subject_ref="", email=PIOTR.upper(), channel_idx=channel_idx
        )


@pytest.mark.parametrize(("scope", "both"), [("channel", False), ("install", True)])
def test_confirmed_optout_follows_the_scope(
    mailer, piotr, polish, monkeypatch, scope, both, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", scope)
    elsewhere = other_channel_contact(polish)
    confirm_optout(mailer.idx, django_capture_on_commit_callbacks)
    assert Contact.objects.get(pk=piotr.pk).opt_out_at is not None
    assert (Contact.objects.get(pk=elsewhere.pk).opt_out_at is not None) is both
    assert Activity.objects.get(company=piotr.company, kind=ActivityKind.OPTOUT).actor == "communicator"


# --- do not contact on the company -------------------------------------------------------------------------------


def test_do_not_contact_suppresses_the_domain_and_releases_it(mailer, shop):
    company_service.set_do_not_contact(shop, True, actor="operator")
    assert Company.objects.get(pk=shop.pk).do_not_contact
    assert suppressed("domain") == [("example-shop-4.test", "manual")]
    company_service.set_do_not_contact(shop, False, actor="operator")
    assert not Company.objects.get(pk=shop.pk).do_not_contact and not suppressed("domain")
    assert notes(shop) == ["do not contact: off", "do not contact: on"]


def test_release_keeps_a_suppression_added_in_communicator(mailer, shop):
    Suppression.objects.create(channel=mailer, kind="domain", value="example-shop-4.test", source="manual", reason="x")
    company_service.set_do_not_contact(shop, True, actor="operator")
    company_service.set_do_not_contact(shop, False, actor="operator")
    assert suppressed("domain") == [("example-shop-4.test", "manual")]


def test_global_domain_row_stays_while_another_channel_holds_it(mailer, shop, polish, monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "install")
    elsewhere = other_channel_contact(polish).company
    Company.objects.filter(pk=elsewhere.pk).update(domain=shop.domain, do_not_contact=True)
    company_service.set_do_not_contact(shop, True, actor="operator")
    company_service.set_do_not_contact(shop, False, actor="operator")
    assert Suppression.objects.filter(kind="domain", channel=None).exists()
    Company.objects.filter(pk=elsewhere.pk).update(do_not_contact=False)
    company_service.set_do_not_contact(shop, True, actor="operator")
    company_service.set_do_not_contact(shop, False, actor="operator")
    assert not suppressed("domain")


def test_do_not_contact_reads_the_stored_flag(mailer, shop):
    stale = Company.objects.get(pk=shop.pk)
    company_service.set_do_not_contact(shop, True, actor="operator")
    company_service.set_do_not_contact(stale, True, actor="other")
    assert notes(shop) == ["do not contact: on"]


def test_patch_do_not_contact_goes_through_the_writer(mailer, shop, admin_api):
    response = admin_api.patch(api_url(f"companies/{shop.pk}/"), {"do_not_contact": True}, format="json")
    assert response.status_code == 200 and response.json()["do_not_contact"] is True
    assert suppressed("domain") == [("example-shop-4.test", "manual")]
    assert Activity.objects.get(kind=ActivityKind.NOTE).actor == "operator"


def test_update_company_refuses_do_not_contact(shop):
    with pytest.raises(ValueError, match="not editable"):
        company_service.update_company(shop, {"do_not_contact": True})


def test_reviewer_skip_suppresses_the_domain(mailer, shop, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.company_skipped.send(sender=Message, subject_ref=f"leads.Company:{shop.pk}")
    assert Company.objects.get(pk=shop.pk).do_not_contact and suppressed("domain")


# --- bounce, wrong person ----------------------------------------------------------------------------------------


def thread(shop) -> SimpleNamespace:
    return SimpleNamespace(pk=5, subject_ref=f"leads.Company:{shop.pk}", recipient_email=PIOTR)


@pytest.mark.parametrize(("hard", "noted"), [(True, ["address invalid: hard bounce"]), (False, [])])
def test_hard_bounce_notes_the_contact(shop, hard, noted, django_capture_on_commit_callbacks):
    message = SimpleNamespace(pk=3, thread=thread(shop))
    with django_capture_on_commit_callbacks(execute=True):
        communicator_signals.message_bounced.send(sender=Message, message=message, hard=hard, reason="")
    assert notes(shop) == noted
    if hard:
        assert Activity.objects.get(kind=ActivityKind.NOTE).contact.email == PIOTR


def test_wrong_person_reply_flags_and_blocks_the_contact(shop, piotr, django_capture_on_commit_callbacks):
    reply = SimpleNamespace(
        pk=9, body_text="", intent="wrong_person", plan_applied={"notify": "medium"}, received_at=timezone.now()
    )
    with mock.patch("django_leads.services.alert_service.alert"), django_capture_on_commit_callbacks(execute=True):
        communicator_signals.reply_received.send(sender=Thread, thread=thread(shop), reply=reply)
    piotr.refresh_from_db()
    assert piotr.wrong_person_at is not None and "contact flagged: wrong person" in notes(shop)
    assert recipient_service.block_reason(piotr) == "wrong_person"
    assert PIOTR not in [contact.email for contact in recipient_service.candidates(shop)]


# --- gate reasons ------------------------------------------------------------------------------------------------


def test_block_reasons_lists_every_reason_in_gate_order(mailer, piotr):
    now = timezone.now()
    Company.objects.filter(pk=piotr.company_id).update(do_not_contact=True)
    Contact.objects.filter(pk=piotr.pk).update(opt_out_at=now, wrong_person_at=now, legal_basis=None)
    AddressStatus.objects.create(email=PIOTR, status="invalid", reason="syntax")
    contact = Contact.objects.select_related("company__channel").get(pk=piotr.pk)
    reasons = ["do_not_contact", "opted_out", "wrong_person", "no_legal_basis", "address_invalid"]
    assert recipient_service.block_reasons(contact) == reasons
    assert recipient_service.block_reason(contact) == "do_not_contact"


def test_communicator_suppression_blocks_a_clean_contact(mailer, piotr):
    Suppression.objects.create(channel=mailer, kind="domain", value="example-shop-4.test", source="manual")
    assert recipient_service.block_reasons(piotr) == ["suppressed"]
    assert not recipient_service.eligible(piotr)


# --- mailable ----------------------------------------------------------------------------------------------------


def test_mailable_explains_a_refused_contact(mailer, piotr, admin_api):
    contact_service.set_legal_basis(piotr, "consent", source="admin", consent_ref="call", actor="operator")
    contact_service.opt_out(piotr, reason="asked", actor="operator")
    body = admin_api.get(api_url(f"companies/{piotr.company_id}/contacts/{piotr.pk}/mailable/")).json()
    assert body["eligible"] is False and body["reasons"] == ["opted_out", "suppressed"]
    assert body["legal_basis"]["value"] == "consent" and body["legal_basis"]["source"] == "admin"
    assert body["communicator"]["refusal"] == "suppressed"
    assert [row["source"] for row in body["communicator"]["suppressions"]] == ["manual"]
    days = body["retention"]["days"]
    assert days == 180 and body["retention"]["exempt_won"] is False and body["retention"]["due_at"]


def test_mailable_of_an_eligible_contact_without_communicator(piotr, admin_api):
    body = admin_api.get(api_url(f"companies/{piotr.company_id}/contacts/{piotr.pk}/mailable/")).json()
    assert body["eligible"] is True and body["reasons"] == [] and body["communicator"] is None
    assert body["legal_basis"] == {"value": "legitimate_interest", "source": None, "set_at": None}


def test_mailable_retention_clock(piotr, admin_api):
    last = timezone.now() - timedelta(days=10)
    Company.objects.filter(pk=piotr.company_id).update(last_activity_at=last, stage=Stage.objects.get(key="won"))
    body = admin_api.get(api_url(f"companies/{piotr.company_id}/contacts/{piotr.pk}/mailable/")).json()
    assert body["retention"]["exempt_won"] is True
    assert body["retention"]["due_at"].startswith((last + timedelta(days=180)).date().isoformat())


def test_mailable_of_a_contact_of_another_company_is_404(shop, company, piotr, admin_api):
    assert admin_api.get(api_url(f"companies/{company.pk}/contacts/{piotr.pk}/mailable/")).status_code == 404
