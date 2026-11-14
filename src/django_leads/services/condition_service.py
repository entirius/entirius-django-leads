# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Conditions `{field, op, value}` on `fact_service` facts — shared by rule conditions and scoring.

`check` runs at save: the field must be a known fact of the channel, the op one its kind allows, and the value is
normalised like an attribute value of that kind (`in` takes a list, `empty` a bool — true = must be empty).
`holds` runs at evaluation; a fact without a value satisfies only `ne` and `empty`.
"""

from decimal import Decimal
from typing import Any

from django_leads.enums import AttributeKind, ConditionOp
from django_leads.models import Channel
from django_leads.services import attribute_service, fact_service

_TEXT_OPS = frozenset({ConditionOp.EQ, ConditionOp.NE, ConditionOp.IN, ConditionOp.CONTAINS, ConditionOp.EMPTY})
OPS_BY_KIND: dict[str, frozenset[str]] = {
    AttributeKind.TEXT: _TEXT_OPS,
    AttributeKind.URL: _TEXT_OPS,
    AttributeKind.NUMBER: frozenset(
        {ConditionOp.EQ, ConditionOp.NE, ConditionOp.IN, ConditionOp.GTE, ConditionOp.LTE, ConditionOp.EMPTY}
    ),
    AttributeKind.DATE: frozenset(
        {ConditionOp.EQ, ConditionOp.NE, ConditionOp.GTE, ConditionOp.LTE, ConditionOp.EMPTY}
    ),
    AttributeKind.BOOL: frozenset({ConditionOp.EQ, ConditionOp.NE, ConditionOp.EMPTY}),
    AttributeKind.SELECT: frozenset({ConditionOp.EQ, ConditionOp.NE, ConditionOp.IN, ConditionOp.EMPTY}),
    AttributeKind.MULTISELECT: frozenset({ConditionOp.CONTAINS, ConditionOp.EMPTY}),
}
EMPTY_VALUES = (None, "", [])


def check_all(channel: Channel, conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every condition checked against the channel's facts (see `check`)."""
    fields = fact_service.field_map(channel) if conditions else {}
    return [check(condition, fields) for condition in conditions]


def first_failing(
    conditions: list[dict[str, Any]], facts: dict[str, Any], fields: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """The first condition that does not hold; a field no longer known reads as an empty text fact."""
    for condition in conditions:
        kind = fields.get(condition["field"], {}).get("kind", AttributeKind.TEXT)
        if not holds(condition, facts, kind):
            return condition
    return None


def check(condition: dict[str, Any], fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The condition with its value normalised; ValueError naming the field otherwise."""
    name, op = condition.get("field"), condition.get("op")
    field = fields.get(name)
    if field is None:
        raise ValueError(f"unknown field {name!r}")
    if op not in OPS_BY_KIND[field["kind"]]:
        raise ValueError(f"op {op!r} does not apply to {name} ({field['kind']})")
    try:
        value = _check_value(field, op, condition.get("value"))
    except ValueError as error:
        raise ValueError(f"{name}: {error}") from None
    return {**condition, "value": value}


def holds(condition: dict[str, Any], facts: dict[str, Any], kind: str) -> bool:
    actual, op, expected = facts.get(condition["field"]), condition["op"], condition["value"]
    if op == ConditionOp.EMPTY:
        return (actual in EMPTY_VALUES) == expected
    if actual in EMPTY_VALUES:
        return op == ConditionOp.NE
    try:
        return _compare(op, _comparable(kind, actual), _expected(kind, op, expected))
    except (ArithmeticError, TypeError):  # a value stored for another kind never matches, never fails the run
        return False


def _check_value(field: dict[str, Any], op: str, value: Any) -> Any:
    if op == ConditionOp.EMPTY:
        cleaned = attribute_service.clean_value(AttributeKind.BOOL, [], value)
        return True if cleaned is None else cleaned
    if op == ConditionOp.IN:
        if not isinstance(value, list) or not value:
            raise ValueError("in takes a non-empty list")
        return [_one(field, field["kind"], item) for item in value]
    kind = AttributeKind.SELECT if field["kind"] == AttributeKind.MULTISELECT else field["kind"]
    return _one(field, kind, value)


def _one(field: dict[str, Any], kind: str, value: Any) -> Any:
    cleaned = attribute_service.clean_value(kind, field["options"], value)
    if cleaned is None:
        raise ValueError("a value is required")
    return cleaned


def _comparable(kind: str, value: Any) -> Any:
    if kind == AttributeKind.NUMBER:
        return Decimal(str(value))
    if kind in (AttributeKind.TEXT, AttributeKind.URL):
        return str(value).casefold()
    return value


def _expected(kind: str, op: str, value: Any) -> Any:
    if op == ConditionOp.IN:
        return [_comparable(kind, item) for item in value]
    return _comparable(kind, value)


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == ConditionOp.EQ:
        return actual == expected
    if op == ConditionOp.NE:
        return actual != expected
    if op == ConditionOp.IN:
        return actual in expected
    if op == ConditionOp.CONTAINS:
        return expected in actual
    if op == ConditionOp.GTE:
        return actual >= expected
    return actual <= expected  # lte
