"""Independent validation for story-008: the planner's story contract is the
schema file, not a copy of it.

Written from the story's acceptance criteria rather than from the
implementation. The story exists because prose restating a schema drifts
from it silently, so these tests prefer observable behavior over source
inspection wherever behavior is available: what `scripts/l5-plan` actually
hands to `claude --append-system-prompt` (captured by putting a fake
`claude` on PATH), and what `build_context` resolves for a workflow stage.

Two properties need a control rather than an assertion:

- the required-field coverage of the rendered planner prompt must come from
  the injection. The control renders a copy of the template with
  `{{story_schema}}` removed and asserts the same coverage check fails.
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
SCHEMAS_DIR = REPO_ROOT / "schemas"
PLANNER = REPO_ROOT / "prompts" / "planner.md"
STORY_SCHEMA_PATH = SCHEMAS_DIR / "story.schema.json"

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
    refusal path is covered in test_story_009_validation.py."""
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
# What this story leaves alone
# --------------------------------------------------------------------------


def _unchanged_by_this_story(rel: str, *, diff_filter: str | None = None) -> bool:
    """Whether *this story's own change* left `rel` alone.

    Not `git diff HEAD`, which was what this helper asked before story-015.
    That asks whether the working tree is dirty here — a question about
    whoever is working right now, answered "clean" for every path the moment
    the coordinator commits the story. The baseline resolution lives in
    `tests/conftest.py`: this story's own run commit against its parent.
    """
    return story_diff(
        [rel], validation_file=Path(__file__), diff_filter=diff_filter,
        options=("--stat",),
    ).strip() == ""


def test_l5_assist_is_unchanged():
    assert _unchanged_by_this_story("scripts/l5-assist")


def test_the_story_schema_is_unchanged():
    assert _unchanged_by_this_story("schemas/story.schema.json")


def test_no_committed_story_artifact_was_edited():
    """Modifications and deletions only: this story's own run commit added
    `.harness/stories/story-008.yaml`, and an addition was never an edit."""
    assert _unchanged_by_this_story(".harness/stories", diff_filter="MD")


def test_every_committed_story_artifact_still_parses():
    import schema_validator
    import story_parser

    stories = sorted((REPO_ROOT / ".harness" / "stories").glob("*.yaml"))
    assert stories
    schema = schema_validator.load_schema("story")
    for path in stories:
        parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        assert parsed["story"]["id"], path.name
