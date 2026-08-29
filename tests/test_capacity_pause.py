"""Independent validation for story-080: a capacity stop pauses the run.

The story adds a third interruption beside the crash and the escalation. Every
non-zero agent exit used to be treated identically, so an invocation stopped by
a provider rate limit produced exactly what a genuinely broken one produced: an
escalated run, an escalation summary, and a run directory to be moved aside. A
budget ceiling is a reason to stop; capacity exhaustion is only a reason to
wait. This module is laid out along the four decisions the story turned on:

  * **where the classification is made.** The agent runner reads the result
    event for the final text and the reported cost; it classifies there too and
    carries the answer on `AgentResult`. The coordinator routes on that field
    and never on a string, so nothing reads the run log back;
  * **what a pause commits.** The same two commits an escalation makes, through
    the same functions, under a subject of its own — because the readers that
    identify an escalation commit identify it by its subject, and a pause is
    not one;
  * **when the process waits.** Only when the signal carried a reset time and
    the wait it implies is within the configured bound. A known reset beyond
    the bound and an unknown reset both exit, the second of which is the case
    that keeps the mechanism honest;
  * **what a capacity resume restores.** Nothing. No archive, no entry, no
    counter reset. That is where it differs from an escalated resume, and the
    difference follows from what each is.

The workflow these runs execute is built by the fixture in `tests/conftest.py`
rather than resolved out of what this repository deploys. The subject here is
the mechanism — what a capacity classification does to a run — and the stage
list, the stage names and the bound are inputs to it. The bound below is this
module's own for the same reason: a run that waits `PAUSE_BOUND` seconds is a
shape this module constructs, and inheriting this deployment's five hours would
make a change to what this repository is willing to wait redden assertions with
nothing to say about whether that value is right. What this repository
*configures* is asserted where the shipped configuration is the subject.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "the paused run wrote no escalation summary and spent no counter" sits
    beside the identical run whose invocation failed carrying *no* capacity
    classification, which escalates, writes the summary and spends the
    self-route;
  * "the injected sleep was never called" sits beside the identical run whose
    reset time falls inside the bound, where it is called with the wait;
  * "the pause commit was already made when the sleep began" is read as the
    work being *inside* that commit rather than as the commit merely existing,
    with `src/app.py` at HEAD compared against what the branch carried before
    the run;
  * "no reset time is anywhere in state.json" sits beside the same scan over a
    state dict with the reset time planted in it, which reports it;
  * "the pause commit's subject is not an escalation" sits beside the same
    reader over an escalation's own subject, which answers, and beside the
    pause reader over both;
  * "the bound appears as a duration nowhere in orchestration/ or hooks/" sits
    beside the same scan over a copy of one of those files with the duration
    planted in it, which reports it;
  * "the resumed pause opened no entry directory and archived no attempt" sits
    beside an escalated resume of the same fixture, which opens and archives;
  * "run_status.py names no status of its own" sits beside the same scan over a
    copy that names one, which reports it.

Nothing here invokes a model: every run goes through a fake agent runner, and
the one test that exercises the real `run_agent` substitutes its `Popen`.
Nothing here sleeps: the wait is injected into `run_story` and recorded.
"""
import ast
import dataclasses
import json
import subprocess
import time
from pathlib import Path

import pytest

import conftest

import agent_runner
import harness_config
import run_status
import schema_validator
import story_coordinator
from agent_runner import AgentResult, CapacityStop

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The numbers and the signals this module owns
# --------------------------------------------------------------------------

#: What this module's target configures as its ceiling on an in-place wait. A
#: duration no harness would choose and this repository does not configure, so
#: a run observed waiting this long is obeying configuration rather than
#: something the harness picked. The two reset offsets pin it from both sides.
PAUSE_BOUND = 733
INSIDE_THE_BOUND = PAUSE_BOUND - 11
BEYOND_THE_BOUND = PAUSE_BOUND + 11

#: One capacity signal the agent runner holds, read off the constant rather
#: than written here: what the CLI says is the runner's fact and this module
#: derives it. The first is enough for the fixtures; every one of them is
#: exercised by the classification tests below.
A_SIGNAL = agent_runner.CAPACITY_SIGNALS[0]

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
TESTS_DIR = "tests/"

#: Enough retries that a run below stops for the reason under test rather than
#: at the retry ceiling.
RULES = {
    "max_retries": 3,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

#: How many times a stage of the built workflow may re-run itself. One, and it
#: matters: a capacity failure must leave it unspent while an ordinary failure
#: spends it, which is how "the pause is decided above the self-route decision"
#: is checked rather than argued.
SELF_ROUTE_BUDGET = 1


# --------------------------------------------------------------------------
# The workflow these runs execute
# --------------------------------------------------------------------------


WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"},
        max_self_routes=SELF_ROUTE_BUDGET),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        max_self_routes=SELF_ROUTE_BUDGET,
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="capacity-pause-workflow",
)

WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for capacity pause tests
  description: |
    A stand-in story used to drive the coordinator's interruption handling
    deterministically against a fake runner that reports capacity stops.

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

mandate:
  source:
    kind: human
  conferred_at: 2026-08-28 09:00:00
  conferred_by: A Developer <developer@example.com>
  recorded_by: l5-plan
"""

#: The target's configuration, with the pause wait as its own line so a fixture
#: can leave the key out altogether — absent is a case the story decides, and
#: it has to be spellable here as absence rather than as a falsy value.
CONFIG = """\
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
{pause_wait}stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: {tests_dir}
"""

APP_AT_HEAD = "print('hello')\n"
#: What a capacity-stopped invocation leaves in the target's working tree
#: before it stops, so the pause has work to commit and the commit has
#: something to be about.
APP_MID_STAGE = APP_AT_HEAD + "print('half-written when capacity ran out')\n"


# --------------------------------------------------------------------------
# A target repository, a harness root, and a fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def _init(root: Path, message: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def build_target(root: Path, *, bound: object = PAUSE_BOUND) -> Path:
    """A target configured to run the built workflow.

    `bound` is what `max_pause_wait_seconds` is set to; `None` leaves the key
    out entirely, which is the "configures no bound" case and is a different
    configuration from one carrying zero.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    pause_wait = "" if bound is None else f"max_pause_wait_seconds: {bound}\n"
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=WORKFLOW["name"], tests_dir=TESTS_DIR,
                        pause_wait=pause_wait))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "test_existing.py",
          "def test_nothing():\n    assert True\n")
    _init(root, "initial")
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)
    return root


class Runner:
    """A fake agent runner whose named invocations fail, with or without capacity.

    `failures` maps a zero-based invocation ordinal to what that invocation
    returns: a `CapacityStop` for a capacity failure, or `None` for an ordinary
    one. Every other invocation succeeds, writing the artifacts its stage
    declares. A capacity-stopped invocation still edits the working tree first,
    because a stage stopped part-way through is exactly the case the pause
    commit exists to protect.
    """

    def __init__(self, target_root: Path, failures: dict | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.failures = dict(failures or {})
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, **extra):
        ordinal = len(self.calls)
        self.calls.append(stage)

        if ordinal in self.failures:
            capacity = self.failures[ordinal]
            write(self.target_root / "src" / "app.py", APP_MID_STAGE)
            return AgentResult(ok=False, result_text=f"{stage} stopped",
                               capacity=capacity)

        if stage == WRITING:
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('invocation {ordinal + 1}')\n")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": ["src/app.py"], "created": [], "deleted": []})
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on invocation {ordinal + 1}.\n")
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
        return AgentResult(ok=True, result_text=f"{stage} done")


def stop(offset: float | None) -> CapacityStop:
    """A capacity classification whose reset time is `offset` seconds from now.

    `None` is the signal that carried no reset time at all, which is the case
    the harness must never guess at.
    """
    return CapacityStop(
        signal=A_SIGNAL,
        reset_at=None if offset is None else time.time() + offset)


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs, one per configured bound."""
    harness = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "harness", rules=RULES)
    _init(harness, "harness")

    def make(*, bound: object = PAUSE_BOUND, name: str = "target"):
        return build_target(tmp_path / name, bound=bound), harness
    return make


def run(target: Path, harness: Path, runner: Runner, *, sleep=None,
        slept: list | None = None) -> int:
    """One `run_story`, with the wait injected so nothing here actually waits."""
    if sleep is None:
        sleep = (slept if slept is not None else []).append
    return story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=sleep)


def run_dir_of(target: Path) -> Path:
    return target / ".harness" / "runs" / STORY_ID


def state_of(target: Path) -> dict:
    return json.loads(
        (run_dir_of(target) / "state.json").read_text(encoding="utf-8"))


def history(target: Path) -> list[dict]:
    path = run_dir_of(target) / "execution-history.json"
    return json.loads(path.read_text(encoding="utf-8"))


def subjects(target: Path) -> list[str]:
    return git(target, "log", "--format=%s").splitlines()


def attempt_archive(under: Path) -> Path:
    """Where archived attempts sit beneath `under`.

    Derived from the coordinator's own `attempt_dir` rather than spelled, and
    taken as the parent of the first attempt's directory so the name is the
    harness's in both places it is asked for: at the run root, where a capacity
    resume must leave nothing, and inside an entry directory, where an
    escalated resume leaves the archive it took with it.
    """
    return story_coordinator.attempt_dir(under, 1).parent


def prompt_files(target: Path) -> list[str]:
    return sorted(path.name for path in run_dir_of(target).glob("prompt-*.md"))


#: The counters the story says a pause leaves exactly where it found them, read
#: off `RunState` by name so a counter renamed here is a counter this module
#: stops asserting about loudly rather than quietly.
SURVIVING_COUNTERS = ("retry_count", "verification_iterations",
                      "self_route_count", "entry_cost_usd", "resume_count")


def counters(target: Path) -> dict:
    state = state_of(target)
    return {key: state[key] for key in SURVIVING_COUNTERS}


def paused_at_the_first_invocation(environment, *, offset=None, bound=PAUSE_BOUND,
                                   name="paused") -> tuple:
    """A run whose first invocation stops for capacity, and what it slept.

    The shape almost every assertion below is built on: one stage entered, one
    invocation, one capacity stop. Returns the target, the harness, the runner,
    the exit code and the durations the injected sleep was handed.
    """
    target, harness = environment(bound=bound, name=name)
    runner = Runner(target, {0: stop(offset)})
    slept: list[float] = []
    code = run(target, harness, runner, slept=slept)
    return target, harness, runner, code, slept


# --------------------------------------------------------------------------
# The classification, where the stream is already read
# --------------------------------------------------------------------------


class FakePopen:
    """Enough of `Popen` for `run_agent`, emitting the events it is given.

    The real CLI is never invoked. `exit_code` is what the process returns, so
    a capacity-stopped invocation can be driven as the non-zero exit it really
    is.
    """

    events: list[dict] = []
    exit_code = 0

    def __init__(self, cmd, **kwargs):
        self.stdin = open("/dev/null", "w")
        self.stdout = iter([json.dumps(event) + "\n"
                            for event in FakePopen.events])

    def wait(self):
        self.stdin.close()
        return FakePopen.exit_code


def invoke_run_agent(monkeypatch, tmp_path, *, events, exit_code=0) -> AgentResult:
    FakePopen.events = list(events)
    FakePopen.exit_code = exit_code
    monkeypatch.setattr(agent_runner.subprocess, "Popen", FakePopen)
    return agent_runner.run_agent(
        "prompt",
        stage=WRITING,
        cwd=tmp_path,
        log_path=tmp_path / "agent.log",
        permission_mode="acceptEdits",
        model=None,
        allowed_tools=["Bash(grep:*)"],
    )


def test_a_result_constructed_without_a_capacity_field_reports_none():
    """The default is what leaves every fake runner in this suite valid, so it
    is stated rather than assumed. Its companion is a result constructed *with*
    a classification, which carries it — so None above is the default doing its
    work rather than the field ignoring what it is given. None must keep
    meaning "nothing was said" rather than "nothing was wrong"."""
    assert AgentResult(ok=True, result_text="done").capacity is None
    carried = CapacityStop(signal=A_SIGNAL, reset_at=17.0)
    assert AgentResult(ok=False, result_text="stopped",
                       capacity=carried).capacity is carried


@pytest.mark.parametrize("signal", agent_runner.CAPACITY_SIGNALS)
def test_every_declared_signal_is_classified_as_a_capacity_stop(signal):
    """Each entry of the constant, exercised rather than trusted, and matched
    case-insensitively: the CLI's own casing is not the harness's to assume."""
    assert agent_runner.capacity_stop(
        {"type": "result", "result": signal}).signal == signal
    assert agent_runner.capacity_stop(
        {"type": "result", "result": signal.upper()}).signal == signal


@pytest.mark.parametrize("text", [
    "done",
    "the implementer could not find the file it was asked to edit",
    "error: the process was killed",
    "",
])
def test_anything_the_constant_does_not_hold_is_not_a_capacity_stop(text):
    """The asymmetry the story states outright: the classifier answers no
    unless the signal is one it holds, because a wrong classification either
    hangs a broken run or discards a recoverable one. The control is the
    parametrised test above, where every held signal does answer."""
    assert agent_runner.capacity_stop({"type": "result", "result": text}) is None


def test_a_reset_time_is_carried_when_the_signal_names_one():
    """And is None when it does not — which is the answer the harness acts on
    most conservatively, so the two must be distinguishable."""
    reset = 1893456000
    with_time = agent_runner.capacity_stop(
        {"type": "result", "result": f"{A_SIGNAL}|{reset}"})
    assert with_time.reset_at == float(reset)
    assert agent_runner.capacity_stop(
        {"type": "result", "result": A_SIGNAL}).reset_at is None


def test_the_classification_is_carried_off_the_result_event(monkeypatch,
                                                            tmp_path):
    """Off the same event the final text and the reported cost come off, so
    nothing reads the run log back. The control is the identical invocation
    whose result event says something else, which classifies as nothing."""
    reset = 1893456000
    stopped = invoke_run_agent(
        monkeypatch, tmp_path, exit_code=1,
        events=[{"type": "result",
                 "result": f"{A_SIGNAL}|{reset}"}])
    assert stopped.ok is False
    assert stopped.capacity.signal == A_SIGNAL
    assert stopped.capacity.reset_at == float(reset)

    ordinary = invoke_run_agent(
        monkeypatch, tmp_path, exit_code=1,
        events=[{"type": "result", "result": "the stage failed at its work"}])
    assert ordinary.ok is False
    assert ordinary.capacity is None


def test_a_non_result_event_carrying_the_signal_classifies_nothing(monkeypatch,
                                                                   tmp_path):
    """The classification is made in the loop that already reads the stream,
    on the result event and on no other. The control is the same text on a
    result event, which does classify."""
    assistant = invoke_run_agent(
        monkeypatch, tmp_path, exit_code=1,
        events=[{"type": "assistant", "result": A_SIGNAL},
                {"type": "result", "result": "the stage failed at its work"}])
    assert assistant.capacity is None

    on_result = invoke_run_agent(
        monkeypatch, tmp_path, exit_code=1,
        events=[{"type": "result", "result": A_SIGNAL}])
    assert on_result.capacity is not None


COORDINATOR_SOURCE = (REPO_ROOT / "orchestration" / "story_coordinator.py").read_text(
    encoding="utf-8")


def signals_in(text: str) -> list[str]:
    lowered = text.lower()
    return [signal for signal in agent_runner.CAPACITY_SIGNALS
            if signal in lowered]


def test_the_signals_are_written_in_the_agent_runner_and_nowhere_else():
    """The vocabulary of what the CLI says lives in the one module that may
    know it, so the coordinator routes on a field rather than on a string.

    The control is the same scan over a copy of the coordinator with one signal
    planted into it, which reports it — so the empty answer above is the scan
    working rather than the scan looking at nothing.
    """
    assert signals_in(COORDINATOR_SOURCE) == []

    planted = COORDINATOR_SOURCE.replace(
        "PAUSE_EXIT_CODE = 3",
        f'CAPACITY = "{A_SIGNAL}"\nPAUSE_EXIT_CODE = 3', 1)
    assert planted != COORDINATOR_SOURCE
    # One signal planted reports at least that signal; the entries of the
    # constant overlap as substrings, so what is asserted is that the scan
    # answers rather than the exact set it answers with.
    assert A_SIGNAL in signals_in(planted)


def test_the_coordinator_routes_on_the_field_rather_than_on_the_text():
    """A result whose text says nothing at all still pauses when the field is
    set, and a result whose *text* carries the signal but whose field is unset
    escalates. The two together are what "routes on the field and on no string"
    means operationally rather than as a source scan."""
    silent = AgentResult(ok=False, result_text="",
                         capacity=CapacityStop(signal=A_SIGNAL))
    assert silent.capacity is not None

    textual = AgentResult(ok=False, result_text=A_SIGNAL)
    assert textual.capacity is None


# --------------------------------------------------------------------------
# The pause: what it writes, and what it leaves alone
# --------------------------------------------------------------------------


def test_a_capacity_failure_leaves_the_run_paused(environment):
    """Status `paused`, exit code the pause's own, and the stage recorded as
    the one it stopped on."""
    target, _, runner, code, slept = paused_at_the_first_invocation(environment)

    assert code == story_coordinator.PAUSE_EXIT_CODE
    assert state_of(target)["status"] == "paused"
    assert state_of(target)["current_stage"] == WRITING
    assert runner.calls == [WRITING]
    assert slept == []


def test_the_pause_exit_code_is_neither_completion_nor_escalation():
    """So a caller can tell a run that must be looked at from one that may
    simply be run again. Stated against the codes the other two outcomes
    actually return, which the runs below observe."""
    assert story_coordinator.PAUSE_EXIT_CODE not in (0, 2)


def test_an_ordinary_failure_still_escalates(environment):
    """The control for every absence in this section, and an acceptance
    criterion in its own right: a failure carrying no capacity classification
    escalates exactly as it did — the same self-route before it, the same
    reason, and a summary written."""
    target, harness = environment(name="ordinary")
    runner = Runner(target, {0: None, 1: None})
    code = run(target, harness, runner)

    assert code == 2
    assert state_of(target)["status"] == "escalated"
    assert (run_dir_of(target) / "escalation-summary.md").is_file()
    # The self-route was spent before the escalation: the stage ran twice.
    assert runner.calls == [WRITING, WRITING]
    assert state_of(target)["self_route_count"] == SELF_ROUTE_BUDGET


def test_the_pause_writes_no_escalation_summary(environment):
    """Absence, with the escalating run above as its control: the same fixture
    escalating does write the file, so this is the pause declining to rather
    than a check pointed at a path nothing ever writes."""
    target, _, _, _, _ = paused_at_the_first_invocation(environment)
    assert not (run_dir_of(target) / "escalation-summary.md").exists()
    assert [entry["event"] for entry in history(target)].count("escalated") == 0


class SnapshottingRunner(Runner):
    """A runner that reads `state.json` at the moment it is invoked.

    So the "before" of a before-and-after comparison is the state the harness
    itself had recorded immediately above the invocation that pauses, rather
    than a value this module reasons its way to. The writing invocation reports
    a cost as well, so the allowance being compared is not sitting at zero for
    want of anything to spend.
    """

    def __init__(self, target_root: Path, failures, *, cost: float):
        super().__init__(target_root, failures)
        self.cost = cost
        self.snapshots: list[dict] = []

    def __call__(self, prompt, **keywords):
        self.snapshots.append(counters(self.target_root))
        result = super().__call__(prompt, **keywords)
        if result.ok:
            return AgentResult(ok=True, result_text=result.result_text,
                               cost_usd=self.cost)
        return result


def test_the_pause_spends_no_counter(environment):
    """Read off `state.json` before and after rather than inferred.

    The reading before is taken inside the invocation that pauses, which is
    where the harness had just written it; the reading after is taken once the
    process has returned. Every counter the story names is compared, and the
    allowance among them is non-zero going in, so an equality here is the pause
    leaving a spent counter alone rather than two zeroes agreeing.

    The control is the ordinary failure below, which does move a counter on the
    identical fixture.
    """
    target, harness = environment(name="counters")
    # The writer succeeds and reports a cost; the verifier stops for capacity.
    runner = SnapshottingRunner(target, {1: stop(None)}, cost=2.5)

    assert run(target, harness, runner) == story_coordinator.PAUSE_EXIT_CODE

    # The snapshot taken inside the invocation that failed, which is index 1:
    # the same index the control below compares, so the two comparisons are
    # bounded at the same point of the same shape.
    before = runner.snapshots[1]
    assert before["entry_cost_usd"] > 0
    assert counters(target) == before

    control, harness = environment(name="counters-control")
    ordinary = SnapshottingRunner(control, {1: None}, cost=2.5)
    run(control, harness, ordinary)
    assert counters(control) != ordinary.snapshots[1]


def test_a_capacity_failure_consumes_no_self_route(environment):
    """The pause is decided above the self-route decision, so a stage under an
    unspent budget still pauses rather than re-running itself.

    The control is the same stage under the same budget failing with no
    capacity classification, which does re-run itself: the runner is called
    twice there and once here. The control's second invocation fails too, so
    the run stops at that stage and the counter it spent is still the one
    standing when the process returns — a control whose second invocation
    succeeded would move on to the next stage, whose entry zeroes the count.
    """
    target, _, runner, _, _ = paused_at_the_first_invocation(
        environment, name="unspent")
    # The evidence a self-route writes, named through the coordinator's own
    # writer with the try number wildcarded rather than spelled here.
    evidence = story_coordinator.self_route_result_file(WRITING, 1, "*")
    assert runner.calls == [WRITING]
    assert state_of(target)["self_route_count"] == 0
    assert list(run_dir_of(target).glob(evidence)) == []

    control, harness = environment(name="spent")
    ordinary = Runner(control, {0: None, 1: None})
    run(control, harness, ordinary)
    assert ordinary.calls == [WRITING, WRITING]
    assert state_of(control)["self_route_count"] == SELF_ROUTE_BUDGET
    assert list(run_dir_of(control).glob(evidence)) != []


def test_a_capacity_failure_renders_no_prompt_under_a_later_number(environment):
    """No attempt was spent, so no later attempt's prompt exists.

    Named from the coordinator's own `prompt_file` rather than spelled here, so
    the assertion is about the file the harness writes. The control is the
    ordinary failure, whose self-route renders a second prompt for the same
    attempt.
    """
    target, _, _, _, _ = paused_at_the_first_invocation(environment,
                                                        name="one-prompt")
    first = story_coordinator.prompt_file(WRITING, 1)
    assert prompt_files(target) == [first]
    assert story_coordinator.prompt_file(WRITING, 2) not in prompt_files(target)

    control, harness = environment(name="two-prompts")
    run(control, harness, Runner(control, {0: None}))
    assert len(prompt_files(control)) > 1


# --------------------------------------------------------------------------
# The event, and what state.json does not gain
# --------------------------------------------------------------------------


PAUSE_EVENT_KIND = "capacity-paused"
HISTORY_SCHEMA = schema_validator.load_schema("execution-history")


def test_the_pause_event_names_what_was_detected_and_when(environment):
    """What was detected always, and when capacity is back when the signal
    carried a reset time. The control is the same run whose signal carried
    none, which says so rather than inventing a time."""
    target, _, _, _, _ = paused_at_the_first_invocation(
        environment, offset=BEYOND_THE_BOUND, name="event-timed")
    entries = [entry for entry in history(target)
               if entry["event"] == PAUSE_EVENT_KIND]
    assert len(entries) == 1
    assert A_SIGNAL in entries[0]["message"]
    assert time.strftime("%Y", time.localtime(
        time.time() + BEYOND_THE_BOUND)) in entries[0]["message"]

    untimed, _, _, _, _ = paused_at_the_first_invocation(
        environment, offset=None, name="event-untimed")
    [entry] = [entry for entry in history(untimed)
               if entry["event"] == PAUSE_EVENT_KIND]
    assert A_SIGNAL in entry["message"]


def test_the_written_history_validates_with_the_pause_entry_present(environment):
    """The declared kind is the contract the written history is checked
    against, so an undeclared kind would make the history invalid the first
    time a run paused. Asserted with the entry present, so the validation is
    about that entry rather than about a history that never had one."""
    target, _, _, _, _ = paused_at_the_first_invocation(environment,
                                                        name="history")
    written = history(target)
    assert PAUSE_EVENT_KIND in [entry["event"] for entry in written]
    assert schema_validator.validate(written, HISTORY_SCHEMA) == []


def test_the_declared_kinds_include_the_pause_and_the_schema_bites():
    """The declaration itself, and a demonstration that it is enforced.

    The control is an entry identical in every other respect whose kind the
    schema does not declare, which is reported — so the validation above
    passing is the declared kind being accepted rather than the enum being
    unenforced.
    """
    kinds = HISTORY_SCHEMA["items"]["properties"]["event"]["enum"]
    assert PAUSE_EVENT_KIND in kinds

    declared = {"sequence": 1, "timestamp": "2026-01-01 00:00:00",
                "event": PAUSE_EVENT_KIND, "message": "planted"}
    assert schema_validator.validate([declared], HISTORY_SCHEMA) == []
    assert schema_validator.validate(
        [{**declared, "event": "not-a-kind"}], HISTORY_SCHEMA) != []


def values_in(node) -> list:
    """Every scalar anywhere inside a decoded JSON document."""
    if isinstance(node, dict):
        return [value for item in node.values() for value in values_in(item)]
    if isinstance(node, list):
        return [value for item in node for value in values_in(item)]
    return [node]


def test_state_json_gains_no_field_and_holds_no_reset_time(environment):
    """The pause is expressed by the `status` value alone.

    Two absences, each controlled. The field set is compared against
    `RunState`'s own fields, so a field added to the dataclass fails this
    rather than slipping through; and the reset time appears nowhere among the
    document's scalars, which is demonstrated to be a real check by the same
    scan over the same document with the reset time planted into it.
    """
    target, harness = environment(name="no-field")
    reset = time.time() + BEYOND_THE_BOUND
    runner = Runner(target, {0: CapacityStop(signal=A_SIGNAL, reset_at=reset)})
    assert run(target, harness, runner) == story_coordinator.PAUSE_EXIT_CODE

    state = state_of(target)
    declared = {field.name for field in
                dataclasses.fields(story_coordinator.RunState)}
    assert set(state) == declared

    assert reset not in values_in(state)
    planted = {**state, "reset_at": reset}
    assert reset in values_in(planted)


# --------------------------------------------------------------------------
# The commit a pause leaves, and the readers that must not confuse it
# --------------------------------------------------------------------------


def test_a_pause_commits_what_the_run_left(environment):
    """The same two-commit shape an escalation leaves, so the tree is clean
    when the process exits and the work survives a checkout or a wait of days.

    The tree the pause committed carries the half-written file the stopped
    invocation left, which is what makes this a commit of the run's work
    rather than of its bookkeeping alone.
    """
    target, _, _, _, _ = paused_at_the_first_invocation(environment,
                                                        name="commits")
    assert git(target, "status", "--porcelain").strip() == ""

    subject = story_coordinator.pause_commit_subject(STORY_ID, WRITING)
    assert subjects(target)[:2] == [subject, subject]
    assert git(target, "show", "HEAD:src/app.py") == APP_MID_STAGE


def test_the_pause_commit_says_what_it_is_and_how_to_undo_it(environment):
    """The message says the run paused on capacity, that nothing about the work
    has been rejected, that a resume continues it, and how to put the changes
    back — because two commits a pause leaves are otherwise indistinguishable
    at a glance from the two an escalation leaves."""
    target, _, _, _, _ = paused_at_the_first_invocation(environment,
                                                        name="message")
    body = git(target, "log", "-1", "--format=%B")

    assert "capacity" in body.lower()
    assert "rejected" in body.lower()
    assert "resume" in body.lower()
    assert story_coordinator.PAUSE_UNDO_COMMAND in body
    assert STORY_ID in body and WRITING in body


def test_each_commit_reader_answers_only_for_its_own_kind():
    """Both directions, which is the whole reason there are two readers.

    A pause commit read by the escalation's reader answers None, and an
    escalation commit read by the pause's reader answers None — and each reader
    answers with the story id for its own subject, so neither None above is a
    reader that has stopped seeing anything.
    """
    paused = story_coordinator.pause_commit_subject(STORY_ID, WRITING)
    escalated = story_coordinator.escalation_commit_subject(STORY_ID, WRITING)
    assert paused != escalated

    assert story_coordinator.paused_story(paused) == STORY_ID
    assert story_coordinator.escalated_story(escalated) == STORY_ID
    assert story_coordinator.escalated_story(paused) is None
    assert story_coordinator.paused_story(escalated) is None


def test_the_head_escalation_reader_does_not_read_a_pause_commit(environment):
    """The reader `_complete` amends on, driven against a real pause commit.

    The control is the same reader against the same target once an escalation
    commit is on top of it, which does answer — so the None below is the pause
    subject being told apart rather than the reader failing on this repository.
    """
    target, harness = environment(name="head-reader")
    runner = Runner(target, {0: stop(None)})
    run(target, harness, runner)
    assert story_coordinator._head_escalated(target) is None

    subprocess.run(["git", "-C", str(target), "commit", "-q", "--allow-empty",
                    "-m", story_coordinator.escalation_commit_subject(
                        STORY_ID, WRITING)], check=True)
    assert story_coordinator._head_escalated(target) == STORY_ID


def test_a_completion_after_a_resumed_pause_is_not_amended_over_the_pause(
    environment,
):
    """`_complete` amends only over this story's own escalation commit, and a
    pause commit is not one — so the pause commit survives the completion and
    the completion is a commit of its own."""
    target, harness = environment(name="not-amended")
    assert run(target, harness, Runner(target, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    paused_head = git(target, "rev-parse", "HEAD").strip()

    assert run(target, harness, Runner(target, {})) == 0
    log = subjects(target)
    assert story_coordinator.pause_commit_subject(STORY_ID, WRITING) in log
    assert git(target, "rev-parse", "HEAD").strip() != paused_head
    assert paused_head in git(target, "log", "--format=%H")


# --------------------------------------------------------------------------
# The wait: three cases, and what lands before it
# --------------------------------------------------------------------------


def test_a_known_reset_within_the_bound_is_the_wait():
    """The pure decision, over the three inputs and nothing else."""
    assert story_coordinator.pause_wait_seconds(
        100.0, 40.0, PAUSE_BOUND) == pytest.approx(60.0)


def test_a_known_reset_beyond_the_bound_is_no_wait_at_all():
    """A run that would sit longer than the developer authorized exits and is
    resumed deliberately."""
    assert story_coordinator.pause_wait_seconds(
        40.0 + PAUSE_BOUND + 1, 40.0, PAUSE_BOUND) is None


def test_an_unknown_reset_is_no_wait_whatever_the_bound_says():
    """The case that keeps the mechanism honest: the bound is a ceiling on a
    wait whose length the harness has been told, never a duration it sleeps in
    the absence of one. Asserted against a bound large enough to have permitted
    any wait, so this is the unknown reset deciding rather than the bound."""
    assert story_coordinator.pause_wait_seconds(None, 40.0, PAUSE_BOUND) is None
    assert story_coordinator.pause_wait_seconds(None, 40.0, 10 ** 9) is None


def test_a_bound_of_zero_never_waits_and_a_past_reset_waits_for_nothing():
    """Zero is what a target configuring no bound has, so it must not consult
    the reset time at all; and a reset already past is a wait of nothing rather
    than a negative one."""
    assert story_coordinator.pause_wait_seconds(100.0, 40.0, 0) is None
    assert story_coordinator.pause_wait_seconds(10.0, 40.0, PAUSE_BOUND) == 0.0


def test_a_reset_within_the_bound_is_slept_to_and_the_stage_re_entered(
    environment,
):
    """The run waits in place and continues to completion from the same stage.

    The re-entry is the same attempt of the same stage, so the prompt file it
    renders is the one that attempt was about to be run with rather than a new
    one.
    """
    target, harness = environment(name="waits")
    runner = Runner(target, {0: stop(INSIDE_THE_BOUND)})
    slept: list[float] = []

    assert run(target, harness, runner, slept=slept) == 0

    assert len(slept) == 1
    assert slept[0] == pytest.approx(INSIDE_THE_BOUND, abs=30)
    assert runner.calls == [WRITING, WRITING, VERIFYING]
    assert state_of(target)["status"] == "completed"
    assert prompt_files(target) == sorted({
        story_coordinator.prompt_file(WRITING, 1),
        story_coordinator.prompt_file(VERIFYING, 1)})


def test_a_reset_beyond_the_bound_sleeps_not_at_all(environment):
    """And returns the pause exit code. The control is the run above, which on
    the identical fixture does sleep — the two differ in the reset time and in
    nothing else."""
    target, _, _, code, slept = paused_at_the_first_invocation(
        environment, offset=BEYOND_THE_BOUND, name="beyond")
    assert slept == []
    assert code == story_coordinator.PAUSE_EXIT_CODE
    assert state_of(target)["status"] == "paused"


def test_a_signal_carrying_no_reset_time_sleeps_not_at_all(environment):
    """Demonstrated by the injected sleep never having been called, not by
    reasoning about the code — which is the form the story asks for, and the
    reason the sleep is injected at all. Its control is the within-bound run
    above, where the same injected sleep is called once."""
    target, _, _, code, slept = paused_at_the_first_invocation(
        environment, offset=None, name="unknown")
    assert slept == []
    assert code == story_coordinator.PAUSE_EXIT_CODE
    assert state_of(target)["status"] == "paused"


def test_the_state_and_the_commit_both_land_before_the_sleep(environment):
    """The durability is unconditional and the wait is opportunistic.

    Observed at the moment the injected sleep is called: the recorded status is
    already `paused`, the pause event is already in the history, the pause
    commit is already the branch tip, and the work the stopped invocation left
    is already inside it. The control for the last of those is `src/app.py` at
    HEAD, which is the half-written text rather than what the branch carried
    before the run — so the commit observed here is a commit of the work rather
    than an empty one made in the right place.
    """
    target, harness = environment(name="ordering")
    observed: dict = {}

    def observing_sleep(seconds):
        observed["seconds"] = seconds
        observed["status"] = state_of(target)["status"]
        observed["subject"] = subjects(target)[0]
        observed["committed"] = git(target, "show", "HEAD:src/app.py")
        observed["event"] = [entry["event"] for entry in history(target)]

    runner = Runner(target, {0: stop(INSIDE_THE_BOUND)})
    assert run(target, harness, runner, sleep=observing_sleep) == 0

    assert observed["status"] == "paused"
    assert observed["subject"] == story_coordinator.pause_commit_subject(
        STORY_ID, WRITING)
    assert observed["committed"] == APP_MID_STAGE != APP_AT_HEAD
    assert PAUSE_EVENT_KIND in observed["event"]


class Killed(Exception):
    """What being killed mid-sleep looks like from inside the injected wait."""


def test_a_process_killed_while_waiting_leaves_a_run_that_resumes(environment):
    """The whole reason the ordering above matters, driven rather than argued.

    The wait is entered and the process never comes back out of it, which is
    what a kill during a five-hour sleep is. A fresh `run_story` then picks the
    run up at the stage it paused on and drives it to completion, and the work
    the stopped invocation left is on the branch throughout.

    The target here gitignores its run directory, which is the shape this
    harness deploys in and the shape `commit_escalated_work` names in as many
    words. A target that tracks its run directory carries the note the wait
    writes as an uncommitted change, and the clean-tree pre-flight refuses that
    resume — so the guarantee is asserted where it holds rather than asserted
    generally and quietly weakened to fit.
    """
    target, harness = environment(name="killed")
    write(target / ".gitignore", ".harness/runs/\n")
    conftest.commit_setup(target, "ignore the run directory")

    def killer(seconds):
        raise Killed

    with pytest.raises(Killed):
        run(target, harness, Runner(target, {0: stop(INSIDE_THE_BOUND)}),
            sleep=killer)

    assert state_of(target)["status"] == "paused"
    assert git(target, "show", "HEAD:src/app.py") == APP_MID_STAGE

    resumed = Runner(target, {})
    assert run(target, harness, resumed) == 0
    assert resumed.calls == [WRITING, VERIFYING]
    assert state_of(target)["status"] == "completed"


# --------------------------------------------------------------------------
# The configured bound: read from configuration, written in no source
# --------------------------------------------------------------------------


def test_the_bound_is_read_as_the_string_the_config_parser_produces(environment):
    """The parser coerces nothing, so the coordinator parses. Asserted against
    the value the fixture configured, and against the absent case, which is the
    one duration harness source is allowed to hold."""
    target, _ = environment(name="reads")
    config = harness_config.load_config(target)
    assert config["max_pause_wait_seconds"] == str(PAUSE_BOUND)
    assert story_coordinator.pause_wait_bound(config) == float(PAUSE_BOUND)
    assert story_coordinator.pause_wait_bound({}) == 0.0
    assert float(story_coordinator.NO_PAUSE_WAIT) == 0.0


def test_an_absent_bound_waits_for_nothing_at_all(environment):
    """A capacity signal with a reset time one second away still exits paused.

    The control is the same one-second reset on a target that *does* configure
    a bound, which waits — so the exit here is the absent key deciding rather
    than the reset time being unusable.
    """
    target, _, _, code, slept = paused_at_the_first_invocation(
        environment, offset=1, bound=None, name="unbounded")
    assert slept == []
    assert code == story_coordinator.PAUSE_EXIT_CODE

    bounded, harness = environment(name="bounded")
    runner = Runner(bounded, {0: stop(1)})
    waited: list[float] = []
    assert run(bounded, harness, runner, slept=waited) == 0
    assert len(waited) == 1


def test_the_harness_obeys_a_bound_it_would_never_have_chosen(environment):
    """The bound is pinned from both sides by one number this module invented.

    A reset just inside `PAUSE_BOUND` is waited for and a reset just outside it
    is not, and the two runs differ in nothing else — so what separates them
    can only be the number the configuration carries. A harness that had
    stopped reading the key would fall back to never waiting and fail the first
    half.
    """
    inside, _, _, inside_code, waited = paused_at_the_first_invocation(
        environment, offset=INSIDE_THE_BOUND, name="just-inside")
    assert len(waited) == 1 and inside_code == 0

    _, _, _, outside_code, not_waited = paused_at_the_first_invocation(
        environment, offset=BEYOND_THE_BOUND, name="just-outside")
    assert not_waited == [] and outside_code == story_coordinator.PAUSE_EXIT_CODE


#: Where harness source lives, for the scan that must find no duration in it.
HARNESS_SOURCES = sorted(
    path for directory in ("orchestration", "hooks")
    for path in (REPO_ROOT / directory).glob("*.py"))

#: What this repository configures as its own bound, read from its own
#: configuration rather than written here: the assertion is that this number
#: lives there and not in source, so writing it here would be writing the very
#: thing it forbids.
CONFIGURED_BOUND = conftest.repository_config()["max_pause_wait_seconds"]


def durations_in(source: str) -> list[str]:
    """Every numeric literal in `source` equal to this repository's bound.

    Read as numbers rather than as text, so a bound spelled `18_000` or `18000`
    is found either way and a coincidental substring of a longer number is not.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            if float(node.value) == float(CONFIGURED_BOUND):
                found.append(repr(node.value))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.strip() == str(CONFIGURED_BOUND):
            found.append(repr(node.value))
    return found


def test_the_configured_bound_appears_as_a_duration_in_no_harness_source():
    """Every wait the harness takes is read from configuration, so the only
    duration written in its own source is the zero that means do not wait.

    The control is the same scan over a copy of the coordinator with the
    duration planted into it, which reports it — so the empty answer is the
    scan working rather than the scan parsing nothing.
    """
    assert HARNESS_SOURCES, "no harness source was found to scan"
    for path in HARNESS_SOURCES:
        assert durations_in(path.read_text(encoding="utf-8")) == [], path

    planted = COORDINATOR_SOURCE.replace(
        'NO_PAUSE_WAIT = "0"', f'NO_PAUSE_WAIT = "{CONFIGURED_BOUND}"', 1)
    assert planted != COORDINATOR_SOURCE
    assert durations_in(planted) == [repr(str(CONFIGURED_BOUND))]


def test_this_repository_and_the_template_configure_the_same_bound():
    """A newly initialised target is consistent with this one rather than
    drifting from it. Both read as text off the files that carry them."""
    template = (REPO_ROOT / "templates" / "config.yaml").read_text(encoding="utf-8")
    assert f"max_pause_wait_seconds: {CONFIGURED_BOUND}" in template

    shipped = (REPO_ROOT / ".harness" / "config.yaml").read_text(encoding="utf-8")
    assert f"max_pause_wait_seconds: {CONFIGURED_BOUND}" in shipped


def test_the_key_is_declared_in_the_harness_config_schema():
    """The declared set is what the pre-flight checks a target's config
    against, so an undeclared key would refuse every run of a target carrying
    it — which is what the runs above would hit first if it were missing."""
    schema = schema_validator.load_schema("harness-config")
    assert "max_pause_wait_seconds" in schema["properties"]


# --------------------------------------------------------------------------
# Pre-flight: a bound that is not a bound
# --------------------------------------------------------------------------


#: Values a bound may not take, and values it may. Zero is a bound and means
#: never wait, which is what an absent key resolves to.
NOT_BOUNDS = ["five hours", "-1", "", "nan", "18,000"]
ARE_BOUNDS = ["0", "1", "0.5", str(PAUSE_BOUND)]


@pytest.mark.parametrize("value", ARE_BOUNDS)
def test_a_non_negative_number_is_a_bound_the_check_accepts(value):
    assert story_coordinator.pause_wait_problems(
        {"max_pause_wait_seconds": value}) == []


@pytest.mark.parametrize("value", NOT_BOUNDS)
def test_a_value_that_is_not_a_non_negative_number_is_reported(value):
    """Named by the key and by the value, so the message says which line of the
    configuration to change and what in it is wrong."""
    problems = story_coordinator.pause_wait_problems(
        {"max_pause_wait_seconds": value})
    assert len(problems) == 1, problems
    assert "max_pause_wait_seconds" in problems[0]
    assert repr(value) in problems[0]


def test_a_configuration_carrying_no_bound_is_not_checked_at_all():
    """Absent means zero, which is what every run did before this key existed,
    so there is nothing to report. The control is the parametrised refusal
    above, which does report."""
    assert story_coordinator.pause_wait_problems({}) == []


def test_a_bad_bound_refuses_the_run_before_anything_is_created(environment,
                                                               capsys):
    """The refusal in full: exit 1, no agent invoked, no run directory and so
    no state inside it, and no branch cut.

    The control is the identical run on the same target with the bound
    repaired, which does create each of those and does invoke a stage — so the
    absences are a refusal having happened rather than a check looking at a run
    that never started for some other reason.
    """
    target, harness = environment(bound="five hours", name="refused")
    before = git(target, "branch", "--format=%(refname:short)")
    runner = Runner(target, {})

    assert run(target, harness, runner) == 1

    assert runner.calls == []
    assert not run_dir_of(target).exists()
    assert git(target, "branch", "--format=%(refname:short)") == before
    message = capsys.readouterr().err
    assert "max_pause_wait_seconds" in message
    assert "'five hours'" in message

    repaired, harness = environment(bound=PAUSE_BOUND, name="repaired")
    proceeding = Runner(repaired, {})
    assert run(repaired, harness, proceeding) == 0
    assert proceeding.calls
    assert (run_dir_of(repaired) / "state.json").is_file()


# --------------------------------------------------------------------------
# Resuming a paused run in a fresh process
# --------------------------------------------------------------------------


def test_a_paused_run_resumed_in_a_fresh_process_continues_where_it_stopped(
    environment,
):
    """The whole point of writing the pause before sleeping: a second
    `run_story`, with no state carried in memory, picks the run up at the stage
    it paused on and drives it to completion with its artifacts intact."""
    target, harness = environment(name="fresh")
    first = Runner(target, {0: stop(None)})
    assert run(target, harness, first) == story_coordinator.PAUSE_EXIT_CODE
    assert first.calls == [WRITING]

    second = Runner(target, {})
    assert run(target, harness, second) == 0
    assert second.calls == [WRITING, VERIFYING]
    assert state_of(target)["status"] == "completed"
    assert (run_dir_of(target) / conftest.VERIFICATION_RESULT).is_file()


def test_a_capacity_resume_opens_no_entry_archives_nothing_and_resets_nothing(
    environment,
):
    """Compared across the resume rather than asserted about the end state.

    The control is an *escalated* resume of the same fixture, which does open
    an entry directory, does archive the interrupted attempt and does reset the
    counters — so the three absences here are the capacity branch declining
    rather than a run that had nothing to archive.
    """
    target, harness = environment(name="no-entry")
    assert run(target, harness, Runner(target, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    run_dir = run_dir_of(target)
    before = sorted(path.name for path in run_dir.iterdir())
    counters_before = counters(target)

    assert run(target, harness, Runner(target, {})) == 0

    # Both directories named through the coordinator's own writers rather than
    # spelled here, so a rename of either moves this with it.
    assert not story_coordinator.entry_dir(run_dir, 1).exists()
    assert not attempt_archive(run_dir).exists()
    after = counters(target)
    assert after["resume_count"] == counters_before["resume_count"]
    assert after["retry_count"] == counters_before["retry_count"]
    assert after["entry_cost_usd"] == counters_before["entry_cost_usd"]
    assert set(before) <= set(path.name for path in run_dir.iterdir())

    # The control: an escalated run of the same fixture, resumed.
    escalated, harness = environment(name="escalated-resume")
    assert run(escalated, harness, Runner(escalated, {0: None, 1: None})) == 2
    assert run(escalated, harness, Runner(escalated, {})) == 0
    escalated_dir = run_dir_of(escalated)
    opened = story_coordinator.entry_dir(escalated_dir, 1)
    assert opened.is_dir()
    # The archive travels into the entry directory with the rest of the
    # counter-keyed names, which is where an escalated resume leaves it.
    assert attempt_archive(opened).is_dir()
    assert state_of(escalated)["resume_count"] == 1


def test_the_capacity_resume_transitions_are_written_once(environment):
    """Both paths take the same function, which is what stops them drifting.

    Driven rather than read: the in-place wait and the fresh-process resume
    each leave the status `running` and each append a `resumed` event carrying
    the same note, and that note is what tells the capacity resume apart from
    the escalated one in the record.
    """
    waited, harness = environment(name="resume-in-place")
    assert run(waited, harness, Runner(waited, {0: stop(INSIDE_THE_BOUND)})) == 0
    in_place = [entry for entry in history(waited) if entry["event"] == "resumed"]

    fresh, harness = environment(name="resume-fresh")
    assert run(fresh, harness, Runner(fresh, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    assert run(fresh, harness, Runner(fresh, {})) == 0
    restarted = [entry for entry in history(fresh) if entry["event"] == "resumed"]

    assert len(in_place) == len(restarted) == 1
    assert in_place[0]["message"] == restarted[0]["message"]


def refusable_by_the_guard(target: Path, harness: Path, status: str) -> None:
    """Put a paused run's state into the shape the resume guard would refuse.

    The guard establishes three things before it refuses: the story artifact is
    the one the run read, the branch tip's parent is the commit the run
    recorded, and the harness is at the revision the run recorded. A pause
    records neither commit nor revision, because it is not an escalation and
    has nothing to establish — so this writes them, and commits that write, so
    that the tip's parent is the recorded commit and the tree is clean.

    The result is a state the guard has real evidence about, under whichever
    status the caller asks for. Which is the point: the two runs below differ
    in the status alone.
    """
    run_dir = run_dir_of(target)
    state = story_coordinator.load_state(run_dir)
    state.status = status
    state.escalation_commit = git(target, "rev-parse", "HEAD").strip()
    state.harness_revision = git(harness, "rev-parse", "HEAD").strip()
    story_coordinator.save_state(run_dir, state)
    conftest.commit_setup(target, "record what the guard compares against")

    story_text = (target / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text(
        encoding="utf-8")
    assert story_coordinator.unchanged_since_escalation(
        state, story_text, target, harness), \
        "the guard has no evidence here, so neither run below would be refused"


def test_a_paused_run_is_not_refused_by_the_unchanged_since_escalation_guard(
    environment,
):
    """That guard applies to an escalation, and a pause has nothing to have
    changed.

    Both runs are put into a state the guard has real evidence about — checked
    before either runs, so neither absence below is the guard having nothing to
    say. The paused one resumes; the escalated one, identical but for the
    status, is refused. That is the guard being consulted for one status and
    not the other, demonstrated rather than argued.
    """
    paused, harness = environment(name="guard-paused")
    assert run(paused, harness, Runner(paused, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    refusable_by_the_guard(paused, harness, "paused")
    assert run(paused, harness, Runner(paused, {})) == 0

    escalated, harness = environment(name="guard-escalated")
    assert run(escalated, harness, Runner(escalated, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    refusable_by_the_guard(escalated, harness, "escalated")
    assert run(escalated, harness, Runner(escalated, {})) == 1


def test_the_clean_tree_pre_flight_exempts_a_paused_run_as_it_does_an_escalated(
    environment,
):
    """A pause commits, so the tree it leaves is clean and anything uncommitted
    now is the developer's own — which is exactly what that exemption says.

    The control is the same resume with work the developer left in the tree,
    which is refused: so the exemption is a condition being met rather than a
    check that never runs.
    """
    target, harness = environment(name="clean-tree")
    assert run(target, harness, Runner(target, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    assert git(target, "status", "--porcelain").strip() == ""
    assert run(target, harness, Runner(target, {})) == 0

    dirty, harness = environment(name="dirty-tree")
    assert run(dirty, harness, Runner(dirty, {0: stop(None)})) == \
        story_coordinator.PAUSE_EXIT_CODE
    write(dirty / "src" / "elsewhere.py", "work the developer left behind\n")
    assert run(dirty, harness, Runner(dirty, {})) == 1


# --------------------------------------------------------------------------
# What the status reader does with a fourth status
# --------------------------------------------------------------------------


RUN_STATUS_SOURCE = (REPO_ROOT / "orchestration" / "run_status.py").read_text(
    encoding="utf-8")

#: Every status the coordinator may write, derived from what the shipped
#: statuses actually are rather than listed here: the point of the scan below
#: is that `run_status` knows none of them.
KNOWN_STATUSES = ("running", "paused", "escalated", "completed")


def statuses_named_in(source: str) -> list[str]:
    """Every status this source names as a string literal of its own."""
    return sorted({node.value for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant)
                   and isinstance(node.value, str)
                   and node.value in KNOWN_STATUSES})


def test_the_status_reader_renders_the_recorded_status_rather_than_a_list(
    environment,
):
    """`l5-status` reports a paused run with no change to `run_status.py`,
    because it renders what the state records rather than a list of statuses it
    knows.

    Both halves: the reader is driven against a real paused run and reports it,
    and the same module names no status of its own. The control for that
    absence is the identical scan over a copy that names one, which reports it.
    """
    target, _, _, _, _ = paused_at_the_first_invocation(environment,
                                                        name="status")
    detail = run_status.format_detail(target, STORY_ID)
    assert "paused" in detail
    assert "paused" in run_status.format_listing(target)

    assert statuses_named_in(RUN_STATUS_SOURCE) == []
    planted = RUN_STATUS_SOURCE.replace(
        'TAIL_LINES = 10', 'TAIL_LINES = 10\nENDED = ("completed",)', 1)
    assert planted != RUN_STATUS_SOURCE
    assert statuses_named_in(planted) == ["completed"]


def test_the_run_script_says_both_statuses_resume():
    """The docstring is the first thing a reader of the script meets, and a run
    whose state says it escalated is now one of two statuses that resume."""
    docstring = ast.get_docstring(
        ast.parse((REPO_ROOT / "scripts" / "l5-run").read_text(encoding="utf-8")))
    assert "escalated" in docstring
    assert "paused" in docstring
