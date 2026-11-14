# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Leads' side of communicator's do-not-contact store: the address of an opted-out contact and the domain of a
do-not-contact company are suppressed there, and the outreach gate asks `can_mail`. A channel without a communicator
channel of the same idx has nothing to suppress or ask (no mail can leave it)."""

import logging
from typing import Any

from django_communicator.enums import SuppressionKind, SuppressionScope, SuppressionSource
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.services import review_service
from django_communicator.services import suppression_service as store

from django_leads.models import Channel, Company

logger = logging.getLogger(__name__)

DO_NOT_CONTACT_REASON = "leads: do not contact"


def communicator_channel(channel: Channel) -> CommunicatorChannel | None:
    return CommunicatorChannel.objects.filter(idx=channel.idx).first()


def suppress_address(channel: Channel, email: str, *, reason: str) -> None:
    """Idempotent; an address already suppressed keeps its row."""
    if target := communicator_channel(channel):
        store.suppress_email(target, email, source=SuppressionSource.MANUAL, reason=reason)


def suppress_domain(channel: Channel, domain: str) -> None:
    target = communicator_channel(channel)
    if target is None:
        return
    try:
        store.create_suppression(target, kind=SuppressionKind.DOMAIN, value=domain, reason=DO_NOT_CONTACT_REASON)
    except store.DuplicateSuppressionError:
        return
    except ValueError:
        logger.warning("leads: domain of a do-not-contact company cannot be suppressed in channel %s", channel.idx)


def release_domain(channel: Channel, domain: str) -> None:
    """Removes only the domain row leads wrote; a suppression added in communicator by hand stays, and a global row
    stays while a do-not-contact company of another channel has the domain."""
    target = communicator_channel(channel)
    if target is None:
        return
    rows = store.list_suppressions(target, {"value": domain}).filter(
        kind=SuppressionKind.DOMAIN, reason=DO_NOT_CONTACT_REASON
    )
    if _held_elsewhere(channel, domain):
        rows = rows.exclude(channel=None)
    for row in rows:
        store.delete_suppression(target, row.pk)


def _held_elsewhere(channel: Channel, domain: str) -> bool:
    return Company.objects.filter(domain=domain, do_not_contact=True).exclude(channel=channel).exists()


def refusal(channel: Channel, email: str) -> str | None:
    """Communicator's `can_mail` code (`suppressed`, `address_invalid`) or None."""
    target = communicator_channel(channel)
    return store.can_mail(target, email) if target else None


def refusals(channel: Channel, emails: list[str]) -> dict[str, str | None]:
    """`refusal` of every address, keyed by the address as given — three queries for any number."""
    target = communicator_channel(channel)
    return store.can_mail_many(target, emails) if target else dict.fromkeys(emails)


def withdraw_drafts(channel: Channel, email: str, *, reason: str) -> None:
    """Drafts to `email` waiting for review in the channel are rejected with `reason` (nobody may send them)."""
    target = communicator_channel(channel)
    if target and email:
        review_service.withdraw(target, email, reason=reason)


def explain(channel: Channel, email: str) -> dict[str, Any] | None:
    target = communicator_channel(channel)
    return store.explain(target, email) if target and email else None


def install_wide() -> bool:
    """Whether a suppression holds in every channel (`COMMUNICATOR_SUPPRESSION_SCOPE = "install"`)."""
    return store.scope() == SuppressionScope.INSTALL
