"""Canonical Task input definitions, JSON Schema conversion, and validation.

The GUI edits :class:`InputDefinition`-shaped dictionaries.  Relay stores the
compatible JSON Schema in ``TaskSpec.input_schema`` so CLI and Agent callers
retain a stable public contract.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

VALUE_TYPES = ("text", "number", "boolean", "choice")
CARDINALITIES = ("single", "list")


def parse_schema(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Task input schema is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Task input schema must be a JSON object.")
    return deepcopy(value)


def compile_definitions(definitions: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for raw in definitions:
        definition = normalize_definition(raw)
        name = definition["name"]
        if name in properties:
            raise ValueError(f"Input item {name!r} is duplicated.")
        item_type = {"text": "string", "number": "number", "boolean": "boolean", "choice": "string"}[
            definition["value_type"]
        ]
        item: dict[str, Any] = {"type": item_type}
        if definition["description"]:
            item["description"] = definition["description"]
        if definition["value_type"] == "choice":
            item["enum"] = definition["choices"]
        if definition["has_default"]:
            item["default"] = definition["default"]
        if definition["cardinality"] == "list":
            item = {"type": "array", "items": item}
            if definition["has_default"]:
                item["default"] = definition["default"]
        properties[name] = item
        if definition["required"]:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def normalize_definition(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("An input item name is required.")
    value_type = str(raw.get("value_type") or "text")
    cardinality = str(raw.get("cardinality") or "single")
    if value_type not in VALUE_TYPES or cardinality not in CARDINALITIES:
        raise ValueError(f"Input item {name!r} has an unsupported type or shape.")
    choices = [str(value).strip() for value in raw.get("choices") or [] if str(value).strip()]
    if value_type == "choice" and not choices:
        raise ValueError(f"Input item {name!r} needs at least one allowed value.")
    default = raw.get("default")
    has_default = bool(raw.get("has_default", default is not None))
    out = {
        "name": name,
        "description": str(raw.get("description") or "").strip(),
        "value_type": value_type,
        "cardinality": cardinality,
        "required": bool(raw.get("required")),
        "choices": choices,
        "has_default": has_default,
        "default": default,
    }
    if has_default:
        _validate_value(name, default, compile_definitions([{**out, "has_default": False}])["properties"][name])
    return out


def extract_definitions(value: str | dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Import only flat schemas the GUI can faithfully edit; otherwise None."""
    schema = parse_schema(value)
    if not schema:
        return []
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return None
    required = {str(name) for name in schema.get("required") or []}
    definitions: list[dict[str, Any]] = []
    for name, item in schema["properties"].items():
        if not isinstance(item, dict):
            return None
        cardinality = "single"
        if item.get("type") == "array":
            cardinality = "list"
            item = item.get("items")
            if not isinstance(item, dict):
                return None
        schema_type = item.get("type", "string")
        value_type = {"string": "text", "number": "number", "boolean": "boolean"}.get(schema_type)
        choices = item.get("enum") or []
        if choices:
            if schema_type != "string" or not all(isinstance(choice, str) for choice in choices):
                return None
            value_type = "choice"
        if value_type is None or any(key in item for key in ("oneOf", "anyOf", "allOf", "$ref")):
            return None
        default = schema["properties"][name].get("default")
        definitions.append(
            {
                "name": str(name),
                "description": str(item.get("description") or ""),
                "value_type": value_type,
                "cardinality": cardinality,
                "required": str(name) in required,
                "choices": list(choices),
                "has_default": default is not None,
                "default": default,
            }
        )
    return definitions


def apply_defaults(inputs: dict[str, Any], schema_value: str | dict[str, Any] | None) -> dict[str, Any]:
    schema = parse_schema(schema_value)
    values = dict(inputs or {})
    for name, item in (schema.get("properties") or {}).items():
        if name not in values and isinstance(item, dict) and "default" in item:
            values[name] = deepcopy(item["default"])
    return values


def validate_inputs(inputs: dict[str, Any], schema_value: str | dict[str, Any] | None) -> dict[str, Any]:
    schema = parse_schema(schema_value)
    if not schema:
        return dict(inputs or {})
    if not isinstance(inputs, dict):
        raise ValueError("Task inputs must be a JSON object.")
    values = apply_defaults(inputs, schema)
    required = [str(name) for name in schema.get("required") or []]
    missing = [name for name in required if name not in values]
    if missing:
        raise ValueError(f"Required Task inputs are missing: {', '.join(missing)}")
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unknown = [str(name) for name in values if name not in properties]
        if unknown:
            raise ValueError(f"Unknown Task inputs: {', '.join(unknown)}")
    for name, value in values.items():
        item = properties.get(name)
        if isinstance(item, dict):
            _validate_value(str(name), value, item)
    return values


def _validate_value(name: str, value: Any, item: dict[str, Any]) -> None:
    if item.get("type") == "array":
        if not isinstance(value, list):
            raise ValueError(f"Task input {name!r} must be array.")
        for entry in value:
            _validate_value(name, entry, item.get("items") or {})
        return
    checks = {
        "string": lambda current: isinstance(current, str),
        "number": lambda current: isinstance(current, (int, float)) and not isinstance(current, bool),
        "integer": lambda current: isinstance(current, int) and not isinstance(current, bool),
        "boolean": lambda current: isinstance(current, bool),
        "object": lambda current: isinstance(current, dict),
        "null": lambda current: current is None,
    }
    expected = item.get("type")
    if expected in checks and not checks[expected](value):
        raise ValueError(f"Task input {name!r} must be {expected}.")
    choices = item.get("enum")
    if isinstance(choices, list) and value not in choices:
        raise ValueError(f"Task input {name!r} must be one of: {', '.join(map(str, choices))}.")
