"""A starting run says what the whole story is expected to take.

A run already says, as each long step begins, how long that step is expected
to take. It said nothing about the story as a whole, and adding the step
figures up gives the wrong answer: they cover one pass through the work, and
some runs are sent back for another. So a fresh run now says one line at its
start — how long a straight-through run takes, how long one that is sent back
takes, how often that happens, and that the figures are working time and
exclude waiting for a turn at the test machine.

This module holds that behaviour in the halves it actually has:

  * the derivation as a pure function over parsed records — one total per
    story, the split on the first stage's record count, the stories the
    execution log counts, the window and the ordering by record timestamp,
    and the records that are not observations;
  * the execution log located through its declaration rather than by
    filename, and the stories it records as completed;
  * the renderer, its four parts, its whole minutes and its rate to the
    nearest five percent, beside the per-operation clause it must not
    disturb;
  * the composition, which says nothing at all where either group is thinner
    than the minimum;
  * runs driven with the fake runner: a fresh run over a seeded history
    saying the line once at its start, a run over no history and a run over a
    thin one saying nothing, a resumed run saying nothing, and two otherwise
    identical runs reaching the same stages, retries, exit code and status;
  * the declarations — the kind in the run's own event enum, named by no
    cross-run log, spelled at one site — the constants that are constants
    rather than configuration, and the duration log's declaration, which no
    longer claims a single reader;
  * what may read the figures, which is the one announcement and nothing
    else.

Nothing here reaches a model: the coordinator makes no model call and the
runs below have the inspection's only invocation switched off. The workflow
is the one `tests/test_an_operation_announces_what_it_has_taken_before.py`
builds rather than the definition this repository ships, and the histories
are seeded under the target each run works in rather than read out of this
repository's own. Every absence asserted here sits beside a demonstration
that the same check reports the violation it exists to catch.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest

import agent_runner
import conftest
import schema_validator
import story_coordinator

import test_cross_run_history as cross
from test_a_run_announces_what_it_is_waiting_on import history_in, messages_in
from test_execution_history import COORDINATOR_SOURCE

from test_an_operation_announces_what_it_has_taken_before import (  # noqa: F401
    DURATION_LOG,                                    # fixtures used by name
    REQUIRED as DURATION_FIELDS,
    STAGE_NAMES,
    STORY_ID,
    WORKFLOW,
    clause_of,
    configured_workflow,
    harness_root,
    history_dir_of,
    no_model,
    observation,
    stamp,
    without_clause,
)

FIRST_STAGE, VERIFYING = STAGE_NAMES

#: The category a failing verdict names and the stage it routes to, read off
#: the loaded definition rather than written here.
RETRY_CATEGORY, _RETRY_STAGE = conftest.first_retry_route(WORKFLOW)

WINDOW = story_coordinator.WHOLE_STORY_ESTIMATE_WINDOW
MINIMUM = story_coordinator.WHOLE_STORY_ESTIMATE_MINIMUM
COMPLETED = story_coordinator.COMPLETED_EXECUTION_STATUS

#: The second declared log this module seeds: the one saying what point an
#: execution reached. Derived here from the declaration's own fields — the
#: only record shape carrying a status — so the coordinator's locator is
#: checked against this rather than standing in for it.
EXECUTION_LOG = next(name for name, shape in cross.DECLARATIONS.items()
                     if "status" in shape["properties"])
EXECUTION_FIELDS = set(cross.DECLARATIONS[EXECUTION_LOG]["required"])

#: The kind the line carries. The coordinator's own vocabulary, spelled here
#: as the modules reading the stream spell theirs, and pinned to behaviour
#: below: the seeded run's stream must gain exactly this kind and no other.
WHOLE_STORY_KIND = "whole-story-estimated"

#: The line, as the story states it and as a reader meets it. Written here
#: rather than derived from the renderer, because the claim is that the
#: renderer produces this form and a pattern read out of it would agree with
#: itself whatever it rendered.
LINE = re.compile(
    r"^Estimated: (\d+) minutes?, or (\d+) if it is sent back "
    r"\(about (\d+)% are\)\. Excludes waiting for the machine\.$")


@pytest.fixture(autouse=True)
def no_estimates_held_over():
    """The per-operation estimates are module state, established once per run
    and left in place when the run ends. The runs below establish them, so
    each test here starts and ends with none, exactly as the module that owns
    that state does."""
    story_coordinator.set_duration_estimates({})
    yield
    story_coordinator.set_duration_estimates({})


# ==========================================================================
# Seeding: the records this module hands the derivation
# ==========================================================================

AN_OPERATION = "an-operation-this-module-named"


def duration(story_id: str, operation: str, seconds, when: str) -> dict:
    """One duration record of the declared shape, keyed by its own fields."""
    record = observation(operation, seconds, when, story_id)
    assert set(record) == DURATION_FIELDS, "the declared shape is not this"
    return record


def execution(story_id: str, status: str, when: str) -> dict:
    """One execution record of the declared shape."""
    record = {"story_id": story_id, "status": status, "timestamp": when}
    assert set(record) == EXECUTION_FIELDS, "the declared shape is not this"
    return record


def a_story(story_id: str, first_stage_values, *, index: int,
            stage: str = FIRST_STAGE, other=()) -> list[dict]:
    """One story's duration records: its passes at `stage`, then whatever
    else it did. `index` spaces the stories apart in time, so the story with
    the larger index is the more recent one."""
    values = list(first_stage_values)
    return [duration(story_id, stage, seconds, stamp(index * 8 + position))
            for position, seconds in enumerate(values)] + \
           [duration(story_id, AN_OPERATION, seconds,
                     stamp(index * 8 + len(values) + position))
            for position, seconds in enumerate(other)]


#: Six counted stories: three that hold one record of the first stage, whose
#: totals are 300, 600 and 900 seconds, and three that hold two, whose totals
#: are 1200, 1800 and 2400. So the medians are 600 and 1800 seconds — ten
#: minutes and thirty — and half the stories were sent back.
STRAIGHT_THROUGH = {"story-101": [300.0], "story-102": [600.0],
                    "story-103": [900.0]}
SENT_BACK = {"story-111": [600.0, 600.0], "story-112": [900.0, 900.0],
             "story-113": [1200.0, 1200.0]}
SEEDED_LINE = ("Estimated: 10 minutes, or 30 if it is sent back "
               "(about 50% are). Excludes waiting for the machine.")

#: A seventh story, sent back, enormous, and never recorded as completed. Its
#: total would move both the sent-back median and the rate if it were counted,
#: so the line the seeded run says is the proof that it is not.
UNCOUNTED_STORY = "story-199"
UNCOUNTED_PASSES = [50_000.0, 50_000.0]


def seeded_durations() -> list[dict]:
    """The duration records of the six counted stories and the uncounted one,
    in story order — which is also timestamp order here."""
    records: list[dict] = []
    for index, (story, passes) in enumerate(
            {**STRAIGHT_THROUGH, **SENT_BACK,
             UNCOUNTED_STORY: UNCOUNTED_PASSES}.items()):
        records += a_story(story, passes, index=index)
    return records


def seeded_executions() -> list[dict]:
    """One execution record per seeded story: the six counted ones completed,
    the seventh escalated."""
    stories = list(STRAIGHT_THROUGH) + list(SENT_BACK)
    records = [execution(story, COMPLETED, stamp(index * 8 + 7))
               for index, story in enumerate(stories)]
    return records + [execution(UNCOUNTED_STORY, "escalated", stamp(60))]


COUNTED = set(STRAIGHT_THROUGH) | set(SENT_BACK)


def reading_over(durations: list, executions: list | None = None,
                 segment: str = "a-segment") -> story_coordinator.HistoryReading:
    """A startup read carrying exactly the records handed in.

    The reading is an input to the derivation rather than its subject, so it
    is constructed here rather than read off a repository: nothing about which
    files a history happens to hold is part of what this module asserts, and a
    constructed one moves under nothing.
    """
    files = {
        Path(segment) / DURATION_LOG: [(json.dumps(r), r) for r in durations],
        Path(segment) / EXECUTION_LOG: [(json.dumps(r), r)
                                        for r in (executions or [])],
    }
    return story_coordinator.HistoryReading([], {}, None, files)


# ==========================================================================
# The derivation as a pure function over records
# ==========================================================================


def test_a_storys_total_is_every_record_it_holds_added_up():
    """One story, one pass at the first stage and two other timed operations:
    its total is the sum of all three, not of the stage alone."""
    records = a_story("story-201", [300.0], index=0, other=[120.0, 60.0])
    totals = story_coordinator.whole_story_totals(
        records, {"story-201"}, FIRST_STAGE)
    assert totals.straight_through == [480.0]
    assert totals.sent_back == []


def test_one_record_of_the_first_stage_is_straight_through_and_more_is_sent_back():
    """The split, with its two sides in one call so the classification is the
    record count and not something else about either story."""
    records = (a_story("story-201", [300.0], index=0)
               + a_story("story-202", [300.0, 300.0], index=1))
    totals = story_coordinator.whole_story_totals(
        records, {"story-201", "story-202"}, FIRST_STAGE)
    assert totals.straight_through == [300.0]
    assert totals.sent_back == [600.0]


def test_a_story_holding_no_record_of_the_first_stage_is_in_neither_group():
    """Nothing about such a story says which way it went, so it is in neither
    median and in neither denominator. The control is the same records with
    one first-stage pass added, which does land in a group."""
    records = a_story("story-201", [], index=0, other=[300.0])
    totals = story_coordinator.whole_story_totals(
        records, {"story-201"}, FIRST_STAGE)
    assert totals.straight_through == [] and totals.sent_back == []

    with_a_pass = a_story("story-201", [300.0], index=0, other=[300.0])
    grouped = story_coordinator.whole_story_totals(
        with_a_pass, {"story-201"}, FIRST_STAGE)
    assert grouped.straight_through == [600.0]


def test_the_first_stage_is_the_one_it_is_handed_and_not_a_name_of_its_own():
    """The same records classified against the two stage names the workflow
    declares: the story holding two records of the first stage is sent back
    when that stage is named, and is in neither group when the other is —
    because it holds none of the other's. A name written into the derivation
    could not do that."""
    records = a_story("story-201", [300.0, 300.0], index=0, other=[60.0])
    assert FIRST_STAGE != VERIFYING

    by_first = story_coordinator.whole_story_totals(
        records, {"story-201"}, FIRST_STAGE)
    assert by_first.sent_back == [660.0] and by_first.straight_through == []

    by_other = story_coordinator.whole_story_totals(
        records, {"story-201"}, VERIFYING)
    assert by_other.sent_back == [] and by_other.straight_through == []

    against_that_stage = a_story("story-202", [300.0], index=1,
                                 stage=VERIFYING)
    assert story_coordinator.whole_story_totals(
        against_that_stage, {"story-202"}, VERIFYING).straight_through == [300.0]


def test_a_story_the_counted_set_does_not_name_contributes_to_neither_group():
    """The control is the same records with the story counted, where it does
    contribute: the exclusion is the counted set deciding rather than the
    records being unreadable."""
    records = (a_story("story-201", [300.0], index=0)
               + a_story("story-202", [900.0], index=1))
    without = story_coordinator.whole_story_totals(
        records, {"story-201"}, FIRST_STAGE)
    assert without.straight_through == [300.0]

    with_both = story_coordinator.whole_story_totals(
        records, {"story-201", "story-202"}, FIRST_STAGE)
    assert with_both.straight_through == [300.0, 900.0]


#: One more story than the window holds, each straight through, each total
#: distinct: so which stories fell outside the window is visible in the
#: totals rather than only in their count.
def a_window_and_one_more(oldest_first: bool = True) -> list[dict]:
    records: list[dict] = []
    for index in range(WINDOW + 1):
        story = f"story-3{index:02d}"
        when = index if oldest_first else WINDOW - index
        records += a_story(story, [60.0 * (index + 1)], index=when)
    return records


def every_story(count: int) -> set[str]:
    return {f"story-3{index:02d}" for index in range(count)}


def test_only_the_most_recent_stories_within_the_window_are_counted():
    """One story more than the window, and the oldest is the one dropped.

    The control is the same stories with the oldest made the *newest*: it is
    then inside the window and its total is counted, so the bound is doing
    the excluding rather than that story being ignored for some other reason.
    """
    counted = every_story(WINDOW + 1)
    totals = story_coordinator.whole_story_totals(
        a_window_and_one_more(), counted, FIRST_STAGE)
    assert len(totals.straight_through) == WINDOW
    assert 60.0 not in totals.straight_through

    reversed_time = story_coordinator.whole_story_totals(
        a_window_and_one_more(oldest_first=False), counted, FIRST_STAGE)
    assert len(reversed_time.straight_through) == WINDOW
    assert 60.0 in reversed_time.straight_through


def test_the_window_is_taken_by_timestamp_and_not_by_the_order_records_arrive():
    """The same records handed over in time order, reversed and interleaved
    give one answer: what is recent is decided by the timestamp each record
    carries, which is what lets a log gathered from segments whose names do
    not sort in time order be read correctly."""
    counted = every_story(WINDOW + 1)
    in_time = a_window_and_one_more()
    arrangements = [in_time, list(reversed(in_time)),
                    in_time[1::2] + in_time[::2]]
    assert arrangements[1] != in_time and arrangements[2] != in_time
    answers = [story_coordinator.whole_story_totals(
        arrangement, counted, FIRST_STAGE) for arrangement in arrangements]
    assert answers[0] == answers[1] == answers[2]
    assert len(answers[0].straight_through) == WINDOW


def not_observations(story_id: str) -> list[dict]:
    """Records of the declared shape that are not observations: a duration
    that is not a number — a string, null, a bool — and a timestamp that
    cannot be read."""
    return [
        duration(story_id, FIRST_STAGE, "twelve", stamp(40)),
        duration(story_id, FIRST_STAGE, None, stamp(41)),
        duration(story_id, FIRST_STAGE, True, stamp(42)),
        duration(story_id, FIRST_STAGE, 480, "not a timestamp"),
        duration(story_id, FIRST_STAGE, 480, 1_700_000_000),
    ]


def test_a_record_that_is_not_an_observation_contributes_nothing_and_stops_nothing():
    """Skipped, and the totals are of the observations around them. The
    control is the same records on their own: enough lines to fill a group,
    and they yield none, so they are being skipped rather than counted. A
    line that is not an object at all is skipped the same way."""
    skipped = not_observations("story-201") + ["not even an object"]
    real = a_story("story-201", [300.0], index=0)
    totals = story_coordinator.whole_story_totals(
        skipped + real, {"story-201"}, FIRST_STAGE)
    assert totals.straight_through == [300.0] and totals.sent_back == []

    assert len(skipped) > 1, "one skipped line cannot show a group filling"
    alone = story_coordinator.whole_story_totals(
        skipped, {"story-201"}, FIRST_STAGE)
    assert alone.straight_through == [] and alone.sent_back == []


def test_no_records_at_all_yields_no_totals():
    totals = story_coordinator.whole_story_totals([], set(), FIRST_STAGE)
    assert totals.straight_through == [] and totals.sent_back == []


# ==========================================================================
# The execution log, and the stories it counts
# ==========================================================================


def test_the_execution_log_is_found_through_its_declaration_and_not_by_filename():
    """Located by the status a finished run is recorded under: the same
    declarations with that value gone from the enum locate nothing, and a
    declaration set naming the log under another filename still locates it."""
    assert story_coordinator.execution_log_name(cross.DECLARATIONS) == \
        EXECUTION_LOG

    stripped = json.loads(json.dumps(cross.DECLARATIONS))
    enum = stripped[EXECUTION_LOG]["properties"]["status"]["enum"]
    stripped[EXECUTION_LOG]["properties"]["status"]["enum"] = \
        [value for value in enum if value != COMPLETED]
    assert story_coordinator.execution_log_name(stripped) is None

    renamed = {("another-name.jsonl" if name == EXECUTION_LOG else name): shape
               for name, shape in cross.DECLARATIONS.items()}
    assert story_coordinator.execution_log_name(renamed) == "another-name.jsonl"


def test_the_completed_status_is_one_the_declaration_admits():
    """The constant the coordinator counts by is a value the log can carry,
    rather than a spelling only the coordinator knows."""
    assert COMPLETED in \
        cross.DECLARATIONS[EXECUTION_LOG]["properties"]["status"]["enum"]


def test_only_the_stories_recorded_as_completed_are_counted():
    """A run that escalated leaves records of the operations it did make, and
    summing those would report a story that did less work as a story that took
    less time. The control is the same reading with that story's status
    changed to the completed one, where it is counted."""
    reading = reading_over(seeded_durations(), seeded_executions())
    assert story_coordinator.completed_story_ids(reading) == COUNTED
    assert UNCOUNTED_STORY not in COUNTED

    completed_instead = [
        {**record, "status": COMPLETED}
        if record["story_id"] == UNCOUNTED_STORY else record
        for record in seeded_executions()]
    assert story_coordinator.completed_story_ids(
        reading_over(seeded_durations(), completed_instead)) == \
        COUNTED | {UNCOUNTED_STORY}


def test_a_line_that_is_not_a_record_counts_nothing_and_stops_nothing():
    """A line that is not an object, an object with no story, and one whose
    story is not a string: skipped. The control is the real records beside
    them, which are still counted."""
    noise = ["not an object", {"status": COMPLETED}, {},
             {"story_id": 12, "status": COMPLETED, "timestamp": stamp(1)}]
    reading = reading_over([], noise + seeded_executions())
    assert story_coordinator.completed_story_ids(reading) == COUNTED
    assert story_coordinator.completed_story_ids(reading_over([], noise)) == set()


# ==========================================================================
# The renderer
# ==========================================================================


@pytest.mark.parametrize("straight, sent, rate, rendered", [
    (600, 1800, 0.5,
     "Estimated: 10 minutes, or 30 if it is sent back (about 50% are). "
     "Excludes waiting for the machine."),
    (4500, 12600, 0.4,
     "Estimated: 75 minutes, or 210 if it is sent back (about 40% are). "
     "Excludes waiting for the machine."),
    (60, 120, 0.0,
     "Estimated: 1 minute, or 2 if it is sent back (about 0% are). "
     "Excludes waiting for the machine."),
])
def test_the_line_carries_its_four_parts_in_the_stated_shape(
        straight, sent, rate, rendered):
    assert story_coordinator.whole_story_run_time(straight, sent, rate) == \
        rendered
    assert LINE.match(rendered)


@pytest.mark.parametrize("seconds, minutes", [
    (60, 1), (599, 10), (660, 11), (1, 1), (0, 1)])
def test_the_figures_are_whole_minutes(seconds, minutes):
    """Rounded to the minute, and never to nothing: a figure of zero minutes
    would say a story takes no time."""
    line = story_coordinator.whole_story_run_time(seconds, seconds, 0.5)
    found = LINE.match(line)
    assert found and (int(found.group(1)), int(found.group(2))) == \
        (minutes, minutes)


@pytest.mark.parametrize("rate, share", [
    (0.0, 0), (0.41, 40), (0.44, 45), (0.46, 45), (0.49, 50), (1.0, 100)])
def test_the_rate_is_rendered_to_the_nearest_five_percent(rate, share):
    found = LINE.match(story_coordinator.whole_story_run_time(600, 600, rate))
    assert found and int(found.group(3)) == share


def test_the_per_operation_clause_is_what_it_was():
    """The other renderer, untouched: this story adds a figure beside the
    per-stage estimate rather than changing the one spelling of that clause."""
    assert story_coordinator.estimated_run_time(720) == \
        "(Estimated run time: 12 minutes)"
    assert story_coordinator.estimated_run_time(40) == \
        "(Estimated run time: 40 seconds)"
    assert clause_of(SEEDED_LINE) is None, \
        "the whole-story line must not be read as a per-operation clause"


# ==========================================================================
# The composition, and the silence where history is thin
# ==========================================================================


def test_the_line_is_composed_from_the_two_medians_and_the_share():
    """The whole derivation driven end to end over constructed records,
    including the story the execution log does not count — whose totals would
    move both the sent-back figure and the rate if it were counted."""
    reading = reading_over(seeded_durations(), seeded_executions())
    assert story_coordinator.whole_story_estimate(reading, FIRST_STAGE) == \
        SEEDED_LINE

    counted_instead = [
        {**record, "status": COMPLETED}
        if record["story_id"] == UNCOUNTED_STORY else record
        for record in seeded_executions()]
    assert story_coordinator.whole_story_estimate(
        reading_over(seeded_durations(), counted_instead), FIRST_STAGE) != \
        SEEDED_LINE


def a_group(count: int, *, passes: int, index: int = 0) -> tuple[list, list]:
    """`count` counted stories, each holding `passes` records of the first
    stage, with the execution records that count them."""
    durations: list[dict] = []
    executions: list[dict] = []
    for position in range(count):
        story = f"story-4{index}{position:02d}"
        durations += a_story(story, [300.0] * passes, index=index * 30 + position)
        executions.append(execution(story, COMPLETED, stamp(1)))
    return durations, executions


@pytest.mark.parametrize("straight, sent", [
    (MINIMUM - 1, MINIMUM), (MINIMUM, MINIMUM - 1), (0, 0),
    (MINIMUM, MINIMUM)])
def test_a_group_thinner_than_the_minimum_says_nothing_at_all(straight, sent):
    """One below the minimum in either group and there is no line; the
    minimum in both and there is one — the pair, so the threshold is the
    constant and not some other number."""
    straight_durations, straight_executions = a_group(straight, passes=1, index=0)
    sent_durations, sent_executions = a_group(sent, passes=2, index=1)
    reading = reading_over(straight_durations + sent_durations,
                           straight_executions + sent_executions)

    line = story_coordinator.whole_story_estimate(reading, FIRST_STAGE)
    if straight >= MINIMUM and sent >= MINIMUM:
        assert line is not None and LINE.match(line)
    else:
        assert line is None


def test_an_empty_history_says_nothing():
    assert story_coordinator.whole_story_estimate(
        reading_over([], []), FIRST_STAGE) is None


# ==========================================================================
# Runs over a seeded history, over none, and resumed
# ==========================================================================

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(marker: str) -> dict:
    """A failing verdict recommending no retry, which escalates the run."""
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"the sample behaviour is missing ({marker})",
            "location": "src/app.py",
            "required_behavior": "the sample behaviour exists",
        }],
        "unverified": [],
        "retry_recommended": False,
        "retry_target": RETRY_CATEGORY,
    }


class Runner:
    """A fake agent runner writing each stage's declared artifacts, with the
    verdicts the caller asked for, in order."""

    def __init__(self, target_root: Path, verdicts: list | None = None):
        self.run_dir = conftest.run_dir_for(target_root, STORY_ID)
        self.calls: list[str] = []
        self.verdicts = list(verdicts or [PASS])

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None,
                 run_dir=None):
        self.calls.append(stage)
        if stage == FIRST_STAGE:
            cross.write_json(self.run_dir / conftest.CHANGED_FILES, {
                "modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Implemented.\n", encoding="utf-8")
            (Path(cwd) / "src" / "app.py").write_text(
                "print('changed')\n", encoding="utf-8")
        elif stage == VERIFYING:
            seen = self.calls.count(stage) - 1
            cross.write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                             self.verdicts[min(seen, len(self.verdicts) - 1)])
        return agent_runner.AgentResult(ok=True, result_text=f"{stage} done")


def seed(target_root: Path, durations: list[dict],
         executions: list[dict]) -> None:
    """Write a history the run did not produce, under one segment.

    The tree is created as the run would create it and the files are
    committed there, because a run refuses a dirty tree and the seeding may
    not be what refuses it.
    """
    tree = conftest.worktree_a_run_left(target_root, STORY_ID)
    history = history_dir_of(target_root, STORY_ID)
    for log, records in ((DURATION_LOG, durations),
                         (EXECUTION_LOG, executions)):
        path = history / "seeded" / log
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records),
                        encoding="utf-8")
    conftest.commit_setup(tree, "a history this run did not produce")


def run(target_root: Path, harness: Path,
        verdicts: list | None = None) -> tuple[int, Runner, Path]:
    runner = Runner(target_root, verdicts)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner, conftest.run_dir_for(target_root, STORY_ID)


def lines_of_the_kind(run_dir: Path) -> list[str]:
    return [entry["message"] for entry in history_in(run_dir)
            if entry["event"] == WHOLE_STORY_KIND]


def test_a_fresh_run_over_enough_history_says_the_line_once_at_its_start(
        target_root, harness_root, no_model):
    """The standing claim: one line, carrying the four parts, before the run
    has entered a stage, in the run's structured history and in the events
    log a person watches."""
    seed(target_root, seeded_durations(), seeded_executions())
    code, runner, run_dir = run(target_root, harness_root)
    assert code == 0 and runner.calls == STAGE_NAMES
    assert no_model.calls == 0

    assert lines_of_the_kind(run_dir) == [SEEDED_LINE]
    assert LINE.match(SEEDED_LINE)
    assert SEEDED_LINE in messages_in(run_dir)

    entries = history_in(run_dir)
    position = [entry["event"] for entry in entries].index(WHOLE_STORY_KIND)
    started = [entry["event"] for entry in entries].index("stage-started")
    assert position < started, "the line came after the run had begun working"


def test_a_run_over_no_history_says_no_such_line(
        target_root, harness_root, no_model):
    """The fallback exercised rather than assumed: no history at all, and
    nothing of the kind anywhere in the stream."""
    code, runner, run_dir = run(target_root, harness_root)
    assert code == 0 and runner.calls == STAGE_NAMES
    assert lines_of_the_kind(run_dir) == []
    assert [line for line in messages_in(run_dir) if LINE.match(line)] == []


@pytest.mark.parametrize("thin", ["straight", "sent"])
def test_a_run_whose_history_is_thin_in_either_group_says_no_such_line(
        target_root, harness_root, no_model, thin):
    """A history holding one fewer than the minimum in one group: the run
    says nothing invented. The control is the same run over the full history
    above, which does say it."""
    durations = seeded_durations()
    executions = seeded_executions()
    dropped = list(STRAIGHT_THROUGH if thin == "straight" else SENT_BACK)[0]
    seed(target_root,
         [r for r in durations if r["story_id"] != dropped],
         [r for r in executions if r["story_id"] != dropped])

    code, _, run_dir = run(target_root, harness_root)
    assert code == 0
    assert lines_of_the_kind(run_dir) == []
    assert [line for line in messages_in(run_dir) if LINE.match(line)] == []


def comparable(entries: list[dict]) -> list[tuple]:
    """An event stream with the whole-story line taken out and the
    per-operation clause stripped from what is left.

    Two differences are expected between a run over a seeded history and a
    run over none, and only one of them belongs to this story: the new line,
    and the per-stage estimate a seeded history also establishes, which this
    story leaves exactly as it was. Both are removed here so what remains is
    the routing, which must be identical.
    """
    return [(entry["event"], entry.get("stage"), without_clause(entry["message"]))
            for entry in entries if entry["event"] != WHOLE_STORY_KIND]


def test_a_seeded_history_changes_what_the_run_says_and_not_what_it_does(
        target_root, harness_root, tmp_path, no_model):
    """Two identical runs, one over a seeded history and one over none: the
    same stage sequence, retry count, exit code and recorded status, and an
    event stream that differs only in what is reported.

    The control is that the streams *do* differ before the reports are taken
    out, so the equality after is those reports being the whole difference
    rather than two runs that were identical anyway.
    """
    seeded = tmp_path / "seeded-target"
    shutil.copytree(target_root, seeded, symlinks=True)
    seed(seeded, seeded_durations(), seeded_executions())

    plain_code, plain_runner, plain_dir = run(target_root, harness_root)
    seeded_code, seeded_runner, seeded_dir = run(seeded, harness_root)
    assert plain_code == seeded_code == 0
    assert plain_runner.calls == seeded_runner.calls == STAGE_NAMES
    assert no_model.calls == 0

    plain_state = cross.state_of(plain_dir)
    seeded_state = cross.state_of(seeded_dir)
    for key in ("status", "retry_count", "verification_iterations",
                "current_stage"):
        assert plain_state[key] == seeded_state[key], key

    plain = history_in(plain_dir)
    seeded_history = history_in(seeded_dir)
    assert [e["message"] for e in plain] != [e["message"] for e in seeded_history]
    assert comparable(plain) == comparable(seeded_history)
    assert lines_of_the_kind(plain_dir) == []
    assert lines_of_the_kind(seeded_dir) == [SEEDED_LINE]


def test_the_seeded_run_adds_exactly_one_kind_to_the_stream(
        target_root, harness_root, tmp_path, no_model):
    """What the seeded run says that the plain run does not is one kind, and
    it is the one this module names — so the literal above is pinned to the
    behaviour rather than merely agreeing with the schema."""
    seeded = tmp_path / "seeded-target"
    shutil.copytree(target_root, seeded, symlinks=True)
    seed(seeded, seeded_durations(), seeded_executions())

    _, _, plain_dir = run(target_root, harness_root)
    _, _, seeded_dir = run(seeded, harness_root)
    plain = {entry["event"] for entry in history_in(plain_dir)}
    added = {entry["event"] for entry in history_in(seeded_dir)} - plain
    assert added == {WHOLE_STORY_KIND}


def test_a_resumed_run_says_no_such_line(target_root, harness_root, no_model):
    """The first entry escalates and says the line, because it is fresh. The
    resume, over the same history and the same workflow, says nothing: it
    would be telling a developer about work already done.

    The escalating entry is the control: the same target, the same seeded
    history, and the line said once — so the resume's silence is the resume
    deciding rather than the history having become too thin.
    """
    seed(target_root, seeded_durations(), seeded_executions())
    code, _, run_dir = run(target_root, harness_root, [failing("first entry")])
    assert code == 2, "the shape was meant to escalate"
    assert cross.state_of(run_dir)["status"] == "escalated"
    assert lines_of_the_kind(run_dir) == [SEEDED_LINE]

    tree = conftest.run_root_for(target_root, STORY_ID)
    (tree / "src" / "app.py").write_text(
        "print('what the developer decided')\n", encoding="utf-8")
    conftest.commit_setup(tree, "what the developer decided to do")

    resumed_code, resumed_runner, _ = run(target_root, harness_root, [PASS])
    assert resumed_code == 0, "the resume was meant to complete"
    assert resumed_runner.calls, "no stage ran, so nothing was resumed"
    assert "resumed" in [entry["event"] for entry in history_in(run_dir)]
    assert lines_of_the_kind(run_dir) == [SEEDED_LINE]


# ==========================================================================
# Where the line is declared, and what is a constant
# ==========================================================================


def test_the_kind_is_declared_in_the_runs_own_event_enum():
    run_schema = schema_validator.load_schema("execution-history")
    assert WHOLE_STORY_KIND in \
        run_schema["items"]["properties"][cross.EVENT]["enum"]


def test_the_kind_is_named_by_no_cross_run_logs_enum():
    """A report to a person outlives the run that made it in no durable log:
    what the whole story takes is derived from the records rather than being a
    second record beside them. The control is the same projection against the
    same declaration with the kind added to its enum, which does produce a
    record — so the None is the declaration deciding rather than the
    projection being unable to produce anything at all."""
    declarations = story_coordinator.history_log_declarations()
    assert declarations, "no cross-run log is declared at all"
    for log, declaration in declarations.items():
        assert WHOLE_STORY_KIND not in \
            declaration["properties"][cross.EVENT]["enum"], log
        entry = {"sequence": 1, "timestamp": stamp(0), cross.EVENT: WHOLE_STORY_KIND,
                 "message": SEEDED_LINE}
        for name in declaration.get("required", []):
            entry.setdefault(name, 1.5 if "seconds" in name else f"a {name}")
        assert story_coordinator.history_record(
            entry, [], STORY_ID, declaration) is None, log

        routed = json.loads(json.dumps(declaration))
        routed["properties"][cross.EVENT]["enum"] = [WHOLE_STORY_KIND]
        assert story_coordinator.history_record(
            entry, [], STORY_ID, routed) is not None, log


def emit_sites(source: str, kind: str) -> list[str]:
    """Every `append_event` call in `source` naming `kind` as a literal,
    unparsed. A kind assembled at runtime is refused rather than silently
    dropped, because the scan could not see it."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "append_event"):
            continue
        named = next((kw.value for kw in node.keywords if kw.arg == "kind"), None)
        if named is None:
            continue
        assert isinstance(named, ast.Constant), (
            f"a kind assembled at runtime cannot be checked: {ast.unparse(node)}")
        if named.value == kind:
            found.append(ast.unparse(node))
    return found


def test_the_kind_is_spelled_at_exactly_one_site():
    """One emitter, so the line is said once wherever a run reaches it. The
    control is a planted second emitter, which the same scan reports."""
    assert len(emit_sites(COORDINATOR_SOURCE, WHOLE_STORY_KIND)) == 1

    planted = ('\n\ndef _planted_emitter(run_dir):\n'
               f'    append_event(run_dir, "again", kind="{WHOLE_STORY_KIND}")\n')
    assert len(emit_sites(COORDINATOR_SOURCE + planted, WHOLE_STORY_KIND)) == 2


def guards_around(source: str, kind: str) -> list[list[str]]:
    """For each emitter of `kind`, the tests of the `if` statements enclosing
    it, unparsed, outermost first."""
    tree = ast.parse(source)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "append_event"):
            continue
        named = next((kw.value for kw in node.keywords if kw.arg == "kind"), None)
        if not (isinstance(named, ast.Constant) and named.value == kind):
            continue
        tests, walk = [], parents.get(id(node))
        while walk is not None:
            if isinstance(walk, ast.If):
                tests.append(ast.unparse(walk.test))
            walk = parents.get(id(walk))
        found.append(list(reversed(tests)))
    return found


FRESH_RUN_FLAG = "fresh_run"


def test_the_line_is_said_on_a_fresh_run_and_on_no_other():
    """The emitter sits under the flag that says this entry is a fresh run
    rather than a resume, and that flag is the loaded state being absent — so
    the silence holds whichever status a resume resumed from, rather than for
    the one status a run can be driven into here.

    The control is a planted emitter under no such guard, which the same scan
    reports as guarded by nothing.
    """
    [guards] = guards_around(COORDINATOR_SOURCE, WHOLE_STORY_KIND)
    assert FRESH_RUN_FLAG in guards, guards
    assert f"{FRESH_RUN_FLAG} = state is None" in COORDINATOR_SOURCE

    planted = ('\n\ndef _planted_emitter(run_dir):\n'
               f'    append_event(run_dir, "always", kind="{WHOLE_STORY_KIND}")\n')
    assert [] in guards_around(COORDINATOR_SOURCE + planted, WHOLE_STORY_KIND)


def estimate_keys_in(schema: dict) -> list[str]:
    return sorted(key for key in schema.get("properties", {})
                  if "estimate" in key)


def test_the_window_and_the_minimum_are_constants_and_not_configuration():
    """The two figures, and the absence of a key for either in the target's
    own configuration schema — with a copy of that schema carrying such a key
    as the control for the absence."""
    assert WINDOW == 20
    assert MINIMUM == 3
    schema = schema_validator.load_schema("harness-config")
    assert estimate_keys_in(schema) == []
    planted = json.loads(json.dumps(schema))
    planted["properties"]["whole_story_estimate_window"] = {"type": "integer"}
    assert estimate_keys_in(planted) == ["whole_story_estimate_window"]


#: The sentence the duration log's declaration carried before this story, and
#: which the second reader makes untrue.
A_SINGLE_READER = "read by exactly one thing"


def test_the_duration_logs_declaration_no_longer_claims_a_single_reader():
    """It says there are two readers and what the second derives. The control
    is the same check over the declaration with the old sentence put back,
    which reports it — so the absence is the text having changed rather than
    the check looking in the wrong place."""
    declared = schema_validator.load_schema(
        story_coordinator.CROSS_RUN_HISTORY_SCHEMA)
    description = declared["properties"][DURATION_LOG]["description"]

    assert A_SINGLE_READER not in description
    assert "two things" in description
    assert "whole story" in description

    as_it_was = description.replace("read by two things", A_SINGLE_READER)
    assert A_SINGLE_READER in as_it_was, \
        "the control did not reproduce the claim, so the absence proves nothing"


# ==========================================================================
# What may read the figures
# ==========================================================================


def functions_naming(name: str, source: str) -> set[str | None]:
    """The functions whose bodies mention `name`, with None standing for the
    module level."""
    found: set[str | None] = set()

    def visit(node, enclosing):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Name) and child.id == name:
                found.add(enclosing)
            inner = (child.name
                     if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else enclosing)
            visit(child, inner)

    visit(ast.parse(source), None)
    return found


#: Each new name, and every function in the coordinator that may mention it.
#: The composition is named by the one announcement site and the pieces by the
#: composition, so no verdict, check, timeout, bound, retry or routing
#: decision can be reading a whole-story figure.
MAY_NAME = {
    "whole_story_estimate": {"run_story"},
    "whole_story_totals": {"whole_story_estimate"},
    "whole_story_run_time": {"whole_story_estimate"},
    "completed_story_ids": {"whole_story_estimate"},
    "execution_log_name": {"completed_story_ids"},
}


@pytest.mark.parametrize("name", sorted(MAY_NAME))
def test_each_whole_story_figure_is_named_only_where_it_may_be(name):
    assert functions_naming(name, COORDINATOR_SOURCE) == MAY_NAME[name]


@pytest.mark.parametrize("name", sorted(MAY_NAME))
def test_the_scan_reports_a_second_reader(name):
    """The control for each absence above: a planted function that routes on
    the figure, which the same scan reports."""
    planted = ('\n\ndef _planted_route(run_dir):\n'
               f'    if {name}(run_dir, "x"):\n'
               '        return "escalate"\n')
    assert functions_naming(name, COORDINATOR_SOURCE + planted) == \
        MAY_NAME[name] | {"_planted_route"}


#: What a second read of the history directory would look like: the reader
#: itself, and the resolver that names a log's files.
A_SECOND_READ = ("read_history", "history_log_files")


def calls_in(source: str, function: str) -> set[str]:
    """The names called inside a named function of `source`."""
    tree = ast.parse(source)
    node = next(found for found in ast.walk(tree)
                if isinstance(found, ast.FunctionDef) and found.name == function)
    return {call.func.id for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}


@pytest.mark.parametrize("function", sorted(MAY_NAME))
@pytest.mark.parametrize("reader", A_SECOND_READ)
def test_the_derivation_makes_no_second_read_of_the_history(function, reader):
    """It is composed from the startup read the run already makes. The
    control is the same scan over a copy of that function with the read
    planted into it, which reports it."""
    assert reader not in calls_in(COORDINATOR_SOURCE, function)

    planted = (f'\n\ndef {function}_probe(directory, config):\n'
               f'    return {reader}(directory, config)\n')
    assert reader in calls_in(COORDINATOR_SOURCE + planted, f"{function}_probe")
