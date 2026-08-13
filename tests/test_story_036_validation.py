"""Independent validation for story-036: a stage that failed mechanically runs
again, in place.

The subject is a *routing decision*, so almost nothing here is asserted from
source. Target repositories are built under tmp_path, a fake agent runner is
told which invocation of which stage fails mechanically — its process dies, it
skips a required output, or it leaves one at the run root untouched — and what
the coordinator does with that is whatever the run does.

Three properties carry the story, and each is held against a *stage* rather
than argued:

  * the compatibility property — a stage declaring no `max_self_routes`
    escalates on all three mechanical failures exactly as it did before, with
    `retry_count` untouched and nothing in the run directory named for a
    self-route;
  * the mechanism — the one stage the shipped workflow grants a budget re-runs
    in place once and the run completes, and a second consecutive mechanical
    failure at that same stage escalates naming both the failure and the
    exhausted budget;
  * the two-budget separation — a self-route spends no attempt: `retry_count`,
    `attempts/attempt-N/`, `retry-history.json` and every attempt number in a
    rendered prompt filename are compared against a control run that differs
    only in not failing mechanically at all.

Every absence asserted here carries a demonstration that it can fail:

  * "a budgetless stage leaves no self-route artifact, no try-suffixed prompt
    and no self-routed event" sits beside the identical failure at the budgeted
    stage, which leaves all three;
  * "the escalation reason says nothing about a budget" sits beside the same
    stage's *exhausted* escalation, and the two are required to differ by
    exactly the budget clause — the budgetless reason is produced by the same
    run under a probe workflow with that one key removed, so the comparison is
    against behaviour rather than against a literal;
  * "a stage that did not self-route renders no evidence in its prompt" is read
    off runs in which some other invocation's prompt *does* carry it;
  * "the crash evidence names no output" sits beside the missing-output
    evidence from the same failure class family, which names one;
  * "the stage baseline was not re-captured" sits beside a fresh capture taken
    from the same tree, which does hold the file the crashed invocation wrote;
  * "the record satisfies the new schema" sits beside the same record with one
    required field dropped, which the same validator rejects;
  * "a malformed budget leaves no run directory, no state, no log and no
    branch" sits beside the same probe run with a sound budget, which creates
    all four, and beside a mutant whose pre-flight answer is replaced, which
    stops refusing;
  * "no self-route reads or writes max_retries" sits beside the same scan over
    a copy of that source with the name planted in it.

No stage name, artifact name or budget value is written here as a literal
where the point is that the coordinator writes none: all three come off the
loaded workflow definition, the way the coordinator reads them.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns
it to the documenter, the stage that runs after this one.

Nothing here invokes a model. Every run goes through a fake runner, and the
`no_model` guard below turns the one call that would reach one into a failure.
"""
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import BASELINE, load_mutant, repository_file_at

# The suite target — a real module under a real pytest suite — and the helpers
# that drive edits into it are story-017's. Reused rather than copied so a
# regression in either reddens both files.
from test_story_017_validation import (  # noqa: F401 - fixtures used by name
    APP_ADDITIVE,
    TEST_APP_AT_HEAD,
    ADDED_COVERAGE,
    append_to_story,
    configure,
    harness_root,
)
from test_story_017_validation import target as suite_target  # noqa: F401

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import agent_runner  # noqa: E402
import context_assembler  # noqa: E402
import harness_config  # noqa: E402
import schema_validator  # noqa: E402
import story_coordinator  # noqa: E402
from agent_runner import AgentResult  # noqa: E402

REPO_ROOT = HARNESS_ROOT
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")
RULES = harness_config.load_rules(REPO_ROOT)

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"

# --------------------------------------------------------------------------
# Everything about the workflow is read off the workflow
# --------------------------------------------------------------------------

WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")
STAGES = WORKFLOW["stages"]
STAGE_NAMES = [stage["name"] for stage in STAGES]

#: The key this story introduced, named once so every read below agrees.
BUDGET_KEY = "max_self_routes"

BUDGETED_DECLARATIONS = [s for s in STAGES if BUDGET_KEY in s]
BUDGETLESS_DECLARATIONS = [s for s in STAGES if BUDGET_KEY not in s]
BUDGETED = BUDGETED_DECLARATIONS[0]["name"]
BUDGET = BUDGETED_DECLARATIONS[0][BUDGET_KEY]
BUDGETLESS = [s["name"] for s in BUDGETLESS_DECLARATIONS]

VERIFIER = next(s for s in STAGES if "on_failure" in s)
VERIFIER_NAME = VERIFIER["name"]
RETRY_CATEGORY, RETRY_ROUTE = next(
    (category, route["stage"])
    for category, route in VERIFIER["on_failure"]["retry_routing"].items()
)

REVERT_DECLARATION = next(s for s in STAGES if "revert_check" in s)
CLEAN_CLONE_STAGE = next(s for s in STAGES if "clean_clone" in s)

SCHEMA_STEM = "self-route-result"
SCHEMA_PATH = REPO_ROOT / "schemas" / f"{SCHEMA_STEM}.schema.json"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

#: A test command that fails, spelled as an interpreter invocation rather than
#: a shell builtin: the check runs the command directly, not through a shell.
FAILING_TEST_COMMAND = shlex.join([sys.executable, "-c", "raise SystemExit(1)"])


def declaration_of(stage_name: str, workflow: dict | None = None) -> dict:
    return next(s for s in (workflow or WORKFLOW)["stages"]
                if s["name"] == stage_name)


def failing(attempt: int) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"attempt {attempt} did not implement the sample behavior",
            "location": f"src/attempt_{attempt}.py",
            "required_behavior": f"the behavior exists after attempt {attempt}",
        }],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": RETRY_CATEGORY,
    }


GUIDANCE = {
    "current_focus": ["make the sample behavior exist"],
    "preserve_behavior": ["the existing behavior"],
    "retry_scope": ["src/"],
}


def test_the_workflow_this_file_reads_still_has_something_to_say():
    """Every derivation above, stated so a workflow change reddens here first.

    A file whose subject is "one stage declares a budget and the others do
    not" is worth nothing if the declaration moves or disappears, and the
    parametrizations below would quietly shrink to nothing rather than fail.
    """
    assert len(BUDGETED_DECLARATIONS) == 1, BUDGETED_DECLARATIONS
    assert isinstance(BUDGET, int) and not isinstance(BUDGET, bool)
    assert BUDGET >= 1
    assert BUDGETLESS, "no stage declares nothing, so compatibility is untestable"
    assert BUDGETED not in BUDGETLESS
    assert RETRY_ROUTE == BUDGETED, (
        "this file drives a stale output at the budgeted stage through the "
        "verifier's retry route, which has to reach it")
    assert story_coordinator.required_artifacts(declaration_of(BUDGETED))


# --------------------------------------------------------------------------
# No model, for every test in this file
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """Turn the one call that would reach a model into a failure.

    Wrapped rather than replaced, because `subprocess.run` — which every git
    call below goes through — is built on `Popen`; what it refuses is the one
    command that reaches a model.
    """
    real = agent_runner.subprocess.Popen

    def guarded(command, *args, **kwargs):
        first = command[0] if isinstance(command, (list, tuple)) else command
        if str(first).endswith("claude"):
            raise AssertionError("a model was invoked")
        return real(command, *args, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "Popen", guarded)


def test_the_no_model_guard_fires_when_a_model_is_invoked(tmp_path):
    """The control for the guard every other test in this file runs under."""
    with pytest.raises(AssertionError, match="a model was invoked"):
        agent_runner.run_agent("prompt", stage=BUDGETED, cwd=tmp_path,
                               log_path=tmp_path / "agent.log")


# --------------------------------------------------------------------------
# A target repository and a harness root
# --------------------------------------------------------------------------

STORY = """\
story:
  id: story-001
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists
  - existing behavior is preserved

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
project: self-route-target
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: {test_command}
"""


def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def write_json(path: Path, payload) -> str:
    return write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, *, workflow: str = "story-workflow",
                 test_command: str = "echo tests-ok") -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=workflow, test_command=test_command))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", "print('hello')\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    git(root, "branch", "-M", DEFAULT_BRANCH)
    return root


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so a test can hold a subject and its control side by side."""
    def make(name: str, **kwargs) -> Path:
        return build_target(tmp_path / name, **kwargs)
    return make


def probe_harness(tmp_path: Path, name: str, mutate) -> Path:
    """A harness root carrying a workflow this repository does not ship.

    Everything but the workflow is the shipped harness — the same prompts, the
    same schemas, the same rules — so a run against it differs from a run
    against this repository in exactly the declaration under test.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("prompts", "rules", "schemas"):
        (root / directory).symlink_to(REPO_ROOT / directory)
    workflow = json.loads(json.dumps(WORKFLOW))
    workflow["name"] = name
    mutate(workflow)
    (root / "workflows").mkdir()
    write_json(root / "workflows" / f"{name}.json", workflow)
    return root


def without_budget(stage_name: str):
    """A mutation removing one stage's declared budget: the pre-story shape."""
    def mutate(workflow: dict) -> None:
        declaration_of(stage_name, workflow).pop(BUDGET_KEY, None)
    return mutate


def with_budget(stage_name: str, value):
    def mutate(workflow: dict) -> None:
        declaration_of(stage_name, workflow)[BUDGET_KEY] = value
    return mutate


# --------------------------------------------------------------------------
# The fake runner
#
# Driven by a per-stage, per-invocation plan of mechanical failures. Every
# stage writes the artifacts its own declaration in the *loaded* workflow
# requires, never a list written here, unless the plan says otherwise.
# --------------------------------------------------------------------------

OK = "ok"          #: write everything the declaration requires
CRASH = "crash"    #: the agent process dies without completing


def skip(artifact: str) -> tuple:
    """Leave one required output alone: missing if absent, stale if present."""
    return ("skip", artifact)


def _nth(sequence: list, index: int, default):
    if not sequence or index >= len(sequence):
        return default
    return sequence[index]


class Runner:
    """A fake agent runner with a plan of mechanical failures.

    It records, at the entry to every invocation, the self-route count the
    run's own state.json carried — which is how "the count is zero at every
    entry that is not a self-route" is checked as a fact observed during the
    run rather than as a number written here.
    """

    def __init__(self, target_root: Path, plan: dict | None = None,
                 verdicts: list | None = None, workflow: dict | None = None,
                 hooks: dict | None = None, tree: dict | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.plan = plan or {}
        self.verdicts = list(verdicts or [PASS])
        self.stages = (workflow or WORKFLOW)["stages"]
        self.hooks = hooks or {}
        self.tree = tree or {}
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        #: (stage, the self_route_count state.json held at this entry)
        self.counts: list[tuple[str, int]] = []

    def _declaration(self, stage: str) -> dict:
        return next(s for s in self.stages if s["name"] == stage)

    def _fresh(self, artifact: str, stage: str, call: int, verdict: dict) -> None:
        path = self.run_dir / artifact
        if artifact == "verification-result.json":
            write_json(path, verdict)
        elif artifact.endswith("changed-files.json"):
            write_json(path, {"modified": ["src/app.py"], "created": [],
                              "deleted": []})
        elif artifact == "test-results.json":
            write_json(path, {"status": "passed", "tests_written": 1,
                              "tests_run": 1, "tests_passed": 1,
                              "tests_failed": 0, "failures": []})
        else:
            write(path, f"{artifact} written by {stage} call {call}.\n")

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        self.prompts.append((stage, prompt))
        call = self.calls.count(stage)
        if log_path:
            # The real runner writes the stage's log here, and "a refused run
            # wrote no log" is only a claim about the refusal if a run that
            # proceeds does write one.
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{stage} invocation {call}\n")
        self.counts.append((stage, self_route_count_of(self.run_dir)))

        action = _nth(self.plan.get(stage, []), call - 1, OK)
        if action == CRASH:
            edit = _nth(self.tree.get(stage, []), call - 1, None)
            if edit:
                edit(self.target_root)
            return AgentResult(ok=False, result_text="the process died")

        skipped = {action[1]} if isinstance(action, tuple) else set()
        verdict = self.verdicts[min(self.calls.count(VERIFIER_NAME) - 1,
                                    len(self.verdicts) - 1)]
        record = None
        edit = _nth(self.tree.get(stage, []), call - 1, None)
        if edit:
            record = edit(self.target_root)

        for artifact in story_coordinator.required_artifacts(
                self._declaration(stage)):
            if artifact in skipped:
                continue
            if record is not None and artifact.endswith("changed-files.json"):
                write_json(self.run_dir / artifact, record)
                continue
            self._fresh(artifact, stage, call, verdict)

        if stage == VERIFIER_NAME and verdict.get("retry_recommended"):
            write_json(self.run_dir / "retry-guidance.json", GUIDANCE)

        hook = _nth(self.hooks.get(stage, []), call - 1, None)
        if hook:
            hook(self.run_dir)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads(
        (run_dir_of(target_root) / "state.json").read_text(encoding="utf-8"))


def self_route_count_of(run_dir: Path) -> int:
    path = run_dir / "state.json"
    if not path.is_file():
        return 0
    return json.loads(path.read_text(encoding="utf-8")).get("self_route_count", 0)


def history_of(target_root: Path) -> list[dict]:
    return json.loads(
        (run_dir_of(target_root) / "execution-history.json").read_text(
            encoding="utf-8"))


def events_of(target_root: Path, kind: str) -> list[dict]:
    return [entry for entry in history_of(target_root) if entry["event"] == kind]


def escalation_reason(target_root: Path) -> str:
    reason = story_coordinator.escalation_reason(run_dir_of(target_root))
    assert reason, "the run did not escalate, so there is no reason to read"
    return reason


def self_route_records(target_root: Path) -> list[tuple[str, dict]]:
    """Every self-route evidence artifact the run left, by filename."""
    run_dir = run_dir_of(target_root)
    return [(path.name, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(run_dir.glob("self-route-*.json"))]


def try_prompts(target_root: Path) -> list[str]:
    return sorted(p.name for p in run_dir_of(target_root).glob("prompt-*-try-*.md"))


def prompt_names(target_root: Path) -> list[str]:
    return sorted(p.name for p in run_dir_of(target_root).glob("prompt-*.md"))


def attempt_dirs(target_root: Path) -> list[str]:
    archive = run_dir_of(target_root) / "attempts"
    return sorted(p.name for p in archive.glob("*")) if archive.is_dir() else []


def retry_history_of(target_root: Path):
    path = run_dir_of(target_root) / "retry-history.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def drive(target_root: Path, harness: Path, plan: dict | None = None,
          verdicts: list | None = None, workflow: dict | None = None,
          hooks: dict | None = None, tree: dict | None = None,
          start_stage: str | None = None) -> tuple[int, Runner]:
    runner = Runner(target_root, plan, verdicts, workflow, hooks, tree)
    code = story_coordinator.run_story(
        STORY_ID, harness, target_root, runner, start_stage)
    return code, runner


# --------------------------------------------------------------------------
# The three mechanical failures, as plans
#
# Each is a (label, plan-builder) pair: given a stage and which invocation of
# it should fail, the plan that makes exactly that happen, plus the verdicts
# needed to reach that invocation. Written once so the compatibility half and
# the mechanism half are driven by the same three failures.
# --------------------------------------------------------------------------


def first_output_of(stage_name: str) -> str:
    return story_coordinator.required_artifacts(declaration_of(stage_name))[0]


def crash_plan(stage_name: str, times: int) -> dict:
    return {"plan": {stage_name: [CRASH] * times}, "verdicts": [PASS]}


def missing_plan(stage_name: str, times: int) -> dict:
    """The stage's first invocations skip an output that is not there yet."""
    return {"plan": {stage_name: [skip(first_output_of(stage_name))] * times},
            "verdicts": [PASS]}


def stale_plan(stage_name: str, times: int) -> dict:
    """The stage runs once cleanly, a retry brings it back, and it then skips
    an output the run root already holds — which is the stale case."""
    return {
        "plan": {stage_name: [OK] + [skip(first_output_of(stage_name))] * times},
        "verdicts": [failing(1), PASS],
    }


FAILURES = [
    ("agent-process-failed", crash_plan),
    ("missing-required-artifacts", missing_plan),
    ("stale-required-artifacts", stale_plan),
]
FAILURE_IDS = [name for name, _ in FAILURES]


def test_the_three_failure_classes_are_the_three_the_schema_declares():
    """The plans above are named for the schema's enum, so a class added or
    renamed there reddens the parametrizations rather than silently leaving
    one untested."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert sorted(schema["properties"]["failure"]["enum"]) == sorted(FAILURE_IDS)


# --------------------------------------------------------------------------
# Compatibility: a stage that declares no budget escalates exactly as before
# --------------------------------------------------------------------------


def self_route_traces(target_root: Path) -> list[str]:
    """Everything a self-route would have left, as a list of what was found.

    A list rather than three assertions so the same statement can be made of
    the run that is *supposed* to leave them, which is the control.
    """
    found = []
    for name, _ in self_route_records(target_root):
        found.append(f"evidence artifact {name}")
    for name in try_prompts(target_root):
        found.append(f"try-suffixed prompt {name}")
    for entry in events_of(target_root, "self-routed"):
        found.append(f"self-routed event naming {entry.get('stage')}")
    return found


#: The stages a retry runs a second time, which is the only way a required
#: output can be at the run root already and so the only way it can go stale.
#: Read off the verifier's own route rather than written here, so a workflow
#: that reroutes elsewhere narrows or widens the cases below rather than
#: leaving one of them quietly untested.
RERUN_STAGES = STAGE_NAMES[STAGE_NAMES.index(RETRY_ROUTE):
                           STAGE_NAMES.index(VERIFIER_NAME) + 1]

#: Every (stage, failure) pair that a stage declaring no budget can actually
#: reach. Filtered here rather than skipped inside the test: a skip reports as
#: a case that was considered, and a case a run cannot produce was never one.
BUDGETLESS_CASES = [
    (stage, failure, build)
    for stage in BUDGETLESS
    for failure, build in FAILURES
    if failure != "stale-required-artifacts" or stage in RERUN_STAGES
]
BUDGETLESS_IDS = [f"{stage}-{failure}" for stage, failure, _ in BUDGETLESS_CASES]


def test_every_mechanical_failure_is_reachable_at_some_budgetless_stage():
    """Otherwise the filter above could empty a whole failure class out of the
    compatibility parametrization without anything saying so."""
    assert {failure for _, failure, _ in BUDGETLESS_CASES} == set(FAILURE_IDS)


@pytest.mark.parametrize("stage,failure,build", BUDGETLESS_CASES,
                         ids=BUDGETLESS_IDS)
def test_a_stage_declaring_no_budget_escalates_on_every_mechanical_failure(
    make_target, harness_root, stage, failure, build,
):
    """The compatibility property, held against every stage that declares
    nothing and every mechanical failure there is.

    Landing this story changes nothing until a workflow opts in, and that is
    what this says: the run escalates on the *first* failure, the stage is
    invoked once within its attempt, `retry_count` is whatever the run's
    verdicts made it and no self-route moved it, and the run directory holds
    nothing named for a self-route. The control for the three absences is the
    identical failure at the budgeted stage, two sections below, which leaves
    all three.
    """
    target_root = make_target(f"budgetless-{stage}-{failure}")
    spec = build(stage, 1)
    code, runner = drive(target_root, harness_root, **spec)

    assert code == 2
    state = state_of(target_root)
    assert state["status"] == "escalated"
    assert state["current_stage"] == stage
    assert state["self_route_count"] == 0

    # The stage ran once per attempt: it escalated where it failed rather than
    # being given another go.
    attempts = state["retry_count"] + 1
    assert runner.calls.count(stage) == attempts

    assert self_route_traces(target_root) == []
    assert "self-route" not in escalation_reason(target_root)


@pytest.mark.parametrize("failure,build", FAILURES, ids=FAILURE_IDS)
def test_the_budgeted_stage_leaves_every_trace_the_budgetless_ones_do_not(
    make_target, harness_root, failure, build,
):
    """The control for the three absences above.

    Same fixture, same failure, same single occurrence — at the one stage the
    shipped workflow grants a budget. All three traces appear, so a green
    result above cannot mean the reader stopped looking.
    """
    target_root = make_target(f"traced-{failure}")
    code, _ = drive(target_root, harness_root, **build(BUDGETED, 1))

    assert code == 0
    traces = self_route_traces(target_root)
    assert len(traces) == 3 * BUDGET
    assert any(t.startswith("evidence artifact") for t in traces)
    assert any(t.startswith("try-suffixed prompt") for t in traces)
    assert any(t.startswith("self-routed event") for t in traces)


def test_the_reasons_a_budgetless_stage_escalates_with_are_the_pre_story_ones(
    make_target, harness_root,
):
    """The observed reasons, tied to the text the coordinator carried before
    this story rather than to literals written here.

    Read out of the pre-story coordinator through the shared resolution, so
    the comparison survives the commit this run makes. If a reason had been
    reworded while converting its site into a self-route, the phrase would no
    longer be found in both.
    """
    pre_story = repository_file_at(
        "orchestration/story_coordinator.py",
        validation_file=Path(__file__), bound=BASELINE)

    # A stage a retry brings back, so all three failures are reachable at one
    # stage and the three reasons are read off one stage's behaviour.
    stage = next(s for s in BUDGETLESS if s in RERUN_STAGES)
    observed = {}
    for failure, build in FAILURES:
        target_root = make_target(f"pre-story-{failure}")
        assert drive(target_root, harness_root, **build(stage, 1))[0] == 2
        observed[failure] = escalation_reason(target_root)

    phrases = {
        "agent-process-failed": "agent process failed",
        "missing-required-artifacts": "did not produce required artifacts",
        "stale-required-artifacts": "left required artifacts unwritten",
    }
    assert set(phrases) == set(FAILURE_IDS)
    for failure, phrase in phrases.items():
        assert phrase in observed[failure], observed[failure]
        assert phrase in pre_story, phrase
        assert phrase in COORDINATOR_SOURCE, phrase


# --------------------------------------------------------------------------
# The mechanism: one failure re-runs the stage, a second escalates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("failure,build", FAILURES, ids=FAILURE_IDS)
def test_one_mechanical_failure_re_runs_the_budgeted_stage_and_the_run_completes(
    make_target, harness_root, failure, build,
):
    """The stage runs again *in place* — the same stage, no reroute — and the
    run then reaches the end."""
    target_root = make_target(f"self-routed-{failure}")
    code, runner = drive(target_root, harness_root, **build(BUDGETED, 1))

    assert code == 0
    assert state_of(target_root)["status"] == "completed"

    # In place: the invocation after the failure is the same stage, and the
    # run went on to every stage the workflow declares.
    failed_at = runner.calls.index(BUDGETED) if failure != \
        "stale-required-artifacts" else runner.calls.index(BUDGETED, 1)
    assert runner.calls[failed_at + 1] == BUDGETED
    assert runner.calls[-1] == STAGE_NAMES[-1]

    records = self_route_records(target_root)
    assert len(records) == 1
    _, record = records[0]
    assert record["stage"] == BUDGETED
    assert record["failure"] == failure
    assert record["try"] == 1


@pytest.mark.parametrize("failure,build", FAILURES, ids=FAILURE_IDS)
def test_a_consecutive_failure_past_the_budget_escalates(
    make_target, harness_root, failure, build,
):
    """The second consecutive mechanical failure at the same stage ends the
    run, and the reason names both halves.

    The budget clause is not matched against a literal: the same failure is
    driven again under a probe workflow with that one key removed from that
    same stage, and the two reasons are required to differ by exactly the
    clause. That pins the composition — the site's own words, then the budget
    — and it pins the budgetless text to what the stage says without the key.
    """
    subject = make_target(f"exhausted-{failure}")
    code, runner = drive(subject, harness_root, **build(BUDGETED, BUDGET + 1))
    assert code == 2
    assert state_of(subject)["current_stage"] == BUDGETED
    exhausted = escalation_reason(subject)

    # The same run, with the budget removed from that same stage.
    harness = probe_harness(Path(subject).parent, f"nobudget-{failure}",
                            without_budget(BUDGETED))
    workflow = harness_config.load_workflow(harness, f"nobudget-{failure}")
    control = build_target(Path(subject).parent / f"control-{failure}",
                           workflow=workflow["name"])
    spec = build(BUDGETED, BUDGET + 1)
    assert drive(control, harness, workflow=workflow, **spec)[0] == 2
    plain = escalation_reason(control)

    assert exhausted == (
        f"{plain}; {BUDGETED} has exhausted its self-route budget of {BUDGET}")
    assert BUDGETED in exhausted
    assert str(BUDGET) in exhausted
    assert self_route_traces(control) == []

    # It spent the budget before it stopped, rather than escalating early.
    assert len(self_route_records(subject)) == BUDGET
    attempt_calls = runner.calls.count(BUDGETED) - (
        1 if failure == "stale-required-artifacts" else 0)
    assert attempt_calls == BUDGET + 1


# --------------------------------------------------------------------------
# The two budgets are separate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bookkeeping:
    """Everything a retry moves and a self-route must not."""

    retry_count: int
    attempts: tuple
    retry_history: str
    attempt_prompts: tuple


def bookkeeping_of(target_root: Path) -> Bookkeeping:
    history = retry_history_of(target_root)
    return Bookkeeping(
        retry_count=state_of(target_root)["retry_count"],
        attempts=tuple(attempt_dirs(target_root)),
        retry_history=json.dumps(history, sort_keys=True),
        attempt_prompts=tuple(n for n in prompt_names(target_root)
                              if "-try-" not in n),
    )


@pytest.fixture
def paired_runs(make_target, harness_root):
    """One run whose budgeted stage crashes once, and the identical run
    without the crash. Everything else — the verdicts, the retry they force,
    the stages, the target — is the same."""
    verdicts = [failing(1), PASS]
    subject = make_target("paired-subject")
    subject_code, subject_runner = drive(
        subject, harness_root, {BUDGETED: [CRASH]}, verdicts)
    control = make_target("paired-control")
    control_code, control_runner = drive(control, harness_root, {}, verdicts)
    assert subject_code == 0 and control_code == 0
    return subject, control, subject_runner, control_runner


def test_a_self_route_moves_none_of_the_retry_bookkeeping(paired_runs):
    """retry_count, the attempts/ archive, retry-history.json and every
    attempt-numbered prompt filename, compared against a run that differs only
    in not failing mechanically.

    This is the two-budget separation, proven rather than asserted: the two
    runs took a different number of agent invocations and produced identical
    retry bookkeeping.
    """
    subject, control, subject_runner, control_runner = paired_runs
    assert len(subject_runner.calls) == len(control_runner.calls) + 1
    assert bookkeeping_of(subject) == bookkeeping_of(control)
    assert bookkeeping_of(control).retry_count == 1
    assert bookkeeping_of(control).attempts == ("attempt-1",)
    assert retry_history_of(control) is not None


def test_the_same_comparison_reports_a_retry_that_does_move_it(
    make_target, harness_root, paired_runs,
):
    """The control for the assertion above: a run with one more *retry* — not
    one more invocation — differs in all four fields, so a comparison that had
    stopped being able to see a difference would fail here."""
    _, control, _, _ = paired_runs
    more_retries = make_target("paired-two-retries")
    assert drive(more_retries, harness_root,
                 verdicts=[failing(1), failing(2), PASS])[0] == 0
    one = bookkeeping_of(control)
    two = bookkeeping_of(more_retries)
    assert one.retry_count != two.retry_count
    assert one.attempts != two.attempts
    assert one.retry_history != two.retry_history
    assert one.attempt_prompts != two.attempt_prompts


def test_the_self_route_added_only_a_try_suffixed_prompt(paired_runs):
    """On disk: the two runs' prompt filenames differ by the try prompts and
    by nothing else, and no filename claims an attempt the control never
    reached."""
    subject, control, _, _ = paired_runs
    extra = set(prompt_names(subject)) - set(prompt_names(control))
    assert extra == set(try_prompts(subject))
    assert set(prompt_names(control)) - set(prompt_names(subject)) == set()
    assert try_prompts(subject) == [
        f"prompt-{BUDGETED}-attempt-1-try-{n}.md" for n in range(1, BUDGET + 1)]
    assert not try_prompts(control)


def test_a_run_with_no_self_route_writes_exactly_todays_prompt_filenames(
    paired_runs,
):
    """The other half of the filename criterion: the control's names are all
    of the form this harness wrote before the story, and the first invocation
    of the stage that self-routed kept its unsuffixed name."""
    subject, control, _, _ = paired_runs
    for name in prompt_names(control):
        assert name.startswith("prompt-") and name.endswith(".md")
        assert "-try-" not in name
    assert f"prompt-{BUDGETED}-attempt-1.md" in prompt_names(subject)


def test_the_source_of_the_self_route_reads_neither_retry_budget():
    """No self-route reads or writes `max_retries` or `retry_count`.

    Scanned over the self-route section's own source — the decision function
    and the helpers beneath it — rather than argued from the run, because the
    claim is about what the code may consult.
    """
    section = story_coordinator_section()
    assert "max_retries" not in section
    assert "retry_count" not in section


def test_that_scan_reports_a_read_that_was_planted():
    """The control: the same scan over the same source with the read planted."""
    planted = story_coordinator_section().replace(
        "budget = stage.get", "budget = min(state.retry_count, 1) and stage.get", 1)
    assert planted != story_coordinator_section()
    assert "retry_count" in planted


def story_coordinator_section() -> str:
    """The self-route decision's own source: the helper and its callees."""
    from conftest import function_source
    return "\n".join(
        function_source(COORDINATOR_SOURCE, name)
        for name in ("self_route", "self_route_statement", "self_route_result_file",
                     "self_route_problems"))


def test_the_rules_still_hold_exactly_one_definition_of_the_retry_ceiling():
    """`rules/execution-rules.json` is the one home for `max_retries`, and this
    story added no second one."""
    text = (REPO_ROOT / "rules" / "execution-rules.json").read_text(
        encoding="utf-8")
    assert text.count('"max_retries"') == 1
    assert json.loads(text)["max_retries"] == RULES["max_retries"]
    # And no stage declaration carries one, which is how a second definition
    # would most plausibly have arrived alongside the new per-stage budget.
    for stage in STAGES:
        assert "max_retries" not in stage


# --------------------------------------------------------------------------
# The count is live, not cumulative
# --------------------------------------------------------------------------


def test_the_count_is_zero_at_every_entry_that_is_not_a_self_route(
    make_target, harness_root,
):
    """A stage self-routes, succeeds, and the run comes back to it on a retry
    and fails mechanically again — and gets its full budget a second time.

    Read off the state.json each invocation actually found, so this is what
    the run recorded rather than what the loop was expected to do.
    """
    target_root = make_target("reset-on-success")
    code, runner = drive(
        target_root, harness_root,
        {BUDGETED: [CRASH, OK, CRASH, OK]},
        verdicts=[failing(1), PASS])

    assert code == 0
    counts = [count for stage, count in runner.counts if stage == BUDGETED]
    assert counts == [0, 1, 0, 1]
    # Every other stage entered with zero, including the ones that ran after
    # the budgeted stage had already self-routed earlier in the same run.
    assert {count for stage, count in runner.counts if stage != BUDGETED} == {0}

    names = [name for name, _ in self_route_records(target_root)]
    assert names == [
        f"self-route-{BUDGETED}-attempt-1-try-1.json",
        f"self-route-{BUDGETED}-attempt-2-try-1.json",
    ]
    attempts = [record["attempt"] for _, record in self_route_records(target_root)]
    assert attempts == [1, 2]


def test_a_stage_that_self_routed_does_not_spend_another_stages_budget(
    tmp_path, make_target,
):
    """Two budgeted stages in one run: the first spends its budget and
    succeeds, and the second still has its own when it fails.

    Driven under a probe workflow, because the shipped one grants a budget to
    one stage — and "the count is scoped to the stage" cannot be shown with
    only one stage to scope it to.
    """
    other = BUDGETLESS[0]
    harness = probe_harness(tmp_path, "two-budgets", with_budget(other, 1))
    workflow = harness_config.load_workflow(harness, "two-budgets")
    target_root = build_target(tmp_path / "two-budget-target",
                               workflow="two-budgets")

    code, runner = drive(target_root, harness,
                         {BUDGETED: [CRASH], other: [CRASH]},
                         workflow=workflow)

    assert code == 0
    assert runner.calls.count(BUDGETED) == 2
    assert runner.calls.count(other) == 2
    assert [record["stage"] for _, record in self_route_records(target_root)] \
        == sorted([BUDGETED, other], key=STAGE_NAMES.index)


# --------------------------------------------------------------------------
# Resume, and an old state file
# --------------------------------------------------------------------------


def test_an_existing_state_file_without_the_field_still_loads(tmp_path):
    """The new field is defaulted, and a missing value means no self-route."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(run_dir / "state.json", {
        "story_id": STORY_ID, "branch": f"story/{STORY_ID}",
        "current_stage": BUDGETED, "status": "running",
    })
    state = story_coordinator.load_state(run_dir)
    assert state.self_route_count == 0

    # The control: a state file that *does* carry a count loads it, so the
    # zero above is the default rather than the field being ignored.
    write_json(run_dir / "state.json", {
        "story_id": STORY_ID, "branch": f"story/{STORY_ID}",
        "current_stage": BUDGETED, "status": "running", "self_route_count": 2,
    })
    assert story_coordinator.load_state(run_dir).self_route_count == 2


def test_a_resumed_run_starts_the_resumed_stage_with_a_count_of_zero(
    make_target, harness_root,
):
    """A run is driven to an exhausted escalation — leaving a non-zero count
    in state.json — and then resumed. The resumed stage fails mechanically
    once and self-routes, which it could only do from a count of zero."""
    target_root = make_target("resumed")
    assert drive(target_root, harness_root,
                 {BUDGETED: [CRASH] * (BUDGET + 1)})[0] == 2
    escalated = state_of(target_root)
    assert escalated["self_route_count"] == BUDGET
    assert escalated["status"] == "escalated"

    # The story is amended, so the resume guard — which refuses a resume that
    # would reach the same point the same way — has something to see.
    append_to_story(target_root, "\n  - and one more constraint\n")

    code, runner = drive(target_root, harness_root, {BUDGETED: [CRASH]},
                         start_stage=BUDGETED)
    assert code == 0
    assert runner.counts[0] == (BUDGETED, 0)
    assert runner.calls[:2] == [BUDGETED, BUDGETED]


# --------------------------------------------------------------------------
# The evidence artifact
# --------------------------------------------------------------------------


def test_the_schema_exists_is_listed_in_the_manifest_and_is_supported():
    assert SCHEMA_PATH.is_file()
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert SCHEMA_STEM in manifest["schemas"]
    assert SCHEMA_STEM in schema_validator.shipped_schemas(REPO_ROOT)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema_validator.unsupported_keywords(schema) == []


def test_the_schema_appears_in_no_stages_schemas_map():
    """The coordinator writes this record, not an agent, so no stage is asked
    to satisfy it."""
    for stage in STAGES:
        assert SCHEMA_STEM not in stage.get("schemas", {}).values()


@pytest.fixture
def crashed_and_skipped(make_target, harness_root):
    """Two runs at the budgeted stage: one whose process died, one that
    skipped a required output. The two evidence records, side by side."""
    crashed = make_target("evidence-crash")
    assert drive(crashed, harness_root, **crash_plan(BUDGETED, 1))[0] == 0
    skipped = make_target("evidence-missing")
    assert drive(skipped, harness_root, **missing_plan(BUDGETED, 1))[0] == 0
    return (self_route_records(crashed)[0][1],
            self_route_records(skipped)[0][1])


def test_each_evidence_record_satisfies_the_new_schema(crashed_and_skipped):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for record in crashed_and_skipped:
        assert schema_validator.validate(record, schema) == []


def test_the_validator_reports_a_record_with_a_required_field_dropped(
    crashed_and_skipped,
):
    """The control for the assertion above, once per required field: a green
    validation must mean the validator looked."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    record = crashed_and_skipped[0]
    for field in schema["required"]:
        broken = {k: v for k, v in record.items() if k != field}
        assert schema_validator.validate(broken, schema), field


def test_the_crash_evidence_names_no_output_and_says_the_tree_holds_the_work(
    crashed_and_skipped,
):
    """A failed process did not reach the point of declaring what it had done,
    so there is nothing to name — and what the stage most needs to know is
    that the tree already holds whatever the invocation wrote."""
    crashed, _ = crashed_and_skipped
    assert "artifacts" not in crashed
    statement = crashed["statement"]
    assert "exited without completing" in statement
    assert "working tree" in statement
    for artifact in story_coordinator.required_artifacts(
            declaration_of(BUDGETED)):
        assert artifact not in statement


def test_the_missing_output_evidence_does_name_the_output(crashed_and_skipped):
    """The control for the absence above: the other failure class, through the
    same writer, names its artifact in both the record and the statement."""
    _, skipped = crashed_and_skipped
    artifact = first_output_of(BUDGETED)
    assert skipped["artifacts"] == [artifact]
    assert artifact in skipped["statement"]
    assert artifact in skipped["reason"]
    assert "exited without completing" not in skipped["statement"]


def test_the_evidence_says_the_coordinator_wrote_it_and_no_verifier_judged_it(
    crashed_and_skipped,
):
    """A self-routed stage has no agent-authored guidance behind it, and the
    statement is required to say so rather than reading like a verdict."""
    for record in crashed_and_skipped:
        assert "no verifier saw it" in record["statement"]


def test_no_self_route_overwrites_another(make_target, harness_root):
    """Two self-routes in one run, at the same stage, in different attempts:
    two files, two distinct names, and each still holding its own attempt."""
    target_root = make_target("no-overwrite")
    assert drive(target_root, harness_root,
                 {BUDGETED: [CRASH, OK, CRASH, OK]},
                 verdicts=[failing(1), PASS])[0] == 0
    records = self_route_records(target_root)
    assert len({name for name, _ in records}) == len(records) == 2
    assert {record["attempt"] for _, record in records} == {1, 2}


# --------------------------------------------------------------------------
# The re-run's prompt
# --------------------------------------------------------------------------

PLACEHOLDER = "{{self_route_result}}"

#: A phrase out of the crashed-process statement, used where the assertion is
#: that the *text* reached a prompt file. The statement itself carries an em
#: dash, and the evidence reaches a prompt as JSON, where json.dumps escapes
#: it — so a whole-statement substring match would be a claim about escaping
#: rather than about the evidence arriving.
STATEMENT_PHRASE = "exited without completing"


def rendered_placeholder(prompt: str, prompt_file: str) -> str:
    """What the self-route placeholder rendered to in one prompt.

    The anchors come out of the template itself rather than being written
    here, so a reworded section moves both at once and this keeps reading the
    right span.
    """
    template = context_assembler.load_template(REPO_ROOT, prompt_file)
    before, after = template.split(PLACEHOLDER)
    lead = before.rstrip("\n").splitlines()[-1]
    trail = next((line for line in after.splitlines() if line.strip()), None)
    body = prompt.split(lead + "\n", 1)[1]
    span = body.split("\n" + trail, 1)[0] if trail else body
    return span.strip("\n")


def test_every_workflow_stage_template_carries_the_placeholder_once():
    for stage in STAGES:
        template = context_assembler.load_template(REPO_ROOT, stage["prompt"])
        assert template.count(PLACEHOLDER) == 1, stage["name"]


def test_build_context_renders_none_when_nothing_is_passed(tmp_path):
    """The keyword is optional and defaults to None, so a call that omits it
    renders exactly what it rendered before this story."""
    import inspect
    signature = inspect.signature(context_assembler.build_context)
    parameter = signature.parameters["self_route_result"]
    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.fixture
def prompted(make_target, harness_root):
    """A completing run in which the budgeted stage self-routed once."""
    target_root = make_target("prompted")
    code, runner = drive(target_root, harness_root, **crash_plan(BUDGETED, 1))
    assert code == 0
    return target_root, runner


def test_the_re_run_prompt_carries_the_coordinators_own_evidence(prompted):
    """The prompt the re-run was given holds the artifact the run directory
    holds — one thing, said in two places, not two derivations."""
    target_root, runner = prompted
    prompt_file = declaration_of(BUDGETED)["prompt"]
    stage_prompts = [p for stage, p in runner.prompts if stage == BUDGETED]
    rendered = rendered_placeholder(stage_prompts[1], prompt_file)
    _, record = self_route_records(target_root)[0]
    assert json.loads(rendered) == record
    assert json.loads(rendered)["statement"] == record["statement"]
    assert STATEMENT_PHRASE in stage_prompts[1]


def test_the_first_invocations_prompt_carries_none(prompted):
    """The control that the reader above is looking at the right span: the
    same stage's first prompt, in the same run, renders None there."""
    _, runner = prompted
    prompt_file = declaration_of(BUDGETED)["prompt"]
    stage_prompts = [p for stage, p in runner.prompts if stage == BUDGETED]
    assert rendered_placeholder(stage_prompts[0], prompt_file) == "None"


def test_no_later_stage_renders_evidence_for_a_self_route_it_did_not_take(
    prompted,
):
    """Every stage that ran after the budgeted one self-routed earlier in the
    same run renders None, so the evidence does not leak forward."""
    _, runner = prompted
    seen_after = [(stage, prompt) for stage, prompt in runner.prompts
                  if stage != BUDGETED]
    assert seen_after, "no other stage ran, so this would be vacuous"
    for stage, prompt in seen_after:
        prompt_file = declaration_of(stage)["prompt"]
        assert rendered_placeholder(prompt, prompt_file) == "None", stage


def test_the_prompt_the_evidence_reaches_is_the_try_suffixed_file_on_disk(
    prompted,
):
    """The prompt filename and the evidence filename agree, and both name the
    same try — so a reader of the run directory can put them together."""
    target_root, _ = prompted
    name, record = self_route_records(target_root)[0]
    assert name == (f"self-route-{BUDGETED}-attempt-{record['attempt']}"
                    f"-try-{record['try']}.json")
    written = (run_dir_of(target_root) /
               f"prompt-{BUDGETED}-attempt-{record['attempt']}"
               f"-try-{record['try']}.md")
    assert written.is_file()
    assert STATEMENT_PHRASE in written.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The history
# --------------------------------------------------------------------------


def test_every_self_route_appends_one_history_entry_naming_stage_and_reason(
    make_target, harness_root,
):
    """Counting self-routes per stage is a history query, which is only true
    if there is one entry per self-route and it names its stage."""
    target_root = make_target("history")
    assert drive(target_root, harness_root,
                 {BUDGETED: [CRASH, OK, CRASH, OK]},
                 verdicts=[failing(1), PASS])[0] == 0

    entries = events_of(target_root, "self-routed")
    records = self_route_records(target_root)
    assert len(entries) == len(records) == 2
    for entry, (_, record) in zip(entries, records):
        assert entry["stage"] == BUDGETED
        assert entry["retry_stage"] == BUDGETED
        assert record["reason"] in entry["message"]
        assert entry["retry_reason"] == record["reason"]
    assert sum(1 for e in entries if e["stage"] == BUDGETED) == 2


def test_the_history_of_a_self_routing_run_satisfies_the_history_schema(
    make_target, harness_root,
):
    target_root = make_target("history-schema")
    assert drive(target_root, harness_root, **crash_plan(BUDGETED, 1))[0] == 0
    schema = schema_validator.load_schema("execution-history", REPO_ROOT)
    assert schema_validator.validate(history_of(target_root), schema) == []


def test_the_pre_story_history_schema_rejects_that_same_history(
    make_target, harness_root,
):
    """The control for the assertion above: the enum this story widened is
    what makes it pass, and the schema as it stood before rejects the very
    same history for the very reason it was widened."""
    target_root = make_target("history-schema-control")
    assert drive(target_root, harness_root, **crash_plan(BUDGETED, 1))[0] == 0
    pre_story = json.loads(repository_file_at(
        "schemas/execution-history.schema.json",
        validation_file=Path(__file__), bound=BASELINE))
    errors = schema_validator.validate(history_of(target_root), pre_story)
    assert errors
    assert any("self-routed" in error for error in errors)


def test_the_retry_history_schema_records_why_a_self_route_is_not_written_there():
    """The decision is recorded where a reader of that file will meet it."""
    description = json.loads(
        (REPO_ROOT / "schemas" / "retry-history.schema.json").read_text(
            encoding="utf-8"))["description"]
    assert "self-route" in description


def test_a_self_routing_run_appends_nothing_to_retry_history(
    make_target, harness_root,
):
    """The behaviour behind that description, driven: a run whose only
    incident is a self-route produces no retry-history.json at all."""
    target_root = make_target("no-retry-history")
    assert drive(target_root, harness_root, **crash_plan(BUDGETED, 1))[0] == 0
    assert self_route_records(target_root)
    assert retry_history_of(target_root) is None
    assert attempt_dirs(target_root) == []
    assert state_of(target_root)["retry_count"] == 0


# --------------------------------------------------------------------------
# The stage baseline is captured once and reused
# --------------------------------------------------------------------------


LEFTOVER = "leftover_from_the_crash.py"


def crash_leaves_a_governed_file(root: Path) -> None:
    """What a dying invocation leaves behind: a file under the very prefix the
    stage declares it must not create, written before the process exited."""
    prefix = declaration_of(BUDGETED)["may_not_create"][0]
    write(root / prefix / LEFTOVER, "def test_leftover():\n    assert True\n")


def repairs_the_suite(root: Path) -> dict:
    """The re-run's edit: an addition to the module and coverage for it."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / "tests" / "test_app.py", TEST_APP_AT_HEAD + ADDED_COVERAGE)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


@pytest.fixture
def crashed_then_re_ran(suite_target, harness_root):
    """A real-suite target whose budgeted stage crashed after writing a file
    under its governed prefix, then self-routed and ran again."""
    code, runner = drive(
        suite_target, harness_root, {BUDGETED: [CRASH]},
        tree={BUDGETED: [crash_leaves_a_governed_file, repairs_the_suite]})
    return suite_target, code, runner


def baseline_dirs(target_root: Path, stage: str) -> list[Path]:
    declaration = declaration_of(stage)["revert_check"]
    root = run_dir_of(target_root) / declaration["baseline"]
    return sorted(p for p in root.glob(f"{stage}-attempt-*") if p.is_dir())


def test_the_stage_baseline_is_the_one_the_first_invocation_captured(
    crashed_then_re_ran,
):
    """One baseline for the stage and attempt, and it does not hold the file
    the crashed invocation left in the tree — so the re-run is decided against
    what the stage originally found, not against its own partial work."""
    target_root, _, _ = crashed_then_re_ran
    directories = baseline_dirs(target_root, BUDGETED)
    assert len(directories) == 1
    captured = {p.relative_to(directories[0]).as_posix()
                for p in directories[0].rglob("*") if p.is_file()}
    assert captured, "the capture found nothing, so its contents say nothing"
    assert LEFTOVER not in {Path(p).name for p in captured}


def test_a_fresh_capture_from_the_same_tree_does_hold_that_file(
    crashed_then_re_ran, tmp_path,
):
    """The control for the absence above. The same capture, taken now against
    the same tree, finds the leftover — so its absence from the run's baseline
    is a statement about *when* the capture happened rather than about a
    reader that stopped seeing files."""
    target_root, _, _ = crashed_then_re_ran
    declaration = declaration_of(BUDGETED)
    scratch = tmp_path / "fresh-capture"
    fresh = story_coordinator.capture_stage_baseline(
        scratch, target_root, declaration["revert_check"]["baseline"],
        BUDGETED, 99, declaration["may_not_create"])
    names = {p.name for p in fresh.rglob("*") if p.is_file()}
    assert LEFTOVER in names


def test_the_revert_check_still_decided_against_that_baseline(
    crashed_then_re_ran,
):
    """The reading a developer takes from the run: the re-run's unforced
    coverage was escalated on, which is what the check says about an edit the
    stage's original baseline shows nothing forced."""
    target_root, code, _ = crashed_then_re_ran
    assert code == 2
    reason = escalation_reason(target_root)
    assert "reverted" in reason
    assert "tests/test_app.py" in reason
    # And the self-route did happen, so this is the re-run being decided.
    assert len(self_route_records(target_root)) == 1


# --------------------------------------------------------------------------
# Every other escalation still escalates, budget or no budget
# --------------------------------------------------------------------------


def invalid_schema(run_dir: Path) -> None:
    """A required output that does not satisfy the schema its stage declares."""
    artifact = next(iter(declaration_of(BUDGETED)["schemas"]))
    write_json(run_dir / artifact, {"modified": "not a list", "created": [],
                                    "deleted": []})


def blocked_path(run_dir: Path) -> None:
    record = declaration_of(BUDGETED)["changed_files"]
    blocked = RULES["blocked_paths"][-1]
    write_json(run_dir / record, {"modified": [f"{blocked}something.json"],
                                  "created": [], "deleted": []})


def owned_by_no_one(run_dir: Path) -> None:
    record = declaration_of(BUDGETED)["changed_files"]
    prefix = declaration_of(BUDGETED)["may_not_create"][0]
    write_json(run_dir / record, {"modified": [], "deleted": [],
                                  "created": [f"{prefix}test_new.py"]})


BOUNDARY_CASES = [
    ("schema-violation", invalid_schema, "wrote an invalid artifact"),
    ("blocked-path", blocked_path, "modified blocked path"),
    ("stage-output-ownership", owned_by_no_one, "must not create"),
]


@pytest.mark.parametrize("name,hook,phrase",
                         BOUNDARY_CASES, ids=[c[0] for c in BOUNDARY_CASES])
def test_a_boundary_violation_at_a_budgeted_stage_still_escalates(
    make_target, harness_root, tmp_path, name, hook, phrase,
):
    """Retrying the same instructions after a boundary violation produces the
    same violation again, so none of these is a self-route however much budget
    the stage declares.

    The reason is compared against the same violation under a probe workflow
    with the budget removed, so "unchanged" is a comparison rather than a
    literal.
    """
    subject = make_target(f"boundary-{name}")
    code, runner = drive(subject, harness_root, hooks={BUDGETED: [hook]})
    assert code == 2
    assert phrase in escalation_reason(subject)
    assert runner.calls.count(BUDGETED) == 1
    assert self_route_traces(subject) == []
    assert state_of(subject)["retry_count"] == 0

    harness = probe_harness(tmp_path, f"nobudget-{name}",
                            without_budget(BUDGETED))
    workflow = harness_config.load_workflow(harness, f"nobudget-{name}")
    control = build_target(tmp_path / f"boundary-control-{name}",
                           workflow=workflow["name"])
    assert drive(control, harness, hooks={BUDGETED: [hook]},
                 workflow=workflow)[0] == 2
    assert escalation_reason(subject) == escalation_reason(control)


@pytest.fixture
def budgeted_clean_clone(tmp_path):
    """A harness whose clean-clone stage declares a self-route budget.

    The shipped workflow grants one only to the stage before it, and the
    criterion is about a stage that *does* declare a budget still escalating
    on the clean-clone check's two escalations — so the budget is moved onto
    that stage rather than the claim being made about a stage without one.
    """
    name = "budgeted-clean-clone"
    harness = probe_harness(tmp_path, name,
                            with_budget(CLEAN_CLONE_STAGE["name"], 1))
    return harness, harness_config.load_workflow(harness, name)


def test_a_clean_clone_that_cannot_run_still_escalates_at_a_budgeted_stage(
    tmp_path, budgeted_clean_clone,
):
    harness, workflow = budgeted_clean_clone
    target_root = build_target(tmp_path / "clean-clone-unrunnable",
                               workflow=workflow["name"])
    configure(target_root, clean_clone_python="nowhere/python")

    code, runner = drive(target_root, harness, workflow=workflow)

    assert code == 2
    assert "could not run" in escalation_reason(target_root)
    assert "nowhere/python" in escalation_reason(target_root)
    assert self_route_traces(target_root) == []
    assert runner.calls.count(CLEAN_CLONE_STAGE["name"]) == 1


def test_a_failing_clean_clone_with_retries_exhausted_still_escalates(
    tmp_path, budgeted_clean_clone,
):
    """The other clean-clone escalation: the suite fails where the code ships
    and the retry ceiling has been reached. It is the *retry* budget that is
    exhausted, and the stage's own budget does not extend it."""
    harness, workflow = budgeted_clean_clone
    target_root = build_target(tmp_path / "clean-clone-failing",
                               workflow=workflow["name"],
                               test_command=FAILING_TEST_COMMAND)

    verdicts = [failing(n) for n in range(1, RULES["max_retries"] + 1)] + [PASS]
    code, runner = drive(target_root, harness, verdicts=verdicts,
                         workflow=workflow)

    assert code == 2
    reason = escalation_reason(target_root)
    assert "clean-clone check failed and retries are exhausted" in reason
    assert state_of(target_root)["retry_count"] == RULES["max_retries"]
    assert self_route_traces(target_root) == []


# --------------------------------------------------------------------------
# Pre-flight: a budget that cannot be spent refuses the run
# --------------------------------------------------------------------------

MALFORMED = [-1, "1", 1.5, True, None, [1]]


def test_the_check_accepts_every_budget_a_stage_may_declare():
    """The function itself, over stage lists that are not a real workflow, so
    the accepted and refused values are pinned to the values rather than to
    whichever one a run happens to meet first."""
    assert story_coordinator.self_route_problems(STAGES) == []
    for good in (0, 1, 7):
        assert story_coordinator.self_route_problems(
            [{"name": "s", BUDGET_KEY: good}]) == []
    # Undeclared is the normal case and is not checked at all.
    assert story_coordinator.self_route_problems([{"name": "s"}]) == []


@pytest.mark.parametrize("value", MALFORMED, ids=[repr(v) for v in MALFORMED])
def test_the_check_reports_one_problem_per_malformed_budget(value):
    problems = story_coordinator.self_route_problems(
        [{"name": "alpha", BUDGET_KEY: value}, {"name": "beta"}])
    assert len(problems) == 1
    assert "alpha" in problems[0]
    assert "beta" not in problems[0]


def created_nothing(target_root: Path) -> list[str]:
    """What a refused run must not have left behind, as a list of violations.

    A list rather than five assertions so the same statement can be made of
    the run that is supposed to create them, which is the control.
    """
    run_dir = run_dir_of(target_root)
    problems = []
    if run_dir.exists():
        problems.append(f"a run directory exists at {run_dir}")
    if (run_dir / "state.json").exists():
        problems.append("state.json was written")
    if (target_root / ".harness" / "logs" / f"{STORY_ID}.log").exists():
        problems.append("a log was written")
    branch = f"story/{STORY_ID}"
    if git(target_root, "branch", "--list", branch).stdout.strip():
        problems.append(f"branch {branch} was created")
    head = git(target_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if head != DEFAULT_BRANCH:
        problems.append(f"the repository was left on {head}")
    return problems


@pytest.mark.parametrize("value", MALFORMED, ids=[repr(v) for v in MALFORMED])
def test_a_workflow_declaring_a_budget_that_is_not_a_count_is_refused(
    tmp_path, capsys, value,
):
    name = f"bad-budget-{MALFORMED.index(value)}"
    harness = probe_harness(tmp_path, name, with_budget(BUDGETED, value))
    target_root = build_target(tmp_path / f"target-{name}", workflow=name)
    runner = Runner(target_root)

    assert story_coordinator.run_story(
        STORY_ID, harness, target_root, runner) == 1

    message = capsys.readouterr().err
    assert BUDGETED in message
    assert BUDGET_KEY in message
    assert message.count(BUDGETED) == 1, "one message per problem"
    assert runner.calls == []
    assert created_nothing(target_root) == []


def test_the_same_run_under_a_sound_budget_creates_all_of_it(tmp_path):
    """The control for `created_nothing`: same harness-building code, same
    target, same runner, with a budget the check accepts."""
    harness = probe_harness(tmp_path, "sound-budget", with_budget(BUDGETED, 0))
    target_root = build_target(tmp_path / "target-sound", workflow="sound-budget")
    workflow = harness_config.load_workflow(harness, "sound-budget")
    runner = Runner(target_root, workflow=workflow)

    assert story_coordinator.run_story(
        STORY_ID, harness, target_root, runner) == 0

    assert runner.calls
    assert created_nothing(target_root) == [
        f"a run directory exists at {run_dir_of(target_root)}",
        "state.json was written",
        "a log was written",
        f"branch story/{STORY_ID} was created",
        f"the repository was left on story/{STORY_ID}",
    ]


def test_a_declared_budget_of_zero_escalates_like_declaring_nothing(tmp_path):
    """Zero is a deliberate declaration of no budget: accepted at pre-flight,
    and spent nowhere."""
    harness = probe_harness(tmp_path, "zero-budget", with_budget(BUDGETED, 0))
    workflow = harness_config.load_workflow(harness, "zero-budget")
    target_root = build_target(tmp_path / "target-zero", workflow="zero-budget")

    code, runner = drive(target_root, harness, {BUDGETED: [CRASH]},
                         workflow=workflow)

    assert code == 2
    assert runner.calls.count(BUDGETED) == 1
    assert self_route_traces(target_root) == []
    assert "self-route" not in escalation_reason(target_root)


def test_the_pre_flight_is_what_refuses_the_bad_budget(tmp_path):
    """The control for the refusal. With the pre-flight's answer replaced by
    "no problems", the same bad workflow is no longer refused and the run
    proceeds to create everything the refusal is asserted not to create."""
    module = load_mutant(
        COORDINATOR_PATH,
        [("budget_problems = self_route_problems(stages)",
          "budget_problems = []")],
        name="mutant_coordinator_without_self_route_preflight",
        tmp_path=tmp_path)

    harness = probe_harness(tmp_path, "unchecked-budget",
                            with_budget(BUDGETED, -1))
    workflow = harness_config.load_workflow(harness, "unchecked-budget")
    target_root = build_target(tmp_path / "target-unchecked",
                               workflow="unchecked-budget")
    runner = Runner(target_root, workflow=workflow)

    assert module.run_story(STORY_ID, harness, target_root, runner) != 1
    assert runner.calls
    assert created_nothing(target_root) != []


# --------------------------------------------------------------------------
# The three passages the story holds the implementation to
# --------------------------------------------------------------------------


def test_the_self_route_cannot_alternate(make_target, harness_root):
    """"The global ceiling survives the exception, because a self-route cannot
    alternate: when it succeeds the workflow advances, and the only way it
    repeats is by failing again in the same place."

    Driven: in every run this file makes, the invocation after a self-route is
    the same stage, and the invocation after *that* is either the same stage
    again — another failure in the same place — or a later stage.
    """
    target_root = make_target("no-alternation")
    assert drive(target_root, harness_root,
                 {BUDGETED: [CRASH, OK, CRASH, OK]},
                 verdicts=[failing(1), PASS])[0] == 0

    entries = events_of(target_root, "self-routed")
    assert entries
    history = history_of(target_root)
    for entry in entries:
        following = history[history.index(entry) + 1:]
        started = next(e for e in following if e["event"] == "stage-started")
        assert started["stage"] == entry["stage"]


def test_a_self_route_has_no_destination_other_than_itself(
    make_target, harness_root,
):
    """"There is no defect to categorize and no other stage to send the work
    to, so the failed stage runs again."

    No retry category is recorded for a self-route, and the stage it names is
    its own — which is what makes counting them per stage a history query.
    """
    target_root = make_target("no-destination")
    assert drive(target_root, harness_root, **crash_plan(BUDGETED, 1))[0] == 0
    entry = events_of(target_root, "self-routed")[0]
    assert entry["stage"] == entry["retry_stage"] == BUDGETED
    assert "retry_category" not in entry
    assert "retry_decision" not in entry
