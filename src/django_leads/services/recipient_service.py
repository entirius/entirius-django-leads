# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Who gets the message: the primary contact, or the toolbox pick validated against the candidates."""

import json

from django.conf import settings
from django_utils.toolbox import ToolboxClient, ToolboxError
from django_utils.toolbox.schemas import CompletionRequest, Message

from django_leads.enums import ActivityKind, ContactStrategy
from django_leads.models import Company, Contact, RecipientPickProfile, StageRule
from django_leads.services import activity_service
from django_leads.utils.prompts import render_prompt

PICK_KEY = "leads.pick_recipient"


class PickRejected(Exception):
    """The toolbox answer cannot be used; the message is the reason code."""


def candidates(company: Company) -> list[Contact]:
    contacts = company.contacts.exclude(email="").filter(opt_out_at__isnull=True, anonymised_at__isnull=True)
    return list(contacts.select_related("language").order_by("-is_primary", "pk"))


def pick_recipient(company: Company, rule: StageRule) -> tuple[Contact | None, dict]:
    options = candidates(company)
    if not options:
        return None, {}
    if rule.contact_strategy == ContactStrategy.PRIMARY or len(options) == 1:
        return options[0], {"reason": "primary"}
    try:
        contact, reason = _ask(company, options)
    except PickRejected as error:
        activity_service.record(company, ActivityKind.RULE, f"recipient pick rejected: {error}")
        return options[0], {"reason": "fallback"}
    data = {"contact_id": contact.pk, "reason": reason}
    activity_service.record(company, ActivityKind.RULE, "recipient picked", contact=contact, data=data)
    return contact, data


def _ask(company: Company, options: list[Contact]) -> tuple[Contact, str]:
    profile = RecipientPickProfile.objects.filter(channel=company.channel, key=PICK_KEY).first()
    if profile is None:
        raise PickRejected("no_profile")
    values = {
        "company_name": company.name,
        "hooks": json.dumps(company.hooks),
        "contacts_json": _contacts_json(options),
    }
    request = CompletionRequest(
        model=profile.model,
        messages=[Message(role="user", content=render_prompt(profile.prompt_text, values))],
        json_schema=profile.json_schema or None,
        tags=[PICK_KEY, f"channel:{company.channel.idx}"],
    )
    try:
        with ToolboxClient(settings.AI_TOOLBOX_CHANNEL) as client:
            parsed = client.complete(request).parsed or {}
    except ToolboxError as error:
        raise PickRejected(error.code or type(error).__name__) from None
    chosen = next((contact for contact in options if contact.pk == parsed.get("contact_id")), None)
    if chosen is None:
        raise PickRejected("unknown contact")
    return chosen, str(parsed.get("reason", ""))[:255]


def _contacts_json(options: list[Contact]) -> str:
    rows = [
        {
            "id": contact.pk,
            "email": contact.email,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "job_title": contact.job_title,
            "language": contact.language.iso2.lower() if contact.language else "",
        }
        for contact in options
    ]
    return json.dumps({"candidates": rows}, ensure_ascii=False)
