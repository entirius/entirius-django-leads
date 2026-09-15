# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The seam between leads and external lead sources (CSV now, a CRM such as Twenty later)."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from django_leads.models import Channel, Company


class ImportFailed(ValueError):
    """The whole source is rejected; the message is the error code."""


@dataclass(frozen=True)
class CandidateRow:
    """One company + contact candidate — the CSV columns of the import, all str, "" when absent."""

    company_name: str
    domain: str
    website: str
    company_type: str
    industry: str
    first_name: str
    last_name: str
    email: str
    job_title: str
    language: str
    legal_basis: str
    phone: str
    external_ref: str = ""


class Connector(Protocol):
    key: str

    def fetch_candidates(self, channel: Channel) -> Iterable[CandidateRow]: ...

    def push_status(self, company: Company) -> None: ...
