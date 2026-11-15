# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""People searches: start one (limits first), run it (up to `PROSPECTING_MAX_ATTEMPTS` provider calls) and keep the
four checks of every person with their source and date.

A check that is still missing after the last attempt stays `no_info` — the candidate shows which confirmation is
missing; a salesperson decides anyway. Nothing here writes to leads or sends mail."""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from django_prospecting import providers
from django_prospecting import settings as prospecting_settings
from django_prospecting.enums import AddressVerdict, CandidateStatus, CheckKind, CheckResult, SearchStatus
from django_prospecting.models import Candidate, CandidateCheck, PeopleSearch
from django_prospecting.providers.base import FoundPerson, ProviderError, SearchAnswer
from django_prospecting.services import leads_bridge, pattern_service

logger = logging.getLogger(__name__)

FINAL_VERDICTS = frozenset({AddressVerdict.VALID, AddressVerdict.INVALID, AddressVerdict.CATCH_ALL})


def start(
    *, channel_idx: str, subject_ref: str, company_name: str, domain: str, reason: str, requested_by: str = ""
) -> PeopleSearch:
    """A queued search runs once on commit (worker). A company with a queued search gets that one back; a company
    over `PROSPECTING_COMPANY_SEARCH_LIMIT` gets a `refused` search that never runs."""
    _fail_stale(channel_idx, subject_ref)
    queued = PeopleSearch.objects.filter(channel_idx=channel_idx, subject_ref=subject_ref, status=SearchStatus.QUEUED)
    if existing := queued.first():
        return existing
    fields = {"channel_idx": channel_idx, "subject_ref": subject_ref, "company_name": company_name}
    fields.update(domain=domain.lower(), reason=reason, requested_by=requested_by)
    done = PeopleSearch.objects.filter(channel_idx=channel_idx, subject_ref=subject_ref).exclude(
        status=SearchStatus.REFUSED
    )
    if done.count() >= prospecting_settings.PROSPECTING_COMPANY_SEARCH_LIMIT:
        detail = f"company search limit reached ({prospecting_settings.PROSPECTING_COMPANY_SEARCH_LIMIT})"
        return PeopleSearch.objects.create(
            status=SearchStatus.REFUSED, detail=detail, finished_at=timezone.now(), **fields
        )
    search = PeopleSearch.objects.create(**fields)
    transaction.on_commit(lambda: _enqueue(search.pk))
    return search


def _fail_stale(channel_idx: str, subject_ref: str) -> None:
    """A queued search untouched for `PROSPECTING_QUEUED_STALE_MINUTES` lost its worker: it must not hold the company."""
    limit = timezone.now() - timedelta(minutes=prospecting_settings.PROSPECTING_QUEUED_STALE_MINUTES)
    PeopleSearch.objects.filter(
        channel_idx=channel_idx, subject_ref=subject_ref, status=SearchStatus.QUEUED, modified_at__lt=limit
    ).update(
        status=SearchStatus.FAILED, detail="never finished (worker lost)", finished_at=timezone.now(),
        modified_at=timezone.now(),
    )  # fmt: skip


def _enqueue(search_id: int) -> None:
    from django_prospecting.tasks import run_search

    run_search.delay(search_id)


def spent_today(channel_idx: str) -> int:
    """Provider calls of the channel since midnight (the server's day)."""
    midnight = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = PeopleSearch.objects.filter(channel_idx=channel_idx, created_at__gte=midnight)
    return rows.aggregate(total=Sum("attempts"))["total"] or 0


def run(search: PeopleSearch) -> PeopleSearch:
    """Ask the provider until every check of every person is `ok` or the attempts are used up."""
    if search.status != SearchStatus.QUEUED:
        return search
    errors: list[str] = []
    try:
        _attempts(search, errors)
    except Exception as error:
        logger.exception("prospecting: search %s failed", search.pk)
        errors.append(f"unexpected {type(error).__name__}")
    search.status = SearchStatus.DONE if search.candidates.exists() else SearchStatus.FAILED
    search.detail = "; ".join(dict.fromkeys(errors))[:255]
    search.finished_at = timezone.now()
    search.save(update_fields=["status", "detail", "finished_at", "modified_at"])
    return search


def _attempts(search: PeopleSearch, errors: list[str]) -> None:
    provider, tester = providers.search_provider(), providers.address_tester()
    for _ in range(prospecting_settings.PROSPECTING_MAX_ATTEMPTS):
        if spent_today(search.channel_idx) >= prospecting_settings.PROSPECTING_DAILY_BUDGET:
            errors.append("daily budget used up")
            break
        search.attempts += 1
        search.save(update_fields=["attempts", "modified_at"])
        try:
            answer = provider.find_people(
                company_name=search.company_name, domain=search.domain, channel_idx=search.channel_idx
            )
        except ProviderError as error:
            errors.append(str(error))
            continue
        merge(search, answer, tester)
        if _complete(search):
            break


def _complete(search: PeopleSearch) -> bool:
    checks = CandidateCheck.objects.filter(candidate__search=search)
    return checks.exists() and not checks.filter(result=CheckResult.NO_INFO).exists()


def merge(search: PeopleSearch, answer: SearchAnswer, tester) -> None:
    """One provider answer: the pattern (once), new people as candidates, and every open check tried again."""
    if not search.mail_pattern and (found := pattern_service.infer(answer.addresses, search.domain)):
        search.mail_pattern, search.pattern_source = found.pattern, found.source[:500]
        search.save(update_fields=["mail_pattern", "pattern_source", "modified_at"])
    for person in answer.people:
        candidate = _candidate(search, person)
        if candidate is not None:
            _check(candidate, person, search, tester)


def _candidate(search: PeopleSearch, person: FoundPerson) -> Candidate | None:
    """The candidate of this search with the person's name, a new one while under the limit, else None."""
    key = (pattern_service.fold(person.first_name), pattern_service.fold(person.last_name))
    for candidate in search.candidates.all():
        if (pattern_service.fold(candidate.first_name), pattern_service.fold(candidate.last_name)) == key:
            return candidate
    if search.candidates.count() >= prospecting_settings.PROSPECTING_MAX_CANDIDATES:
        return None
    candidate = Candidate.objects.create(
        search=search,
        channel_idx=search.channel_idx,
        subject_ref=search.subject_ref,
        first_name=person.first_name,
        last_name=person.last_name,
        job_title=person.job_title,
        profile_url=person.profile_url,
        source=person.source,
        found_at=timezone.now(),
        status=CandidateStatus.PENDING,
    )
    CandidateCheck.objects.bulk_create([CandidateCheck(candidate=candidate, kind=kind) for kind in CheckKind.values])
    return candidate


def _check(candidate: Candidate, person: FoundPerson, search: PeopleSearch, tester) -> None:
    checks = {check.kind: check for check in candidate.checks.all()}
    for check in checks.values():
        if check.result == CheckResult.NO_INFO:
            check.attempts += 1
    role, employed = checks[CheckKind.ROLE], checks[CheckKind.EMPLOYED]
    if role.result == CheckResult.NO_INFO and person.role_fits and person.job_title:
        _ok(role, person.job_title, person.source)
        candidate.job_title = person.job_title
    elif person.job_title and not role.value:
        role.value = person.job_title[:255]
    if employed.result == CheckResult.NO_INFO and person.still_employed:
        _ok(employed, "works there", person.source)
    _addresses(candidate, person, search, tester, checks[CheckKind.EMAIL], checks[CheckKind.MAILBOX])
    CandidateCheck.objects.bulk_update(checks.values(), ["result", "value", "source", "checked_at", "attempts"])
    candidate.save(update_fields=["job_title", "email", "email_options", "modified_at"])


def _addresses(candidate, person: FoundPerson, search: PeopleSearch, tester, email_check, mailbox_check) -> None:
    """Guesses from the pattern (or the published address), each tested once with a final verdict: a valid one is the
    candidate's address; a domain accepting everything leaves the mailbox check without information."""
    options = {option["email"]: option for option in candidate.email_options}
    if person.email and _well_formed(person.email):
        options.setdefault(person.email.lower(), {"email": person.email.lower(), "source": person.source})
    guessed = pattern_service.guesses(
        person.first_name, person.last_name, search.domain, search.mail_pattern,
        prospecting_settings.PROSPECTING_MAX_ADDRESSES,
    )  # fmt: skip
    pattern_note = f"pattern {search.mail_pattern} from {search.pattern_source}" if search.mail_pattern else ""
    for address in guessed:
        options.setdefault(address, {"email": address, "source": pattern_note or "common address patterns"})
    for address in leads_bridge.erased_emails(list(options)):  # an erased address is never tested nor offered
        options.pop(address, None)
    tester_source = getattr(tester, "source", type(tester).__name__)
    for option in options.values():
        if option.get("verdict") in FINAL_VERDICTS:
            continue
        verdict = tester.check(option["email"]) if _well_formed(option["email"]) else AddressVerdict.INVALID
        option.update(verdict=str(verdict), tested_at=timezone.now().isoformat(), tested_by=tester_source)
    candidate.email_options = list(options.values())
    valid = next((o for o in candidate.email_options if o.get("verdict") == AddressVerdict.VALID), None)
    candidate.email = (valid or (candidate.email_options[0] if candidate.email_options else {})).get("email", "")
    if email_check.result == CheckResult.NO_INFO and (person.email or search.mail_pattern or valid):
        _ok(email_check, candidate.email, (valid or options.get(candidate.email, {})).get("source", ""))
    if mailbox_check.result == CheckResult.NO_INFO:
        if valid:
            _ok(mailbox_check, valid["email"], tester_source)
        elif any(o.get("verdict") == AddressVerdict.CATCH_ALL for o in candidate.email_options):
            mailbox_check.value = "the domain accepts every address"


def _ok(check: CandidateCheck, value: str, source: str) -> None:
    check.result, check.value, check.source, check.checked_at = (
        CheckResult.OK,
        value[:255],
        source[:500],
        timezone.now(),
    )


def _well_formed(email: str) -> bool:
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def list_searches(channel_idx: str, subject_ref: str | None = None):
    rows = PeopleSearch.objects.filter(channel_idx=channel_idx)
    return rows.filter(subject_ref=subject_ref) if subject_ref else rows


def get_search(channel_idx: str, pk: int) -> PeopleSearch:
    return PeopleSearch.objects.get(channel_idx=channel_idx, pk=pk)
