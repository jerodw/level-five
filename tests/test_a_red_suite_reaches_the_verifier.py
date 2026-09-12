"""A red declared suite run reaches the verifier rather than re-entering the stage.

When the coordinator's declared suite run came back non-zero it re-entered the
stage that had just run. That attribution is a guess the coordinator is not able
to make: a red suite says the tree is broken and says nothing about which stage
broke it. For the stage that authors validation it is backwards by construction,
because that stage may only change files under the test location and is the one
stage forbidden to fix the usual cause.

So the failure is recorded exactly as it was recorded before and the run
*advances*. It reaches the verifier — which already reads the workflow's
retry_routing table and already writes back a category the coordinator routes on
— and the verdict decides where the work goes. The coordinator gained no
classification and no new category; the judgement moved to the agent that was
always making judgements of this kind. A verifier that passes the work anyway is
still stopped, by the refusal that will not let a run complete carrying a suite
failure nothing superseded.

Every case is driven through `story_coordinator.run_story` with a fake agent
runner, against a target repository built under `tmp_path`, so what is asserted
is what a real run wrote rather than what this module's process happens to have
imported. Nothing here invokes a model and nothing here runs this repository's
own suite.

The workflow is built by `tests/conftest.py`'s builder rather than read out of
`workflows/`: the routing is the subject and the stage list, the budgets and the
artifact names are inputs to it, so reading the shipped definition here would
turn a deployment fact into something this module enforces. It declares three
stages deliberately — the advance is to the *next* stage in workflow order, and
a two-stage definition could not tell that apart from an advance to the judge.
It declares no clean-clone check, which is what leaves the end-of-run refusal
reachable: a passing verdict over a standing failure has to arrive there rather
than being turned back by a check that runs the same red suite again.

Every absence asserted here carries a demonstration that it can fail:

  * "the red suite records no self-route" — no artifact, no event, no moved
    counter, no re-run prompt — sits beside the same stage of the same workflow
    withholding a required output, which self-routes on the budget that stage
    declares, so the emptiness is about which failures route back rather than
    about a stage that had nowhere to go;
  * "no line saying a failure was carried forward" is read off a run whose suite
    passed, and sits beside the run that met a red one, which writes it;
  * "the suite-failed cause is gone from orchestration and from the shipped
    schema" sits beside the causes that remain, which the same readings find,
    and beside a source with a call site naming it planted in.

`.harness/docs/ARCHITECTURE.md` is not asserted on: this story's plan assigns it
to the documenter, the stage that runs after this one.
"""
import ast
import json
from pathlib import Path

import pytest

import conftest
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import StageRef

# The target builder and the sentinel-driven suite are
# tests/test_coordinator_runs_the_suite.py's: a repository whose configured
# command exits zero exactly when a stage has repaired one file, which is what
# lets one command drive a red suite and a green one. Reused rather than
# copied, so a regression in that machinery reddens both files.
from test_coordinator_runs_the_suite import (
    BROKEN,
    REPAIR,
    SENTINEL,
    SUITE_FIELD,
    build_suite_target,
)
from test_self_routing_retry import write, write_json

ORCHESTRATION_DIR = Path(story_coordinator.__file__).resolve().parent
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

STORY_ID = "story-001"

#: The record the coordinator writes for the suite run this workflow declares.
#: Deliberately not a name this repository deploys, so a record found under it
#: got there from the definition rather than from a name in the harness.
SUITE_ARTIFACT = "carried-forward-suite-result.json"
DOCUMENT = "the-document.md"

#: Above one, so that "no self-route was taken for the red suite" is a fact
#: about the routing rather than about a stage with no budget to spend. The
#: control below spends it on a cause that does route back.
BUDGET = 2

#: The retry category this workflow defines, and one it does not. Both are the
#: fixture's own words: what the coordinator does with a category is read off
#: the table, and this module holds that a red suite is routed by that same
#: lookup rather than by anything new.
DEFINED_CATEGORY = "the-implementation"
UNDEFINED_CATEGORY = "a-category-this-workflow-never-defined"


def build_workflow() -> dict:
    """Three stages: one that authors validation and declares the suite run,
    one between it and the judge, and the judge."""
    return conftest.build_workflow(
        conftest.workflow_stage(
            outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
            changed_files=conftest.TESTER_CHANGED_FILES,
            max_self_routes=BUDGET,
            suite_run={"result": SUITE_ARTIFACT},
            schemas={conftest.TEST_RESULTS: "test-results",
                     conftest.TESTER_CHANGED_FILES: "changed-files"}),
        conftest.workflow_stage(outputs=(DOCUMENT,)),
        conftest.workflow_stage(
            name=conftest.VERIFYING_STAGE,
            outputs=(conftest.VERIFICATION_RESULT,),
            schemas={conftest.VERIFICATION_RESULT: "verification-result",
                     conftest.RETRY_GUIDANCE: "retry-guidance"},
            retry_routing={DEFINED_CATEGORY: {
                "stage": StageRef(0),
                "when": "the behaviour the story asked for is missing"}}),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name="red-suite-carry-forward-workflow")


WORKFLOW = build_workflow()
DECLARING, BETWEEN, JUDGING = [s["name"] for s in WORKFLOW["stages"]]

PROMPTS = {name: (conftest.built_stage_prompt(name)
                  + f"{SUITE_FIELD}:\n{{{{{SUITE_FIELD}}}}}\n")
           for name in (DECLARING, BETWEEN, JUDGING)}

PASSES = {"status": "passed", "blocking_issues": [], "unverified": [],
          "retry_recommended": False}


def fails(category: str) -> dict:
    return {
        "status": "failed",
        "blocking_issues": [{"severity": "high",
                             "issue": "the whole suite is failing",
                             "location": "src/app.py",
                             "required_behavior": "the suite passes"}],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": category,
    }


GUIDANCE = {
    "current_focus": [{"focus": "make the suite pass",
                       "satisfied_when": "the suite passes"}],
    "preserve_behavior": ["the existing behavior"],
    "retry_scope": ["src/"],
}


def test_the_fixture_declares_what_every_case_below_rests_on():
    """The premises, stated so a change to the builder reddens here rather than
    quietly emptying the assertions: one stage declares the suite run, a stage
    sits between it and the judge, that stage has room to self-route, and no
    clean-clone check stands between a passing verdict and the end of the run.
    """
    declaring = [s for s in WORKFLOW["stages"] if "suite_run" in s]

    assert [s["name"] for s in declaring] == [DECLARING]
    assert declaring[0]["max_self_routes"] == BUDGET >= 2
    assert [DECLARING, BETWEEN, JUDGING] == [
        s["name"] for s in WORKFLOW["stages"]]
    assert BETWEEN != JUDGING
    assert all("clean_clone" not in s for s in WORKFLOW["stages"])
    # And the artifact name is one the harness does not carry, so a record
    # found under it in a run directory came off this declaration.
    assert SUITE_ARTIFACT not in COORDINATOR_SOURCE


# --------------------------------------------------------------------------
# The target, and the fake runner that drives it
# --------------------------------------------------------------------------


def _nth(sequence, index, default):
    if not sequence or index >= len(sequence):
        return default
    return sequence[index]


class Runner:
    """A fake agent runner writing each stage's declared artifacts.

    Its plan says, per stage and per invocation, whether that invocation
    repairs the sentinel the target's suite reads; `withhold` names an artifact
    one invocation of the declaring stage does not write, which is the control
    for the self-route absence. Every artifact comes off the stage's
    declaration in the *loaded* workflow rather than off a list written here.

    Every stage is planned rather than defaulted, deliberately. A default that
    repaired the sentinel would have the stages the failure is carried forward
    *to* quietly fix it, which is precisely the thing this module is watching
    the coordinator not do.
    """

    def __init__(self, target_root: Path, plan: dict, verdicts: list,
                 withhold: dict | None = None):
        self.target_root = Path(target_root)
        self.tree = conftest.run_root_for(target_root)
        self.run_dir = run_dir_of(target_root)
        self.plan = dict(plan)
        self.verdicts = list(verdicts)
        self.withhold = dict(withhold or {})
        self.stages = WORKFLOW["stages"]
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        self.states: list[dict] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, run_dir=None):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        self.states.append(state_of(self.run_dir))
        call = self.calls.count(stage)

        changed: list[str] = []
        if _nth(self.plan.get(stage, []), call - 1, BROKEN) == REPAIR:
            write(self.tree / SENTINEL, "repaired\n")
            changed = [SENTINEL]

        verdict = conftest.answering_guidance(
            self.verdicts[min(self.calls.count(JUDGING) - 1,
                              len(self.verdicts) - 1)],
            self.run_dir)
        declaration = next(s for s in self.stages if s["name"] == stage)
        for artifact in story_coordinator.required_artifacts(declaration):
            if self.withhold.get((stage, call)) == artifact:
                continue
            self._write(artifact, stage, call, verdict, changed)
        if stage == JUDGING and verdict.get("retry_recommended"):
            write_json(self.run_dir / conftest.RETRY_GUIDANCE, GUIDANCE)
        return AgentResult(ok=True, result_text=f"{stage} done")

    def _write(self, artifact, stage, call, verdict, changed):
        path = self.run_dir / artifact
        if artifact == conftest.VERIFICATION_RESULT:
            write_json(path, verdict)
        elif artifact.endswith("changed-files.json"):
            write_json(path, {"modified": list(changed), "created": [],
                              "deleted": []})
        elif artifact == conftest.TEST_RESULTS:
            write_json(path, {"tests_written": 1})
        else:
            write(path, f"{artifact} written by {stage} call {call}.\n")


def run_dir_of(target_root: Path) -> Path:
    return conftest.run_dir_for(Path(target_root), STORY_ID)


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "carry-forward-harness", prompts=PROMPTS)


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so one test can hold a subject and its control side by side."""
    def make(name: str) -> Path:
        return build_suite_target(tmp_path / name, workflow=WORKFLOW["name"])
    return make


@pytest.fixture
def target_root(make_target) -> Path:
    return make_target("carry-forward-target")


def drive(target_root: Path, harness: Path, plan: dict, verdicts: list,
          withhold: dict | None = None):
    runner = Runner(target_root, plan, verdicts, withhold)
    code = story_coordinator.run_story(
        STORY_ID, harness, target_root, runner)
    return code, runner, run_dir_of(target_root)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def state_of(run_dir: Path) -> dict:
    path = Path(run_dir) / "state.json"
    return read_json(path) if path.is_file() else {}


def history_of(run_dir: Path) -> list[dict]:
    return read_json(Path(run_dir) / "execution-history.json")


def self_route_records(run_dir: Path) -> list[dict]:
    return [read_json(path)
            for path in sorted(Path(run_dir).glob("self-route-*.json"))]


def carry_forward_lines(run_dir: Path) -> list[dict]:
    """Every event whose message says a suite failure was carried forward.

    Read by what the message says rather than by an event kind, because the
    kind it is appended under is a shared one. A list rather than an assertion,
    so the same reading can be made of a run whose suite never failed.
    """
    return [entry for entry in history_of(run_dir)
            if "carrying the failure forward" in entry["message"]]


def retained_pair(run_dir: Path, attempt: int = 1, try_number: int = 0):
    result = story_coordinator.retained_suite_result_file(
        SUITE_ARTIFACT, DECLARING, attempt, try_number)
    return result, str(Path(run_dir) / story_coordinator.suite_output_file(
        result))


@pytest.fixture
def red_run(target_root, harness_root):
    """The central run: the declaring stage leaves the suite red, and the
    verdict over the failure passes anyway — so nothing supersedes it and the
    run reaches the refusal at the end.

    Passing rather than failing, because what this fixture is for is the
    *advance*: a failed verdict would route a retry and the run's shape would
    then be about the retry rather than about how the failure travelled.
    """
    return drive(target_root, harness_root,
                 {DECLARING: [BROKEN], BETWEEN: [BROKEN], JUDGING: [BROKEN]},
                 [PASSES])


@pytest.fixture
def green_run(target_root, harness_root):
    """The control run beside it: the same three stages, and the declaring
    stage repairs the suite, so no failure is ever recorded."""
    return drive(target_root, harness_root,
                 {DECLARING: [REPAIR], BETWEEN: [BROKEN], JUDGING: [BROKEN]},
                 [PASSES])


# --------------------------------------------------------------------------
# The advance
# --------------------------------------------------------------------------


def test_a_red_declared_suite_run_advances_to_the_stage_after_the_declaring_one(
    red_run,
):
    """The next stage in workflow order, and no stage skipped: the run reaches
    the stage between the declaring one and the judge, and then the judge."""
    _, runner, _ = red_run
    assert runner.calls == [DECLARING, BETWEEN, JUDGING]


def test_that_run_records_no_self_route_for_the_red_suite(red_run):
    """The absence, in every place a self-route is visible: the record
    artifact, the event, the counter, and the try-suffixed prompt a re-entered
    stage is handed.

    The counter is read off the state the stage after the declaring one opened
    on, because the count is live — it is zeroed when a stage completes, so a
    run that had spent it would read as zero at the end either way.
    """
    _, runner, run_dir = red_run

    assert self_route_records(run_dir) == []
    assert [e for e in history_of(run_dir) if e["event"] == "self-routed"] == []
    assert runner.states[1]["self_route_count"] == 0
    assert not (run_dir / story_coordinator.prompt_file(DECLARING, 1, 1)).exists()


def test_the_same_stage_does_self_route_when_it_withholds_a_required_output(
    make_target, harness_root,
):
    """The control for that absence.

    The same stage of the same workflow, with the same declared budget, meeting
    a cause the coordinator does route back in place: a required output it did
    not write. It re-runs, writes a record, appends the event and lands a
    try-suffixed prompt — so the emptiness above is a fact about which failures
    route back rather than about a stage that could not have self-routed
    whatever happened to it.
    """
    target_root = make_target("withholds-an-output")
    code, runner, run_dir = drive(
        target_root, harness_root,
        {DECLARING: [REPAIR, REPAIR], BETWEEN: [BROKEN], JUDGING: [BROKEN]},
        [PASSES],
        withhold={(DECLARING, 1): conftest.TEST_RESULTS})

    assert code == 0
    assert runner.calls == [DECLARING, DECLARING, BETWEEN, JUDGING]
    assert [record["failure"] for record in self_route_records(run_dir)] == [
        story_coordinator.MISSING_REQUIRED_ARTIFACTS]
    assert [e["stage"] for e in history_of(run_dir)
            if e["event"] == "self-routed"] == [DECLARING]
    assert (run_dir / story_coordinator.prompt_file(DECLARING, 1, 1)).is_file()


def test_the_outstanding_failure_written_to_state_is_the_record_it_always_was(
    red_run,
):
    """Field for field: the stage, the attempt, the try, the recorded scope, the
    exit code, and the retained result and output paths. This story changed
    where a standing failure goes and nothing about what is recorded, so the
    whole record is compared rather than sampled — and both paths it names are
    files that exist and hold the run that failed.

    Read off the state the *next* stage opened on, so it is the record as the
    advance left it rather than whatever the end of the run held.
    """
    _, runner, run_dir = red_run
    result, output = retained_pair(run_dir)

    assert runner.states[1]["unshadowed_suite_failure"] == {
        "stage": DECLARING,
        "attempt": 1,
        "try": 0,
        "scope": [],
        "exit_code": 1,
        "result_path": result,
        "output_path": output,
    }
    assert read_json(run_dir / result)["exit_code"] == 1
    assert Path(output).is_file()


def test_the_events_log_names_the_red_suite_and_the_carry_forward(red_run):
    """Without this line a reader of events.log meets an advance and cannot
    tell it from an advance over a passing suite. So the line names the
    declaring stage, the exit code and the retained pair, and the whole stream
    still validates against the schema it is written under."""
    _, _, run_dir = red_run
    (line,) = carry_forward_lines(run_dir)
    result, output = retained_pair(run_dir)

    assert line["stage"] == DECLARING
    assert DECLARING in line["message"]
    assert "exited 1" in line["message"]
    assert result in line["message"]
    assert output in line["message"]
    assert schema_validator.validate(
        history_of(run_dir),
        schema_validator.load_schema("execution-history")) == []


def test_a_run_whose_suite_passed_records_neither_the_failure_nor_the_line(
    green_run,
):
    """The control for both readings above: the same workflow, the same three
    stages and the same declared suite run, differing in whether the declaring
    stage repaired what the suite reads. No failure in state, no line in the
    log, and the run completes."""
    code, _, run_dir = green_run

    assert code == 0
    assert state_of(run_dir)["status"] == "completed"
    assert state_of(run_dir)["unshadowed_suite_failure"] == {}
    assert carry_forward_lines(run_dir) == []
    assert read_json(run_dir / SUITE_ARTIFACT)["exit_code"] == 0


# --------------------------------------------------------------------------
# What the verifier is given, and what it decides
# --------------------------------------------------------------------------


def test_the_verifier_is_invoked_with_the_failing_suite_record_in_its_context(
    red_run,
):
    """The point of carrying the failure forward: the stage that judges the run
    is handed the coordinator's record of the run that failed, and the path to
    the whole of its output.

    The declaring stage's own prompt is the control beside it — written before
    any suite had run, it carries no exit code at all.
    """
    _, runner, run_dir = red_run
    judged = runner.prompts[JUDGING][0]

    assert '"exit_code": 1' in judged
    assert str(run_dir / story_coordinator.suite_output_file(
        SUITE_ARTIFACT)) in judged
    assert '"exit_code"' not in runner.prompts[DECLARING][0]
    # And the same thing is on disk under that invocation's prompt filename.
    assert '"exit_code": 1' in (run_dir / story_coordinator.prompt_file(
        JUDGING, 1)).read_text(encoding="utf-8")


def test_a_failed_verdict_over_a_red_suite_routes_through_the_tables_lookup(
    target_root, harness_root,
):
    """The judgement the coordinator does not make, made where it belongs: the
    verifier fails the work, names a category the table defines, and the retry
    goes to the stage that table names — spending a retry and archiving the
    attempt, as any failed verification does.

    Nothing about that route is new, which is the property: the destination is
    read off the workflow rather than out of anything this story added.
    """
    code, runner, run_dir = drive(
        target_root, harness_root,
        {DECLARING: [BROKEN, REPAIR], BETWEEN: [BROKEN], JUDGING: [BROKEN]},
        [fails(DEFINED_CATEGORY), PASSES])
    routed = [e for e in history_of(run_dir)
              if e["event"] == "verification-failed"]

    assert code == 0
    assert runner.calls == [DECLARING, BETWEEN, JUDGING] * 2
    assert [e["retry_category"] for e in routed] == [DEFINED_CATEGORY]
    assert [e["retry_stage"] for e in routed] == [
        WORKFLOW["stages"][2]["on_failure"]["retry_routing"][
            DEFINED_CATEGORY]["stage"]]
    assert state_of(run_dir)["retry_count"] == 1
    assert (run_dir / "attempts" / "attempt-1").is_dir()
    assert (run_dir / "retry-history.json").is_file()
    # And the rerun's green suite superseded the failure, so the run completed.
    assert state_of(run_dir)["unshadowed_suite_failure"] == {}
    assert state_of(run_dir)["status"] == "completed"


def test_a_failed_verdict_naming_a_category_the_workflow_lacks_still_escalates(
    target_root, harness_root,
):
    """The other half of the lookup, unchanged by this story: a red suite gives
    the verifier no licence to name a category the workflow does not define.
    The run escalates naming the category and what the workflow does define,
    and no retry is spent on it."""
    code, _, run_dir = drive(
        target_root, harness_root,
        {DECLARING: [BROKEN], BETWEEN: [BROKEN], JUDGING: [BROKEN]},
        [fails(UNDEFINED_CATEGORY)])
    reason = story_coordinator.escalation_reason(run_dir)

    assert code == 2
    assert UNDEFINED_CATEGORY in reason
    assert DEFINED_CATEGORY in reason
    assert state_of(run_dir)["retry_count"] == 0
    assert not (run_dir / "attempts").exists()


def test_a_passing_verdict_over_a_standing_failure_escalates_at_the_end(red_run):
    """The refusal this story relies on and does not change: a verifier that
    read a red suite and passed the work anyway does not get a completed run.

    The reason names the failure that still stands — the stage, the exit code
    and both retained paths — and the run writes no completion report and
    records no story-completed event.
    """
    code, runner, run_dir = red_run
    reason = story_coordinator.escalation_reason(run_dir)
    result, output = retained_pair(run_dir)

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.prompts[JUDGING]  # the verdict really was reached
    assert DECLARING in reason
    assert "exited 1" in reason
    assert result in reason
    assert output in reason
    assert not (run_dir / "completion-report.md").exists()
    assert "story-completed" not in [e["event"] for e in history_of(run_dir)]


def test_the_refusal_costs_no_further_suite_run(red_run):
    """It is decided off the state rather than by asking the suite again: the
    run announced exactly one run of the declared artifact, which is the one the
    declaring stage's turn was judged by."""
    _, _, run_dir = red_run
    announced = [e for e in history_of(run_dir)
                 if e["event"] == "suite-rerun-started"
                 and e.get("artifacts") == [SUITE_ARTIFACT]]
    assert len(announced) == 1
    assert announced[0]["stage"] == DECLARING


# --------------------------------------------------------------------------
# The cause no call site can produce any more
# --------------------------------------------------------------------------


#: The value the removed cause was spelled as, and the constant that held it.
#: Written out because they are what is being asserted absent; every other name
#: in this module comes off the definition or off the coordinator's own
#: constants.
REMOVED_VALUE = "suite-failed"
REMOVED_CONSTANT = "SUITE_FAILED"


def self_route_causes_in(source: str) -> list[str]:
    """The name each `self_route` call site in `source` passes as `failure`.

    Read out of the source rather than driven, because what is asserted is that
    *no* site can produce the cause, which no run can demonstrate. A list rather
    than an assertion, so the same reading can be made of a source that does
    name it.
    """
    names = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.id if isinstance(node.func, ast.Name) \
            else getattr(node.func, "attr", "")
        if called != "self_route":
            continue
        for keyword in node.keywords:
            if keyword.arg == "failure":
                names.append(getattr(keyword.value, "id", None)
                             or ast.dump(keyword.value))
    return names


ORCHESTRATION_MODULES = sorted(ORCHESTRATION_DIR.glob("*.py"))


def test_the_scan_has_modules_to_scan():
    """Otherwise the sweep below could quietly collect nothing."""
    assert len(ORCHESTRATION_MODULES) > 1
    assert any(path.name == "story_coordinator.py"
               for path in ORCHESTRATION_MODULES)


def test_no_site_under_orchestration_passes_a_suite_failure_to_self_route():
    """The cause is gone from every call site and from the declared set, so a
    later reader cannot believe it reachable.

    The causes that remain are asserted beside it: the scan does find call
    sites, and every one of them names a cause the declared set holds.
    """
    named = self_route_causes_in(COORDINATOR_SOURCE)

    assert named, "no self_route call site names a failure, so nothing was read"
    assert REMOVED_CONSTANT not in named
    declared = set(story_coordinator.SELF_ROUTE_FAILURES)
    assert all(getattr(story_coordinator, name, None) in declared
               for name in named)
    assert REMOVED_VALUE not in declared
    assert not hasattr(story_coordinator, REMOVED_CONSTANT)


@pytest.mark.parametrize("module", ORCHESTRATION_MODULES,
                         ids=[p.name for p in ORCHESTRATION_MODULES])
def test_no_module_under_orchestration_spells_the_removed_cause(module):
    assert REMOVED_VALUE not in module.read_text(encoding="utf-8")


def test_the_same_readings_report_that_cause_planted_back_in(red_run):
    """The control for both absences above.

    A source with a call site naming the removed constant is reported by the
    same scan, and the same string search finds the value written back into a
    copy of the coordinator's own source — so the empty results are facts about
    what this repository ships rather than about readings that find nothing
    anywhere.
    """
    planted = (
        f"decision = self_route(run_dir, state, stage, "
        f"failure={REMOVED_CONSTANT}, reason='', artifacts=[], attempt=1)\n")
    assert self_route_causes_in(planted) == [REMOVED_CONSTANT]
    assert REMOVED_VALUE in COORDINATOR_SOURCE + f'\nX = "{REMOVED_VALUE}"\n'


def test_the_shipped_schema_no_longer_permits_that_failure_value():
    """The artifact half: the enum the self-route record is validated against
    does not carry the value, and a record claiming it is reported.

    Beside it, a record carrying a cause the coordinator does still route on,
    which the same validator accepts — so the refusal is about the value rather
    than about a validator refusing everything.
    """
    schema = schema_validator.load_schema("self-route-result")
    enum = schema["properties"]["failure"]["enum"]
    surviving = story_coordinator.SELF_ROUTE_FAILURES[0]

    assert REMOVED_VALUE not in enum
    assert set(enum) == set(story_coordinator.SELF_ROUTE_FAILURES)
    assert REMOVED_VALUE not in json.dumps(schema)

    record = {"stage": DECLARING, "attempt": 1, "try": 1,
              "failure": surviving, "reason": "it failed",
              "statement": "run again"}
    assert schema_validator.validate(record, schema) == []
    assert schema_validator.validate(
        {**record, "failure": REMOVED_VALUE}, schema)
