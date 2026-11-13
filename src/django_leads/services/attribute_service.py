# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Custom attributes: definitions per channel and entity, and the only validation of values.

Values are stored normalised in the entity's `attributes` JSON: number → decimal string, date → ISO, bool → bool,
select → a listed value, multiselect → a list of listed values, url → http(s), text → at most 2 000 characters (never
interpreted as HTML). A patch merges: a key set to `null` is removed.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Model, QuerySet

from django_leads.enums import AttributeEntity, AttributeKind
from django_leads.models import AttributeDefinition, Channel, Company, Contact, Deal, ScoringProfile, StageRule

TEXT_MAX_LENGTH = 2000
# A decimal string never longer than this: `1e999999999` would otherwise expand to a billion digits.
NUMBER_MAX_DIGITS = 30
SELECT_KINDS = frozenset({AttributeKind.SELECT, AttributeKind.MULTISELECT})
CREATE_FIELDS = frozenset(
    {"entity", "key", "label", "kind", "options", "required", "order", "show_in_list", "is_active"}
)
# Entity and key are what stored values hang on — fixed after create; `kind` only while no value is stored.
UPDATE_FIELDS = frozenset({"label", "kind", "options", "required", "order", "show_in_list", "is_active"})
ENTITY_MODELS: dict[str, type[Model]] = {
    AttributeEntity.COMPANY: Company,
    AttributeEntity.CONTACT: Contact,
    AttributeEntity.DEAL: Deal,
}
TRUE_WORDS, FALSE_WORDS = frozenset({"true", "1", "yes"}), frozenset({"false", "0", "no"})
_validate_url = URLValidator(schemes=["http", "https"])


class DefinitionExists(Exception):
    """The channel already defines this key for the entity."""


class DefinitionInUse(Exception):
    """Stored values hang on the definition: its kind is fixed and it cannot be deleted."""


class InvalidAttribute(ValueError):
    """A value (or a key) the channel's definitions refuse; `key` names the attribute."""

    def __init__(self, key: str, message: str) -> None:
        self.key = key
        super().__init__(f"{key}: {message}")


def list_definitions(channel: Channel, entity: str = "") -> QuerySet[AttributeDefinition]:
    definitions = AttributeDefinition.objects.filter(channel=channel).order_by("entity", "order", "key")
    return definitions.filter(entity=entity) if entity else definitions


def active_definitions(channel: Channel, entity: str) -> dict[str, AttributeDefinition]:
    return {row.key: row for row in list_definitions(channel, entity).filter(is_active=True)}


def create_definition(channel: Channel, fields: dict[str, Any]) -> AttributeDefinition:
    """Raises ValueError (field not editable, key shadows a built-in fact, bad options) and `DefinitionExists`."""
    from django_leads.services import fact_service

    _check_fields(fields, CREATE_FIELDS)
    if fields.get("key") in fact_service.BUILTIN_KINDS:
        raise ValueError(f"key {fields['key']!r} is a built-in field")
    definition = AttributeDefinition(channel=channel, **fields)
    check_options(definition.kind, definition.options)
    try:
        with transaction.atomic():
            definition.save()
    except IntegrityError:
        raise DefinitionExists(f"attribute {definition.entity}.{definition.key} already exists") from None
    return definition


def update_definition(definition: AttributeDefinition, updates: dict[str, Any]) -> AttributeDefinition:
    _check_fields(updates, UPDATE_FIELDS)
    if updates.get("kind", definition.kind) != definition.kind:
        _check_unused(definition, "its kind is fixed")
    for field, value in updates.items():
        setattr(definition, field, value)
    check_options(definition.kind, definition.options)
    definition.save(update_fields=[*updates, "modified_at"])
    return definition


def delete_definition(definition: AttributeDefinition) -> None:
    _check_unused(definition, "deactivate it instead")
    definition.delete()


def _check_unused(definition: AttributeDefinition, advice: str) -> None:
    """Stored values and conditions (rules, scoring profiles) hold values normalised for the current kind."""
    if has_values(definition):
        raise DefinitionInUse(f"attribute {definition.key} has stored values — {advice}")
    if is_referenced(definition):
        raise DefinitionInUse(f"attribute {definition.key} is used by a rule or scoring profile — {advice}")


def is_referenced(definition: AttributeDefinition) -> bool:
    """Whether a rule condition, scoring rule or disqualifier of the channel names `attr.<key>`."""
    if definition.entity != AttributeEntity.COMPANY:  # conditions read company facts only
        return False
    name = f"attr.{definition.key}"
    rules = StageRule.objects.filter(channel_id=definition.channel_id).values_list("conditions", flat=True)
    profiles = ScoringProfile.objects.filter(channel_id=definition.channel_id).values_list("rules", "disqualifiers")
    lists = [*rules, *(items for pair in profiles for items in pair)]
    return any(condition.get("field") == name for conditions in lists for condition in conditions)


def has_values(definition: AttributeDefinition) -> bool:
    model = ENTITY_MODELS[definition.entity]
    scope = (
        {"company__channel_id": definition.channel_id} if model is Contact else {"channel_id": definition.channel_id}
    )
    return model.objects.filter(attributes__has_key=definition.key, **scope).exists()


def check_options(kind: str, options: list[dict[str, str]]) -> None:
    """Select kinds need at least one option and unique values; the other kinds take none."""
    values = [option["value"] for option in options]
    if kind in SELECT_KINDS and not values:
        raise ValueError(f"options are required for {kind}")
    if kind not in SELECT_KINDS and values:
        raise ValueError(f"options are only allowed for {sorted(SELECT_KINDS)}")
    if len(set(values)) != len(values):
        raise ValueError("option values must be unique")


def clean(channel: Channel, entity: str, values: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
    """Normalised values; `None` (or blank) means "remove the key". Unknown or inactive keys raise
    `InvalidAttribute`; a required key may not be removed, and on `create` every required key must be given."""
    definitions = active_definitions(channel, entity)
    cleaned = {key: _clean_one(definitions, key, value) for key, value in values.items()}
    missing = [key for key, row in definitions.items() if row.required and cleaned.get(key, ...) is None]
    if create:
        missing += [key for key, row in definitions.items() if row.required and key not in cleaned]
    if missing:
        raise InvalidAttribute(sorted(missing)[0], "is required")
    return cleaned


def apply(channel: Channel, entity: str, current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """`current` with the cleaned patch merged in (null removes a key)."""
    merged = {**current, **clean(channel, entity, patch)}
    return {key: value for key, value in merged.items() if value is not None}


def apply_locked(
    instance: Company | Contact | Deal, channel: Channel, entity: str, patch: dict[str, Any]
) -> dict[str, Any]:
    """`apply` on the row's stored JSON read under a lock (caller's transaction), set on the instance: two concurrent
    patches of different keys both land."""
    rows = type(instance).objects.select_for_update().filter(pk=instance.pk)
    instance.attributes = apply(channel, entity, rows.values_list("attributes", flat=True).get(), patch)
    return instance.attributes


def initial(channel: Channel, entity: str, values: dict[str, Any]) -> dict[str, Any]:
    """The attributes of a new row: cleaned, required keys enforced, blanks dropped."""
    return {key: value for key, value in clean(channel, entity, values, create=True).items() if value is not None}


def fill_missing(instance: Company | Contact, values: dict[str, Any] | None) -> list[str]:
    """Import/form matching rule: only keys the row does not hold yet are set; `["attributes"]` when any was."""
    added = {key: value for key, value in (values or {}).items() if key not in instance.attributes}
    if not added:
        return []
    instance.attributes = {**instance.attributes, **added}
    return ["attributes"]


def _clean_one(definitions: dict[str, AttributeDefinition], key: str, value: Any) -> Any:
    definition = definitions.get(key)
    if definition is None:
        raise InvalidAttribute(key, "not an active attribute of this channel")
    try:
        return clean_value(definition.kind, definition.options, value)
    except ValueError as error:
        raise InvalidAttribute(key, str(error)) from None


def clean_value(kind: str, options: list[dict[str, str]], value: Any) -> Any:
    """One value of the kind, normalised; `None` / blank → `None`. Raises ValueError on a value the kind refuses."""
    if value is None or value == "" or value == []:
        return None
    if kind in SELECT_KINDS:
        return _CLEANERS[kind](value, {option["value"] for option in options})
    return _CLEANERS[kind](value)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if len(value) > TEXT_MAX_LENGTH:
        raise ValueError(f"at most {TEXT_MAX_LENGTH} characters")
    return value


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise ValueError("must be a number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("must be a number") from None
    if not number.is_finite():
        raise ValueError("must be a finite number")
    if max(number.adjusted() + 1, len(number.as_tuple().digits)) > NUMBER_MAX_DIGITS:
        raise ValueError(f"at most {NUMBER_MAX_DIGITS} digits")
    return format(number, "f")


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word not in TRUE_WORDS | FALSE_WORDS:
        raise ValueError("must be true or false")
    return word in TRUE_WORDS


def _date(value: Any) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError("must be an ISO date (YYYY-MM-DD)") from None


def _url(value: Any) -> str:
    text = _text(value).strip()
    try:
        _validate_url(text)
    except DjangoValidationError:
        raise ValueError("must be an http or https URL") from None
    return text


def _select(value: Any, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"must be one of {sorted(allowed)}")
    return value


def _multiselect(value: Any, allowed: set[str]) -> list[str] | None:
    """A list, or a comma-separated string (a CSV cell); duplicates collapse, order kept."""
    items = [item.strip() for item in value.split(",")] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ValueError("must be a list")
    return list(dict.fromkeys(_select(item, allowed) for item in items if item != "")) or None


_CLEANERS: dict[str, Callable[..., Any]] = {
    AttributeKind.TEXT: _text,
    AttributeKind.NUMBER: _number,
    AttributeKind.BOOL: _bool,
    AttributeKind.DATE: _date,
    AttributeKind.URL: _url,
    AttributeKind.SELECT: _select,
    AttributeKind.MULTISELECT: _multiselect,
}


def _check_fields(fields: dict[str, Any], allowed: frozenset[str]) -> None:
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"fields not editable: {sorted(invalid)}")
