"""Independent validation for story-009: the workflow's stage rules reach the
planner by injection, and the target-root lookup has one implementation.

Written from the story's acceptance criteria rather than from the
implementation. The story exists because prose restating a workflow fact
drifts from it silently, so these tests prefer observable behavior: what
`scripts/l5-plan` actually hands to `claude --append-system-prompt`
(captured by putting a fake `claude` on PATH), what the three entry points
print and return when no `.harness/config.yaml` exists, and what
`workflow_context` renders for a workflow definition the code has never
seen.

The coverage assertions need a control rather than an assertion: the stage,
restriction, and blocked-path coverage of the rendered prompt must come
from the injection. The control renders a copy of the template with the
three new placeholders removed and asserts the same coverage checks fail.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import story_diff

import context_assembler
import harness_config

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
ORCHESTRATION = REPO_ROOT / "orchestration"
PLANNER = REPO_ROOT / "prompts" / "planner.md"

PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")
NEW_PLACEHOLDERS = (
    "{{workflow_stages}}",
    "{{stage_create_restrictions}}",
    "{{blocked_paths}}",
)

NO_CONFIG_MESSAGE = "No .harness/config.yaml found here or above. Run l5-init first."


#: The loaded workflow build_context has taken as a required argument
#: since story-028, which injects the workflow's own facts — its stages,
#: its create restrictions, its retry routes — into every stage prompt.
WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")


def planner_template() -> str:
    return PLANNER.read_text(encoding="utf-8")


def workflow() -> dict:
    return harness_config.load_workflow(REPO_ROOT, "story-workflow")


def rules() -> dict:
    return harness_config.load_rules(REPO_ROOT)


def stage_names() -> list[str]:
    return [stage["name"] for stage in workflow()["stages"]]


def declared_restrictions() -> list[tuple[str, str]]:
    return [
        (stage["name"], prefix)
        for stage in workflow()["stages"]
        for prefix in stage.get("may_not_create", [])
    ]


def full_context() -> dict:
    context = context_assembler.schema_context(REPO_ROOT)
    context.update(context_assembler.workflow_context(workflow(), rules()))
    return context


def rendered_planner_prompt() -> str:
    return context_assembler.render(planner_template(), full_context())


def missing_stage_names(rendered: str) -> set[str]:
    return {
        name for name in stage_names()
        if not re.search(rf"\b{name}\b", rendered)
    }


def missing_restrictions(rendered: str) -> set[tuple[str, str]]:
    """(stage, prefix) pairs no single line of the rendered prompt states.

    Lines belonging verbatim to an injected schema file are not counted: a
    schema description that happens to mention a stage beside a prefix (the
    story schema's stage_exceptions prose does) is not the workflow stating
    its restriction."""
    schema_lines = {
        line
        for path in (REPO_ROOT / "schemas").glob("*.schema.json")
        for line in path.read_text(encoding="utf-8").splitlines()
    }
    lines = [line for line in rendered.splitlines() if line not in schema_lines]
    return {
        (stage, prefix)
        for stage, prefix in declared_restrictions()
        if not any(stage in line and prefix in line for line in lines)
    }


def missing_blocked_paths(rendered: str) -> set[str]:
    return {path for path in rules()["blocked_paths"] if path not in rendered}


def run_script(name: str, *args: str, cwd: Path, env: dict | None = None
               ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


# --------------------------------------------------------------------------
# planner.md states no workflow fact of its own
# --------------------------------------------------------------------------


def test_the_workflow_defines_the_four_expected_stages():
    """Anchor the data-driven assertions below to the stages the acceptance
    criteria name, so an accidentally emptied workflow cannot vacuously pass."""
    assert stage_names() == ["implementer", "tester", "verifier", "documenter"]
    assert declared_restrictions() == [("implementer", "tests/")]
    assert rules()["blocked_paths"] == [".git/", ".harness/runs/", "rules/"]


def test_the_template_names_no_workflow_stage_of_its_own():
    text = PLACEHOLDER.sub("", planner_template())
    for name in stage_names():
        assert not re.search(rf"\b{name}\b", text), name


def test_the_template_names_no_may_not_create_prefix_of_its_own():
    text = PLACEHOLDER.sub("", planner_template())
    for _, prefix in declared_restrictions():
        assert prefix not in text, prefix


def test_the_template_carries_the_three_workflow_placeholders():
    text = planner_template()
    for placeholder in NEW_PLACEHOLDERS:
        assert placeholder in text, placeholder


def test_the_skeleton_stage_field_description_stays():
    assert "stage: <the workflow stage expected to change it>" in planner_template()


def test_the_stage_exceptions_guidance_stays_in_the_template():
    """Injection replaces the statement of which stage is restricted on which
    path, not the planner's judgement about exceptions."""
    text = planner_template()
    assert "without asking the developer first" in text
    assert "stage_exceptions" in text
    assert "lifts one of those restrictions" in text


# --------------------------------------------------------------------------
# The rendered prompt carries every workflow fact
# --------------------------------------------------------------------------


def test_the_rendered_prompt_names_every_stage_the_workflow_defines():
    assert missing_stage_names(rendered_planner_prompt()) == set()


def test_the_rendered_prompt_states_every_declared_create_restriction():
    assert missing_restrictions(rendered_planner_prompt()) == set()


def test_the_rendered_prompt_lists_every_blocked_path_as_repository_wide():
    rendered = rendered_planner_prompt()
    assert missing_blocked_paths(rendered) == set()
    assert "repository-wide" in rendered
    assert "not per story" in rendered


def test_the_rendered_prompt_has_no_leftover_placeholder():
    assert PLACEHOLDER.search(rendered_planner_prompt()) is None


def test_the_coverage_comes_from_the_injection_and_not_from_leftover_prose():
    """Negative control: strip the three placeholders and every coverage
    check above must lose ground. A coverage assertion that passes against a
    stripped template proves nothing."""
    stripped = planner_template()
    for placeholder in NEW_PLACEHOLDERS:
        stripped = stripped.replace(placeholder, "")
    rendered = context_assembler.render(stripped, full_context())
    assert missing_stage_names(rendered) != set()
    assert missing_restrictions(rendered) == set(declared_restrictions())
    assert missing_blocked_paths(rendered) == set(rules()["blocked_paths"])


# --------------------------------------------------------------------------
# workflow_context is a function over the definitions, not over this workflow
# --------------------------------------------------------------------------


def test_workflow_context_renders_a_workflow_the_code_has_never_seen():
    unseen_workflow = {"stages": [
        {"name": "alpha"},
        {"name": "beta", "may_not_create": ["docs/", "src/"]},
    ]}
    context = context_assembler.workflow_context(
        unseen_workflow, {"blocked_paths": ["vendored/"]}
    )
    assert context["workflow_stages"] == "- alpha\n- beta"
    lines = context["stage_create_restrictions"].splitlines()
    assert len(lines) == 2
    assert all("beta" in line for line in lines)
    assert "docs/" in lines[0] and "src/" in lines[1]
    assert "alpha" not in context["stage_create_restrictions"]
    assert context["blocked_paths"] == "- vendored/"


def test_workflow_context_renders_absent_declarations_as_none():
    context = context_assembler.workflow_context(
        {"stages": [{"name": "solo"}]}, {}
    )
    assert context["stage_create_restrictions"] is None
    assert context["blocked_paths"] is None
    rendered = context_assembler.render("{{stage_create_restrictions}}", context)
    assert rendered == "None"


def test_workflow_context_and_build_context_render_blocked_paths_identically(
    target_root, harness_root
):
    import schema_validator
    import story_parser

    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    the_rules = rules()
    built = context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text, schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=the_rules,
        workflow=WORKFLOW,
        retry_count=0,
    )
    injected = context_assembler.workflow_context(workflow(), the_rules)
    assert built["blocked_paths"] == injected["blocked_paths"]
    assert built["blocked_paths"] == "- .git/\n- .harness/runs/\n- rules/"


# --------------------------------------------------------------------------
# l5-plan reads the target repository and refuses to start without one
# --------------------------------------------------------------------------


@pytest.fixture
def plan_capture(tmp_path: Path):
    """A fake `claude` on PATH plus a minimal target repository, returning a
    callable that runs l5-plan from a chosen directory and reports (result,
    captured argv or None)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_path = tmp_path / "argv.json"
    fake = bin_dir / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps(sys.argv))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def run_plan(cwd: Path):
        result = run_script("l5-plan", "a story request", cwd=cwd, env=env)
        argv = (json.loads(argv_path.read_text(encoding="utf-8"))
                if argv_path.is_file() else None)
        return result, argv

    return run_plan


def test_l5_plan_injects_the_workflow_facts_into_the_session_prompt(
    plan_capture, tmp_path
):
    """End to end: the prompt handed to `claude --append-system-prompt` from a
    real target repository carries every stage, restriction, and blocked path,
    with the story schema still injected beside them."""
    project = tmp_path / "project"
    (project / ".harness").mkdir(parents=True)
    (project / ".harness" / "config.yaml").write_text(
        "workflow: story-workflow\n", encoding="utf-8"
    )
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)

    result, argv = plan_capture(nested)
    assert result.returncode == 0, result.stderr
    assert argv is not None
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert missing_stage_names(prompt) == set()
    assert missing_restrictions(prompt) == set()
    assert missing_blocked_paths(prompt) == set()
    assert PLACEHOLDER.search(prompt) is None
    schema_text = (REPO_ROOT / "schemas" / "story.schema.json").read_text(
        encoding="utf-8"
    )
    assert schema_text in prompt
    assert argv[-1] == "Story request: a story request"


def test_l5_plan_without_a_target_repository_exits_nonzero_and_starts_no_session(
    plan_capture, tmp_path
):
    bare = tmp_path / "nowhere"
    bare.mkdir()
    result, argv = plan_capture(bare)
    assert result.returncode != 0
    assert NO_CONFIG_MESSAGE in result.stderr
    assert argv is None, "l5-plan started a session with no target repository"


# --------------------------------------------------------------------------
# One walk-up loop, and the entry points behave as before the extraction
# --------------------------------------------------------------------------


def test_find_target_root_lives_in_harness_config():
    nested_probe = REPO_ROOT / "orchestration"
    assert harness_config.find_target_root(nested_probe) == REPO_ROOT


def test_find_target_root_exits_with_the_no_config_message(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        harness_config.find_target_root(tmp_path)
    assert str(excinfo.value) == NO_CONFIG_MESSAGE


def test_all_three_scripts_call_the_shared_lookup():
    for name in ("l5-run", "l5-plan", "l5-status"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "harness_config.find_target_root(Path.cwd())" in source, name


def test_the_walk_up_loop_appears_exactly_once_in_the_repository():
    hits = []
    for path in sorted(list(ORCHESTRATION.glob("*.py")) + list(SCRIPTS.iterdir())):
        if path.is_file() and "start.parents" in path.read_text(encoding="utf-8"):
            hits.append(path.name)
    assert hits == ["harness_config.py"]


@pytest.mark.parametrize("script,args", [
    ("l5-run", ("story-001",)),
    ("l5-status", ()),
    ("l5-plan", ("a story request",)),
])
def test_each_entry_point_fails_identically_with_no_config(tmp_path, script, args):
    """The message and exit status are byte-for-byte what l5-run produced
    before the extraction, for every caller."""
    result = run_script(script, *args, cwd=tmp_path)
    assert result.returncode == 1
    assert result.stderr.strip() == NO_CONFIG_MESSAGE
    assert result.stdout == ""


def test_l5_run_still_finds_the_target_root_from_a_subdirectory(target_root):
    """Behavior-level proof the extracted lookup still walks up: a pre-flight
    refusal (an exception naming an unknown stage) reaches l5-run run from a
    nested directory, which requires the config lookup to have succeeded."""
    story = target_root / ".harness" / "stories" / "story-001.yaml"
    story.write_text(
        story.read_text()
        + "\nstage_exceptions:\n  - stage: reviewer\n    create: tests/\n"
          "    reason: why\n",
        encoding="utf-8",
    )
    nested = target_root / "src" / "deep"
    nested.mkdir(parents=True)
    result = run_script("l5-run", "story-001", cwd=nested)
    assert result.returncode == 1
    assert "reviewer" in result.stderr
    assert NO_CONFIG_MESSAGE not in result.stderr


# --------------------------------------------------------------------------
# What this story leaves alone
# --------------------------------------------------------------------------


def _unchanged_by_this_story(rel: str) -> bool:
    """Whether *this story's own change* left `rel` alone.

    Not `git diff HEAD`. That asks whether the working tree is dirty here,
    which is a question about whoever is working right now: it goes vacuously
    green the moment anything is committed, and red for every later story that
    legitimately edits one of these paths. Bound the comparison at both ends
    instead — this story's own run commit against its parent.

    Since story-015 the resolution lives once in `tests/conftest.py` rather
    than being restated here, and it keys on this validation file's own
    adding commit rather than on a marker planted in the story's source: the
    commit that added `tests/test_story_009_validation.py` *is* story-009's
    run commit, and no marker has to be chosen and kept true for that to
    hold.
    """
    return story_diff(
        [rel], validation_file=Path(__file__), options=("--stat",),
    ).strip() == ""


@pytest.mark.parametrize("rel", ["workflows/", "rules/", "schemas/"])
def test_the_definitions_this_story_injects_are_unchanged(rel):
    assert _unchanged_by_this_story(rel)
