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


def add_contact(candidate: Candidate, *, email: str, actor: str) -> str:
    """The contact reference leads answers (`leads.Contact:7`). Raises `LeadsUnavailable`, or the `ValueError` of
    leads for a refused person (unknown company, address already a contact)."""
    if not apps.is_installed("django_leads"):
        raise LeadsUnavailable("django_leads is not installed")
    from django_leads.services import candidate_service

    return candidate_service.accept_candidate(
        subject_ref=candidate.subject_ref,
        email=email,
        first_name=candidate.first_name,
        last_name=candidate.last_name,
        job_title=candidate.job_title,
        source_note=f"prospecting: {candidate.source}"[:255],
        actor=actor,
    )
