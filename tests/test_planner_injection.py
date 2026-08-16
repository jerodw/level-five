"""What reaches the planner is injected, never restated in the template.

Two stories share that subject and this module validates both, which is why
it declares two origins in `conftest.STORY_ORIGINS` and every story-range
call below names the one it means:

- story-008: the planner's story *contract* is the schema file, not a copy
  of it.
- story-009: the *workflow's* stage rules — its stages, its create
  restrictions, the repository's blocked paths — reach the planner by
  injection too, and the target-root lookup the three entry points share has
  one implementation.

Written from the stories' acceptance criteria rather than from the
implementation. Both exist because prose restating a definition drifts from
it silently, so these tests prefer observable behavior over source
inspection wherever behavior is available: what `scripts/l5-plan` actually
hands to `claude --append-system-prompt` (captured by putting a fake
`claude` on PATH), what the three entry points print and return when no
`.harness/config.yaml` exists, and what `build_context` and
`workflow_context` resolve for definitions the code has never seen.

Three properties need a control rather than an assertion:

- the required-field coverage of the rendered planner prompt must come from
  the injection. The control renders a copy of the template with
  `{{story_schema}}` removed and asserts the same coverage check fails.
- the stage, restriction and blocked-path coverage needs the same control,
  with the three workflow placeholders removed.
- the "no normative prose in planner.md" check must be able to fail. The
  control feeds the detector the prose story-008 removed and asserts it is
  flagged.
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
ORCHESTRATION = REPO_ROOT / "orchestration"
SCRIPTS = REPO_ROOT / "scripts"
SCHEMAS_DIR = REPO_ROOT / "schemas"
PLANNER = REPO_ROOT / "prompts" / "planner.md"
STORY_SCHEMA_PATH = SCHEMAS_DIR / "story.schema.json"

#: The two stories this module validates, as `conftest.STORY_ORIGINS`
#: declares them. Every story-range call below names one of these, because a
#: module with two origins has two answers to "which commits are mine" and
#: being handed one of them silently is how a comparison ends up bounded at
#: the wrong story's commits.
STORY_008 = "tests/test_story_008_validation.py"
STORY_009 = "tests/test_story_009_validation.py"

PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")

# Vocabulary that states a section or field is part of the contract. "must"
# is deliberately absent: the template legitimately points at the schema
# ("the schema above says what must be present") without restating it, and
# the story asks for the statements to move, not for the pointer to go.
NORMATIVE = re.compile(r"\b(required|optional|mandatory)\b", re.IGNORECASE)

# The prose story-008 removed from planner.md, kept verbatim as the control
# for the detector below.
REMOVED_PROSE = """\
Write the approved story exactly in this shape. The shape is a
contract: the harness extracts acceptance_criteria by its top-level
key and injects it into the verifier prompt, and l5-run refuses to
execute a story artifact that is missing any required top-level
section.

A story may also carry an optional top-level stage_exceptions section,
and most do not.
"""


#: The loaded workflow build_context has taken as a required argument
#: since story-028, which injects the workflow's own facts — its stages,
#: its create restrictions, its retry routes — into every stage prompt.
WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")


def planner_template() -> str:
    return PLANNER.read_text(encoding="utf-8")


def story_schema() -> dict:
    return json.loads(STORY_SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_property_names(node: object, out: set[str] | None = None) -> set[str]:
    """Every property name the schema defines, at any depth."""
    names = set() if out is None else out
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in node.values():
            schema_property_names(value, names)
    elif isinstance(node, list):
        for value in node:
            schema_property_names(value, names)
    return names


def required_property_names(node: object, out: set[str] | None = None) -> set[str]:
    """Every name appearing in a `required` list, at any depth."""
    names = set() if out is None else out
    if isinstance(node, dict):
        required = node.get("required")
        if isinstance(required, list):
            names.update(n for n in required if isinstance(n, str))
        for value in node.values():
            required_property_names(value, names)
    elif isinstance(node, list):
        for value in node:
            required_property_names(value, names)
    return names


def normative_sentences(text: str) -> list[str]:
    """Sentences that name a schema property AND state contract membership."""
    names = schema_property_names(story_schema())
    flagged = []
    for sentence in re.split(r"(?<=[.;])\s+", PLACEHOLDER.sub("", text)):
        if not NORMATIVE.search(sentence):
            continue
        words = set(re.findall(r"[a-z_]+", sentence.lower()))
        if words & names:
            flagged.append(" ".join(sentence.split()))
    return flagged


def missing_required_names(rendered: str) -> set[str]:
    """Required property names absent from the rendered prompt.

    Looked for in their quoted JSON form, which only the injected schema
    supplies — the skeleton writes the story dialect, unquoted.
    """
    return {
        name for name in required_property_names(story_schema())
        if f'"{name}"' not in rendered
    }


def rendered_planner_prompt() -> str:
    return context_assembler.render(
        planner_template(), context_assembler.schema_context(REPO_ROOT)
    )


# --------------------------------------------------------------------------
# planner.md states nothing normative of its own
# --------------------------------------------------------------------------


def test_the_planner_template_injects_the_story_schema():
    assert "{{story_schema}}" in planner_template()


def test_the_planner_template_states_no_required_section_or_field_itself():
    assert normative_sentences(planner_template()) == []


def test_the_detector_flags_the_prose_this_story_removed():
    """Control: an empty result above must mean the prose is gone, not that
    the detector cannot see it."""
    flagged = normative_sentences(REMOVED_PROSE)
    assert len(flagged) == 2
    assert any("required top-level" in s for s in flagged)
    assert any("optional top-level stage_exceptions" in s for s in flagged)


def test_the_skeleton_survives_as_an_illustration():
    text = planner_template()
    assert "story-NNN" in text and "acceptance_criteria:" in text
    assert "illustration" in text
    # The word "contract" now points at the schema, not at the skeleton.
    assert re.search(r"contract is schemas/story\.schema\.json", text)


def test_the_skeleton_names_no_field_the_schema_does_not_define():
    known = schema_property_names(story_schema())
    text = PLACEHOLDER.sub("", planner_template())
    keys = {
        match.group(1)
        for match in re.finditer(r"^\t\s*(?:- )?([a-z_]+):", text, re.MULTILINE)
    }
    assert keys, "no skeleton keys found — the skeleton is indented with tabs"
    assert keys <= known, keys - known


def test_the_stage_exceptions_ask_first_guidance_survives():
    text = planner_template()
    assert "stage_exceptions" in text
    assert "without asking the developer first" in text


# --------------------------------------------------------------------------
# The rendered prompt carries the schema
# --------------------------------------------------------------------------


def test_the_rendered_prompt_carries_the_schema_file_verbatim():
    assert STORY_SCHEMA_PATH.read_text(encoding="utf-8") in rendered_planner_prompt()


def test_the_rendered_prompt_has_no_leftover_placeholder():
    assert PLACEHOLDER.search(rendered_planner_prompt()) is None


def test_every_required_property_name_reaches_the_rendered_prompt():
    required = required_property_names(story_schema())
    assert {
        "story", "tasks", "acceptance_criteria", "scope",
        "verification_requirements", "constraints",
        "id", "title", "description", "modify", "do_not_modify",
        "file", "reason", "stage", "create",
    } <= required
    assert missing_required_names(rendered_planner_prompt()) == set()


def test_the_coverage_comes_from_the_injection_and_not_from_leftover_prose():
    """Negative control: strip the placeholder and the coverage collapses."""
    stripped = planner_template().replace("{{story_schema}}", "")
    rendered = context_assembler.render(
        stripped, context_assembler.schema_context(REPO_ROOT)
    )
    assert missing_required_names(rendered) == required_property_names(story_schema())


# --------------------------------------------------------------------------
# l5-plan renders the template and passes it through --append-system-prompt
# --------------------------------------------------------------------------


@pytest.fixture
def captured_plan_argv(tmp_path: Path) -> list[str]:
    """Run scripts/l5-plan with a fake `claude` on PATH and capture its argv.

    Since story-009, l5-plan locates the target repository the way l5-run
    does, so the fixture provides a minimal .harness/config.yaml; the
    refusal path is covered by story-009's half of this module."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "config.yaml").write_text(
        "workflow: story-workflow\n", encoding="utf-8"
    )
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
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "l5-plan"), "a story request"],
        env=env, capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(argv_path.read_text(encoding="utf-8"))


def test_l5_plan_starts_an_interactive_session_with_the_request(captured_plan_argv):
    assert Path(captured_plan_argv[0]).name == "claude"
    assert "--append-system-prompt" in captured_plan_argv
    assert captured_plan_argv[-1] == "Story request: a story request"
    assert "--permission-mode" in captured_plan_argv


def test_l5_plan_passes_the_rendered_prompt_not_the_raw_template(captured_plan_argv):
    prompt = captured_plan_argv[captured_plan_argv.index("--append-system-prompt") + 1]
    assert prompt != planner_template()
    assert PLACEHOLDER.search(prompt) is None
    assert STORY_SCHEMA_PATH.read_text(encoding="utf-8") in prompt
    assert missing_required_names(prompt) == set()


def test_l5_plan_requires_a_target_repository(tmp_path):
    """Superseded by story-009, which inverted this test: l5-plan now reads
    the target's config to learn which workflow to inject, so with no
    .harness/config.yaml here or above it refuses instead of planning."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "l5-plan"), "a story request"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "config.yaml" in result.stderr


def test_l5_plan_adds_no_second_substitution_implementation():
    source = (REPO_ROOT / "scripts" / "l5-plan").read_text(encoding="utf-8")
    assert "context_assembler.render" in source
    assert "context_assembler.schema_context" in source
    assert "re.sub" not in source and ".replace(" not in source


# --------------------------------------------------------------------------
# One schema-context implementation, and build_context unchanged
# --------------------------------------------------------------------------


def test_the_schemas_glob_appears_once_in_context_assembler():
    source = (ORCHESTRATION / "context_assembler.py").read_text(encoding="utf-8")
    assert source.count('glob("*.schema.json")') == 1
    assert source.count("schemas") >= 1


def test_build_context_uses_the_same_schema_context_function():
    source = (ORCHESTRATION / "context_assembler.py").read_text(encoding="utf-8")
    body = source.split("def build_context")[1]
    assert "schema_context(harness_root)" in body


def test_schema_context_maps_every_schema_file_to_its_placeholder_name():
    context = context_assembler.schema_context(REPO_ROOT)
    expected = {
        path.name[: -len(".schema.json")].replace("-", "_") + "_schema":
            path.read_text(encoding="utf-8")
        for path in SCHEMAS_DIR.glob("*.schema.json")
    }
    assert context == expected
    assert all(isinstance(value, str) for value in context.values())


def test_build_context_still_resolves_every_stage_schema_placeholder(
    target_root, harness_root
):
    import harness_config
    import schema_validator
    import story_parser

    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    context = context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text, schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules={"blocked_paths": [".git/"], "max_retries": 3},
        workflow=WORKFLOW,
        retry_count=0,
    )
    for path in SCHEMAS_DIR.glob("*.schema.json"):
        key = path.name[: -len(".schema.json")].replace("-", "_") + "_schema"
        assert context[key] == path.read_text(encoding="utf-8"), key

    for name in ("implementer.md", "tester.md", "verifier.md"):
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, name), context
        )
        assert PLACEHOLDER.search(rendered) is None, name


# --------------------------------------------------------------------------
# What story-008 leaves alone
# --------------------------------------------------------------------------


def _unchanged_by_story_008(rel: str, *, diff_filter: str | None = None) -> bool:
    """Whether *story-008's own change* left `rel` alone.

    Not `git diff HEAD`, which was what this helper asked before story-015.
    That asks whether the working tree is dirty here — a question about
    whoever is working right now, answered "clean" for every path the moment
    the coordinator commits the story. The baseline resolution lives in
    `tests/conftest.py`: the story's own run commit against its parent.

    The origin is named because this module validates two stories, so the
    resolution refuses to guess which one a range belongs to.
    """
    return story_diff(
        [rel], validation_file=Path(__file__), diff_filter=diff_filter,
        options=("--stat",), origin=STORY_008,
    ).strip() == ""


def test_l5_assist_is_unchanged():
    assert _unchanged_by_story_008("scripts/l5-assist")


def test_the_story_schema_is_unchanged():
    assert _unchanged_by_story_008("schemas/story.schema.json")


def test_no_committed_story_artifact_was_edited():
    """Modifications and deletions only: story-008's own run commit added
    `.harness/stories/story-008.yaml`, and an addition was never an edit."""
    assert _unchanged_by_story_008(".harness/stories", diff_filter="MD")


def test_every_committed_story_artifact_still_parses():
    import schema_validator
    import story_parser

    stories = sorted((REPO_ROOT / ".harness" / "stories").glob("*.yaml"))
    assert stories
    schema = schema_validator.load_schema("story")
    for path in stories:
        parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        assert parsed["story"]["id"], path.name


# ==========================================================================
# story-009: the workflow's own facts reach the planner the same way
# ==========================================================================


NEW_PLACEHOLDERS = (
    "{{workflow_stages}}",
    "{{stage_create_restrictions}}",
    "{{blocked_paths}}",
)

NO_CONFIG_MESSAGE = "No .harness/config.yaml found here or above. Run l5-init first."


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


def rendered_prompt_with_workflow_facts() -> str:
    """The planner prompt rendered with the workflow facts injected too.

    Distinct from `rendered_planner_prompt` above, which renders the schema
    context alone: story-008's coverage assertions are about what the schema
    injection supplies, and story-009's about what the workflow injection
    does. Two contexts, so two renderings.
    """
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
    assert stage_names() == ["implementer", "tester", "documenter", "verifier"]
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
    assert missing_stage_names(rendered_prompt_with_workflow_facts()) == set()


def test_the_rendered_prompt_states_every_declared_create_restriction():
    assert missing_restrictions(rendered_prompt_with_workflow_facts()) == set()


def test_the_rendered_prompt_lists_every_blocked_path_as_repository_wide():
    rendered = rendered_prompt_with_workflow_facts()
    assert missing_blocked_paths(rendered) == set()
    assert "repository-wide" in rendered
    assert "not per story" in rendered


def test_the_rendered_prompt_with_workflow_facts_has_no_leftover_placeholder():
    assert PLACEHOLDER.search(rendered_prompt_with_workflow_facts()) is None


def test_the_workflow_fact_coverage_comes_from_the_injection_not_leftover_prose():
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
# What story-009 leaves alone
# --------------------------------------------------------------------------


def _unchanged_by_story_009(rel: str) -> bool:
    """Whether *story-009's own change* left `rel` alone.

    Not `git diff HEAD`. That asks whether the working tree is dirty here,
    which is a question about whoever is working right now: it goes vacuously
    green the moment anything is committed, and red for every later story that
    legitimately edits one of these paths. Bound the comparison at both ends
    instead — that story's own run commit against its parent.

    Since story-015 the resolution lives once in `tests/conftest.py` rather
    than being restated here. It keyed on this validation file's own adding
    commit until story-038 renamed and merged the file; the commit that added
    `tests/test_story_009_validation.py` is still story-009's run commit, and
    it is now named as an origin rather than inferred from a filename, so no
    later rename can move the answer.
    """
    return story_diff(
        [rel], validation_file=Path(__file__), options=("--stat",),
        origin=STORY_009,
    ).strip() == ""


@pytest.mark.parametrize("rel", ["workflows/", "rules/", "schemas/"])
def test_the_definitions_this_story_injects_are_unchanged(rel):
    assert _unchanged_by_story_009(rel)
