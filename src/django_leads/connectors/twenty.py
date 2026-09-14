# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Twenty CRM connector — interface only in v1. It will fill `Company.source=connector` and `external_ref`."""

from collections.abc import Iterable

from django_leads.connectors.base import CandidateRow
from django_leads.models import Channel, Company


class TwentyConnector:
    key = "twenty"

    def __init__(self, **kwargs) -> None:
        raise NotImplementedError(
            "Twenty CRM connector is an interface only in v1 — see the follow-up analysis after 1.0.0 "
            "(roadmap/00003 brief, Out of scope)"
        )

    def fetch_candidates(self, channel: Channel) -> Iterable[CandidateRow]:
        raise NotImplementedError

    def push_status(self, company: Company) -> None:
        raise NotImplementedError
