"""A forgotten file is not a failure: the self-route budget, split by cause.

A stage that left the suite red and a stage that did the work and forgot to
write a JSON file used to cost the run the same thing. story-108 splits the
budget: a stage declaring `max_bookkeeping_self_routes` spends it on the
bookkeeping causes — a required artifact missing, or one the freshness check
read as a previous attempt's — and spends `max_self_routes` on everything else.
A stage declaring no bookkeeping budget spends `max_self_routes` on every
cause, exactly as every stage did before, which is what makes landing the split
nothing until a definition opts in.

What this module holds:

  * story-101's sequence, driven end to end rather than abstracted — a stage
    re-entered for a stale required artifact and then met by a red suite,
    reaching the suite failure with the failure budget it would have had if the
    forgotten file had never happened. Its control is the identical plan under
    a definition declaring no bookkeeping budget, which escalates one suite
    failure earlier and with the wording it escalated with before this story.
  * a stage that never writes its required artifact, which still stops, with an
    escalation reason naming the bookkeeping budget it exhausted rather than
    the failure budget it never touched.
  * the classification, held total against the declared set: every `failure=`
    the coordinator's own call sites name is a member of `SELF_ROUTE_FAILURES`,
    and every member of that set is driven through `self_route` and observed to
    move the counter its classification says it moves. A cause added later
    without being classified reddens both.
  * the try number, which is still the total re-entries of the stage entry, so
    every self-route record and re-run prompt is written and discovered under
    the number it used before the split.
  * the pre-flight, which refuses a bookkeeping budget that is not a count on
    the same terms and in the same message register as the failure budget.

The workflow these runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns: the split is the subject and
the stage list, the budgets and the artifact names are inputs to it, so
deriving them from what this repository deploys would make a deployment fact
into something this module enforces. What this deployment declares is asserted
where the shipped definition is the subject, in
`tests/test_shipped_workflow_is_valid.py`.

The suite the coordinator runs after the declaring stage's turn is
`tests/test_coordinator_runs_the_suite.py`'s: a script in the target whose exit
status follows a sentinel file a stage can repair or break. Reused rather than
copied, so a red suite here is the same red suite that module drives.

Every absence asserted here carries a demonstration that it can fail:

  * "the failure budget was untouched by the bookkeeping re-entry" sits beside
    the same plan under a definition declaring no bookkeeping budget, where it
    is touched and the run stops a suite failure earlier;
  * "the escalation names the bookkeeping budget and not the failure one" sits
    beside the run that exhausts the failure budget, whose reason names that
    one and not the other;
  * "no call site names an unclassified cause" sits beside the same scan over a
    source with an unclassified call site planted in it, which reports it;
  * "no new value joined the failure enum" sits beside the same comparison
    against an enum with a value added to it, which reports the difference.

Nothing here invokes a model, and nothing here runs this repository's suite.
"""
import ast
import dataclasses
import json
from pathlib import Path

import pytest

import agent_runner
import conftest
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import workflow_stage

# The target builder, the sentinel-driven suite and the verifying stage are
# tests/test_coordinator_runs_the_suite.py's: a repository whose configured
# suite exits zero exactly when a stage has repaired a file, which is what lets
# one command drive a green suite and then a red one. Reused rather than
# copied, so a regression in that machinery reddens both files.
from test_coordinator_runs_the_suite import (  # noqa: E402
    FAILED,
    GUIDANCE,
    PASS,
    REPAIRED,
    SENTINEL,
    SUITE_ARTIFACT,
    build_suite_target,
    run_dir_of,
    verifying_stage,
)
from test_self_routing_retry import (  # noqa: E402
    STORY_ID,
    amend_the_story,
    write,
    write_json,
)

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")
SCHEMA_PATH = REPO_ROOT / "schemas" / "self-route-result.schema.json"

# --------------------------------------------------------------------------
# The two budgets, and the workflow that declares them
#
# The keys come off the coordinator's own constants rather than being spelled
# here, so the key a definition declares, the key the pre-flight refuses on and
# the key this module builds a stage with are one string.
# --------------------------------------------------------------------------

BUDGET_KEY = story_coordinator.SELF_ROUTE_BUDGET_KEY
BOOKKEEPING_KEY = story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY

#: Both at two, and deliberately equal: a run below has to distinguish "the
#: failure budget was spent" from "the bookkeeping budget was spent" by which
#: one the stop names, and unequal numbers would let it be distinguished by
#: arithmetic instead. Two rather than one because each budget needs a middle —
#: an invocation that has already spent one unit of it and is still re-run.
FAILURE_BUDGET = 2
BOOKKEEPING_BUDGET = 2


def declaring_stage(*, bookkeeping: object = BOOKKEEPING_BUDGET) -> dict:
    """The stage that authors validation, declares the suite run, and opts into
    the split.

    `bookkeeping` is what the control varies: passing None builds the same
    stage declaring no bookkeeping budget at all, which is the pre-story shape
    and the shape every stage that has not opted in still has.
    """
    return workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        max_self_routes=FAILURE_BUDGET,
        max_bookkeeping_self_routes=bookkeeping,
        suite_run={"result": SUITE_ARTIFACT},
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"})


def build(name: str, *, bookkeeping: object = BOOKKEEPING_BUDGET) -> dict:
    return conftest.build_workflow(
        declaring_stage(bookkeeping=bookkeeping), verifying_stage(),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name=name)


#: The definition that opted into the split, and the one that did not. Both
#: carry the same stages, the same outputs and the same failure budget, so a
#: run that differs between them differs by the one declaration.
SPLIT = build("split-budget-workflow")
UNSPLIT = build("unsplit-budget-workflow", bookkeeping=None)

DECLARING, VERIFYING = [stage["name"] for stage in SPLIT["stages"]]


def test_the_two_definitions_differ_by_exactly_the_bookkeeping_budget():
    """The premise every subject-and-control pair below rests on, stated so a
    change to the builder reddens here rather than quietly making the control
    differ by something else as well."""
    split, unsplit = SPLIT["stages"][0], UNSPLIT["stages"][0]
    assert split[BOOKKEEPING_KEY] == BOOKKEEPING_BUDGET
    assert BOOKKEEPING_KEY not in unsplit
    assert {k: v for k, v in split.items() if k != BOOKKEEPING_KEY} == unsplit
    assert split[BUDGET_KEY] == unsplit[BUDGET_KEY] == FAILURE_BUDGET
    # The keys this module builds with are the keys the coordinator reads.
    assert BUDGET_KEY in split and BOOKKEEPING_KEY in split


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
        agent_runner.run_agent("prompt", stage=DECLARING, cwd=tmp_path,
                               log_path=tmp_path / "agent.log")


# --------------------------------------------------------------------------
# The fake runner
#
# Driven by a per-stage, per-invocation plan. Every stage writes the artifacts
# its own declaration in the *loaded* workflow requires, never a list written
# here, unless the plan says to leave one alone.
# --------------------------------------------------------------------------

OK = "ok"          #: write everything declared, and leave the suite green
BROKEN = "broken"  #: write everything declared, and leave the suite red


def skip(artifact: str) -> tuple:
    """Leave one required output alone: missing if absent, stale if present."""
    return ("skip", artifact)


def _nth(sequence: list, index: int, default):
    if not sequence or index >= len(sequence):
        return default
    return sequence[index]


class Runner:
    """A fake agent runner with a plan of mechanical failures and suite states.

    It records, at the entry to every invocation, the two counts the run's own
    state.json carried — which is how "the resumed stage started from zero" and
    "the failure budget was untouched" are checked as facts observed during the
    run rather than as numbers written here.
    """

    def __init__(self, target_root: Path, plan: dict | None = None,
                 verdicts: list | None = None, workflow: dict | None = None):
        self.target_root = Path(target_root)
        self.run_dir = run_dir_of(target_root)
        self.plan = plan or {}
        self.verdicts = list(verdicts or [PASS])
        self.stages = (workflow or SPLIT)["stages"]
        self.calls: list[str] = []
        #: (stage, self_route_count, bookkeeping_self_route_count) at entry
        self.counts: list[tuple[str, int, int]] = []

    def _declaration(self, stage: str) -> dict:
        return next(s for s in self.stages if s["name"] == stage)

    def _counts_now(self) -> tuple[int, int]:
        path = self.run_dir / "state.json"
        if not path.is_file():
            return (0, 0)
        state = json.loads(path.read_text(encoding="utf-8"))
        return (state.get("self_route_count", 0),
                state.get("bookkeeping_self_route_count", 0))

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        call = self.calls.count(stage)
        self.counts.append((stage, *self._counts_now()))
        if log_path:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{stage} invocation {call}\n")

        action = _nth(self.plan.get(stage, []), call - 1, OK)
        skipped = {action[1]} if isinstance(action, tuple) else set()
        changed: list[str] = []
        if action in (OK, BROKEN):
            state = REPAIRED if action == OK else "the state the stage found"
            path = self.target_root / SENTINEL
            if path.read_text(encoding="utf-8").strip() != state:
                write(path, f"{state}\n")
                changed = [SENTINEL]

        verdict = conftest.answering_guidance(
            self.verdicts[min(self.calls.count(VERIFYING) - 1,
                              len(self.verdicts) - 1)],
            self.run_dir)
        for artifact in story_coordinator.required_artifacts(
                self._declaration(stage)):
            if artifact in skipped:
                continue
            self._write(artifact, stage, call, verdict, changed)
        if stage == VERIFYING and verdict.get("retry_recommended"):
            write_json(self.run_dir / "retry-guidance.json", GUIDANCE)
        return AgentResult(ok=True, result_text=f"{stage} done")

    def _write(self, artifact: str, stage: str, call: int, verdict: dict,
               changed: list[str]) -> None:
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


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so one test can hold a subject and its control side by side."""
    def make(name: str, workflow: dict = SPLIT) -> Path:
        return build_suite_target(tmp_path / name, workflow=workflow["name"])
    return make


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying both definitions, so a subject and its control
    load a real file out of the same root and differ only in what they name."""
    root = conftest.materialize_workflow(SPLIT, tmp_path / "split-harness")
    return conftest.materialize_workflow(UNSPLIT, root)


def drive(target_root: Path, harness: Path, plan: dict | None = None,
          verdicts: list | None = None, workflow: dict = SPLIT,
          start_stage: str | None = None):
    """One run, returning its exit code, its runner and its run directory."""
    runner = Runner(target_root, plan, verdicts, workflow)
    code = story_coordinator.run_story(
        STORY_ID, harness, target_root, runner, start_stage)
    return code, runner, run_dir_of(target_root)


def state_of(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def self_route_records(run_dir: Path) -> list[tuple[str, dict]]:
    return [(path.name, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(Path(run_dir).glob("self-route-*.json"))]


def causes_of(run_dir: Path) -> list[str]:
    """The failure each self-route this run took was taken for, in order."""
    return [record["failure"] for _, record in self_route_records(run_dir)]


def reason_of(run_dir: Path) -> str:
    reason = story_coordinator.escalation_reason(run_dir)
    assert reason, "the run did not escalate, so there is no reason to read"
    return reason


# --------------------------------------------------------------------------
# story-101's sequence: a forgotten file, and then a red suite
#
# story-101 spent one self-route on a genuine suite failure, spent the next on
# a turn that repaired those failures and left test-results.json unwritten, and
# then met a flake with nothing left. It escalated with its work complete. The
# sequences below are that one, driven rather than described: a bookkeeping
# re-entry, and then as many suite failures as the failure budget allows.
# --------------------------------------------------------------------------


def missing_then_red() -> dict:
    """The stage's first invocation forgets a file that is not at the run root
    yet, and every invocation after it leaves the suite red."""
    return {"plan": {DECLARING: [skip(conftest.TEST_RESULTS)]
                     + [BROKEN] * (FAILURE_BUDGET + 1)},
            "verdicts": [PASS]}


def stale_then_red() -> dict:
    """story-101's own shape. The stage runs once cleanly and the suite passes;
    a failed verdict routes a retry back to it; the invocation the retry brings
    leaves at the run root what the first one wrote, which is the stale case;
    and every invocation after that leaves the suite red."""
    return {"plan": {DECLARING: [OK, skip(conftest.TEST_RESULTS)]
                     + [BROKEN] * (FAILURE_BUDGET + 1)},
            "verdicts": [FAILED, PASS]}


SEQUENCES = {
    story_coordinator.MISSING_REQUIRED_ARTIFACTS: missing_then_red,
    story_coordinator.STALE_REQUIRED_ARTIFACTS: stale_then_red,
}


def test_every_bookkeeping_cause_is_driven_to_a_red_suite():
    """The companion the parametrization needs, against the declared subset
    rather than a list written here: a cause classified as bookkeeping and left
    without a sequence would leave the pair below silently untested."""
    assert set(SEQUENCES) == set(
        story_coordinator.BOOKKEEPING_SELF_ROUTE_FAILURES)


@pytest.mark.parametrize("cause", sorted(SEQUENCES), ids=sorted(SEQUENCES))
def test_a_bookkeeping_re_entry_leaves_the_failure_budget_whole(
    make_target, harness_root, cause,
):
    """The story's first acceptance criterion, driven end to end.

    The stage is re-entered once for a forgotten or stale file and then meets
    the suite failing, and it gets the whole of `max_self_routes` for the suite
    — the failure budget it would have had if the bookkeeping re-entry had
    never happened. The counts read off state.json say the same thing twice
    over: the total is every re-entry, and the subset is the one that was
    bookkeeping.
    """
    target_root = make_target(f"split-{cause}")
    code, runner, run_dir = drive(target_root, harness_root, **SEQUENCES[cause]())

    assert code == 2
    state = state_of(run_dir)
    assert state["status"] == "escalated"
    assert state["current_stage"] == DECLARING

    # One bookkeeping re-entry, then a suite failure for every unit of the
    # failure budget — the sequence a stage whose budget was not split could
    # not have reached, because the first re-entry would have eaten one of them.
    assert causes_of(run_dir) == [cause] + [story_coordinator.SUITE_FAILED] * \
        FAILURE_BUDGET

    # The total is every re-entry of this stage entry; the subset is the one of
    # them that was bookkeeping; the difference is the failure budget, spent.
    assert state["self_route_count"] == FAILURE_BUDGET + 1
    assert state["bookkeeping_self_route_count"] == 1
    assert state["self_route_count"] - state["bookkeeping_self_route_count"] \
        == FAILURE_BUDGET

    # And it stopped on the budget that governed the failure it met.
    assert f"{DECLARING} has exhausted its {BUDGET_KEY} budget of " \
        f"{FAILURE_BUDGET}" in reason_of(run_dir)


@pytest.mark.parametrize("cause", sorted(SEQUENCES), ids=sorted(SEQUENCES))
def test_the_same_sequence_under_an_unsplit_budget_stops_a_suite_failure_early(
    make_target, harness_root, cause,
):
    """The control for the assertion above, and the compatibility property in
    the same run.

    The identical plan under a definition declaring no bookkeeping budget: the
    forgotten file spends `max_self_routes`, so the stage reaches one fewer
    suite failure and stops. That is what the split bought, and it is also what
    every stage that has not opted in still does — the escalation is worded
    byte for byte as it was before this story, naming a self-route budget
    rather than a key.
    """
    target_root = make_target(f"unsplit-{cause}", UNSPLIT)
    code, runner, run_dir = drive(target_root, harness_root,
                                  **SEQUENCES[cause](), workflow=UNSPLIT)

    assert code == 2
    assert causes_of(run_dir) == [cause] + [story_coordinator.SUITE_FAILED] * \
        (FAILURE_BUDGET - 1)

    state = state_of(run_dir)
    assert state["self_route_count"] == FAILURE_BUDGET
    # The counter the split added stays at zero however such a stage re-enters,
    # which is what leaves the whole budget being spent by one comparison.
    assert state["bookkeeping_self_route_count"] == 0

    reason = reason_of(run_dir)
    assert f"{DECLARING} has exhausted its self-route budget of " \
        f"{FAILURE_BUDGET}" in reason
    assert BUDGET_KEY not in reason
    assert BOOKKEEPING_KEY not in reason


@pytest.mark.parametrize("cause", sorted(SEQUENCES), ids=sorted(SEQUENCES))
def test_the_split_run_reached_a_suite_failure_the_unsplit_one_never_saw(
    make_target, harness_root, cause,
):
    """The two runs above, compared where the comparison is the point.

    Same plan, same target builder, same harness root: the stage under the
    split budget was invoked once more than the stage under the unsplit one,
    and that extra invocation is a suite failure rather than a forgotten file.
    A split that changed the accounting without changing what the stage got to
    do would pass both tests above and fail this one.
    """
    split_root = make_target(f"compare-split-{cause}")
    unsplit_root = make_target(f"compare-unsplit-{cause}", UNSPLIT)

    _, split, split_dir = drive(split_root, harness_root, **SEQUENCES[cause]())
    _, unsplit, unsplit_dir = drive(unsplit_root, harness_root,
                                    **SEQUENCES[cause](), workflow=UNSPLIT)

    assert split.calls.count(DECLARING) == unsplit.calls.count(DECLARING) + 1
    assert causes_of(split_dir).count(story_coordinator.SUITE_FAILED) == \
        causes_of(unsplit_dir).count(story_coordinator.SUITE_FAILED) + 1


# --------------------------------------------------------------------------
# A stage that never writes its artifact still stops
# --------------------------------------------------------------------------


@pytest.fixture
def never_writes(make_target, harness_root):
    """Every invocation forgets the same required output, so the bookkeeping
    budget is the only thing that can end the run."""
    target_root = make_target("never-writes")
    return drive(target_root, harness_root,
                 {DECLARING: [skip(conftest.TEST_RESULTS)]
                  * (BOOKKEEPING_BUDGET + 1)})


def test_a_stage_that_never_writes_its_artifact_still_stops(never_writes):
    """The split buys a correction pass a budget of its own; it does not buy an
    unbounded one. The stage is re-entered for every unit of the bookkeeping
    budget and the invocation past it ends the run."""
    code, runner, run_dir = never_writes

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    # It spent the whole budget before stopping rather than escalating early.
    assert runner.calls.count(DECLARING) == BOOKKEEPING_BUDGET + 1
    assert causes_of(run_dir) == [story_coordinator.MISSING_REQUIRED_ARTIFACTS] \
        * BOOKKEEPING_BUDGET
    assert VERIFYING not in runner.calls


def test_the_stop_names_the_bookkeeping_budget_and_not_the_failure_one(
    never_writes,
):
    """A bookkeeping exhaustion and a failure exhaustion must not read as the
    same stop, which is what naming the budget in the reason is for.

    The failure budget was never touched, and the reason says so by not naming
    it. Its control is the run two sections above, which exhausts the failure
    budget and whose reason names that key and not this one — so "names one and
    not the other" is a distinction this reading can actually draw.
    """
    _, _, run_dir = never_writes
    reason = reason_of(run_dir)

    assert f"{DECLARING} has exhausted its {BOOKKEEPING_KEY} budget of " \
        f"{BOOKKEEPING_BUDGET}" in reason
    # `max_bookkeeping_self_routes` contains neither of these, so finding the
    # failure budget named would mean it was named on its own account.
    assert f"its {BUDGET_KEY} budget" not in reason
    assert "self-route budget" not in reason

    state = state_of(run_dir)
    assert state["bookkeeping_self_route_count"] == BOOKKEEPING_BUDGET
    assert state["self_route_count"] == BOOKKEEPING_BUDGET
    assert state["self_route_count"] - state["bookkeeping_self_route_count"] == 0


def test_the_self_routed_events_name_the_budget_that_was_spent(never_writes):
    """The event stream says the same thing the escalation says, so a reader
    reconstructing the routing decision from state.json and the history meets
    the budget rather than an unlabelled number."""
    _, _, run_dir = never_writes
    history = json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))
    routed = [entry for entry in history if entry["event"] == "self-routed"]

    assert len(routed) == BOOKKEEPING_BUDGET
    for spent, entry in enumerate(routed, start=1):
        assert f"{BOOKKEEPING_KEY} {spent} of {BOOKKEEPING_BUDGET}" \
            in entry["message"]


# --------------------------------------------------------------------------
# The try number is still the total
# --------------------------------------------------------------------------


def test_every_record_and_prompt_is_written_under_the_running_total(
    make_target, harness_root,
):
    """`self_route_count` keeps meaning the total re-entries of the stage
    entry, because it is the try number that names self-route records and
    re-run prompts and the number a resume archive discovers them by.

    The sequence is a bookkeeping re-entry followed by failure re-entries, so a
    coordinator keying the filenames off either budget's own counter would
    write a name over another name here rather than the three distinct ones
    below.
    """
    target_root = make_target("try-numbers")
    _, _, run_dir = drive(target_root, harness_root, **missing_then_red())

    attempt = 1
    names = [name for name, _ in self_route_records(run_dir)]
    assert names == [story_coordinator.self_route_result_file(
        DECLARING, attempt, number)
        for number in range(1, FAILURE_BUDGET + 2)]
    assert [record["try"] for _, record in self_route_records(run_dir)] == \
        list(range(1, FAILURE_BUDGET + 2))

    for number in range(1, FAILURE_BUDGET + 2):
        prompt = run_dir / story_coordinator.prompt_file(
            DECLARING, attempt, number)
        assert prompt.is_file(), prompt.name


def test_a_resume_starts_the_resumed_stage_with_both_counters_at_zero(
    make_target, harness_root,
):
    """A run is driven to a bookkeeping escalation — leaving a non-zero subset
    in state.json — and then resumed. The resumed stage forgets its output once
    and is re-entered for it, which it could only be from a bookkeeping count
    of zero.

    Read off the state.json the resumed invocation actually found, so this is
    what the run recorded rather than what the loop was expected to do.
    """
    target_root = make_target("resumed")
    code, _, run_dir = drive(
        target_root, harness_root,
        {DECLARING: [skip(conftest.TEST_RESULTS)] * (BOOKKEEPING_BUDGET + 1)})
    assert code == 2
    escalated = state_of(run_dir)
    assert escalated["bookkeeping_self_route_count"] == BOOKKEEPING_BUDGET
    assert escalated["self_route_count"] == BOOKKEEPING_BUDGET

    # The story is amended, so the resume guard — which refuses a resume that
    # would reach the same point the same way — has something to see.
    amend_the_story(target_root)

    code, runner, run_dir = drive(
        target_root, harness_root,
        {DECLARING: [skip(conftest.TEST_RESULTS), OK]},
        start_stage=DECLARING)

    assert code == 0
    # Both counters at zero at the resumed stage's first entry, and the stage
    # was then invoked twice: the second invocation is a re-entry it could only
    # have been granted from a bookkeeping count of zero, since the escalated
    # state carried the whole budget spent.
    assert runner.counts[0] == (DECLARING, 0, 0)
    assert runner.calls.count(DECLARING) == 2
    assert story_coordinator.MISSING_REQUIRED_ARTIFACTS in causes_of(run_dir)


# --------------------------------------------------------------------------
# The classification is total against the declared set
# --------------------------------------------------------------------------


def self_route_call_causes(source: str) -> list[str]:
    """The name each `self_route` call site in `source` passes as `failure`.

    Read out of the source rather than driven, because a cause a run cannot
    reach is still a cause the classification has to cover: what the split
    decides on is the value at the call site, and a call site naming something
    the declared set omits would fall to the failure branch silently.
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


def unclassified_causes(source: str) -> list[str]:
    """Every `failure=` a call site names that the declared set does not hold."""
    declared = set(story_coordinator.SELF_ROUTE_FAILURES)
    return [name for name in self_route_call_causes(source)
            if getattr(story_coordinator, name, None) not in declared]


def test_every_cause_the_coordinator_routes_on_is_in_the_declared_set():
    """The totality the story asks for, asserted against the declared set
    rather than against a list written here.

    A cause added later at a new call site and left out of
    `SELF_ROUTE_FAILURES` is a cause on neither side of the split — it would
    fall to the failure branch by omission rather than by classification — and
    this is what says so.
    """
    names = self_route_call_causes(COORDINATOR_SOURCE)
    assert names, "no self_route call site names a failure, so nothing was read"
    assert unclassified_causes(COORDINATOR_SOURCE) == []


def test_the_same_scan_reports_a_call_site_naming_an_unclassified_cause():
    """The control for the absence above.

    A source with a call site the declared set does not cover, so a green
    result there means the reading looked rather than that it found nothing to
    look at. Written here rather than mutated out of the coordinator: the
    subject is what the scan does with a call site, and a sentence carries that
    with nothing to resolve.
    """
    planted = (
        "decision = self_route(run_dir, state, stage, "
        "failure=UNCLASSIFIED_CAUSE, reason='', artifacts=[], attempt=1)\n")
    assert unclassified_causes(planted) == ["UNCLASSIFIED_CAUSE"]

    # And the same scan over a call site the set *does* cover reports nothing,
    # so it is the classification being read rather than the shape of the call.
    covered = planted.replace("UNCLASSIFIED_CAUSE", "SUITE_FAILED")
    assert self_route_call_causes(covered) == ["SUITE_FAILED"]
    assert unclassified_causes(covered) == []


def test_the_bookkeeping_subset_and_its_complement_partition_the_set():
    """The two halves are together the whole set and share no member.

    Stated against the declared values, and given teeth by the two assertions
    around it: the subset is drawn from the whole set rather than from
    anywhere, and neither tuple repeats a member — which a set comparison alone
    would hide.
    """
    everything = story_coordinator.SELF_ROUTE_FAILURES
    bookkeeping = story_coordinator.BOOKKEEPING_SELF_ROUTE_FAILURES
    complement = set(everything) - set(bookkeeping)

    assert set(bookkeeping) <= set(everything)
    assert set(bookkeeping) | complement == set(everything)
    assert not set(bookkeeping) & complement
    assert len(set(everything)) == len(everything)
    assert len(set(bookkeeping)) == len(bookkeeping)
    assert bookkeeping, "no cause is classified as bookkeeping"
    assert complement, "every cause is bookkeeping, so nothing is split"


def test_the_declared_set_is_the_failure_enum_the_schema_ships():
    """A cause the schema declares and the classification omits, or the
    reverse, is a cause the split does not decide on.

    This is also what says no new value joined the enum for the split: the
    coordinator's set and the schema's enum are the same values, and the split
    is about which budget a cause spends rather than about what a cause is.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    enum = schema["properties"]["failure"]["enum"]
    assert set(enum) == set(story_coordinator.SELF_ROUTE_FAILURES)


def test_that_comparison_reports_an_enum_with_a_value_added_to_it():
    """The control for the absence above: the same comparison against an enum
    carrying one more value reports the difference, so a green result means the
    two were read rather than that neither was."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    grown = list(schema["properties"]["failure"]["enum"]) + ["a-new-cause"]
    assert set(grown) != set(story_coordinator.SELF_ROUTE_FAILURES)


# --------------------------------------------------------------------------
# Every declared cause spends the budget its classification says it spends
#
# Driven against `self_route` itself rather than through a run, so a cause a
# run cannot reach today is still driven — which is what makes the loop total
# over the declared set rather than over the causes this file has plans for.
# --------------------------------------------------------------------------


def spend(stage: dict, failure: str, run_dir: Path,
          state: story_coordinator.RunState | None = None):
    """One self-route decision, against a state this test owns."""
    state = state or story_coordinator.RunState(
        story_id=STORY_ID, branch=f"story/{STORY_ID}", current_stage=DECLARING)
    decision = story_coordinator.self_route(
        run_dir, state, stage, failure=failure, reason="the stage failed",
        artifacts=[conftest.TEST_RESULTS], attempt=1)
    return decision, state


@pytest.mark.parametrize("failure", story_coordinator.SELF_ROUTE_FAILURES)
def test_each_declared_cause_moves_the_counter_its_classification_names(
    tmp_path, failure,
):
    """The split, over every cause the coordinator declares.

    The expectation is derived from `BOOKKEEPING_SELF_ROUTE_FAILURES` rather
    than written per cause, so a cause added to the subset is driven here the
    moment it is classified, and one added to neither is driven as a failure —
    which is what the totality assertions above are there to refuse.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stage = SPLIT["stages"][0]

    decision, state = spend(stage, failure, run_dir)

    assert decision.taken
    # The total moves for every cause, which is what keeps the try number the
    # total; only the subset distinguishes them.
    assert state.self_route_count == 1
    bookkeeping = failure in story_coordinator.BOOKKEEPING_SELF_ROUTE_FAILURES
    assert state.bookkeeping_self_route_count == (1 if bookkeeping else 0)


@pytest.mark.parametrize("failure", story_coordinator.SELF_ROUTE_FAILURES)
def test_under_an_undeclared_bookkeeping_budget_no_cause_moves_the_subset(
    tmp_path, failure,
):
    """The compatibility property at the level of the decision: a stage that
    declared no bookkeeping budget leaves the subset at zero however it
    re-enters, so every cause is compared against `max_self_routes` alone."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    decision, state = spend(UNSPLIT["stages"][0], failure, run_dir)

    assert decision.taken
    assert state.self_route_count == 1
    assert state.bookkeeping_self_route_count == 0


def test_a_declared_bookkeeping_budget_of_zero_is_a_refusal_and_not_an_absence(
    tmp_path,
):
    """Zero and absent are different declarations and the coordinator spends
    them differently: absent means the stage never opted into the split, so a
    bookkeeping cause spends the failure budget; zero means the stage opted in
    and granted the bookkeeping causes nothing, so the first one stops it.

    Both are driven here, against the same cause, so the difference is the
    declaration alone.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cause = story_coordinator.BOOKKEEPING_SELF_ROUTE_FAILURES[0]

    refused, refused_state = spend(
        {**SPLIT["stages"][0], BOOKKEEPING_KEY: 0}, cause, run_dir)
    assert not refused.taken
    assert refused_state.self_route_count == 0
    assert refused_state.bookkeeping_self_route_count == 0

    absent, absent_state = spend(UNSPLIT["stages"][0], cause, run_dir)
    assert absent.taken
    assert absent_state.self_route_count == 1


# --------------------------------------------------------------------------
# The self-route record is the record it always was
# --------------------------------------------------------------------------


@pytest.fixture
def bookkeeping_record(make_target, harness_root) -> dict:
    """The evidence a bookkeeping self-route left, out of a real run."""
    target_root = make_target("record")
    _, _, run_dir = drive(target_root, harness_root,
                          {DECLARING: [skip(conftest.TEST_RESULTS)]})
    records = self_route_records(run_dir)
    assert len(records) == 1
    return records[0][1]


def test_the_record_a_bookkeeping_self_route_writes_satisfies_the_schema(
    bookkeeping_record,
):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema_validator.validate(bookkeeping_record, schema) == []


def test_the_record_carries_the_fields_it_carried_and_no_others(
    bookkeeping_record,
):
    """A self-route record written after this story carries the same fields it
    carried before it, with the failure value it always carried: the split
    changed which budget a cause spends and not what a self-route records.

    The field set is read off the schema rather than typed out, so a property
    added there without being written — or the reverse — is a violation.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert set(bookkeeping_record) <= set(schema["properties"])
    assert set(schema["required"]) <= set(bookkeeping_record)
    assert bookkeeping_record["failure"] == \
        story_coordinator.MISSING_REQUIRED_ARTIFACTS
    assert bookkeeping_record["stage"] == DECLARING
    assert bookkeeping_record["try"] == 1


def test_the_validator_reports_that_record_with_a_required_field_dropped(
    bookkeeping_record,
):
    """The control for the validation above, once per required field: a green
    validation must mean the validator looked."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for field in schema["required"]:
        broken = {k: v for k, v in bookkeeping_record.items() if k != field}
        assert schema_validator.validate(broken, schema), field


# --------------------------------------------------------------------------
# Pre-flight: a bookkeeping budget that cannot be spent refuses the run
# --------------------------------------------------------------------------

MALFORMED = [-1, "1", 1.5, True, None, [1]]


def test_the_check_accepts_every_bookkeeping_budget_a_stage_may_declare():
    """The function itself, over stage lists that are not a real workflow, so
    the accepted values are pinned to the values rather than to whichever one a
    run happens to meet first."""
    assert story_coordinator.self_route_problems(SPLIT["stages"]) == []
    assert story_coordinator.self_route_problems(UNSPLIT["stages"]) == []
    for good in (0, 1, 7):
        assert story_coordinator.self_route_problems(
            [{"name": "s", BOOKKEEPING_KEY: good}]) == []
    # Undeclared is the normal case and is not checked at all.
    assert story_coordinator.self_route_problems([{"name": "s"}]) == []


@pytest.mark.parametrize("value", MALFORMED, ids=[repr(v) for v in MALFORMED])
def test_the_check_reports_one_problem_naming_the_stage_and_the_value(value):
    """The refusal is in the register `self_route_problems` already uses for
    the failure budget: one problem, naming the stage, the key and the value.

    The key is in the message because a definition declaring both budgets and
    one bad one gives a reader nothing to act on otherwise.
    """
    problems = story_coordinator.self_route_problems(
        [{"name": "alpha", BOOKKEEPING_KEY: value}, {"name": "beta"}])

    assert len(problems) == 1
    assert "alpha" in problems[0]
    assert "beta" not in problems[0]
    assert BOOKKEEPING_KEY in problems[0]
    assert repr(value) in problems[0]
    assert "not a non-negative integer" in problems[0]


def test_a_stage_declaring_both_budgets_badly_is_reported_for_each():
    """Both budgets are held to the same terms in the same loop, so a stage
    declaring two bad ones is reported twice rather than being stopped at the
    first — and each problem names the key it is about, which is the only thing
    telling a reader which of the two to fix."""
    problems = story_coordinator.self_route_problems(
        [{"name": "alpha", BUDGET_KEY: "two", BOOKKEEPING_KEY: "three"}])

    assert len(problems) == 2
    assert [p for p in problems if f"declares {BUDGET_KEY} 'two'" in p]
    assert [p for p in problems if f"declares {BOOKKEEPING_KEY} 'three'" in p]


def test_a_run_under_a_bookkeeping_budget_that_is_not_a_count_is_refused(
    tmp_path, capsys,
):
    """The pre-flight, end to end: the refusal happens before any run state is
    created, and it names the stage and the offending value.

    Its control is the identical run under the same definition with a
    bookkeeping budget the check accepts, which creates all of it.
    """
    bad = build("bad-bookkeeping-workflow", bookkeeping="two")
    harness = conftest.materialize_workflow(bad, tmp_path / "bad-harness")
    target_root = build_suite_target(tmp_path / "bad-target",
                                     workflow=bad["name"])
    runner = Runner(target_root, workflow=bad)

    assert story_coordinator.run_story(
        STORY_ID, harness, target_root, runner) == 1

    message = capsys.readouterr().err
    assert DECLARING in message
    assert BOOKKEEPING_KEY in message
    assert repr("two") in message
    assert runner.calls == []
    assert not run_dir_of(target_root).exists()
    assert not (target_root / ".harness" / "logs" / f"{STORY_ID}.log").exists()


def test_the_same_run_under_a_sound_bookkeeping_budget_creates_all_of_it(
    tmp_path,
):
    """The control for the refusal above: same builder, same target, same
    runner, with a bookkeeping budget the check accepts."""
    sound = build("sound-bookkeeping-workflow", bookkeeping=0)
    harness = conftest.materialize_workflow(sound, tmp_path / "sound-harness")
    target_root = build_suite_target(tmp_path / "sound-target",
                                     workflow=sound["name"])
    runner = Runner(target_root, workflow=sound)

    assert story_coordinator.run_story(
        STORY_ID, harness, target_root, runner) == 0

    assert runner.calls
    assert run_dir_of(target_root).is_dir()
    assert (target_root / ".harness" / "logs" / f"{STORY_ID}.log").is_file()


# --------------------------------------------------------------------------
# The counter is state, and a reader of state.json meets both numbers
# --------------------------------------------------------------------------


def test_a_state_file_written_before_this_story_still_loads(tmp_path):
    """The new field is defaulted, and a missing value means no bookkeeping
    re-entry — which is also what a stage that never opted into the split
    carries."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_json(run_dir / "state.json", {
        "story_id": STORY_ID, "branch": f"story/{STORY_ID}",
        "current_stage": DECLARING, "status": "running", "self_route_count": 2,
    })
    assert story_coordinator.load_state(run_dir).bookkeeping_self_route_count \
        == 0

    # The control: a state file that *does* carry the subset loads it, so the
    # zero above is the default rather than the field being ignored.
    write_json(run_dir / "state.json", {
        "story_id": STORY_ID, "branch": f"story/{STORY_ID}",
        "current_stage": DECLARING, "status": "running", "self_route_count": 2,
        "bookkeeping_self_route_count": 1,
    })
    assert story_coordinator.load_state(run_dir).bookkeeping_self_route_count \
        == 1


def test_the_field_sits_beside_the_total_it_is_a_subset_of():
    """The two are declared together, so a reader of `RunState` meets the
    subset where they meet the total rather than somewhere else."""
    names = [f.name for f in dataclasses.fields(story_coordinator.RunState)]
    assert names.index("bookkeeping_self_route_count") == \
        names.index("self_route_count") + 1
