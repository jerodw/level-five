"""An operation with enough history announces how long it is expected to take.

Every long step in a run announces itself before it starts and says what the
wait is worth, and what it said was self-referential: "as long as a suite
run", "about as long as a stage". story-173 keeps every measured duration in
a declared cross-run log and reads an estimate off that log at the moment a
step announces itself — per operation, the median of a bounded window of the
most recent observations — so the line says how long rather than only that
the step is long. Where too few observations exist the step says exactly what
it said before, so a fresh target announces nothing invented.

This module holds the standing assertion in both halves and the properties
around it:

  * the estimate as a pure function of the log's records — the median, the
    window, the ordering by record timestamp, the minimum, and the records
    that are not observations;
  * the rendered clause, and the composer rendering it in place of the cost
    clause and rendering the unchanged line without one;
  * runs driven with the fake runner over a seeded duration log and over
    none: a figure derived from the history, today's wording where the
    history is absent or too thin, the window bounded at ten by timestamp
    rather than by file order, and the same routing either way;
  * every timed operation leaving exactly one record per occurrence, the
    inspection in whichever position the workflow runs it, the untimed ones
    leaving none, and the nomination selector's permission reaching the log
    that records what happened and not the one that records how long it
    took;
  * the log found through its declaration, the estimate read by nothing that
    routes, and the constants that are constants rather than configuration.

Nothing here reaches a model: the coordinator makes no model call, and the
one inspection a completing run makes reaches a fake that finds nothing. The
workflows are built by `tests/conftest.py`'s builder rather than resolved out
of what this repository ships, and the histories are seeded under the target
the run works in rather than read out of this repository's own. Every absence
asserted here sits beside a demonstration that the same check reports the
violation it exists to catch.
"""
from __future__ import annotations

import ast
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

import pytest

import agent_runner
import conftest
import harness_config
import inspection
import schema_validator
import story_coordinator
import story_inspection

import test_coordinator_runs_the_suite as suite
import test_cross_run_history as cross
import test_inspection_findings_reach_the_story as pre_stage
from test_a_run_announces_what_it_is_waiting_on import (
    INSPECTION_KIND,
    SUITE_RERUN_KIND,
    SWEEP_KIND,
    SUITE_TURN_KIND,
    history_in,
    messages_in,
)
from test_execution_history import COORDINATOR_SOURCE
from test_revert_check import (  # noqa: F401 - fixtures used by name
    NOMINATION_REPAIRED,
    enable_selection,
    forced_repair,
    harness_root as nominating_harness,
    target as nominating_target,
    with_nomination,
)
from test_revert_check import WRITING as NOMINATING_STAGE
from test_revert_check import run as nominating_run

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
STORY_ID = "story-001"

# --------------------------------------------------------------------------
# The declaration, read off the shipped schema: the subject of the assertions
# about what is declared, and the source of every field name below
# --------------------------------------------------------------------------

#: The fifth log, told apart by the pair of fields only a duration record
#: carries — the same derivation the coordinator makes, made here from the
#: schema directly so the coordinator's is checked against it below rather
#: than standing in for it.
DURATION_LOG = next(name for name, shape in cross.DECLARATIONS.items()
                    if "operation" in shape["properties"]
                    and "duration_seconds" in shape["properties"])
DECLARATION = cross.DECLARATIONS[DURATION_LOG]
REQUIRED = set(DECLARATION["required"])
TIMED_KINDS = set(cross.kinds_routed_to(DURATION_LOG))

#: The run's own history fields the projection derives rather than reads off
#: the entry. Named so `routed_entries` below can ask of an entry only what
#: the entry itself has to carry.
DERIVED = {"story_id", "status", "retry_count"}

#: The kind a stage's entry into the loop carries. The coordinator's own
#: vocabulary rather than a name a workflow declares, spelled here as the
#: modules reading the stream spell it.
STAGE_STARTED = "stage-started"

#: The clause, as the story states it and as a reader meets it. Written here
#: rather than derived from the renderer, because the claim below is that the
#: renderer produces this form and a pattern read out of it would agree with
#: itself whatever it rendered.
CLAUSE = re.compile(r" \(Estimated run time: (\d+) (seconds?|minutes?)\)$")

WINDOW = story_coordinator.DURATION_ESTIMATE_WINDOW
MINIMUM = story_coordinator.DURATION_ESTIMATE_MINIMUM


def without_clause(message: str) -> str:
    return CLAUSE.sub("", message)


def clause_of(message: str) -> str | None:
    found = CLAUSE.search(message)
    return found.group(0).strip() if found else None


@pytest.fixture(autouse=True)
def no_estimates_held_over():
    """The estimates are module state, established once per run and left in
    place when the run ends. A direct call below reads whatever the last run
    in this worker established, so each test starts and ends with none."""
    story_coordinator.set_duration_estimates({})
    yield
    story_coordinator.set_duration_estimates({})


# ==========================================================================
# The estimate as a pure function of the records
# ==========================================================================

AN_OPERATION = "an-operation-this-module-named"
ANOTHER = "another-operation-this-module-named"


def stamp(index: int, year: int = 2026) -> str:
    """A timestamp in the history's own format, later for a larger index."""
    return f"{year}-01-{1 + index // 24:02d} {index % 24:02d}:00:00"


def observation(operation: str, seconds, when: str,
                story_id: str = "story-000") -> dict:
    """One record of the declared shape, keyed by the declaration's fields."""
    record = {"story_id": story_id, "operation": operation,
              "duration_seconds": seconds, "timestamp": when}
    assert set(record) == REQUIRED, "the declared shape is not what this seeds"
    return record


def observations(operation: str, values: list, first: int = 0,
                 year: int = 2026) -> list[dict]:
    """`values` observed in order, each one hour after the last."""
    return [observation(operation, seconds, stamp(first + index, year))
            for index, seconds in enumerate(values)]


def test_the_estimate_is_the_median_of_the_operations_observations():
    records = observations(AN_OPERATION, [600, 900, 720])
    assert story_coordinator.duration_estimates(records) == {AN_OPERATION: 720.0}


def test_each_operation_is_estimated_from_its_own_observations_alone():
    records = (observations(AN_OPERATION, [100, 100, 100])
               + observations(ANOTHER, [700, 700, 700], first=3))
    assert story_coordinator.duration_estimates(records) == {
        AN_OPERATION: 100.0, ANOTHER: 700.0}


@pytest.mark.parametrize("count", [MINIMUM - 1, MINIMUM])
def test_an_operation_needs_the_minimum_before_it_has_a_figure(count: int):
    """One below the minimum has no entry; the minimum itself has one — the
    pair, so the threshold is the constant and not some other number."""
    records = observations(AN_OPERATION, [300] * count)
    estimates = story_coordinator.duration_estimates(records)
    assert (AN_OPERATION in estimates) == (count >= MINIMUM)


#: Ten values whose median moves the moment an eleventh joins them: half at
#: one figure and half at another, so the median of the ten is their midpoint
#: and the median of eleven is whichever half the eleventh lands beside.
TEN = [300] * (WINDOW // 2) + [900] * (WINDOW - WINDOW // 2)
MIDPOINT = 600.0
FAR_OUTLIER = 100_000


def test_the_window_is_bounded_and_an_older_observation_beyond_it_moves_nothing():
    """The eleventh observation is the oldest, so it falls outside the window
    and the figure is the median of the ten.

    The control is the same eleven observations with the outlier the *newest*
    instead: it is then inside the window and the figure moves, so the bound
    is doing the excluding rather than the outlier being ignored for some
    other reason.
    """
    assert len(TEN) == WINDOW
    older = observations(AN_OPERATION, [FAR_OUTLIER], first=0)
    recent = observations(AN_OPERATION, TEN, first=1)
    assert story_coordinator.duration_estimates(older + recent) == {
        AN_OPERATION: MIDPOINT}

    newest = observations(AN_OPERATION, [FAR_OUTLIER], first=1 + WINDOW)
    moved = story_coordinator.duration_estimates(recent + newest)
    assert moved[AN_OPERATION] != MIDPOINT


def test_the_window_is_taken_by_timestamp_and_not_by_the_order_records_arrive():
    """The same records handed over newest-first, oldest-first and shuffled
    give one figure: what is recent is decided by the timestamp each record
    carries, which is what lets a log gathered from segments whose names do
    not sort in time order be read correctly."""
    older = observations(AN_OPERATION, [FAR_OUTLIER], first=0)
    recent = observations(AN_OPERATION, TEN, first=1)
    in_time = older + recent
    reversed_order = list(reversed(in_time))
    interleaved = in_time[1::2] + in_time[::2]
    assert reversed_order != in_time and interleaved != in_time
    for arrangement in (in_time, reversed_order, interleaved):
        assert story_coordinator.duration_estimates(arrangement) == {
            AN_OPERATION: MIDPOINT}


def non_observations(operation: str) -> list[dict]:
    """Records of the declared shape that are not observations: a duration
    that is not a number — a string, null, a bool — and a timestamp that
    cannot be read."""
    return [
        observation(operation, "twelve", stamp(20)),
        observation(operation, None, stamp(21)),
        observation(operation, True, stamp(22)),
        observation(operation, 480, "not a timestamp"),
        observation(operation, 480, 1_700_000_000),
    ]


def test_a_record_that_is_not_an_observation_contributes_nothing_and_stops_nothing():
    """Skipped, and the figure is the median of the observations around it.
    The control is the same skipped records on their own: they are enough
    lines to clear the minimum, and yield no figure, so they are being skipped
    rather than counted. A line that is not an object at all is skipped the
    same way."""
    skipped = non_observations(AN_OPERATION) + ["not even an object"]
    valid = observations(AN_OPERATION, [600, 900, 720])
    assert story_coordinator.duration_estimates(
        skipped + valid) == {AN_OPERATION: 720.0}
    assert len(skipped) >= MINIMUM
    assert story_coordinator.duration_estimates(skipped) == {}


def test_no_records_at_all_yields_no_estimate():
    assert story_coordinator.duration_estimates([]) == {}


# ==========================================================================
# The rendered clause, and the composer
# ==========================================================================


@pytest.mark.parametrize("seconds, rendered", [
    (1, "(Estimated run time: 1 second)"),
    (12.4, "(Estimated run time: 12 seconds)"),
    (59, "(Estimated run time: 59 seconds)"),
    (60, "(Estimated run time: 1 minute)"),
    (80, "(Estimated run time: 1 minute)"),
    (180, "(Estimated run time: 3 minutes)"),
    (200, "(Estimated run time: 3 minutes)"),
    (720, "(Estimated run time: 12 minutes)"),
])
def test_the_clause_says_whole_minutes_or_whole_seconds_under_a_minute(
        seconds, rendered):
    assert story_coordinator.estimated_run_time(seconds) == rendered


def test_a_composed_line_with_an_estimate_carries_the_clause_in_place_of_the_cost():
    assert story_coordinator._announcement(
        "doing the long thing", "this takes a while", estimate=240) == \
        "doing the long thing (Estimated run time: 4 minutes)"


def test_a_composed_line_without_an_estimate_is_what_it_was():
    """The two halves and the one separator, unchanged: the control for the
    line above, differing in the estimate alone."""
    assert story_coordinator._announcement(
        "doing the long thing", "this takes a while") == \
        "doing the long thing; this takes a while"
    assert story_coordinator._announcement(
        "doing the long thing", "this takes a while", estimate=None) == \
        "doing the long thing; this takes a while"


@pytest.mark.parametrize("cost", ["", " ", "\n\t "])
def test_an_estimate_does_not_excuse_an_empty_cost_clause(cost: str):
    """Refused with an estimate exactly as without one: the figure is present
    only where the history happens to hold enough, so a call that leaned on
    it would say nothing about the wait on a fresh target."""
    with pytest.raises(ValueError):
        story_coordinator._announcement("doing the long thing", cost,
                                        estimate=240)


def test_the_suite_rerun_announcer_reads_the_estimate_under_its_operation(
        tmp_path):
    """The announcer with an estimate held for the operation it names renders
    the figure; the same call naming no operation, and the same call naming
    one nothing is held for, render the clause they rendered before."""
    story_coordinator.set_duration_estimates({AN_OPERATION: 240.0})
    phrase = "for a reason this module made up"
    lines = {}
    for label, operation in (("named", AN_OPERATION), ("other", ANOTHER),
                             ("none", None)):
        run_dir = tmp_path / label
        run_dir.mkdir()
        story_coordinator._suite_rerun_started(
            run_dir, "a-stage", "an-artifact.json", phrase,
            operation=operation)
        [lines[label]] = messages_in(run_dir)

    assert lines["named"] == \
        f"re-running the suite {phrase} (Estimated run time: 4 minutes)"
    assert lines["other"] == lines["none"]
    assert clause_of(lines["none"]) is None
    assert lines["none"].startswith(f"re-running the suite {phrase}; ")


# ==========================================================================
# Runs over a seeded duration log, and over none
# ==========================================================================

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="estimated-run-time-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VERIFYING = STAGE_NAMES

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "estimated-run-time-harness")


class NoModel:
    """Stands in for `agent_runner.run_agent`, which the coordinator must
    never reach: only a completing run's inspection does, and the runs below
    have it switched off."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("the coordinator reached a model")


@pytest.fixture
def no_model(monkeypatch) -> NoModel:
    stand_in = NoModel()
    monkeypatch.setattr(agent_runner, "run_agent", stand_in)
    return stand_in


class Runner:
    """A fake agent runner that writes each stage's declared artifacts."""

    def __init__(self, target_root: Path):
        self.run_dir = conftest.run_dir_for(target_root, STORY_ID)
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None,
                 run_dir=None):
        self.calls.append(stage)
        if stage == WRITING:
            cross.write_json(self.run_dir / conftest.CHANGED_FILES, {
                "modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Implemented.\n", encoding="utf-8")
            (Path(cwd) / "src" / "app.py").write_text(
                "print('changed')\n", encoding="utf-8")
        elif stage == VERIFYING:
            cross.write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
        return agent_runner.AgentResult(ok=True, result_text=f"{stage} done")


def history_dir_of(target_root: Path, story_id: str = STORY_ID) -> Path:
    """Where the run reads and writes the cross-run history: in the tree the
    run works in, resolved through the harness's own resolver."""
    return harness_config.history_dir(
        conftest.run_root_for(Path(target_root), story_id),
        harness_config.load_config(target_root))


def seed(target_root: Path, segments: dict[str, list[dict]],
         story_id: str = STORY_ID) -> None:
    """Write a duration log the run did not produce, one file per segment.

    The tree is created as the run would create it and the files are
    committed there, because a run refuses a dirty tree and the seeding may
    not be what refuses it.
    """
    tree = conftest.worktree_a_run_left(target_root, story_id)
    history = history_dir_of(target_root, story_id)
    for segment, records in segments.items():
        path = history / segment / DURATION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records),
                        encoding="utf-8")
    conftest.commit_setup(tree, "a duration history this run did not produce")


def duration_records(target_root: Path, story_id: str = STORY_ID) -> list[dict]:
    """Every record of the duration log, gathered as the run gathers it."""
    return [json.loads(line)
            for path in story_coordinator.history_log_files(
                history_dir_of(target_root, story_id), DURATION_LOG)
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def run(target_root: Path, harness: Path) -> tuple[int, Runner, Path]:
    runner = Runner(target_root)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner, conftest.run_dir_for(target_root, STORY_ID)


def stage_started_lines(run_dir: Path) -> dict[str, str]:
    """Each stage's entry line, from the run's own history, keyed by stage."""
    found = {}
    for entry in history_in(run_dir):
        if entry["event"] == STAGE_STARTED:
            found[entry["stage"]] = entry["message"]
    return found


def test_a_run_over_no_history_announces_every_stage_bare(
        target_root, harness_root, no_model):
    """The fallback, exercised rather than assumed: no history, and every
    stage-started line is the stage's name and nothing more."""
    assert duration_records(target_root) == []
    code, runner, run_dir = run(target_root, harness_root)
    assert code == 0 and runner.calls == STAGE_NAMES
    assert no_model.calls == 0

    assert stage_started_lines(run_dir) == {
        name: f"{name} stage started" for name in STAGE_NAMES}
    assert [line for line in messages_in(run_dir) if clause_of(line)] == []


#: Three observations of the writing stage whose median is twelve minutes.
#: Seeded under a segment of the history named for an earlier story.
SEEDED = [600, 900, 720]
SEEDED_CLAUSE = "(Estimated run time: 12 minutes)"


def test_a_run_over_enough_history_announces_the_median_on_that_stage_alone(
        target_root, harness_root, no_model):
    """The standing claim's first half, and its control in the same run: the
    stage with enough observations announces the figure, in exactly the
    story's form, and the stage with none announces what it announced before."""
    seed(target_root, {"story-000": observations(WRITING, SEEDED)})
    code, runner, run_dir = run(target_root, harness_root)
    assert code == 0 and runner.calls == STAGE_NAMES
    assert no_model.calls == 0

    lines = stage_started_lines(run_dir)
    assert lines[WRITING] == f"{WRITING} stage started {SEEDED_CLAUSE}"
    assert lines[VERIFYING] == f"{VERIFYING} stage started"
    # The same line, in the events log a person watches.
    assert f"{WRITING} stage started {SEEDED_CLAUSE}" in messages_in(run_dir)


@pytest.mark.parametrize("count", [MINIMUM - 1, MINIMUM])
def test_a_run_announces_a_figure_at_the_minimum_and_not_one_below_it(
        target_root, harness_root, no_model, count):
    seed(target_root, {"story-000": observations(WRITING, [720] * count)})
    code, _, run_dir = run(target_root, harness_root)
    assert code == 0

    line = stage_started_lines(run_dir)[WRITING]
    if count >= MINIMUM:
        assert line == f"{WRITING} stage started {SEEDED_CLAUSE}"
    else:
        assert line == f"{WRITING} stage started"


def test_an_eleventh_older_observation_does_not_move_the_figure_a_run_announces(
        target_root, harness_root, no_model):
    """The window at run level: ten recent observations and one far outlier
    older than all of them, and the run announces the median of the ten."""
    seed(target_root, {"story-000": (
        observations(WRITING, [FAR_OUTLIER], first=0)
        + observations(WRITING, TEN, first=1))})
    code, _, run_dir = run(target_root, harness_root)
    assert code == 0
    assert stage_started_lines(run_dir)[WRITING] == \
        f"{WRITING} stage started (Estimated run time: 10 minutes)"


#: Two segments whose names sort the other way round from their records'
#: timestamps: the segment sorting first holds the newer observations. So the
#: last ten in file order and the most recent ten by timestamp are different
#: sets, and the figure says which one the run took.
NEWER_SEGMENT = "story-000-a"
OLDER_SEGMENT = "story-000-z"
NEWER_VALUES = [600] * WINDOW
OLDER_VALUES = [60] * WINDOW


def test_the_window_is_the_most_recent_by_timestamp_across_out_of_order_segments(
        target_root, harness_root, no_model):
    seed(target_root, {
        NEWER_SEGMENT: observations(WRITING, NEWER_VALUES, year=2026),
        OLDER_SEGMENT: observations(WRITING, OLDER_VALUES, year=2025),
    })
    # The premise: in file order the older records really do come last.
    files = story_coordinator.history_log_files(
        history_dir_of(target_root), DURATION_LOG)
    assert [path.parent.name for path in files] == [NEWER_SEGMENT, OLDER_SEGMENT]

    code, _, run_dir = run(target_root, harness_root)
    assert code == 0
    assert stage_started_lines(run_dir)[WRITING] == \
        f"{WRITING} stage started (Estimated run time: 10 minutes)"


def test_a_seeded_record_that_is_not_an_observation_neither_counts_nor_stops_the_run(
        target_root, harness_root, no_model):
    """Lines that are not observations of the stage, beside one fewer real
    observation than the minimum: the run completes, and the stage announces
    bare, because the lines that are not observations did not make up the
    shortfall. The control is the same lines beside enough observations, in
    the test below, where the run announces the median of the real ones."""
    seed(target_root, {"story-000": (
        non_observations(WRITING)
        + observations(WRITING, SEEDED[:MINIMUM - 1]))})
    code, runner, run_dir = run(target_root, harness_root)
    assert code == 0 and runner.calls == STAGE_NAMES
    assert stage_started_lines(run_dir)[WRITING] == f"{WRITING} stage started"


def test_the_same_non_observations_beside_enough_observations_still_yield_the_figure(
        target_root, harness_root, no_model):
    seed(target_root, {"story-000": (
        non_observations(WRITING) + observations(WRITING, SEEDED))})
    code, _, run_dir = run(target_root, harness_root)
    assert code == 0
    assert stage_started_lines(run_dir)[WRITING] == \
        f"{WRITING} stage started {SEEDED_CLAUSE}"


def test_a_seeded_log_changes_the_announcement_text_and_nothing_else(
        target_root, harness_root, tmp_path, no_model):
    """Two identical runs, one over a seeded duration log and one over none:
    the same stage sequence, retry count, exit code, recorded status and
    model calls, and an event stream that differs only in the clause.

    The control is that the streams *do* differ before the clause is taken
    out, so the equality after is the clause being the whole difference
    rather than two runs that were identical anyway.
    """
    seeded = tmp_path / "seeded-target"
    shutil.copytree(target_root, seeded, symlinks=True)
    seed(seeded, {"story-000": observations(WRITING, SEEDED)
                  + observations(VERIFYING, SEEDED, first=3)})

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
    assert [e["event"] for e in plain] == [e["event"] for e in seeded_history]
    assert [e.get("stage") for e in plain] == \
        [e.get("stage") for e in seeded_history]
    plain_messages = [e["message"] for e in plain]
    seeded_messages = [e["message"] for e in seeded_history]
    assert plain_messages != seeded_messages
    assert plain_messages == [without_clause(m) for m in seeded_messages]
    assert [m for m in plain_messages if clause_of(m)] == []
    assert [m for m in seeded_messages if clause_of(m)] == [
        f"{WRITING} stage started {SEEDED_CLAUSE}",
        f"{VERIFYING} stage started {SEEDED_CLAUSE}",
    ]


# ==========================================================================
# Every timed operation leaves one record per occurrence
# ==========================================================================

#: The workflow of `tests/test_coordinator_runs_the_suite.py`'s all-three
#: fixture with the suite census declared on the same stage, so one run makes
#: every timed check: the revert check's whole-suite fallthrough, the census
#: and the declared suite run on the writing stage, and the clean-clone check
#: after the verdict. The post-story inspection is turned on in the target.
CENSUS_ARTIFACT = "census-probe-result.json"

EVERY_TIMED = conftest.build_workflow(
    suite.declaring_stage(
        may_not_create=(suite.GOVERNED_PREFIX,),
        revert_check={"result": suite.REVERT_ARTIFACT,
                      "baseline": "stage-baseline"},
        suite_census={"result": CENSUS_ARTIFACT,
                      "baseline": "stage-baseline",
                      "paths": [suite.GOVERNED_PREFIX]}),
    suite.verifying_stage(),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="every-timed-operation-workflow")

TIMED_STAGES = [stage["name"] for stage in EVERY_TIMED["stages"]]
TIMED_WRITING, TIMED_VERIFYING = TIMED_STAGES
CLEAN_CLONE_ARTIFACT = next(s["clean_clone"]["result"]
                            for s in EVERY_TIMED["stages"] if "clean_clone" in s)

#: A census that prints one constant counter, so the check runs and permits
#: and the two censuses it takes are the only thing this module needs of it.
CENSUS_SCRIPT = 'import json\nprint(json.dumps({"constant": 1}))\n'
CENSUS_SCRIPT_REL = "census.py"

#: What the run's every timed operation is recorded under, derived from the
#: fixture's stage names and the coordinator's fixed names for the rest.
EVERY_OPERATION = sorted([
    TIMED_WRITING, TIMED_VERIFYING,
    story_coordinator.REVERT_CHECK_OPERATION,
    story_coordinator.SUITE_CENSUS_OPERATION,
    story_coordinator.SUITE_RUN_OPERATION,
    story_coordinator.CLEAN_CLONE_OPERATION,
    story_coordinator.INSPECTION_OPERATION,
])


def every_timed_target(tmp_path: Path) -> Path:
    root = suite.build_suite_target(tmp_path / "every-timed",
                                    workflow=EVERY_TIMED["name"])
    suite.write(root / CENSUS_SCRIPT_REL, CENSUS_SCRIPT)
    config = root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"census_command: {shlex.join([sys.executable, str(root / CENSUS_SCRIPT_REL)])}\n"
        + f"{inspection.SOURCE_DIRS_KEY}:\n  - {suite.GOVERNED_PREFIX}\n"
        + f"{story_inspection.MAX_FILES_KEY}: {cross.INSPECTION_FILE_CAP}\n",
        encoding="utf-8")
    conftest.commit_setup(root, "the census and the inspection this run makes")
    return root


def every_timed_harness(tmp_path: Path) -> Path:
    root = suite.materialize(EVERY_TIMED, tmp_path / "every-timed-harness")
    (root / "prompts" / inspection.INSPECTOR_PROMPT).write_text(
        "# the template this fixture renders\n{{scope_paths}}\n",
        encoding="utf-8")
    return root


#: The two operations the every-timed run is seeded with enough history for,
#: so the same run shows a check's announcement carrying a figure beside the
#: checks announcing what they announced before.
SEEDED_CHECK = story_coordinator.CLEAN_CLONE_OPERATION
SEEDED_INSPECTION = story_coordinator.INSPECTION_OPERATION


@pytest.fixture
def every_timed(tmp_path, monkeypatch):
    """One completing run making every timed operation, over a history that
    holds enough observations of the clean-clone check and the inspection and
    none of anything else."""
    inspector = cross.NoFindings()
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    target = every_timed_target(tmp_path)
    seed(target, {"story-000": (
        observations(SEEDED_CHECK, [240, 240, 240])
        + observations(SEEDED_INSPECTION, [30, 45, 40], first=3))})
    harness = every_timed_harness(tmp_path)
    runner = suite.Runner(target, workflow=EVERY_TIMED)
    code = story_coordinator.run_story(STORY_ID, harness, target, runner)
    assert code == 0, conftest.run_dir_for(target, STORY_ID)
    assert runner.calls == TIMED_STAGES
    assert inspector.calls == 1, "the inspection made no invocation"
    return target, conftest.run_dir_for(target, STORY_ID)


def this_runs_records(target: Path) -> list[dict]:
    return [record for record in duration_records(target)
            if record.get("story_id") == STORY_ID]


def routed_entries(run_dir: Path) -> list[dict]:
    """The run's own entries the duration log's declaration selects: a kind
    its enum names, carrying every field it requires that the projection does
    not derive."""
    asked = REQUIRED - DERIVED
    return [entry for entry in story_coordinator.load_history(run_dir)
            if entry.get("event") in TIMED_KINDS
            and asked <= set(entry)]


def test_every_timed_operation_leaves_exactly_one_record(every_timed):
    """Each stage — the one that judges included, since its stage-started
    line reads an estimate under its own name like any other — each timed
    check, and the inspection: one record each, and no other."""
    target, run_dir = every_timed
    records = this_runs_records(target)
    left = sorted(record["operation"] for record in records)
    assert left == EVERY_OPERATION, (
        f"operations with no record: {sorted(set(EVERY_OPERATION) - set(left))}; "
        f"recorded more than once or unexpected: "
        f"{sorted(set(left) - set(EVERY_OPERATION)) or [o for o in set(left) if left.count(o) > 1]}")
    assert len(records) == len(routed_entries(run_dir))


def test_each_record_carries_exactly_the_declared_fields_and_a_numeric_duration(
        every_timed):
    target, _ = every_timed
    records = this_runs_records(target)
    assert records
    for record in records:
        assert set(record) == REQUIRED, record
        seconds = record["duration_seconds"]
        assert isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
        assert seconds >= 0
        assert cross.EVENT not in record


def test_no_entry_of_any_other_kind_reaches_the_log(every_timed):
    """The run emitted kinds the log does not name and entries of named kinds
    that carry no duration — the announcements, the stage starts — and none
    of them is a record. The control is the projection itself: an entry of an
    unrouted kind carrying the duration fields still writes nothing, and an
    entry of a routed kind missing them writes nothing, while the same entry
    carrying both writes a record."""
    target, run_dir = every_timed
    entries = story_coordinator.load_history(run_dir)
    assert {e["event"] for e in entries} - TIMED_KINDS, \
        "the run emitted only timed kinds, so there is nothing to keep out"
    assert len(this_runs_records(target)) == len(routed_entries(run_dir))

    fields = {"operation": AN_OPERATION, "duration_seconds": 12.5}
    unrouted = sorted(set(cross.RUN_KINDS) - TIMED_KINDS)[0]
    routed = sorted(TIMED_KINDS)[0]
    base = {"sequence": 1, "timestamp": stamp(0), "message": "an entry"}
    assert story_coordinator.history_record(
        {**base, cross.EVENT: unrouted, **fields}, [], STORY_ID, DECLARATION
    ) is None
    assert story_coordinator.history_record(
        {**base, cross.EVENT: routed}, [], STORY_ID, DECLARATION) is None
    record = story_coordinator.history_record(
        {**base, cross.EVENT: routed, **fields}, [], STORY_ID, DECLARATION)
    assert record == {"story_id": STORY_ID, "timestamp": stamp(0), **fields}


def test_a_check_with_enough_history_announces_the_figure_and_the_others_do_not(
        every_timed):
    """The standing claim at the checks: the clean-clone check, seeded with
    enough observations, announces the figure in place of its clause; the
    three checks with none announce with the clause they had; the inspection,
    seeded under a minute, says seconds."""
    _, run_dir = every_timed
    reruns = [e for e in history_in(run_dir) if e["event"] == SUITE_RERUN_KIND]
    assert len(reruns) == 4, [e["message"] for e in reruns]
    clean = [e for e in reruns if e["artifacts"] == [CLEAN_CLONE_ARTIFACT]]
    others = [e for e in reruns if e["artifacts"] != [CLEAN_CLONE_ARTIFACT]]
    assert len(clean) == 1 and len(others) == 3

    assert clause_of(clean[0]["message"]) == "(Estimated run time: 4 minutes)"
    assert "; " not in clean[0]["message"]
    for entry in others:
        assert clause_of(entry["message"]) is None, entry["message"]
        assert "; this takes " in entry["message"], entry["message"]

    [inspecting] = [e for e in history_in(run_dir)
                    if e["event"] == INSPECTION_KIND]
    assert clause_of(inspecting["message"]) == "(Estimated run time: 40 seconds)"
    assert story_coordinator.INSPECTION_COST not in inspecting["message"]


def test_the_run_wrote_its_own_records_beneath_the_seeded_ones(every_timed):
    """The seeded records are untouched and still lead: the log was appended
    to and never rewritten, and nothing read them back into a decision."""
    target, _ = every_timed
    records = duration_records(target)
    seeded = [r for r in records if r["story_id"] != STORY_ID]
    assert len(seeded) == 6
    assert records[:len(seeded)] == seeded


# ==========================================================================
# The inspection is timed in whichever position the workflow runs it
# ==========================================================================

#: The pre-stage fixture of `tests/test_inspection_findings_reach_the_story.py`:
#: a workflow whose judging stage declares the inspection, so it runs before
#: that stage on every attempt rather than once after the run. Both positions
#: share one invocation, so the record is under the one name either way.
PRE_STAGE_STORY_ID = pre_stage.STORY_ID
assert PRE_STAGE_STORY_ID == STORY_ID, "the seeded history is keyed by story"


@pytest.fixture
def pre_stage_target(tmp_path) -> Path:
    return pre_stage.build_target(tmp_path / "pre-stage-target")


@pytest.fixture
def pre_stage_harness(tmp_path) -> Path:
    return pre_stage.materialize(pre_stage.WORKFLOW,
                                 tmp_path / "pre-stage-harness")


def inspection_records(target: Path) -> list[dict]:
    return [record for record in this_runs_records(target)
            if record["operation"] == story_coordinator.INSPECTION_OPERATION]


def test_the_pre_stage_inspection_leaves_one_record_per_attempt(
        pre_stage_target, pre_stage_harness, tmp_path, no_model):
    """A failed verdict routes a retry, so the run reaches attempt 2 and the
    judging stage is inspected twice: two invocations, and two `inspection`
    records for this story — one per occurrence, in the pre-stage position,
    under the same name the post-story position records under.

    The control is the run's own count of invocations, which the records must
    equal rather than merely be non-empty; and the every-timed run above,
    whose one post-story inspection left one.
    """
    run = pre_stage.drive(
        pre_stage_target, pre_stage_harness, tmp_path,
        verdicts=[pre_stage.failing_into(pre_stage.RETRY_CATEGORY),
                  pre_stage.PASS])
    assert run.code == 0, run.messages
    assert run.state["retry_count"] == 1
    assert len(run.inspector.invocations) == 2
    assert no_model.calls == 0

    records = inspection_records(pre_stage_target)
    assert len(records) == len(run.inspector.invocations)
    for record in records:
        assert set(record) == REQUIRED, record
        seconds = record["duration_seconds"]
        assert isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
    # The records are of the pre-stage position: read off the shared journal,
    # each invocation immediately precedes an entry to the judging stage, and
    # the run's last line is a stage rather than an inspection after it.
    lines = run.lines
    assert lines.count("inspected") == 2
    for index, line in enumerate(lines):
        if line == "inspected":
            assert lines[index + 1] == f"stage {pre_stage.JUDGING}", lines
    assert lines[-1] != "inspected"


def test_the_pre_stage_inspection_announces_with_the_figure_its_history_holds(
        pre_stage_target, pre_stage_harness, tmp_path, no_model):
    """The same announcer in the other position: seeded with enough
    observations of the inspection, the pre-stage announcement carries the
    figure in place of its cost clause, and the same run over no history — the
    test above — announces it with the clause it had."""
    seed(pre_stage_target, {"story-000": observations(
        story_coordinator.INSPECTION_OPERATION, [30, 45, 40])})
    run = pre_stage.drive(pre_stage_target, pre_stage_harness, tmp_path)
    assert run.code == 0, run.messages

    inspecting = [e for e in history_in(run.run_dir)
                  if e["event"] == INSPECTION_KIND]
    assert len(inspecting) == 1
    assert clause_of(inspecting[0]["message"]) == "(Estimated run time: 40 seconds)"
    assert story_coordinator.INSPECTION_COST not in inspecting[0]["message"]
    assert len(inspection_records(pre_stage_target)) == 1


# ==========================================================================
# The untimed steps leave no record
# ==========================================================================


def test_a_nomination_the_selector_decided_leaves_no_duration_record(
        nominating_target, nominating_harness):
    """The revert check's nomination selector permits the edits without the
    whole suite, and that permission is recorded where a permission is
    recorded and not as an observation of the revert check.

    The control is the every-timed run above, whose revert check fell through
    to the whole suite and did leave one; and within this run, the stages and
    the clean-clone check, which leave theirs.
    """
    enable_selection(nominating_target)
    code, _ = nominating_run(
        nominating_target, nominating_harness,
        {NOMINATING_STAGE: with_nomination(forced_repair, NOMINATION_REPAIRED)})
    assert code == 0

    run_dir = conftest.run_dir_for(nominating_target, STORY_ID)
    [permitted] = [e for e in history_in(run_dir)
                   if e["event"] == "revert-check-permitted"]
    assert "operation" not in permitted and "duration_seconds" not in permitted

    operations = [r["operation"] for r in duration_records(nominating_target)]
    assert story_coordinator.REVERT_CHECK_OPERATION not in operations
    assert story_coordinator.CLEAN_CLONE_OPERATION in operations
    assert NOMINATING_STAGE in operations


def test_the_sweep_and_the_suite_turn_wait_are_named_by_no_duration_log(
        tmp_path):
    """Their kinds are outside the log's enum, so an entry of either kind
    writes no record even when it carries the duration fields; the control is
    the same entry under a kind the enum names, which does."""
    fields = {"operation": AN_OPERATION, "duration_seconds": 12.5}
    base = {"sequence": 1, "timestamp": stamp(0), "message": "a wait"}
    for kind in (SWEEP_KIND, SUITE_TURN_KIND):
        assert kind not in TIMED_KINDS
        assert story_coordinator.history_record(
            {**base, cross.EVENT: kind, **fields}, [], STORY_ID, DECLARATION
        ) is None, kind
    assert story_coordinator.history_record(
        {**base, cross.EVENT: sorted(TIMED_KINDS)[0], **fields},
        [], STORY_ID, DECLARATION) is not None


# ==========================================================================
# Where the log is found, what reads the estimate, and what is a constant
# ==========================================================================


def test_the_coordinator_finds_the_log_through_its_declared_fields():
    """Located by the pair of fields only a duration record carries, never by
    filename: the same declarations with that pair removed from the log
    locate nothing, and a declaration set naming the log under another
    filename still locates it."""
    assert story_coordinator.duration_log_name(cross.DECLARATIONS) == DURATION_LOG

    stripped = json.loads(json.dumps(cross.DECLARATIONS))
    del stripped[DURATION_LOG]["properties"]["operation"]
    assert story_coordinator.duration_log_name(stripped) is None

    renamed = {("another-name.jsonl" if name == DURATION_LOG else name): shape
               for name, shape in cross.DECLARATIONS.items()}
    assert story_coordinator.duration_log_name(renamed) == "another-name.jsonl"


def test_the_estimate_is_read_only_by_the_announcers():
    """Every read of an estimate in the coordinator is handed straight to the
    composer or to the stage-started line's renderer, with one sanctioned
    exception named below, so no verdict, check, retry or routing decision
    can be reading it. The control is a planted function that routes on one,
    which the same reader reports — and the same read planted into a function
    that is not the sanctioned one, which it reports too."""
    assert estimate_reads_outside_announcements(COORDINATOR_SOURCE) == []
    assert estimate_reads_in(COORDINATOR_SOURCE), \
        "the scan found no read at all, so it is looking nowhere"

    planted = ('\n\ndef _planted_route(run_dir):\n'
               '    if duration_estimate("x") > 5:\n'
               '        return "escalate"\n')
    assert estimate_reads_outside_announcements(COORDINATOR_SOURCE + planted) == [
        "duration_estimate('x')"]

    # A read that is not routed on and not handed to a renderer is still
    # reported when it sits in a function other than the sanctioned one: the
    # exemption is by name, and a second bound reading the estimate is a
    # second reader.
    planted_bound = ('\n\ndef _planted_bound(config):\n'
                     '    return 2 * duration_estimate("y")\n')
    assert estimate_reads_outside_announcements(
        COORDINATOR_SOURCE + planted_bound) == ["duration_estimate('y')"]

    inspecting = (REPO_ROOT / "orchestration" / "story_inspection.py").read_text(
        encoding="utf-8")
    assert estimate_reads_in(inspecting) == []
    assert "_duration_estimates" not in inspecting


def test_the_one_sanctioned_reader_is_the_suite_turn_wait_and_it_says_why():
    """The exemption is held shut from both sides: the sanctioned function
    exists, actually reads the estimate (so the exemption is not stale), reads
    it for the suite-run operation and no other, and its docstring states
    why a wait's length may read the estimate where a verdict may not. Every
    other read in the coordinator is still handed to a renderer, so the
    exemption covers nothing but the wait."""
    assert SANCTIONED_READERS == {"suite_turn_wait_seconds"}
    tree = ast.parse(COORDINATOR_SOURCE)
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    for name in SANCTIONED_READERS:
        assert name in functions
        reads = reads_in(functions[name])
        assert reads, f"{name} reads no estimate, so its exemption is stale"
        assert {ast.unparse(read.args[0]) for read in reads} == \
            {"SUITE_RUN_OPERATION"}
        docstring = ast.get_docstring(functions[name]) or ""
        assert "verdict" in docstring and "wait" in docstring
        assert "History is evidence" in docstring
    # Every read outside the sanctioned function is handed to a renderer:
    # the same scan with the exemption emptied reports exactly the sanctioned
    # function's reads and nothing else.
    every_read = estimate_reads_outside_announcements(
        COORDINATOR_SOURCE, sanctioned=frozenset())
    sanctioned_reads = [ast.unparse(read) for name in SANCTIONED_READERS
                        for read in reads_in(functions[name])]
    assert every_read == sanctioned_reads


#: The two renderers an estimate may be handed to. The composer, and the
#: renderer the stage-started line uses because it carries no cost clause.
RENDERERS = {"_announcement", "_with_estimate"}

#: The one function outside the renderers that may read an estimate: the
#: bound on waiting for a turn at the suite, since story-190 a configured
#: count of suite runs times what one has taken here. A wait's length is not
#: a verdict, a check, a retry or an escalation — it decides how long a run
#: waits for a turn and nothing about what the run does — so it may read
#: history where those may not. The exemption is by name, so a second bound
#: reading the estimate is a second reader and is reported.
SANCTIONED_READERS = frozenset({"suite_turn_wait_seconds"})


def reads_in(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "duration_estimate"]


def estimate_reads_in(source: str) -> list[ast.Call]:
    return reads_in(ast.parse(source))


def estimate_reads_outside_announcements(
        source: str, sanctioned: frozenset[str] = SANCTIONED_READERS) -> list[str]:
    tree = ast.parse(source)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def enclosing_function(node: ast.AST) -> str | None:
        while node is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
            node = parents.get(id(node))
        return None

    loose = []
    for read in reads_in(tree):
        if enclosing_function(read) in sanctioned:
            continue
        parent = parents.get(id(read))
        if isinstance(parent, ast.keyword):
            parent = parents.get(id(parent))
        handed_to_a_renderer = (isinstance(parent, ast.Call)
                                and isinstance(parent.func, ast.Name)
                                and parent.func.id in RENDERERS)
        if not handed_to_a_renderer:
            loose.append(ast.unparse(read))
    return loose


def test_the_estimates_table_is_touched_only_by_its_own_two_functions():
    """The module state is written by one function and read by one, so the
    figure a run announces with was established once from the startup read
    and nowhere else."""
    touching = functions_naming("_duration_estimates", COORDINATOR_SOURCE)
    # The module-level definition is the one mention outside a function.
    assert None in touching
    assert sorted(touching - {None}) == \
        ["duration_estimate", "set_duration_estimates"]


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


def test_the_window_and_the_minimum_are_the_storys_constants_and_not_configuration():
    """The two figures the story states, and the absence of a key for either
    in the target's own configuration schema — with a copy of that schema
    carrying such a key as the control for the absence."""
    assert WINDOW == 10
    assert MINIMUM == 3
    schema = schema_validator.load_schema("harness-config")
    assert estimate_keys_in(schema) == []
    planted = json.loads(json.dumps(schema))
    planted["properties"]["duration_estimate_window"] = {"type": "integer"}
    assert estimate_keys_in(planted) == ["duration_estimate_window"]


def estimate_keys_in(schema: dict) -> list[str]:
    return sorted(key for key in schema.get("properties", {})
                  if "estimate" in key)


def test_the_operation_field_and_the_new_kinds_are_declared_for_the_runs_own_history():
    """What the run's own history declares: the field a completion entry
    carries beside its duration, and the enum naming every kind the duration
    log routes — so a kind the duration log names is one the run can emit."""
    run_schema = schema_validator.load_schema("execution-history")
    properties = run_schema["items"]["properties"]
    assert "operation" in properties
    assert properties["operation"]["type"] == "string"
    assert "operation" not in run_schema["items"].get("required", [])
    assert TIMED_KINDS <= set(properties[cross.EVENT]["enum"])
