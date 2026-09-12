"""The coordinator runs the suite a stage declares, and the stage authors it.

A stage that authors validation used to run the target's whole suite inside its
own turn. This story moves that run to the coordinator: the stage declares
`suite_run`, ends its turn, and the coordinator runs the configured
`test_command` as a subprocess and reads its exit status.

Zero advances the workflow. Non-zero used to bring the declaring stage back in
place on its own self-route budget, and since story-137 it advances too: the
failure is recorded in state, a line naming it is appended to the event stream,
and the run carries on to the next stage, so the failure reaches the verifier —
the agent that already reads the workflow's retry_routing table — instead of
being attributed by a coordinator that cannot tell which stage broke the tree.

Every case below is driven through `story_coordinator.run_story` with a fake
agent runner, against a target repository built under `tmp_path`. What is
asserted is what a real run wrote: the records in the run directory, the event
stream, `state.json`, the prompts the coordinator rendered and the exit code.
Nothing here calls the check function on its own, because the subject is a
*routing decision* and calling the check alone would leave the decision under
test unexercised. Nothing here invokes a model.

The workflow those runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns. The mechanism is the
subject; the stage list, the budgets and the artifact names are inputs to it,
and deriving them from what this repository deploys would make a deployment
fact into something this module enforces. Every name is still derived rather
than written — from the fixture's definition rather than from the shipped one.
The two places the shipped artifacts *are* the subject — what
`prompts/story-verifier.md` and `prompts/story-tester.md` say, and what the schemas
declare — read what this repository ships, deliberately.

The target's suite is a script this module writes into the target: it prints
far more than the retained tail can hold, announces a count in a framework's
own words, and exits zero exactly when a sentinel file under the governed
prefix has been repaired. That is what lets one command drive a red suite, a
green one, a revert check that finds the edits were needed, and an output long
enough for the tail to lose its own beginning.

Every absence asserted here carries a demonstration that it can fail:

  * "the record carries no count of tests" sits beside the same scan over a
    record with a count planted in it, and beside the counts the suite
    announced being present in the output the record passes on as text;
  * "a red suite records no self-route" — no self-route artifact, no
    self-routed event, `self_route_count` unmoved — sits beside a run through
    the same fixture whose declaring stage withholds a required output, which
    self-routes on the budget that stage declares, so the absence is a fact
    about the red suite rather than about a stage that could never self-route;
  * "a workflow declaring no suite run writes no record and records no
    outstanding failure" sits beside the identical run under the declaring
    workflow, which does both;
  * "the early marker is absent from the retained tail" is what makes the
    file's copy of it evidence, and both halves are asserted of each of the
    three coordinator suite runs;
  * "the stage was told nothing of a suite run on its first invocation" sits
    beside its second invocation, which carries the record;
  * "prompts/story-tester.md carries no instruction to run the suite" sits beside
    the same scan over a text carrying the instruction that was removed, which
    the scan reports;
  * "no orchestration module derives a count of tests" sits beside the same
    scan over that source with a count derivation planted in it.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns
it to the documenter, the stage that runs after this one.
"""
import json
import shlex
import sys
from pathlib import Path

import pytest

import conftest
import context_assembler
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import StageRef, workflow_stage

# The target builder is tests/test_self_routing_retry.py's: a repository with a
# configurable workflow and a configurable test command, which is exactly what
# these runs need. Reused rather than copied so a regression in it reddens both
# files.
from test_self_routing_retry import build_target, write, write_json

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATION_DIR = REPO_ROOT / "orchestration"
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

STORY_ID = "story-001"

# --------------------------------------------------------------------------
# The target's suite
# --------------------------------------------------------------------------

#: The prefix the declaring stage is restricted from creating under, and the
#: file beneath it the suite's verdict depends on. Repairing that file is what
#: a stage does here instead of authoring tests, so a red suite and a green one
#: differ by something a stage did rather than by a flag this module set.
GOVERNED_PREFIX = "checks/"
SENTINEL = "checks/keep.txt"
REPAIRED = "repaired"

#: The first thing the suite prints. Longer output than the retained tail can
#: hold follows it, so this line is exactly the content a reader of the tail
#: alone has lost — which is what the full-output file is for.
EARLY_MARKER = "EARLY-MARKER the first line this run printed"

#: A framework's own summary line, in a framework's own words. Nothing in the
#: harness may turn these numbers into fields; they are here so "no count is
#: derived" is asserted against output that offers two.
ANNOUNCED_PASSED = 42
ANNOUNCED_FAILED = 7
SUMMARY_LINE = f"{ANNOUNCED_PASSED} passed, {ANNOUNCED_FAILED} failed in 3.21s"

CHECK_SCRIPT = f'''\
"""The target's whole suite, as far as this module's runs are concerned."""
import pathlib
import sys

print("{EARLY_MARKER}")
for index in range(400):
    print(f"line {{index}}: " + "verbose output that a tail cannot all hold")
print("{SUMMARY_LINE}")
state = pathlib.Path("{SENTINEL}").read_text(encoding="utf-8").strip()
print("LATE-MARKER the run is about to exit")
sys.exit(0 if state == "{REPAIRED}" else 1)
'''

#: The configured command, spelled as an interpreter invocation rather than a
#: shell builtin: the check runs the command directly, not through a shell.
TEST_COMMAND = shlex.join([sys.executable, "check.py"])

#: A command whose executable does not exist, which is how the check reports
#: that it could not run at all.
UNRUNNABLE_COMMAND = shlex.join(
    [str(Path("no-such-runner-anywhere-on-this-machine")), "--all"])

TAIL = story_coordinator.CLEAN_CLONE_OUTPUT_TAIL

# --------------------------------------------------------------------------
# The workflow these runs execute
# --------------------------------------------------------------------------

#: The artifact the fixture's declaration names. Deliberately not the name this
#: repository deploys: the record reaching the run directory under this name is
#: what says the coordinator reads the name off the declaration rather than
#: carrying one of its own.
SUITE_ARTIFACT = "suite-probe-result.json"

#: The declaration the revert check reads its baseline from, named here once so
#: the all-three-runs fixture below and the assertions over it agree.
REVERT_ARTIFACT = "revert-probe-result.json"

BUDGET = 2


def declaring_stage(**extra) -> dict:
    """The stage that authors validation and declares the suite run.

    It declares a self-route budget above one, which since story-137 is what
    makes "no self-route was taken for the red suite" worth asserting: this
    stage *can* self-route, and a run below shows it doing so for a required
    output it did not write, so the absence beside a red suite is a fact about
    the routing rather than about a stage with nowhere to go.
    """
    return workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        max_self_routes=BUDGET,
        suite_run={"result": SUITE_ARTIFACT},
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"},
        **extra)


def verifying_stage() -> dict:
    return workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": StageRef(0)},
        retry_routing={"the-work": {
            "stage": StageRef(0),
            "when": "the behaviour the story asked for is missing"}})


WORKFLOW = conftest.build_workflow(
    declaring_stage(), verifying_stage(),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="suite-run-workflow")

#: The same workflow with the revert check declared too, so one run makes all
#: three of the coordinator's whole-suite runs and each one's full output can
#: be looked for.
ALL_THREE = conftest.build_workflow(
    declaring_stage(may_not_create=(GOVERNED_PREFIX,),
                    revert_check={"result": REVERT_ARTIFACT,
                                  "baseline": "stage-baseline"}),
    verifying_stage(),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="all-three-suite-runs-workflow")

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
DECLARING, VERIFYING = STAGE_NAMES

DECLARATION = story_coordinator.suite_run_declaration(WORKFLOW["stages"])
RETRY_CATEGORY = next(iter(
    WORKFLOW["stages"][1]["on_failure"]["retry_routing"]))

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

FAILED = {
    "status": "failed",
    "blocking_issues": [{"severity": "high", "issue": "the work is not done",
                         "location": "src/app.py",
                         "required_behavior": "the sample behavior exists"}],
    "unverified": [], "retry_recommended": True, "retry_target": RETRY_CATEGORY,
}

GUIDANCE = {
    "current_focus": [{"focus": "make the sample behavior exist",
                       "satisfied_when": "the sample behavior exists"}],
    "preserve_behavior": ["the existing behavior"],
    "retry_scope": ["src/"],
}


def test_the_fixture_declares_the_check_on_one_stage_with_room_to_self_route():
    """The premises every case below rests on, stated so a change to the
    fixture reddens here rather than quietly emptying the assertions."""
    declaring = [s for s in WORKFLOW["stages"] if "suite_run" in s]
    assert [s["name"] for s in declaring] == [DECLARING]
    assert DECLARATION == {"result": SUITE_ARTIFACT}
    assert declaring[0]["max_self_routes"] == BUDGET >= 2


def test_the_declared_artifact_name_is_one_the_harness_does_not_carry():
    """What makes "the name comes off the declaration" checkable: this name
    appears nowhere in the coordinator, so a record found under it in a run
    directory got there from the workflow."""
    assert SUITE_ARTIFACT not in COORDINATOR_SOURCE
    assert SUITE_ARTIFACT in json.dumps(WORKFLOW)


# --------------------------------------------------------------------------
# The target, and the fake runner that drives it
# --------------------------------------------------------------------------


def build_suite_target(root: Path, *, workflow: str = WORKFLOW["name"],
                       test_command: str = TEST_COMMAND) -> Path:
    """A target repository whose configured suite is the script above."""
    build_target(root, workflow=workflow, test_command=test_command)
    write(root / "check.py", CHECK_SCRIPT)
    write(root / SENTINEL, "the state the stage found\n")
    conftest.commit_setup(root, "the suite this target runs")
    return root


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so one test can hold a subject and its control side by side."""
    def make(name: str, **kwargs) -> Path:
        return build_suite_target(tmp_path / name, **kwargs)
    return make


@pytest.fixture
def target_root(make_target) -> Path:
    return make_target("suite-run-target")


#: The context field the coordinator injects the coordinator's suite-run record
#: under. `conftest.BUILT_PROMPT_FIELDS` predates it, and the fixture says a
#: module needing a field it does not list passes its own template — which is
#: what this does, rather than widening the shared list and changing what every
#: other module's built prompts render.
SUITE_FIELD = "suite_run_result"

PROMPTS = {name: (conftest.built_stage_prompt(name)
                  + f"{SUITE_FIELD}:\n{{{{{SUITE_FIELD}}}}}\n")
           for name in STAGE_NAMES}


def materialize(workflow: dict, root: Path, prompts: dict | None = None) -> Path:
    return conftest.materialize_workflow(workflow, root,
                                         prompts=prompts or PROMPTS)


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the built definition, so every case below drives
    a real coordinator loading a real file."""
    return materialize(WORKFLOW, tmp_path / "suite-run-harness")


BROKEN = "broken"     #: the stage leaves the sentinel as it found it
REPAIR = "repair"     #: the stage repairs it, so the suite exits zero


def plan_touching_nothing_after(declaring: list[str]) -> dict:
    """`declaring`'s plan, with the judging stage told to touch nothing.

    The runner's default action is REPAIR, which was harmless while a red suite
    brought the declaring stage back before any other stage ran: the judging
    stage was never reached over a failing suite. Since story-137 it is, and a
    default that repairs would have the stage that *judges* the failure quietly
    fix it — so every run below that carries a failure forward says what the
    judging stage does with the sentinel rather than letting the default say
    it. One more invocation than the declaring stage's plan allows for, so the
    default is never reached on either stage.
    """
    return {DECLARING: list(declaring),
            VERIFYING: [BROKEN] * (len(declaring) + 1)}


def _nth(sequence: list, index: int, default):
    if not sequence or index >= len(sequence):
        return default
    return sequence[index]


class Runner:
    """A fake agent runner that writes each stage's declared artifacts.

    Its plan says, per stage and per invocation, whether that invocation
    repairs the sentinel the target's suite reads. Every artifact it writes
    comes off the stage's declaration in the *loaded* workflow rather than off
    a list written here.
    """

    def __init__(self, target_root: Path, plan: dict | None = None,
                 verdicts: list | None = None, workflow: dict | None = None):
        self.target_root = Path(target_root)
        # The tree the run works in, which since story-117 is a worktree of its
        # own rather than the tree the run was invoked from. The sentinel the
        # target's suite reads has to be repaired there, because that is the
        # tree the coordinator runs the suite against.
        self.tree = conftest.run_root_for(target_root)
        self.run_dir = run_dir_of(target_root)
        self.plan = plan or {}
        self.verdicts = list(verdicts or [PASS])
        self.stages = (workflow or WORKFLOW)["stages"]
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    def _declaration(self, stage: str) -> dict:
        return next(s for s in self.stages if s["name"] == stage)

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, run_dir=None):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        call = self.calls.count(stage)

        action = _nth(self.plan.get(stage, []), call - 1, REPAIR)
        changed: list[str] = []
        if action == REPAIR:
            if not self._repaired():
                write(self.tree / SENTINEL, f"{REPAIRED}\n")
            # Named whether or not this invocation had to write it: the
            # coordinator compares the tree the attempt began on against the
            # tree the turn ended on, so an invocation that found the content
            # it wanted already there is still the one whose record has to
            # account for it.
            changed = [SENTINEL]

        verdict = conftest.answering_guidance(
            self.verdicts[min(self.calls.count(VERIFYING) - 1,
                              len(self.verdicts) - 1)],
            self.run_dir)
        for artifact in story_coordinator.required_artifacts(
                self._declaration(stage)):
            self._write(artifact, stage, call, verdict, changed)
        if stage == VERIFYING and verdict.get("retry_recommended"):
            write_json(self.run_dir / "retry-guidance.json", GUIDANCE)
        return AgentResult(ok=True, result_text=f"{stage} done")

    def _repaired(self) -> bool:
        return (self.tree / SENTINEL).read_text(
            encoding="utf-8").strip() == REPAIRED

    def _write(self, artifact: str, stage: str, call: int, verdict: dict,
               changed: list[str]) -> None:
        path = self.run_dir / artifact
        if artifact == conftest.VERIFICATION_RESULT:
            write_json(path, verdict)
        elif artifact.endswith("changed-files.json"):
            write_json(path, {"modified": list(changed), "created": [],
                              "deleted": []})
        elif artifact == conftest.TEST_RESULTS:
            # What the narrowed schema asks of the author: what it wrote, and
            # nothing about what running it produced.
            write_json(path, {"tests_written": 1})
        else:
            write(path, f"{artifact} written by {stage} call {call}.\n")


class WithholdingRunner(Runner):
    """The same fake runner, with one invocation's required output withheld.

    `withhold` maps an invocation number of a stage to the artifact that
    invocation does not write, which is the mechanical failure the coordinator
    *does* route back in place. It exists so the absence a red suite leaves can
    be shown beside a self-route the same stage of the same workflow takes.
    """

    def __init__(self, target_root: Path, plan: dict | None = None,
                 verdicts: list | None = None, workflow: dict | None = None,
                 *, withhold: dict | None = None):
        super().__init__(target_root, plan, verdicts, workflow)
        self.withhold = dict(withhold or {})

    def _write(self, artifact, stage, call, verdict, changed):
        if self.withhold.get(call) == artifact:
            return
        super()._write(artifact, stage, call, verdict, changed)


class SnapshotRunner(Runner):
    """The same fake runner, recording what the run directory held at the top
    of every invocation.

    The coordinator saves the state before each stage iteration, so the file an
    invocation opens on holds what the iteration before it decided. That is
    where the cost of the carry-forward is observable *at the moment it
    happens*, rather than only in whatever the run ended with — which matters
    here, because the verdict the carry-forward advances into goes on to route
    a retry of its own.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshots: list[dict] = []
        self.attempts_dir_at: list[bool] = []
        self.retry_history_at: list[bool] = []

    def __call__(self, prompt, **kwargs):
        self.snapshots.append(state_of(self.run_dir))
        self.attempts_dir_at.append((self.run_dir / "attempts").exists())
        self.retry_history_at.append(
            (self.run_dir / "retry-history.json").exists())
        return super().__call__(prompt, **kwargs)


def run_dir_of(target_root: Path) -> Path:
    return conftest.run_dir_for(Path(target_root), STORY_ID)


def drive(target_root: Path, harness: Path, plan: dict | None = None,
          verdicts: list | None = None, workflow: dict | None = None):
    """One run, returning its exit code, its runner and its run directory."""
    runner = Runner(target_root, plan, verdicts, workflow)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def record_of(run_dir: Path, artifact: str = SUITE_ARTIFACT) -> dict:
    return read_json(run_dir / artifact)


def state_of(run_dir: Path) -> dict:
    return read_json(run_dir / "state.json")


def history_of(run_dir: Path) -> list[dict]:
    return read_json(run_dir / "execution-history.json")


def events_of(run_dir: Path) -> list[str]:
    return [entry["event"] for entry in history_of(run_dir)]


def self_route_records(run_dir: Path) -> list[tuple[str, dict]]:
    return [(path.name, read_json(path))
            for path in sorted(Path(run_dir).glob("self-route-*.json"))]


def rendered_prompt(run_dir: Path, stage: str, attempt: int = 1,
                    try_number: int = 0) -> str:
    """The prompt one invocation was given, read back off the run directory,
    through the coordinator's own name-shaping function rather than a second
    spelling of it here."""
    return (Path(run_dir) / story_coordinator.prompt_file(
        stage, attempt, try_number)).read_text(encoding="utf-8")


@pytest.fixture
def green_run(target_root, harness_root):
    """The central passing run: the declaring stage repairs the sentinel, so
    the suite the coordinator runs after its turn exits zero."""
    return drive(target_root, harness_root)


@pytest.fixture
def carried_forward_run(target_root, harness_root):
    """The central failing run: the declaring stage's first invocation leaves
    the suite red, the run carries the failure forward to the verifier, the
    verifier fails the verdict and names the category the table routes back to
    the declaring stage, and the invocation that retry brings repairs it.

    Two coordinator suite runs of the declaring stage, in two attempts rather
    than in two tries of one — which is what a carried-forward failure makes
    the shape of a red-then-green story.
    """
    return drive(target_root, harness_root,
                 plan_touching_nothing_after([BROKEN, REPAIR]),
                 verdicts=[FAILED, PASS])


# --------------------------------------------------------------------------
# The declared suite run happens, and is recorded
# --------------------------------------------------------------------------


def test_the_declared_suite_run_executes_the_configured_command(green_run):
    """After the stage's turn ends, not inside it: the record is in the run
    directory under the name the declaration gave, and it ran the target's
    configured command with the target repository as its working directory."""
    code, _, run_dir = green_run
    record = record_of(run_dir)

    assert code == 0
    assert record["ran"] is True
    assert record["command"] == TEST_COMMAND
    assert record["runner"] == shlex.split(TEST_COMMAND)[0]
    assert record["exit_code"] == 0
    # The command reads a file by a path relative to the target repository, so
    # a run that produced this output ran there.
    assert SUMMARY_LINE in record["output_tail"]


def test_a_passing_suite_advances_with_one_invocation_of_the_declaring_stage(
    green_run,
):
    """The cost of the passing case: the stage is invoked once and the workflow
    moves on with no further invocation of it."""
    code, runner, run_dir = green_run
    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert runner.calls.count(DECLARING) == 1
    assert state_of(run_dir)["status"] == "completed"
    assert not self_route_records(run_dir)


def test_the_check_announces_itself_before_the_run_it_is_about_to_make(
    green_run,
):
    """A suite run takes as long as a suite takes, so the console cannot sit
    silent for it. Read off the event stream, which is where the coordinator
    records the announcement, and the whole stream still validates."""
    _, _, run_dir = green_run
    announcements = [e for e in history_of(run_dir)
                     if e["event"] == "suite-rerun-started"
                     and e["stage"] == DECLARING]
    assert len(announcements) == 1
    assert announcements[0]["artifacts"] == [SUITE_ARTIFACT]
    assert schema_validator.validate(
        history_of(run_dir),
        schema_validator.load_schema("execution-history")) == []


def test_the_record_validates_against_the_schema_the_manifest_registers(
    green_run,
):
    _, _, run_dir = green_run
    schema = schema_validator.load_schema("suite-run-result")
    manifest = read_json(REPO_ROOT / "schemas" / "manifest.json")

    assert "suite-run-result" in manifest["schemas"]
    assert "suite-run-result" in schema_validator.shipped_schemas(REPO_ROOT)
    assert schema_validator.unsupported_keywords(schema) == []
    assert schema_validator.validate(record_of(run_dir), schema) == []


def test_a_record_missing_a_required_field_is_reported_by_that_schema(green_run):
    """Control: the validation above must be able to fail."""
    _, _, run_dir = green_run
    schema = schema_validator.load_schema("suite-run-result")
    record = record_of(run_dir)
    for field in schema["required"]:
        stripped = {k: v for k, v in record.items() if k != field}
        assert schema_validator.validate(stripped, schema), field


def test_that_schema_appears_in_no_stages_schemas_map():
    """The coordinator writes this record, not an agent, so no stage is asked
    to satisfy it — as with clean-clone-result and revert-check-result."""
    shipped = conftest.shipped_workflow()
    for stage in shipped["stages"]:
        assert "suite-run-result" not in stage.get("schemas", {}).values()
    assert SUITE_ARTIFACT not in json.dumps(shipped)


# --------------------------------------------------------------------------
# The record carries an exit status and no count of tests
# --------------------------------------------------------------------------


#: Every field name the record may carry, off the schema this repository ships
#: rather than written here.
RECORD_FIELDS = set(
    schema_validator.load_schema("suite-run-result")["properties"])

#: What a count of tests would be called if one had been derived. The names the
#: narrowed test-results schema removed, plus the shapes a framework's summary
#: line would most plausibly arrive as.
COUNT_FIELDS = ("tests_run", "tests_passed", "tests_failed", "failures",
                "passed", "failed", "total")


def counts_in(record: dict) -> list[str]:
    """Every field of a record that looks like a count of tests.

    A list rather than an assertion so the same statement can be made of a
    record that does carry one, which is the control.
    """
    found = [field for field in record if field in COUNT_FIELDS]
    found += [f"{field} is a number" for field, value in record.items()
              if isinstance(value, int) and not isinstance(value, bool)
              and field != "exit_code"]
    return sorted(found)


def test_the_record_carries_an_exit_code_and_no_count_of_tests(green_run):
    """The suite announced a count in its own words. The record carries the
    exit status, the command, the tail and the path — and no number derived
    from that line."""
    _, _, run_dir = green_run
    record = record_of(run_dir)

    assert set(record) <= RECORD_FIELDS
    assert counts_in(record) == []
    assert record["exit_code"] == 0


def test_the_announced_counts_survive_only_as_the_text_of_the_output(green_run):
    """The other half: the numbers are not lost, they are simply not fields.
    The output the record passes on carries the framework's own line
    verbatim."""
    _, _, run_dir = green_run
    record = record_of(run_dir)
    assert SUMMARY_LINE in record["output_tail"]
    assert SUMMARY_LINE in Path(record["output_path"]).read_text(
        encoding="utf-8")


def test_the_same_scan_reports_a_record_that_does_carry_a_count(green_run):
    """Control for the absence above: with a count planted in it, the same scan
    reports it — so a green result is a fact about the record rather than about
    where this test is looking."""
    _, _, run_dir = green_run
    planted = {**record_of(run_dir), "tests_passed": ANNOUNCED_PASSED}
    assert counts_in(planted) == sorted(
        ["tests_passed", "tests_passed is a number"])


# --------------------------------------------------------------------------
# A red suite is carried forward to the stage after the declaring one
# --------------------------------------------------------------------------


def test_a_red_suite_advances_to_the_next_stage(carried_forward_run):
    """The invocation after the failure is the *next* stage rather than the
    declaring one run again, so the failure reaches the stage that judges it.

    What brings the declaring stage back afterwards is the ordinary retry the
    verifier's failed verdict routes, which is a different mechanism spending
    different budget, and the run then reaches the end green.
    """
    code, runner, run_dir = carried_forward_run

    assert code == 0
    assert runner.calls == [DECLARING, VERIFYING, DECLARING, VERIFYING]
    assert state_of(run_dir)["status"] == "completed"
    assert record_of(run_dir)["exit_code"] == 0


def test_the_red_suite_records_no_self_route_of_any_kind(carried_forward_run):
    """The absence the story is about, in the three places a self-route is
    visible: the record artifact, the event, and the counter on the state.

    The counter is read off the state the *verifying* stage opened on rather
    than off the state the run ended with, because the count is live: it is
    zeroed when a stage completes and a run that had spent it would read as
    zero at the end either way.
    """
    _, _, run_dir = carried_forward_run

    assert self_route_records(run_dir) == []
    assert [e for e in history_of(run_dir) if e["event"] == "self-routed"] == []
    assert state_of(run_dir)["self_route_count"] == 0
    assert not (run_dir / story_coordinator.prompt_file(DECLARING, 1, 1)).exists()


def test_the_same_stage_does_self_route_when_it_withholds_an_output(
    make_target, harness_root,
):
    """The control for the absence above.

    The same stage of the same workflow, whose declared budget is therefore the
    same budget, meeting a cause that *is* routed back in place: a required
    output it did not write. It re-runs, writes a self-route record, appends a
    self-routed event and moves the counter — so the emptiness beside the red
    suite is a fact about which failures route back rather than about a stage
    that could not have self-routed whatever happened to it.
    """
    target_root = make_target("withholds-an-output")
    runner = WithholdingRunner(target_root, {DECLARING: [REPAIR]},
                               withhold={1: conftest.TEST_RESULTS})
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)

    assert code == 0
    assert runner.calls == [DECLARING, DECLARING, VERIFYING]
    assert [record["failure"] for _, record in self_route_records(run_dir)] == [
        story_coordinator.MISSING_REQUIRED_ARTIFACTS]
    assert [e["stage"] for e in history_of(run_dir)
            if e["event"] == "self-routed"] == [DECLARING]
    assert (run_dir / story_coordinator.prompt_file(DECLARING, 1, 1)).is_file()


def test_the_carry_forward_spends_no_retry_at_the_moment_it_happens(
    target_root, harness_root,
):
    """Nothing a retry spends is spent by the advance itself.

    Observed at the verifying stage's first invocation — the iteration the
    carry-forward advanced into — rather than at the end of the run, because
    the verdict that verification returns does route a retry and would spend
    all three afterwards. Its control is the state that same run ends with,
    where the retry the *verifier* asked for has moved every one of them.
    """
    runner = SnapshotRunner(target_root,
                            plan_touching_nothing_after([BROKEN, REPAIR]),
                            verdicts=[FAILED, PASS])
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    run_dir = run_dir_of(target_root)
    at_the_verifier = runner.snapshots[1]

    assert code == 0
    assert at_the_verifier["retry_count"] == 0
    assert runner.attempts_dir_at[1] is False
    assert runner.retry_history_at[1] is False

    ended = state_of(run_dir)
    assert ended["retry_count"] == 1
    assert (run_dir / "attempts" / "attempt-1").is_dir()
    assert (run_dir / "retry-history.json").is_file()


# --------------------------------------------------------------------------
# The advance is recorded where a reader of events.log meets it
# --------------------------------------------------------------------------


def carry_forward_lines(run_dir: Path) -> list[dict]:
    """Every event whose message says a suite failure was carried forward.

    Read by what the message says rather than by an event kind, because the
    kind it is appended under is a shared one — a list rather than an
    assertion, so the same reading can be made of a run whose suite never
    failed, which is the control.
    """
    return [entry for entry in history_of(run_dir)
            if "carrying the failure forward" in entry["message"]]


def test_the_events_log_says_the_red_suite_was_carried_forward(
    carried_forward_run,
):
    """Without this line a reader of events.log meets an advance and has no way
    to tell it from an advance over a passing suite. So the line names the
    declaring stage, the exit code, and the pair the failing run's evidence was
    retained under — and both paths it names are files that exist and hold that
    failing run.
    """
    _, _, run_dir = carried_forward_run
    (line,) = carry_forward_lines(run_dir)
    failed_result = story_coordinator.retained_suite_result_file(
        SUITE_ARTIFACT, DECLARING, 1, 0)
    failed_output = str(run_dir / story_coordinator.suite_output_file(
        failed_result))

    assert line["stage"] == DECLARING
    assert DECLARING in line["message"]
    assert "exited 1" in line["message"]
    assert failed_result in line["message"]
    assert failed_output in line["message"]
    assert read_json(run_dir / failed_result)["exit_code"] == 1
    assert Path(failed_output).is_file()
    assert schema_validator.validate(
        history_of(run_dir),
        schema_validator.load_schema("execution-history")) == []


def test_a_run_whose_suite_never_failed_carries_no_such_line(green_run):
    """The control for the reading above: the same scan over a run that made
    the same declared suite run and passed it finds nothing, so the line is
    written where a failure was met rather than at every advance."""
    _, _, run_dir = green_run
    assert carry_forward_lines(run_dir) == []
    assert record_of(run_dir)["exit_code"] == 0


def test_the_verifier_is_handed_the_failing_suite_record(carried_forward_run):
    """What the carry-forward is *for*, read off the prompt the coordinator
    rendered: the stage the run advanced into was given the record of the run
    that failed, and the path to the whole of its output.

    The declaring stage's own first prompt is the control beside it — it
    carries no suite record at all, because no suite had run when it was
    written.
    """
    _, runner, run_dir = carried_forward_run
    first_verification = runner.prompts[VERIFYING][0]

    assert '"exit_code": 1' in first_verification
    assert str(run_dir / story_coordinator.suite_output_file(
        SUITE_ARTIFACT)) in first_verification
    assert '"exit_code"' not in runner.prompts[DECLARING][0]
    # And the same thing is on disk under that invocation's prompt filename.
    assert '"exit_code": 1' in rendered_prompt(run_dir, VERIFYING, 1)


# --------------------------------------------------------------------------
# A suite that stays red still stops the run, through the verifier's retries
# --------------------------------------------------------------------------


#: More invocations than any run of this fixture can take. The plan is made
#: longer than the run so that the run ending is the ceiling being reached
#: rather than the plan running out and the default repairing the suite; the
#: assertions below hold the run to being shorter than this, which is what says
#: the plan never ran out.
MORE_THAN_ANY_RUN_TAKES = 10


@pytest.fixture
def never_repaired_run(target_root, harness_root):
    """Every invocation leaves the suite red and every verdict fails it.

    The failure is carried forward each time, so what ends this run is the
    retry ceiling the verifier's own recommendations spend — the declaring
    stage's self-route budget is never touched, a red suite no longer being
    among the causes it is spent on.
    """
    return drive(target_root, harness_root,
                 plan_touching_nothing_after(
                     [BROKEN] * MORE_THAN_ANY_RUN_TAKES),
                 verdicts=[FAILED])


def test_a_suite_that_stays_red_stops_the_run_at_the_retry_ceiling(
    never_repaired_run,
):
    code, runner, run_dir = never_repaired_run
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert "retries are exhausted" in reason
    # Every red suite advanced rather than re-running the stage: the declaring
    # stage and the judging one were invoked the same number of times.
    assert runner.calls.count(DECLARING) == runner.calls.count(VERIFYING)
    assert 1 < runner.calls.count(DECLARING) < MORE_THAN_ANY_RUN_TAKES
    assert state_of(run_dir)["retry_count"] == runner.calls.count(VERIFYING) - 1


def test_that_run_took_no_self_route_for_any_of_its_red_suites(
    never_repaired_run,
):
    """The absence over a whole run rather than over one advance, with the
    carry-forward lines beside it: one per red suite the run met, so the empty
    self-route list is a run that recorded its failures somewhere rather than a
    run that met none."""
    _, runner, run_dir = never_repaired_run

    assert self_route_records(run_dir) == []
    assert [e for e in history_of(run_dir) if e["event"] == "self-routed"] == []
    assert len(carry_forward_lines(run_dir)) == runner.calls.count(DECLARING)


def test_the_failure_still_standing_is_the_last_one_and_state_names_it(
    never_repaired_run,
):
    """What a reader of state.json is left with: the most recent failure, whose
    retained record and output are both files that exist and describe a run
    that exited non-zero."""
    _, runner, run_dir = never_repaired_run
    outstanding = state_of(run_dir)["unshadowed_suite_failure"]

    assert outstanding["stage"] == DECLARING
    assert outstanding["attempt"] == runner.calls.count(DECLARING)
    assert outstanding["exit_code"] == 1
    assert read_json(run_dir / outstanding["result_path"])["exit_code"] == 1
    assert Path(outstanding["output_path"]).is_file()


# --------------------------------------------------------------------------
# A check that could not run permits nothing
# --------------------------------------------------------------------------


def test_a_command_that_cannot_be_started_escalates_naming_the_reason(
    make_target, harness_root,
):
    """Not a pass and not a self-route: a check that could not run decides
    nothing, exactly as the clean-clone check's own could-not-run path."""
    target_root = make_target("unrunnable", test_command=UNRUNNABLE_COMMAND)
    code, runner, run_dir = drive(target_root, harness_root)

    record = record_of(run_dir)
    assert code == 2
    assert record["ran"] is False
    assert "exit_code" not in record
    assert record["reason"]
    assert record["reason"] in story_coordinator.escalation_reason(run_dir)
    assert state_of(run_dir)["status"] == "escalated"
    assert not self_route_records(run_dir)
    assert runner.calls == [DECLARING]


def test_the_same_run_with_a_runnable_command_does_not_escalate(green_run):
    """The control beside it: the two runs differ in whether the configured
    command exists and in nothing else."""
    code, _, run_dir = green_run
    assert code == 0
    assert record_of(run_dir)["ran"] is True


# --------------------------------------------------------------------------
# A workflow that declares nothing is the workflow it was
# --------------------------------------------------------------------------


def without_the_declaration(tmp_path: Path,
                            name: str = "no-suite-run") -> tuple[Path, dict]:
    """The same workflow with the suite-run declaration removed, which is this
    branch disabled: the key is the switch."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        stage.pop("suite_run", None)
    workflow["name"] = name
    root = materialize(workflow, tmp_path / name)
    return root, workflow


def test_a_workflow_declaring_no_suite_run_runs_the_stage_as_it_did_before(
    make_target, tmp_path,
):
    """The compatibility property, driven as a run: the declaring stage leaves
    the suite red and the run completes anyway, because no suite was run, no
    record was written, no failure was recorded as outstanding and no line was
    written saying one had been carried forward."""
    harness, workflow = without_the_declaration(tmp_path)
    target_root = make_target("undeclared", workflow=workflow["name"])
    code, runner, run_dir = drive(target_root, harness,
                                  {STAGE_NAMES[0]: [BROKEN]},
                                  workflow=workflow)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert state_of(run_dir)["status"] == "completed"
    assert not (run_dir / SUITE_ARTIFACT).exists()
    assert not (run_dir / story_coordinator.suite_output_file(
        SUITE_ARTIFACT)).exists()
    assert not self_route_records(run_dir)
    assert not state_of(run_dir)["unshadowed_suite_failure"]
    assert carry_forward_lines(run_dir) == []
    assert [e for e in history_of(run_dir)
            if e["event"] == "suite-rerun-started"
            and e["stage"] == STAGE_NAMES[0]] == []


def test_the_identical_run_under_the_declaring_workflow_records_a_failure(
    make_target, harness_root,
):
    """The control beside it. Same fake runner, same plan, same target — and
    the declaration is the only difference — so the silence above is a fact
    about the missing key rather than about a suite that passed anyway."""
    target_root = make_target("declared-control")
    _, _, run_dir = drive(target_root, harness_root, {DECLARING: [BROKEN]})

    assert (run_dir / SUITE_ARTIFACT).is_file()
    assert carry_forward_lines(run_dir)
    assert [e["stage"] for e in history_of(run_dir)
            if e["event"] == "suite-rerun-started"] != []


# --------------------------------------------------------------------------
# Every coordinator suite run keeps its whole output
# --------------------------------------------------------------------------


@pytest.fixture
def all_three_run(make_target, tmp_path):
    """One run making all three of the coordinator's whole-suite runs: the
    revert check on the declaring stage's governed edits, the suite run it
    declares, and the clean-clone check after the verdict."""
    target_root = make_target("all-three", workflow=ALL_THREE["name"])
    harness = materialize(ALL_THREE, tmp_path / "all-three-harness")
    return drive(target_root, harness, workflow=ALL_THREE)


#: The three records, named off the declarations that write them rather than
#: written here.
THREE_ARTIFACTS = [
    story_coordinator.suite_run_declaration(ALL_THREE["stages"])["result"],
    next(s["revert_check"]["result"] for s in ALL_THREE["stages"]
         if "revert_check" in s),
    next(s["clean_clone"]["result"] for s in ALL_THREE["stages"]
         if "clean_clone" in s),
]


def test_the_fixture_really_makes_three_distinct_suite_runs(all_three_run):
    """The premise the parametrization rests on: three declarations, three
    records, three announcements."""
    code, _, run_dir = all_three_run
    assert code == 0
    assert len(set(THREE_ARTIFACTS)) == 3
    assert len([e for e in history_of(run_dir)
                if e["event"] == "suite-rerun-started"]) == 3


@pytest.mark.parametrize("artifact", THREE_ARTIFACTS)
def test_each_coordinator_suite_run_names_the_file_holding_its_whole_output(
    all_three_run, artifact,
):
    """The path is in the record, the file is where the record says, and the
    filename is the one the shared helper derives from the artifact name — so
    no call site spells an output filename."""
    _, _, run_dir = all_three_run
    record = record_of(run_dir, artifact)
    written = Path(record["output_path"])

    assert written.is_file()
    assert written == run_dir / story_coordinator.suite_output_file(artifact)
    assert written.parent == run_dir


@pytest.mark.parametrize("artifact", THREE_ARTIFACTS)
def test_content_the_tail_lost_is_in_the_file_that_run_names(
    all_three_run, artifact,
):
    """The whole point of the file. The suite prints far more than the retained
    tail can hold, so its first line is *absent* from the tail — which is the
    demonstration that the presence of that line in the file is evidence rather
    than a coincidence — and the tail is the end of what the file holds."""
    _, _, run_dir = all_three_run
    record = record_of(run_dir, artifact)
    output = Path(record["output_path"]).read_text(encoding="utf-8")

    assert len(output) > TAIL
    assert EARLY_MARKER not in record["output_tail"]
    assert EARLY_MARKER in output
    assert output.endswith(record["output_tail"])


def test_the_revert_check_still_decides_what_it_decided_before(all_three_run):
    """The two existing checks keep their present behaviour apart from gaining
    the file: the revert check still ran the suite with the governed edits
    reverted and still permitted them, because that run failed."""
    _, _, run_dir = all_three_run
    record = record_of(run_dir, THREE_ARTIFACTS[1])
    assert record["permitted"] is True
    assert record["paths"] == [SENTINEL]
    assert record["exit_code"] != 0


def test_the_clean_clone_check_still_passed_over_the_committed_story(
    all_three_run,
):
    _, _, run_dir = all_three_run
    record = record_of(run_dir, THREE_ARTIFACTS[2])
    assert record["ran"] is True
    assert record["exit_code"] == 0
    assert "clean-clone-passed" in events_of(run_dir)


# --------------------------------------------------------------------------
# What the shipped schemas say
# --------------------------------------------------------------------------


TEST_RESULTS_SCHEMA = read_json(
    REPO_ROOT / "schemas" / "test-results.schema.json")

#: The fields the narrowed schema removed, named here so their absence is
#: asserted rather than assumed.
REMOVED_FIELDS = ("status", "tests_run", "tests_passed", "tests_failed",
                  "failures")


def test_the_test_results_schema_asks_only_what_the_author_knows():
    assert TEST_RESULTS_SCHEMA["required"] == ["tests_written"]
    assert set(TEST_RESULTS_SCHEMA["properties"]) == {"tests_written"}
    for field in REMOVED_FIELDS:
        assert field not in TEST_RESULTS_SCHEMA["properties"], field


def test_that_schema_accepts_what_the_author_writes_and_refuses_silence():
    """The control for the narrowing: the one field is genuinely required, so
    the schema is not simply permitting everything now."""
    schema = schema_validator.load_schema("test-results")
    assert schema_validator.validate({"tests_written": 0}, schema) == []
    assert schema_validator.validate({}, schema)


def test_the_schema_says_why_the_counts_are_not_replaced():
    """The reasoning is recorded where a reader of the artifact meets it: what
    is lost, why it cannot be asked of a target, and what replaces the
    verdict."""
    description = TEST_RESULTS_SCHEMA["description"]
    for phrase in ("test framework", "exit code", "suite-run-result.json"):
        assert phrase in description, phrase
    for field in REMOVED_FIELDS:
        assert field in description, field


def suite_declarations_in(stages: list[dict]) -> dict[str, str]:
    """Every coordinator suite run a workflow declares, as key to artifact.

    A stage key whose value names a result artifact is a check the coordinator
    runs and records; that shape is what the three share, so the collection is
    read off a definition rather than written out here.
    """
    return {key: value["result"] for stage in stages
            for key, value in stage.items()
            if isinstance(value, dict) and isinstance(value.get("result"), str)}


#: Which checks make a coordinator suite run: taken off the fixture that
#: declares all three, because that is the mechanism, not the deployment.
SUITE_DECLARATION_KEYS = sorted(suite_declarations_in(ALL_THREE["stages"]))

SHIPPED_SCHEMA_NAMES = schema_validator.shipped_schemas(REPO_ROOT)


def describing_schema(stage: dict, artifact: str) -> str:
    """The name of the schema describing an artifact a stage declares.

    A schema names a *kind* of record and an artifact names one instance of
    that kind, and the two coincide until two stages have to write the kind
    into one run directory. Then the second is written with its stage's name in
    front of the kind — `tester-changed-files.json` beside `changed-files`,
    which is the convention this repository already ships and declares in the
    stage's own `schemas` map — so a stem the manifest does not carry is
    resolved by removing that prefix. The stage's name comes off the definition
    and nothing is written here. A name that resolves to no shipped schema is
    returned as it is, so the caller's assertion against the manifest reddens
    rather than this quietly inventing an answer.
    """
    stem = Path(artifact).stem
    prefix = f"{stage['name']}-"
    if stem not in SHIPPED_SCHEMA_NAMES and stem.startswith(prefix):
        return stem[len(prefix):]
    return stem


def shipped_schemas_for(key: str) -> list[str]:
    """The distinct schemas describing the records the shipped stages declare
    under one check.

    More than one stage can declare the same check — the confinement this
    repository puts on the stage that writes the validation brings a second
    revert check with it — so the stages are collected rather than reduced to
    whichever came last, which is what reading one artifact per key would do.
    Two stages declaring the same kind resolve to one schema; two resolving to
    different ones leave the collection longer than the keys, which the premise
    below reports.
    """
    return list(dict.fromkeys(
        describing_schema(stage, stage[key]["result"])
        for stage in conftest.shipped_workflow()["stages"]
        if isinstance(stage.get(key), dict)
        and isinstance(stage[key].get("result"), str)))


#: The schemas describing those records in this repository's own deployment.
#: The keys come from the fixture; the artifact names come from what this
#: repository ships, because a shipped schema is the subject here.
COORDINATOR_RECORD_SCHEMAS = [
    stem for key in SUITE_DECLARATION_KEYS for stem in shipped_schemas_for(key)
]


def test_the_derived_collection_names_the_three_shipped_records():
    """The premise the parametrization below rests on, so that a derivation
    collecting nothing — or collecting something this repository does not
    ship — reddens here rather than quietly emptying the cases.

    The collection is held to one schema per declaration key as well as to
    three, because a reader downstream pairs the two collections positionally
    and a key resolving to two schemas would pair them wrongly rather than
    fail.
    """
    assert len(COORDINATOR_RECORD_SCHEMAS) == 3
    assert len(set(COORDINATOR_RECORD_SCHEMAS)) == 3
    assert len(COORDINATOR_RECORD_SCHEMAS) == len(SUITE_DECLARATION_KEYS)
    for stem in COORDINATOR_RECORD_SCHEMAS:
        assert stem in SHIPPED_SCHEMA_NAMES, stem


def test_the_resolution_reports_an_artifact_no_shipped_schema_describes():
    """The control for the resolution above: stripping a stage's name off an
    artifact must be able to arrive at nothing.

    Without this, a prefix rule that stripped its way to any shipped name would
    make the assertion above green for an artifact this repository describes
    nowhere. The victim carries the stage's name and a kind the manifest does
    not hold, and the resolution keeps the kind rather than inventing one.
    """
    stage = {"name": "some-stage"}
    resolved = describing_schema(stage, "some-stage-a-kind-nobody-ships.json")
    assert resolved == "a-kind-nobody-ships"
    assert resolved not in SHIPPED_SCHEMA_NAMES
    # And the prefix is only removed when the whole name is not itself shipped:
    # a record whose name happens to start with its stage's is left alone.
    shipped_kind = COORDINATOR_RECORD_SCHEMAS[0]
    named_for_it = {"name": shipped_kind.split("-")[0]}
    assert describing_schema(
        named_for_it, f"{shipped_kind}.json") == shipped_kind


@pytest.mark.parametrize("stem", COORDINATOR_RECORD_SCHEMAS)
def test_every_coordinator_suite_record_declares_the_output_path(stem):
    """The field the three records gained, in the schemas that describe them,
    with the tail kept beside it so each artifact is readable alone."""
    schema = schema_validator.load_schema(stem)
    assert "output_path" in schema["properties"]
    assert "output_tail" in schema["properties"]
    assert "output_path" not in schema.get("required", [])


# --------------------------------------------------------------------------
# What the shipped prompts say
#
# These read what this repository ships, deliberately: an assertion about what
# a stage is told has to read the words the stage is given.
# --------------------------------------------------------------------------


VERIFIER_PROMPT = (REPO_ROOT / "prompts" / "story-verifier.md").read_text(
    encoding="utf-8")
TESTER_PROMPT = (REPO_ROOT / "prompts" / "story-tester.md").read_text(encoding="utf-8")

PLACEHOLDER = f"{{{{{SUITE_FIELD}}}}}"


def test_the_shipped_verifier_prompt_injects_the_coordinators_record():
    assert PLACEHOLDER in VERIFIER_PROMPT


def test_the_verifier_is_told_to_take_the_whole_suite_verdict_from_it():
    """The passage that used to point at the authoring stage's artifact now
    points at the coordinator's record and names where more detail lives."""
    passage = VERIFIER_PROMPT[VERIFIER_PROMPT.index("The whole suite"):]
    passage = passage[:passage.index("\n\n")]
    assert "coordinator" in passage
    assert "exit code" in passage
    assert "output_path" in passage


def test_the_record_reaches_a_rendered_shipped_verifier_prompt(
    make_target, tmp_path,
):
    """Driven as a run rather than argued from the template: a harness root
    whose verifying stage carries the shipped verifier prompt, and the record
    the coordinator wrote is in the prompt that stage was handed."""
    target_root = make_target("shipped-verifier-prompt")
    harness = materialize(WORKFLOW, tmp_path / "shipped-verifier-harness",
                          {**PROMPTS, VERIFYING: VERIFIER_PROMPT})
    code, _, run_dir = drive(target_root, harness)
    prompt = rendered_prompt(run_dir, VERIFYING)

    assert code == 0
    assert PLACEHOLDER not in prompt
    assert '"exit_code": 0' in prompt
    assert str(run_dir / story_coordinator.suite_output_file(
        SUITE_ARTIFACT)) in prompt


def test_a_context_built_without_the_record_renders_nothing_there():
    """The control: the field is injected because the coordinator found a
    record, not because the assembler always fills it."""
    assert context_assembler.render(
        PLACEHOLDER, {SUITE_FIELD: None}) == "None"
    assert context_assembler.render(
        PLACEHOLDER, {SUITE_FIELD: '{"exit_code": 0}'}) == '{"exit_code": 0}'


#: The words the removed passage was made of: the placeholder that named the
#: command, and the guidance about surviving a run of it.
SUITE_RUNNING_INSTRUCTIONS = ("{{test_command}}", "pipe", "tail", "pager",
                              "background", "polling")


def suite_running_words(text: str) -> list[str]:
    """Every instruction to run the suite that a text still carries.

    A list rather than an assertion so the same statement can be made of a text
    that does carry them, which is the control.
    """
    return [word for word in SUITE_RUNNING_INSTRUCTIONS if word in text]


def test_the_shipped_tester_prompt_carries_no_instruction_to_run_the_suite():
    assert suite_running_words(TESTER_PROMPT) == []


def test_that_scan_reports_the_instruction_that_was_removed():
    """The control for the absence above, against the passage this story
    removed — constructed here rather than resolved out of the commit graph,
    because what it has to be is a text carrying the instruction, and nothing
    about that needs a history."""
    removed = (
        "Generate and execute tests that validate the story's acceptance "
        "criteria. Run the full test suite:\n{{test_command}}\n\n"
        "Do not pipe it through `tail`, `head` or a pager: those buffer "
        "everything until the process exits. And if your tooling moves the "
        "command to the background, keep waiting for it — polling is fine.\n")
    assert suite_running_words(TESTER_PROMPT + removed) == list(
        SUITE_RUNNING_INSTRUCTIONS)


def test_the_shipped_tester_prompt_says_what_the_stage_does_instead():
    """The absence is not silence: the stage is told it authors and records,
    that the coordinator runs the suite afterwards, and what a red suite does
    to it."""
    for phrase in ("executes no test command", "coordinator", PLACEHOLDER):
        assert phrase in TESTER_PROMPT, phrase


# --------------------------------------------------------------------------
# Nothing under orchestration/ turns a suite's output into a count
# --------------------------------------------------------------------------


ORCHESTRATION_MODULES = sorted(COORDINATION_DIR.glob("*.py"))


def counts_derived_in(source: str) -> list[str]:
    """Every place a module names a count of tests.

    The scan is over the names a derived count would have to be called: the
    fields the narrowed schema removed. A module that stores the output and
    passes it on as text names none of them.
    """
    return sorted(field for field in
                  ("tests_run", "tests_passed", "tests_failed", "tests_written")
                  if field in source)


def test_the_scan_has_modules_to_scan():
    """Otherwise the parametrization below could quietly collect nothing."""
    assert len(ORCHESTRATION_MODULES) > 1
    assert any(path.name == "story_coordinator.py"
               for path in ORCHESTRATION_MODULES)


@pytest.mark.parametrize("module", ORCHESTRATION_MODULES,
                         ids=[p.name for p in ORCHESTRATION_MODULES])
def test_no_orchestration_module_derives_a_count_from_a_suites_output(module):
    assert counts_derived_in(module.read_text(encoding="utf-8")) == []


def test_the_same_scan_reports_a_derivation_planted_in_that_source():
    """The control: the same scan over the same source with a count derived
    from the run's output reports it."""
    planted = COORDINATOR_SOURCE.replace(
        "output_tail=output[-CLEAN_CLONE_OUTPUT_TAIL:],",
        "output_tail=output[-CLEAN_CLONE_OUTPUT_TAIL:],\n"
        "        tests_passed=output.count(' passed'),",
        1)
    assert planted != COORDINATOR_SOURCE
    assert counts_derived_in(planted) == ["tests_passed"]
