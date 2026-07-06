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


def extract_section(story_text: str, key: str) -> str | None:
    """Pull one top-level block (e.g. acceptance_criteria) out of a story."""
    lines = story_text.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith(f"{key}:"):
            inside = True
            continue
        if inside and line and not line.startswith((" ", "\t")):
            break
        if inside:
            collected.append(line)
    text = "\n".join(collected).strip("\n")
    return text if text.strip() else None


def latest_verifier_finding(run_dir: Path) -> str | None:
    iterations = sorted((run_dir / "verification").glob("iteration-*.json"))
    return _read(iterations[-1]) if iterations else None


def build_context(
    *,
    story_text: str,
    run_dir: Path,
    target_root: Path,
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

    return {
        "story": story_text,
        "acceptance_criteria": extract_section(story_text, "acceptance_criteria"),
        "blocked_paths": "\n".join(f"- {p}" for p in rules.get("blocked_paths", [])),
        "test_command": config.get("test_command"),
        "repository_standards": standards,
        "architecture_docs": _read_files(target_root, doc_paths),
        "architecture_doc_paths": "\n".join(f"- {p}" for p in doc_paths) or None,
        "run_dir": str(run_dir),
        "changed_files": _read(run_dir / "changed-files.json"),
        "implementation_summary": _read(run_dir / "implementation-summary.md"),
        "test_results": _read(run_dir / "test-results.json"),
        "verification_result": _read(run_dir / "verification-result.json"),
        "latest_verifier_finding": latest_verifier_finding(run_dir),
        "retry_guidance": _read(run_dir / "retry-guidance.json"),
        "retry_state": retry_state,
        "testing_standards": _read(standards_dir / "testing.md"),
    }
