"""Load harness configuration and workflow definitions.

The target repository's .harness/config.yaml uses a deliberately small
subset of YAML: `key: value` pairs and lists of `- item` lines. Parsing it
directly keeps the harness free of third-party dependencies.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import schema_validator

# The declaration of which keys the harness reads, beside the artifact
# schemas. It is also what a target's config file is checked against at
# pre-flight: a key the schema does not declare refuses the run.
CONFIG_SCHEMA_NAME = "harness-config"


def undeclared_config_problems(
    config: dict, harness_root: Path | None = None
) -> list[str]:
    """One problem per key a loaded config carries that the schema does not declare.

    The declared set is the set of keys the harness reads, so a key outside
    it is a key nothing will ever act on — a retired name left behind after
    a rename, or a mistyping of a declared one. Either is refused rather
    than ignored, because ignoring it lets the run fall through to a
    default and quietly do something other than what the config asked for.

    Each problem names the offending key and lists the declared set, the
    shape the routing refusal takes: a bare "unknown key" would leave the
    developer to find the vocabulary themselves. Problems come back in the
    order the config carries the keys, and an empty list is the whole of
    "this config carries none".
    """
    declared = declared_config_keys(harness_root)
    listed = ", ".join(declared)
    return [
        f"'{key}' is not a key the harness reads; it reads: {listed}"
        for key in config
        if key not in declared
    ]


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

    Two things read it. The coverage asserts set equality against the keys
    the harness actually reads, and `undeclared_config_problems` checks a
    loaded config against it at pre-flight, so a key the schema does not
    declare refuses the run rather than being silently ignored.

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


#: A workflow declaration references configuration as `{{key}}`, and only as
#: a whole list entry. A general mechanism -- any declaration reaching any
#: config key -- was considered and rejected: one key needs this, and a
#: narrow token leaves every existing reader of a loaded workflow untouched.
_WORKFLOW_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def workflow_token_values(config: dict) -> dict[str, str | None]:
    """The configuration a workflow declaration may reference, by token name.

    Written as a literal read per key rather than a lookup by whatever name
    the definition happens to carry, so the keys this resolution reads are
    visible to a reader -- and to the scan that holds the declared set equal
    to the set the harness reads -- exactly like every other configured key.
    That the mapping has one entry is the narrowness, stated in code.
    """
    return {"tests_dir": config.get("tests_dir")}


class UnresolvedWorkflowToken(ValueError):
    """A loaded workflow carries a reference the configuration cannot answer.

    Carries `problems` in the shape every pre-flight refusal enumerates, so
    the coordinator turns it into a refusal rather than composing its own
    wording for it.
    """

    def __init__(self, workflow: str, tokens: list[str]):
        self.workflow = workflow
        self.tokens = tokens
        referable = ", ".join(f"{{{{{name}}}}}" for name in workflow_token_values({}))
        self.problems = [
            f"'{{{{{token}}}}}' is not a configuration reference the harness "
            f"resolves; a workflow declaration may reference {referable}, and "
            f"only as a whole list entry"
            for token in tokens
        ]
        super().__init__("; ".join(self.problems))


def _resolve_tokens(value, values: dict[str, str | None], unresolved: list[str]):
    """Substitute every `{{key}}` list entry, dropping the ones with no value.

    An unset key resolves the entry *out of the list* rather than to an empty
    string: a restriction whose prefix is "" is a prefix every path is under,
    which is the opposite of the "this target declares none" the absence
    means. Every other token-shaped string -- one naming a key outside the
    narrow set, or a resolvable one somewhere a list entry cannot be dropped
    from -- is collected as unresolved for the caller to refuse on.
    """
    if isinstance(value, dict):
        return {key: _resolve_tokens(item, values, unresolved)
                for key, item in value.items()}
    if isinstance(value, list):
        resolved = []
        for item in value:
            match = _WORKFLOW_TOKEN.fullmatch(item) if isinstance(item, str) else None
            if match is None:
                resolved.append(_resolve_tokens(item, values, unresolved))
                continue
            name = match.group(1)
            if name not in values:
                unresolved.append(name)
            elif values[name]:
                resolved.append(values[name])
        return resolved
    if isinstance(value, str):
        unresolved.extend(_WORKFLOW_TOKEN.findall(value))
    return value


def load_workflow(harness_root: Path, name: str, config: dict) -> dict:
    """The workflow definition, with its configuration references resolved.

    Resolution happens once, when the definition loads, so `stage_restrictions`
    and every reader of a loaded workflow reads exactly what it read when the
    declaration was a literal directory. `config` is required rather than
    defaulted: a caller that omitted it would silently load a definition with
    the configured entries missing, which is a quieter wrong answer than a
    TypeError.
    """
    path = harness_root / "workflows" / f"{name}.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    unresolved: list[str] = []
    resolved = _resolve_tokens(definition, workflow_token_values(config), unresolved)
    if unresolved:
        raise UnresolvedWorkflowToken(name, unresolved)
    return resolved


def load_rules(harness_root: Path) -> dict:
    path = harness_root / "rules" / "execution-rules.json"
    return json.loads(path.read_text(encoding="utf-8"))
