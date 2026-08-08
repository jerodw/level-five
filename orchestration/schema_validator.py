"""Validate structured artifacts against the schemas in schemas/.

schemas/ is the single source of truth for every artifact shape the harness
routes on: the same file is injected into the prompt that asks an agent to
produce the artifact and read here to check what the agent produced.

This is a deliberately small subset of JSON Schema — type, required,
properties, items, enum — because the harness depends on the standard
library only. A schema keyword outside that subset raises rather than being
silently ignored, so a schema can never claim a constraint the validator
does not enforce.

additionalProperties is not enforced by design: the failure mode that
matters is a missing or mistyped field a later stage routes on, and an
extra harmless key should not end a run.
"""
from __future__ import annotations

import json
from pathlib import Path

# Schemas ship with the harness code, so they are resolved relative to this
# module rather than to a caller-supplied root.
HARNESS_ROOT = Path(__file__).resolve().parents[1]

# The declared inventory of shipped schemas, beside the schemas it names.
MANIFEST_NAME = "manifest.json"
MANIFEST_KEY = "schemas"

SUPPORTED_KEYWORDS = frozenset({"type", "required", "properties", "items", "enum"})

# Annotations carry no constraint, so ignoring them ignores nothing.
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "title", "description"})

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def schemas_dir(harness_root: Path | None = None) -> Path:
    return (harness_root or HARNESS_ROOT) / "schemas"


def load_schema(name: str, harness_root: Path | None = None) -> dict:
    """Read schemas/<name>.schema.json. Missing or malformed schemas raise."""
    path = schemas_dir(harness_root) / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def shipped_schemas(harness_root: Path | None = None) -> tuple[str, ...]:
    """The declared inventory of schema names, read from schemas/manifest.json.

    The manifest is the single source of truth for what the harness ships;
    the tests that assert set equality against the directory are the check,
    not the declaration. A missing or malformed manifest raises rather than
    degrading to an empty or partial inventory, which would silently widen
    what an inventory test accepts.
    """
    path = schemas_dir(harness_root) / MANIFEST_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{path} could not be read: {error}") from error
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not parseable as JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError(f"{path}: expected an object, found {type(manifest).__name__}")
    names = manifest.get(MANIFEST_KEY)
    if not isinstance(names, list) or not names:
        raise ValueError(f"{path}: {MANIFEST_KEY!r} must be a non-empty array of names")
    for name in names:
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path}: every entry of {MANIFEST_KEY!r} must be a name")
    return tuple(names)


def unsupported_keywords(schema: dict) -> list[str]:
    """Every keyword anywhere in the schema that this validator cannot honor."""
    found: list[str] = []

    def walk(node: dict, path: str) -> None:
        for keyword, value in node.items():
            if keyword in ANNOTATION_KEYWORDS:
                continue
            if keyword not in SUPPORTED_KEYWORDS:
                found.append(f"{path}{keyword}")
                continue
            if keyword == "properties":
                for prop, subschema in value.items():
                    walk(subschema, f"{path}properties.{prop}.")
            elif keyword == "items":
                walk(value, f"{path}items.")

    walk(schema, "")
    return found


def validate(instance: object, schema: dict) -> list[str]:
    """Check instance against schema; return human-readable error strings.

    Each error names the failing JSON path, what the schema expected, and
    what was found, so an escalation message is actionable on its own.
    """
    unsupported = unsupported_keywords(schema)
    if unsupported:
        raise ValueError(
            "schema uses keyword(s) this validator does not support: "
            + ", ".join(unsupported)
        )
    errors: list[str] = []
    _validate(instance, schema, "$", errors)
    return errors


def _describe(value: object) -> str:
    return f"{_type_name(value)} ({json.dumps(value, default=str)})"


def _type_name(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    for name, py_type in _TYPES.items():
        if name in ("integer", "number", "boolean"):
            continue
        if isinstance(value, py_type):
            return name
    return type(value).__name__


def _matches_type(value: object, expected: str) -> bool:
    py_type = _TYPES.get(expected)
    if py_type is None:
        raise ValueError(f"schema declares unknown type {expected!r}")
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, py_type)


def _validate(value: object, schema: dict, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        errors.append(
            f"{path}: expected type {expected_type}, found {_describe(value)}"
        )
        return

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(json.dumps(option) for option in schema["enum"])
        errors.append(f"{path}: expected one of [{allowed}], found {_describe(value)}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(
                    f"{path}.{key}: expected a required property, found it missing"
                )
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                _validate(value[key], subschema, f"{path}.{key}", errors)

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]", errors)
