# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest

from django_prospecting import settings as prospecting_settings
from django_prospecting.enums import CheckKind, CheckResult, SearchReason, SearchStatus
from django_prospecting.models import PeopleSearch
from django_prospecting.providers.base import FoundPerson, ProviderError, SearchAnswer
from django_prospecting.services import search_service
from tests.prospecting.conftest import CHANNEL_IDX, DOMAIN, SUBJECT, queued_search, run_search

pytestmark = pytest.mark.django_db


def checks(candidate) -> dict[str, str]:
    return {check.kind: check.result for check in candidate.checks.all()}


def test_fixed_search_finds_three_people_with_their_checks(fixed_provider):
    search = run_search()
    assert search.status == SearchStatus.DONE
    assert search.mail_pattern == "{first}.{last}"
    assert f"jan.nowak@{DOMAIN}" in search.pattern_source
    people = {c.last_name: c for c in search.candidates.all()}
    assert set(people) == {"Zielińska", "Wójcik", "Lis"}
    assert checks(people["Zielińska"]) == dict.fromkeys(CheckKind.values, CheckResult.OK)
    assert people["Zielińska"].email == f"ewa.zielinska@{DOMAIN}"
    # The role of the office manager never fits; the CEO's employment is never confirmed.
    assert checks(people["Wójcik"])[CheckKind.ROLE] == CheckResult.NO_INFO
    assert checks(people["Lis"])[CheckKind.EMPLOYED] == CheckResult.NO_INFO


def test_a_missing_check_is_tried_three_times_then_no_information(fixed_provider):
    search = run_search()
    assert search.attempts == prospecting_settings.PROSPECTING_MAX_ATTEMPTS == 3
    role = search.candidates.get(last_name="Wójcik").checks.get(kind=CheckKind.ROLE)
    assert (role.result, role.attempts, role.value) == (CheckResult.NO_INFO, 3, "Office Manager")


def test_every_found_information_has_a_source_and_a_date(fixed_provider):
    candidate = run_search().candidates.get(last_name="Zielińska")
    assert candidate.source and candidate.found_at
    for check in candidate.checks.all():
        assert check.source and check.checked_at, check.kind
    for option in candidate.email_options:
        assert option["source"] and option["tested_at"] and option["verdict"]


def test_catch_all_domain_leaves_the_mailbox_without_information(fixed_provider):
    search = run_search(domain="catchall.shop.test")
    mailbox = search.candidates.get(last_name="Zielińska").checks.get(kind=CheckKind.MAILBOX)
    assert mailbox.result == CheckResult.NO_INFO
    assert mailbox.value == "the domain accepts every address"


class OnlyFirstAnswerFails:
    def __init__(self):
        self.calls = 0

    def find_people(self, **kwargs) -> SearchAnswer:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("ToolboxConnectionError -")
        person = FoundPerson(first_name="Ola", last_name="Kot", job_title="CEO", role_fits=True, still_employed=True)
        return SearchAnswer(people=[person])


def test_a_failed_attempt_is_retried(monkeypatch, fixed_provider):
    provider = OnlyFirstAnswerFails()
    monkeypatch.setattr("django_prospecting.providers.search_provider", lambda: provider)
    search = run_search()
    assert search.status == SearchStatus.DONE
    assert provider.calls == 2  # the second answer confirms every check: no third call
    assert "ToolboxConnectionError" in search.detail
    candidate = search.candidates.get()
    # No pattern was published: the guess ola.kot@ confirmed by the tester makes the address known.
    assert checks(candidate) == dict.fromkeys(CheckKind.values, CheckResult.OK)


def test_nothing_found_is_failed(monkeypatch, fixed_provider):
    class Empty:
        def find_people(self, **kwargs):
            return SearchAnswer()

    monkeypatch.setattr("django_prospecting.providers.search_provider", Empty)
    assert run_search().status == SearchStatus.FAILED


def test_daily_budget_stops_the_search(monkeypatch, fixed_provider):
    monkeypatch.setattr(prospecting_settings, "PROSPECTING_DAILY_BUDGET", 1)
    search = run_search()
    assert search.attempts == 1
    assert "daily budget" in search.detail
    assert search_service.spent_today(CHANNEL_IDX) == 1


def test_start_runs_on_commit_and_reuses_a_queued_search(fixed_provider, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False):
        first = search_service.start(
            channel_idx=CHANNEL_IDX, subject_ref=SUBJECT, company_name="Shop", domain="Shop.TEST",
            reason=SearchReason.OPTED_OUT,
        )  # fmt: skip
        again = search_service.start(
            channel_idx=CHANNEL_IDX, subject_ref=SUBJECT, company_name="Shop", domain="shop.test", reason="manual"
        )
    assert again.pk == first.pk and first.domain == "shop.test"
    with django_capture_on_commit_callbacks(execute=True):
        search_service.start(
            channel_idx=CHANNEL_IDX, subject_ref="leads.Company:7", company_name="Other", domain="other.test",
            reason="manual",
        )  # fmt: skip
    assert PeopleSearch.objects.get(subject_ref="leads.Company:7").status == SearchStatus.DONE  # eager worker


def test_company_search_limit_refuses(monkeypatch, fixed_provider):
    monkeypatch.setattr(prospecting_settings, "PROSPECTING_COMPANY_SEARCH_LIMIT", 1)
    queued_search(status=SearchStatus.DONE)
    refused = search_service.start(
        channel_idx=CHANNEL_IDX, subject_ref=SUBJECT, company_name="Shop", domain="shop.test", reason="manual"
    )
    assert refused.status == SearchStatus.REFUSED
    assert "limit" in refused.detail
