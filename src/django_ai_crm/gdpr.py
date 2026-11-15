# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""GDPR hooks of the AI CRM (discovered by django_leads): a proposal's summary names the recipient of a draft. Export
lists the proposals that mention the address; erasure scrubs them (an open one is expired — its arguments are gone)."""

from typing import Any

from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from django_ai_crm.enums import ProposalStatus
from django_ai_crm.models import Proposal

ERASED = "[erased]"


def _proposals(email: str) -> QuerySet[Proposal]:
    if not (address := email.strip().lower()):
        raise ValueError("an email address is required")
    fields = {f"{name}_text": Cast(name, TextField()) for name in ("arguments", "result")}
    query = Q(summary__icontains=address) | Q(arguments_text__icontains=address) | Q(result_text__icontains=address)
    return Proposal.objects.annotate(**fields).filter(query)


def gdpr_export(email: str) -> dict[str, Any]:
    rows = _proposals(email).values("id", "channel_idx", "tool", "arguments", "summary", "source", "status", "result",
                                    "created_at", "decided_at")  # fmt: skip
    return {"Proposal": list(rows)}


def gdpr_erase(email: str) -> dict[str, int]:
    ids = list(_proposals(email).values_list("pk", flat=True))
    rows = Proposal.objects.filter(pk__in=ids)
    rows.filter(status=ProposalStatus.PENDING).update(status=ProposalStatus.EXPIRED, decided_at=timezone.now())
    return {"proposals": rows.update(summary=ERASED, arguments={}, result={})}
