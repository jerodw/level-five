"""Load harness configuration and workflow definitions.

The target repository's .harness/config.yaml uses a deliberately small
subset of YAML: `key: value` pairs and lists of `- item` lines. Parsing it
directly keeps the harness free of third-party dependencies.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import schema_validator

# The declaration of which keys the harness reads, beside the artifact
# schemas. It is a declaration and not a run-time check: nothing here
# validates a target's config file against it, and no unknown key is
# refused.
CONFIG_SCHEMA_NAME = "harness-config"


def find_target_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / ".harness" / "config.yaml").is_file():
            return candidate
    sys.exit("No .harness/config.yaml found here or above. Run l5-init first.")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_config(target_root: Path) -> dict:
    path = target_root / ".harness" / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"No .harness/config.yaml under {target_root}; run l5-init first."
        )
    config: dict = {}
    current_list: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and current_list:
            config[current_list].append(_unquote(line.strip()[2:].strip()))
        elif ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), _unquote(value.strip())
            if value:
                config[key] = value
                current_list = None
            else:
                config[key] = []
                current_list = key
    return config


def declared_config_keys(harness_root: Path | None = None) -> tuple[str, ...]:
    """The keys the harness reads, declared in schemas/harness-config.schema.json.

    The schema ships with the harness code, so it is resolved relative to
    this module's package exactly as the artifact schemas are, and read
    through schema_validator.load_schema so schemas/ keeps one reader.

    The declaration is not a run-time check. Nothing calls this while a run
    is executing, no target's config file is validated against it, and no
    unknown key is refused; what reads it is the coverage that asserts set
    equality against the keys the harness actually reads.

    A missing, unparseable or wrong-shaped schema raises ValueError naming
    the path, rather than degrading to an empty or partial tuple, which
    would silently make that coverage vacuous instead of red.
    """
    path = schema_validator.schemas_dir(harness_root) / f"{CONFIG_SCHEMA_NAME}.schema.json"
    try:
        schema = schema_validator.load_schema(CONFIG_SCHEMA_NAME, harness_root)
    except OSError as error:
        raise ValueError(f"{path} could not be read: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not parseable as JSON: {error}") from error
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: expected an object, found {type(schema).__name__}")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError(f"{path}: 'properties' must be a non-empty object of key declarations")
    for key in properties:
        if not isinstance(key, str) or not key:
            raise ValueError(f"{path}: every declared property must be a key name")
    return tuple(properties)


def load_workflow(harness_root: Path, name: str) -> dict:
    path = harness_root / "workflows" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_rules(harness_root: Path) -> dict:
    path = harness_root / "rules" / "execution-rules.json"
    return json.loads(path.read_text(encoding="utf-8"))
