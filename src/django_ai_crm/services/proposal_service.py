# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The only writer of `Proposal`: irreversible actions waiting for the salesperson's yes or no."""

from datetime import timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from django_ai_crm import settings as module_settings
from django_ai_crm.enums import ProposalStatus
from django_ai_crm.models import Proposal
from django_ai_crm.services.caller import Caller, ToolError


def create(caller: Caller, tool: str, arguments: dict, summary: str) -> Proposal:
    return Proposal.objects.create(
        channel_idx=caller.channel_idx, user=caller.user, tool=tool, arguments=arguments, summary=summary[:255],
        source=caller.source,
    )  # fmt: skip


def _expire_old(caller: Caller) -> None:
    hours = module_settings.get("AI_CRM_PROPOSAL_TTL_HOURS")
    stale = _mine(caller).filter(status=ProposalStatus.PENDING, created_at__lt=timezone.now() - timedelta(hours=hours))
    stale.update(status=ProposalStatus.EXPIRED, decided_at=timezone.now())


def _mine(caller: Caller) -> QuerySet[Proposal]:
    return Proposal.objects.filter(user=caller.user, channel_idx=caller.channel_idx)


def pending(caller: Caller) -> QuerySet[Proposal]:
    """The caller's open proposals, newest first."""
    _expire_old(caller)
    return _mine(caller).filter(status=ProposalStatus.PENDING)


def latest(caller: Caller) -> Proposal | None:
    return pending(caller).first()


def take(caller: Caller, proposal_id: int) -> Proposal:
    """The caller's pending proposal, locked against a second confirm; `ToolError` otherwise."""
    _expire_old(caller)
    proposal = _mine(caller).select_for_update().filter(pk=proposal_id).first()
    if proposal is None:
        raise ToolError("not_found", f"Proposal {proposal_id} not found among your proposals.")
    if proposal.status != ProposalStatus.PENDING:
        raise ToolError("conflict", f"Proposal {proposal_id} is {proposal.status}.")
    return proposal


def _decide(proposal: Proposal, status: str, outcome: dict) -> None:
    proposal.status, proposal.result, proposal.decided_at = status, outcome, timezone.now()
    proposal.save(update_fields=["status", "result", "decided_at"])


def confirmed(proposal: Proposal, outcome: dict) -> None:
    _decide(proposal, ProposalStatus.CONFIRMED, outcome)


def fail(proposal: Proposal, message: str) -> None:
    Proposal.objects.filter(pk=proposal.pk).update(
        status=ProposalStatus.FAILED, result={"error": message}, decided_at=timezone.now()
    )


def reject(caller: Caller, proposal_id: int) -> Proposal:
    proposal = take(caller, proposal_id)
    _decide(proposal, ProposalStatus.REJECTED, {})
    return proposal


def dump(proposal: Proposal) -> dict[str, Any]:
    return {
        "id": proposal.pk,
        "tool": proposal.tool,
        "arguments": proposal.arguments,
        "summary": proposal.summary,
        "source": proposal.source,
        "status": proposal.status,
        "created_at": proposal.created_at.isoformat(),
    }
