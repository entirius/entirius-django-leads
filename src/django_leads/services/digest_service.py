# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Q16: the daily digest — what the automation did and what it refused in the last day, one notification per channel.

Built from the timeline (Activity), so it never claims more than was recorded. Reasons of refusals are the gate's codes
(`do_not_contact`, `opted_out`, `email_invalid`, ...), never addresses. Nothing is sent when nothing happened."""

from collections import Counter
from datetime import datetime, timedelta

from django.utils import timezone

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel
from django_leads.services import alert_service, queue_service

BLOCK_PREFIX = "blocked: "


def build(channel: Channel, *, since: datetime | None = None) -> dict:
    since = since or timezone.now() - timedelta(hours=leads_settings.LEADS_DIGEST_HOURS)
    rows = Activity.objects.filter(company__channel=channel, created_at__gte=since)
    kinds = Counter(rows.values_list("kind", flat=True))
    blocked = Counter(
        message.removeprefix(BLOCK_PREFIX)
        for message in rows.filter(kind=ActivityKind.BLOCKED).values_list("message", flat=True)
    )
    bad_addresses = sum(
        1
        for data in rows.filter(kind=ActivityKind.VERIFICATION).values_list("data", flat=True)
        if data.get("to") == "invalid"
    )
    resumed = rows.filter(kind=ActivityKind.RESUME, message__startswith="resumed").count()
    queue = queue_service.build(channel)
    return {
        "channel": channel.idx,
        "since": since,
        "done": {
            "drafts": kinds[ActivityKind.DRAFT],
            "sent": kinds[ActivityKind.SENT],
            "resumed": resumed,
            "replies": kinds[ActivityKind.REPLY],
        },
        "refused": {
            "blocked": dict(blocked),
            "bad_addresses": bad_addresses,
            "opt_outs": kinds[ActivityKind.OPTOUT],
        },
        "waiting": queue["counts"],
    }


def is_empty(digest: dict) -> bool:
    return (
        not any(digest["done"].values())
        and not any((digest["refused"]["blocked"], digest["refused"]["bad_addresses"], digest["refused"]["opt_outs"]))
        and not any(digest["waiting"].values())
    )


def render(digest: dict) -> tuple[str, str]:
    done, refused, waiting = digest["done"], digest["refused"], digest["waiting"]
    title = f"Outbound digest: {done['sent']} sent, {done['drafts']} drafts, {waiting['reply']} replies to read"
    blocked = ", ".join(f"{reason} x{count}" for reason, count in sorted(refused["blocked"].items())) or "none"
    lines = [
        f"Done: {done['drafts']} drafts, {done['sent']} sent, {done['resumed']} resumed after 'not now', "
        f"{done['replies']} replies received.",
        f"Refused: blocked by the gate: {blocked}; bad addresses: {refused['bad_addresses']}; "
        f"do-not-contact entries: {refused['opt_outs']}.",
        f"Waiting for you: {waiting['reply']} replies, {waiting['drafts']} drafts to approve, {waiting['task']} tasks "
        f"today, {waiting['hot_company']} hot companies not contacted, {waiting['decision']} contacts to decide.",
    ]
    return title, "\n".join(lines)


def send_all(channel_idx: str | None = None) -> dict[str, int]:
    """Sent per channel idx: 1 = notification delivered to the notifications module, 0 = nothing to say or no module."""
    result = {}
    channels = Channel.objects.filter(idx=channel_idx) if channel_idx else Channel.objects.all()
    for channel in channels.order_by("idx"):
        digest = build(channel)
        if is_empty(digest):
            result[channel.idx] = 0
            continue
        title, body = render(digest)
        result[channel.idx] = int(alert_service.notify_channel(channel, severity="low", title=title, body=body))
    return result
