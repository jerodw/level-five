"""Independent validation for story-069: a run executes the workflow its work
item names.

The choice of workflow used to live in one place — `config.get("workflow",
"story-workflow")` — so a second definition was unreachable without editing
`.harness/config.yaml` between runs. This story makes the choice reachable per
work item, and puts it in two places that must agree: the story artifact names
the workflow, and `l5-plan` takes the same name as an argument because the
planner's stage facts are injected before the interview that would otherwise
choose. A session whose artifact names another workflow is refused, and that
refusal is what makes the injected facts trustworthy rather than merely usual.

Written from the story's acceptance criteria rather than from the
implementation, at four altitudes:

  * **the declaration.** `schemas/story.schema.json` is read here as the
    subject it is — an artifact this harness ships — and asked whether it
    declares the field, optionally, and says what its absence means.
  * **the functions.** `harness_config.workflow_names`,
    `harness_config.load_workflow` and `plan_validation.workflow_problems` are
    pure over inputs a test can construct, so they are driven directly.
  * **the run.** Targets built under `tmp_path` are driven through the real
    `story_coordinator.run_story` with a fake agent runner, and what a refusal
    *left behind* is read off the tree rather than inferred from an exit code.
  * **the planning session.** The real `scripts/l5-plan` is run as a
    subprocess against a throwaway repository with a stub `claude` on PATH, so
    what the prompt carries and what the session commits are observations of
    the script rather than of its source.

**The workflows here are built, not read.** Every definition these runs and
sessions execute is assembled by the builder in `tests/conftest.py` and
written into a harness root this module owns. The subject is the *mechanism* —
which definition a run loads and where the name came from — and a workflow is
its input: reading `workflows/story-workflow.json` here would make what this
repository deploys into something the suite enforces, and would also make the
central case untestable, since the story ships no second definition to select
between. No stage name, no artifact name and no create restriction below is
written by a test: each is derived from the definition the fixture built.

Every absence asserted here carries a demonstration that the same check
reports the violation it exists to catch:

  * "the run executed the workflow the artifact named and none of the other's
    distinctive stages" sits beside the identical target whose artifact names
    no workflow, which executes the configured one's stages instead;
  * "the refused run left no run directory, state file, log, branch or agent
    invocation" sits beside the identical target naming a defined workflow,
    where all five appear;
  * "the planner prompt carries the named workflow's stages and not the
    configured one's" sits beside the same session run without `--workflow`,
    where the configured one's appear and the named one's do not;
  * "the refused session committed nothing and left the artifact in the working
    tree" sits beside the same session whose artifact names the workflow it was
    rendered against, which is committed;
  * "no committed story artifact was edited" sits beside a constructed
    repository in which one was, which the same comparison reports;
  * "a state file written before the field existed reads as no recorded
    workflow" sits beside the same reader over a state file that records one.

Nothing here invokes a model: every run goes through the fake runner below and
every session through the stub `claude`.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from conftest import story_diff
from test_plan_commit import Planning, STUB, bare_remote, writes

import harness_config
import plan_validation
import story_coordinator
from agent_runner import AgentResult

HARNESS_ROOT = Path(__file__).resolve().parents[1]
#: The same directory, named for the other thing it is. The harness root is
#: where the shipped prompts and schemas are read from; the repository is what
#: the committed story artifacts belong to. Two names because the suite keeps
#: the two questions apart — `tests/test_artifact_schemas.py` forbids any test
#: from reaching `.harness/` *through a harness root*, since that is the only
#: way a test could reach a run directory that CI does not have.
REPO_ROOT = HARNESS_ROOT
STORY_SCHEMA_PATH = HARNESS_ROOT / "schemas" / "story.schema.json"
COORDINATOR_SOURCE = HARNESS_ROOT / "orchestration" / "story_coordinator.py"

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
TESTS_DIR = "tests/"

#: The placeholder the planner template carries for the selected workflow's
#: name, and the pattern every placeholder takes — the assembler's own, so a
#: template scanned here is scanned the way the renderer reads it.
WORKFLOW_NAME_PLACEHOLDER = "{{workflow_name}}"
PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")

RULES = {
    "max_retries": 2,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}


# --------------------------------------------------------------------------
# The workflows this module builds
#
# Two runnable definitions with different stage lists, so "the run executed
# *that* workflow's stages, in *that* workflow's order" is an observation
# rather than a coincidence: the call list is compared against the stage list
# read off the definition that was meant to be loaded, in the order it
# declares them.
#
# The names below are the fixture's own, declared once here, and every
# assertion derives from them the way it would derive from a shipped
# definition. `conftest.VERIFYING_STAGE` is the exception the harness itself
# imposes: verdict handling is keyed on that name, so a definition whose run
# must reach a verdict has to call its judging stage that. Both definitions
# therefore share it, and comparisons that need to tell the two apart use the
# stages that are distinctive to each.
# --------------------------------------------------------------------------

#: The prefixes each definition restricts, distinct per workflow so a rendered
#: planner prompt says which definition it was rendered against.
SELECTED_PREFIX = "selected-only/"
CONFIGURED_PREFIX = "configured-only/"


def runnable_workflow(name: str, writing: str, validating: str,
                      prefix: str, **extra) -> dict:
    """A definition a run can complete, whose stages are the ones named.

    Two writing stages and a judging one: enough shape for a run to reach a
    verdict, and enough difference between two of them for a call list to say
    which was loaded.
    """
    return conftest.build_workflow(
        conftest.workflow_stage(
            name=writing,
            outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
            changed_files=conftest.CHANGED_FILES,
            schemas={conftest.CHANGED_FILES: "changed-files"},
            may_not_create=(prefix,)),
        conftest.workflow_stage(
            name=validating,
            outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
            changed_files=conftest.TESTER_CHANGED_FILES,
            schemas={conftest.TEST_RESULTS: "test-results",
                     conftest.TESTER_CHANGED_FILES: "changed-files"}),
        conftest.workflow_stage(
            name=conftest.VERIFYING_STAGE,
            outputs=(conftest.VERIFICATION_RESULT,),
            schemas={conftest.VERIFICATION_RESULT: "verification-result",
                     conftest.RETRY_GUIDANCE: "retry-guidance"},
            retry_routing={"implementation-defect": {
                "stage": conftest.StageRef(0),
                "when": "the behaviour the story asked for is missing"}}),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name=name,
        **extra,
    )


#: The workflow a story artifact names below.
SELECTED = runnable_workflow("selected-workflow", "drafting", "checking",
                             SELECTED_PREFIX)
#: The workflow the target configuration names below — a different definition,
#: so every case naming the first is a case in which the two disagree.
CONFIGURED = runnable_workflow("configured-workflow", "composing", "auditing",
                               CONFIGURED_PREFIX)

#: A name no definition under any harness root this module builds carries.
UNDEFINED = "cartographer-workflow"


def stages_of(workflow: dict) -> list[str]:
    return [stage["name"] for stage in workflow["stages"]]


def distinctive(workflow: dict, other: dict) -> set[str]:
    """The stages one definition declares and the other does not.

    The judging stage is named by the harness rather than by either
    definition, so it is common to both and cannot tell them apart; what can
    is exactly this set.
    """
    return set(stages_of(workflow)) - set(stages_of(other))


def restrictions_of(workflow: dict) -> list[tuple[str, str]]:
    return [(stage["name"], prefix)
            for stage in workflow["stages"]
            for prefix in stage.get("may_not_create", [])]


def test_the_two_definitions_this_module_builds_can_be_told_apart():
    """The derivations above are load-bearing; an accidental overlap would
    make every "it ran the one it was told to" assertion vacuous."""
    assert SELECTED["name"] != CONFIGURED["name"]
    assert distinctive(SELECTED, CONFIGURED)
    assert distinctive(CONFIGURED, SELECTED)
    assert restrictions_of(SELECTED) and restrictions_of(CONFIGURED)
    assert not set(restrictions_of(SELECTED)) & set(restrictions_of(CONFIGURED))
    assert UNDEFINED not in (SELECTED["name"], CONFIGURED["name"])


# --------------------------------------------------------------------------
# The target repository, the harness root, and the fake runner
# --------------------------------------------------------------------------


STORY = """\
story:
  id: {story_id}
  title: Sample story for workflow selection tests
  description: |
    A stand-in story used to drive the coordinator deterministically against
    a fake runner.
{workflow_line}
tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior
"""

CONFIG = """\
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: {tests_dir}
"""

APP_AT_HEAD = "print('hello')\n"


def story_text(declared: str | None = None, story_id: str = STORY_ID) -> str:
    """The story artifact, naming a workflow or naming none.

    Naming none is the compatibility shape: exactly what every artifact
    written before this field existed carries.
    """
    line = f"  workflow: {declared}\n" if declared else ""
    return STORY.format(story_id=story_id, workflow_line=line)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def init_repo(root: Path, message: str = "initial") -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)


def build_target(root: Path, configured: str, declared: str | None) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=configured, tests_dir=TESTS_DIR))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_text(declared))
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / (TESTS_DIR + "test_existing.py"),
          "def test_nothing():\n    assert True\n")
    init_repo(root)
    return root


def build_harness(root: Path, workflows, *, copy=()) -> Path:
    """A harness root carrying every definition it was given, and nothing this
    repository deploys except the rules and schemas.

    Several definitions in one root, because the whole subject is a *choice*
    between them: a root holding one definition can only ever answer the
    question one way.
    """
    for workflow in workflows:
        conftest.materialize_workflow(workflow, root, rules=RULES, copy=copy)
    return root


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs.

    A factory rather than a fixture per case, because several tests below hold
    two configurations side by side — an artifact naming a workflow and the
    same artifact naming none — and each needs its own target.
    """
    made = {}

    def make(*, configured: str = CONFIGURED["name"],
             declared: str | None = None,
             workflows=(SELECTED, CONFIGURED),
             name: str = "case") -> tuple[Path, Path]:
        assert name not in made, f"two environments named {name}"
        harness = build_harness(tmp_path / f"harness-{name}", workflows)
        init_repo(harness, "harness")
        target = build_target(tmp_path / f"target-{name}", configured, declared)
        made[name] = (target, harness)
        return target, harness

    return make


PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

#: A verdict that fails and asks for no retry, which is how a run below is
#: driven into an escalation it can then be resumed from.
FAIL = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior is missing",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": False,
}


class Runner:
    """A fake agent runner that writes whatever the running stage declares.

    It is built from the definitions rather than from a list of stage names,
    so it serves a run under either of them and a stage it has never heard of
    is a stage it writes nothing for — which is what makes "the coordinator
    ran the other workflow" visible as a failure rather than as a quiet pass.
    """

    def __init__(self, target_root: Path, *workflows: dict,
                 verdicts=None, story_id: str = STORY_ID):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.outputs = {stage["name"]: list(stage.get("outputs", []))
                        for workflow in workflows
                        for stage in workflow["stages"]}
        self.verdicts = list(verdicts or [PASS])
        self.calls: list[str] = []
        #: Every prompt the coordinator rendered, so what reached a stage can
        #: be read rather than inferred.
        self.prompts: list[tuple[str, str]] = []

    def _write(self, artifact: str) -> None:
        if artifact == conftest.CHANGED_FILES:
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('call {len(self.calls)}')\n")
            write_json(self.run_dir / artifact,
                       {"modified": ["src/app.py"], "created": [],
                        "deleted": []})
        elif artifact == conftest.TESTER_CHANGED_FILES:
            write_json(self.run_dir / artifact,
                       {"modified": [], "created": [], "deleted": []})
        elif artifact == conftest.TEST_RESULTS:
            write_json(self.run_dir / artifact, {"tests_written": 1})
        elif artifact == conftest.VERIFICATION_RESULT:
            seen = self.calls.count(conftest.VERIFYING_STAGE) - 1
            write_json(self.run_dir / artifact,
                       self.verdicts[min(seen, len(self.verdicts) - 1)])
        else:
            write(self.run_dir / artifact, f"Written for {artifact}.\n")

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        self.prompts.append((stage, prompt))
        # Written exactly as the real runner writes it, so "the refusal left
        # no log" is an observation of a file somebody would otherwise have
        # written rather than of one nothing ever writes.
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"===== stage: {stage} =====\n")
        for artifact in self.outputs.get(stage, []):
            self._write(artifact)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(target: Path, harness: Path, runner: Runner | None = None,
        **kwargs) -> tuple[int, Runner]:
    runner = runner if runner is not None else Runner(target, SELECTED,
                                                      CONFIGURED)
    code = story_coordinator.run_story(STORY_ID, harness, target, runner,
                                       **kwargs)
    return code, runner


def run_dir_of(target: Path) -> Path:
    return target / ".harness" / "runs" / STORY_ID


def state_of(target: Path) -> dict:
    return json.loads(
        (run_dir_of(target) / "state.json").read_text(encoding="utf-8"))


def log_of(target: Path) -> Path:
    return target / ".harness" / "logs" / f"{STORY_ID}.log"


def branches(target: Path) -> set[str]:
    listing = git(target, "branch", "--format=%(refname:short)").stdout
    return {line.strip() for line in listing.splitlines() if line.strip()}


# ==========================================================================
# 1. The declaration: the story schema carries an optional workflow field
# ==========================================================================


def story_schema() -> dict:
    return json.loads(STORY_SCHEMA_PATH.read_text(encoding="utf-8"))


def story_object() -> dict:
    return story_schema()["properties"]["story"]


def test_the_schema_declares_workflow_on_the_story_object_as_a_string():
    declared = story_object()["properties"]["workflow"]
    assert declared["type"] == "string"


def test_the_schema_leaves_workflow_out_of_the_story_objects_required_list():
    """Its control is in the same assertion: the fields that *are* required are
    read from the same list, so an empty or missing list cannot pass this."""
    required = story_object()["required"]
    assert "workflow" not in required
    assert {"id", "title", "description"} <= set(required)


def test_the_schemas_description_says_what_an_absent_workflow_means():
    description = story_object()["properties"]["workflow"]["description"]
    assert "workflows/" in description
    assert "configuration" in description
    lowered = description.lower()
    assert "absen" in lowered or "absent" in lowered


def test_an_artifact_naming_a_workflow_is_read_as_carrying_it():
    """The schema half read through the coordinator's own reader, so the field
    is one the parse keeps rather than one the schema merely permits. Its
    control is the artifact naming none, which reads as carrying none."""
    reading = story_coordinator.read_story(story_text(SELECTED["name"]))
    assert reading.problems == []
    assert reading.parsed["story"]["workflow"] == SELECTED["name"]

    plain = story_coordinator.read_story(story_text())
    assert plain.problems == []
    assert "workflow" not in plain.parsed["story"]


def test_every_committed_story_artifact_still_parses_under_the_schema():
    """The field is optional, so the artifacts written before it existed must
    still validate — which is what makes "no committed artifact was edited"
    a property this story could hold rather than one it had to buy."""
    import schema_validator
    import story_parser

    stories = sorted((REPO_ROOT / ".harness" / "stories").glob("*.yaml"))
    assert stories
    schema = schema_validator.load_schema("story")
    for path in stories:
        parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        assert parsed["story"]["id"], path.name


def _unchanged_by_this_story(rel: str, repo: Path, *,
                             diff_filter: str | None = None) -> bool:
    """Whether a story's own change left `rel` alone, in `repo`.

    Not `git diff HEAD`, which asks whether the working tree is dirty here —
    a question about whoever is working now, answered "clean" for every path
    the moment the coordinator commits the run. The resolution is the shared
    one in `tests/conftest.py`, bounded at both ends, and the repository it is
    pointed at is one the caller constructed: a comparison against a story
    that has *already* run is the only way to check the check.
    """
    return story_diff(
        [rel], validation_file=Path(repo) / conftest.CONSTRUCTED_VALIDATION_REL,
        repo=Path(repo), diff_filter=diff_filter, options=("--stat",),
    ).strip() == ""


def test_no_committed_story_artifact_is_edited_by_a_story_like_this_one(tmp_path):
    """Modifications and deletions only: a story's own run commit adds its own
    artifact under `.harness/stories/`, and an addition was never an edit."""
    rel = ".harness/stories"
    assert _unchanged_by_this_story(
        rel, conftest.constructed_story(tmp_path, respected=[rel],
                                        name="stories-left-alone"),
        diff_filter="MD")
    assert not _unchanged_by_this_story(
        rel, conftest.constructed_story(tmp_path, violated=[rel],
                                        name="stories-rewritten"),
        diff_filter="MD")
    assert _unchanged_by_this_story(
        rel, conftest.constructed_story(tmp_path, violated=[rel],
                                        violation="add", name="stories-added"),
        diff_filter="MD")


@pytest.mark.parametrize("rel", ["workflows/", ".harness/config.yaml"])
def test_the_paths_this_story_declares_out_of_scope_are_left_alone(rel, tmp_path):
    assert _unchanged_by_this_story(
        rel, conftest.constructed_story(tmp_path, respected=[rel],
                                        name=f"kept-{rel.strip('./')}"))
    assert not _unchanged_by_this_story(
        rel, conftest.constructed_story(tmp_path, violated=[rel],
                                        name=f"edited-{rel.strip('./')}"))


# ==========================================================================
# 2. Naming the definitions a harness root holds, and refusing a name
# ==========================================================================


@pytest.fixture
def two_workflow_harness(tmp_path) -> Path:
    return build_harness(tmp_path / "named-harness", (SELECTED, CONFIGURED))


# `workflow_names` and `UnknownWorkflow` are proved in
# `tests/test_harness_config.py`, beside the other proofs about what a harness
# root holds. What is asked here is the thing neither of them can answer alone:
# that the refusal a developer reads is one wording rather than two, whether
# the defect is caught when the artifact is written or when it is run.


# ==========================================================================
# 3. Which workflow a run executes
# ==========================================================================


def test_a_run_executes_the_workflow_its_story_artifact_names(environment):
    """The central criterion. The configuration names the other definition, so
    the stages executed can only have come from the artifact's choice.

    Its control is the next test: the same fixture whose artifact names no
    workflow executes the configured one's stages instead.
    """
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=SELECTED["name"], name="named")

    code, runner = run(target, harness)

    assert code == 0, runner.calls
    assert runner.calls == stages_of(SELECTED)
    assert not set(runner.calls) & distinctive(CONFIGURED, SELECTED)


def test_a_run_whose_artifact_names_no_workflow_executes_the_configured_one(
    environment,
):
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=None, name="unnamed")

    code, runner = run(target, harness)

    assert code == 0, runner.calls
    assert runner.calls == stages_of(CONFIGURED)
    assert not set(runner.calls) & distinctive(SELECTED, CONFIGURED)


def test_the_stages_executed_are_the_named_workflows_own_order(environment):
    """Order, not membership: the call list is compared against the stage list
    the loaded definition declares, in the order it declares them."""
    target, harness = environment(declared=SELECTED["name"], name="order")

    _, runner = run(target, harness)

    assert runner.calls == [stage["name"] for stage in SELECTED["stages"]]


def test_the_prompt_each_stage_received_carries_the_named_workflows_stages(
    environment,
):
    """A second, independent reading of which definition was loaded: the
    context the coordinator assembled lists the workflow's stages, and a run
    under the other definition would list the other's."""
    target, harness = environment(declared=SELECTED["name"], name="prompts")

    _, runner = run(target, harness)

    for stage, prompt in runner.prompts:
        for name in stages_of(SELECTED):
            assert name in prompt, (stage, name)
        for name in distinctive(CONFIGURED, SELECTED):
            assert name not in prompt, (stage, name)


def test_the_configured_default_is_still_resolved_through_the_same_expression():
    """The story's constraint, and the reason the standing proof that the
    `workflow` config key is obeyed still bites: the default is spelled as it
    always was rather than moved behind a helper."""
    source = COORDINATOR_SOURCE.read_text(encoding="utf-8")
    assert 'config.get("workflow", "story-workflow")' in source


# --------------------------------------------------------------------------
# A name with no definition is refused at pre-flight
# --------------------------------------------------------------------------


def test_a_run_whose_artifact_names_an_undefined_workflow_is_refused(
    environment, capsys,
):
    target, harness = environment(declared=UNDEFINED, name="undefined")

    code, runner = run(target, harness)

    assert code == 1
    refusal = capsys.readouterr().err
    assert UNDEFINED in refusal
    for name in (SELECTED["name"], CONFIGURED["name"]):
        assert name in refusal, name
    assert runner.calls == []


def test_that_refusal_leaves_no_run_directory_state_log_branch_or_invocation(
    environment,
):
    """Read off the refused target's tree, as the story asks, rather than off
    the exit status. Its control is the next test, which makes the same five
    observations of the same fixture naming a defined workflow."""
    target, harness = environment(declared=UNDEFINED, name="undefined-tree")
    before = branches(target)

    code, runner = run(target, harness)

    assert code == 1
    assert not run_dir_of(target).exists()
    assert not (run_dir_of(target) / "state.json").exists()
    assert not log_of(target).exists()
    assert branches(target) == before
    assert runner.calls == []


def test_the_same_fixture_naming_a_defined_workflow_creates_all_five(
    environment,
):
    """The control the absences above need."""
    target, harness = environment(declared=SELECTED["name"], name="defined-tree")
    before = branches(target)

    code, runner = run(target, harness)

    assert code == 0, runner.calls
    assert run_dir_of(target).is_dir()
    assert state_of(target)["status"] == "completed"
    assert log_of(target).is_file()
    assert branches(target) - before == {f"story/{STORY_ID}"}
    assert runner.calls == stages_of(SELECTED)


def test_plan_time_and_pre_flight_report_an_undefined_workflow_identically(
    environment, capsys, two_workflow_harness,
):
    """One defect, one wording. The plan-time problem is composed by
    `plan_validation`, the pre-flight one printed by the coordinator, and the
    sentence naming the defect has to be the same text in both."""
    target, harness = environment(declared=UNDEFINED, name="one-wording")

    run(target, harness)
    refusal = capsys.readouterr().err

    at_plan_time = plan_validation.workflow_problems(
        {"story": {"workflow": UNDEFINED}}, harness, UNDEFINED)
    assert at_plan_time
    for problem in at_plan_time:
        assert problem in refusal, problem


# ==========================================================================
# 4. What the run records, and what a resume loads
# ==========================================================================


def test_state_json_records_the_workflow_the_run_loaded(environment):
    target, harness = environment(declared=SELECTED["name"], name="recorded")

    code, _ = run(target, harness)

    assert code == 0
    assert state_of(target)["workflow"] == SELECTED["name"]


def test_a_run_under_the_configured_default_records_that_name_too(environment):
    """Not only the artifact's choice: what a resume needs is the name the run
    loaded, whichever of the three places it came from."""
    target, harness = environment(configured=CONFIGURED["name"], declared=None,
                                  name="recorded-default")

    run(target, harness)

    assert state_of(target)["workflow"] == CONFIGURED["name"]


def test_a_state_file_written_before_the_field_existed_still_loads(tmp_path):
    """Its control sits in the same test: a state file that *does* record a
    workflow reads it back, so "no recorded workflow" is a fact about the file
    rather than a reader that always answers empty."""
    run_dir = tmp_path / "old-run"
    run_dir.mkdir()
    pre_story = {"story_id": STORY_ID, "branch": f"story/{STORY_ID}",
                 "status": "escalated", "current_stage": "drafting"}
    write_json(run_dir / "state.json", pre_story)

    loaded = story_coordinator.load_state(run_dir)
    assert loaded is not None
    assert loaded.workflow == ""

    write_json(run_dir / "state.json",
               {**pre_story, "workflow": SELECTED["name"]})
    assert story_coordinator.load_state(run_dir).workflow == SELECTED["name"]


def test_a_resumed_run_loads_the_workflow_its_state_recorded(environment):
    """Amending the artifact between two entries of one run must not change
    what the second entry runs under: the run's own record decides.

    The amendment is also what clears the resume guard, which refuses a resume
    when nothing has changed since the escalation — so the run being resumed
    at all is evidence the artifact really was amended.
    """
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=SELECTED["name"], name="resume")
    escalating = Runner(target, SELECTED, CONFIGURED, verdicts=[FAIL])
    code, _ = run(target, harness, escalating)
    assert code == 2, escalating.calls
    assert state_of(target)["status"] == "escalated"
    assert state_of(target)["workflow"] == SELECTED["name"]

    write(target / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_text(CONFIGURED["name"]))
    git(target, "add", "-A")
    git(target, "commit", "-q", "--allow-empty", "-m", "name another workflow")

    resumed = Runner(target, SELECTED, CONFIGURED)
    code, _ = run(target, harness, resumed)

    assert resumed.calls, "the resume ran no stage at all"
    assert set(resumed.calls) <= set(stages_of(SELECTED))
    assert not set(resumed.calls) & distinctive(CONFIGURED, SELECTED)
    assert state_of(target)["workflow"] == SELECTED["name"]


def test_a_fresh_run_of_the_amended_artifact_does_load_the_amended_workflow(
    environment,
):
    """The control for the resume above: with no run to resume, the same
    amended artifact selects the workflow it names, so the resume's answer
    came from the record rather than from the artifact being ignored."""
    target, harness = environment(configured=SELECTED["name"],
                                  declared=CONFIGURED["name"], name="fresh")

    code, runner = run(target, harness)

    assert code == 0, runner.calls
    assert runner.calls == stages_of(CONFIGURED)


# ==========================================================================
# 5. Every pre-flight the coordinator already made runs against the selection
# ==========================================================================


def defective(name: str, mutate) -> dict:
    """A probe definition derived from the sound one by mutating exactly the
    declaration the case is about.

    Everything else is what the sound definition declares, so the refusal a
    case observes is the refusal that one mutated declaration caused.
    """
    probe = json.loads(json.dumps(runnable_workflow(
        name, "drafting", "checking", SELECTED_PREFIX)))
    mutate(probe)
    return probe


def _unroutable(probe: dict) -> None:
    judging = next(stage for stage in probe["stages"] if "on_failure" in stage)
    for route in judging["on_failure"]["retry_routing"].values():
        route["stage"] = UNDEFINED


def _uncountable_self_route_budget(probe: dict) -> None:
    probe["stages"][0]["max_self_routes"] = "two"


def _unenterable_correction_pass(probe: dict) -> None:
    judging = next(stage for stage in probe["stages"] if "on_failure" in stage)
    judging["correction_pass"] = {"stage": UNDEFINED,
                                  "result": "correction-pass-result.json"}


def _unusable_cost_ceiling(probe: dict) -> None:
    probe["max_run_cost_usd"] = "a lot"


def _unresolvable_token(probe: dict) -> None:
    probe["stages"][0]["may_not_create"] = ["{{no_such_setting}}"]


#: One case per pre-flight the coordinator already performed, each named by the
#: defect it plants and each carrying a fragment of the refusal that defect
#: produces. The fragments are the defects' own vocabulary rather than whole
#: sentences, so a reworded refusal is still recognised while a *different*
#: refusal is not.
DEFECTIVE = {
    "retry-routing": (_unroutable, "route"),
    "self-route-budget": (_uncountable_self_route_budget, "max_self_routes"),
    "correction-pass-entry": (_unenterable_correction_pass, "correction_pass"),
    "cost-ceiling": (_unusable_cost_ceiling, "max_run_cost_usd"),
    "configuration-token": (_unresolvable_token, "no_such_setting"),
}


@pytest.mark.parametrize("case", sorted(DEFECTIVE))
def test_each_existing_pre_flight_runs_against_the_selected_workflow(
    case, environment, capsys,
):
    """The defective definition is the one the *artifact* names while the
    configuration names the sound one, so a refusal can only mean the
    pre-flight ran against what the selection loaded."""
    mutate, fragment = DEFECTIVE[case]
    probe = defective(f"{case}-workflow", mutate)
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=probe["name"],
                                  workflows=(CONFIGURED, probe),
                                  name=f"defective-{case}")
    before = branches(target)

    code, runner = run(target, harness)

    assert code == 1
    assert fragment in capsys.readouterr().err
    assert runner.calls == []
    assert not run_dir_of(target).exists()
    assert not log_of(target).exists()
    assert branches(target) == before


@pytest.mark.parametrize("case", sorted(DEFECTIVE))
def test_the_same_defect_in_the_configured_workflow_alone_does_not_refuse(
    case, environment,
):
    """The control for the family above: the defective definition is present in
    the harness root but is *not* the one selected, and the run completes. So
    each refusal above is the selection's doing rather than the mere presence
    of a bad file."""
    mutate, _ = DEFECTIVE[case]
    probe = defective(f"{case}-workflow", mutate)
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=SELECTED["name"],
                                  workflows=(SELECTED, CONFIGURED, probe),
                                  name=f"unselected-{case}")

    code, runner = run(target, harness)

    assert code == 0, runner.calls
    assert runner.calls == stages_of(SELECTED)


def test_a_start_stage_is_checked_against_the_selected_workflow(environment):
    """A --stage the selected workflow does not define is refused, and one it
    does define is accepted — even though the *configured* workflow defines
    neither the first nor the second."""
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=SELECTED["name"], name="start-stage")
    foreign = sorted(distinctive(CONFIGURED, SELECTED))[0]

    code, runner = run(target, harness, start_stage=foreign)

    assert code == 1
    assert runner.calls == []
    assert not run_dir_of(target).exists()

    own = sorted(distinctive(SELECTED, CONFIGURED))[0]
    code, runner = run(target, harness, start_stage=own)
    assert code == 0, runner.calls
    assert runner.calls[0] == own


def test_a_stage_exception_is_checked_against_the_selected_workflow(
    environment, capsys,
):
    """The story cross-check moved below the workflow load, so it too asks the
    definition that was selected. A grant naming a stage only the *configured*
    workflow defines is refused."""
    target, harness = environment(configured=CONFIGURED["name"],
                                  declared=SELECTED["name"], name="exception")
    foreign = sorted(distinctive(CONFIGURED, SELECTED))[0]
    story = target / ".harness" / "stories" / f"{STORY_ID}.yaml"
    write(story, story_text(SELECTED["name"])
          + "\nstage_exceptions:\n"
          + f"  - stage: {foreign}\n"
          + f"    create: {SELECTED_PREFIX}\n"
          + "    reason: the story's own deliverable needs it\n")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "grant a stage exception")

    code, runner = run(target, harness)

    assert code == 1
    assert foreign in capsys.readouterr().err
    assert runner.calls == []
    assert not run_dir_of(target).exists()


# ==========================================================================
# 6. plan_validation's two checks on a declared workflow
# ==========================================================================


def test_an_artifact_naming_no_workflow_has_nothing_to_report(
    two_workflow_harness,
):
    """Its control is the next two tests, where the same function over the same
    root does report both things that can be wrong with a name."""
    assert plan_validation.workflow_problems(
        story_coordinator.read_story(story_text()).parsed,
        two_workflow_harness, CONFIGURED["name"]) == []


def test_an_artifact_naming_a_workflow_with_no_definition_is_reported(
    two_workflow_harness,
):
    problems = plan_validation.workflow_problems(
        story_coordinator.read_story(story_text(UNDEFINED)).parsed,
        two_workflow_harness, UNDEFINED)

    assert problems
    reported = " ".join(problems)
    assert UNDEFINED in reported
    for name in harness_config.workflow_names(two_workflow_harness):
        assert name in reported, name


def test_an_artifact_disagreeing_with_the_session_is_reported_with_both_names(
    two_workflow_harness,
):
    problems = plan_validation.workflow_problems(
        story_coordinator.read_story(story_text(SELECTED["name"])).parsed,
        two_workflow_harness, CONFIGURED["name"])

    assert problems
    reported = " ".join(problems)
    assert SELECTED["name"] in reported
    assert CONFIGURED["name"] in reported


def test_an_artifact_naming_the_session_s_own_workflow_is_not_reported(
    two_workflow_harness,
):
    assert plan_validation.workflow_problems(
        story_coordinator.read_story(story_text(SELECTED["name"])).parsed,
        two_workflow_harness, SELECTED["name"]) == []


def test_artifact_problems_requires_the_root_and_the_selection(
    two_workflow_harness, tmp_path,
):
    """Neither is defaulted: defaulting the root would resolve the definitions
    against whatever directory the process stands in, and defaulting the
    selection would let a caller check agreement against a workflow it never
    rendered. Shown by calling with them and by the signature carrying no
    default for either."""
    import inspect

    signature = inspect.signature(plan_validation.artifact_problems)
    for name in ("harness_root", "selected"):
        parameter = signature.parameters[name]
        assert parameter.default is inspect.Parameter.empty, name

    path = tmp_path / f"{STORY_ID}.yaml"
    path.write_text(story_text(SELECTED["name"]), encoding="utf-8")
    stages = SELECTED["stages"]
    assert plan_validation.artifact_problems(
        [path], stages, tmp_path, two_workflow_harness,
        SELECTED["name"]) == {}
    assert plan_validation.artifact_problems(
        [path], stages, tmp_path, two_workflow_harness,
        CONFIGURED["name"])[path]


# ==========================================================================
# 7. l5-plan: --workflow selects what the planner is rendered against
# ==========================================================================


PLANNING_CONFIG = """\
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: tests/
"""

#: The prompts `l5-plan` needs from a harness root beyond the stage templates
#: `materialize_workflow` writes: the planner template itself and the shared
#: prose partial it resolves. Both are this repository's own artifacts — the
#: planner template is a subject of the assertions below rather than an input
#: to them — so they are copied rather than rebuilt.
PLANNER_PROMPTS = ("planner.md", "prose-layer.md")


@pytest.fixture
def planning_harness(tmp_path) -> Path:
    """A harness root a real `l5-plan` can run out of.

    `scripts/` and `orchestration/` are copied because the entry point
    resolves its own harness root from its own location, and a symlink would
    resolve straight back to this repository — which ships neither definition
    built here and would undo the whole point of building them.
    """
    root = build_harness(tmp_path / "planning-harness",
                         (SELECTED, CONFIGURED),
                         copy=("orchestration", "scripts"))
    for name in PLANNER_PROMPTS:
        (root / "prompts" / name).write_text(
            (HARNESS_ROOT / "prompts" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return root


@pytest.fixture
def planning(tmp_path) -> Planning:
    """A throwaway target with a stub `claude` on PATH and a bare origin.

    The stub reads none of the prompt it is handed and runs no git command, so
    a commit that exists afterwards was made by `l5-plan`, and a prompt read
    back out of its log is the prompt the script rendered.
    """
    root = tmp_path / "plan-target"
    (root / ".harness" / "stories").mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          PLANNING_CONFIG.format(workflow=CONFIGURED["name"]))
    write(root / "README.md", "target\n")
    for command in (
        ["git", "init", "-q", "-b", DEFAULT_BRANCH],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(command, cwd=root, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    planning = Planning(root, bin_dir, root / ".harness" / "stories",
                        tmp_path / "session.json")
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    return planning


def plan(planning: Planning, harness: Path, *args: str,
         **stub) -> subprocess.CompletedProcess:
    """Run the real `scripts/l5-plan` out of a harness root the test built."""
    return subprocess.run(
        [sys.executable, str(harness / "scripts" / "l5-plan"), *args],
        cwd=planning.root, env=planning.env(**stub),
        capture_output=True, text=True,
    )


def session_prompt(planning: Planning) -> str:
    argv = planning.session()["argv"]
    return argv[argv.index("--append-system-prompt") + 1]


def carries(prompt: str, workflow: dict) -> bool:
    """Whether every stage a definition declares reaches a rendered prompt."""
    return all(name in prompt for name in stages_of(workflow))


def test_l5_plan_renders_the_planner_against_the_workflow_the_argument_names(
    planning, planning_harness,
):
    """Observed through a create restriction only the named workflow declares,
    which is what the story asks for: the configuration names the other one, so
    the restriction in the prompt can only have come from the argument.

    Its control is the next test, which runs the same session without the
    argument and finds the configured workflow's facts instead.
    """
    result = plan(planning, planning_harness, "--workflow", SELECTED["name"],
                  "a story request")

    assert result.returncode == 0, result.stderr
    prompt = session_prompt(planning)
    assert carries(prompt, SELECTED)
    for stage, prefix in restrictions_of(SELECTED):
        assert any(stage in line and prefix in line
                   for line in prompt.splitlines()), (stage, prefix)
    for name in distinctive(CONFIGURED, SELECTED):
        assert name not in prompt, name
    assert CONFIGURED_PREFIX not in prompt


def test_l5_plan_without_the_argument_renders_the_configured_workflow(
    planning, planning_harness,
):
    result = plan(planning, planning_harness, "a story request")

    assert result.returncode == 0, result.stderr
    prompt = session_prompt(planning)
    assert carries(prompt, CONFIGURED)
    for stage, prefix in restrictions_of(CONFIGURED):
        assert any(stage in line and prefix in line
                   for line in prompt.splitlines()), (stage, prefix)
    for name in distinctive(SELECTED, CONFIGURED):
        assert name not in prompt, name
    assert SELECTED_PREFIX not in prompt


def test_the_planner_prompt_carries_the_name_of_the_selected_workflow(
    planning, planning_harness,
):
    """Resolved from the same argument the injected facts are resolved from,
    so what the prompt says it was started for and what it was rendered
    against cannot differ. Its control is the unnamed session, which carries
    the configured name and not the selected one."""
    plan(planning, planning_harness, "--workflow", SELECTED["name"],
         "a story request")
    assert SELECTED["name"] in session_prompt(planning)
    assert CONFIGURED["name"] not in session_prompt(planning)

    plan(planning, planning_harness, "a story request")
    assert CONFIGURED["name"] in session_prompt(planning)
    assert SELECTED["name"] not in session_prompt(planning)


def planner_template() -> str:
    """The planner template this repository ships, which is the subject of the
    three assertions below rather than an input to them."""
    return (HARNESS_ROOT / "prompts" / "planner.md").read_text(encoding="utf-8")


def test_the_planner_template_carries_the_workflow_name_placeholder():
    assert WORKFLOW_NAME_PLACEHOLDER in planner_template()


def test_the_planner_template_asks_for_the_name_to_be_recorded():
    """An injected name nobody is asked to write down would reach the session
    and stop there; what makes the refusal at the end of the session fair is
    that the planner was told to record what it was given."""
    template = planner_template()
    assert "story.workflow" in template
    assert "workflow:" in template


def test_the_planner_template_names_no_workflow_of_its_own():
    """The rule the stage names are held to, applied to the definition's own
    name: with the placeholders stripped, neither definition this module built
    nor the one this repository deploys is written into the prose.

    Its control is the render beside it, where the injected name does appear —
    so "the name is not in the template" cannot be satisfied by a check that
    has stopped seeing names at all.
    """
    stripped = PLACEHOLDER.sub("", planner_template())
    for name in (SELECTED["name"], CONFIGURED["name"],
                 conftest.shipped_workflow(HARNESS_ROOT,
                                           "story-workflow")["name"]):
        assert name not in stripped, name

    import context_assembler
    rendered = context_assembler.render(planner_template(),
                                        {"workflow_name": SELECTED["name"]})
    assert SELECTED["name"] in rendered


@pytest.mark.parametrize("argv", [
    ("--workflow", SELECTED["name"], "a story request"),
    ("--workflow", SELECTED["name"], "--base", DEFAULT_BRANCH,
     "a story request"),
    ("--base", DEFAULT_BRANCH, "--workflow", SELECTED["name"],
     "a story request"),
])
def test_the_argument_is_parsed_ahead_of_the_request_in_either_order(
    argv, planning, planning_harness,
):
    """`--workflow` sits beside `--base`, and neither option's presence
    decides whether the other can be given. Everything after them is the
    request, passed to the session unchanged."""
    result = plan(planning, planning_harness, *argv)

    assert result.returncode == 0, result.stderr
    assert planning.session()["argv"][-1] == "Story request: a story request"
    assert carries(session_prompt(planning), SELECTED)


def test_l5_plan_given_a_workflow_with_no_definition_refuses_before_the_session(
    planning, planning_harness,
):
    """No session, so nothing was planned against facts that were never
    resolved. Its control is the same invocation naming a defined workflow,
    which does start one."""
    result = plan(planning, planning_harness, "--workflow", UNDEFINED,
                  "a story request")

    assert result.returncode != 0
    assert UNDEFINED in result.stderr
    for name in (SELECTED["name"], CONFIGURED["name"]):
        assert name in result.stderr, name
    assert not planning.log.is_file(), "l5-plan started a session"
    assert planning.head() == planning.git(
        "rev-parse", DEFAULT_BRANCH).stdout.strip()

    assert plan(planning, planning_harness, "--workflow", SELECTED["name"],
                "a story request").returncode == 0
    assert planning.log.is_file()


# ==========================================================================
# 8. l5-plan: what the session's artifact may name
# ==========================================================================


PLANNED_ID = "story-900"


def planned(declared: str | None) -> str:
    return story_text(declared, story_id=PLANNED_ID)


def artifact_path(planning: Planning) -> Path:
    return planning.stories_dir / f"{PLANNED_ID}.yaml"


def relative_artifact() -> str:
    return f".harness/stories/{PLANNED_ID}.yaml"


def untracked(planning: Planning) -> list[str]:
    return [line[3:] for line in planning.status().splitlines()
            if line.startswith("??")]


def test_a_session_whose_artifact_names_the_workflow_it_ran_under_commits(
    planning, planning_harness,
):
    """The control for both refusals below: the same session, the same
    artifact, and the only difference is the name it records."""
    head = planning.head()
    result = plan(planning, planning_harness, "--workflow", SELECTED["name"],
                  "a story request",
                  L5_STUB_WRITE=writes((relative_artifact(),
                                        planned(SELECTED["name"]))))

    assert result.returncode == 0, result.stderr + result.stdout
    assert planning.head() != head
    assert untracked(planning) == []


def test_a_session_whose_artifact_names_no_definition_is_refused(
    planning, planning_harness,
):
    head = planning.head()
    result = plan(planning, planning_harness, "--workflow", SELECTED["name"],
                  "a story request",
                  L5_STUB_WRITE=writes((relative_artifact(),
                                        planned(UNDEFINED))))

    assert result.returncode != 0
    assert UNDEFINED in result.stderr
    for name in (SELECTED["name"], CONFIGURED["name"]):
        assert name in result.stderr, name
    # The artifact is the developer's: left where the session wrote it,
    # uncommitted, which is the state they can repair and re-run from.
    assert artifact_path(planning).is_file()
    assert artifact_path(planning).read_text(encoding="utf-8") == planned(
        UNDEFINED)
    assert planning.head() == head
    assert relative_artifact() in untracked(planning)


def test_a_session_whose_artifact_names_another_workflow_is_refused_with_both(
    planning, planning_harness,
):
    """The refusal names the workflow the artifact declared and the one the
    session was rendered against, because a developer told only one of them
    cannot tell which of the two to change."""
    head = planning.head()
    result = plan(planning, planning_harness, "--workflow", SELECTED["name"],
                  "a story request",
                  L5_STUB_WRITE=writes((relative_artifact(),
                                        planned(CONFIGURED["name"]))))

    assert result.returncode != 0
    assert SELECTED["name"] in result.stderr
    assert CONFIGURED["name"] in result.stderr
    assert artifact_path(planning).is_file()
    assert planning.head() == head
    assert relative_artifact() in untracked(planning)


def test_an_unnamed_session_refuses_an_artifact_naming_other_than_the_default(
    planning, planning_harness,
):
    """With no `--workflow`, the session was rendered against the configured
    workflow, so that is what the artifact must name. Its control is the same
    session whose artifact names the configured one, which commits."""
    head = planning.head()
    result = plan(planning, planning_harness, "a story request",
                  L5_STUB_WRITE=writes((relative_artifact(),
                                        planned(SELECTED["name"]))))
    assert result.returncode != 0
    assert planning.head() == head

    # The refused artifact is still in the working tree, and `l5-plan` commits
    # what a session *added* — so the control starts from the state the first
    # session started from rather than from the wreckage it left.
    artifact_path(planning).unlink()
    assert plan(planning, planning_harness, "a story request",
                L5_STUB_WRITE=writes((relative_artifact(),
                                      planned(CONFIGURED["name"])))
                ).returncode == 0
    assert planning.head() != head


def test_an_artifact_naming_no_workflow_is_committed_by_any_session(
    planning, planning_harness,
):
    """The compatibility case at plan time: an artifact that names nothing runs
    the configured default, so no session has grounds to refuse it."""
    head = planning.head()
    result = plan(planning, planning_harness, "--workflow", SELECTED["name"],
                  "a story request",
                  L5_STUB_WRITE=writes((relative_artifact(), planned(None))))

    assert result.returncode == 0, result.stderr + result.stdout
    assert planning.head() != head
