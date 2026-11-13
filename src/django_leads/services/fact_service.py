# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The one accessor of company facts: built-in fields plus `attr.<key>` (and `contact.attr.<key>`).

Rule conditions, scoring and the template context read a company only through `facts`; `fields` describes the same
names (kind, label, options) for validation at save and for the CMS. An attribute without a value is `None`.
"""

from typing import Any

from django_leads.enums import UNKNOWN_LEAD_TYPE, AttributeEntity, AttributeKind, ScoreSegment
from django_leads.models import AttributeDefinition, Channel, Company, Contact, Stage
from django_leads.services import attribute_service, lead_type_service

ATTR_PREFIX = "attr."
CONTACT_ATTR_PREFIX = "contact.attr."
BUILTIN_KINDS: dict[str, str] = {
    "lead_type": AttributeKind.SELECT,
    "industry": AttributeKind.TEXT,
    "platform": AttributeKind.TEXT,
    "domain": AttributeKind.TEXT,
    "origin": AttributeKind.TEXT,
    "stage": AttributeKind.SELECT,
    "hooks_count": AttributeKind.NUMBER,
    "emails_count": AttributeKind.NUMBER,
    "rotation_count": AttributeKind.NUMBER,
    "score_points": AttributeKind.NUMBER,
    "score_segment": AttributeKind.SELECT,
}


def facts(company: Company, contact: Contact | None = None) -> dict[str, Any]:
    values = _builtins(company)
    values.update(_attributes(company, AttributeEntity.COMPANY, ATTR_PREFIX))
    if contact is not None:
        values.update(_attributes(contact, AttributeEntity.CONTACT, CONTACT_ATTR_PREFIX, company.channel_id))
    return values


def attributes_of(values: dict[str, Any], prefix: str) -> dict[str, Any]:
    """`{key: value}` of the facts under one prefix (`attr.` or `contact.attr.`)."""
    return {name.removeprefix(prefix): value for name, value in values.items() if name.startswith(prefix)}


def fields(channel: Channel) -> list[dict[str, Any]]:
    """Every fact a rule condition or a scoring rule may name: `{name, kind, label, options}`; company attributes
    follow the built-ins in display order (contact attributes are not known when a rule runs)."""
    options = _builtin_options(channel)
    builtins = [
        _field(name, kind, name.replace("_", " ").capitalize(), options.get(name, []))
        for name, kind in BUILTIN_KINDS.items()
    ]
    definitions = attribute_service.active_definitions(channel, AttributeEntity.COMPANY).values()
    return [*builtins, *(_attribute_field(row) for row in definitions)]


def field_map(channel: Channel) -> dict[str, dict[str, Any]]:
    return {field["name"]: field for field in fields(channel)}


def _builtins(company: Company) -> dict[str, Any]:
    emails = company.contacts.filter(anonymised_at__isnull=True).exclude(email="")
    return {
        "lead_type": company.lead_type,
        "industry": company.industry,
        "platform": company.platform,
        "domain": company.domain,
        "origin": company.origin,
        "stage": company.stage.key,
        "hooks_count": len(company.hooks or []),
        "emails_count": emails.count(),
        "rotation_count": company.rotation_count,
        "score_points": company.score_points,
        "score_segment": company.score_segment,
    }


def _attributes(row: Company | Contact, entity: str, prefix: str, channel_id: int | None = None) -> dict[str, Any]:
    keys = AttributeDefinition.objects.filter(
        channel_id=channel_id or row.channel_id, entity=entity, is_active=True
    ).values_list("key", flat=True)
    return {f"{prefix}{key}": row.attributes.get(key) for key in keys}


def _builtin_options(channel: Channel) -> dict[str, list[dict[str, str]]]:
    lead_types = lead_type_service.list_lead_types(channel).filter(is_active=True)
    stages = Stage.objects.filter(channel=channel).order_by("order", "id")
    return {
        "lead_type": [
            *({"value": row.code, "label": row.label} for row in lead_types),
            {"value": UNKNOWN_LEAD_TYPE, "label": "Unknown"},
        ],
        "stage": [{"value": stage.key, "label": stage.label} for stage in stages],
        "score_segment": [{"value": value, "label": label} for value, label in ScoreSegment.choices],
    }


def _field(name: str, kind: str, label: str, options: list[dict[str, str]]) -> dict[str, Any]:
    return {"name": name, "kind": str(kind), "label": label, "options": options}


def _attribute_field(definition: AttributeDefinition) -> dict[str, Any]:
    return _field(f"{ATTR_PREFIX}{definition.key}", definition.kind, definition.label, definition.options)
