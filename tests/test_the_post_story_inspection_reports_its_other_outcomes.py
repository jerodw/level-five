"""The post-story inspection reports a refused area suggestion, every drop
reason it defines, and a record it could not write.

After a story finishes, the harness inspects the result and writes a few
lines into the run's events.log saying what it found, what it filed and what
it left out. Before story-191 three of those lines were wrong or missing:

  * a filed brief that named no area beside a refused area suggestion was
    reported on the direct filing path and never on the path every real run
    uses, where briefs are held until the run completes — the evidence that
    a suggestion was made was dropped when the briefs were held;
  * the one-line summary counted its drops by walking a list of reasons
    written by hand, so a reason the inspection module defined and the list
    did not name was counted in the total and named nowhere;
  * a record that could not be written to the history at all was reported
    as written but "no record path is tracked", which sends a reader to look
    for tracking when the problem was writing.

What this module holds the producers to, in the story's own terms:

  * the held artifact carries the refused suggestions the pre-stage
    inspection made, the completion filing reads them back, and a held brief
    naming no area beside a refused suggestion gets its "no area was named
    for" line at completion — while one beside neither a suggestion nor a
    refusal gets none, and the refusal reaches no queue entry and no count;
  * `_summary` names no drop-reason constant, counts every reason the
    inspection module defines with the clause it always used, and counts a
    reason no constant defines the same way;
  * a record that was never written is reported as not written with the
    cause, in the shape `scripts/l5-inspect` prints for the same outcome;
    a record git ignores is told so by git rather than inferred; and a
    record that was written and whose commit failed is reported as before;
  * the segment files decide whether a record was written and the text
    `_say` swallowed is only the cause: the append prints after it projects,
    so a raise on the print leaves a record on disk that is committed, and
    is not reported as missing;
  * the pre-stage inspection, which commits no record of its own, names a
    record it could not write in one events.log note, so the line missing
    from the corpus is said on the path every real run takes — and the
    segment files decide there too: where the append raised on its print
    after projecting the record, the history holds the record and no note
    names it as missing.

Every absence asserted here has its control beside it: the run that writes
no area line is shown against the run that writes one, the refusal absent
from the queue is shown against the finding present in it, the phrase absent
from one note is shown present in another, the run whose events.log carries
no "NOT written" note is shown against the run whose does, and each source
scan is shown reporting a planted violation.

The fixtures are the ones the sibling modules already build — the pre-stage
inspection's workflow for a run that holds briefs, and the post-story
inspection's target for a record commit — because what is asked here is
asked of the same runs those modules read.
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import inspect as introspection
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import agent_runner
import harness_config
import inspection
import outbox
import story_coordinator
import story_inspection
from test_a_record_commit_that_fails_is_reported import (  # noqa: F401 - shared
    HOOK_SAID,                                             # idioms and fixtures
    RAISED,
    arm,
    events_log_lines,
    history_of,
    narrow_record_paths,
    out_of_scope_run_dir,
    printed_report,
    record_lines,
    rejecting_hook,
    staged,
)
from test_an_inspection_records_what_it_cost import (  # noqa: F401 - shared
    SOURCE_DIR,
    STORY_ID,
    WORKFLOW,
    harness,
    head_subject,
    no_model,
    target,
)
from test_inspection_findings_reach_the_story import (
    FINDINGS_ARTIFACT,
    JUDGING as PRE_STAGE_JUDGING,
    PASS,
    SIBLING_FINDING,
    STORY_ID as PRE_STAGE_STORY_ID,
    WORKFLOW as PRE_STAGE_WORKFLOW,
    Inspector as PreStageInspector,
    Run,
    Runner as PreStageRunner,
    build_target as build_pre_stage_target,
    held_record,
    materialize,
)

#: The key the held artifact carries the refused suggestions under. The
#: story names it, and the artifact is the coordinator's own file with no
#: schema to read it off — so it is spelled once here and the assertion below
#: shows the writer really puts a refusal there rather than somewhere else.
REFUSED_KEY = "refused_area_suggestions"

#: What the story says a record git ignores is reported as, and what a record
#: that never reached the history is reported as beginning with. These are the
#: subject: the story asked for these words, so they are held to here.
NOT_TRACKED = "nothing staged: no record path is tracked"
NOT_WRITTEN = "the record could not be written"

#: What an Inspector says about a brief it could name no area for. A
#: distinctive sentence, so a line carrying it is traceable to the suggestion.
CONCERNS = "zzz how a held brief is filed, which no declared area covers"

#: A reason no constant in the inspection module defines, for the summary.
UNDEFINED_REASON = "zzz-a-reason-no-constant-defines"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


# ==========================================================================
# The pre-stage inspection: a refused suggestion reaches the held artifact
# ==========================================================================


class Suggesting(PreStageInspector):
    """The pre-stage fake, writing area suggestions beside the findings.

    The envelope is written by the invocation rather than planted under one,
    so what the inspection reads is a file the fake wrote exactly as a real
    invocation's would be — including an entry the reader will refuse.
    """

    def __init__(self, *args, suggestions=None, **keywords):
        super().__init__(*args, **keywords)
        self.suggestions = suggestions

    def __call__(self, prompt, **keywords):
        result = super().__call__(prompt, **keywords)
        if self.suggestions is not None:
            artifact = inspection.findings_paths(
                self.tree, harness_config.load_config(self.tree))[0]
            document = json.loads(artifact.read_text(encoding="utf-8"))
            document[inspection.AREA_SUGGESTIONS] = self.suggestions
            artifact.write_text(json.dumps(document), encoding="utf-8")
        return result


@pytest.fixture
def pre_stage_harness(tmp_path) -> Path:
    return materialize(PRE_STAGE_WORKFLOW, tmp_path / "pre-stage-harness")


def pre_stage_run(tmp_path: Path, harness_root: Path, name: str, *,
                  findings, suggestions=None, inspector=Suggesting,
                  runner=PreStageRunner) -> Run:
    """One completing run of the pre-stage fixture, with the fake installed
    in place of the guard the imported autouse fixture put there. The two
    fakes are the classes above unless a case names its own."""
    target_root = build_pre_stage_target(tmp_path / name)
    journal = tmp_path / f"{name}-journal.txt"
    inspecting = inspector(target_root, journal, findings=findings,
                           suggestions=suggestions)
    agent_runner.run_agent = inspecting
    running = runner(target_root, journal, [PASS])
    code = story_coordinator.run_story(
        PRE_STAGE_STORY_ID, harness_root, target_root, running,
        sleep=lambda _seconds: None)
    return Run(code, target_root, journal, inspecting, running)


def refused_details(envelope: dict) -> list[str]:
    """What the reader records as the detail of each refused entry in one
    envelope, through the reader itself, so the assertion below compares
    what was held against what was refused rather than against a spelling."""
    _usable, dropped = inspection._area_suggestions(envelope)
    return [one.detail for one in dropped
            if one.reason == inspection.UNUSABLE_SUGGESTION]


def a_refusal() -> list:
    """One suggestion entry the reader refuses: a slug with no concerns."""
    return [{"slug": SIBLING_FINDING["slug"]}]


def a_usable_suggestion() -> list:
    return [{"slug": SIBLING_FINDING["slug"], "concerns": CONCERNS}]


def inspection_line(run: Run) -> str:
    """The pre-stage inspection's summary line, selected by its counts."""
    said = [line for line in run.messages if "finding(s)" in line]
    assert len(said) == 1, run.messages
    return said[0]


def test_a_refused_suggestion_is_written_into_the_held_artifact(
        tmp_path, pre_stage_harness):
    """The refusal's detail, as the reader recorded it, under the key the
    story names — and the inspection's own line still counts the refusal,
    which shows a refusal really happened in this run."""
    refusal = a_refusal()
    run = pre_stage_run(tmp_path, pre_stage_harness, "refused",
                        findings=[SIBLING_FINDING], suggestions=refusal)

    assert run.code == 0
    assert run.inspector.invocations, "the inspection was never attempted"
    expected = refused_details({"findings": [SIBLING_FINDING],
                                inspection.AREA_SUGGESTIONS: refusal})
    assert expected, "the reader refused nothing, so there is nothing to hold"
    assert held_record(run)[REFUSED_KEY] == expected
    assert f"; {inspection.UNUSABLE_SUGGESTION}: 1" in inspection_line(run)


@pytest.mark.parametrize("suggestions", [None, []], ids=["none", "empty"])
def test_an_inspection_that_wrote_no_suggestion_holds_an_empty_list(
        tmp_path, pre_stage_harness, suggestions):
    """The key is present and empty, rather than absent, on an envelope
    carrying no suggestions at all and on one carrying an empty list."""
    run = pre_stage_run(tmp_path, pre_stage_harness, "none",
                        findings=[SIBLING_FINDING], suggestions=suggestions)

    assert run.code == 0
    assert REFUSED_KEY in held_record(run)
    assert held_record(run)[REFUSED_KEY] == []


def test_an_inspection_whose_suggestions_were_all_usable_holds_no_refusal(
        tmp_path, pre_stage_harness):
    """The control for the refusal above: the same run with a usable pair in
    the refused entry's place holds the pair and no refusal."""
    run = pre_stage_run(tmp_path, pre_stage_harness, "usable",
                        findings=[SIBLING_FINDING],
                        suggestions=a_usable_suggestion())

    assert run.code == 0
    record = held_record(run)
    assert record[REFUSED_KEY] == []
    assert record["area_suggestions"] == [
        {"slug": SIBLING_FINDING["slug"], "concerns": CONCERNS}]
    assert inspection.UNUSABLE_SUGGESTION not in inspection_line(run)


def test_write_held_defaults_the_refusals_to_none_and_serialises_each_as_text(
        tmp_path):
    """The writer, asked directly: omitted, the key holds an empty list; given
    drops' details, each is written as a string under the key — and the
    reader beside `held_area_suggestions` gives them back as drops under the
    refusal's reason, tolerating an absent key and a non-string entry."""
    run_dir = tmp_path / PRE_STAGE_STORY_ID
    run_dir.mkdir()
    story_inspection.write_held(run_dir, FINDINGS_ARTIFACT, 1,
                                PRE_STAGE_STORY_ID)
    path = run_dir / story_inspection.held_artifact_file(FINDINGS_ARTIFACT, 1)
    assert json.loads(path.read_text(encoding="utf-8"))[REFUSED_KEY] == []
    assert story_inspection.held_refused_area_suggestions(
        run_dir, FINDINGS_ARTIFACT, 1) == ()

    story_inspection.write_held(run_dir, FINDINGS_ARTIFACT, 2,
                                PRE_STAGE_STORY_ID,
                                refused_area_suggestions=["first", "second"])
    path = run_dir / story_inspection.held_artifact_file(FINDINGS_ARTIFACT, 2)
    assert json.loads(path.read_text(encoding="utf-8"))[REFUSED_KEY] == \
        ["first", "second"]
    assert story_inspection.held_refused_area_suggestions(
        run_dir, FINDINGS_ARTIFACT, 2) == (
            inspection.Drop(inspection.UNUSABLE_SUGGESTION, "first"),
            inspection.Drop(inspection.UNUSABLE_SUGGESTION, "second"))

    # An artifact written before the key existed, and one carrying an entry
    # that is not a string: neither raises, and the entry contributes nothing.
    path = run_dir / story_inspection.held_artifact_file(FINDINGS_ARTIFACT, 3)
    path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    assert story_inspection.held_refused_area_suggestions(
        run_dir, FINDINGS_ARTIFACT, 3) == ()
    path = run_dir / story_inspection.held_artifact_file(FINDINGS_ARTIFACT, 4)
    path.write_text(json.dumps({"findings": [], REFUSED_KEY: [7, "kept"]}),
                    encoding="utf-8")
    assert story_inspection.held_refused_area_suggestions(
        run_dir, FINDINGS_ARTIFACT, 4) == (
            inspection.Drop(inspection.UNUSABLE_SUGGESTION, "kept"),)


# ==========================================================================
# The pre-stage inspection names a record it could not write
# ==========================================================================


def run_history_dir(tree: Path) -> Path:
    """The history directory of the tree a run works in, resolved as the run
    resolves it — under the worktree since story-117, not the target."""
    return harness_config.history_dir(tree, harness_config.load_config(tree))


class BreakingTheHistory(Suggesting):
    """The pre-stage fake whose invocation leaves the history directory a
    file, so the append that records this inspection cannot make its segment.

    Done inside the invocation and not before the run, because every stage
    completion before the inspection projects a duration into the same
    segment through an append nothing guards: broken earlier, the run would
    stop at the first stage rather than reach the inspection.
    """

    def __init__(self, *args, **keywords):
        super().__init__(*args, **keywords)
        #: The directory it left a file at, as the append will name it.
        self.broken: Path | None = None

    def __call__(self, prompt, **keywords):
        result = super().__call__(prompt, **keywords)
        self.broken = run_history_dir(self.tree)
        shutil.rmtree(self.broken)
        self.broken.write_text("a file where the directory should be\n",
                               encoding="utf-8")
        return result


class RestoringTheHistory(PreStageRunner):
    """The stage runner that puts the directory back when the judging stage
    is entered — after the inspection, so what the inspection found is what
    it found, and before that stage's completion projects its own duration,
    so the run can go on to complete."""

    def __call__(self, prompt, *, stage, cwd=None, **keywords):
        if stage == PRE_STAGE_JUDGING:
            directory = run_history_dir(Path(cwd))
            assert directory.is_file(), "the inspection did not break it"
            directory.unlink()
            directory.mkdir()
        return super().__call__(prompt, stage=stage, cwd=cwd, **keywords)


def not_written_notes(run: Run) -> list[str]:
    return [line for line in run.messages
            if f"inspection record of {PRE_STAGE_STORY_ID} NOT written: "
            in line]


def test_a_pre_stage_record_that_cannot_be_written_is_named_in_events_log(
        tmp_path, pre_stage_harness):
    """The run still exits 0 and completes, the holding and the findings
    record are still written, the inspection's own line is in events.log,
    and events.log carries exactly one "NOT written" note carrying the
    history path — where the cross-run log holds no record of the
    inspection, which is what the note is naming. The control is the
    ordinary run below, whose events.log carries no such note and whose
    log holds the record."""
    run = pre_stage_run(tmp_path, pre_stage_harness, "unwritable",
                        findings=[SIBLING_FINDING],
                        inspector=BreakingTheHistory,
                        runner=RestoringTheHistory)

    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.inspector.invocations, "the inspection was never attempted"
    assert held_record(run)["findings"] == [SIBLING_FINDING]
    assert held_record(run)[REFUSED_KEY] == []
    assert run.findings_record()["ran"] is True
    assert "1 finding(s)" in inspection_line(run)

    notes = not_written_notes(run)
    assert len(notes) == 1, run.messages
    assert run.inspector.broken is not None, "the history was never broken"
    assert str(run.inspector.broken) in notes[0], notes
    assert run.history_records == [], run.history_records

    ordinary = pre_stage_run(tmp_path, pre_stage_harness, "writable",
                             findings=[SIBLING_FINDING])
    assert ordinary.code == 0
    assert not_written_notes(ordinary) == [], ordinary.messages
    assert len(ordinary.history_records) == 1, ordinary.history_records


class RaisingOnItsOwnLine(Suggesting):
    """The pre-stage fake whose invocation leaves `builtins.print` raising a
    broken pipe on the one message carrying the inspection's own summary —
    the line naming its "finding(s)" — and passing every other message
    through untouched.

    The append that records the inspection projects the record into the
    history and then prints, so the raise lands after the segment file is on
    disk: `_say` swallows it and hands back the print's text with the record
    held. Left in place after the invocation returns, because the append
    runs after it; the runner below puts the real print back.
    """

    def __init__(self, *args, **keywords):
        super().__init__(*args, **keywords)
        #: The print that was there before, so it can be put back.
        self.real_print = None
        #: The message the replacement raised on, or None if it never did.
        self.raised_on: str | None = None

    def __call__(self, prompt, **keywords):
        result = super().__call__(prompt, **keywords)
        self.real_print = builtins.print
        fake = self

        def breaking(*args, **print_keywords):
            message = " ".join(str(one) for one in args)
            if fake.raised_on is None and "finding(s)" in message:
                fake.raised_on = message
                raise BrokenPipeError(PIPE_SAID)
            return fake.real_print(*args, **print_keywords)

        builtins.print = breaking
        return result

    def restore(self) -> None:
        if self.real_print is not None:
            builtins.print = self.real_print


class RestoringThePrint(PreStageRunner):
    """The stage runner that puts the real print back when the judging stage
    is entered — after the inspection's append, whose print is the one the
    case is about, so what the inspection did is what it did."""

    def __init__(self, *args, inspector: RaisingOnItsOwnLine, **keywords):
        super().__init__(*args, **keywords)
        self.inspector = inspector

    def __call__(self, prompt, *, stage, cwd=None, **keywords):
        if stage == PRE_STAGE_JUDGING:
            assert self.inspector.raised_on is not None, \
                "the inspection's print never raised"
            self.inspector.restore()
        return super().__call__(prompt, stage=stage, cwd=cwd, **keywords)


def test_a_pre_stage_append_that_raised_after_projecting_writes_no_note(
        tmp_path, pre_stage_harness):
    """The segment files decide on the pre-stage path as they do in
    `commit_record`, and the swallowed text is only the cause: the append
    projected the record and then raised on its print, so the history holds
    the one inspection record, the inspection's line is in events.log, the
    run exits 0 and completes, and no "NOT written" note names a record the
    history holds. The control is the broken-history case above, whose note
    the same reading finds — a note decided on the swallowed text alone
    would be written here too, since the text is the same broken pipe."""
    target_root = build_pre_stage_target(tmp_path / "piped")
    journal = tmp_path / "piped-journal.txt"
    inspecting = RaisingOnItsOwnLine(target_root, journal,
                                     findings=[SIBLING_FINDING])
    agent_runner.run_agent = inspecting
    running = RestoringThePrint(target_root, journal, [PASS],
                                inspector=inspecting)
    try:
        code = story_coordinator.run_story(
            PRE_STAGE_STORY_ID, pre_stage_harness, target_root, running,
            sleep=lambda _seconds: None)
    finally:
        # Whether or not the judging stage was reached to put it back.
        inspecting.restore()
    run = Run(code, target_root, journal, inspecting, running)

    assert run.code == 0
    assert run.state["status"] == "completed"
    assert run.inspector.invocations, "the inspection was never attempted"
    assert run.inspector.raised_on is not None, "the print never raised"
    assert "1 finding(s)" in inspection_line(run)
    assert "1 finding(s)" in run.inspector.raised_on, run.inspector.raised_on
    assert len(run.history_records) == 1, run.history_records
    assert not_written_notes(run) == [], run.messages
    assert not any(PIPE_SAID in line for line in run.messages), run.messages


# ==========================================================================
# The completion filing: the refusal opens the area gate and nothing else
# ==========================================================================


@contextlib.contextmanager
def history_established(target_root: Path):
    """The run-side history directory established for a direct call, as
    `run_story` establishes it for a run, and taken down afterwards."""
    directory = harness_config.history_dir(
        target_root, harness_config.load_config(target_root))
    story_coordinator.set_history_dir(directory)
    try:
        yield directory
    finally:
        story_coordinator.set_history_dir(None)


def filing_driven(tmp_path: Path, harness_root: Path, name: str, *,
                  refused=(), suggestions=()) -> tuple[Path, Path]:
    """`file_at_completion` driven against a run directory whose final
    attempt's held artifact holds one area-less brief beside whatever
    suggestions and refusals the caller names. Returns the target and the run
    directory, named for the story so the record's segment is the story's."""
    target_root = build_pre_stage_target(tmp_path / name)
    run_dir = tmp_path / f"{name}-runs" / PRE_STAGE_STORY_ID
    run_dir.mkdir(parents=True)
    story_inspection.write_held(
        run_dir, FINDINGS_ARTIFACT, 1, PRE_STAGE_STORY_ID,
        findings=[SIBLING_FINDING],
        area_suggestions=tuple(inspection.AreaSuggestion(**one)
                               for one in suggestions),
        refused_area_suggestions=tuple(refused))
    with history_established(target_root):
        answered = story_inspection.file_at_completion(
            run_dir, target_root, harness_config.load_config(target_root),
            harness_root, PRE_STAGE_STORY_ID, PRE_STAGE_WORKFLOW["stages"])
    assert answered is None
    return target_root, run_dir


def area_lines(run_dir: Path) -> list[str]:
    return [line for line in events_log_lines(run_dir)
            if "no area was named for" in line]


def filed_line(run_dir: Path) -> str:
    said = [line for line in events_log_lines(run_dir)
            if "filed as briefs" in line]
    assert len(said) == 1, events_log_lines(run_dir)
    return said[0]


def queued_text(target_root: Path) -> str:
    """Every entry the target's queue holds, as one text to search."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in outbox.entry_files(outbox.queue_dir(target_root)))


def test_a_held_brief_naming_no_area_beside_a_refusal_is_reported_at_completion(
        tmp_path, pre_stage_harness):
    """The line the story exists to add: a "no area was named for" line for
    the held brief, written by the completion filing, with no usable
    suggestion in play — so it is the refusal that opened the gate."""
    refusal = refused_details({"findings": [SIBLING_FINDING],
                               inspection.AREA_SUGGESTIONS: a_refusal()})
    target_root, run_dir = filing_driven(tmp_path, pre_stage_harness,
                                         "refused", refused=refusal)

    lines = area_lines(run_dir)
    assert len(lines) == 1, events_log_lines(run_dir)
    assert SIBLING_FINDING["slug"] in lines[0]
    assert "the Inspector says it concerns" not in lines[0], lines[0]
    assert "1 filed as briefs" in filed_line(run_dir)


def test_a_held_brief_beside_neither_a_suggestion_nor_a_refusal_is_not_reported(
        tmp_path, pre_stage_harness):
    """A target declaring no vocabulary is reported exactly as before: no
    line, while the filing itself still happened. The control is the run
    above, identical but for the refusal, which does write the line."""
    target_root, run_dir = filing_driven(tmp_path, pre_stage_harness, "bare")

    assert area_lines(run_dir) == [], events_log_lines(run_dir)
    assert "1 filed as briefs" in filed_line(run_dir)
    assert SIBLING_FINDING["body"] in queued_text(target_root)


def test_a_usable_suggestion_still_opens_the_gate_with_its_words(
        tmp_path, pre_stage_harness):
    """The path that worked before the story is unchanged: a usable
    suggestion carried to completion gives the line the Inspector's words."""
    _target, run_dir = filing_driven(
        tmp_path, pre_stage_harness, "suggested",
        suggestions=[{"slug": SIBLING_FINDING["slug"], "concerns": CONCERNS}])

    lines = area_lines(run_dir)
    assert len(lines) == 1, events_log_lines(run_dir)
    assert CONCERNS in lines[0]


def test_a_refusal_carried_to_completion_reaches_no_queue_entry_and_no_count(
        tmp_path, pre_stage_harness):
    """The refusal reaches no brief, no payload and no outbox entry, and the
    filing's line counts it nowhere: its dropped count is the filing's own,
    and it names no unusable-suggestion clause. The control for the absence
    from the queue is the held finding's own words, which the same reading
    finds in the entry it became."""
    refusal = refused_details({"findings": [SIBLING_FINDING],
                               inspection.AREA_SUGGESTIONS: a_refusal()})
    target_root, run_dir = filing_driven(tmp_path, pre_stage_harness,
                                         "uncounted", refused=refusal)

    queued = queued_text(target_root)
    assert SIBLING_FINDING["body"] in queued
    for detail in refusal:
        assert detail not in queued, queued
    assert inspection.UNUSABLE_SUGGESTION not in queued

    line = filed_line(run_dir)
    assert "1 filed as briefs, 0 dropped" in line, line
    assert inspection.UNUSABLE_SUGGESTION not in line, line
    filed = [entry for entry in story_coordinator.load_history(run_dir)
             if entry["event"] == story_inspection.FILED_EVENT]
    assert len(filed) == 1
    assert filed[0]["dropped"] == 0


# ==========================================================================
# The summary counts its drops from the reasons the drops carry
# ==========================================================================


def drop_reason_names() -> set[str]:
    """Every constant the inspection module builds a `Drop` under, by name,
    read off the module's source: the first argument of each `Drop(...)`
    call that is a bare name. This is the vocabulary a hand-written list in
    the summary would have had to keep up with."""
    source = Path(inspection.__file__).read_text(encoding="utf-8")
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == inspection.Drop.__name__
                and node.args and isinstance(node.args[0], ast.Name)):
            found.add(node.args[0].id)
    return found


def reason_names_in(source: str, names: set[str]) -> list[str]:
    """Which of `names` a source names, as a bare name or as an attribute."""
    named = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in names:
            named.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            named.append(node.attr)
    return sorted(named)


def test_the_drop_reasons_are_read_off_the_module_and_are_strings():
    """The premise: the derivation finds the vocabulary, and each name it
    finds resolves to a string on the module."""
    names = drop_reason_names()
    assert names, "no Drop is built under a bare name"
    assert {inspection.MALFORMED, inspection.PAST_THE_CAP,
            inspection.UNUSABLE_SUGGESTION} <= {
        getattr(inspection, name) for name in names}
    for name in names:
        assert isinstance(getattr(inspection, name), str), name


def test_the_summary_names_no_drop_reason_constant():
    """The source of `_summary`, read as the subject: none of the names the
    inspection module builds drops under appears in it. The control is a
    planted source holding the hand-written list, which the same scan
    reports."""
    names = drop_reason_names()
    source = introspection.getsource(story_inspection._summary)
    assert reason_names_in(source, names) == []

    planted = ("def _summary(report):\n"
               "    reasons = [inspection.MALFORMED, PAST_THE_CAP]\n"
               "    return len(report.dropped_for(reasons[0]))\n")
    assert reason_names_in(planted, names) == ["MALFORMED", "PAST_THE_CAP"]


def summary_of(report) -> str:
    return story_inspection._summary("label", report, (), (), "")


def test_every_defined_reason_is_summarised_with_the_clause_it_always_had():
    """One drop count per defined reason, each a different figure, and the
    line carries "; <reason>: <count>" for every one of them, with the
    total agreeing."""
    names = sorted(drop_reason_names())
    dropped = tuple(
        inspection.Drop(getattr(inspection, name), f"{name} {ordinal}")
        for position, name in enumerate(names, start=1)
        for ordinal in range(position))
    line = summary_of(inspection.Report(dropped=dropped))

    for position, name in enumerate(names, start=1):
        assert f"; {getattr(inspection, name)}: {position}" in line, line
    assert f"{len(dropped)} dropped" in line, line


def test_a_reason_no_constant_defines_is_summarised_with_its_count():
    """A report whose drops carry a reason no constant defines is summarised
    with that reason and its count, where the hand-written list counted it
    in the total and named it nowhere. The control beside it is a report
    with none of that reason, whose line does not carry it."""
    dropped = (inspection.Drop(UNDEFINED_REASON, "one"),
               inspection.Drop(UNDEFINED_REASON, "two"),
               inspection.Drop(inspection.MALFORMED, "three"))
    line = summary_of(inspection.Report(dropped=dropped))

    assert f"; {UNDEFINED_REASON}: 2" in line, line
    assert f"; {inspection.MALFORMED}: 1" in line, line
    assert "3 dropped" in line, line

    without = summary_of(inspection.Report(
        dropped=(inspection.Drop(inspection.MALFORMED, "three"),)))
    assert UNDEFINED_REASON not in without, without


def test_the_reasons_are_named_once_each_in_first_seen_order():
    """Two drops under one reason are one clause with a count of two, and the
    clauses follow the order the reasons were first seen in."""
    dropped = (inspection.Drop(inspection.PAST_THE_CAP, "a"),
               inspection.Drop(inspection.MALFORMED, "b"),
               inspection.Drop(inspection.PAST_THE_CAP, "c"))
    line = summary_of(inspection.Report(dropped=dropped))

    assert line.count(f"; {inspection.PAST_THE_CAP}:") == 1, line
    assert f"; {inspection.PAST_THE_CAP}: 2" in line, line
    assert line.index(inspection.PAST_THE_CAP) < \
        line.index(inspection.MALFORMED), line


# ==========================================================================
# The record commit: not written, not tracked, and written but refused
# ==========================================================================


def broad_shape_of(recorded: inspection.Recorded) -> str:
    """The clause `scripts/l5-inspect` prints for one record outcome, after
    the subject it shares with every other outcome's line.

    Read through the script itself, so the narrow mode's note is held to
    the shape the broad report prints rather than to a spelling here: the
    subject is the longest prefix the three outcomes' lines share, and what
    follows it is the shape.
    """
    def line_for(one: inspection.Recorded) -> str:
        printed, _code = printed_report(inspection.Report(record=one), False)
        lines = record_lines(printed)
        assert len(lines) == 1, printed
        return lines[0]

    every = [line_for(inspection.Recorded(written=True, committed=True)),
             line_for(inspection.Recorded(written=True, committed=False,
                                          detail="x")),
             line_for(inspection.Recorded(written=False, committed=False,
                                          detail="x"))]
    subject = os.path.commonprefix(every)
    assert subject.endswith(" "), every
    return line_for(recorded)[len(subject):]


def test_the_broad_report_prints_the_same_three_shapes_it_printed_before():
    """The broad report is unchanged in what it says: the three outcomes are
    three lines, told apart by "NOT committed" and by "not written", each
    carrying the detail where there is one."""
    landed = broad_shape_of(inspection.Recorded(written=True, committed=True))
    refused = broad_shape_of(inspection.Recorded(
        written=True, committed=False, detail="git said no"))
    unwritten = broad_shape_of(inspection.Recorded(
        written=False, committed=False, detail="the disk said no"))

    assert "NOT" not in landed and landed.startswith("committed"), landed
    assert refused.startswith("written but NOT committed: git said no"), refused
    assert unwritten.startswith("NOT committed, because it was not written: "
                                "the disk said no"), unwritten


def expected_note(story_id: str, recorded: inspection.Recorded) -> str:
    """The one line the narrow mode's events.log gains for a record that
    was not committed, in the broad report's own shape for that outcome."""
    return f"inspection record of {story_id} {broad_shape_of(recorded)}"


def not_committed_notes(run_dir: Path) -> list[str]:
    return [line for line in events_log_lines(run_dir)
            if "NOT committed" in line]


@pytest.fixture
def recorded_commits(monkeypatch) -> list[inspection.Recorded]:
    """What every `commit_record` call in the test answered, in order — the
    producer returns nothing to its caller, so its result is read here."""
    answers: list[inspection.Recorded] = []
    original = story_inspection.commit_record

    def recording(*args, **keywords):
        recorded = original(*args, **keywords)
        answers.append(recorded)
        return recorded

    monkeypatch.setattr(story_inspection, "commit_record", recording)
    return answers


def inspection_lines(run_dir: Path) -> list[str]:
    return [line for line in events_log_lines(run_dir)
            if line.startswith(f"post-story inspection of {STORY_ID}")]


def test_a_history_directory_that_cannot_be_written_is_reported_as_not_written(
        target, harness, tmp_path, history_of, no_model, recorded_commits):
    """The inspection line is still in events.log; `commit_record` answers
    written False and committed False with a detail beginning "the record
    could not be written"; and events.log gains exactly one note in the
    not-written shape, carrying the cause and not "no record path is
    tracked". The control for that absence is the ignoring target below,
    where the same reading finds the phrase."""
    assert not history_of.exists(), history_of
    history_of.write_text("a file where the directory should be\n",
                          encoding="utf-8")
    run_dir = out_of_scope_run_dir(tmp_path)
    config = harness_config.load_config(target)
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()

    story_inspection.inspect_after_story(
        run_dir, target, config, harness, STORY_ID, WORKFLOW["stages"])

    assert no_model.calls == 0
    assert len(inspection_lines(run_dir)) == 1, events_log_lines(run_dir)
    assert len(recorded_commits) == 1
    recorded = recorded_commits[0]
    assert not recorded.written
    assert not recorded.committed
    assert recorded.detail.startswith(NOT_WRITTEN), recorded
    assert str(history_of) in recorded.detail, recorded

    notes = not_committed_notes(run_dir)
    assert len(notes) == 1, events_log_lines(run_dir)
    assert notes[0] == expected_note(STORY_ID, recorded), notes
    assert "because it was not written" in notes[0]
    assert str(history_of) in notes[0]
    assert NOT_TRACKED not in notes[0]
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before
    assert not staged(target)


def test_a_record_paths_that_raises_reaches_the_detail_and_the_note(
        target, tmp_path, monkeypatch):
    """Where `story_inspection.record_paths` raises inside `commit_record`,
    the exception's text is in the answer's detail and in the one note,
    which takes the not-written shape: nothing had established a record."""
    def raises(*_args, **_keywords):
        raise RuntimeError(RAISED)
    monkeypatch.setattr(story_inspection, "record_paths", raises)
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)

    recorded = story_inspection.commit_record(run_dir, target, config,
                                              STORY_ID)

    assert not recorded.written
    assert not recorded.committed
    assert RAISED in recorded.detail, recorded
    notes = not_committed_notes(run_dir)
    assert len(notes) == 1, events_log_lines(run_dir)
    assert notes[0] == expected_note(STORY_ID, recorded), notes
    assert RAISED in notes[0]


def test_a_record_nothing_wrote_is_reported_as_not_written_with_the_fixed_cause(
        target, tmp_path):
    """A run directory holding no record and no swallowed write failure: the
    declaration routes the kind somewhere and no segment file holds it, so
    the answer is not written, with the fixed phrase as the cause."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)
    assert narrow_record_paths(target, config) == []

    recorded = story_inspection.commit_record(run_dir, target, config,
                                              STORY_ID)

    assert not recorded.written
    assert not recorded.committed
    assert recorded.detail == \
        f"{NOT_WRITTEN}: {story_inspection.NO_SEGMENT_DETAIL}", recorded
    assert not_committed_notes(run_dir) == [expected_note(STORY_ID, recorded)]


def test_a_swallowed_write_problem_is_the_cause_where_no_segment_file_holds_the_record(
        target, tmp_path, history_of):
    """The segment files decide and the swallowed text is only the cause:
    handed the text `_say` swallowed with no segment file on disk,
    `commit_record` reports not written with that text as the cause; handed
    the same text beside a segment file, it stages and commits the record
    it found — the append projects the record before it prints, so a raise
    after the projection leaves a record that is there to commit."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)
    assert narrow_record_paths(target, config) == []

    unwritten = story_inspection.commit_record(
        run_dir, target, config, STORY_ID, write_problem=RAISED)

    assert not unwritten.written
    assert not unwritten.committed
    assert unwritten.detail == f"{NOT_WRITTEN}: {RAISED}", unwritten
    assert not_committed_notes(run_dir) == [expected_note(STORY_ID, unwritten)]

    assert story_inspection._say(run_dir, "an earlier line", findings=0,
                                 filed=0, dropped=0, scope_files=0,
                                 invocations=0, dedupe_ran=False) == ""
    assert narrow_record_paths(target, config), "no record was written"

    landed = story_inspection.commit_record(
        run_dir, target, config, STORY_ID, write_problem=RAISED)

    assert landed == inspection.Recorded(written=True, committed=True)
    assert head_subject(target) == \
        story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID)
    assert not_committed_notes(run_dir) == [expected_note(STORY_ID, unwritten)]
    assert not staged(target)


#: What the print at the end of the append raises in the driven case below:
#: a reader gone away, which is what a run whose output is piped meets.
PIPE_SAID = "[Errno 32] Broken pipe"


def test_a_raise_after_the_projection_leaves_a_record_that_is_committed(
        target, tmp_path, history_of, monkeypatch):
    """The driven case: on a writable history the append projects the
    record and then raises on its print, so `_say` returns the print's text
    while the segment file is on disk. The commit stages what it finds —
    committed, with no NOT-committed note and nothing left in the target's
    tree — rather than reading the swallowed text as a record never made,
    which staged nothing and left the history dirty for the next run's
    pre-flight to refuse on."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)
    assert narrow_record_paths(target, config) == []

    def broken(*_args, **_keywords):
        raise BrokenPipeError(PIPE_SAID)

    with monkeypatch.context() as patched:
        patched.setattr(builtins, "print", broken)
        problem = story_inspection._say(
            run_dir, "the record line", findings=0, filed=0, dropped=0,
            scope_files=0, invocations=0, dedupe_ran=False)

    assert problem == PIPE_SAID, problem
    assert narrow_record_paths(target, config), "the projection wrote nothing"
    assert events_log_lines(run_dir) == ["the record line"]

    recorded = story_inspection.commit_record(
        run_dir, target, config, STORY_ID, write_problem=problem)

    assert recorded == inspection.Recorded(written=True, committed=True)
    assert head_subject(target) == \
        story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID)
    assert not_committed_notes(run_dir) == [], events_log_lines(run_dir)
    assert events_log_lines(run_dir) == ["the record line"]
    assert not staged(target)
    assert _git(target, "status", "--porcelain").stdout == ""


def test_say_returns_the_swallowed_exceptions_text_and_nothing_on_success(
        target, tmp_path, history_of):
    """The seam the cause travels through: "" where the append returned, and
    the exception's text where the guard swallowed one."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    assert story_inspection._say(run_dir, "fine", findings=0, filed=0,
                                 dropped=0, scope_files=0, invocations=0,
                                 dedupe_ran=False) == ""
    assert history_of.is_dir(), "the first append wrote no history"

    # The history taken away and a file put where the directory was, so the
    # next append's segment cannot be made.
    shutil.rmtree(history_of)
    history_of.write_text("a file where the directory should be\n",
                          encoding="utf-8")
    problem = story_inspection._say(run_dir, "not fine", findings=0, filed=0,
                                    dropped=0, scope_files=0, invocations=0,
                                    dedupe_ran=False)
    assert problem, "the guard swallowed nothing"
    assert str(history_of) in problem, problem
    # The line itself reached events.log both times: the failure is the
    # history's and not the log's.
    assert events_log_lines(run_dir) == ["fine", "not fine"]


def ignoring_the_history(target_root: Path) -> None:
    """The target's own ignore rules keeping the history directory out of
    version control, committed so the tree the record commit finds is clean."""
    directory = harness_config.history_dir(
        target_root, harness_config.load_config(target_root))
    with (target_root / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write(f"{directory.relative_to(target_root).as_posix()}/\n")
    _git(target_root, "add", "--", ".gitignore")
    _git(target_root, "commit", "-q", "-m", "ignore the history")


def test_a_target_ignoring_its_history_is_told_no_record_path_is_tracked(
        target, harness, tmp_path, history_of, no_model, recorded_commits):
    """Written True, committed False, the not-tracked detail rather than one
    beginning "git add failed", no commit gained, and the one note in the
    written-but-not-committed shape — which is also the control showing the
    phrase the not-written note above was asserted not to carry is one this
    reading finds where it is."""
    ignoring_the_history(target)
    run_dir = out_of_scope_run_dir(tmp_path)
    config = harness_config.load_config(target)
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()

    story_inspection.inspect_after_story(
        run_dir, target, config, harness, STORY_ID, WORKFLOW["stages"])

    assert no_model.calls == 0
    assert narrow_record_paths(target, config), "no record was written"
    assert len(recorded_commits) == 1
    recorded = recorded_commits[0]
    assert recorded.written
    assert not recorded.committed
    assert recorded.detail == NOT_TRACKED, recorded
    assert not recorded.detail.startswith("git add failed")
    assert inspection.NOT_TRACKED_DETAIL == NOT_TRACKED

    notes = not_committed_notes(run_dir)
    assert len(notes) == 1, events_log_lines(run_dir)
    assert notes[0] == expected_note(STORY_ID, recorded), notes
    assert NOT_TRACKED in notes[0]
    assert "because it was not written" not in notes[0]
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before
    assert story_inspection.COMMIT_SUBJECT.format(story_id=STORY_ID) not in \
        _git(target, "log", "--format=%s").stdout


def test_the_shared_commit_asks_git_before_staging_and_commits_a_tracked_path(
        target, tmp_path):
    """`commit_record_paths` directly: an ignored path answers the not-tracked
    detail with no add attempted, while the same call over a path git does
    not ignore commits it — so the answer is git's and not a file's absence.
    The empty-paths answer claims nothing about tracking."""
    ignoring_the_history(target)
    config = harness_config.load_config(target)
    ignored = harness_config.history_segment(
        harness_config.history_dir(target, config), STORY_ID) / "record.jsonl"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("{}\n", encoding="utf-8")
    tracked = target / SOURCE_DIR / "record.py"
    tracked.write_text("def record():\n    pass\n", encoding="utf-8")
    relative_ignored = str(ignored.relative_to(target))
    relative_tracked = str(tracked.relative_to(target))
    head_before = _git(target, "rev-parse", "HEAD").stdout.strip()

    assert inspection.ignored_paths(
        target, [relative_ignored, relative_tracked]) == {relative_ignored}

    refused = inspection.commit_record_paths(target, [relative_ignored],
                                             "the record commit")
    assert refused == inspection.Recorded(written=True, committed=False,
                                          detail=NOT_TRACKED)
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == head_before
    assert not staged(target)

    landed = inspection.commit_record_paths(target, [relative_tracked],
                                            "the record commit")
    assert landed == inspection.Recorded(written=True, committed=True)
    assert head_subject(target) == "the record commit"

    none = inspection.commit_record_paths(target, [], "the record commit")
    assert none.written and not none.committed
    assert "tracked" not in none.detail, none
    assert none.detail == inspection.NO_PATH_DETAIL


def test_a_record_that_was_written_and_refused_is_reported_as_before(
        target, harness, arm, tmp_path, history_of, no_model,
        recorded_commits):
    """Written True, committed False, git's words in the detail, and the one
    note in the written-but-not-committed shape carrying the hook's words."""
    rejecting_hook(target, arm)
    arm.write_text("armed\n", encoding="utf-8")
    run_dir = out_of_scope_run_dir(tmp_path)
    config = harness_config.load_config(target)

    story_inspection.inspect_after_story(
        run_dir, target, config, harness, STORY_ID, WORKFLOW["stages"])

    assert no_model.calls == 0
    assert len(recorded_commits) == 1
    recorded = recorded_commits[0]
    assert recorded.written
    assert not recorded.committed
    assert HOOK_SAID in recorded.detail, recorded

    notes = not_committed_notes(run_dir)
    assert len(notes) == 1, events_log_lines(run_dir)
    assert notes[0] == expected_note(STORY_ID, recorded), notes
    assert "written but NOT committed" in notes[0]
    assert HOOK_SAID in notes[0]
    assert not staged(target, *narrow_record_paths(target, config))


def test_a_landed_commit_adds_no_note(target, tmp_path, history_of):
    """The control for every note counted above: a record that was written
    and committed leaves events.log with the record line and nothing about
    the commit."""
    run_dir = tmp_path / STORY_ID
    run_dir.mkdir()
    config = harness_config.load_config(target)
    assert story_inspection._say(run_dir, "the record line", findings=0,
                                 filed=0, dropped=0, scope_files=0,
                                 invocations=0, dedupe_ran=False) == ""

    recorded = story_inspection.commit_record(run_dir, target, config,
                                              STORY_ID)

    assert recorded == inspection.Recorded(written=True, committed=True)
    assert events_log_lines(run_dir) == ["the record line"]


# ==========================================================================
# Every committing call site hands the write problem over
# ==========================================================================


def calls_to_commit_record(source: str) -> list[ast.Call]:
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == story_inspection.commit_record.__name__]


def calls_not_threading_the_problem(source: str) -> list[int]:
    """Line numbers of `commit_record` calls that pass no `write_problem`."""
    return [call.lineno for call in calls_to_commit_record(source)
            if not any(keyword.arg == "write_problem"
                       for keyword in call.keywords)]


def test_every_committing_call_site_threads_what_say_returned():
    """Every `commit_record` call in the module passes `write_problem`, so
    no path can commit a record without being told whether it was written.
    The control is a planted call that omits it."""
    source = Path(story_inspection.__file__).read_text(encoding="utf-8")
    assert calls_to_commit_record(source), "the scan found no call at all"
    assert calls_not_threading_the_problem(source) == []

    planted = ("def after(run_dir, target_root, config, story_id):\n"
               "    problem = _say(run_dir, 'line')\n"
               "    commit_record(run_dir, target_root, config, story_id)\n")
    assert calls_not_threading_the_problem(planted) == [3]


def test_record_paths_raises_rather_than_answering_nothing(
        target, monkeypatch):
    """The guard that turned a raise into no paths is gone: a raise from the
    derivation reaches the caller, where `commit_record`'s own guard puts its
    text in the detail."""
    def raises(*_args, **_keywords):
        raise RuntimeError(RAISED)
    monkeypatch.setattr(inspection, "record_paths", raises)

    with pytest.raises(RuntimeError, match=RAISED):
        story_inspection.record_paths(
            target, harness_config.load_config(target), STORY_ID)
