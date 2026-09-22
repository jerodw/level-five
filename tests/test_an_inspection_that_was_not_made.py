"""story-198 validation: an inspection that produced no report is told apart
from one that reported nothing.

When a story's work is done the harness asks an independent reviewer to read
the change and say what it found. Sometimes that reviewer's process dies before
it writes anything, and the run read that silence as "looked, found nothing":
it said so in the run's log, handed the stage that judges the change a record
claiming the inspection ran and was clean, and moved on. Every other agent the
harness runs is already treated differently — a stage whose process dies is
simply run again — so the inspection is made once more, and only a second
silence is recorded as one.

Beside it, the loss that made a dead reviewer cost findings rather than merely
a report. A run that is interrupted and resumed moves everything the
interrupted attempt wrote into an archive directory, and the filing at the end
of a run looked only at the current attempt, so findings raised before the
interruption — including ones a verifier looked at and reasonably declined,
which are supposed to become backlog items — never reached the backlog. The
filing now reads those archived entries too.

The subjects are kept apart deliberately:

  * **the retry, and its bound.** Driven at a fake runner whose invocations
    deliver or do not on a schedule this module sets, so what is observed is
    the number of invocations actually made rather than a count inferred from
    what came back. One that produced no artifact is made again; one that
    produced a report is made once whatever the report says; a scope whose
    every invocation produces nothing is made exactly twice and no more.

  * **what the trigger is, and what it is not.** Shown three ways, because a
    retry that fired on a count or on a figure would pass every assertion
    about the one that fires on the missing file. An envelope carrying an
    empty findings array is a real answer and is invoked once; a costly
    invocation that delivered is invoked once; and the branch that decides is
    read as source and required to be decided by the document alone.

  * **saying it could not be made.** The run's own summary line, the findings
    record the judging stage reads, and the boolean on the cross-run record —
    three statements about one inspection, each driven rather than reasoned
    about, and each shown beside the same run whose invocation delivered.

  * **the announcement.** The second invocation is announced before it is
    made, in both narrow modes, through the announcer the first one uses.

  * **the earlier entries.** The filing at completion is driven as a function
    over artifacts this module wrote, so what an entry holds and where it
    holds it are inputs rather than the outcome of a run that had to be
    interrupted. The end-to-end case — a real run escalated, resumed and
    completed — is
    `tests/test_one_defect_found_on_several_attempts_is_filed_once.py`'s.

  * **the guarantee.** None of it may stop a run: an inspection that never
    delivers still completes its run, and every entry point in the module
    still returns nothing on every path and raises on none.

Every absence asserted here carries a demonstration that it can fail:

  * "a report that reported nothing is invoked once" sits beside the identical
    drive whose invocation wrote no file, which is invoked twice;
  * "no drop is recorded when the second invocation delivered" sits beside the
    drive where it did not, which records one;
  * "the retry branch reads nothing but the document" sits beside a planted
    source whose branch also reads the reported cost, which the same scan
    reports;
  * "the summary states no findings tally" sits beside the same run whose
    invocation delivered, where the same line carries one;
  * "the findings record says the inspection did not run" sits beside the same
    run that delivered, whose record says it did;
  * "no archive path is spelled in the producing module" sits beside a planted
    source that spells it, which the same scan reports;
  * "a finding found in two entries is filed once" sits beside the same two
    entries carrying different defects, where both are filed;
  * "the line carries no earlier-entry clause" sits beside the run with an
    earlier entry, where it does;
  * "the entry points return nothing and raise nothing" sits beside a planted
    entry point that returns a value and raises, which the same scan reports.

Nothing here reaches a model: every invocation goes through a fake this module
installed, over the autouse guard the fixtures it borrows already install.
Nothing here resolves this repository's own commit graph: every repository read
is one a fixture built under `tmp_path`, and every workflow, stage name and
artifact name is derived from a definition a fixture wrote rather than from the
one this repository ships.
"""
from __future__ import annotations

import ast
import inspect as introspection
import json
import textwrap
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import outbox
import story_coordinator
import story_inspection

import test_inspection_findings_reach_the_story as prestage
from test_inspection import (  # noqa: F401 - the broad-mode drive and its fake
    MAX_COST_USD,
    OTHER_SOURCE_DIR,
    SOURCE_DIR,
    brief,
    configuration,
    inspecting,
    writes,
    writes_nothing,
    writes_text,
)
from test_an_inspection_records_what_it_cost import (  # noqa: F401 - the
    FIRST_COST,                                       # narrow-mode run, its
    SECOND_COST,                                      # fake and its records
    STORY_ID,
    Inspector,
    Runner,
    harness,
    messages,
    no_model,
    one_record,
    problems_with,
    routed_logs,
    run,
    run_dir_of,
    target,
)
from test_a_failed_dedupe_is_visible import summary_of  # noqa: F401

#: What an invocation that reported a large figure reported. Large enough that
#: a retry decided on spend rather than on the missing file would behave
#: differently here than at `FIRST_COST`, and distinct from every figure the
#: borrowed fixture defines so that a record carrying it is traceable here.
A_LARGE_COST = 7.53


# ==========================================================================
# The fakes: an invocation that does not deliver
#
# Both are the borrowed module's own fake with one thing added — the findings
# artifact removed again after the invocation wrote it, on the invocations the
# caller says did not deliver. Removing it afterwards rather than declining to
# write it is deliberate: what an invocation delivers is a file on disk, so the
# case under test is exactly "the reader found nothing there", reached through
# the same write path every other test in the suite drives.
# ==========================================================================


def delivers_on(schedule, index: int) -> bool:
    """Whether the `index`-th invocation of a schedule delivered.

    The last entry stands for every invocation past it, so a schedule of
    `(False,)` is a fake that never delivers however many times it is asked
    and `(False, True)` is one that delivers on its second.
    """
    return bool(schedule[min(index, len(schedule) - 1)])


class SometimesDelivers(Inspector):
    """The post-story fake, delivering on the schedule it was given."""

    def __init__(self, *args, delivers=(True,), **keywords):
        super().__init__(*args, **keywords)
        self.delivers = tuple(delivers)

    def __call__(self, prompt, **keywords):
        index = len(self.invocations)
        answered = super().__call__(prompt, **keywords)
        if not delivers_on(self.delivers, index):
            self.artifact.unlink(missing_ok=True)
        return answered


class SometimesDeliversToTheStage(prestage.Inspector):
    """The pre-stage fake, on the same schedule.

    Its artifact is resolved from the tree the invocation was made in rather
    than from the target, because a run works in a worktree of its own and
    that is where the invocation wrote.
    """

    def __init__(self, *args, delivers=(True,), **keywords):
        super().__init__(*args, **keywords)
        self.delivers = tuple(delivers)

    def __call__(self, prompt, **keywords):
        index = len(self.invocations)
        answered = super().__call__(prompt, **keywords)
        if not delivers_on(self.delivers, index):
            config = harness_config.load_config(self.tree)
            inspection.findings_paths(self.tree, config)[0].unlink(
                missing_ok=True)
        return answered


def after_the_story(target_root: Path, harness_root: Path, monkeypatch, *,
                    delivers, findings=(), costs=(FIRST_COST,)):
    """One completing run whose post-story inspection is on that schedule.

    Returns `(code, inspector)`.
    """
    config = harness_config.load_config(target_root)
    inspector = SometimesDelivers(target_root, config, findings=list(findings),
                                  costs=list(costs), delivers=delivers)
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    return run(target_root, harness_root, Runner(target_root)), inspector


def before_the_stage(target_root: Path, harness_root: Path, tmp_path: Path, *,
                     delivers, findings=(), name="not-made-journal"):
    """One completing run whose pre-stage inspection is on that schedule.

    The fake is put in place of the guard the autouse fixture installed, which
    is the idiom the borrowed module's own driver uses and for its reason: the
    fixture patched the attribute, so the original is what is restored when
    the test ends whatever this leaves there.
    """
    journal = tmp_path / f"{name}.txt"
    inspector = SometimesDeliversToTheStage(
        target_root, journal, findings=list(findings), delivers=delivers)
    agent_runner.run_agent = inspector
    stage_runner = prestage.Runner(target_root, journal, [prestage.PASS])
    code = story_coordinator.run_story(
        prestage.STORY_ID, harness_root, target_root, stage_runner,
        sleep=lambda _seconds: None)
    return prestage.Run(code, target_root, journal, inspector, stage_runner)


def broadly(target_root: Path, harness_root: Path, *, delivers, findings=()):
    """One whole broad-mode inspection on that schedule.

    The mode no run makes and no events.log records, driven here only for what
    it writes to the cross-run log: it is a second writer of that record, not
    the same one the narrow modes share, so what the narrow cases observe of
    the record says nothing about this one. The runner is handed over
    explicitly, as every drive of this mode in the suite hands one over, so
    nothing here can fall through to the real one.

    Returns `(report, inspector)`.
    """
    config = harness_config.load_config(target_root)
    inspector = SometimesDelivers(target_root, config, findings=list(findings),
                                  delivers=delivers)
    report = inspection.inspect(target_root, config, harness_root,
                                runner=inspector)
    return report, inspector


@pytest.fixture
def stage_harness(tmp_path) -> Path:
    """The pre-stage fixture's harness, under a name of its own so that it does
    not collide with the post-story fixture's beside it."""
    return prestage.materialize(prestage.WORKFLOW,
                                tmp_path / "not-made-stage-harness")


@pytest.fixture
def stage_target(tmp_path) -> Path:
    return prestage.build_target(tmp_path / "not-made-stage-target")


# ==========================================================================
# The retry, and its bound
# ==========================================================================


def wrote_nothing_then(*findings: dict):
    """An invocation that writes no file, and a second that writes these."""
    def act(artifact: Path, index: int) -> None:
        if index:
            writes(*findings)(artifact, index)
    return act


def wrote_nothing_then_unreadable(text: str):
    """An invocation that writes no file, and a second that writes `text`.

    Two invocations failing in two different ways, so a drop detail that
    carries both is carrying two distinguishable facts rather than one
    sentence twice.
    """
    def act(artifact: Path, index: int) -> None:
        if index:
            writes_text(text)(artifact, index)
    return act


def test_a_first_invocation_that_produced_no_artifact_is_made_once_more(
        tmp_path):
    """And what the second found is filed, with no drop recorded for the
    first.

    The whole of the story's first criterion but the record, which is read in
    its own case below: the second invocation's envelope is the answer, so the
    finding reaches the queue and nothing says the inspection produced no
    artifact. The control for that silence is the case beneath this one, where
    the identical drive with no second envelope does record it.
    """
    found = inspecting(tmp_path, act=wrote_nothing_then(brief()),
                       name="the-second-delivered")

    assert len(found.invocations) == 2
    assert found.report.invocations == 2
    assert found.filed_slugs == [brief()["slug"]]
    assert found.dropped(inspection.NO_ARTIFACT) == (), found.report.dropped
    assert [entry["payload"]["slug"] for entry in found.entries] == \
        [brief()["slug"]]


def test_two_invocations_that_produced_nothing_record_one_drop_naming_both(
        tmp_path):
    """One drop, not two, and its detail names what each invocation failed at.

    The two invocations fail differently — one writes no file at all, the
    other writes something no reader can make a document of — so a detail
    carrying both is carrying both readings rather than one of them twice.
    """
    unreadable = "the findings, in prose"
    found = inspecting(tmp_path,
                       act=wrote_nothing_then_unreadable(unreadable),
                       name="neither-delivered")

    assert len(found.invocations) == 2
    assert found.report.invocations == 2
    assert found.report.filed == ()
    dropped = found.dropped(inspection.NO_ARTIFACT)
    assert len(dropped) == 1, found.report.dropped
    assert "wrote no findings file" in dropped[0].detail, dropped
    assert "not JSON" in dropped[0].detail, dropped


@pytest.mark.parametrize("described, act", [
    ("an envelope carrying nothing", writes()),
    ("an envelope carrying a finding", writes(brief())),
], ids=["reported nothing", "reported a finding"])
def test_an_invocation_that_produced_a_report_is_made_exactly_once(
        described, act, tmp_path):
    """A report is a real answer whatever it says, so it is never retried.

    The empty envelope is the case the whole distinction rests on: an
    Inspector that looked and found nothing has answered, and an inspection
    that asked it twice would be paying a second invocation for every clean
    change the harness reviews. The control is the same drive against an
    invocation that wrote no file, which is made twice.
    """
    found = inspecting(tmp_path, act=act, name="reported-something")
    assert len(found.invocations) == 1
    assert found.report.invocations == 1
    assert found.dropped(inspection.NO_ARTIFACT) == (), found.report.dropped

    control = inspecting(tmp_path, act=writes_nothing, name="reported-nothing")
    assert len(control.invocations) == 2
    assert control.report.invocations == 2


def test_a_scope_whose_every_invocation_produces_nothing_is_made_twice(
        tmp_path):
    """At most one retry, per invocation and not per inspection.

    Two scopes, neither of which ever delivers: four invocations and not six,
    not three, and not a loop. The count is read at the runner and again off
    the report, because a report that had stopped counting would agree with
    any number the runner recorded.
    """
    found = inspecting(tmp_path, act=writes_nothing,
                       arguments=(SOURCE_DIR, OTHER_SOURCE_DIR),
                       name="neither-scope-delivered")

    assert len(found.invocations) == 4
    assert found.report.invocations == 4
    assert len(found.dropped(inspection.NO_ARTIFACT)) == 2, \
        found.report.dropped


def test_the_second_invocation_is_handed_its_own_allowance(tmp_path):
    """The ceiling is declared per invocation, so the retry gets one of its
    own rather than what the first invocation left of it — which is the shape
    a stage running again in place already has. Observed at the runner, where
    the allowance is handed over, rather than read off the configuration."""
    found = inspecting(tmp_path, act=writes_nothing, name="both-allowances")

    assert [one.max_budget_usd for one in found.invocations] == \
        [MAX_COST_USD, MAX_COST_USD]


# ==========================================================================
# What the trigger is, and what it is not
# ==========================================================================


def test_no_retry_is_made_on_an_invocation_that_reported_a_large_cost(
        target, harness, monkeypatch):
    """The cost stays corroboration in the record and never something the
    routing reads: an invocation that delivered is made once whatever it
    reported spending. The control is the case beneath it, where an invocation
    reporting the same figure and delivering nothing is made again — so the
    figure is not what suppressed the retry here."""
    code, inspector = after_the_story(
        target, harness, monkeypatch, delivers=(True,),
        findings=[], costs=(A_LARGE_COST,))

    assert code == 0
    assert len(inspector.invocations) == 1
    assert one_record(target)["invocations"] == 1


def test_a_retry_is_made_however_much_the_first_invocation_reported(
        target, harness, monkeypatch):
    """The control for the case above, and the criterion in its own right: the
    same large figure, and an invocation that produced no file, is made again.
    The record carries both invocations' figures summed, which is what lets a
    reader of the log afterwards see that two were made."""
    code, inspector = after_the_story(
        target, harness, monkeypatch, delivers=(False, True),
        findings=[], costs=(A_LARGE_COST, SECOND_COST))

    assert code == 0
    assert len(inspector.invocations) == 2
    record = one_record(target)
    assert record["invocations"] == 2
    assert record["cost_usd"] == pytest.approx(A_LARGE_COST + SECOND_COST)
    assert problems_with(record) == []


def test_a_retry_neither_invocation_reported_a_cost_for_records_none(
        target, harness, monkeypatch):
    """Summed on the terms the single figure was carried on. Two invocations
    that each reported nothing leave the field off the record rather than
    recording a zero, because a zero is what an invocation that genuinely cost
    nothing would report and the two must not average together later. The
    control is the case above, where the field is there."""
    code, inspector = after_the_story(
        target, harness, monkeypatch, delivers=(False, False), costs=(None,))

    assert code == 0
    assert len(inspector.invocations) == 2
    record = one_record(target)
    assert "cost_usd" not in record, record
    assert record["invocations"] == 2


def callee(call: ast.Call) -> str:
    """The name a call names, bare or as an attribute, or "" for neither."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def retry_decision(source: str) -> tuple[set[str], str]:
    """What the branch that invokes a second time reads, and what the first
    invocation's document was bound to.

    The invocation is located rather than spelled: it is the statement of the
    function's own body that unpacks a call whose callee is also called inside
    a branch — which is what a retry is, one thing called twice with the second
    call guarded. The names the guard reads are then everything it could be
    deciding on, and a numeric literal in it is reported as a name would be,
    because a threshold is exactly the shape a retry decided on a count or a
    figure would take.
    """
    tree = ast.parse(textwrap.dedent(source))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef))
    guarded = {callee(node) for branch in ast.walk(function)
               if isinstance(branch, ast.If)
               for node in ast.walk(branch) if isinstance(node, ast.Call)}
    invocation = next(
        node for node in function.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        and isinstance(node.targets[0], ast.Tuple)
        and callee(node.value) in guarded)
    document = invocation.targets[0].elts[0].id

    read: set[str] = set()
    for branch in ast.walk(function):
        if not isinstance(branch, ast.If):
            continue
        if not any(isinstance(node, ast.Call)
                   and callee(node) == callee(invocation.value)
                   for node in ast.walk(branch)):
            continue
        for node in ast.walk(branch.test):
            if isinstance(node, ast.Name):
                read.add(node.id)
            elif isinstance(node, ast.Attribute):
                read.add(node.attr)
            elif isinstance(node, ast.Constant) and node.value is not None:
                read.add(repr(node.value))
    return read, document


def test_the_branch_that_invokes_again_is_decided_by_the_document_alone():
    """The absence of the findings artifact and nothing else.

    Read as source beside the three behavioural cases above, because those
    show that today's two figures do not move the decision while this shows
    that no figure can: a branch that also read a count or a cost would pass
    every one of them for whatever inputs happened to be driven, and is
    reported here.
    """
    read, document = retry_decision(
        introspection.getsource(inspection.inspect_scope))
    assert read == {document}, read


def test_that_scan_reports_a_branch_that_also_reads_what_it_cost():
    """The control for the absence above."""
    planted = textwrap.dedent("""
        def inspect_scope(scope, runner):
            document, problem, cost = _one_invocation(scope, runner)
            if document is None or cost > 0.5:
                document, problem, cost = _one_invocation(scope, runner)
            return document
    """)
    read, document = retry_decision(planted)
    assert document == "document", document
    assert read == {"document", "cost", "0.5"}, read


# ==========================================================================
# Saying it could not be made
# ==========================================================================


def test_the_runs_line_says_the_inspection_could_not_be_made_and_why(
        target, harness, monkeypatch):
    """The summary of a run whose invocations produced no report.

    What it must not do is report a count of findings: zero findings is what an
    inspection that looked and found nothing reports, and the two being one
    line in the run's own log is half of what let a dead reviewer's findings be
    lost. The reason each invocation failed at is on the line, so a developer
    reading it afterwards knows what happened rather than only that something
    did.
    """
    code, inspector = after_the_story(target, harness, monkeypatch,
                                      delivers=(False, False))

    assert code == 0
    assert len(inspector.invocations) == 2
    said = summary_of(target)
    assert "could not be made" in said, said
    assert "finding(s)" not in said, said
    assert inspection.NO_ARTIFACT in said, said
    assert "wrote no findings file" in said, said


def test_a_run_whose_inspection_found_nothing_says_what_it_always_said(
        target, harness, monkeypatch):
    """The control for the wording above, and the criterion that an inspection
    which produced a report is untouched: an empty report is a real answer, so
    the line is the tally it has always been and the record beside it carries
    the same cost, the same invocation count and the same counts."""
    code, inspector = after_the_story(target, harness, monkeypatch,
                                      delivers=(True,), findings=[])

    assert code == 0
    assert len(inspector.invocations) == 1
    said = summary_of(target)
    assert "0 finding(s), 0 filed, 0 dropped" in said, said
    assert "could not be made" not in said, said

    record = one_record(target)
    assert record["findings"] == 0
    assert record["filed"] == 0
    assert record["dropped"] == 0
    assert record["invocations"] == 1
    assert record["cost_usd"] == pytest.approx(FIRST_COST)
    assert problems_with(record) == []


@pytest.mark.parametrize("delivers, made", [
    ((True,), True),
    ((False, True), True),
    ((False, False), False),
], ids=["delivered", "delivered on the retry", "never delivered"])
def test_the_cross_run_record_of_every_inspection_says_whether_it_was_made(
        delivers, made, target, harness, monkeypatch):
    """The boolean is written on every record, true and false alike.

    Written on every one for the reason the dedupe verdict beside it is: a
    boolean has a false to say, and absence would mean either that or a line
    written before the field existed — so a record nobody can query for the
    runs where the inspection *was* made is a record nobody can query at all.
    The three cases are driven together because what the field must separate
    is a retry that delivered from one that did not, and one reading of one of
    them says nothing about that.
    """
    code, _ = after_the_story(target, harness, monkeypatch, delivers=delivers)

    assert code == 0
    record = one_record(target)
    assert record["inspection_made"] is made, record
    assert problems_with(record) == []


@pytest.mark.parametrize("delivers, made", [
    ((True,), True),
    ((False,), False),
], ids=["delivered", "never delivered"])
def test_the_broad_modes_record_says_whether_its_inspection_was_made(
        delivers, made, target, harness):
    """Every inspection's record, which is the criterion, includes the mode no
    run makes.

    The record of a broad inspection is written by a writer of its own rather
    than by the one the two narrow modes share, so a field threaded through
    that one alone would leave this log's broad lines carrying nothing — and a
    reader asking the log which inspections were made would get an answer that
    silently omits every l5-inspect. The invocation count is read off the same
    record beside it, because the count and the boolean are the two figures
    that corroborate each other: a record saying an inspection was not made
    and claiming one invocation is a record one of whose halves is wrong.

    The bound is re-observed here rather than assumed from the narrow cases:
    the number of invocations is derived from the scopes this target produced,
    so what is asserted is one per scope where they delivered and two where
    none did, whatever the configuration makes of this target.
    """
    report, inspector = broadly(target, harness, delivers=delivers)

    assert report.scopes, "the broad inspection covered no scope"
    assert len(inspector.invocations) == len(report.scopes) * (1 if made else 2)
    record = one_record(target)
    assert record["mode"] == inspection.MODE_BROAD, record
    assert record["inspection_made"] is made, record
    assert record["invocations"] == len(inspector.invocations), record
    assert problems_with(record) == [], record


@pytest.mark.parametrize("delivers, made", [
    ((True,), True),
    ((False, True), True),
    ((False, False), False),
], ids=["delivered", "delivered on the retry", "never delivered"])
def test_the_pre_stage_modes_record_says_whether_its_inspection_was_made(
        delivers, made, stage_target, stage_harness, tmp_path):
    """The other narrow mode's record, driven rather than reasoned about.

    The two narrow modes reach one writer today, and that is exactly why this
    is driven: what each mode hands that writer is the mode's own business, so
    a mode that passed nothing would write a record with the field absent and
    pass every assertion made about the mode beside it. Asserted over every
    inspection record the run wrote rather than over one picked out of them,
    because "on every record" is the thing being claimed.
    """
    driven = before_the_stage(stage_target, stage_harness, tmp_path,
                              delivers=delivers,
                              findings=[prestage.OWN_FINDING],
                              name=f"recorded-{'-'.join(map(str, delivers))}")

    assert driven.code == 0
    written = driven.history_records
    assert written, "the run wrote no inspection record"
    assert [one.get("inspection_made") for one in written] == \
        [made] * len(written), written
    for record in written:
        assert problems_with(record) == [], record


def test_the_declaration_declares_the_field_and_says_what_a_false_records(
        target, harness, monkeypatch):
    """A shipped declaration, which is the legitimate subject here: the claim
    is about what this harness declares a record to be.

    The description has to carry what a reader cannot get from the value. A
    false is not "the change is clean" and not "nothing was found" — it is
    nothing looked, which is exactly the reading a column of zero findings
    invited — so the words that separate it from an inspection that found
    nothing are required to be there. The control beneath is the same record
    with the field carrying a string, which the same declaration reports, so
    the acceptance above is a shape being checked rather than a validator
    that passes everything.
    """
    declared = [declaration["properties"]["inspection_made"]
                for declaration in routed_logs().values()
                if "inspection_made" in declaration.get("properties", {})]
    assert declared, "no log this record reaches declares the field"
    for field in declared:
        assert field["type"] == "boolean", field
        assert "found nothing" in field.get("description", ""), field
        assert "every record" in field.get("description", ""), field

    assert after_the_story(target, harness, monkeypatch,
                           delivers=(True,))[0] == 0
    record = one_record(target)
    assert problems_with(record) == [], record
    reported = problems_with({**record, "inspection_made": "no"})
    assert reported, "the declaration accepts a field of the wrong type"
    assert any("inspection_made" in problem for problem in reported), reported


def test_the_stage_that_judges_is_told_the_inspection_was_not_made(
        stage_target, stage_harness, tmp_path):
    """The record the judging stage reads, on the run where no invocation
    delivered.

    `ran` true with an empty findings list is the shape that says the
    Inspector looked and found nothing, and handing that to the stage is the
    other half of what was lost: the verifier is already told how to read a
    false, and nothing ever wrote one. So the record says it did not run, and
    says why. The control is the run beneath it, whose invocation delivered.
    """
    driven = before_the_stage(stage_target, stage_harness, tmp_path,
                              delivers=(False, False),
                              findings=[prestage.OWN_FINDING])

    assert driven.code == 0
    assert len(driven.inspector.invocations) == 2
    record = driven.findings_record(1)
    assert record["ran"] is False, record
    assert record["reason"], record
    assert record["findings"] == [], record


def test_the_stage_that_judges_is_told_what_the_inspector_found(
        stage_target, stage_harness, tmp_path):
    """The control for the record above: the identical run whose invocation
    produced a report writes `ran` true and the finding, which is what it
    wrote before this story and what it must go on writing."""
    driven = before_the_stage(stage_target, stage_harness, tmp_path,
                              delivers=(True,),
                              findings=[prestage.OWN_FINDING])

    assert driven.code == 0
    assert len(driven.inspector.invocations) == 1
    record = driven.findings_record(1)
    assert record["ran"] is True, record
    assert record["findings"] == [prestage.OWN_FINDING], record


# ==========================================================================
# The announcement made before the second invocation
# ==========================================================================


#: The kind the coordinator appends an announcement under. An event kind is
#: the coordinator's own vocabulary rather than a name a workflow declares, so
#: it is spelled here as every module reading one of the other announcements
#: spells theirs. That the retry goes out under *this* kind and not a new one
#: is half of what the cases below assert: a sibling kind for the retry would
#: leave the count at one here, and a second definition of one event free to
#: drift from the first in what it says the wait is worth.
ANNOUNCEMENT = "inspection-started"


def announcements(run_dir: Path) -> list[dict]:
    return [one for one in story_coordinator.load_history(run_dir)
            if one["event"] == ANNOUNCEMENT]


def test_the_post_story_retry_is_announced_before_it_is_made(
        target, harness, monkeypatch, tmp_path):
    """A retry that said nothing would be a second silence of a stage's length
    where the reader has just been told the first one is the inspection — the
    reading-as-a-hang the announcement exists against, met twice. So the
    second invocation is announced the way a stage being re-run is, before it
    is made, and the line says why it is being made again.

    The control is the delivering run beneath it, which announces once.
    """
    code, inspector = after_the_story(target, harness, monkeypatch,
                                      delivers=(False, True))

    assert code == 0
    assert len(inspector.invocations) == 2
    said = announcements(run_dir_of(target))
    assert len(said) == 2, said
    assert "again" in said[1]["message"], said
    assert "produced no findings file" in said[1]["message"], said
    assert said[1]["sequence"] > said[0]["sequence"], said
    # Before the invocation it announces, which is what makes it an
    # announcement rather than a report: the summary the inspection writes
    # when it is over comes after both.
    completed = [one for one in story_coordinator.load_history(
        run_dir_of(target)) if one["event"] == inspection.INSPECTION_EVENT]
    assert completed[0]["sequence"] > said[1]["sequence"], completed


def test_a_post_story_inspection_that_delivered_announces_once(
        target, harness, monkeypatch):
    """The control for the count above: the announcement is per invocation
    made, so an inspection made once says so once."""
    code, _ = after_the_story(target, harness, monkeypatch, delivers=(True,))

    assert code == 0
    assert len(announcements(run_dir_of(target))) == 1


def test_the_pre_stage_retry_is_announced_before_it_is_made(
        stage_target, stage_harness, tmp_path):
    """The same of the other narrow mode, driven rather than inferred from the
    two sharing a call: both modes hand the inspection the same announcing
    seam, and a mode that stopped handing one over would announce nothing and
    pass every assertion made about the mode beside it."""
    driven = before_the_stage(stage_target, stage_harness, tmp_path,
                              delivers=(False, True),
                              findings=[prestage.OWN_FINDING])

    assert driven.code == 0
    assert len(driven.inspector.invocations) == 2
    said = announcements(driven.run_dir)
    assert len(said) == 2, said
    assert "again" in said[1]["message"], said


def test_a_pre_stage_inspection_that_delivered_announces_once(
        stage_target, stage_harness, tmp_path):
    """The control for the count above, under its own target: one invocation
    made is one announcement, so the two above are the retry rather than a
    mode that announces twice whatever happens."""
    driven = before_the_stage(stage_target, stage_harness, tmp_path,
                              delivers=(True,),
                              findings=[prestage.OWN_FINDING],
                              name="announced-once")

    assert driven.code == 0
    assert len(announcements(driven.run_dir)) == 1


# ==========================================================================
# The earlier entries of a run, at its completion
#
# Driven as a function over artifacts written here, because what an entry
# holds and where it holds it are the inputs to this behaviour rather than
# its outcome: reaching them by interrupting a real run would make every
# assertion below a fact about how the coordinator resumes.
# ==========================================================================


#: The artifact the filing reads, taken off the fixture workflow's own
#: declaration rather than written here, so a definition that named it
#: differently is followed rather than contradicted.
ARTIFACT = story_coordinator.inspection_declaration(
    prestage.WORKFLOW["stages"])["result"]


def a_finding(slug: str, marker: str, *, paths=None) -> dict:
    return prestage.finding(slug, list(paths or [prestage.SIBLING_FILE]),
                            marker)


def entry_holding(directory: Path, attempt: int, *, held=(), remainder=(),
                  acted_on=(), suggestions=()):
    """Write one attempt's three artifacts into `directory`.

    The same three functions that write them in a run, so what is read back
    cannot drift from what a run would have left: the held findings, the
    findings record the routed ones were written into, and the accounting
    whose remainder slugs say which of those the verdict left.

    `acted_on` are the routed findings that attempt's verdict repaired rather
    than left, so they are in the findings record and in the accounting's
    routed and acted-on lists and out of its remainder — which is the shape a
    verdict that acted on one leaves, and what tells a defect this run fixed
    from one it declined. `suggestions` are the Inspector's area suggestions,
    which travel with the held findings for the line that says a filed brief
    named no area.
    """
    routed = list(remainder) + list(acted_on)
    story_inspection.write_held(directory, ARTIFACT, attempt,
                                prestage.STORY_ID, findings=list(held),
                                area_suggestions=list(suggestions))
    story_inspection.write_findings(directory, ARTIFACT, attempt,
                                    prestage.STORY_ID, ran=True,
                                    findings=routed)
    story_inspection.write_accounting(directory, ARTIFACT, attempt,
                                      prestage.STORY_ID,
                                      routed=routed, acted_on=list(acted_on),
                                      remainder=list(remainder))


class Completion:
    """One drive of the filing at completion, and what an assertion reads."""

    def __init__(self, target_root: Path, harness_root: Path):
        self.target = Path(target_root)
        self.harness = Path(harness_root)
        self.run_dir = conftest.run_dir_for(self.target, prestage.STORY_ID)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def entry(self, number: int) -> Path:
        """An earlier entry's directory, named by the coordinator's own
        function so that nothing here spells the archive path."""
        directory = story_coordinator.entry_dir(self.run_dir, number)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def file(self) -> None:
        config = harness_config.load_config(self.target)
        # Where the cross-run projection writes, pointed at this target the
        # way a run points it at its own. It is a module global a run sets,
        # so a drive that did not set it would either write nothing or write
        # into whichever target ran last — and what this case observes would
        # then depend on what ran before it.
        story_coordinator.set_history_dir(
            harness_config.history_dir(self.target, config))
        story_inspection.file_at_completion(
            self.run_dir, self.target, config, self.harness,
            prestage.STORY_ID, prestage.WORKFLOW["stages"])

    @property
    def events(self) -> list[dict]:
        return [one for one in story_coordinator.load_history(self.run_dir)
                if one["event"] == story_inspection.FILED_EVENT]

    @property
    def line(self) -> str:
        said = self.events
        assert len(said) == 1, said
        return said[0]["message"]

    @property
    def area_notes(self) -> list[str]:
        """The lines saying a filed brief named no area.

        Selected on what the line says rather than on the kind it carries,
        because `_note` writes every one of the filing's asides under the one
        generic kind and this case is about only these.
        """
        return [one["message"]
                for one in story_coordinator.load_history(self.run_dir)
                if "no area was named for" in one.get("message", "")]

    @property
    def queued(self) -> list[dict]:
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in outbox.entry_files(outbox.queue_dir(self.target))]

    @property
    def slugs(self) -> list[str]:
        return sorted(entry["payload"]["slug"] for entry in self.queued)

    @property
    def titles(self) -> list[str]:
        return sorted(entry["payload"]["title"] for entry in self.queued)


@pytest.fixture
def completing(tmp_path, stage_harness):
    """A factory, so one case can hold a subject and its control side by
    side under targets that share nothing."""
    made: list[Completion] = []

    def make(name: str) -> Completion:
        made.append(Completion(
            prestage.build_target(tmp_path / f"completing-{name}"),
            stage_harness))
        return made[-1]
    return make


THIS_ENTRY_HELD = a_finding("zzz-this-entry-held",
                            "MARKER-HELD what this entry held")
THIS_ENTRY_LEFT = a_finding("zzz-this-entry-left",
                            "MARKER-LEFT what this entry's verdict left")
EARLIER_HELD = a_finding("zzz-earlier-held",
                         "MARKER-EARLIER-HELD what the earlier entry held")
EARLIER_LEFT = a_finding("zzz-earlier-left",
                         "MARKER-EARLIER-LEFT what its verdict left")
SUPERSEDED = a_finding("zzz-superseded",
                       "MARKER-SUPERSEDED what an earlier attempt of it held")


def test_the_filing_reads_the_earlier_entries_of_the_run(completing):
    """The criterion: an interrupted entry's final reading is filed too.

    Everything the interrupted entry wrote was moved into that entry's own
    directory and the filing looked only at the current one, so a finding a
    verifier had looked at and reasonably declined before the interruption
    reached the backlog from nowhere — its copy was sitting in the archive
    with nothing to go and get it. Both what that entry held and what its
    verdict left are filed, and the line says how many came from there, so
    the recovery is visible rather than silent.
    """
    completion = completing("with-an-earlier-entry")
    entry_holding(completion.run_dir, 1,
                  held=[THIS_ENTRY_HELD], remainder=[THIS_ENTRY_LEFT])
    entry_holding(completion.entry(1), 2,
                  held=[EARLIER_HELD], remainder=[EARLIER_LEFT])

    completion.file()

    assert completion.slugs == sorted(one["slug"] for one in (
        THIS_ENTRY_HELD, THIS_ENTRY_LEFT, EARLIER_HELD, EARLIER_LEFT))
    assert "from earlier entries of this run: 2" in completion.line, \
        completion.line
    assert "4 filed as briefs" in completion.line, completion.line


def test_only_each_entrys_own_final_attempt_is_filed(completing):
    """An entry contributes the attempt whose inspection read the tree it
    ended on, and no earlier reading of it — the rule the run already applies
    to its current entry, applied within an archived one.

    The control for that absence is the attempt beside it in the same
    directory, whose findings the same reading does file.
    """
    completion = completing("only-the-final-attempt")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1, held=[SUPERSEDED])
    entry_holding(completion.entry(1), 2, held=[EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    assert SUPERSEDED["slug"] not in completion.slugs
    assert "from earlier entries of this run: 1" in completion.line, \
        completion.line


def test_a_defect_found_in_two_entries_is_filed_once_under_this_ones_wording(
        completing):
    """A defect found on both sides of an interruption is one defect.

    Deduped by the key the queue would file each under, so what counts as the
    same defect here is exactly what the queue counts as one — a second notion
    of sameness would file a duplicate the queue could not collapse. The
    current entry's reading wins, because it is the reading of the tree that
    shipped; and what came from earlier entries is what survived the dedupe,
    so the line reports what the recovery added rather than what it read.
    """
    completion = completing("the-same-defect-twice")
    rephrased = {**THIS_ENTRY_HELD,
                 "title": "zzz: MARKER-EARLIER the same defect, said before",
                 "body": "MARKER-EARLIER the same defect, said before"}
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1, held=[rephrased])

    completion.file()

    assert completion.slugs == [THIS_ENTRY_HELD["slug"]]
    assert completion.titles == [THIS_ENTRY_HELD["title"]]
    assert "earlier entries" not in completion.line, completion.line


def test_two_entries_carrying_two_defects_file_both(completing):
    """The control for the collapse above: nothing about reading two entries
    makes one of them disappear, so the identical drive with two different
    defects files two."""
    completion = completing("two-defects-twice")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1, held=[EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    assert "from earlier entries of this run: 1" in completion.line, \
        completion.line


#: What the Inspector said each area-less brief concerns, in the words of the
#: entry whose inspection wrote it. Distinct per entry, so a line carrying one
#: of them says which entry's reading it came from rather than merely that a
#: suggestion reached the report.
THIS_ENTRY_CONCERNS = "MARKER-CONCERNS-THIS what this entry's reading was about"
EARLIER_CONCERNS = "MARKER-CONCERNS-EARLIER what the earlier reading was about"


def suggestion(finding: dict, concerns: str):
    return inspection.AreaSuggestion(slug=finding["slug"], concerns=concerns)


def test_a_recovered_brief_carries_its_own_entrys_area_suggestion(completing):
    """A brief recovered from an earlier entry is reported with the suggestion
    that entry's inspection wrote for it.

    The suggestions are matched to a filed brief by slug, and the suggestion
    for a slug only the earlier entry's inspection saw is only in that entry's
    held artifact — so a report assembled from the current entry alone found
    nothing for the recovered brief and dropped the clause saying what the
    Inspector thought it concerns, which is the whole of what that line is
    worth to a person deciding where the work belongs. Each brief's line now
    carries its own entry's words, and the control is the other line beside
    it: two entries, two suggestions, neither line carrying the other's.
    """
    completion = completing("each-entrys-own-suggestion")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD],
                  suggestions=[suggestion(THIS_ENTRY_HELD,
                                          THIS_ENTRY_CONCERNS)])
    entry_holding(completion.entry(1), 1, held=[EARLIER_HELD],
                  suggestions=[suggestion(EARLIER_HELD, EARLIER_CONCERNS)])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    said = {line.split("no area was named for ")[1].split(";")[0]: line
            for line in completion.area_notes}
    assert sorted(said) == sorted([THIS_ENTRY_HELD["slug"],
                                   EARLIER_HELD["slug"]]), completion.area_notes
    assert THIS_ENTRY_CONCERNS in said[THIS_ENTRY_HELD["slug"]]
    assert EARLIER_CONCERNS not in said[THIS_ENTRY_HELD["slug"]]
    assert EARLIER_CONCERNS in said[EARLIER_HELD["slug"]]
    assert THIS_ENTRY_CONCERNS not in said[EARLIER_HELD["slug"]]


def test_a_defect_both_entries_saw_is_reported_with_this_entrys_words(
        completing):
    """The suggestion follows the wording, on a defect both entries raised.

    The dedupe keeps the current entry's copy of a defect found on both sides
    of an interruption, because that is the reading of the tree that shipped.
    A report that let an earlier entry's suggestion win for the same slug
    would file this entry's brief and say it concerns what the *earlier*
    reading thought — one brief describing two readings, and no way for a
    person reading the line to tell which half they are looking at. So the two
    halves agree, and the control is the second brief beside it, which only
    the earlier entry saw and which does carry that entry's words.
    """
    completion = completing("one-defect-two-readings")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD],
                  suggestions=[suggestion(THIS_ENTRY_HELD,
                                          THIS_ENTRY_CONCERNS)])
    entry_holding(completion.entry(1), 1,
                  held=[THIS_ENTRY_HELD, EARLIER_HELD],
                  suggestions=[suggestion(THIS_ENTRY_HELD, EARLIER_CONCERNS),
                               suggestion(EARLIER_HELD, EARLIER_CONCERNS)])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    said = {line.split("no area was named for ")[1].split(";")[0]: line
            for line in completion.area_notes}
    assert sorted(said) == sorted([THIS_ENTRY_HELD["slug"],
                                   EARLIER_HELD["slug"]]), completion.area_notes
    assert THIS_ENTRY_CONCERNS in said[THIS_ENTRY_HELD["slug"]]
    assert EARLIER_CONCERNS not in said[THIS_ENTRY_HELD["slug"]]
    assert EARLIER_CONCERNS in said[EARLIER_HELD["slug"]]


def test_a_run_whose_current_entry_holds_nothing_still_says_it_named_no_area(
        completing):
    """A run resumed past the stage that inspects holds nothing in its current
    entry, and the briefs it recovers are still reported as naming no area.

    The gate that keeps a target declaring no area vocabulary silent reads the
    suggestions and the refusals the inspection wrote, and with the current
    entry holding neither artifact there is no attempt there to read them off
    — so a report built from the current entry alone was empty, the gate
    closed, and every recovered brief went unreported. The evidence is the
    earlier entry's, which is where the inspection that found these briefs
    wrote it.
    """
    completion = completing("nothing-in-the-current-entry")
    entry_holding(completion.entry(1), 1, held=[EARLIER_HELD, EARLIER_LEFT],
                  suggestions=[suggestion(EARLIER_HELD, EARLIER_CONCERNS)])

    completion.file()

    assert completion.slugs == sorted([EARLIER_HELD["slug"],
                                       EARLIER_LEFT["slug"]])
    assert sorted(
        line.split("no area was named for ")[1].split(";")[0]
        for line in completion.area_notes
    ) == sorted([EARLIER_HELD["slug"], EARLIER_LEFT["slug"]]), \
        completion.area_notes
    assert any(EARLIER_CONCERNS in line for line in completion.area_notes)


def test_a_target_declaring_no_areas_is_reported_as_it_was(completing):
    """The control for both cases above: the gate is unchanged.

    The identical drive with no suggestion written by any entry — which is
    what an inspection of a target that declares no area vocabulary leaves —
    says nothing about any of the briefs it files, so what the two cases above
    observe is evidence reaching the gate rather than the gate having been
    opened for everyone.
    """
    completion = completing("no-vocabulary-at-all")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1, held=[EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    assert completion.area_notes == []


def test_an_earlier_entrys_finding_this_run_repaired_is_not_filed(completing):
    """A defect an earlier entry raised and a later entry of the same run
    repaired is not filed as a brief.

    The recovery deduplicates an earlier entry's findings against what the
    current entry holds or was left with, and the remainder by construction
    excludes the routed findings the verdict acted on — so a finding declined
    before an interruption and repaired after it was in neither of the current
    entry's lists, nothing collapsed the earlier copy, and the backlog gained
    an item describing code as it no longer is. The queue's own dedupe cannot
    catch it either: it asks the tracker and the local queue about a brief
    that was never filed.

    The control is the finding beside it in the same entry, which this run
    never answered and which is still recovered and filed — so what the drop
    turns on is the current entry having acted on that slug, not the recovery
    having stopped reading the entry.
    """
    completion = completing("repaired-after-the-interruption")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD],
                  acted_on=[EARLIER_LEFT])
    entry_holding(completion.entry(1), 1,
                  remainder=[EARLIER_LEFT, EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    assert EARLIER_LEFT["slug"] not in completion.slugs
    # And not counted either: what the line reports is what the recovery added.
    assert "from earlier entries of this run: 1" in completion.line, \
        completion.line
    assert "2 filed as briefs" in completion.line, completion.line


def test_the_same_finding_is_filed_where_this_run_repaired_nothing(
        completing):
    """The control for the drop above: the identical drive whose current entry
    acted on nothing recovers and files that same finding.

    So the drop turns on the slug the accounting records as acted on, and not
    on anything about the finding itself or about where it was read from.
    """
    completion = completing("repaired-nothing")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1,
                  remainder=[EARLIER_LEFT, EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_LEFT["slug"],
                                       EARLIER_HELD["slug"]])
    assert "from earlier entries of this run: 2" in completion.line, \
        completion.line


def test_a_repair_this_entry_recorded_before_it_was_retried_still_drops_it(
        completing):
    """What an entry repaired is what any of its attempts repaired, not what
    its last one did.

    The accounting artifact is written once per attempt, so each attempt of an
    entry carries its own list of the findings that attempt's verdict acted on.
    A verdict that acts on a finding advances no attempt counter — a correction
    or a repair pass spends no retry — so an entry can record a repair on one
    attempt and then be retried into a later one whose verdict acted on
    nothing. Reading the highest attempt alone finds no repair there, the
    earlier entry's copy of the defect survives the dedupe, and a brief is
    filed describing code this same run already fixed. Nothing downstream
    collapses it: the queue's own dedupe asks about a brief that was never
    filed.

    The drive is that shape at the run root — the repair recorded on attempt 1,
    the entry then retried into attempt 2 which acted on nothing — and the
    control is the case below it, identical but for the repair, which files the
    finding. Beside them stand the single-attempt drives above, where the
    acted-on attempt is also the last.
    """
    completion = completing("repaired-then-retried")
    entry_holding(completion.run_dir, 1, acted_on=[EARLIER_LEFT])
    entry_holding(completion.run_dir, 2, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1,
                  remainder=[EARLIER_LEFT, EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    assert EARLIER_LEFT["slug"] not in completion.slugs
    assert "from earlier entries of this run: 1" in completion.line, \
        completion.line
    assert "2 filed as briefs" in completion.line, completion.line


def test_the_same_finding_is_filed_where_the_retried_entry_repaired_nothing(
        completing):
    """The control for the drop above: the identical drive whose first attempt
    acted on nothing recovers and files that same finding.

    So what the drop turns on is a repair recorded on an attempt that is not
    the entry's last, and not on the entry having more than one attempt.
    """
    completion = completing("retried-having-repaired-nothing")
    entry_holding(completion.run_dir, 1)
    entry_holding(completion.run_dir, 2, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(1), 1,
                  remainder=[EARLIER_LEFT, EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_LEFT["slug"],
                                       EARLIER_HELD["slug"]])
    assert "from earlier entries of this run: 2" in completion.line, \
        completion.line


def test_a_repair_an_earlier_entry_recorded_before_its_retry_still_drops_it(
        completing):
    """The same rule inside the walk of the earlier entries.

    An archived entry has attempts for the same reason the current one does,
    and the one that recorded the repair is as likely not to be its last. The
    newer archived entry here repaired the defect on its first attempt and was
    retried into a second that acted on nothing; the older entry's verdict had
    left that defect, and filing its copy would file a brief for work this run
    has already done.

    The control is the case below it, which is this drive with the repair
    removed and which files the finding — so the drop turns on the repair
    recorded on the non-final attempt rather than on the older entry having
    been read differently.
    """
    completion = completing("an-earlier-entry-repaired-then-retried")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(2), 1, acted_on=[EARLIER_LEFT])
    entry_holding(completion.entry(2), 2)
    entry_holding(completion.entry(1), 1,
                  remainder=[EARLIER_LEFT, EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_HELD["slug"]])
    assert EARLIER_LEFT["slug"] not in completion.slugs
    assert "from earlier entries of this run: 1" in completion.line, \
        completion.line


def test_the_same_finding_is_filed_where_the_earlier_entry_repaired_nothing(
        completing):
    """The control for the drop above: the identical drive whose newer archived
    entry acted on nothing on either attempt files that finding from the older
    entry, and counts it."""
    completion = completing("an-earlier-entry-retried-having-repaired-nothing")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(2), 1)
    entry_holding(completion.entry(2), 2)
    entry_holding(completion.entry(1), 1,
                  remainder=[EARLIER_LEFT, EARLIER_HELD])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_LEFT["slug"],
                                       EARLIER_HELD["slug"]])
    assert "from earlier entries of this run: 2" in completion.line, \
        completion.line


def test_a_finding_repaired_before_an_entry_found_it_again_is_still_filed(
        completing):
    """The direction is what keeps the drop from being too eager.

    A defect one entry repaired and a *later* entry's inspection found again
    is that later entry's finding: the repair did not hold, and the entry that
    raised it afterwards read a tree the repair was already in. So an entry's
    findings are filtered by what a newer entry acted on and never by what an
    older one did, and this drive — the oldest entry acting on the slug its
    successor's verdict then left — files it.
    """
    completion = completing("repaired-then-found-again")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    entry_holding(completion.entry(2), 1, remainder=[EARLIER_LEFT])
    entry_holding(completion.entry(1), 1, acted_on=[EARLIER_LEFT])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       EARLIER_LEFT["slug"]])


def test_with_no_earlier_entry_the_filing_and_its_line_are_what_they_were(
        completing):
    """A run that was never interrupted has no archive to read, and files and
    says exactly what it filed and said before this story: the same two
    findings, and the line in the shape it has always had with no clause
    about entries that do not exist.

    The control for the absence of that clause is every case above, where the
    same reading of the same line finds it.
    """
    completion = completing("never-interrupted")
    entry_holding(completion.run_dir, 1,
                  held=[THIS_ENTRY_HELD], remainder=[THIS_ENTRY_LEFT])

    completion.file()

    assert completion.slugs == sorted([THIS_ENTRY_HELD["slug"],
                                       THIS_ENTRY_LEFT["slug"]])
    assert completion.line == (
        f"filing at the completion of {prestage.STORY_ID}: 1 held and 1 left "
        f"by the verdict on attempt 1; 2 filed as briefs, 0 dropped")


def test_an_earlier_entry_that_cannot_be_read_costs_the_recovery_and_no_more(
        completing):
    """The filing is total, and an archive it cannot read is not the run's
    failure: the current entry's findings are filed, one event is written and
    nothing is raised."""
    completion = completing("an-unreadable-entry")
    entry_holding(completion.run_dir, 1, held=[THIS_ENTRY_HELD])
    (completion.entry(1) / story_inspection.held_artifact_file(
        ARTIFACT, 1)).write_text("not a record", encoding="utf-8")

    completion.file()

    assert completion.slugs == [THIS_ENTRY_HELD["slug"]]
    assert "could not run" not in completion.line, completion.line


def literal_strings(source: str) -> list[str]:
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def archive_name_parts() -> list[str]:
    """The literal parts of an entry directory's path, taken off the function
    that composes it: the directory the entries sit in, and the prefix each
    entry's own name carries."""
    named = story_coordinator.entry_dir(Path("run"), 7)
    return [named.parent.name, named.name.replace("7", "")]


def spellings_of_the_archive_in(source: str) -> list[str]:
    """Which parts of the archive path a source writes out as a literal."""
    parts = archive_name_parts()
    return sorted(one for one in literal_strings(source)
                  if any(part and part in one for part in parts))


def test_the_archive_path_is_spelled_in_the_coordinator_and_nowhere_else():
    """The filing reaches the archived entries through the coordinator's own
    naming rather than by composing that path a second time.

    Two spellings of one directory are two things to keep in step, and the one
    that is only read would go on reading the old place silently. The control
    is a planted source that spells it, which the same scan reports.
    """
    source = Path(story_inspection.__file__).read_text(encoding="utf-8")
    assert all(archive_name_parts()), archive_name_parts()
    assert spellings_of_the_archive_in(source) == [], \
        spellings_of_the_archive_in(source)


def test_that_scan_reports_a_module_that_spells_the_archive_path():
    """The control for the absence above."""
    directory, prefix = archive_name_parts()
    planted = (f'def entries(run_dir):\n'
               f'    return (run_dir / "{directory}").glob("{prefix}*")\n')
    assert spellings_of_the_archive_in(planted) == sorted(
        [directory, f"{prefix}*"])


# ==========================================================================
# The guarantee: none of it can stop a run
# ==========================================================================


def test_a_run_whose_inspection_never_delivers_still_completes(
        target, harness, monkeypatch):
    """Two invocations that produced nothing, and the run completes with the
    status it has when its inspection produces a report. The inspection is a
    mechanism whose whole purpose is to be helpful, and it must not become the
    thing that stops the work."""
    code, inspector = after_the_story(target, harness, monkeypatch,
                                      delivers=(False, False))

    assert code == 0
    assert len(inspector.invocations) == 2
    state = json.loads((run_dir_of(target) / "state.json").read_text(
        encoding="utf-8"))
    assert state["status"] == "completed"


def entry_points() -> list[str]:
    """The module's guarded entry points, by name.

    Derived from the shape every one of them has rather than listed: a public
    function whose work is delegated to a private sibling of the same name is
    the guard-and-body pair this module uses for everything a run calls into
    it, so one added later is held to the same rule with no edit here.
    """
    names = {name for name, _ in
             introspection.getmembers(story_inspection,
                                      introspection.isfunction)}
    return sorted(name for name in names
                  if not name.startswith("_") and f"_{name}" in names)


def totality_problems(source: str) -> list[str]:
    """What stops a function being total: a return carrying a value, or a
    raise."""
    found: list[str] = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Return) and node.value is not None:
            found.append(f"returns a value at line {node.lineno}")
        elif isinstance(node, ast.Raise):
            found.append(f"raises at line {node.lineno}")
    return found


def test_every_entry_point_returns_nothing_and_raises_on_no_path():
    """The guarantee this story had to leave intact, asserted of the whole set
    rather than of the two it touched: a caller that could be handed a value
    is a caller that could route on it, and one that could be raised through
    is one whose failure becomes the run's."""
    names = entry_points()
    assert len(names) >= 4, names
    for name in names:
        source = introspection.getsource(getattr(story_inspection, name))
        assert totality_problems(source) == [], (name, source)


def test_that_scan_reports_an_entry_point_that_answers_and_one_that_raises():
    """The control for the absence above."""
    planted = ("def inspect_after_story(run_dir):\n"
               "    if not run_dir:\n"
               "        raise ValueError(run_dir)\n"
               "    return True\n")
    assert len(totality_problems(planted)) == 2, totality_problems(planted)
