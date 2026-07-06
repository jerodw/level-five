"""Load harness configuration and workflow definitions.

The target repository's .harness/config.yaml uses a deliberately small
subset of YAML: `key: value` pairs and lists of `- item` lines. Parsing it
directly keeps the harness free of third-party dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path


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
            config[current_list].append(line.strip()[2:].strip())
        elif ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value:
                config[key] = value
                current_list = None
            else:
                config[key] = []
                current_list = key
    return config


def load_workflow(harness_root: Path, name: str) -> dict:
    path = harness_root / "workflows" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_rules(harness_root: Path) -> dict:
    path = harness_root / "rules" / "execution-rules.json"
    return json.loads(path.read_text(encoding="utf-8"))
