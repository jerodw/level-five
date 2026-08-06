"""Assemble stage context and inject it into prompt templates.

The coordinator selects what each stage sees and delivers it by
injection: artifact content is substituted into {{placeholder}} fields in
a fixed template. Optional placeholders with nothing to inject render as
None so the prompt stays coherent. Source code is never injected; agents
read it by reference because the coordinator cannot enumerate it in
advance.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


def render(template: str, context: dict[str, str | None]) -> str:
    def substitute(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        return value if value else "None"

    return PLACEHOLDER.sub(substitute, template)


def load_template(harness_root: Path, prompt_file: str) -> str:
    return (harness_root / "prompts" / prompt_file).read_text(encoding="utf-8")


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _read_files(root: Path, paths: list[str]) -> str | None:
    sections = []
    for rel in paths:
        text = _read(root / rel)
        if text:
            sections.append(f"--- {rel} ---\n{text}")
    return "\n".join(sections) if sections else None


def _dashed_lines(items: object) -> str | None:
    """Render a parsed list of criteria as one dash-prefixed line each.

    The list comes from the parse, so each item is already one whole
    criterion: no YAML indentation, no quoting, no hand-wrapping. An absent
    or empty list renders as None, the optional-placeholder convention.
    """
    if not items:
        return None
    return "\n".join(f"- {item}" for item in items)


def latest_verifier_finding(run_dir: Path) -> str | None:
    iterations = sorted((run_dir / "verification").glob("iteration-*.json"))
    return _read(iterations[-1]) if iterations else None


def build_context(
    *,
    story_text: str,
    story: dict,
    run_dir: Path,
    target_root: Path,
    harness_root: Path,
    config: dict,
    rules: dict,
    retry_count: int,
) -> dict[str, str | None]:
    standards_dir = target_root / config.get("standards_dir", ".harness/standards")
    standards = _read_files(
        standards_dir, sorted(p.name for p in standards_dir.glob("*.md"))
    ) if standards_dir.is_dir() else None
    doc_paths = config.get("architecture_docs", [])

    retry_state = None
    if retry_count > 0:
        retry_state = json.dumps(
            {"retry_iteration": retry_count, "max_retries": rules["max_retries"]},
            indent=2,
        )

    # {{story}} stays the raw artifact, byte-identical to the file on disk, so
    # agents read the story as authored. Every *structural* value is taken from
    # the parse instead, so nothing reads the artifact a second way.
    context: dict[str, str | None] = {
        "story": story_text,
        "acceptance_criteria": _dashed_lines(story.get("acceptance_criteria")),
        "blocked_paths": "\n".join(f"- {p}" for p in rules.get("blocked_paths", [])),
        "test_command": config.get("test_command"),
        "repository_standards": standards,
        "architecture_docs": _read_files(target_root, doc_paths),
        "architecture_doc_paths": "\n".join(f"- {p}" for p in doc_paths) or None,
        "run_dir": str(run_dir),
        "changed_files": _read(run_dir / "changed-files.json"),
        "tester_changed_files": _read(run_dir / "tester-changed-files.json"),
        "implementation_summary": _read(run_dir / "implementation-summary.md"),
        "test_results": _read(run_dir / "test-results.json"),
        "verification_result": _read(run_dir / "verification-result.json"),
        "latest_verifier_finding": latest_verifier_finding(run_dir),
        "retry_guidance": _read(run_dir / "retry-guidance.json"),
        "retry_state": retry_state,
        "testing_standards": _read(standards_dir / "testing.md"),
    }

    # Artifact schemas are injected rather than restated inline in prompts, so
    # the definition an agent is asked to satisfy is the same file the
    # coordinator enforces. schemas/verification-result.schema.json becomes
    # {{verification_result_schema}}.
    for schema_path in sorted((harness_root / "schemas").glob("*.schema.json")):
        stem = schema_path.name[: -len(".schema.json")]
        context[stem.replace("-", "_") + "_schema"] = schema_path.read_text(
            encoding="utf-8"
        )

    # Two-pass render: resolve the shared harness-layer partial (including its
    # own {{blocked_paths}} placeholder) against the assembled context before
    # injecting it, because render() is single-pass and does not re-scan
    # substituted text. Absent partial leaves harness_layer unset -> None.
    harness_layer_path = harness_root / "prompts" / "harness-layer.md"
    if harness_layer_path.is_file():
        partial = load_template(harness_root, "harness-layer.md")
        context["harness_layer"] = render(partial, context)

    return context
