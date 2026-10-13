"""story-222 validation: the approval shows the gaps a plan leaves.

A plan now records each gap it leaves — a problem its planning found and the
developer chose not to fix in this story — in a list of its own, gaps_left,
each entry carrying the developer's reason. What the developer approves is what
they read first, so when `l5-plan` asks whether they approve the plan it prints
each of those entries, whole and one to a line, beneath the line naming that
artifact's story and above the question. An artifact leaving no gap prints
what it printed before this story.

Written from the story's acceptance criteria rather than from the
implementation, at three altitudes:

  * **the reader** — `story_coordinator.gaps_left` — is driven directly against
    parses this module constructs, because it is a function over a mapping:
    what it answers for a parse it cannot read, and that what it answers is
    each entry whole on one line.
  * **the question** — `approve` in `scripts/l5-plan` — is called in process
    with a stream that claims to be a terminal and keeps what had been printed
    at the moment it was read, so "the question is the last thing printed
    before the answer is read" is observed at the read rather than inferred
    from an ordering of lines afterwards.
  * **the session** is driven end to end: the real `scripts/l5-plan` run as a
    subprocess against the throwaway planning repository and stub `claude`
    that `tests/test_plan_commit.py` builds, the reply read from a terminal the
    way `tests/test_a_run_says_which_story_it_is.py` drives it, and the lines
    read with that module's printed-line readers. The fixtures and readers are
    imported rather than rebuilt.

Every absence asserted here carries a demonstration that it can fail:

  * "a parse the reader cannot read answers with no gaps" sits beside the same
    call on a list of strings, which answers with each entry;
  * "an artifact leaving no gap prints nothing between its line and the next"
    sits beside the same artifact carrying a gap, where that gap's line is
    printed there;
  * "nothing was stamped before the answer" is a rejected session compared by
    the artifact's own bytes, beside the same fixture approved, where the
    mandate is stamped and the plan committed;
  * "an artifact whose gaps cannot be read shows none" sits beside the
    validation that follows refusing it, naming the fault;
  * "the gaps and the title come from the one reading" sits beside the same
    reading of a copy of the script whose gaps are read a second time, which
    it reports.

No model is invoked anywhere here, nothing here runs the suite, and nothing
here resolves this repository's history.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

import conftest
import plan_mandate
import story_coordinator

from test_plan_commit import (
    Planning,
    bare_remote,
    kept_worktree,
    make_planning,
    run_plan,
    writes,
)
from test_story_mandate import PLANNED_ID, PLANNED_REL, session_writing
#: The printed-line readers, the declining runner and the titled artifacts,
#: taken from the module whose subject is the line the gaps are printed
#: beneath rather than written a second time here.
from test_a_run_says_which_story_it_is import (
    TITLE,
    decline_plan,
    printed_lines,
    report_source,
    subject_line,
    titled,
)
#: A stream that claims to be a terminal and answers with a reply, from the
#: module whose subject is the approval itself.
from test_the_approval_is_observed import Answering

#: The field a plan records each gap it leaves in, read from where the
#: coordinator spells it once rather than written here.
GAPS_LEFT = story_coordinator.GAPS_LEFT

#: The words the question is recognised by, as the modules beside this one
#: recognise it.
QUESTION = "approve this plan?"

#: Gaps a plan might leave, each the gap and the developer's reason. The
#: second runs past the bound a title is cut at, so "printed whole" is a claim
#: the display could fail; neither carries a colon, which the dialect would
#: read as a key.
SHORT_GAP = ("the tracker scripts keep their old wording (it lies in a "
             "different part of the system)")
LONG_GAP = ("a gap whose account runs well past the bound a story title is "
            "cut at when it is printed beside its id, so a display that cut "
            "it would show less than the developer approves (it waits on a "
            "decision the developer chooses not to make now)")
GAPS = (SHORT_GAP, LONG_GAP)

#: A second story the session writes, which leaves no gap.
SECOND_ID = "story-901"
SECOND_REL = f".harness/stories/{SECOND_ID}.yaml"
SECOND_TITLE = "Zarquon the second planned story"


def leaving(text: str, *gaps: str) -> str:
    """An artifact's text with a gaps_left list holding `gaps` after it."""
    return text + f"\n{GAPS_LEFT}:\n" + "".join(f"  - {gap}\n" for gap in gaps)


def test_the_gaps_this_module_plans_with_are_what_it_expects_printed():
    """Load-bearing: the long gap must exceed the bound a title is cut at, and
    every gap must parse back as the entry it was written as, or the cases
    below assert nothing about printing an entry whole."""
    assert len(LONG_GAP) > story_coordinator.PRINTED_TITLE_MAX_LENGTH
    reading = story_coordinator.read_story(
        leaving(titled(TITLE), *GAPS) + conftest.MANDATE_BLOCK)
    assert reading.problems == []
    assert reading.parsed[GAPS_LEFT] == list(GAPS)


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """The throwaway planning repository with a bare origin to push to, so an
    approved session gets as far as the commit its control needs."""
    made = make_planning(tmp_path)
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


# ==========================================================================
# 1. The reader, driven against parses this module constructs
# ==========================================================================


@pytest.mark.parametrize("story", [
    pytest.param(None, id="no parse at all"),
    pytest.param("story text", id="a parse that is not a mapping"),
    pytest.param({}, id="a story leaving no gap"),
    pytest.param({GAPS_LEFT: SHORT_GAP}, id="a gap that is not in a list"),
    pytest.param({GAPS_LEFT: None}, id="an empty value"),
    pytest.param({GAPS_LEFT: {"gap": "reason"}}, id="a mapping"),
    pytest.param({GAPS_LEFT: [7, None, ["nested"]]}, id="entries that are not strings"),
])
def test_a_gaps_value_the_reader_cannot_read_answers_with_no_gaps(story):
    """So a story leaving no gap and a story whose gaps could not be read both
    show nothing at the question; the fault, where there is one, is the
    validation's to report. The control is the test below."""
    assert story_coordinator.gaps_left(story) == []


def test_the_same_reader_answers_with_each_entry_whole_on_one_line():
    """The control for the empty answers above, and what "printed whole on a
    line of its own" needs of the reader: each entry comes back in order and
    uncut, however far past a title's bound it runs, with any run of
    whitespace — a newline among them — collapsed so it prints as one line.
    An entry that is not a string is passed over rather than printed."""
    wrapped = "a gap the planner\n  wrapped (its\treason)"
    assert story_coordinator.gaps_left({GAPS_LEFT: [*GAPS, 7, wrapped]}) == [
        *GAPS, "a gap the planner wrapped (its reason)"]


# ==========================================================================
# 2. The question, asked in process
# ==========================================================================


class KeepingWhatWasPrinted(Answering):
    """A terminal that answers no, and keeps what had been printed to stdout
    at the moment it was read."""

    def __init__(self, capsys):
        super().__init__(conftest.DECLINES)
        self.capsys = capsys
        self.printed: str | None = None

    def readline(self) -> str:
        self.printed = self.capsys.readouterr().out
        return super().readline()


#: Two artifacts, the first leaving gaps and the second leaving none. Keyed by
#: path, as `report` hands them to the question.
FIRST, SECOND = Path(PLANNED_REL), Path(SECOND_REL)
TITLES = {FIRST: TITLE, SECOND: SECOND_TITLE}


@pytest.fixture(scope="module")
def l5_plan():
    """The script under test as a module, through the shared loader, so its
    `approve` can be called in process."""
    return conftest.load_script("l5-plan", name="l5_plan_shows_the_gaps")


@pytest.fixture
def asked(l5_plan, monkeypatch, capsys):
    """Ask the question in process: what it printed up to the moment its
    answer was read, as lines, and what it returned. `pass_gaps` False makes
    the call the script made before this story, with the titles alone."""
    def ask(written=(), gaps=None, *, pass_gaps=True):
        stream = KeepingWhatWasPrinted(capsys)
        monkeypatch.setattr(sys, "stdin", stream)
        if pass_gaps:
            approved = l5_plan.approve(list(written), TITLES, gaps)
        else:
            approved = l5_plan.approve(list(written), TITLES)
        assert stream.reads == 1
        assert capsys.readouterr().out == "", \
            "something was printed after the read"
        return stream.printed.split("\n"), approved

    return ask


def gap_lines(lines: list[str], gaps: tuple[str, ...]) -> list[int]:
    """Where each gap is printed: the index of the one line ending in it."""
    found = []
    for gap in gaps:
        ending = [index for index, line in enumerate(lines) if line.endswith(gap)]
        assert len(ending) == 1, (gap, lines)
        found.append(ending[0])
    return found


def test_each_gap_is_printed_whole_beneath_its_artifacts_line_and_the_question_is_last(
        asked):
    """Each gap of the first artifact on a line of its own, whole, directly
    beneath that artifact's line and in the order the plan gives them; the
    second artifact, which leaves none, is named on the line after them; and
    the question is the last thing printed before the answer is read. A gap's
    line carries nothing that could be read as another story's line."""
    lines, approved = asked(gaps={FIRST: list(GAPS)})

    assert approved is False
    assert QUESTION in lines[-1]
    first = lines.index(subject_line(PLANNED_ID, TITLE))
    second = lines.index(subject_line(SECOND_ID, SECOND_TITLE))
    assert gap_lines(lines, GAPS) == [first + 1, first + 2]
    assert second == first + 1 + len(GAPS)
    assert second + 1 == len(lines) - 1
    for index in gap_lines(lines, GAPS):
        assert lines[index].startswith("l5-plan:")
        assert PLANNED_ID not in lines[index] and SECOND_ID not in lines[index]


def test_artifacts_leaving_no_gap_print_exactly_what_the_call_without_gaps_prints(
        asked):
    """What was printed before this story, asked of the question itself: with
    no gaps handed, with an empty list for every artifact, and with an
    artifact absent from the gaps handed, the question prints byte for byte
    what the call the script made before gaps existed prints — the titles
    alone.

    The control is the same question handed gaps for one artifact, whose
    output is that same text with the gap lines added beneath that artifact's
    line and nothing else changed."""
    before, _ = asked(pass_gaps=False)
    for gaps in (None, {}, {FIRST: [], SECOND: []}, {Path("story-999.yaml"): GAPS}):
        assert asked(gaps=gaps)[0] == before, gaps

    with_gaps, _ = asked(gaps={FIRST: list(GAPS)})
    added = gap_lines(with_gaps, GAPS)
    assert [line for index, line in enumerate(with_gaps)
            if index not in added] == before
    assert len(with_gaps) == len(before) + len(GAPS)


def test_a_session_written_block_is_still_told_of_before_the_question(asked):
    """The other thing the question carries, unmoved: where the session wrote
    a mandate block, the developer is told so after the artifacts' lines —
    gaps included — and before the question, which is still last."""
    lines, _ = asked(written=[FIRST], gaps={FIRST: list(GAPS)})
    told = [index for index, line in enumerate(lines)
            if plan_mandate.MANDATE_KEY in line and str(FIRST) in line]
    assert len(told) == 1, lines
    assert max(gap_lines(lines, GAPS)) < told[0] < len(lines) - 1
    assert lines.index(subject_line(SECOND_ID, SECOND_TITLE)) < told[0]
    assert QUESTION in lines[-1]


# ==========================================================================
# 3. The session, driven end to end
# ==========================================================================


def question_index(lines: list[str]) -> int:
    asked_at = [index for index, line in enumerate(lines) if QUESTION in line]
    assert len(asked_at) == 1, lines
    return asked_at[0]


def test_the_approval_prints_each_gap_beneath_its_artifacts_line_before_anything_is_stamped(
        planning: Planning):
    """Two artifacts, one leaving gaps and one leaving none, and the answer
    no: each gap is printed whole on a line of its own directly beneath its
    artifact's line, the other artifact's line follows them, and the question
    follows that. Nothing was stamped: the artifact is the session's own bytes
    and no story branch carries a commit.

    The control is the test below: the same session approved stamps and
    commits, having printed the same lines."""
    head = planning.head()
    result = decline_plan(planning, L5_STUB_WRITE=writes(
        (PLANNED_REL, leaving(titled(TITLE), *GAPS)),
        (SECOND_REL, titled(SECOND_TITLE, SECOND_ID))))

    assert result.returncode == 1
    lines = printed_lines(result.stdout)
    first = lines.index(subject_line(PLANNED_ID, TITLE))
    assert gap_lines(lines, GAPS) == [first + 1, first + 2]
    assert lines[first + 3] == subject_line(SECOND_ID, SECOND_TITLE)
    assert question_index(lines) == first + 4

    assert planning.head() == head
    written = (kept_worktree(result) / PLANNED_REL).read_text(encoding="utf-8")
    assert written == leaving(titled(TITLE), *GAPS)
    assert not plan_mandate.carries_a_mandate(written)
    assert planning.git(
        "rev-parse", "--verify", planning.planned_branch(PLANNED_ID)
    ).returncode != 0


def test_the_same_session_approved_stamps_and_commits_what_it_showed(
        planning: Planning):
    """The control the rejection above needs: the answer is what decided
    whether anything was stamped, and the gaps were printed above the
    question either way. What is committed carries the gaps as the session
    wrote them, beside the mandate the approval stamped."""
    head = planning.head()
    result = run_plan(planning, L5_STUB_WRITE=session_writing(
        leaving(titled(TITLE), *GAPS)))

    assert result.returncode == 0, result.stdout + result.stderr
    lines = printed_lines(result.stdout)
    first = lines.index(subject_line(PLANNED_ID, TITLE))
    assert gap_lines(lines, GAPS) == [first + 1, first + 2]
    assert question_index(lines) == first + 3
    assert planning.planned_head(PLANNED_ID) != head
    committed = planning.planned_file(PLANNED_REL)
    assert plan_mandate.carries_a_mandate(committed)
    assert story_coordinator.gaps_left(
        story_coordinator.read_story(committed).parsed) == list(GAPS)


def test_an_artifact_leaving_no_gap_is_followed_directly_by_the_question(
        planning: Planning):
    """What an artifact with no gaps_left printed before this story: its line,
    and then the question, with nothing between. The control is the same
    artifact leaving one gap, where that gap's line — and only it — stands
    between the two."""
    bare = decline_plan(planning, L5_STUB_WRITE=session_writing(titled(TITLE)))
    lines = printed_lines(bare.stdout)
    assert question_index(lines) == lines.index(
        subject_line(PLANNED_ID, TITLE)) + 1

    leaving_one = decline_plan(planning, L5_STUB_WRITE=session_writing(
        leaving(titled(TITLE), SHORT_GAP)))
    lines = printed_lines(leaving_one.stdout)
    first = lines.index(subject_line(PLANNED_ID, TITLE))
    assert gap_lines(lines, (SHORT_GAP,)) == [first + 1]
    assert question_index(lines) == first + 2


@pytest.mark.parametrize("tail", [
    pytest.param(f"\n{GAPS_LEFT}: {SHORT_GAP}\n", id="a gap that is not in a list"),
    pytest.param(f"\n{GAPS_LEFT}:\n  gap: {SHORT_GAP}\n",
                 id="a list that does not parse"),
])
def test_an_artifact_whose_gaps_cannot_be_read_shows_none_and_is_refused(
        planning: Planning, tail: str):
    """A gaps value that fails its schema, and one the dialect cannot parse,
    show no gap at the question — the artifact's line is followed directly by
    the question — and the validation that follows refuses the artifact with
    its problem printed and nothing committed, which is how the developer is
    told. Neither text is printed as though it were a gap.

    What the refusal must print is what the coordinator's own reader says of
    the artifact once a mandate is on it, which is the artifact the
    validation is handed, so the fault is the reader's and not a wording
    written here; it is asserted non-empty, so each tail is shown to be a
    fault the reader reports."""
    head = planning.head()
    text = titled(TITLE) + tail
    faults = story_coordinator.read_story(text + conftest.MANDATE_BLOCK).problems
    assert faults, tail
    result = run_plan(planning, L5_STUB_WRITE=session_writing(text))

    lines = printed_lines(result.stdout)
    named = [index for index, line in enumerate(lines)
             if line.startswith(subject_line(PLANNED_ID))]
    assert len(named) == 1, lines
    assert question_index(lines) == named[0] + 1
    assert SHORT_GAP not in result.stdout.split(QUESTION)[0]
    assert result.returncode == 1
    for fault in faults:
        assert fault in result.stderr, fault
    assert "committed nothing" in result.stdout
    assert planning.head() == head


# ==========================================================================
# 4. Where the gaps come from
# ==========================================================================


#: The two per-artifact readers the approval's subject is taken through, by
#: the names the coordinator gives them.
TITLE_READER = story_coordinator.story_title.__name__
GAPS_READER = story_coordinator.gaps_left.__name__


def readers_handed(source: str) -> dict[str, list[str]]:
    """What each of the two readers is handed in `source`, as the expression
    each call's argument is."""
    handed: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in (TITLE_READER, GAPS_READER)):
            handed.setdefault(node.func.attr, []).extend(
                ast.dump(argument) for argument in node.args)
    return handed


def takes_both_from_one_reading(source: str) -> bool:
    """Whether `source` reads each artifact once, through the coordinator's
    reader, and hands the title's reader and the gaps' reader the same value
    from that reading — a name bound to it, never a reading of its own."""
    handed = readers_handed(source)
    title, gaps = handed.get(TITLE_READER), handed.get(GAPS_READER)
    return (source.count("read_story") == 1
            and "story_coordinator.read_story" in source
            and bool(title) and title == gaps
            and all(argument.startswith("Name(") for argument in title))


def test_the_gaps_and_the_title_are_taken_from_the_one_reading_of_each_artifact():
    """l5-plan still reads each artifact once, through the coordinator's own
    reader, and takes both the title and the gaps from that one reading, so
    what is printed beneath a story's line is what the validation that
    follows reads in the same artifact."""
    assert takes_both_from_one_reading(report_source())


def test_the_reading_reports_gaps_read_a_second_time_or_not_at_all():
    """The control for the claim above: the same reading over a copy of the
    script's `report` whose gaps are read by a second call to the
    coordinator's reader reports it, and so does a copy that takes no gaps at
    all — so the claim is about where the gaps come from rather than a reading
    that answers yes to anything."""
    source = report_source()
    reread, count = re.subn(
        rf"story_coordinator\.{GAPS_READER}\(\w+\)",
        f"story_coordinator.{GAPS_READER}("
        f"story_coordinator.read_story(text).parsed)", source)
    assert count == 1
    assert not takes_both_from_one_reading(reread)

    unread, count = re.subn(
        rf"story_coordinator\.{GAPS_READER}\((\w+)\)", r"[]", source)
    assert count == 1
    assert not takes_both_from_one_reading(unread)
