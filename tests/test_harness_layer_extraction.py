"""Which prompts carry a shared partial, and which do not.

story-003 extracted the shared [Harness Layer] block into a single injected
file (prompts/harness-layer.md) and injected it into the implementer, tester
and documenter stage templates via {{harness_layer}}, leaving the verifier's
distinct harness layer and the non-stage prompts intact.

story-055 added a *second* shared partial beside it, prompts/prose-layer.md,
with a different audience: the harness-layer partial addresses stages that
mutate a tree, and the prose partial addresses anything that writes prose a
human later reads — which includes the planner, a template no coordinator
renders. So the two partials have deliberately different reach, and this
module holds both: the second one reaching every workflow stage and the
planner, and the first one reaching exactly what it reached before.

The prompt templates and the shared partials are live harness artifacts, and
what this module asserts is what this repository ships, so they are read as
its subject rather than stood in for by a fixture. The two reach assertions
are read back out of prompts a *run* rendered and out of the prompt
`scripts/l5-plan` hands to a session, rather than off the templates alone,
because a placeholder in a template that nothing resolves is not reach.

Every absence asserted here carries a demonstration that it can fail:

  * "the prose partial carries no {{placeholder}} of its own" sits beside the
    same detector run over the harness-layer partial, which carries one;
  * "the planner receives none of the harness layer" sits beside the same
    search for the prose partial's text in the same rendered prompt, which
    finds it, and beside a stage prompt, which carries both;
  * "editing one file changes every stage" is the existing one-file-edit
    control, restated for the second partial.
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
import conftest
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

STAGE_TEMPLATES = ("story-implementer.md", "story-tester.md", "documenter.md")

REPO_ROOT = Path(context_assembler.__file__).resolve().parents[1]

#: The second shared partial, named through the assembler's own constant so
#: this module and the code under test have one spelling of it between them.
PROSE_LAYER = context_assembler.PROSE_LAYER


#: The loaded workflow build_context has taken as a required argument
#: since story-028, which injects the workflow's own facts — its stages,
#: its create restrictions, its retry routes — into every stage prompt.
WORKFLOW = conftest.shipped_workflow(
    Path(context_assembler.__file__).resolve().parents[1], "story-workflow")


#: Template filename -> the stage that declares it, read off the definition
#: rather than derived by stripping `.md`. The two coincided until story-071
#: renamed the prompts a single workflow owns, and the declaration is what the
#: coordinator loads.
STAGE_OF_TEMPLATE = {stage["prompt"]: stage["name"]
                     for stage in WORKFLOW["stages"]}


def parsed_story(story_text: str) -> dict:
    return story_parser.parse(story_text, schema_validator.load_schema("story"))

# The shared block whose duplication story-003 removes. Matched exactly,
# including the {{blocked_paths}} placeholder line.
SHARED_BLOCK = (
    "[Harness Layer]\n"
    "\n"
    "All work must:\n"
    "- stay within the scope defined by the injected workflow state,\n"
    "- produce the required output artifacts in the run directory, and\n"
    "- avoid modifying blocked paths under any circumstances.\n"
    "\n"
    "Blocked paths for every stage:\n"
    "{{blocked_paths}}"
)


def _build(target_root, harness_root):
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    context = context_assembler.build_context(
        story_text=story_text,
        story=parsed_story(story_text),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )
    return context, rules


def test_shared_partial_file_holds_the_block_once(harness_root):
    """AC1: prompts/harness-layer.md exists and holds the shared block including
    the {{blocked_paths}} placeholder line."""
    partial = (harness_root / "prompts" / "harness-layer.md").read_text()
    # The block opens the file and is held to its text exactly. It was the
    # whole file until story-035 appended the granted-list placeholder and the
    # single-command sentence to it; equality is repointed to a prefix rather
    # than relaxed, and the block still has to appear once and only once.
    assert partial.startswith(SHARED_BLOCK)
    assert partial.count("[Harness Layer]") == 1
    assert partial.count("Blocked paths for every stage:") == 1
    assert "{{blocked_paths}}" in partial


def test_stage_templates_use_placeholder_not_the_literal_block(harness_root):
    """AC2: each stage template contains {{harness_layer}} and no longer holds
    the literal duplicated harness-layer block."""
    for name in STAGE_TEMPLATES:
        template = context_assembler.load_template(harness_root, name)
        assert "{{harness_layer}}" in template, name
        # The distinctive lines of the shared block must not survive inline.
        assert "All work must:" not in template, name
        assert "Blocked paths for every stage:" not in template, name


def test_verifier_and_non_stage_prompts_are_intact(harness_root):
    """AC3: verifier keeps its distinct evidence-discipline harness layer and
    is not switched to the shared placeholder; planner and assist have no
    harness layer at all."""
    verifier = context_assembler.load_template(harness_root, "story-verifier.md")
    assert "{{harness_layer}}" not in verifier
    assert "All verification claims must:" in verifier      # its own, distinct block
    assert "All work must:" not in verifier
    for name in ("planner.md", "assist.md"):
        template = context_assembler.load_template(harness_root, name)
        assert "{{harness_layer}}" not in template, name
        assert "[Harness Layer]" not in template, name


def test_rendered_stage_prompts_have_no_leftover_placeholders(target_root, harness_root):
    """AC4: the two-pass render resolves both {{harness_layer}} and the nested
    {{blocked_paths}} in every stage prompt."""
    context, _ = _build(target_root, harness_root)
    for name in STAGE_TEMPLATES:
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, name), context
        )
        assert "{{harness_layer}}" not in rendered, name
        assert "{{blocked_paths}}" not in rendered, name
        assert "{{" not in rendered, name


def test_rendered_harness_layer_matches_pre_change_text(target_root, harness_root):
    """AC5: the injected, resolved harness layer is equivalent to the pre-change
    inline block — same rule text with the actual blocked paths resolved."""
    context, rules = _build(target_root, harness_root)
    resolved_blocked = "\n".join(f"- {p}" for p in rules.get("blocked_paths", []))
    expected = SHARED_BLOCK.replace("{{blocked_paths}}", resolved_blocked)
    assert resolved_blocked  # guard: blocked paths are actually present
    for name in STAGE_TEMPLATES:
        rendered = context_assembler.render(
            context_assembler.load_template(harness_root, name), context
        )
        assert expected in rendered, name


def test_one_file_edit_changes_every_stage(target_root, harness_root, tmp_path):
    """AC6: editing only prompts/harness-layer.md changes the harness layer of
    all three rendered stage prompts."""
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    fake_root = tmp_path / "harness"
    prompts = fake_root / "prompts"
    prompts.mkdir(parents=True)
    for name in (*STAGE_TEMPLATES, "harness-layer.md"):
        (prompts / name).write_text(
            (harness_root / "prompts" / name).read_text(), encoding="utf-8"
        )

    def render_all():
        context = context_assembler.build_context(
            story_text=story_text, story=parsed_story(story_text), run_dir=run_dir, target_root=target_root,
            harness_root=fake_root, config=config, rules=rules, workflow=WORKFLOW, retry_count=0,
        )
        return {
            name: context_assembler.render(
                context_assembler.load_template(fake_root, name), context
            )
            for name in STAGE_TEMPLATES
        }

    before = render_all()

    (prompts / "harness-layer.md").write_text(
        "[Harness Layer]\nONE-FILE-EDIT-MARKER\nBlocked paths for every stage:\n{{blocked_paths}}",
        encoding="utf-8",
    )
    after = render_all()

    for name in STAGE_TEMPLATES:
        assert "ONE-FILE-EDIT-MARKER" in after[name], name
        assert after[name] != before[name], name
        # blocked paths still resolve inside the edited block
        assert "- rules/" in after[name], name


def test_harness_layer_renders_none_when_partial_absent(target_root, harness_root, tmp_path):
    """The graceful-absence path: with no shared partial, harness_layer is unset
    and renders as None rather than raising."""
    fake_root = tmp_path / "harness"
    (fake_root / "prompts").mkdir(parents=True)
    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    context = context_assembler.build_context(
        story_text=story_text, story=parsed_story(story_text), run_dir=run_dir, target_root=target_root,
        harness_root=fake_root, config=config, rules=rules, workflow=WORKFLOW, retry_count=0,
    )
    assert context.get("harness_layer") is None
    rendered = context_assembler.render("x {{harness_layer}} y", context)
    assert rendered == "x None y"


# --------------------------------------------------------------------------
# story-055: the second shared partial, and the first one's unchanged reach
# --------------------------------------------------------------------------


def prose_partial() -> str:
    return (REPO_ROOT / "prompts" / PROSE_LAYER).read_text(encoding="utf-8")


def test_the_prose_partial_exists_and_carries_no_placeholder_of_its_own():
    """It has to render against the planner's context, which is narrower than a
    stage's: a placeholder here would resolve to the literal None in the one
    prompt that most needs the rule."""
    assert (REPO_ROOT / "prompts" / PROSE_LAYER).is_file()
    assert context_assembler.PLACEHOLDER.search(prose_partial()) is None


def test_the_same_detector_reports_the_placeholder_the_other_partial_carries():
    """Control: an empty result above means there is no placeholder, not that
    the detector has stopped seeing them."""
    other = (REPO_ROOT / "prompts" / "harness-layer.md").read_text(encoding="utf-8")
    found = context_assembler.PLACEHOLDER.search(other)
    assert found is not None
    assert found.group(1) == "blocked_paths"


def test_the_prose_partial_states_the_rule_its_exception_and_the_habit():
    text = prose_partial()
    rule = text.index("Do not write the count")
    exception = text.index("exception")
    habit = text.index("habit")
    assert rule < exception < habit
    # The exception is a count the adjacent content does not already carry.
    assert "adjacent" in text[exception:habit]
    # The habit is holding a count that matters with a test rather than prose.
    assert "test" in text[habit:]


# --------------------------------------------------------------------------
# The reach of each partial, read off prompts something actually rendered
# --------------------------------------------------------------------------


PASS_VERDICT = {"status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False}

#: What a stage's declared artifact is filled with, keyed by the schema the
#: stage declares for it rather than by the artifact's name, so the runner
#: below writes whatever the shipped definition declares without this module
#: naming a stage or an artifact of its own.
BY_SCHEMA = {
    "changed-files": {"modified": [], "created": [], "deleted": []},
    "test-results": {"status": "passed", "tests_written": 1, "tests_run": 1,
                     "tests_passed": 1, "tests_failed": 0, "failures": []},
    "verification-result": PASS_VERDICT,
}


class DeclaredArtifactRunner:
    """A fake runner that writes whatever the loaded workflow says a stage owns.

    Each invocation looks its own stage up in the definition the run loaded and
    writes the outputs that stage declares, filling each from the schema the
    stage declares for it and falling back to prose for the outputs that
    declare none.
    """

    def __init__(self, run_dir: Path, workflow: dict):
        self.run_dir = run_dir
        self.stages = {stage["name"]: stage for stage in workflow["stages"]}
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        declaration = self.stages[stage]
        schemas = declaration.get("schemas", {})
        for artifact in declaration.get("outputs", []):
            path = self.run_dir / artifact
            payload = BY_SCHEMA.get(schemas.get(artifact))
            if payload is None:
                path.write_text(f"{stage} had nothing to report.\n",
                                encoding="utf-8")
            else:
                path.write_text(json.dumps(payload, indent=2) + "\n",
                                encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


@pytest.fixture
def rendered_run(target_root, harness_root):
    """A real run of the shipped workflow, for the prompts it leaves behind.

    The subject is which of this repository's prompts carry the partial once
    rendered, so this run is driven against what this repository ships rather
    than against a built workflow. Nothing here invokes a model.
    """
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    runner = DeclaredArtifactRunner(run_dir, WORKFLOW)
    code = story_coordinator.run_story("story-001", harness_root, target_root,
                                       runner)
    assert code == 0, (code, runner.calls)
    return runner, run_dir


def rendered_stage_prompts(run_dir: Path) -> dict[str, str]:
    """Each stage's rendered prompt, read back off the run directory."""
    return {
        stage["name"]: (run_dir / story_coordinator.prompt_file(
            stage["name"], 1)).read_text(encoding="utf-8")
        for stage in WORKFLOW["stages"]
    }


def test_every_workflow_stages_rendered_prompt_carries_the_prose_partial(
    rendered_run,
):
    runner, run_dir = rendered_run
    assert runner.calls == [stage["name"] for stage in WORKFLOW["stages"]]
    for name, prompt in rendered_stage_prompts(run_dir).items():
        assert prose_partial() in prompt, name


def test_no_rendered_stage_prompt_leaves_a_placeholder_unresolved(rendered_run):
    _, run_dir = rendered_run
    for name, prompt in rendered_stage_prompts(run_dir).items():
        assert context_assembler.PLACEHOLDER.search(prompt) is None, name


def test_the_harness_layer_partial_still_reaches_the_stages_it_reached(
    rendered_run,
):
    """Its reach is unchanged by the second partial: every stage that mutates
    a tree carries it, and the verifier carries its own inline block instead."""
    _, run_dir = rendered_run
    prompts = rendered_stage_prompts(run_dir)
    for name in STAGE_TEMPLATES:
        stage = STAGE_OF_TEMPLATE[name]
        assert "[Harness Layer]" in prompts[stage], stage
        assert "All work must:" in prompts[stage], stage

    verifier = next(p for p in prompts.values()
                    if "All verification claims must:" in p)
    assert "All work must:" not in verifier
    assert prose_partial() in verifier


# --------------------------------------------------------------------------
# The planner, which no coordinator renders
# --------------------------------------------------------------------------


@pytest.fixture
def planner_prompt(tmp_path: Path) -> str:
    """The prompt `scripts/l5-plan` hands to a session, captured whole.

    Through the real script with a stub `claude` on PATH, which is the render
    the planner actually receives — the same fixture shape
    `tests/test_planner_injection.py` established for the same reason.
    """
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "config.yaml").write_text(
        "workflow: story-workflow\ntests_dir: tests/\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_path = tmp_path / "argv.json"
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_path)!r}, 'w').write(json.dumps(sys.argv))\n",
        encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        # The session states the workflow it renders against: this module's
        # subject is the shared partial in the rendered prompt, and since
        # story-072 an invocation with no terminal and no --workflow is refused
        # rather than falling back to the configured name.
        [sys.executable, str(REPO_ROOT / "scripts" / "l5-plan"),
         "--workflow", "story-workflow", "a story request"],
        env=env, capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_path.read_text(encoding="utf-8"))
    return argv[argv.index("--append-system-prompt") + 1]


def test_the_planner_prompt_the_script_renders_carries_the_prose_partial(
    planner_prompt,
):
    assert prose_partial() in planner_prompt


def test_that_render_still_resolves_every_placeholder_it_did_before(
    planner_prompt,
):
    """The partial is injected into a context narrower than a stage's, so this
    is where an unresolved placeholder would show up first."""
    assert context_assembler.PLACEHOLDER.search(planner_prompt) is None
    assert "{{" not in planner_prompt
    # And the render is still the one it was: the story schema it has always
    # carried is still injected whole.
    assert (REPO_ROOT / "schemas" / "story.schema.json").read_text(
        encoding="utf-8") in planner_prompt


def test_the_planner_receives_none_of_the_harness_layer(planner_prompt):
    """The first partial's reach is unchanged: the planner mutates no tree and
    is given no blocked-path block, before this story or after it."""
    assert "{{harness_layer}}" not in planner_prompt
    assert "[Harness Layer]" not in planner_prompt
    assert "All work must:" not in planner_prompt


def test_that_absence_is_about_the_planner_and_not_about_the_search(
    planner_prompt, rendered_run,
):
    """Control: the same searches over a stage's rendered prompt find the
    harness layer, and the same search for the prose partial finds it in the
    planner's. An empty result above is the planner's reach rather than a
    search looking in the wrong place."""
    _, run_dir = rendered_run
    stage = next(p for p in rendered_stage_prompts(run_dir).values()
                 if "All work must:" in p)
    assert "[Harness Layer]" in stage
    assert prose_partial() in stage
    assert prose_partial() in planner_prompt


def test_assist_receives_neither_partial(harness_root):
    """The non-stage prompt no run renders keeps carrying neither, which the
    story states as an unchanged fact rather than a new one."""
    assist = context_assembler.load_template(harness_root, "assist.md")
    assert "{{harness_layer}}" not in assist
    assert "[Harness Layer]" not in assist
    assert "{{prose_layer}}" not in assist
    # Control: the templates that do carry them say so in the same spelling.
    planner = context_assembler.load_template(harness_root, "planner.md")
    assert "{{prose_layer}}" in planner
    assert "{{harness_layer}}" in context_assembler.load_template(
        harness_root, STAGE_TEMPLATES[0])


def test_one_file_edit_changes_the_prose_layer_of_every_prompt_carrying_it(
    target_root, harness_root, tmp_path,
):
    """The partial is one file with one home: editing it changes every prompt
    that carries it, which is what makes it a partial rather than a paragraph
    repeated five times."""
    fake_root = tmp_path / "harness"
    prompts_dir = fake_root / "prompts"
    prompts_dir.mkdir(parents=True)
    carriers = (*STAGE_TEMPLATES, "story-verifier.md", "planner.md")
    for name in (*carriers, "harness-layer.md", PROSE_LAYER):
        (prompts_dir / name).write_text(
            (harness_root / "prompts" / name).read_text(encoding="utf-8"),
            encoding="utf-8")

    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories"
                  / "story-001.yaml").read_text(encoding="utf-8")
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)

    def render_all():
        context = context_assembler.build_context(
            story_text=story_text, story=parsed_story(story_text),
            run_dir=run_dir, target_root=target_root, harness_root=fake_root,
            config=config, rules=rules, workflow=WORKFLOW, retry_count=0)
        rendered = {name: context_assembler.render(
            context_assembler.load_template(fake_root, name), context)
            for name in carriers if name != "planner.md"}
        # The planner's render is the narrower one l5-plan assembles, resolved
        # through the same helper, which is the whole point of the helper.
        planner_context = context_assembler.schema_context(REPO_ROOT)
        planner_context["prose_layer"] = context_assembler.resolved_partial(
            fake_root, PROSE_LAYER, planner_context)
        rendered["planner.md"] = context_assembler.render(
            context_assembler.load_template(fake_root, "planner.md"),
            planner_context)
        return rendered

    before = render_all()
    (prompts_dir / PROSE_LAYER).write_text(
        "[Prose Layer]\nONE-FILE-PROSE-EDIT-MARKER\n", encoding="utf-8")
    after = render_all()

    for name in carriers:
        assert "ONE-FILE-PROSE-EDIT-MARKER" in after[name], name
        assert after[name] != before[name], name
