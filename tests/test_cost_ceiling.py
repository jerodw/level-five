"""Independent validation for story-063: a run has a cost ceiling.

The story divides money into a ceiling that bounds a call and a ceiling that
bounds repetition, and a number that is policy from a number that accounts. The
module is laid out along those divisions because the divisions are the story:

  * the **per-execution allowance** bounds one call. It is declared on a stage,
    handed to every invocation of that stage unmodified, and passed through to
    the agent CLI so the invocation stops itself. Nothing about it is
    cumulative;
  * the **run ceiling** bounds repetition. It is declared on the workflow and
    compared, before a stage is invoked, against what the current entry of the
    run has spent;
  * the **live allowance** is `entry_cost_usd` on state.json. It is policy, it
    is entry-scoped, and a resume zeroes it;
  * the **record** is cost.json at the run root. It accounts, it spans every
    entry, and nothing resets, rewrites or prunes it.

The workflows these runs execute are built by the fixture in `tests/conftest.py`
rather than resolved out of what this repository deploys. The subject here is
the mechanism -- what a declared ceiling does to an invocation -- and the stage
list, the stage names and the ceiling values are inputs to it. The numbers are
this module's own for the same reason the retry ceiling is: a run that stops at
$18.00 is a shape this module constructs, and inheriting the shipped $90 would
make a change to what this deployment is willing to spend redden assertions
with nothing to say about whether that value is right. What this repository
*declares* is asserted in `tests/test_shipped_workflow_is_valid.py`, where the
shipped definition is the subject rather than an input.

Every figure below is a multiple of a half dollar, so every sum this module
compares is exact in binary and no assertion about a total is really an
assertion about floating-point rounding.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "a stage under no ceiling is called with no budget argument at all" sits
    beside the same runner under a workflow that declares one, which receives
    it, and beside a runner whose signature has no budget parameter at all,
    which the ceiling-less run still drives to completion;
  * "no budget argument appears anywhere in the command run_agent builds" sits
    beside the same scan over the command built with a budget, which finds it;
  * "the invocation that exhausted its allowance was not invoked again" sits
    beside the identical run whose invocation stopped one half-dollar short,
    which self-routes and is invoked again;
  * "the malformed definition left no run directory, no state and no branch"
    sits beside the identical run under the same definition with the offending
    value repaired, which creates all three;
  * "no per-stage cumulative total is kept in state" sits beside the same scan
    over a state dict with such a total planted in it, which reports it;
  * "cost.json is not under the archived entry" sits beside the artifacts that
    *are* under it, and beside the record's own contents, which span both
    entries;
  * "a run stopped on a ceiling is not refused by the unchanged guard" sits
    beside the guard's own evidence, which is non-empty for that very run, and
    beside an ordinary escalation on the same evidence, which is refused.

Nothing here invokes a model: every run goes through a fake agent runner, and
the one test that exercises the real `run_agent` substitutes its `Popen`.
"""
import json
import subprocess
from pathlib import Path

import pytest

import conftest

import agent_runner
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# The numbers this module owns
#
# Half-dollar multiples throughout, so `4.5 + 2.5 + 4.5 + 2.5 + 4.5` is exactly
# 18.5 and a comparison against a recorded total is a comparison of the totals
# rather than of two roundings. The run ceiling sits where the third writing
# invocation is reached with less left than that stage's allowance, which is
# the shape the flat-allowance criterion is about.
# --------------------------------------------------------------------------

#: What the workflow will spend before it refuses to enter another stage.
RUN_CEILING = 18.0
#: What one invocation of each stage may spend, whatever it has spent before.
WRITER_ALLOWANCE = 5.0
VERIFIER_ALLOWANCE = 3.0
#: What each invocation reports spending, both comfortably under the allowance
#: handed to it, so an ordinary run stops on the run ceiling rather than on an
#: exhausted allowance.
WRITER_COST = 4.5
VERIFIER_COST = 2.5

#: Enough retries that the runs below stop for the reason under test rather
#: than at the retry ceiling.
MAX_RETRIES = 3
RULES = {
    "max_retries": MAX_RETRIES,
    "require_verifier_pass": True,
    "blocked_paths": [".git/", ".harness/runs/", "rules/"],
}

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
TESTS_DIR = "tests/"


# --------------------------------------------------------------------------
# The workflows these runs execute
# --------------------------------------------------------------------------


def _ceiling(value) -> dict:
    """A `max_execution_cost_usd` declaration, or none at all.

    Absent and zero are different declarations and the coordinator treats them
    differently, so a stage asked for no ceiling gets no key rather than a key
    holding something falsy.
    """
    return {} if value is None else {"max_execution_cost_usd": value}


def build(name: str, *, run_ceiling=None, writer=None, verifier=None,
          extra_workflow: dict | None = None) -> dict:
    """A two-stage workflow declaring exactly the ceilings it was asked for."""
    workflow = conftest.build_workflow(
        conftest.workflow_stage(
            outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
            changed_files=conftest.CHANGED_FILES,
            schemas={conftest.CHANGED_FILES: "changed-files"},
            max_self_routes=1,
            **_ceiling(writer)),
        conftest.workflow_stage(
            name=conftest.VERIFYING_STAGE,
            outputs=(conftest.VERIFICATION_RESULT,),
            schemas={conftest.VERIFICATION_RESULT: "verification-result",
                     conftest.RETRY_GUIDANCE: "retry-guidance"},
            max_self_routes=1,
            retry_routing={"implementation-defect": {
                "stage": conftest.StageRef(0),
                "when": "the behaviour the story asked for is missing"}},
            **_ceiling(verifier)),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name=name,
    )
    if run_ceiling is not None:
        workflow["max_run_cost_usd"] = run_ceiling
    if extra_workflow:
        workflow.update(extra_workflow)
    return workflow


#: Both ceilings declared. The run this drives stops on the run ceiling with
#: every invocation comfortably inside its own allowance.
CEILINGED = build("ceilinged-workflow", run_ceiling=RUN_CEILING,
                  writer=WRITER_ALLOWANCE, verifier=VERIFIER_ALLOWANCE)
#: Neither ceiling declared anywhere. The compatibility case.
UNCEILINGED = build("unceilinged-workflow")

STAGE_NAMES = [stage["name"] for stage in CEILINGED["stages"]]
WRITING, VERIFYING = STAGE_NAMES
#: The declared allowance of each stage, read off the definition rather than
#: written a second time.
ALLOWANCES = {stage["name"]: stage["max_execution_cost_usd"]
              for stage in CEILINGED["stages"]}

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
RETRY_CATEGORY = next(iter(
    CEILINGED["stages"][1]["on_failure"]["retry_routing"]))


def failing(marker: str) -> dict:
    """A failing verdict recommending a retry, its text naming the attempt."""
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"{marker} did not implement the sample behavior",
            "location": f"src/{marker}.py",
            "required_behavior": f"the sample behavior exists after {marker}",
        }],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": RETRY_CATEGORY,
    }


STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for cost ceiling tests
  description: |
    A stand-in story used to drive the coordinator's cost accounting
    deterministically against a fake runner that reports recorded costs.

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


# --------------------------------------------------------------------------
# A target repository, a harness root, and a fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def _init(root: Path, message: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def build_target(root: Path, workflow_name: str) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=workflow_name, tests_dir=TESTS_DIR))
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


@pytest.fixture
def environment(tmp_path):
    """A builder for (target, harness) pairs running a definition given here.

    A factory rather than a fixture per workflow, because several tests below
    hold two definitions side by side — a ceilinged run and its ceiling-less
    companion, or a malformed declaration and its repair — and each needs its
    own target configured to run its own workflow.
    """
    def make(workflow: dict, rules: dict | None = None) -> tuple[Path, Path]:
        harness = conftest.materialize_workflow(
            workflow, tmp_path / f"harness-{workflow['name']}",
            rules=rules or RULES)
        _init(harness, "harness")
        target = build_target(tmp_path / f"target-{workflow['name']}",
                              workflow["name"])
        return target, harness
    return make


#: What "the coordinator passed no budget argument" looks like from inside the
#: runner, as distinct from a budget of None. A stage under no ceiling must not
#: be invoked with the keyword at all, so the two have to be distinguishable
#: here or the assertion cannot say which happened.
NO_BUDGET = object()


class Runner:
    """A fake agent runner reporting a recorded cost for each invocation.

    Every stage writes the artifacts its declaration names, and the writing
    stage also edits the target's working tree. What it records, beyond the
    calls, is the *keywords it was handed*: a stage under no ceiling has to be
    invoked with exactly the arguments the coordinator passed before ceilings
    existed, which is a statement about the call rather than about its result.
    """

    def __init__(self, target_root: Path, *, verdicts=None, costs=(None,),
                 skip_outputs=()):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = list(verdicts or [PASS])
        #: One cost per invocation, the last repeating. None means the
        #: invocation reported no cost at all, which is what every fake runner
        #: in this suite did before this story.
        self.costs = list(costs)
        #: Zero-based ordinals of the invocations that write nothing, which is
        #: the mechanical failure a self-route exists to answer.
        self.skip_outputs = set(skip_outputs)
        self.calls: list[str] = []
        #: (stage, the budget it was handed, or NO_BUDGET)
        self.budgets: list[tuple[str, object]] = []
        #: (stage, every keyword beyond the ones the coordinator has always
        #: passed) — so an unexpected one is reported rather than swallowed.
        self.extras: list[tuple[str, dict]] = []

    def budget_at(self, stage: str, occurrence: int = 0):
        return [budget for name, budget in self.budgets
                if name == stage][occurrence]

    def cost_of(self, ordinal: int):
        return self.costs[min(ordinal, len(self.costs) - 1)]

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, **extra):
        ordinal = len(self.calls)
        self.calls.append(stage)
        self.extras.append((stage, dict(extra)))
        self.budgets.append((stage, extra.get("max_budget_usd", NO_BUDGET)))

        if ordinal not in self.skip_outputs:
            if stage == WRITING:
                write(self.target_root / "src" / "app.py",
                      APP_AT_HEAD + f"print('invocation {ordinal + 1}')\n")
                write_json(self.run_dir / conftest.CHANGED_FILES,
                           {"modified": ["src/app.py"], "created": [],
                            "deleted": []})
                write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                      f"Implemented on invocation {ordinal + 1}.\n")
            elif stage == VERIFYING:
                seen = self.calls.count(stage) - 1
                write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                           self.verdicts[min(seen, len(self.verdicts) - 1)])
        return AgentResult(ok=True, result_text=f"{stage} done",
                           cost_usd=self.cost_of(ordinal))


class RunnerWithoutTheBudgetParameter(Runner):
    """The same runner with the signature it had before this story.

    No `**extra` and no budget keyword, so the coordinator passing one would
    raise `TypeError` here rather than being quietly accepted. This is how "a
    stage under no ceiling is invoked with exactly today's arguments" is
    checked as a property of the call rather than of what a permissive fake
    chose to ignore.
    """

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        return Runner.__call__(self, prompt, stage=stage, cwd=cwd,
                               log_path=log_path,
                               permission_mode=permission_mode, model=model,
                               allowed_tools=allowed_tools)


def run(target: Path, harness: Path, runner: Runner) -> int:
    return story_coordinator.run_story(STORY_ID, harness, target, runner)


def run_dir_of(target: Path) -> Path:
    return target / ".harness" / "runs" / STORY_ID


def state_of(target: Path) -> dict:
    return json.loads(
        (run_dir_of(target) / "state.json").read_text(encoding="utf-8"))


def events(target: Path) -> list[str]:
    log = (run_dir_of(target) / "events.log").read_text(encoding="utf-8")
    return [line.split("] ", 1)[1] for line in log.splitlines() if "] " in line]


def history(target: Path) -> list[dict]:
    path = run_dir_of(target) / "execution-history.json"
    return json.loads(path.read_text(encoding="utf-8"))


def cost_record(target: Path) -> dict:
    return story_coordinator.load_cost_record(run_dir_of(target))


def branches(target: Path) -> list[str]:
    listing = git(target, "branch", "--format=%(refname:short)")
    return sorted(line.strip() for line in listing.stdout.splitlines()
                  if line.strip())


# --------------------------------------------------------------------------
# The cost the runner carries
# --------------------------------------------------------------------------


def test_a_result_constructed_without_a_cost_reports_none():
    """The default is what leaves every fake runner in this suite valid, so it
    is stated rather than assumed. Its companion is a result constructed *with*
    a cost, which carries it — so "None" above is the default doing its work
    rather than the field ignoring what it is given."""
    assert AgentResult(ok=True, result_text="done").cost_usd is None
    assert AgentResult(ok=True, result_text="done", cost_usd=1.5).cost_usd == 1.5


class FakePopen:
    """Enough of `Popen` for `run_agent`, recording the command it was built
    with and emitting one result event. The real CLI is never invoked."""

    calls: list[list[str]] = []
    #: The result event the next construction will emit, as a dict.
    event: dict = {"type": "result", "result": "done"}

    def __init__(self, cmd, **kwargs):
        FakePopen.calls.append(list(cmd))
        self.stdin = open("/dev/null", "w")
        self.stdout = iter([json.dumps(FakePopen.event) + "\n"])

    def wait(self):
        self.stdin.close()
        return 0


def invoke_run_agent(monkeypatch, tmp_path, *, event=None,
                     **kwargs) -> tuple[list[str], AgentResult]:
    """Drive the real `run_agent` against a fake process. Returns the command
    it built and the result it produced."""
    FakePopen.calls = []
    FakePopen.event = event or {"type": "result", "result": "done"}
    monkeypatch.setattr(agent_runner.subprocess, "Popen", FakePopen)
    result = agent_runner.run_agent(
        "prompt",
        stage=WRITING,
        cwd=tmp_path,
        log_path=tmp_path / "agent.log",
        permission_mode="acceptEdits",
        model=None,
        allowed_tools=["Bash(grep:*)"],
        **kwargs,
    )
    assert len(FakePopen.calls) == 1
    return FakePopen.calls[0], result


def test_the_reported_total_is_carried_off_the_result_event(monkeypatch, tmp_path):
    """The cost is read from the event the runner already parses, beside the
    result text. The control is the same event without the field, which yields
    None rather than zero — so "no cost was reported" stays distinguishable
    from "the invocation was free"."""
    _, reported = invoke_run_agent(
        monkeypatch, tmp_path,
        event={"type": "result", "result": "done", "total_cost_usd": 4.5})
    assert reported.cost_usd == 4.5
    assert reported.result_text == "done"

    _, silent = invoke_run_agent(
        monkeypatch, tmp_path, event={"type": "result", "result": "done"})
    assert silent.cost_usd is None


def test_a_budget_reaches_the_cli_as_max_budget_usd(monkeypatch, tmp_path):
    """What makes a ceiling a ceiling rather than a gate between stages: the
    allowance is enforced inside the invocation, by the CLI that is running
    it."""
    cmd, _ = invoke_run_agent(monkeypatch, tmp_path,
                              max_budget_usd=WRITER_ALLOWANCE)
    assert "--max-budget-usd" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == str(WRITER_ALLOWANCE)


def test_without_a_budget_the_command_is_the_one_it_built_before(monkeypatch,
                                                                 tmp_path):
    """The compatibility half, and the one most easily lost.

    Stated as an absence and as an equality: no argument anywhere in the
    command mentions a budget, and the whole command is word for word the
    command built when the parameter is not passed at all. The control is the same command built *with* a budget,
    which the same scan reports and which differs from the baseline by exactly
    the two arguments the budget adds.
    """
    omitted, _ = invoke_run_agent(monkeypatch, tmp_path)
    passed_none, _ = invoke_run_agent(monkeypatch, tmp_path,
                                      max_budget_usd=None)

    assert [word for word in omitted if "budget" in word.lower()] == []
    assert passed_none == omitted

    with_budget, _ = invoke_run_agent(monkeypatch, tmp_path,
                                      max_budget_usd=WRITER_ALLOWANCE)
    assert [word for word in with_budget if "budget" in word.lower()] == [
        "--max-budget-usd"]
    assert len(with_budget) == len(omitted) + 2


def test_a_budget_of_zero_reaches_the_cli_where_an_absent_one_does_not(
    monkeypatch, tmp_path,
):
    """Zero is a deliberate refusal to spend anything and absent is unbounded,
    so a truthiness test here would spend an allowance nobody granted."""
    zero, _ = invoke_run_agent(monkeypatch, tmp_path, max_budget_usd=0)
    assert "--max-budget-usd" in zero
    assert zero[zero.index("--max-budget-usd") + 1] == "0"


# --------------------------------------------------------------------------
# Pre-flight: a ceiling that is not a ceiling
# --------------------------------------------------------------------------


#: Values a ceiling may not take. `True` and `False` are here because
#: `isinstance(True, int)` holds, so a bare number test accepts a declaration
#: of `true` as a ceiling of one dollar. `null` is not here because the builder
#: spells "declare nothing" that way; it has its own test below, where the key
#: is written into the definition directly.
NOT_CEILINGS = ["lots", -1, -0.5, True, False, [], {}]
#: Values it may. Zero is accepted and means a deliberate refusal to run.
ARE_CEILINGS = [0, 0.0, 1, 90, 4.5]


@pytest.mark.parametrize("value", ARE_CEILINGS)
def test_a_non_negative_number_is_a_ceiling_the_validator_accepts(value):
    accepted = build("accepted", run_ceiling=value, writer=value)
    assert story_coordinator.cost_ceiling_problems(accepted) == []


@pytest.mark.parametrize("value", NOT_CEILINGS)
def test_a_run_ceiling_that_is_not_a_number_is_reported(value):
    """The workflow-level half, named by the workflow and by the value, so the
    message says which definition to change and what in it is wrong."""
    bad = build("bad-run-ceiling", run_ceiling=value)
    problems = story_coordinator.cost_ceiling_problems(bad)
    assert len(problems) == 1, problems
    assert bad["name"] in problems[0]
    assert repr(value) in problems[0]


@pytest.mark.parametrize("value", NOT_CEILINGS)
def test_an_execution_ceiling_that_is_not_a_number_is_reported(value):
    """The per-stage half, on the same terms, named by the stage."""
    bad = build("bad-execution-ceiling", writer=value)
    problems = story_coordinator.cost_ceiling_problems(bad)
    assert len(problems) == 1, problems
    assert WRITING in problems[0]
    assert repr(value) in problems[0]


def test_a_definition_declaring_no_ceiling_is_not_checked_at_all():
    """Absent is unbounded, which is what every workflow did before ceilings
    existed — so a definition declaring neither has nothing to report."""
    assert story_coordinator.cost_ceiling_problems(UNCEILINGED) == []


def test_a_ceiling_declared_as_null_is_refused_rather_than_read_as_absent():
    """Declaring `null` is declaring something, and it is not a number.

    Written into the definition directly, because the builder spells "declare
    nothing" as `None` and a workflow author writing `"max_run_cost_usd": null`
    into the JSON has done a different thing. The control is the same
    definition with the key removed, which the validator does not check at all
    — so the refusal is about the declaration rather than about the absence of
    a usable value.
    """
    declared_null = {**UNCEILINGED, "max_run_cost_usd": None}
    declared_null["stages"] = [
        {**declared_null["stages"][0], "max_execution_cost_usd": None},
        *declared_null["stages"][1:]]

    problems = story_coordinator.cost_ceiling_problems(declared_null)
    assert len(problems) == 2, problems
    assert declared_null["name"] in problems[0]
    assert WRITING in problems[1]

    assert story_coordinator.cost_ceiling_problems(UNCEILINGED) == []


def test_a_malformed_ceiling_is_refused_before_anything_is_created(
    environment, capsys,
):
    """The refusal in full: exit 1, no agent invoked, no run directory and so
    no state file inside it, and no branch cut.

    The control is the identical run under the same definition with the
    offending value repaired, which does create each of those and does invoke a
    stage — so the absences above are a refusal having happened rather than a
    check looking at a run that never got started for some other reason.
    """
    bad = build("refused-workflow", run_ceiling="ninety",
                writer=WRITER_ALLOWANCE)
    target, harness = environment(bad)
    before = branches(target)
    runner = Runner(target, costs=[WRITER_COST])

    assert run(target, harness, runner) == 1

    assert runner.calls == []
    assert not run_dir_of(target).exists()
    assert branches(target) == before
    message = capsys.readouterr().err
    assert bad["name"] in message
    assert "'ninety'" in message

    # The control: the same definition with the ceiling repaired.
    repaired = build("refused-workflow", run_ceiling=RUN_CEILING,
                     writer=WRITER_ALLOWANCE)
    conftest.materialize_workflow(repaired, harness)
    proceeding = Runner(target, costs=[WRITER_COST, VERIFIER_COST])
    assert run(target, harness, proceeding) == 0
    assert proceeding.calls
    assert (run_dir_of(target) / "state.json").is_file()
    assert branches(target) != before


def test_a_per_stage_ceiling_is_refused_on_the_same_terms(environment, capsys):
    """The stage-level refusal end to end, naming the stage."""
    bad = build("refused-stage-workflow", writer=-1)
    target, harness = environment(bad)
    runner = Runner(target, costs=[WRITER_COST])

    assert run(target, harness, runner) == 1

    assert runner.calls == []
    assert not run_dir_of(target).exists()
    assert WRITING in capsys.readouterr().err


# --------------------------------------------------------------------------
# The allowance handed to one invocation, and that it is flat
# --------------------------------------------------------------------------


def drive_to_the_run_ceiling(target: Path, harness: Path) -> Runner:
    """Run the ceilinged workflow until it refuses to enter another stage.

    Every invocation stays inside its own allowance, so the only thing that
    can stop this run is the run ceiling: writing $4.50 and verifying $2.50
    against a ceiling of $18.00 reaches $18.50 on the third writing invocation
    and stops above the verifier that would have followed it.
    """
    runner = Runner(target, costs=[WRITER_COST, VERIFIER_COST],
                    verdicts=[failing("attempt-1"), failing("attempt-2"),
                              failing("attempt-3")])
    # Costs alternate by stage rather than by ordinal, so the list is built
    # from the calls the run actually makes.
    runner.cost_of = lambda ordinal: (
        WRITER_COST if runner.calls[ordinal] == WRITING else VERIFIER_COST)
    assert run(target, harness, runner) == 2, "the shape was meant to escalate"
    return runner


def test_each_invocation_is_handed_its_stages_whole_declared_allowance(
    environment,
):
    """The property the replan exists to establish, and the one a
    remainder-based implementation would silently violate.

    Several readings of one run, because they are readings of the same
    flatness: every invocation is handed exactly what its own stage declares;
    the second writing invocation is handed the whole allowance again after the
    first spent 90% of it; and the third is handed the whole allowance while
    the run has less than that left before its ceiling.
    """
    target, harness = environment(CEILINGED)
    runner = drive_to_the_run_ceiling(target, harness)

    assert runner.calls == [WRITING, VERIFYING, WRITING, VERIFYING, WRITING]
    for stage, budget in runner.budgets:
        assert budget == ALLOWANCES[stage], (stage, budget)

    # Flat across invocations of one stage: the first spent $4.50 of $5.00 and
    # the second is handed $5.00 rather than the $0.50 a cumulative stage
    # ceiling would leave it.
    assert runner.budget_at(WRITING, 0) == WRITER_ALLOWANCE
    assert runner.budget_at(WRITING, 1) == WRITER_ALLOWANCE

    # And flat against the run's remainder: by the third writing invocation the
    # entry has spent $14.00 of $18.00, so a budget reduced to what the run has
    # left would be $4.00 rather than the stage's $5.00.
    spent_before_the_third = 2 * (WRITER_COST + VERIFIER_COST)
    assert RUN_CEILING - spent_before_the_third < WRITER_ALLOWANCE
    assert runner.budget_at(WRITING, 2) == WRITER_ALLOWANCE


def test_a_stage_under_no_ceiling_is_invoked_with_no_budget_argument(environment):
    """A workflow declaring nothing calls the runner with exactly the arguments
    it passed before ceilings existed — asserted as the absence of the keyword
    rather than as a budget of None, which is a different call.

    The control is the same runner under the ceilinged definition, which does
    receive the keyword, so the absence is the coordinator withholding it
    rather than the recording looking in the wrong place.
    """
    target, harness = environment(UNCEILINGED)
    runner = Runner(target, costs=[WRITER_COST, VERIFIER_COST])

    assert run(target, harness, runner) == 0

    assert runner.calls == [WRITING, VERIFYING]
    for stage, extra in runner.extras:
        assert extra == {}, (stage, extra)
    assert all(budget is NO_BUDGET for _, budget in runner.budgets)

    ceilinged_target, ceilinged_harness = environment(CEILINGED)
    ceilinged = Runner(ceilinged_target, costs=[WRITER_COST, VERIFIER_COST])
    assert run(ceilinged_target, ceilinged_harness, ceilinged) == 0
    assert all(budget is not NO_BUDGET for _, budget in ceilinged.budgets)


def test_a_runner_with_only_todays_signature_still_drives_a_ceilingless_run(
    environment,
):
    """The same property from the runner's side, where it actually bites: a
    fake whose signature has no budget parameter at all. A coordinator that
    passed the keyword unconditionally would raise `TypeError` here.

    The control is the same runner under the ceilinged definition, where the
    coordinator does pass it and the call is refused — so this is a statement
    about what the ceiling-less path passes rather than about a signature that
    accepts anything.
    """
    target, harness = environment(UNCEILINGED)
    runner = RunnerWithoutTheBudgetParameter(
        target, costs=[WRITER_COST, VERIFIER_COST])

    assert run(target, harness, runner) == 0
    assert runner.calls == [WRITING, VERIFYING]

    ceilinged_target, ceilinged_harness = environment(CEILINGED)
    refused = RunnerWithoutTheBudgetParameter(
        ceilinged_target, costs=[WRITER_COST, VERIFIER_COST])
    with pytest.raises(TypeError, match="max_budget_usd"):
        run(ceilinged_target, ceilinged_harness, refused)


def test_a_ceilingless_run_routes_and_records_exactly_as_it_did_before(
    environment,
):
    """The rest of the compatibility property: no refusal, no change in
    routing, and — for the runner every fake in this suite still is, one
    reporting no cost at all — nothing added to the run directory either.

    A retry is driven rather than a straight-through run, so "routing is
    unchanged" is a statement about the path that actually branches.
    """
    target, harness = environment(UNCEILINGED)
    runner = Runner(target, costs=[None], verdicts=[failing("attempt-1"), PASS])

    assert run(target, harness, runner) == 0

    assert runner.calls == [WRITING, VERIFYING, WRITING, VERIFYING]
    assert not (run_dir_of(target) / "cost.json").exists()
    assert state_of(target)["entry_cost_usd"] == 0.0
    assert state_of(target)["stopped_on_cost"] is False
    assert [entry["event"] for entry in history(target)
            if entry["event"] == "budget-stopped"] == []


# --------------------------------------------------------------------------
# An invocation that spent its whole allowance
# --------------------------------------------------------------------------


def exhaust_the_allowance(target: Path, harness: Path, cost: float) -> Runner:
    """One writing invocation that reports `cost` and writes nothing.

    Writing nothing is the mechanical failure a self-route answers, and the
    stage declares a self-route budget — so what decides whether the stage runs
    again is the cost and nothing else. Only the first invocation reports a
    cost, so whatever the run does afterwards is decided by the work rather
    than by a figure this helper repeated into it.
    """
    runner = Runner(target, costs=[cost, None], skip_outputs={0},
                    verdicts=[PASS])
    code = run(target, harness, runner)
    return code, runner


def test_an_invocation_that_spent_its_allowance_does_not_run_again(environment):
    """A budget-truncated stage is a budget stop, not a mechanical failure. It
    keeps out of the self-route path, which would otherwise re-run the stage
    and spend the allowance a second time."""
    target, harness = environment(CEILINGED)

    code, runner = exhaust_the_allowance(target, harness, WRITER_ALLOWANCE)

    assert code == 2
    assert runner.calls == [WRITING]
    state = state_of(target)
    assert state["status"] == "escalated"
    assert state["stopped_on_cost"] is True
    reason = story_coordinator.escalation_reason(run_dir_of(target))
    assert story_coordinator.format_usd(WRITER_ALLOWANCE) in reason


def test_the_same_invocation_one_half_dollar_short_self_routes_as_before(
    environment,
):
    """The control, and the boundary. The identical failure reporting $4.50
    against an allowance of $5.00 is a mechanical failure and the stage runs
    again in place, which is what the budget stop above is being distinguished
    from."""
    target, harness = environment(CEILINGED)

    code, runner = exhaust_the_allowance(target, harness, WRITER_COST)

    assert code == 0
    assert runner.calls.count(WRITING) == 2
    assert state_of(target)["stopped_on_cost"] is False
    assert any("self-routed" == entry["event"] for entry in history(target))


# --------------------------------------------------------------------------
# The run ceiling
# --------------------------------------------------------------------------


def test_a_run_at_its_ceiling_does_not_enter_the_next_stage(environment):
    """The gate, and what it says. The run has spent $18.50 against a ceiling
    of $18.00, and the stage that would have followed is never invoked."""
    target, harness = environment(CEILINGED)
    runner = drive_to_the_run_ceiling(target, harness)
    spent = 3 * WRITER_COST + 2 * VERIFIER_COST

    assert runner.calls[-1] == WRITING, "the verifier was meant not to follow"
    assert state_of(target)["entry_cost_usd"] == spent
    assert spent >= RUN_CEILING

    reason = story_coordinator.escalation_reason(run_dir_of(target))
    assert story_coordinator.format_usd(spent) in reason
    assert story_coordinator.format_usd(RUN_CEILING) in reason
    assert VERIFYING in reason


def test_a_budget_stop_is_a_different_event_kind_from_an_escalation(environment):
    """A reader of the log can tell a stop about money from an escalation about
    the work. The kind is distinct from the `escalated` entry that follows it,
    and it is one the execution-history schema declares — a kind the enum never
    named would validate nowhere.

    The control is a run that escalated about the work, whose history carries
    the escalation kind and not this one.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)

    kinds = [entry["event"] for entry in history(target)]
    stops = [kind for kind in kinds if kind == "budget-stopped"]
    assert len(stops) == 1, kinds
    assert "escalated" in kinds
    assert stops[0] != "escalated"

    enum = schema_validator.load_schema(
        "execution-history")["items"]["properties"]["event"]["enum"]
    assert stops[0] in enum

    # The control: an ordinary escalation, under a definition declaring no
    # ceiling at all, records the escalation kind and never this one.
    other_target, other_harness = environment(UNCEILINGED)
    ordinary = Runner(other_target, costs=[None],
                      verdicts=[failing(f"attempt-{n}") for n in range(1, 6)])
    assert run(other_target, other_harness, ordinary) == 2
    ordinary_kinds = [entry["event"] for entry in history(other_target)]
    assert "escalated" in ordinary_kinds
    assert "budget-stopped" not in ordinary_kinds


def test_a_run_ceiling_of_zero_refuses_before_any_agent_is_invoked(environment):
    """Zero is a deliberate refusal to run at all, and it is checked above the
    first invocation rather than after it.

    The control is the identical definition with the ceiling absent, whose run
    invokes stages and completes — so zero and absent are shown to be different
    values rather than one falsy value serving both.
    """
    refusing = build("zero-ceiling-workflow", run_ceiling=0)
    target, harness = environment(refusing)
    runner = Runner(target, costs=[WRITER_COST, VERIFIER_COST])

    assert run(target, harness, runner) == 2

    assert runner.calls == []
    assert state_of(target)["stopped_on_cost"] is True
    assert not (run_dir_of(target) / "cost.json").exists()

    absent_target, absent_harness = environment(UNCEILINGED)
    unbounded = Runner(absent_target, costs=[WRITER_COST, VERIFIER_COST])
    assert run(absent_target, absent_harness, unbounded) == 0
    assert unbounded.calls == [WRITING, VERIFYING]


# --------------------------------------------------------------------------
# The live allowance on state.json
# --------------------------------------------------------------------------


def cumulative_totals(state: dict, stage_names: list[str]) -> list[str]:
    """Every key of a state file that keeps a total per stage.

    A per-stage total can be spelled either way, so both shapes are looked
    for: a key naming a stage, and a key whose value is a mapping that could be
    keyed by one. Neither may appear — the run ceiling is the only cumulative comparison
    the harness makes, and a second accumulator is a second thing to keep in
    step with the record.
    """
    return sorted(
        key for key, value in state.items()
        if any(name in key for name in stage_names) or isinstance(value, dict))


def test_state_keeps_one_spend_and_no_per_stage_total(environment):
    """The live allowance is one number: what the current entry has spent.

    The control is the same scan over the same state with both shapes of
    per-stage total planted in it, which reports both — so the empty result
    above is a scan that can see one.
    """
    target, harness = environment(CEILINGED)
    runner = drive_to_the_run_ceiling(target, harness)
    state = state_of(target)

    assert state["entry_cost_usd"] == 3 * WRITER_COST + 2 * VERIFIER_COST
    assert cumulative_totals(state, STAGE_NAMES) == []

    planted = {**state,
               f"{WRITING}_cost_usd": 13.5,
               "cost_by_stage": {WRITING: 13.5, VERIFYING: 5.0}}
    assert cumulative_totals(planted, STAGE_NAMES) == sorted(
        [f"{WRITING}_cost_usd", "cost_by_stage"])
    assert runner.calls, "the shape was meant to invoke something"


def test_a_state_file_written_before_this_story_still_loads(environment):
    """A run escalated before this story landed has neither field in its state
    file. It must load, reading as having spent nothing and as not having
    stopped on a ceiling.

    The control is a field no `RunState` declares, dropped into the same file:
    that one does fail to load, so the tolerance above is the defaults doing
    their work rather than the loader ignoring what it is given.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    path = run_dir_of(target) / "state.json"
    written_before = {key: value
                      for key, value in json.loads(path.read_text()).items()
                      if key not in ("entry_cost_usd", "stopped_on_cost")}
    path.write_text(json.dumps(written_before, indent=2) + "\n",
                    encoding="utf-8")

    loaded = story_coordinator.load_state(run_dir_of(target))
    assert loaded.entry_cost_usd == 0.0
    assert loaded.stopped_on_cost is False

    with pytest.raises(TypeError):
        story_coordinator.RunState(
            **{**written_before, "a_field_nobody_declares": 1})


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------


def test_the_record_holds_one_entry_per_invocation_that_reported_a_cost(
    environment,
):
    """One entry per invocation, carrying the stage, the entry, the attempt and
    the cost — read across a run in which one stage was invoked more than once,
    so an entry list that collapsed repeated invocations of a stage would be
    reported here rather than agreeing by accident.
    """
    target, harness = environment(CEILINGED)
    runner = drive_to_the_run_ceiling(target, harness)
    record = cost_record(target)

    assert [item["stage"] for item in record["invocations"]] == runner.calls
    assert runner.calls.count(WRITING) > 1, "the shape was meant to repeat one stage"
    assert [item["cost_usd"] for item in record["invocations"]] == [
        WRITER_COST if stage == WRITING else VERIFIER_COST
        for stage in runner.calls]
    # Every invocation belongs to the first entry of the run, and the attempts
    # are the ones the run took: one per retry, in the order they were taken.
    assert {item["entry"] for item in record["invocations"]} == {0}
    assert [item["attempt"] for item in record["invocations"]] == [1, 1, 2, 2, 3]


def test_the_total_is_the_sum_of_the_invocations_beside_it(environment):
    """The total and the list cannot disagree, checked against a sum this
    module computes rather than against the figure the record reports."""
    target, harness = environment(CEILINGED)
    runner = drive_to_the_run_ceiling(target, harness)
    record = cost_record(target)

    assert record["total_usd"] == sum(
        item["cost_usd"] for item in record["invocations"])
    assert record["total_usd"] == 3 * WRITER_COST + 2 * VERIFIER_COST
    assert story_coordinator.recorded_cost(run_dir_of(target)) == \
        record["total_usd"]
    assert len(runner.calls) == len(record["invocations"])


def test_an_invocation_reporting_no_cost_adds_nothing_to_either(environment):
    """The record carries what the harness was told and infers nothing, so an
    invocation reporting no cost is absent from the list rather than recorded
    as free.

    Driven on a run in which one invocation reports a cost and the other does
    not, so the absence is one entry missing from a record that exists rather
    than a record that was never written.
    """
    target, harness = environment(CEILINGED)
    runner = Runner(target, costs=[WRITER_COST, None])
    assert run(target, harness, runner) == 0

    record = cost_record(target)
    assert [item["stage"] for item in record["invocations"]] == [WRITING]
    assert record["total_usd"] == WRITER_COST
    assert state_of(target)["entry_cost_usd"] == WRITER_COST


def test_the_record_validates_against_the_schema_this_repository_ships(
    environment,
):
    """The record is held to its own schema, and the schema is in the manifest
    that declares what this harness ships.

    The control is the same record with a required field dropped, which the
    same validator reports — so a clean validation above is the validator
    checking something rather than a schema that constrains nothing.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    schema = schema_validator.load_schema("cost-record")
    record = cost_record(target)

    assert schema_validator.validate(record, schema) == []
    assert "cost-record" in schema_validator.shipped_schemas()

    stripped = {"total_usd": record["total_usd"],
                "invocations": [{key: value
                                 for key, value in record["invocations"][0].items()
                                 if key != "cost_usd"}]}
    assert schema_validator.validate(stripped, schema)


# --------------------------------------------------------------------------
# The allowance and the record across a resume
# --------------------------------------------------------------------------


def resume_after_the_ceiling(target: Path, harness: Path,
                             verdicts=None) -> tuple[int, Runner]:
    """Resume a run stopped on the run ceiling, changing nothing at all.

    Deliberately without touching the story, the branch or the harness: that
    the resume needs no such change is the story's point, and every other
    escalation is refused on exactly that evidence.
    """
    runner = Runner(target, costs=[VERIFIER_COST, WRITER_COST],
                    verdicts=verdicts or [PASS])
    runner.cost_of = lambda ordinal: (
        WRITER_COST if runner.calls[ordinal] == WRITING else VERIFIER_COST)
    return run(target, harness, runner), runner


def test_a_resume_funds_a_fresh_allowance_and_the_run_proceeds(environment):
    """The motivating case, at both ends: the run that stopped on $18.50 of an
    $18.00 ceiling resumes and enters the stage it refused to enter, rather
    than stopping again on the same spend."""
    target, harness = environment(CEILINGED)
    stopped = drive_to_the_run_ceiling(target, harness)
    spent = state_of(target)["entry_cost_usd"]
    assert spent >= RUN_CEILING

    code, resumed = resume_after_the_ceiling(target, harness)

    assert code == 0
    assert resumed.calls == [VERIFYING]
    assert state_of(target)["status"] == "completed"
    assert state_of(target)["stopped_on_cost"] is False
    assert state_of(target)["entry_cost_usd"] == VERIFIER_COST
    assert stopped.calls[-1] == WRITING


def test_the_record_continues_where_the_allowance_restarts(environment):
    """The two halves of the split, in one run.

    The allowance goes back to zero and the record does not: cost.json holds
    every invocation of the entry that ended *and* the invocations of the entry
    that resumed, and its total is the sum across both. An entry index
    distinguishes them, which is what lets a reader see which allowance covered
    which invocations.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    before = cost_record(target)
    assert before["invocations"]

    code, resumed = resume_after_the_ceiling(target, harness)
    assert code == 0

    after = cost_record(target)
    assert after["invocations"][:len(before["invocations"])] == \
        before["invocations"]
    assert len(after["invocations"]) == len(before["invocations"]) + len(
        resumed.calls)
    assert after["total_usd"] == before["total_usd"] + VERIFIER_COST
    assert [item["entry"] for item in after["invocations"]] == \
        [0] * len(before["invocations"]) + [1] * len(resumed.calls)
    # And the live allowance restarted rather than continuing: the entry now
    # running has spent only what its own invocation reported.
    assert state_of(target)["entry_cost_usd"] < after["total_usd"]


def test_the_record_stays_at_the_run_root_when_an_entry_is_archived(environment):
    """A re-entry moves what is keyed by a counter and leaves what accounts for
    the whole run. The record is the second kind.

    The control is the same directory's counter-keyed artifacts, which *are*
    under the archived entry — so "cost.json is not there" is a statement about
    a move that happened rather than about a re-entry that archived nothing.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    run_dir = run_dir_of(target)

    code, _ = resume_after_the_ceiling(target, harness)
    assert code == 0

    entry = story_coordinator.entry_dir(run_dir, 1)
    archived = sorted(path.relative_to(entry).as_posix()
                      for path in entry.rglob("*") if path.is_file())
    assert archived, "the re-entry archived nothing at all"

    assert (run_dir / "cost.json").is_file()
    assert not (entry / "cost.json").exists()
    assert "cost.json" not in story_coordinator.entry_artifacts(
        run_dir, CEILINGED["stages"])


def test_a_resume_reports_what_the_run_has_spent(environment):
    """What makes the reset a decision rather than an accident: the resume says
    what the record holds and what the entry that ended spent, and a resume of
    a run stopped on a ceiling says outright that it funds another allowance.

    The control is the resume of a run that escalated about the work, which
    reports the spend on the same terms and does not say that.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    spent = state_of(target)["entry_cost_usd"]

    code, _ = resume_after_the_ceiling(target, harness)
    assert code == 0

    notes = [line for line in events(target) if "recorded spend so far" in line]
    assert len(notes) == 1, notes
    assert story_coordinator.format_usd(spent) in notes[0]
    assert "allowance" in notes[0]

    # The control: a run under the same definition that escalated at the retry
    # ceiling rather than on money. Its resume reports the spend too, and says
    # nothing about funding a fresh allowance.
    other_target, other_harness = environment(
        build("work-escalation-workflow", writer=WRITER_ALLOWANCE,
              verifier=VERIFIER_ALLOWANCE))
    ordinary = Runner(other_target, costs=[None],
                      verdicts=[failing(f"attempt-{n}") for n in range(1, 6)])
    assert run(other_target, other_harness, ordinary) == 2
    write(other_target / "src" / "app.py", APP_AT_HEAD + "print('decided')\n")
    git(other_target, "add", "-A")
    git(other_target, "commit", "-q", "-m", "decided")
    resumed = Runner(other_target, costs=[None], verdicts=[PASS])
    assert run(other_target, other_harness, resumed) == 0

    other_notes = [line for line in events(other_target)
                   if "recorded spend so far" in line]
    assert len(other_notes) == 1, other_notes
    assert "fund another allowance" not in other_notes[0]
    assert "fund another allowance" in notes[0]


def test_a_budget_stop_is_resumable_on_evidence_that_refuses_every_other(
    environment,
):
    """The exemption, stated against the guard's own answer.

    `unchanged_since_escalation` establishes, for the very run resumed above,
    that the story, the branch and the harness are all what they were — which
    is the evidence it refuses every other escalation on. The budget stop
    resumes anyway, because resuming is the decision the ceiling exists to ask
    for.

    The control is an ordinary escalation in exactly that state, which the same
    coordinator refuses.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    story_text = (target / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text(
        encoding="utf-8")
    state = story_coordinator.load_state(run_dir_of(target))
    evidence = story_coordinator.unchanged_since_escalation(
        state, story_text, target, harness)
    assert evidence, "the guard was meant to have something to refuse on"

    code, resumed = resume_after_the_ceiling(target, harness)
    assert code == 0
    assert resumed.calls

    # The control: an escalation about the work, in the same unchanged state.
    other_target, other_harness = environment(
        build("refused-resume-workflow", writer=WRITER_ALLOWANCE,
              verifier=VERIFIER_ALLOWANCE))
    ordinary = Runner(other_target, costs=[None],
                      verdicts=[failing(f"attempt-{n}") for n in range(1, 6)])
    assert run(other_target, other_harness, ordinary) == 2
    other_state = story_coordinator.load_state(run_dir_of(other_target))
    assert other_state.stopped_on_cost is False
    assert story_coordinator.unchanged_since_escalation(
        other_state, story_text, other_target, other_harness)

    refused = Runner(other_target, costs=[None], verdicts=[PASS])
    assert run(other_target, other_harness, refused) == 1
    assert refused.calls == []


# --------------------------------------------------------------------------
# What the two reports say about money
# --------------------------------------------------------------------------


def test_a_completed_run_reports_what_it_cost(environment):
    """Read across a resume, so the figure is the record's — which spans every
    entry — rather than the entry-scoped allowance, which would understate it.

    The control is the allowance at the moment the report is written, which is
    smaller: a report stating that number would satisfy a bare "a dollar figure
    appears" assertion while being wrong.
    """
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)
    code, _ = resume_after_the_ceiling(target, harness)
    assert code == 0

    total = cost_record(target)["total_usd"]
    allowance = state_of(target)["entry_cost_usd"]
    assert allowance < total
    report = (run_dir_of(target) / "completion-report.md").read_text(
        encoding="utf-8")
    assert story_coordinator.format_usd(total) in report


def test_an_escalated_runs_summary_reports_what_it_cost(environment):
    """The same fact on the other outcome, and the one a developer reading a
    stop most needs: the summary states the run's total beside the entry's own
    spend, which is what the ceiling was compared against."""
    target, harness = environment(CEILINGED)
    drive_to_the_run_ceiling(target, harness)

    summary = (run_dir_of(target) / "escalation-summary.md").read_text(
        encoding="utf-8")
    total = cost_record(target)["total_usd"]
    assert story_coordinator.format_usd(total) in summary
    assert story_coordinator.format_usd(
        state_of(target)["entry_cost_usd"]) in summary
