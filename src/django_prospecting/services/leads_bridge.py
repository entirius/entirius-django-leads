# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The only place that talks to leads (an app of the same package, optional in INSTALLED_APPS, imported lazily): an
accepted candidate becomes a contact through the public service of leads. Leads never learns how the person was found
— only the person and the source."""

from django.apps import apps

from django_prospecting.models import Candidate


class LeadsUnavailable(Exception):
    """django_leads is not in INSTALLED_APPS."""


def _require_leads() -> None:
    if not apps.is_installed("django_leads"):
        raise LeadsUnavailable("django_leads is not installed")


def channel_exists(channel_idx: str) -> bool:
    """True when leads knows the channel; without leads there is nothing to check against."""
    if not apps.is_installed("django_leads"):
        return True
    from django_leads.models import Channel

    return Channel.objects.filter(idx=channel_idx).exists()


def company_for_search(channel_idx: str, subject_ref: str) -> dict[str, str]:
    """Name and domain of the leads company in this channel — what a search is run for, never taken from a request.
    Raises `LeadsUnavailable`, or `ValueError` for a company that is not in the channel or has no domain."""
    _require_leads()
    from django_leads.utils.refs import company_from_ref

    company = company_from_ref(subject_ref)
    if company is None or company.channel.idx != channel_idx:
        raise ValueError(f"no company {subject_ref} in this channel")
    if not company.domain:
        raise ValueError("the company has no domain to search")
    return {"company_name": company.name or company.domain, "domain": company.domain}


def erased_emails(emails: list[str]) -> set[str]:
    """The addresses among `emails` leads remembers as erased or anonymised; none without leads."""
    if not apps.is_installed("django_leads"):
        return set()
    from django_leads.services import erased_address_service

    return erased_address_service.erased_emails(emails)


def add_contact(candidate: Candidate, *, email: str, actor: str) -> str:
    """The contact reference leads answers (`leads.Contact:7`). Raises `LeadsUnavailable`, or the `ValueError` of
    leads for a refused person (unknown company, address already a contact)."""
    _require_leads()
    from django_leads.services import candidate_service

    return candidate_service.accept_candidate(
        subject_ref=candidate.subject_ref,
        channel_idx=candidate.channel_idx,
        email=email,
        first_name=candidate.first_name,
        last_name=candidate.last_name,
        job_title=candidate.job_title,
        source_note=f"prospecting: {candidate.source}"[:255],
        actor=actor,
    )
