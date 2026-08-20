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
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


def render(template: str, context: dict[str, str | None]) -> str:
    def substitute(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        return value if value else "None"

    return PLACEHOLDER.sub(substitute, template)


def load_template(harness_root: Path, prompt_file: str) -> str:
    return (harness_root / "prompts" / prompt_file).read_text(encoding="utf-8")


def schema_context(harness_root: Path) -> dict[str, str]:
    """Map every artifact schema to its injectable placeholder name.

    Artifact schemas are injected rather than restated inline in prompts, so
    the definition an agent is asked to satisfy is the same file the
    coordinator enforces. schemas/verification-result.schema.json becomes
    {{verification_result_schema}}. A new schema file becomes an injectable
    placeholder with no code change.

    This is the one place the glob lives: build_context calls it for workflow
    stages, and l5-plan calls it for the planner template, which no
    coordinator renders.
    """
    context: dict[str, str] = {}
    for schema_path in sorted((harness_root / "schemas").glob("*.schema.json")):
        stem = schema_path.name[: -len(".schema.json")]
        context[stem.replace("-", "_") + "_schema"] = schema_path.read_text(
            encoding="utf-8"
        )
    return context


@dataclass(frozen=True)
class RetryRoute:
    """One entry of a stage's declared retry_routing table."""

    declared_by: str
    category: str
    stage: str
    when: str


def retry_routes(stages: list[dict]) -> list[RetryRoute]:
    """The workflow's declared retry routes, in declared order.

    One derivation of "what does this workflow route", read by the
    coordinator's pre-flight check on the table and by the rendering below
    that injects the categories into the verifier's prompt, in the same
    spirit as story_coordinator.stage_restrictions. It lives here rather
    than in the coordinator because the coordinator imports this module and
    not the reverse, and a second copy in either direction would be a
    second answer to the same question.

    No category name and no destination is written here; both come off the
    loaded workflow definition.
    """
    return [
        RetryRoute(stage["name"], category, route["stage"], route.get("when", ""))
        for stage in stages
        for category, route in stage.get("on_failure", {})
        .get("retry_routing", {})
        .items()
    ]


def workflow_context(workflow: dict, rules: dict) -> dict[str, str | None]:
    """Map the workflow's stage facts to injectable placeholder names.

    The stage list, each stage's may_not_create prefixes, the rules'
    repository-wide blocked_paths, and the declared retry routes are
    injected rather than restated in prose, so the facts an agent is told
    are the definitions the coordinator enforces. l5-plan calls this for
    the planner template, which no coordinator renders; build_context
    merges it for every workflow stage.
    """
    stages = workflow["stages"]
    restrictions = [
        f"{stage['name']} may not create files under {prefix}"
        for stage in stages
        for prefix in stage.get("may_not_create", [])
    ]
    routes = [
        f"{route.category} -> {route.stage}: {route.when}"
        for route in retry_routes(stages)
    ]
    return {
        "workflow_stages": _dashed_lines([stage["name"] for stage in stages]),
        "stage_create_restrictions": _dashed_lines(restrictions),
        "blocked_paths": _dashed_lines(rules.get("blocked_paths")),
        "retry_routes": _dashed_lines(routes),
    }


def config_context(config: dict) -> dict[str, str | None]:
    """Map the target config's own facts to their injectable placeholder names.

    The granted Bash commands are rendered from the target's own configuration
    rather than restated in prose, so what a stage is told it may run cannot
    drift from what is actually permitted. The same reasoning puts the test
    location here: the stage that writes tests is told where they go by the
    configuration the restriction is resolved from, so changing the config
    changes the rendered prompt with no prompt edit. A config declaring
    neither renders as None, the optional-placeholder convention.
    """
    return {
        "allowed_tools": _dashed_lines(config.get("allowed_tools")),
        "tests_dir": config.get("tests_dir"),
    }


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


def _exception_lines(exceptions: object) -> str | None:
    """Render the story's stage exceptions, one dash-prefixed line each.

    The reason travels with the grant: a stage told a rule is lifted should
    also be told why, so it can tell whether its own work is the case the
    story had in mind. Absent or empty renders as None.
    """
    if not exceptions:
        return None
    return "\n".join(
        f"- {item['stage']} may create {item['create']}: {item['reason']}"
        for item in exceptions
    )


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
    workflow: dict,
    retry_count: int,
    retry_category: str | None = None,
    retry_stage: str | None = None,
    allowed_tools: list[str] | None = None,
    self_route_result: str | None = None,
) -> dict[str, str | None]:
    standards_dir = target_root / config.get("standards_dir", ".harness/standards")
    standards = _read_files(
        standards_dir, sorted(p.name for p in standards_dir.glob("*.md"))
    ) if standards_dir.is_dir() else None
    doc_paths = config.get("architecture_docs", [])

    # A stage receiving a retry is told it is on one, and since story-028 why:
    # the category the verifier reported and the stage the coordinator routed
    # to. The ceiling still comes from the rules, which hold the only copy of
    # it. A field with nothing behind it is omitted rather than sent as null,
    # the optional-by-absence convention the history schema already uses.
    retry_state = None
    if retry_count > 0:
        recorded = {
            "retry_iteration": retry_count,
            "max_retries": rules["max_retries"],
            "retry_category": retry_category,
            "retry_stage": retry_stage,
        }
        retry_state = json.dumps(
            {key: value for key, value in recorded.items() if value is not None},
            indent=2,
        )

    # {{story}} stays the raw artifact, byte-identical to the file on disk, so
    # agents read the story as authored. Every *structural* value is taken from
    # the parse instead, so nothing reads the artifact a second way.
    context: dict[str, str | None] = {
        "story": story_text,
        "acceptance_criteria": _dashed_lines(story.get("acceptance_criteria")),
        "stage_exceptions": _exception_lines(story.get("stage_exceptions")),
        "blocked_paths": _dashed_lines(rules.get("blocked_paths")),
        "test_command": config.get("test_command"),
        "repository_standards": standards,
        "architecture_docs": _read_files(target_root, doc_paths),
        "architecture_doc_paths": "\n".join(f"- {p}" for p in doc_paths) or None,
        "run_dir": str(run_dir),
        "changed_files": _read(run_dir / "changed-files.json"),
        "tester_changed_files": _read(run_dir / "tester-changed-files.json"),
        "documenter_changed_files": _read(
            run_dir / "documenter-changed-files.json"
        ),
        "documentation_report": _read(run_dir / "documentation-report.md"),
        "implementation_summary": _read(run_dir / "implementation-summary.md"),
        "test_results": _read(run_dir / "test-results.json"),
        "verification_result": _read(run_dir / "verification-result.json"),
        "latest_verifier_finding": latest_verifier_finding(run_dir),
        "retry_guidance": _read(run_dir / "retry-guidance.json"),
        # A clean-clone retry has no verifier finding behind it — the verifier
        # passed — so this is how the retried implementer receives the
        # evidence, rather than the coordinator fabricating retry-guidance.json.
        "clean_clone_result": _read(run_dir / "clean-clone-result.json"),
        # What the coordinator computed about the claims a document gained in
        # this run: an added claim about another story that nothing reachable
        # from the run's base could support or refute. Read here beside the
        # clean-clone record because it is the same kind of thing — a fact the
        # coordinator computed rather than an agent's judgement — and because
        # the stage that decides what to do about it is the one that judges the
        # documenter's work. Absent renders as None, which is what a stage
        # running before the check does.
        "claim_support_result": _read(run_dir / "claim-support-result.json"),
        # A self-routed stage has no agent-authored guidance behind it — the
        # stage failed mechanically and no verifier saw the work — so the
        # coordinator's own statement of why it is running again is passed in
        # rather than read here: the artifact's name is keyed by stage, attempt
        # and try, which is the coordinator's to compose. Defaulted to None so
        # a call that omits it renders exactly what it rendered before.
        "self_route_result": self_route_result,
        "retry_state": retry_state,
        "testing_standards": _read(standards_dir / "testing.md"),
    }

    context.update(schema_context(harness_root))
    # The workflow's own facts — its stages, its create restrictions, its
    # retry routes — come off the loaded definition rather than being restated
    # in any template. blocked_paths is rendered identically by both, through
    # the same helper, so the merge changes nothing about it.
    context.update(workflow_context(workflow, rules))
    # The target config's grants, injected the same way. This argument is
    # optional where `workflow` is required, and the asymmetry is deliberate:
    # a stage rendered with no categories in it would be a defect, while a
    # stage rendered with no granted list is exactly what every call site
    # rendered before this existed, so omitting it must change nothing.
    # `allowed_tools` still arrives as its own argument rather than off the
    # config, so a call that omits it renders exactly what it rendered before
    # the argument existed; every other configured fact this renders comes off
    # the config the caller already passed.
    context.update(config_context({**config, "allowed_tools": allowed_tools}))

    # Two-pass render: resolve the shared harness-layer partial (including its
    # own {{blocked_paths}} placeholder) against the assembled context before
    # injecting it, because render() is single-pass and does not re-scan
    # substituted text. Absent partial leaves harness_layer unset -> None.
    harness_layer_path = harness_root / "prompts" / "harness-layer.md"
    if harness_layer_path.is_file():
        partial = load_template(harness_root, "harness-layer.md")
        context["harness_layer"] = render(partial, context)

    return context
