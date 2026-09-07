"""A stage's changed-files record accounts for every file the stage touched.

story-101's tester changed a file in the target tree and left it out of its
changed-files record, and nothing caught it. The record is what the whole
governance layer reads — `recorded_by_all_stages` derives what a run touched
from the records, `recorded_by_other_stages` derives which stage created a
governed path, and the blocked-path, ownership and revert checks each decide
from what a record names — so a path missing from one is a path no check can
adjudicate and no reader of the run can see was touched at all.

So the coordinator takes a signature of the target tree before each stage that
declares a record is invoked, compares it after that stage's turn, and reports
any path the stage changed that its record does not name. It amends nothing:
the omission re-enters the stage that wrote the record, which is the only stage
that may correct one, on the bookkeeping cause story-108 created.

What this module holds:

  * the discrimination, which is the whole question. A stage that changes a
    file and omits it is re-entered on the new cause; the *same* stage, with a
    record naming what it changed, is not re-entered and the run walks on to
    the next stage. A check shown only firing has not been shown to work.
  * story-101's own case, driven end to end: a stage edits a file under the
    target's configured test location that the story's scope permits, records
    every other path it changed, and the omitted path alone is reported.
  * the budget the omission spends — the bookkeeping one where a stage declares
    it, `max_self_routes` where a stage does not, and an escalation naming the
    record and the omitted paths when the bookkeeping budget is gone.
  * the statement the re-entered stage is given, which is its own text and not
    the missing-artifact or stale-artifact one, and which reaches that stage's
    re-run prompt.
  * first-seen-wins: a stage re-entered for an omission is compared against the
    tree it originally found, so a re-entry that ignores the report is reported
    again rather than passing.
  * what is *not* reported — another stage's recorded path across a backward
    retry, a path under a repository-wide blocked prefix, a path git excludes
    as ignored, the coordinator's own agent log, and a record naming more than
    the tree shows. Each sits beside a demonstration that the same check
    reports the thing it is supposed to report.
  * the three checks that read the record afterwards, still reading it: a run
    whose record was completed by a re-entry has that completed record
    adjudicated by the blocked-path check, the ownership check and the revert
    check — and the ordering is driven rather than described, by a stage whose
    unrecorded creation under a restricted prefix is reported as an omission
    first and as an ownership violation only once the record names it.

The workflow these runs execute is built by `tests/conftest.py`'s builder and
materialized into a harness root this module owns. The mechanism is the
subject; the stage list, the budgets and the record names are inputs to it, and
deriving them from what this repository deploys would make a deployment fact
into something this module enforces. Every name is still derived rather than
written — from the definition this module builds, and from the target's own
configuration and the harness's own rules where the value is theirs. The record
names the fixture declares are names this repository does not carry, which is
what makes "the check reads the name off the declaration" checkable.

The target is `tests/test_coordinator_runs_the_suite.py`'s: a repository whose
configured suite exits zero exactly when a sentinel file under a governed
prefix has been repaired, which is what lets the revert check below reach a
verdict rather than a shrug. Reused rather than copied.

Nothing here invokes a model, and nothing here runs this repository's suite.
"""
import json
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import story_coordinator
from agent_runner import AgentResult
from conftest import StageRef, workflow_stage

# The target builder and the sentinel-driven suite are
# tests/test_coordinator_runs_the_suite.py's; the file writers and the story
# amendment are tests/test_self_routing_retry.py's. Reused rather than copied,
# so a regression in either reddens more than one file.
from test_coordinator_runs_the_suite import (  # noqa: E402
    GOVERNED_PREFIX,
    PASS,
    REPAIRED,
    SENTINEL,
    STORY_ID,
    build_suite_target,
    run_dir_of,
)
from test_self_routing_retry import (  # noqa: E402
    GUIDANCE,
    amend_the_story,
    write,
    write_json,
)

COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

CAUSE = story_coordinator.INCOMPLETE_CHANGED_FILES

# --------------------------------------------------------------------------
# The workflow these runs execute
# --------------------------------------------------------------------------

#: The records the two writing stages declare. Deliberately names this
#: repository does not carry: an omission reported against one of them got its
#: name off the declaration rather than out of the coordinator.
RECORD = "touched-files-probe.json"
OTHER_RECORD = "other-touched-files-probe.json"

#: Where the revert check's own record goes, likewise the fixture's name.
REVERT_ARTIFACT = "revert-probe-result.json"

#: Two rather than one for each, because a budget of one cannot distinguish
#: "the stage ran again" from "the budget is spent", and the first-seen-wins
#: case below needs a stage to be re-entered twice for the same omission.
FAILURE_BUDGET = 2
BOOKKEEPING_BUDGET = 2

#: What the unsplit stage may spend on an omission: the failure budget, which
#: is the only budget a stage that never opted into the split has.
UNSPLIT_BUDGET = 1


def split_stage(**extra) -> dict:
    """The stage that declares a record and opts into the bookkeeping split."""
    return workflow_stage(
        outputs=(RECORD, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=RECORD,
        max_self_routes=FAILURE_BUDGET,
        max_bookkeeping_self_routes=BOOKKEEPING_BUDGET,
        schemas={RECORD: "changed-files"},
        **extra)


def unsplit_stage() -> dict:
    """A second stage declaring a record and no bookkeeping budget.

    It is here twice over: it is what shows the check is driven off the
    declaration rather than off a stage name — two different stages are held to
    it — and it is the stage that spends `max_self_routes` on an omission,
    which is what every stage that has not opted into the split still does.
    """
    return workflow_stage(
        outputs=(OTHER_RECORD,),
        changed_files=OTHER_RECORD,
        max_self_routes=UNSPLIT_BUDGET,
        schemas={OTHER_RECORD: "changed-files"})


def unchecked_stage() -> dict:
    """A stage declaring no changed-files record, and so nothing to be held to."""
    return workflow_stage(outputs=(conftest.DOCUMENTATION_REPORT,))


def verifying_stage() -> dict:
    return workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"the-work": {
            "stage": StageRef(0),
            "when": "the behaviour the story asked for is missing"}})


def build(name: str, first: dict) -> dict:
    return conftest.build_workflow(
        first, unsplit_stage(), unchecked_stage(), verifying_stage(),
        escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
        name=name)


WORKFLOW = build("records-every-file-workflow", split_stage())

#: The same definition with the first stage governed: a prefix it may not
#: create under and the revert check that decides its modifications there. The
#: three checks that read a record after the completeness check are what this
#: one is for.
GOVERNING = build(
    "records-every-file-governed-workflow",
    split_stage(may_not_create=(GOVERNED_PREFIX,),
                revert_check={"result": REVERT_ARTIFACT,
                              "baseline": "stage-baseline"}))

SPLIT, UNSPLIT, UNCHECKED, VERIFYING = [
    stage["name"] for stage in WORKFLOW["stages"]]

RETRY_CATEGORY = next(iter(
    WORKFLOW["stages"][-1]["on_failure"]["retry_routing"]))

FAILED = {
    "status": "failed",
    "blocking_issues": [{"severity": "high", "issue": "the work is not done",
                         "location": "src/app.py",
                         "required_behavior": "the sample behavior exists"}],
    "unverified": [], "retry_recommended": True, "retry_target": RETRY_CATEGORY,
}


def test_the_definitions_this_module_builds_have_something_to_say():
    """Every derivation above, stated so a change to the builder reddens here
    rather than quietly emptying the cases below.

    What this module needs is one stage that declares a record and a
    bookkeeping budget, a second that declares a record and no bookkeeping
    budget, a third that declares no record at all, and a verifier whose retry
    route reaches the first — so that "held to it", "not held to it" and "held
    to it under the other budget" are all reachable in one run.
    """
    stages = {stage["name"]: stage for stage in WORKFLOW["stages"]}
    assert stages[SPLIT]["changed_files"] == RECORD
    assert stages[UNSPLIT]["changed_files"] == OTHER_RECORD
    assert "changed_files" not in stages[UNCHECKED]
    assert "changed_files" not in stages[VERIFYING]

    assert stages[SPLIT][story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY] \
        == BOOKKEEPING_BUDGET >= 2
    assert story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY \
        not in stages[UNSPLIT]
    assert stages[UNSPLIT][story_coordinator.SELF_ROUTE_BUDGET_KEY] \
        == UNSPLIT_BUDGET
    assert WORKFLOW["stages"][-1]["on_failure"]["retry_routing"][
        RETRY_CATEGORY]["stage"] == SPLIT

    # The governed variant differs from it in exactly the two declarations the
    # three later checks read.
    governed = GOVERNING["stages"][0]
    assert governed["may_not_create"] == [GOVERNED_PREFIX]
    assert governed["revert_check"]["result"] == REVERT_ARTIFACT
    assert {k: v for k, v in governed.items()
            if k not in ("may_not_create", "revert_check")} \
        == WORKFLOW["stages"][0]


def test_the_record_names_this_module_declares_are_names_the_harness_lacks():
    """What makes "the check reads the record name off the declaration"
    checkable: neither name appears in the coordinator, so an omission reported
    against one of them was reported against a name the workflow supplied."""
    assert RECORD not in COORDINATOR_SOURCE
    assert OTHER_RECORD not in COORDINATOR_SOURCE
    assert RECORD in json.dumps(WORKFLOW)
    assert OTHER_RECORD in json.dumps(WORKFLOW)


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
        agent_runner.run_agent("prompt", stage=SPLIT, cwd=tmp_path,
                               log_path=tmp_path / "agent.log")


# --------------------------------------------------------------------------
# The target, and the paths the stages below write into it
# --------------------------------------------------------------------------

#: A file the builder commits so a stage below can delete something that was
#: there before the run, which is the third of the changed-files groups.
DELETABLE = "src/scratch.py"

#: The ignore rule the target carries and the path it covers: a suite run
#: inside a stage's turn leaves exactly this kind of debris behind. The control
#: beside it is the same content at a path the rule does not cover, and it
#: carries no suffix a developer's global excludes are likely to claim — so the
#: pair differs by this target's ignore rule rather than by whose machine the
#: suite is running on.
IGNORE_RULE = "__pycache__/"
IGNORED_PATH = "__pycache__/probe.pyc"
NOT_IGNORED_PATH = "left-behind-probe.txt"


def configured_test_location(target_root: Path) -> str:
    """Where this target says its tests live, read off its own configuration.

    story-101's omitted file was under the target's configured test location,
    and the fixture below puts its omitted file there for that reason — so the
    value is resolved from the target rather than written here.
    """
    return harness_config.load_config(Path(target_root))["tests_dir"]


def story_permitting(scope: list[str]) -> str:
    """A story artifact whose scope permits the paths the stages below edit.

    So that an omission driven here is a bookkeeping defect and not a
    governance violation wearing its clothes: every path a stage writes is one
    the story says the run may modify.
    """
    permitted = "".join(f"    - {path}\n" for path in scope)
    return f"""\
story:
  id: {STORY_ID}
  title: A stand-in story whose scope permits what these stages edit
  description: |
    A stand-in story used to exercise the changed-files completeness check
    deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

scope:
  modify:
{permitted}
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior
""" + conftest.MANDATE_BLOCK


def build_probe_target(root: Path, *, workflow: str = WORKFLOW["name"]) -> Path:
    """The suite target, with a scope that permits these edits and an ignore rule.

    Everything else is `build_suite_target`'s: the sentinel the configured
    suite reads, the governed prefix it sits under, and a committed tree.
    """
    build_suite_target(root, workflow=workflow)
    write(root / DELETABLE, "something that was here before the run\n")
    write(root / ".gitignore", f"{IGNORE_RULE}\n")
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml",
          story_permitting(["src/", configured_test_location(root),
                            GOVERNED_PREFIX]))
    conftest.commit_setup(root, "the tree these runs start from")
    return root


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so one test can hold a subject and its control side by side."""
    def make(name: str, workflow: dict = WORKFLOW) -> Path:
        return build_probe_target(tmp_path / name, workflow=workflow["name"])
    return make


@pytest.fixture
def target_root(make_target) -> Path:
    return make_target("records-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying both definitions, so a run under either loads a
    real file out of the same root and they differ in what they declare."""
    root = conftest.materialize_workflow(WORKFLOW, tmp_path / "records-harness")
    return conftest.materialize_workflow(GOVERNING, root)


# --------------------------------------------------------------------------
# The fake runner
#
# Driven by a per-stage, per-invocation plan of turns. A turn says what the
# invocation writes into the target tree and which of those paths its record
# leaves out, so an omission here is a stage doing work and not writing it
# down — the thing the check exists to notice.
# --------------------------------------------------------------------------


class Turn:
    """One invocation's edits to the target tree, and the record it writes.

    `writes` maps a repository-relative path to its content and `deletes` names
    paths to remove; the record accounts for each of them, in the group that
    describes what happened to it, unless the path is named in `omits`.
    `also_records` names paths the record claims and the turn did not touch,
    which is the case the check is asked to stay silent about.

    `records_as_created` names paths this turn's record accounts for under
    "created" although they are already on disk when it writes them. That is
    what a stage correcting an omission writes: the file exists because an
    earlier turn of the *same* stage created it, so "created" is the stage's
    true account of its own work even though existence at write time says
    otherwise. The completeness check takes the union of the three groups and
    cannot tell the difference; the ownership check reads "created" alone, so
    the distinction is what carries the corrected record into it.
    """

    def __init__(self, writes: dict | None = None, deletes=(), omits=(),
                 also_records=(), records_as_created=()):
        self.writes = dict(writes or {})
        self.deletes = tuple(deletes)
        self.omits = frozenset(omits)
        self.also_records = tuple(also_records)
        self.records_as_created = frozenset(records_as_created)

    def act(self, target_root: Path) -> dict:
        """Make the edits and return the record this turn writes for them."""
        record = {group: [] for group in story_coordinator.CHANGED_FILE_GROUPS}
        for relative, text in self.writes.items():
            path = Path(target_root) / relative
            group = ("created"
                     if relative in self.records_as_created or not path.exists()
                     else "modified")
            write(path, text)
            if relative not in self.omits:
                record[group].append(relative)
        for relative in self.deletes:
            (Path(target_root) / relative).unlink()
            if relative not in self.omits:
                record["deleted"].append(relative)
        record["modified"].extend(self.also_records)
        return record


def _nth(sequence: list, index: int, default):
    if not sequence or index >= len(sequence):
        return default
    return sequence[index]


class Runner:
    """A fake agent runner that plays one turn per invocation.

    Every artifact it writes comes off the stage's declaration in the *loaded*
    workflow rather than off a list written here, and the record it writes is
    the turn's own account of the edits the turn made.
    """

    def __init__(self, target_root: Path, plan: dict | None = None,
                 verdicts: list | None = None, workflow: dict | None = None):
        self.target_root = Path(target_root)
        self.run_dir = run_dir_of(target_root)
        self.plan = plan or {}
        self.verdicts = list(verdicts or [PASS])
        self.stages = (workflow or WORKFLOW)["stages"]
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        #: (stage, self_route_count, bookkeeping_self_route_count) at entry.
        #: Read off the run's own state.json, because both counters are zeroed
        #: when the next stage starts — so a run that completes has nothing
        #: left to read afterwards, and what a budget cost has to be observed
        #: while it is being spent.
        self.counts: list[tuple[str, int, int]] = []

    def _declaration(self, stage: str) -> dict:
        return next(s for s in self.stages if s["name"] == stage)

    def _counts_now(self) -> tuple[int, int]:
        path = self.run_dir / "state.json"
        if not path.is_file():
            return (0, 0)
        state = read_json(path)
        return (state.get("self_route_count", 0),
                state.get("bookkeeping_self_route_count", 0))

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, **extra):
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        self.counts.append((stage, *self._counts_now()))
        call = self.calls.count(stage)
        if log_path:
            # The real runner writes the stage's log here, inside the target
            # tree — which is what makes "the coordinator's own writes are not
            # this stage's omission" a live question in every run below.
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{stage} invocation {call}\n")

        record = _nth(self.plan.get(stage, []), call - 1,
                      Turn()).act(self.target_root)
        verdict = conftest.answering_guidance(
            self.verdicts[min(self.calls.count(VERIFYING) - 1,
                              len(self.verdicts) - 1)],
            self.run_dir)
        declaration = self._declaration(stage)
        for artifact in story_coordinator.required_artifacts(declaration):
            path = self.run_dir / artifact
            if artifact == conftest.VERIFICATION_RESULT:
                write_json(path, verdict)
            elif artifact == declaration.get("changed_files"):
                write_json(path, record)
            else:
                write(path, f"{artifact} written by {stage} call {call}.\n")
        if stage == VERIFYING and verdict.get("retry_recommended"):
            write_json(self.run_dir / conftest.RETRY_GUIDANCE, GUIDANCE)
        return AgentResult(ok=True, result_text=f"{stage} done")


def drive(target_root: Path, harness: Path, plan: dict | None = None,
          verdicts: list | None = None, workflow: dict = WORKFLOW,
          start_stage: str | None = None):
    """One run, returning its exit code, its runner and its run directory."""
    runner = Runner(target_root, plan, verdicts, workflow)
    code = story_coordinator.run_story(
        STORY_ID, harness, target_root, runner, start_stage)
    return code, runner, run_dir_of(target_root)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def state_of(run_dir: Path) -> dict:
    return read_json(run_dir / "state.json")


def self_route_records(run_dir: Path) -> list[dict]:
    return [read_json(path)
            for path in sorted(Path(run_dir).glob("self-route-*.json"))]


def causes_of(run_dir: Path) -> list[str]:
    return [record["failure"] for record in self_route_records(run_dir)]


def recorded_paths(run_dir: Path, artifact: str) -> set[str]:
    """Every path a changed-files record names, across all three groups.

    The union rather than one group, because that is how the check reads a
    record: a path recorded under a different heading is a classification
    quibble and not an omission.
    """
    record = read_json(Path(run_dir) / artifact)
    return {path for group in story_coordinator.CHANGED_FILE_GROUPS
            for path in record.get(group, [])}


def reason_of(run_dir: Path) -> str:
    reason = story_coordinator.escalation_reason(run_dir)
    assert reason, "the run did not escalate, so there is no reason to read"
    return reason


def signature_of(run_dir: Path, stage: str) -> dict:
    return read_json(run_dir / story_coordinator.stage_signature_file(stage))


# --------------------------------------------------------------------------
# The discrimination: an omission is reported, and a complete record is not
# --------------------------------------------------------------------------

#: What a stage below writes into the target tree. Content rather than
#: emptiness, so a file that is written and then written again with something
#: else is a modification the comparison can see.
WROTE = "what the stage put here\n"


@pytest.fixture
def omitted_then_recorded(target_root, harness_root):
    """The stage changes a file and does not record it; its re-entry records it.

    The subject of the first two cases below, and the fixture the statement and
    the budget are read out of.
    """
    unrecorded = f"{configured_test_location(target_root)}test_written.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={unrecorded: WROTE}, omits=[unrecorded]),
                 Turn(writes={unrecorded: WROTE})]})
    return code, runner, run_dir, unrecorded


def test_a_stage_that_omits_a_file_it_wrote_is_re_entered_on_the_new_cause(
    omitted_then_recorded,
):
    """The story's first acceptance criterion, driven end to end.

    The stage wrote a file into the target tree and its record did not name it,
    so the coordinator re-entered that stage — the only stage that may amend
    the record — and the self-route artifact it left names the new cause under
    `failure`.
    """
    code, runner, run_dir, unrecorded = omitted_then_recorded

    assert code == 0
    assert runner.calls.count(SPLIT) == 2
    assert causes_of(run_dir) == [CAUSE]

    record = self_route_records(run_dir)[0]
    assert record["failure"] == CAUSE
    assert record["stage"] == SPLIT
    assert record["artifacts"] == [RECORD]
    assert unrecorded in record["reason"]
    assert RECORD in record["reason"]

    # And the record the stage finally wrote names the file, which is what
    # every reader of the run downstream of this asks it.
    assert unrecorded in recorded_paths(run_dir, RECORD)


def test_the_same_stage_recording_what_it_changed_is_not_re_entered(
    target_root, harness_root,
):
    """The control the check is worth nothing without.

    The identical edit with the identical stage under the identical definition,
    recorded rather than omitted: the stage is invoked once, no self-route is
    taken, and the run walks on to the next stage and finishes. A check shown
    only firing has not been shown to discriminate.
    """
    recorded = f"{configured_test_location(target_root)}test_written.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={recorded: WROTE})]})

    assert code == 0
    assert runner.calls.count(SPLIT) == 1
    assert self_route_records(run_dir) == []
    assert runner.calls == [SPLIT, UNSPLIT, UNCHECKED, VERIFYING]
    assert state_of(run_dir)["status"] == "completed"


def test_the_omitted_path_alone_is_reported_when_the_rest_is_recorded(
    target_root, harness_root,
):
    """story-101's own case, driven rather than described.

    A stage edits a file under the target's *configured* test location, which
    the story's scope permits, records every other path it changed, and leaves
    that one out. The report names it and says nothing about the paths the
    record accounts for — so what is being validated is a bookkeeping omission
    and not a governance violation wearing its clothes.
    """
    location = configured_test_location(target_root)
    omitted = f"{location}test_the_stage_forgot_this.py"
    recorded = "src/app.py"

    # The premises: the omitted path is under the configured test location, and
    # the story permits the run to modify there.
    story = (target_root / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text(
        encoding="utf-8")
    assert omitted.startswith(location)
    assert f"    - {location}\n" in story

    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={omitted: WROTE, recorded: WROTE},
                      omits=[omitted]),
                 Turn(writes={omitted: WROTE, recorded: WROTE})]})

    assert code == 0
    assert causes_of(run_dir) == [CAUSE]
    reason = self_route_records(run_dir)[0]["reason"]
    assert omitted in reason
    assert recorded not in reason


def test_the_report_names_the_group_each_unrecorded_change_belongs_to(
    target_root, harness_root,
):
    """A creation, a modification and a deletion, none of them recorded.

    The derivation is expressed in the three groups the changed-files schema
    already uses, so a reader of the report — and the stage being asked to
    amend its record — is told what to write down as well as where.
    """
    created = f"{configured_test_location(target_root)}test_new.py"
    modified = "src/app.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        # The second turn's record accounts for all three, the deletion the
        # first turn made included: what the check compares against is the tree
        # the attempt began on, so the deletion is still one of the differences
        # this stage is answerable for.
        {SPLIT: [Turn(writes={created: WROTE, modified: WROTE},
                      deletes=[DELETABLE],
                      omits=[created, modified, DELETABLE]),
                 Turn(writes={created: WROTE, modified: WROTE},
                      also_records=[DELETABLE])]})

    assert code == 0
    reason = self_route_records(run_dir)[0]["reason"]
    assert f"modified {modified}" in reason
    assert f"created {created}" in reason
    assert f"deleted {DELETABLE}" in reason


# --------------------------------------------------------------------------
# Which budget an omission spends
# --------------------------------------------------------------------------


def test_the_new_cause_is_declared_and_classified_as_bookkeeping():
    """The membership the budget decision turns on, read off the declarations.

    The parametrized tests in tests/test_bookkeeping_self_route_budget.py
    derive their cases from these two tuples, so the cause reaching the budget
    decision *as a value* is what puts it through them without their being
    edited to name it. This says the value is there to be picked up.
    """
    assert CAUSE in story_coordinator.SELF_ROUTE_FAILURES
    assert CAUSE in story_coordinator.BOOKKEEPING_SELF_ROUTE_FAILURES


def test_an_omission_spends_the_bookkeeping_budget_and_not_the_failure_one(
    omitted_then_recorded,
):
    """A stage that declares the split spends its bookkeeping budget on an
    omission, so the failure budget it would have had for work that was
    actually wrong is untouched.

    Read off the state.json the re-entered invocation itself found, because
    both counters are zeroed when the next stage starts and a completed run has
    nothing left to read: the total counts every re-entry, the subset counts
    the bookkeeping ones, and the difference is what the failure budget has
    been charged.
    """
    _, runner, _, _ = omitted_then_recorded
    entries = [counts for counts in runner.counts if counts[0] == SPLIT]

    assert entries[0] == (SPLIT, 0, 0)
    assert entries[1] == (SPLIT, 1, 1)
    # The difference is the failure budget, spent: nothing.
    assert entries[1][1] - entries[1][2] == 0


def test_a_stage_declaring_no_bookkeeping_budget_spends_the_failure_one(
    target_root, harness_root,
):
    """The compatibility half, driven at the stage that never opted into the
    split: it is re-entered for its omission just the same, and the unit comes
    out of `max_self_routes` — the unsplit behaviour every other cause already
    has there.

    Its control is the test above, which is the same omission at the stage that
    did opt in and moves the other counter.
    """
    unrecorded = "src/written_by_the_second_stage.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {UNSPLIT: [Turn(writes={unrecorded: WROTE}, omits=[unrecorded]),
                   Turn(writes={unrecorded: WROTE})]})

    assert code == 0
    assert runner.calls.count(UNSPLIT) == 2
    assert causes_of(run_dir) == [CAUSE]

    entries = [counts for counts in runner.counts if counts[0] == UNSPLIT]
    assert entries[0] == (UNSPLIT, 0, 0)
    assert entries[1] == (UNSPLIT, 1, 0)


def test_an_exhausted_bookkeeping_budget_escalates_naming_what_was_omitted(
    target_root, harness_root,
):
    """A stage that keeps omitting stops, and the stop says what it stopped on.

    The escalation reason names the record and every path missing from it, so a
    developer reading the run knows what to write down without opening the
    coordinator; and it names the budget that was exhausted, so a bookkeeping
    stop and a failure stop do not read as one thing.
    """
    unrecorded = f"{configured_test_location(target_root)}test_forgotten.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={unrecorded: WROTE}, omits=[unrecorded])]
         * (BOOKKEEPING_BUDGET + 1)})

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert runner.calls.count(SPLIT) == BOOKKEEPING_BUDGET + 1
    assert causes_of(run_dir) == [CAUSE] * BOOKKEEPING_BUDGET

    reason = reason_of(run_dir)
    assert unrecorded in reason
    assert RECORD in reason
    assert f"{story_coordinator.BOOKKEEPING_SELF_ROUTE_BUDGET_KEY} budget of " \
        f"{BOOKKEEPING_BUDGET}" in reason


# --------------------------------------------------------------------------
# The statement the re-entered stage is given
# --------------------------------------------------------------------------


def test_the_statement_is_the_new_causes_own_text(omitted_then_recorded):
    """The re-entered stage is told what it left out, in words written for this
    cause: the record it must amend and the paths missing from it.

    Distinctness is asserted against the two statements this one could have
    inherited — the missing-artifact and stale-artifact texts, composed for the
    same artifact — because a cause that fell through to another's wording
    would still name the record and would tell the stage to write a file it has
    already written.
    """
    _, _, run_dir, unrecorded = omitted_then_recorded
    statement = self_route_records(run_dir)[0]["statement"]

    assert RECORD in statement
    assert unrecorded in statement

    others = [story_coordinator.self_route_statement(cause, [RECORD], None)
              for cause in (story_coordinator.MISSING_REQUIRED_ARTIFACTS,
                            story_coordinator.STALE_REQUIRED_ARTIFACTS)]
    assert statement not in others
    for other in others:
        assert unrecorded not in other


def test_the_stale_wording_is_reached_by_its_own_condition(
    omitted_then_recorded,
):
    """The control for the distinctness above, and the property that keeps it.

    The stale text used to be the function's unconditional fall-through, so any
    cause without a branch inherited it silently. Every cause now reaches its
    text by its own condition and an unknown one raises — which is what makes
    "this statement is its own text" a fact about the function rather than
    about the order its branches happen to sit in.
    """
    _, _, run_dir, _ = omitted_then_recorded
    statement = self_route_records(run_dir)[0]["statement"]

    stale = story_coordinator.self_route_statement(
        story_coordinator.STALE_REQUIRED_ARTIFACTS, [RECORD], None)
    assert statement != stale

    with pytest.raises(ValueError):
        story_coordinator.self_route_statement("a-cause-nobody-declared",
                                               [RECORD], None)

    # And every declared cause has words of its own, so the branch above is the
    # only way an unclassified cause can arrive.
    for cause in story_coordinator.SELF_ROUTE_FAILURES:
        assert story_coordinator.self_route_statement(cause, [RECORD], ["a: b"])


def test_the_statement_reaches_the_re_entered_stages_prompt(
    omitted_then_recorded,
):
    """The statement is evidence only if the stage running again is shown it.

    Its control is the prompt of the invocation that made the omission, which
    names neither the record's omission nor the path — so what the second
    prompt carries arrived because of the self-route rather than because both
    prompts mention everything.
    """
    _, runner, _, unrecorded = omitted_then_recorded
    first, second = runner.prompts[SPLIT]

    assert unrecorded in second
    assert RECORD in second
    assert unrecorded not in first


# --------------------------------------------------------------------------
# What the comparison is taken against: the tree the stage found
# --------------------------------------------------------------------------


def test_a_re_entry_that_ignores_the_report_is_reported_again(
    target_root, harness_root,
):
    """First seen wins, driven by the case that distinguishes it.

    The stage writes a file and omits it; the invocation the re-entry brings
    changes nothing and still does not record it. Compared against the tree as
    the *re-entry* found it, the second turn changed nothing and would pass —
    the omission would become invisible at exactly the moment it is being
    corrected. Compared against the tree the attempt began on, it is reported
    again, which is what happens here.
    """
    unrecorded = f"{configured_test_location(target_root)}test_ignored.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={unrecorded: WROTE}, omits=[unrecorded]),
                 Turn(),
                 Turn(writes={unrecorded: WROTE})]})

    assert code == 0
    assert runner.calls.count(SPLIT) == 3
    assert causes_of(run_dir) == [CAUSE, CAUSE]
    for record in self_route_records(run_dir):
        assert unrecorded in record["reason"]


def test_the_signature_holds_the_tree_the_first_invocation_found(
    omitted_then_recorded, target_root,
):
    """The same property read off the artifact rather than off the routing.

    The signature the run kept does not hold the file the stage created, while
    the tree the run left does — so what a re-entry is compared against is the
    tree before the stage ran, and the assertion above is not passing because
    the coordinator stopped looking.
    """
    _, _, run_dir, unrecorded = omitted_then_recorded
    signature = signature_of(run_dir, SPLIT)

    assert unrecorded not in signature["paths"]
    assert (Path(target_root) / unrecorded).is_file()
    # It holds a tree rather than nothing, which is what makes the absence
    # above a statement about when the capture happened.
    assert SENTINEL in signature["paths"]


def test_the_signature_is_written_under_a_name_one_function_shapes(
    omitted_then_recorded,
):
    """It lives in the run directory rather than in memory, so a coordinator
    process that exits and comes back reads what was captured rather than
    taking a fresh reading; and the name is shaped in one place, so what is
    written and what is looked for cannot drift.

    The stage that declares no record has no signature, which is the same fact
    the check's own silence about it says.
    """
    _, _, run_dir, _ = omitted_then_recorded

    assert (run_dir / story_coordinator.stage_signature_file(SPLIT)).is_file()
    assert not (run_dir /
                story_coordinator.stage_signature_file(UNCHECKED)).exists()

    # One function shapes it: the constant half of the name appears once in the
    # coordinator, at the function that builds it.
    stem = story_coordinator.stage_signature_file("").partition(".")[0]
    assert stem
    assert COORDINATOR_SOURCE.count(stem) == 1

    # And it records which attempt and which entry of the run it was taken for,
    # which is what a resumed process compares before reusing it.
    signature = signature_of(run_dir, SPLIT)
    assert signature["attempt"] == 1
    assert signature["entry"] == 0


def test_a_resumed_run_keeps_checking_the_stage_it_resumes_into(
    target_root, harness_root,
):
    """A run stopped by an exhausted bookkeeping budget, resumed, and the
    resumed stage still held to its record.

    The signature survives the process that took it because it is a file in the
    run directory; what a new entry of the run may reuse is decided by the
    attempt and entry recorded inside it rather than by the file's presence,
    because the tree a resumed stage starts from is the tree the developer
    committed before deciding to resume. Either way the stage that omits after
    the resume is re-entered for it, which is what this drives.
    """
    unrecorded = f"{configured_test_location(target_root)}test_forgotten.py"
    omitting = [Turn(writes={unrecorded: WROTE}, omits=[unrecorded])]
    code, _, run_dir = drive(target_root, harness_root,
                             {SPLIT: omitting * (BOOKKEEPING_BUDGET + 1)})
    assert code == 2

    amend_the_story(target_root)
    resumed = f"{configured_test_location(target_root)}test_forgotten_again.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={resumed: WROTE}, omits=[resumed]),
                 Turn(writes={resumed: WROTE})]},
        start_stage=SPLIT)

    assert code == 0
    assert runner.calls.count(SPLIT) == 2
    # This entry's own first self-route, named by the function that names them,
    # rather than whatever the previous entry left in the directory.
    record = read_json(run_dir / story_coordinator.self_route_result_file(
        SPLIT, 1, 1))
    assert record["failure"] == CAUSE
    assert resumed in record["reason"]
    assert signature_of(run_dir, SPLIT)["entry"] == 1


def test_a_path_another_stages_record_accounts_for_is_not_reported_here(
    target_root, harness_root,
):
    """A backward retry, with a second stage's file written between two
    invocations of the first.

    The first stage runs and records its edit; the second creates a file under
    the configured test location and records it in its own record; a failed
    verdict routes a retry back to the first. The file the second stage created
    is in the tree the first stage's second invocation runs over, and it is not
    the first stage's to account for.

    The control is inside the same run: that invocation *also* writes a file of
    its own and leaves it out, and the report names that one. So the silence
    about the other stage's file is a statement about attribution rather than a
    check that had stopped looking.
    """
    location = configured_test_location(target_root)
    theirs = f"{location}test_written_by_the_other_stage.py"
    mine = "src/written_on_the_retry.py"

    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={"src/app.py": WROTE}),
                 Turn(writes={mine: WROTE}, omits=[mine]),
                 Turn(writes={mine: WROTE})],
         UNSPLIT: [Turn(writes={theirs: WROTE}),
                   Turn(also_records=[theirs])]},
        verdicts=[FAILED, PASS])

    assert code == 0
    assert causes_of(run_dir) == [CAUSE]
    reason = self_route_records(run_dir)[0]["reason"]
    assert mine in reason
    assert theirs not in reason
    # The other stage did record it, which is what makes it accounted for.
    assert theirs in recorded_paths(run_dir, OTHER_RECORD)


# --------------------------------------------------------------------------
# What is not a stage's omission
# --------------------------------------------------------------------------


def blocked_prefix_the_coordinator_does_not_write(harness: Path,
                                                  target_root: Path) -> str:
    """A repository-wide blocked prefix that is nobody's working directory.

    Read off the harness's own rules and narrowed to something a stage can
    safely write into: not the git directory, not the story artifacts the run
    is reading, and not one of the directories the coordinator itself writes
    into during a turn — which are subtracted for their own reason and would
    make a case about blocked paths pass for a different one.
    """
    config = harness_config.load_config(Path(target_root))
    excluded = set(story_coordinator.coordinator_written_prefixes(config)) | {
        ".git/", f"{config['stories_dir'].rstrip('/')}/"}
    candidates = [prefix
                  for prefix in harness_config.load_rules(Path(harness))[
                      "blocked_paths"]
                  if prefix not in excluded]
    assert candidates, "the rules block nothing a stage could be tested against"
    return candidates[0]


def test_a_path_under_a_blocked_prefix_is_not_reported_as_an_omission(
    make_target, harness_root,
):
    """The coordinator's own writes during a stage's turn land under the
    repository-wide blocked paths, and reporting them would re-enter every
    stage of every run for work no stage did.

    Its control is the same runner writing the same content at a path the rules
    do not block, in a second run beside it: that one is reported, so the
    silence above is the subtraction and not a check that saw nothing.
    """
    quiet = make_target("blocked-quiet")
    prefix = blocked_prefix_the_coordinator_does_not_write(harness_root, quiet)
    blocked = f"{prefix}written-during-the-turn.txt"
    unblocked = "written-during-the-turn.txt"

    # The premises: one path is blocked and the other is not, and neither is
    # subtracted for being a directory the coordinator writes into.
    config = harness_config.load_config(quiet)
    written = story_coordinator.coordinator_written_prefixes(config)
    rules = harness_config.load_rules(harness_root)["blocked_paths"]
    assert story_coordinator.is_blocked(blocked, rules)
    assert not story_coordinator.is_blocked(unblocked, rules)
    assert not story_coordinator.is_blocked(blocked, written)

    code, runner, run_dir = drive(
        quiet, harness_root,
        {SPLIT: [Turn(writes={blocked: WROTE}, omits=[blocked])]})
    assert code == 0
    assert runner.calls.count(SPLIT) == 1
    assert self_route_records(run_dir) == []

    loud = make_target("blocked-control")
    code, runner, control_dir = drive(
        loud, harness_root,
        {SPLIT: [Turn(writes={unblocked: WROTE}, omits=[unblocked]),
                 Turn(writes={unblocked: WROTE})]})
    assert code == 0
    assert causes_of(control_dir) == [CAUSE]
    assert unblocked in self_route_records(control_dir)[0]["reason"]


def test_a_path_git_excludes_as_ignored_is_not_reported_as_an_omission(
    make_target, harness_root,
):
    """A suite run inside a stage's turn leaves caches behind, and they are not
    the stage's work.

    The two runs differ in the path alone: the ignored one and a path the same
    ignore rule does not cover, written with the same content by the same turn.
    """
    quiet = make_target("ignored-quiet")
    assert IGNORE_RULE in (quiet / ".gitignore").read_text(encoding="utf-8")

    code, runner, run_dir = drive(
        quiet, harness_root,
        {SPLIT: [Turn(writes={IGNORED_PATH: WROTE}, omits=[IGNORED_PATH])]})
    assert code == 0
    assert self_route_records(run_dir) == []
    assert (quiet / IGNORED_PATH).is_file()

    loud = make_target("ignored-control")
    code, runner, control_dir = drive(
        loud, harness_root,
        {SPLIT: [Turn(writes={NOT_IGNORED_PATH: WROTE},
                      omits=[NOT_IGNORED_PATH]),
                 Turn(writes={NOT_IGNORED_PATH: WROTE})]})
    assert code == 0
    assert causes_of(control_dir) == [CAUSE]
    assert NOT_IGNORED_PATH in self_route_records(control_dir)[0]["reason"]


def test_the_coordinators_own_agent_log_is_not_reported_against_a_stage(
    target_root, harness_root,
):
    """The log the coordinator appends to while a stage's turn runs sits inside
    the target tree and is named by nobody's record.

    It is exercised by every run in this file, which is why the assertion is
    that a clean run took no self-route at all. What makes that a statement
    about the subtraction is stated beside it: the log is written, git neither
    tracks nor ignores it away — it is in the very listing the comparison is
    taken over — and the rules do not block it. It is out because the
    coordinator's own directories are subtracted, and nothing else.
    """
    code, runner, run_dir = drive(target_root, harness_root)
    log = target_root / ".harness" / "logs" / f"{STORY_ID}.log"
    relative = log.relative_to(target_root).as_posix()

    assert code == 0
    assert self_route_records(run_dir) == []
    assert log.is_file() and log.read_text(encoding="utf-8")

    assert relative in story_coordinator._tracked_and_untracked(target_root)
    assert not story_coordinator.is_blocked(
        relative, harness_config.load_rules(harness_root)["blocked_paths"])
    assert story_coordinator.is_blocked(
        relative, story_coordinator.coordinator_written_prefixes(
            harness_config.load_config(target_root)))


def test_a_record_naming_a_path_the_stage_did_not_change_is_not_reported(
    make_target, harness_root,
):
    """The check reports omissions and nothing else.

    A record that claims more than the tree can show has hidden nothing from
    the readers this protects, and every fake runner under tests/ that records
    a path it did not have to rewrite depends on this staying true. Its control
    is the same runner recording nothing and changing something, which is
    reported.
    """
    generous = make_target("records-more")
    code, runner, run_dir = drive(
        generous, harness_root,
        {SPLIT: [Turn(also_records=["src/app.py", DELETABLE])]})

    assert code == 0
    assert runner.calls.count(SPLIT) == 1
    assert self_route_records(run_dir) == []
    assert read_json(run_dir / RECORD)["modified"] == ["src/app.py", DELETABLE]

    silent = make_target("records-less")
    code, _, control_dir = drive(
        silent, harness_root,
        {SPLIT: [Turn(writes={"src/app.py": WROTE}, omits=["src/app.py"]),
                 Turn(writes={"src/app.py": WROTE})]})
    assert code == 0
    assert causes_of(control_dir) == [CAUSE]


def test_a_stage_declaring_no_record_is_not_checked(target_root, harness_root):
    """The check is driven off the declaration alone: a stage that declares no
    changed-files record has no account to be held to, so the file it writes
    into the tree is not an omission and no signature is taken for it.

    Its control is the stage beside it in the same run, which declares one and
    is re-entered for the same kind of edit.
    """
    theirs = "src/written_by_the_stage_with_no_record.py"
    mine = "src/written_by_the_stage_with_one.py"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {UNCHECKED: [Turn(writes={theirs: WROTE})],
         SPLIT: [Turn(writes={mine: WROTE}, omits=[mine]),
                 Turn(writes={mine: WROTE})]})

    assert code == 0
    assert runner.calls.count(UNCHECKED) == 1
    assert not (run_dir /
                story_coordinator.stage_signature_file(UNCHECKED)).exists()
    assert causes_of(run_dir) == [CAUSE]
    assert theirs not in self_route_records(run_dir)[0]["reason"]
    assert mine in self_route_records(run_dir)[0]["reason"]


# --------------------------------------------------------------------------
# The three checks that read the record after this one
# --------------------------------------------------------------------------


def test_the_completeness_check_precedes_the_three_that_read_the_record():
    """The order at the site, read at the site.

    All three later checks decide from what the record names, and an omitted
    governed path is exactly how an edit escapes the revert check that exists
    to decide whether it was forced. The block is sliced from where the record
    name is resolved, and the four calls are found in it in order.
    """
    opening = COORDINATOR_SOURCE.index('record_name = stage.get("changed_files")')
    block = COORDINATOR_SOURCE[opening:]
    calls = ("unrecorded_changes(", "_blocked_violation(",
             "_ownership_violation(", "revert_check(")
    order = [block.index(call) for call in calls]

    assert order == sorted(order)
    # The same reading over the block with the completeness call moved past
    # them reports the reversal, so a green result above means the positions
    # were compared rather than that the calls were not found.
    moved = block.replace(calls[0], "moved_aside(", 1) + f"\n    {calls[0]}\n"
    assert [moved.index(call) for call in calls] != sorted(
        moved.index(call) for call in calls)


@pytest.fixture
def completed_then_governed(make_target, harness_root):
    """A stage whose omission is reported, corrected, and then adjudicated.

    Under the governed definition: the stage modifies the sentinel the target's
    suite reads, which sits under a prefix it may not create in, and leaves it
    out of its record. The re-entry names it, and the revert check then decides
    the very edit the record was hiding.
    """
    target_root = make_target("governed", GOVERNING)
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={SENTINEL: f"{REPAIRED}\n"}, omits=[SENTINEL]),
                 Turn(writes={SENTINEL: f"{REPAIRED}\n"})]},
        workflow=GOVERNING)
    return code, runner, run_dir, target_root


def test_a_record_completed_by_a_re_entry_is_adjudicated_by_the_revert_check(
    completed_then_governed,
):
    """The completed record reaches the check it was hiding the path from.

    The stage's edit is under a prefix the workflow governs, so the revert
    check builds a clone with it undone and runs the target's suite there. The
    suite goes red without the edit, which is what permits it — and none of
    that could have happened while the record did not name the path, because
    the revert check reverts what the record names.
    """
    code, runner, run_dir, _ = completed_then_governed

    assert code == 0
    assert causes_of(run_dir) == [CAUSE]
    assert runner.calls.count(SPLIT) == 2

    result = read_json(run_dir / REVERT_ARTIFACT)
    assert result["paths"] == [SENTINEL]
    assert result["permitted"] is True
    assert result["exit_code"] != 0
    assert SENTINEL in read_json(run_dir / RECORD)["modified"]


def test_the_omission_is_reported_before_the_ownership_check_sees_the_path(
    make_target, harness_root,
):
    """The ordering, driven rather than read.

    The stage *creates* a file under the prefix it may not create in and does
    not record it. The ownership check reads the record, so while the path is
    missing there is nothing for it to escalate on — and what happens is the
    omission being reported. The stage then records the creation, and the
    ownership check escalates on it: the same run, the same edit, decided by
    the later check only once the record accounts for it.

    The re-entry records the path under "created", which is what the stage did
    to it: the file is on disk when the second turn runs only because the
    stage's own first turn put it there.
    """
    target_root = make_target("ownership", GOVERNING)
    created = f"{GOVERNED_PREFIX}invented_by_the_stage.txt"
    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={created: WROTE}, omits=[created]),
                 Turn(writes={created: WROTE}, records_as_created=[created])]},
        workflow=GOVERNING)

    assert causes_of(run_dir) == [CAUSE]
    assert created in self_route_records(run_dir)[0]["reason"]

    assert code == 2
    reason = reason_of(run_dir)
    assert created in reason
    assert f"{SPLIT} created {created}" in reason


def test_the_blocked_path_check_still_reads_the_completed_record(
    make_target, harness_root,
):
    """The other check downstream, on the same terms.

    A path under a repository-wide blocked prefix is not the completeness
    check's business — it subtracts them — so a stage that writes one and then
    records it walks past this check and straight into the blocked-path one,
    which escalates. That is the check reading the record after this one has
    run, driven at the record a stage wrote rather than described.
    """
    target_root = make_target("blocked-record")
    prefix = blocked_prefix_the_coordinator_does_not_write(harness_root,
                                                           target_root)
    blocked = f"{prefix}written-and-then-recorded.txt"

    code, runner, run_dir = drive(
        target_root, harness_root,
        {SPLIT: [Turn(writes={blocked: WROTE})]})

    assert code == 2
    assert self_route_records(run_dir) == []
    assert f"modified blocked path: {blocked}" in reason_of(run_dir)
