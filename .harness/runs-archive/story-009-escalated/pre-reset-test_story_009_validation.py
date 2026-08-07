"""Independent validation for story-009: the planner's workflow facts are the
workflow file, not a copy of it, and the target-repository lookup has one
implementation.

Written from the story's acceptance criteria rather than from the
implementation. story-008 moved the story *shape* into an injected schema;
this story moves the workflow *facts* — the stage names, the stages' create
restrictions, and the repository-wide blocked paths — out of planner.md prose
and into injection, and gives `l5-plan` the target lookup it needs to read
them.

Two properties need a control rather than an assertion:

- the stage / prefix / blocked-path coverage of the rendered planner prompt
  must come from the new placeholders. The control renders a copy of the
  template with those three placeholders removed and asserts the same
  coverage check fails.
- `l5-run`'s no-config behavior must be unchanged by the extraction. The
  control runs the pre-story copy of the script (`git show HEAD~1:...`) in a
  mirrored harness tree and compares stderr and exit status byte for byte.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import context_assembler
import harness_config

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
SCRIPTS = REPO_ROOT / "scripts"
PLANNER = REPO_ROOT / "prompts" / "planner.md"
WORKFLOW_PATH = REPO_ROOT / "workflows" / "story-workflow.json"
RULES_PATH = REPO_ROOT / "rules" / "execution-rules.json"

PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")

NEW_PLACEHOLDERS = (
    "{{workflow_stages}}",
    "{{stage_create_restrictions}}",
    "{{blocked_paths}}",
)

NO_CONFIG_MESSAGE = "No .harness/config.yaml found here or above. Run l5-init first."


def planner_template() -> str:
    return PLANNER.read_text(encoding="utf-8")


def workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def stage_names() -> list[str]:
    return [stage["name"] for stage in workflow()["stages"]]


def create_prefixes() -> list[str]:
    return [
        prefix
        for stage in workflow()["stages"]
        for prefix in stage.get("may_not_create", [])
    ]


def blocked_paths() -> list[str]:
    return rules()["blocked_paths"]


def strip_placeholders(text: str) -> str:
    return PLACEHOLDER.sub("", text)


def render_planner(template: str | None = None) -> str:
    context: dict[str, str | None] = dict(context_assembler.schema_context(REPO_ROOT))
    context.update(context_assembler.workflow_context(workflow(), rules()))
    return context_assembler.render(
        planner_template() if template is None else template, context
    )


def missing_workflow_facts(rendered: str) -> set[str]:
    """Every stage name, create prefix and blocked path absent from `rendered`.

    The coverage assertion and its negative control share one definition, so
    the control demonstrates the same check it is controlling for.
    """
    missing = {
        name for name in stage_names()
        if not re.search(rf"\b{re.escape(name)}\b", rendered)
    }
    missing |= {p for p in create_prefixes() if p not in rendered}
    missing |= {p for p in blocked_paths() if p not in rendered}
    return missing


# --------------------------------------------------------------------------
# planner.md states no workflow fact of its own
# --------------------------------------------------------------------------


def test_the_planner_template_injects_the_workflow_facts():
    text = planner_template()
    for placeholder in NEW_PLACEHOLDERS:
        assert placeholder in text, placeholder


def test_planner_md_names_no_workflow_stage_outside_a_placeholder():
    prose = strip_placeholders(planner_template())
    named = [
        name for name in stage_names()
        if re.search(rf"\b{re.escape(name)}\b", prose, re.IGNORECASE)
    ]
    assert named == [], named


def test_planner_md_names_no_may_not_create_prefix_outside_a_placeholder():
    prose = strip_placeholders(planner_template())
    named = [prefix for prefix in create_prefixes() if prefix in prose]
    assert named == [], named


def test_the_stage_field_description_survives_in_the_skeleton():
    """The skeleton's `stage:` line describes a field; it names no stage."""
    text = planner_template()
    assert "stage: <the workflow stage expected to change it>" in text


def test_the_stage_exceptions_guidance_stays_written_in_the_template():
    """Judgement is role guidance, not a workflow fact: it does not move."""
    text = planner_template()
    assert "without asking the developer first" in text
    assert "stage_exceptions" in text
    # The explanation of what an exception is for stays with the instruction.
    assert "lifts one of those restrictions for one story" in text


# --------------------------------------------------------------------------
# The rendered prompt carries the workflow facts
# --------------------------------------------------------------------------


def test_the_rendered_prompt_names_every_stage_the_workflow_defines():
    rendered = render_planner()
    assert set(stage_names()) == {"implementer", "tester", "verifier", "documenter"}
    for name in stage_names():
        assert re.search(rf"\b{re.escape(name)}\b", rendered), name


def test_the_rendered_prompt_names_every_declared_create_prefix():
    rendered = render_planner()
    assert create_prefixes(), "the workflow declares no may_not_create prefix"
    for prefix in create_prefixes():
        assert prefix in rendered, prefix


def test_the_rendered_prompt_lists_every_blocked_path():
    rendered = render_planner()
    assert blocked_paths()
    for path in blocked_paths():
        assert f"- {path}" in rendered, path


def test_the_rendered_prompt_says_the_blocked_paths_are_repository_wide():
    rendered = render_planner()
    assert re.search(r"repository-wide", rendered)
    assert re.search(r"rather than per story", rendered)


def test_the_rendered_prompt_has_no_leftover_placeholder():
    assert PLACEHOLDER.search(render_planner()) is None


def test_the_coverage_comes_from_the_injection_and_not_from_leftover_prose():
    """Negative control: strip the three new placeholders and the coverage
    collapses. A coverage assertion that passes against a stripped template
    proves nothing."""
    stripped = planner_template()
    for placeholder in NEW_PLACEHOLDERS:
        stripped = stripped.replace(placeholder, "")
    assert missing_workflow_facts(render_planner(stripped))
    assert missing_workflow_facts(render_planner()) == set()


# --------------------------------------------------------------------------
# workflow_context renders the workflow's own declarations
# --------------------------------------------------------------------------


def test_workflow_context_renders_stages_restrictions_and_blocked_paths():
    context = context_assembler.workflow_context(workflow(), rules())
    assert context["workflow_stages"] == "\n".join(f"- {n}" for n in stage_names())
    assert context["blocked_paths"] == "\n".join(f"- {p}" for p in blocked_paths())
    assert "implementer may not create anything under tests/" in (
        context["stage_create_restrictions"]
    )


def test_workflow_context_reads_the_workflow_rather_than_a_copy_of_it():
    """A stage added to the workflow reaches the planner with no code change."""
    altered = workflow()
    altered["stages"].append({"name": "auditor", "may_not_create": ["docs/"]})
    context = context_assembler.workflow_context(altered, {"blocked_paths": ["x/"]})
    assert "- auditor" in context["workflow_stages"]
    assert "auditor may not create anything under docs/" in (
        context["stage_create_restrictions"]
    )
    assert context["blocked_paths"] == "- x/"


def test_workflow_context_declaring_nothing_renders_none():
    context = context_assembler.workflow_context({"stages": []}, {})
    assert context["workflow_stages"] is None
    assert context["stage_create_restrictions"] is None
    assert context["blocked_paths"] is None


def test_the_dashed_line_helper_renders_both_blocked_path_paths():
    """One function renders build_context's blocked_paths and the planner's."""
    source = (ORCHESTRATION / "context_assembler.py").read_text(encoding="utf-8")
    assert source.count('"blocked_paths": _dashed_lines(') == 2
    assert source.count('"blocked_paths": "\\n".join') == 0


# --------------------------------------------------------------------------
# build_context is unchanged
# --------------------------------------------------------------------------


def stage_context(target_root: Path, harness_root: Path) -> dict:
    import schema_validator
    import story_parser

    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    return context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text, schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=rules() | {"max_retries": 2},
        retry_count=0,
    )


def test_build_context_resolves_every_stage_placeholder(target_root, harness_root):
    context = stage_context(target_root, harness_root)
    for stage in workflow()["stages"]:
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, stage["prompt"]), context
        )
        assert PLACEHOLDER.search(rendered) is None, stage["name"]


def test_build_context_blocked_paths_render_exactly_as_before(
    target_root, harness_root
):
    """The pre-story rendering was a plain join of dash-prefixed lines."""
    context = stage_context(target_root, harness_root)
    assert context["blocked_paths"] == "\n".join(f"- {p}" for p in blocked_paths())


def test_build_context_injects_no_workflow_context_keys(target_root, harness_root):
    """workflow_context is the planner's; stage prompts are unaffected."""
    context = stage_context(target_root, harness_root)
    assert "workflow_stages" not in context
    assert "stage_create_restrictions" not in context


# --------------------------------------------------------------------------
# find_target_root has one implementation
# --------------------------------------------------------------------------


def test_find_target_root_lives_in_harness_config(target_root):
    nested = target_root / "src"
    assert harness_config.find_target_root(nested) == target_root
    assert harness_config.find_target_root(target_root) == target_root


def test_find_target_root_exits_with_the_no_config_message(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        harness_config.find_target_root(tmp_path)
    assert excinfo.value.code == NO_CONFIG_MESSAGE


def test_both_scripts_call_the_extracted_lookup():
    for name in ("l5-run", "l5-plan"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "harness_config.find_target_root(" in source, name


def test_the_walk_up_loop_appears_once_in_the_repository():
    """The loop is `for candidate in [start, *start.parents]` guarded by the
    config file. Any second copy is the drift this story exists to remove."""
    copies = []
    for path in sorted(REPO_ROOT.glob("scripts/*")) + sorted(
        REPO_ROOT.glob("orchestration/*.py")
    ):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "start.parents" in text and '".harness" / "config.yaml"' in text:
            copies.append(str(path.relative_to(REPO_ROOT)))
    assert copies == ["orchestration/harness_config.py"], copies


# --------------------------------------------------------------------------
# l5-plan locates the target repository
# --------------------------------------------------------------------------


@pytest.fixture
def fake_claude(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A `claude` on PATH that records its argv instead of starting a session."""
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
    return env, argv_path


def test_l5_plan_exits_non_zero_and_starts_no_session_without_a_config(
    tmp_path, fake_claude
):
    env, argv_path = fake_claude
    workdir = tmp_path / "nowhere"
    workdir.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "l5-plan"), "a story request"],
        env=env, capture_output=True, text=True, cwd=workdir,
    )
    assert result.returncode != 0
    assert result.stderr.strip() == NO_CONFIG_MESSAGE
    assert not argv_path.exists(), "l5-plan started a session with no config"


def test_l5_plan_renders_the_workflow_facts_from_the_target_repository(
    target_root, fake_claude
):
    env, argv_path = fake_claude
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "l5-plan"), "a story request"],
        env=env, capture_output=True, text=True, cwd=target_root / "src",
    )
    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_path.read_text(encoding="utf-8"))
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert PLACEHOLDER.search(prompt) is None
    assert missing_workflow_facts(prompt) == set()
    assert argv[-1] == "Story request: a story request"


def test_l5_plan_stays_thin():
    """The lookup, the loading and the rendering all belong to orchestration."""
    source = (SCRIPTS / "l5-plan").read_text(encoding="utf-8")
    assert "harness_config.load_config(" in source
    assert "harness_config.load_workflow(" in source
    assert "harness_config.load_rules(" in source
    assert "context_assembler.workflow_context(" in source
    assert "json.loads" not in source and "read_text" not in source


# --------------------------------------------------------------------------
# l5-run behaves exactly as before the extraction
# --------------------------------------------------------------------------


def previous_l5_run(tmp_path: Path) -> Path:
    """The pre-story copy of scripts/l5-run in a mirrored harness tree.

    Its own `HARNESS_ROOT` is `parents[1]`, so the mirror symlinks the
    packages it imports and the script runs unmodified.
    """
    source = subprocess.run(
        ["git", "show", "HEAD:scripts/l5-run"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    assert "def find_target_root" in source, "HEAD already has the extraction"
    mirror = tmp_path / "harness-head"
    (mirror / "scripts").mkdir(parents=True)
    for name in ("orchestration", "prompts", "workflows", "rules", "schemas"):
        (mirror / name).symlink_to(REPO_ROOT / name)
    script = mirror / "scripts" / "l5-run"
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)
    return script


def run_script(script: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, cwd=cwd,
    )


def test_l5_run_no_config_behavior_is_byte_for_byte_what_it_was(tmp_path):
    workdir = tmp_path / "nowhere"
    workdir.mkdir()
    before = run_script(previous_l5_run(tmp_path), ["story-001"], workdir)
    after = run_script(SCRIPTS / "l5-run", ["story-001"], workdir)
    assert before.returncode == after.returncode == 1
    assert before.stderr == after.stderr
    assert after.stderr.strip() == NO_CONFIG_MESSAGE
    assert after.stdout == before.stdout == ""


def test_l5_run_usage_behavior_is_byte_for_byte_what_it_was(tmp_path):
    workdir = tmp_path / "nowhere"
    workdir.mkdir()
    before = run_script(previous_l5_run(tmp_path), [], workdir)
    after = run_script(SCRIPTS / "l5-run", [], workdir)
    assert before.returncode == after.returncode == 1
    assert before.stderr == after.stderr


def test_l5_run_finds_the_target_root_from_a_nested_directory(target_root, tmp_path):
    """It reaches the coordinator with the repository root, not the cwd: the
    run fails on the unknown story id rather than on the lookup."""
    result = run_script(SCRIPTS / "l5-run", ["story-404"], target_root / "src")
    assert NO_CONFIG_MESSAGE not in result.stderr
    assert result.returncode != 0


# --------------------------------------------------------------------------
# What this story leaves alone
# --------------------------------------------------------------------------


def _unchanged_against_head(rel: str) -> bool:
    result = subprocess.run(
        ["git", "diff", "HEAD", "--stat", "--", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == ""


@pytest.mark.parametrize("rel", ["workflows/", "rules/", "schemas/", ".harness/stories"])
def test_the_declaration_files_are_unchanged(rel):
    assert _unchanged_against_head(rel)


def test_every_committed_story_artifact_still_parses():
    import schema_validator
    import story_parser

    stories = sorted((REPO_ROOT / ".harness" / "stories").glob("*.yaml"))
    assert stories
    schema = schema_validator.load_schema("story")
    for path in stories:
        parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        assert parsed["story"]["id"], path.name


def test_the_implementer_recorded_no_file_under_tests():
    """All new validation for this story is the tester's, in one file."""
    record = REPO_ROOT / ".harness" / "runs" / "story-009" / "changed-files.json"
    if not record.is_file():
        pytest.skip("no implementer record in this run directory")
    changed = json.loads(record.read_text(encoding="utf-8"))
    listed = changed["modified"] + changed["created"] + changed["deleted"]
    assert [p for p in listed if p.startswith("tests/")] == []
