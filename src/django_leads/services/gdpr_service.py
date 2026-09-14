# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""GDPR art. 15 export and art. 17 erasure by email over every module with GDPR hooks."""

import json
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from django_leads.enums import ActivityKind
from django_leads.gdpr import registry
from django_leads.gdpr.hooks import contacts_of_email
from django_leads.models import Company
from django_leads.services import activity_service
from django_leads.utils.emails import normalize_email


def export(email: str) -> dict[str, Any]:
    """`{email, generated_at, modules: {app name: export}}`, JSON-ready (dates ISO). A blank address raises
    `ValueError` before any module runs."""
    contacts_of_email(email)
    modules = {name: hooks.gdpr_export(email) for name, hooks in registry.discover().items()}
    payload = {"email": normalize_email(email), "generated_at": timezone.now(), "modules": modules}
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def erase(email: str, *, actor: str) -> dict[str, dict[str, int]]:
    """One transaction: the request logged on every affected company (no address in the note), then every module
    erases — leads' `contact_anonymised` signals fire after the commit, when the other modules are done."""
    hooks_by_module = registry.discover()
    with transaction.atomic():
        for company in Company.objects.filter(contacts__in=contacts_of_email(email)).distinct():
            activity_service.record(company, ActivityKind.NOTE, "gdpr erase requested", actor=actor)
        return {name: hooks.gdpr_erase(email) for name, hooks in hooks_by_module.items()}
