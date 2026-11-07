# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sender routes (Q3): a route is a key defined in communicator (which mailbox sends, who is copied). Leads only stores
the key: on the company, on an import batch (the whole file) and as the channel default. Communicator resolves the key
at send time; no key anywhere = communicator's own default."""

import inspect

from django.db.models import QuerySet
from django_communicator.services.communicate_service import communicate

from django_leads.enums import ActivityKind
from django_leads.models import Channel, Company
from django_leads.services import activity_service


def effective_route(company: Company) -> str:
    """The company's own key, else the channel default, else blank (communicator's default)."""
    return company.sender_route or company.channel.default_sender_route


def communicate_kwargs(company: Company) -> dict:
    """`sender_route=<key>` for `communicate()` when this communicator accepts it and a key applies; else nothing, so
    leads works with a communicator that has no routes yet."""
    route = effective_route(company)
    if route and "sender_route" in inspect.signature(communicate).parameters:
        return {"sender_route": route}
    return {}


def set_default(channel: Channel, route: str) -> Channel:
    channel.default_sender_route = route
    channel.save(update_fields=["default_sender_route", "modified_at"])
    return channel


def bulk_set(channel: Channel, company_ids: list[int], route: str, *, actor: str) -> int:
    """Set (or clear, blank) the route of the chosen companies of the channel; returns how many changed."""
    companies = list(Company.objects.filter(channel=channel, pk__in=company_ids).exclude(sender_route=route))
    for company in companies:
        company.sender_route = route
        company.save(update_fields=["sender_route", "modified_at"])
        activity_service.record(company, ActivityKind.NOTE, f"sender route: {route or 'default'}", actor=actor)
    return len(companies)


def companies_on_route(channel: Channel, route: str) -> QuerySet[Company]:
    return Company.objects.filter(channel=channel, sender_route=route)


def update_settings(
    channel: Channel, *, default_sender_route: str | None, resume_days: dict[str, int] | None
) -> Channel:
    """The channel settings API: only the given fields change."""
    fields = ["modified_at"]
    if default_sender_route is not None:
        channel.default_sender_route = default_sender_route
        fields.append("default_sender_route")
    if resume_days is not None:
        channel.resume_days = dict(resume_days)
        fields.append("resume_days")
    channel.save(update_fields=fields)
    return channel
