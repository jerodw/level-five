"""Independent validation for story-167: suite runs in flight at once take the
machine in turn rather than all at once.

Written from the acceptance criteria rather than from the implementation. The
subjects, kept apart:

  * **two runs of one repository.** Driven for real, in two processes, against
    two worktrees of one constructed repository whose configured test command
    records the interval it was running and holds until the test releases it.
    What is asserted is the intervals the command itself recorded, never the
    order of events: the second run's command did not start while the first
    run's was held, and no two recorded intervals overlap.

  * **the scope of the turn.** The lock is one per repository: a worktree of a
    repository derives the same lock file as its primary tree, an unrelated
    repository derives another, and a run of the second is not delayed by a
    turn held on the first — which is what keeps this harness from waiting on
    itself when its own suite drives the coordinator on constructed targets.

  * **the ways the turn fails toward running.** A holder that was killed, a
    lock file that cannot be created or opened, and a bound that expired are
    each shown running the suite, recording the result it would have recorded
    with the turn, and saying in the events log why no turn was taken.

  * **the bound.** Observed through an injected clock and an injected sleep, so
    nothing here waits: the wait ends at the configured number of minutes and
    not before, a holder that lets go inside the bound is waited for, and an
    absent key waits up to sixty minutes.

  * **what the turn does not touch.** The configured command runs exactly as
    configured on every path, no module under `orchestration/` names the
    worker-count option, and the selector runs of a nomination and the census
    take no turn at all.

  * **the shipped artifacts.** The new event kind is in the execution-history
    enum and in no cross-run log; the key is declared; the kind is a literal in
    one function body. These are the live artifacts and the subject of the
    assertions that read them.

Every absence asserted here sits beside a demonstration that it can fail:

  * "no two intervals overlap" sits beside the same reading over a constructed
    pair that does, which it reports;
  * "an uncontended run writes no wait event" sits beside a contended run of
    the same fixture, which writes exactly one per whole-suite run;
  * "a run of an unrelated repository does not wait" sits beside the same run
    with its own lock held, which does;
  * "a killed holder's lock is free" sits beside the same holder alive, under
    which the same turn is refused;
  * "the selector runs and the census take no turn" sits beside the clean-clone
    check under the same held lock, which takes one;
  * "no orchestration module names the option" sits beside a planted module
    that does, which the same scan reports.

The wall clock is read here for one purpose: preconditions. A process has to
have started its command before anything can be asked about whether a second
one waited on it, and the two have to have finished before their records can
be read. A precondition the machine did not let the run reach is reported
through `machine_load.inconclusive`, never failed and never bounded by a
constant chosen for this machine.
"""
from __future__ import annotations

import ast
import contextlib
import fcntl
import json
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import conftest
import harness_config
import machine_load
import schema_validator
import story_coordinator
import worktrees
from test_coordinator_runs_the_suite import (
    DECLARING, SUITE_ARTIFACT, VERIFYING, WORKFLOW, build_suite_target, drive,
    history_of, materialize, record_of, run_dir_of,
)
from test_self_routing_retry import build_target, write
from test_suite_parallelism import worker_count

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
COORDINATOR_SOURCE = Path(story_coordinator.__file__).read_text(encoding="utf-8")

#: The kind this story adds, read off the shipped schema's enum below rather
#: than trusted from here: the assertion that it is declared is what makes
#: every later use of this name a use of a declared kind.
WAITED = "suite-turn-waited"
RERUN_STARTED = "suite-rerun-started"
NOTE = "note"

#: The key this story adds, and the reader the coordinator answers it through.
KEY = "suite_turn_wait_minutes"

#: How long a test waits for a precondition — a process to have started its
#: command, a run to have written its wait, two runs to have finished. A
#: precondition and not the question: a run that has not got there is reported
#: through `machine_load`, not failed.
PRECONDITION_SECONDS = 180

#: How long the recording command holds if nothing releases it, so a test that
#: never releases leaves no process behind for the length of the suite. Well
#: past the precondition above, so no assertion here ever meets it.
HOLD_CEILING_SECONDS = 600


# --------------------------------------------------------------------------
# Reading events
# --------------------------------------------------------------------------


def entries_of(run_dir: Path) -> list[dict]:
    """The run's structured history; a run that has written no event yet has
    an empty one, which is the emptiness an uncontended turn leaves."""
    if not (run_dir / "execution-history.json").is_file():
        return []
    return history_of(run_dir)


def kinds_of(run_dir: Path) -> list[str]:
    return [entry["event"] for entry in entries_of(run_dir)]


def entries_of_kind(run_dir: Path, kind: str) -> list[dict]:
    return [entry for entry in entries_of(run_dir) if entry["event"] == kind]


def log_lines(run_dir: Path) -> list[str]:
    return (run_dir / "events.log").read_text(encoding="utf-8").splitlines()


def notes_about_the_turn(run_dir: Path) -> list[str]:
    """The note events that say the suite ran without a turn, and why."""
    return [entry["message"] for entry in entries_of_kind(run_dir, NOTE)
            if "suite" in entry["message"] and "turn" in entry["message"]]


# --------------------------------------------------------------------------
# Holding a lock, and observing a turn without waiting
# --------------------------------------------------------------------------


@contextlib.contextmanager
def holding(lock_path: Path):
    """Hold the exclusive lock at `lock_path` for the block, in this process.

    A second open of the same file gets its own open file description, so a
    turn tried inside the block meets a held lock exactly as it would meet
    another process's.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle


class FakeClock:
    """A clock the turn reads and a sleep that advances it, with a hook.

    Nothing waits: each sleep the turn asks for is recorded and added to the
    clock. `on_advance` is called with the new time after each sleep, which
    is how a test lets a holder go partway through a wait.
    """

    def __init__(self, on_advance=None):
        self.now_at = 1000.0
        self.slept: list[float] = []
        self.on_advance = on_advance

    def now(self) -> float:
        return self.now_at

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now_at += seconds
        if self.on_advance is not None:
            self.on_advance(self.now_at)


def take_turn(run_dir: Path, target: Path, config: dict, *,
              stage: str = DECLARING, artifact: str = SUITE_ARTIFACT,
              clock: FakeClock | None = None) -> tuple[bool, FakeClock]:
    """Enter `suite_turn` around a body that records it ran, and say so."""
    clock = clock or FakeClock()
    run_dir.mkdir(parents=True, exist_ok=True)
    ran = False
    with story_coordinator.suite_turn(run_dir, target, config, stage, artifact,
                                      sleep=clock.sleep, clock=clock.now):
        ran = True
    return ran, clock


def config_of(target: Path) -> dict:
    return harness_config.load_config(target)


def lock_of(target: Path) -> Path:
    return story_coordinator.suite_lock_path(target, config_of(target))


def configure_bound(target: Path, minutes: str) -> None:
    """Set the bound in a built target's configuration and commit it, so the
    run starts from a tree it can account for."""
    config = target / ".harness" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + f"{KEY}: {minutes}\n",
                      encoding="utf-8")
    conftest.commit_setup(target, "the bound on waiting for a turn")


# --------------------------------------------------------------------------
# The fixture target for in-process runs: the suite-run module's, unchanged
# --------------------------------------------------------------------------


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return materialize(WORKFLOW, tmp_path / "suite-turn-harness")


@pytest.fixture
def make_target(tmp_path: Path):
    def make(name: str, **kwargs) -> Path:
        return build_suite_target(tmp_path / name, **kwargs)
    return make


# --------------------------------------------------------------------------
# Two runs of one repository: the intervals the command itself recorded
# --------------------------------------------------------------------------

#: The command the two-process fixture configures. It records when it was
#: running, holds until a release file appears, and prints its own pid so the
#: record the coordinator writes for it can be matched to what it recorded.
RECORDING_COMMAND = '''\
"""The target's whole suite: records its interval and holds until released."""
import json
import os
import pathlib
import sys
import time

intervals = pathlib.Path(sys.argv[1])
release = pathlib.Path(sys.argv[2])
ceiling = float(sys.argv[3])
pid = os.getpid()

started = time.time()
(intervals / f"{pid}.started").write_text(str(started), encoding="utf-8")
while not release.exists() and time.time() - started < ceiling:
    time.sleep(0.05)
ended = time.time()
(intervals / f"{pid}.json").write_text(json.dumps({
    "pid": pid, "start": started, "end": ended,
    "argv": sys.argv, "cwd": os.getcwd(),
}), encoding="utf-8")
print(f"recorded by pid {pid}")
sys.exit(0)
'''

#: One coordinator run in a process of its own. The fake runner writes each
#: stage's required artifacts off the loaded workflow's own declarations, by
#: the schema each is declared under, so no artifact name is written here.
DRIVER = '''\
"""Drive one story through the coordinator with a fake runner, and exit as it did."""
import json
import sys
from pathlib import Path

orchestration, harness, target, story_id = sys.argv[1:5]
sys.path.insert(0, orchestration)

import harness_config
import story_coordinator
from agent_runner import AgentResult

config = harness_config.load_config(Path(target))
workflow = json.loads((Path(harness) / "workflows" / f"{config['workflow']}.json")
                      .read_text(encoding="utf-8"))
PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def runner(prompt, *, stage, run_dir, **_ignored):
    declaration = next(s for s in workflow["stages"] if s["name"] == stage)
    schemas = declaration.get("schemas", {})
    for artifact in story_coordinator.required_artifacts(declaration):
        path = Path(run_dir) / artifact
        schema = schemas.get(artifact)
        if schema == "changed-files":
            payload = {"modified": [], "created": [], "deleted": []}
        elif schema == "verification-result":
            payload = PASS
        elif schema == "test-results":
            payload = {"tests_written": 1}
        else:
            path.write_text(f"{artifact} written by {stage}\\n", encoding="utf-8")
            continue
        path.write_text(json.dumps(payload, indent=2) + "\\n", encoding="utf-8")
    return AgentResult(ok=True, result_text=f"{stage} done")


sys.exit(story_coordinator.run_story(story_id, Path(harness), Path(target), runner))
'''

FIRST_STORY = "story-001"
SECOND_STORY = "story-002"


def story_text(story_id: str) -> str:
    return conftest.STORY.replace(f"id: {FIRST_STORY}", f"id: {story_id}")


class TwoRuns:
    """One constructed repository, two story artifacts, and the files the two
    processes and their command write."""

    def __init__(self, tmp_path: Path):
        self.intervals = tmp_path / "intervals"
        self.intervals.mkdir()
        self.release = tmp_path / "release"
        self.driver = tmp_path / "drive_run.py"
        self.driver.write_text(DRIVER, encoding="utf-8")
        self.harness = materialize(WORKFLOW, tmp_path / "two-run-harness")
        command = shlex.join([sys.executable, "record.py", str(self.intervals),
                              str(self.release), str(HOLD_CEILING_SECONDS)])
        self.target = build_target(tmp_path / "two-run-target",
                                   workflow=WORKFLOW["name"],
                                   test_command=command)
        write(self.target / "record.py", RECORDING_COMMAND)
        write(self.target / ".harness" / "stories" / f"{SECOND_STORY}.yaml",
              story_text(SECOND_STORY))
        conftest.commit_setup(self.target, "the recording suite and a second story")
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.processes: dict[str, subprocess.Popen] = {}
        self.outputs: list = []

    def start(self, story_id: str) -> subprocess.Popen:
        out = open(self.logs / f"{story_id}.out", "w", encoding="utf-8")
        self.outputs.append(out)
        process = subprocess.Popen(
            [sys.executable, str(self.driver), str(ORCHESTRATION),
             str(self.harness), str(self.target), story_id],
            stdout=out, stderr=subprocess.STDOUT, cwd=self.target)
        self.processes[story_id] = process
        return process

    def output(self, story_id: str) -> str:
        return (self.logs / f"{story_id}.out").read_text(encoding="utf-8")

    def run_dir(self, story_id: str) -> Path:
        return conftest.run_dir_for(self.target, story_id)

    def started_files(self) -> list[Path]:
        return sorted(self.intervals.glob("*.started"))

    def intervals_recorded(self) -> dict[int, dict]:
        recorded = {}
        for path in self.intervals.glob("*.json"):
            entry = json.loads(path.read_text(encoding="utf-8"))
            recorded[entry["pid"]] = entry
        return recorded

    def kill_all(self) -> None:
        for process in self.processes.values():
            if process.poll() is None:
                process.kill()
                process.wait()
        for out in self.outputs:
            out.close()


def wait_for(condition, what: str, *, processes=()) -> None:
    """Poll for a precondition; report inconclusive if the machine did not let
    the run reach it, and fail if a process the precondition depends on exited
    before it was met."""
    deadline = time.monotonic() + PRECONDITION_SECONDS
    while time.monotonic() < deadline:
        if condition():
            return
        for story_id, process in processes:
            if process.poll() is not None and not condition():
                pytest.fail(f"the run of {story_id} exited {process.returncode} "
                            f"before {what}")
        time.sleep(0.1)
    machine_load.inconclusive(what)


def waited_entries(run_dir: Path) -> list[dict]:
    return entries_of_kind(run_dir, WAITED)


def interval_of(record: dict, recorded: dict[int, dict]) -> dict:
    """The interval the command recorded for the run a record describes,
    matched by the pid the command printed into the record's tail."""
    for token in record["output_tail"].split():
        if token.isdigit() and int(token) in recorded:
            return recorded[int(token)]
    raise AssertionError(f"no recorded interval matches {record['output_tail']!r}")


def overlapping(intervals: list[dict]) -> list[tuple[dict, dict]]:
    """Every pair of intervals that were running at the same moment."""
    found = []
    ordered = sorted(intervals, key=lambda entry: entry["start"])
    for index, earlier in enumerate(ordered):
        for later in ordered[index + 1:]:
            if later["start"] < earlier["end"]:
                found.append((earlier, later))
    return found


@pytest.fixture
def two_runs(tmp_path: Path):
    runs = TwoRuns(tmp_path)
    try:
        yield runs
    finally:
        runs.release.touch()
        runs.kill_all()


def test_two_runs_of_one_repository_never_run_the_command_at_once(two_runs):
    """The central claim, from what the command recorded.

    The first run's command is held inside its suite; the second run is
    started and observed announcing its wait while the first still holds; the
    first is then released and both finish. Every interval the command
    recorded — two per run, the declared suite run and the clean-clone check —
    is then read back, and none overlaps another.
    """
    first = two_runs.start(FIRST_STORY)
    wait_for(lambda: len(two_runs.started_files()) >= 1,
             "the first run started its suite command",
             processes=[(FIRST_STORY, first)])

    second = two_runs.start(SECOND_STORY)
    wait_for(lambda: len(waited_entries(two_runs.run_dir(SECOND_STORY))) >= 1
             or len(two_runs.started_files()) >= 2,
             "the second run reached its suite run",
             processes=[(FIRST_STORY, first), (SECOND_STORY, second)])
    # The moment that decides it: the first run's command is still held, and
    # the second run has announced a wait rather than started its own.
    assert len(two_runs.started_files()) == 1, two_runs.started_files()
    assert len(waited_entries(two_runs.run_dir(SECOND_STORY))) == 1

    two_runs.release.touch()
    wait_for(lambda: first.poll() is not None and second.poll() is not None,
             "both runs finished after the release")
    assert first.returncode == 0, two_runs.output(FIRST_STORY)
    assert second.returncode == 0, two_runs.output(SECOND_STORY)

    recorded = two_runs.intervals_recorded()
    by_run = {}
    for story_id in (FIRST_STORY, SECOND_STORY):
        run_dir = two_runs.run_dir(story_id)
        by_run[story_id] = [
            interval_of(record_of(run_dir, SUITE_ARTIFACT), recorded),
            interval_of(record_of(run_dir, conftest.CLEAN_CLONE_RESULT), recorded),
        ]
        for artifact in (SUITE_ARTIFACT, conftest.CLEAN_CLONE_RESULT):
            record = record_of(run_dir, artifact)
            assert record["ran"] is True and record["exit_code"] == 0, record

    # Every whole-suite run either coordinator announced left one interval,
    # and every interval the command recorded belongs to one of them.
    every_interval = by_run[FIRST_STORY] + by_run[SECOND_STORY]
    announced = sum(kinds_of(two_runs.run_dir(story)).count(RERUN_STARTED)
                    for story in (FIRST_STORY, SECOND_STORY))
    assert len(every_interval) == len(recorded) == announced
    assert overlapping(every_interval) == []
    # The second run's suite began only after the held command ended.
    assert by_run[SECOND_STORY][0]["start"] >= by_run[FIRST_STORY][0]["end"]


def test_the_overlap_reading_reports_a_constructed_pair_that_does_overlap():
    """The control for the emptiness above, constructed rather than argued."""
    apart = [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}]
    together = [{"start": 0.0, "end": 2.0}, {"start": 1.0, "end": 3.0}]
    assert overlapping(apart) == []
    assert overlapping(together) == [(together[0], together[1])]


def test_the_contended_run_announces_its_wait_once_before_each_suite_run(two_runs):
    """Exactly one wait event ahead of the suite-run announcement of the same
    stage, in the run that found the lock held, and none in the run that did
    not — readable off the events log as the gap between the two lines."""
    first = two_runs.start(FIRST_STORY)
    wait_for(lambda: len(two_runs.started_files()) >= 1,
             "the first run started its suite command",
             processes=[(FIRST_STORY, first)])
    second = two_runs.start(SECOND_STORY)
    wait_for(lambda: len(waited_entries(two_runs.run_dir(SECOND_STORY))) >= 1,
             "the second run announced its wait",
             processes=[(FIRST_STORY, first), (SECOND_STORY, second)])
    two_runs.release.touch()
    wait_for(lambda: first.poll() is not None and second.poll() is not None,
             "both runs finished after the release")
    assert (first.returncode, second.returncode) == (0, 0)

    contended_dir = two_runs.run_dir(SECOND_STORY)
    announced = [(e["event"], e["stage"]) for e in history_of(contended_dir)
                 if e["event"] in (WAITED, RERUN_STARTED)]
    # The declaring stage's suite run waited. Whether the verifier's clean
    # clone did as well depends on which run reached the lock first after the
    # release, so only the first pair is fixed; every wait there is must be
    # followed at once by the suite-run announcement of its own stage.
    assert announced[:2] == [(WAITED, DECLARING), (RERUN_STARTED, DECLARING)]
    for index, (kind, stage) in enumerate(announced):
        if kind == WAITED:
            assert announced[index + 1] == (RERUN_STARTED, stage)
    assert announced.count((RERUN_STARTED, DECLARING)) == 1
    assert announced.count((RERUN_STARTED, VERIFYING)) == 1

    # The announcement convention: what started, then what the wait is worth,
    # and the line is in events.log ahead of the suite-run line.
    [message] = [e["message"] for e in entries_of_kind(contended_dir, WAITED)
                 if e["stage"] == DECLARING]
    assert message == story_coordinator._announcement(*message.split("; ", 1))
    assert "another run holds" in message and "minutes" in message
    lines = log_lines(contended_dir)
    waited_line = next(i for i, line in enumerate(lines) if line.endswith(message))
    rerun_line = next(i for i, line in enumerate(lines)
                      if "re-running the suite" in line)
    assert waited_line < rerun_line

    # The control: the run that held the lock first waited on nothing.
    assert kinds_of(two_runs.run_dir(FIRST_STORY)).count(WAITED) == 0
    assert kinds_of(two_runs.run_dir(FIRST_STORY)).count(RERUN_STARTED) == 2
    assert not any("turn" in line and "another run holds" in line
                   for line in log_lines(two_runs.run_dir(FIRST_STORY)))


# --------------------------------------------------------------------------
# The scope of the turn: one lock per repository
# --------------------------------------------------------------------------


def test_the_lock_is_derived_from_the_repository_and_shared_by_its_worktrees(
        make_target, tmp_path):
    target = make_target("primary")
    config = config_of(target)
    tree = tmp_path / "a-worktree"
    made = worktrees.add(target, tree, "a-branch", "HEAD")
    assert not made.problems, made.problems

    assert lock_of(target) == worktrees.worktree_root(target, config) / "suite.lock"
    assert story_coordinator.suite_lock_path(tree, config).resolve() == \
        lock_of(target).resolve()
    # A configured worktree_dir moves the lock with the worktrees.
    assert story_coordinator.suite_lock_path(
        target, {**config, "worktree_dir": str(tmp_path / "elsewhere")}
    ) == tmp_path / "elsewhere" / "suite.lock"


def test_a_run_of_an_unrelated_repository_is_not_delayed_by_a_held_turn(
        make_target, harness_root):
    """The lock is the target's own, so a run nested inside another run's suite
    never waits on the run hosting it. Bound zero on the run under test, so a
    turn wrongly shared would show as a wait event rather than as an hour."""
    other = make_target("other-repository")
    subject = make_target("subject-repository")
    configure_bound(subject, "0")

    with holding(lock_of(other)):
        code, _, run_dir = drive(subject, harness_root)

    assert code == 0
    assert kinds_of(run_dir).count(WAITED) == 0
    assert notes_about_the_turn(run_dir) == []
    assert kinds_of(run_dir).count(RERUN_STARTED) == 2
    assert record_of(run_dir, SUITE_ARTIFACT)["exit_code"] == 0
    assert lock_of(subject).is_file()


def test_the_same_run_with_its_own_lock_held_does_wait(make_target, harness_root):
    """The control for the absence above, and the bound-expired path driven
    through a whole run: with the subject's own lock held and the bound at
    zero, every whole-suite run announces its wait, notes that the bound
    expired, and runs the suite anyway to the same result."""
    subject = make_target("subject-repository")
    configure_bound(subject, "0")
    with holding(lock_of(subject)):
        code, _, run_dir = drive(subject, harness_root)

    assert code == 0
    kinds = kinds_of(run_dir)
    assert kinds.count(WAITED) == kinds.count(RERUN_STARTED) == 2
    assert [(e["stage"]) for e in entries_of_kind(run_dir, WAITED)] == \
        [DECLARING, VERIFYING]
    notes = notes_about_the_turn(run_dir)
    assert len(notes) == kinds.count(RERUN_STARTED)
    assert all("0 minutes" in note and "without one" in note for note in notes)
    # The suite ran, as configured, to the result it records with a turn.
    configured = shlex.join(shlex.split(config_of(subject)["test_command"]))
    for artifact in (SUITE_ARTIFACT, conftest.CLEAN_CLONE_RESULT):
        record = record_of(run_dir, artifact)
        assert record["ran"] is True and record["exit_code"] == 0
        assert record["command"] == configured
    # Each wait is announced ahead of the suite run of its own stage.
    for stage in (DECLARING, VERIFYING):
        of_stage = [e["event"] for e in history_of(run_dir)
                    if e.get("stage") == stage
                    and e["event"] in (WAITED, RERUN_STARTED)]
        assert of_stage == [WAITED, RERUN_STARTED]


# --------------------------------------------------------------------------
# A killed holder releases the turn
# --------------------------------------------------------------------------

HOLDER = '''\
"""Take the lock at the path given and hold it until killed."""
import fcntl
import sys

handle = open(sys.argv[1], "a")
fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("held", flush=True)
while True:
    sys.stdin.readline()
'''


def test_a_killed_holder_releases_the_turn(make_target, tmp_path):
    target = make_target("killed-holder")
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = tmp_path / "hold.py"
    holder.write_text(HOLDER, encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(holder), str(lock)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True)
    try:
        assert process.stdout.readline().strip() == "held"

        # The control: while the holder lives, the turn is refused.
        ran, clock = take_turn(tmp_path / "while-held", target,
                               {**config_of(target), KEY: "0"})
        assert ran is True
        assert kinds_of(tmp_path / "while-held") == [WAITED, NOTE]
        assert clock.slept == []

        process.send_signal(signal.SIGKILL)
        process.wait()
    finally:
        if process.poll() is None:
            process.kill()

    assert lock.is_file()
    ran, clock = take_turn(tmp_path / "after-kill", target, config_of(target))
    assert ran is True
    assert kinds_of(tmp_path / "after-kill") == []
    assert clock.slept == []


# --------------------------------------------------------------------------
# A lock file that cannot be created or opened
# --------------------------------------------------------------------------


def test_a_lock_that_cannot_be_opened_runs_the_suite_and_says_why(
        make_target, harness_root):
    """The lock path already occupied by a directory, so it cannot be opened
    for writing. The run completes, the suite result is the one the unblocked
    run beside it records, and a note names the lock path."""
    obstructed = make_target("obstructed")
    lock_of(obstructed).mkdir(parents=True)
    unobstructed = make_target("unobstructed")

    code, _, run_dir = drive(obstructed, harness_root)
    control_code, _, control_dir = drive(unobstructed, harness_root)

    assert code == control_code == 0
    for artifact in (SUITE_ARTIFACT, conftest.CLEAN_CLONE_RESULT):
        subject, control = record_of(run_dir, artifact), record_of(control_dir, artifact)
        assert subject["ran"] is control["ran"] is True
        assert subject["exit_code"] == control["exit_code"] == 0
        assert subject["command"] == control["command"]
    notes = notes_about_the_turn(run_dir)
    assert len(notes) == kinds_of(run_dir).count(RERUN_STARTED)
    assert all(str(lock_of(obstructed)) in note and "could not be created or opened" in note
               for note in notes)
    assert kinds_of(run_dir).count(WAITED) == 0
    assert notes_about_the_turn(control_dir) == []


def test_a_worktree_root_that_is_a_regular_file_runs_the_body_and_says_why(
        make_target, tmp_path):
    """The other way the file cannot be created: nothing can be made under a
    regular file. No wait, no sleep, the body runs, one note."""
    target = make_target("root-is-a-file")
    config = {**config_of(target), "worktree_dir": str(tmp_path / "a-file")}
    (tmp_path / "a-file").write_text("not a directory\n", encoding="utf-8")

    ran, clock = take_turn(tmp_path / "run", target, config)

    assert ran is True
    assert clock.slept == []
    assert kinds_of(tmp_path / "run") == [NOTE]
    [note] = notes_about_the_turn(tmp_path / "run")
    assert str(tmp_path / "a-file" / "suite.lock") in note


# --------------------------------------------------------------------------
# The bound, observed through an injected clock
# --------------------------------------------------------------------------

POLL = story_coordinator.SUITE_TURN_POLL_SECONDS


def test_the_wait_ends_at_the_configured_bound_and_the_suite_runs_anyway(
        make_target, tmp_path):
    target = make_target("bounded")
    config = {**config_of(target), KEY: "2"}
    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "run", target, config)

    assert ran is True
    assert sum(clock.slept) == pytest.approx(120.0, abs=POLL)
    assert all(0 < step <= POLL for step in clock.slept)
    assert kinds_of(tmp_path / "run") == [WAITED, NOTE]
    [waited] = entries_of_kind(tmp_path / "run", WAITED)
    assert waited["stage"] == DECLARING
    assert waited["artifacts"] == [SUITE_ARTIFACT]
    assert "2 minutes" in waited["message"]
    assert str(lock_of(target)) in waited["message"]
    [note] = notes_about_the_turn(tmp_path / "run")
    assert "2 minutes" in note and "without one" in note


def test_a_holder_that_lets_go_inside_the_bound_is_waited_for_and_then_taken(
        make_target, tmp_path):
    """The other side of the bound: a wait that is granted writes the wait and
    no note, and stops sleeping the moment the lock is free."""
    target = make_target("released")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    let_go_after = 30.0

    def let_go(now: float) -> None:
        # Closing the holder releases the lock; the next poll takes it.
        if now - 1000.0 >= let_go_after and not holder.closed:
            holder.close()

    clock = FakeClock(on_advance=let_go)
    try:
        ran, clock = take_turn(tmp_path / "run", target, config, clock=clock)
    finally:
        holder.close()

    assert ran is True
    assert let_go_after <= sum(clock.slept) < let_go_after + 2 * POLL
    assert kinds_of(tmp_path / "run") == [WAITED]
    assert notes_about_the_turn(tmp_path / "run") == []


def test_a_bound_of_zero_takes_the_turn_only_if_it_is_free(make_target, tmp_path):
    target = make_target("zero")
    config = {**config_of(target), KEY: "0"}

    ran, clock = take_turn(tmp_path / "free", target, config)
    assert ran is True and clock.slept == []
    assert kinds_of(tmp_path / "free") == []

    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "held", target, config)
    assert ran is True and clock.slept == []
    assert kinds_of(tmp_path / "held") == [WAITED, NOTE]


def test_an_absent_key_waits_up_to_sixty_minutes(make_target, tmp_path):
    target = make_target("absent")
    config = config_of(target)
    assert KEY not in config
    assert story_coordinator.suite_turn_wait_seconds(config) == 60 * 60
    assert story_coordinator.suite_turn_wait_problems(config) == []

    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "run", target, config)
    assert ran is True
    assert sum(clock.slept) == pytest.approx(3600.0, abs=POLL)
    assert "60 minutes" in entries_of_kind(tmp_path / "run", WAITED)[0]["message"]


def test_an_uncontended_turn_writes_nothing_and_sleeps_nothing(make_target, tmp_path):
    """The absence the two-process control above demonstrates can fail: a free
    lock is taken on the first try, and the events log gains no line."""
    target = make_target("free")
    ran, clock = take_turn(tmp_path / "run", target, config_of(target))
    assert ran is True
    assert clock.slept == []
    assert not (tmp_path / "run" / "events.log").exists()


# --------------------------------------------------------------------------
# The key: refused when malformed, accepted when a duration
# --------------------------------------------------------------------------

NOT_BOUNDS = ["an hour", "-1", "", "nan", "6,0", "inf minus"]
ARE_BOUNDS = ["0", "1", "0.5", "60", "180"]


@pytest.mark.parametrize("value", ARE_BOUNDS)
def test_a_non_negative_number_of_minutes_is_accepted(value):
    assert story_coordinator.suite_turn_wait_problems({KEY: value}) == []
    assert story_coordinator.suite_turn_wait_seconds({KEY: value}) == float(value) * 60


@pytest.mark.parametrize("value", NOT_BOUNDS)
def test_a_value_that_is_not_a_non_negative_number_is_reported_by_key_and_value(value):
    problems = story_coordinator.suite_turn_wait_problems({KEY: value})
    assert len(problems) == 1, problems
    assert KEY in problems[0]
    assert repr(value) in problems[0]


def test_a_bad_bound_refuses_the_run_at_pre_flight_naming_the_key_and_the_value(
        make_target, harness_root, capsys):
    refused = make_target("refused")
    configure_bound(refused, "an hour")
    code, runner, run_dir = drive(refused, harness_root)

    assert code == 1
    assert runner.calls == []
    assert not run_dir.exists()
    message = capsys.readouterr().err
    assert KEY in message and "'an hour'" in message

    # The control: the same run with the bound repaired proceeds.
    repaired = make_target("repaired")
    configure_bound(repaired, "5")
    code, runner, run_dir = drive(repaired, harness_root)
    assert code == 0 and runner.calls


def test_the_key_is_declared_and_set_in_both_configurations():
    schema = schema_validator.load_schema("harness-config")
    assert KEY in schema["properties"]
    description = schema["properties"][KEY]["description"]
    assert "60" in description and "zero" in description.lower()
    assert "pre-flight" in description
    assert conftest.repository_config()[KEY] == "60"
    template = (REPO_ROOT / "templates" / "config.yaml").read_text(encoding="utf-8")
    assert f"{KEY}: 60" in template
    assert "duration" in template and "core count" in template
    shipped = (REPO_ROOT / ".harness" / "config.yaml").read_text(encoding="utf-8")
    assert "duration" in shipped and "core count" in shipped


# --------------------------------------------------------------------------
# The command is untouched, and the option is unknown to orchestration
# --------------------------------------------------------------------------


def test_the_configured_command_still_reads_the_core_count_of_the_machine():
    assert worker_count(conftest.repository_config()["test_command"]) == "auto"


def string_constants(source: str) -> list[str]:
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def modules_naming_the_worker_option(directory: Path) -> list[str]:
    """Every module under `directory` whose string constants name a worker
    count in any spelling `test_suite_parallelism.worker_count` reads."""
    found = []
    for path in sorted(directory.glob("*.py")):
        for text in string_constants(path.read_text(encoding="utf-8")):
            try:
                count = worker_count(text)
            except ValueError:
                continue
            if count is not None:
                found.append(path.name)
                break
    return found


def test_no_orchestration_module_names_the_worker_count_option():
    assert modules_naming_the_worker_option(ORCHESTRATION) == []
    assert len(list(ORCHESTRATION.glob("*.py"))) > 5


def test_the_same_scan_reports_a_planted_module_that_rewrites_the_option(tmp_path):
    planted = tmp_path / "orchestration"
    planted.mkdir()
    (planted / "rewrites.py").write_text(
        'def narrow(argv):\n    return [a for a in argv if a != "-n auto"]\n',
        encoding="utf-8")
    (planted / "innocent.py").write_text('COST = "a suite run"\n', encoding="utf-8")
    assert modules_naming_the_worker_option(planted) == ["rewrites.py"]


def test_the_command_sees_exactly_the_configured_arguments(two_runs):
    """From the command's own side: the argv it was started with is the
    configured command's, unnarrowed and unrewritten, on the path where the
    turn was taken. The bound-expired and no-lock-file paths are held to the
    same recorded command by the runs above, through the coordinator's own
    record of what it ran."""
    configured = shlex.split(config_of(two_runs.target)["test_command"])
    first = two_runs.start(FIRST_STORY)
    two_runs.release.touch()
    wait_for(lambda: first.poll() is not None, "the uncontended run finished")
    assert first.returncode == 0, two_runs.output(FIRST_STORY)
    recorded = two_runs.intervals_recorded()
    assert len(recorded) == 2
    for entry in recorded.values():
        # What the script saw is everything after the interpreter, as written.
        assert entry["argv"] == configured[1:]


# --------------------------------------------------------------------------
# The selector runs and the census take no turn
# --------------------------------------------------------------------------

#: A selector that passes when the governed file holds the repaired content
#: and fails when it holds what the baseline captured — so the nomination is
#: shown to discriminate and the whole-suite fallthrough is never reached.
SELECTOR = '''\
import pathlib
import sys
state = pathlib.Path("checks/keep.txt").read_text(encoding="utf-8").strip()
sys.exit(0 if state == "repaired" else 1)
'''


def revert_check_under_a_held_lock(target: Path, tmp_path: Path):
    write(target / "select.py", SELECTOR)
    config_path = target / ".harness" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + f"test_selection_command: {shlex.join([sys.executable, 'select.py'])} {{test}}\n"
        + f"{KEY}: 0\n", encoding="utf-8")
    conftest.commit_setup(target, "a selector and a zero bound")
    config = config_of(target)
    run_dir = tmp_path / "revert-run"
    run_dir.mkdir()
    baseline = story_coordinator.capture_stage_baseline(
        run_dir, target, "a-baseline", DECLARING, ["checks/"], accounted_for=set())
    write(target / "checks" / "keep.txt", "repaired\n")
    with holding(lock_of(target)):
        result = story_coordinator.revert_check(
            run_dir, target, config, "revert-probe.json", ("checks/keep.txt",),
            baseline, stage_name=DECLARING, nomination="the-nominated-test")
    return run_dir, result


def test_a_nominations_selector_runs_take_no_turn(make_target, tmp_path):
    target = make_target("selector")
    run_dir, result = revert_check_under_a_held_lock(target, tmp_path)

    # Both selector runs ran and decided: pass on the tree the stage left,
    # fail with the governed path reverted.
    assert result.nomination.short_circuited is True
    assert result.permitted is True
    assert result.nomination.applied_exit_code == 0
    assert result.nomination.reverted_exit_code not in (None, 0)
    assert kinds_of(run_dir).count(WAITED) == 0
    assert notes_about_the_turn(run_dir) == []
    assert kinds_of(run_dir).count(RERUN_STARTED) == 1


def test_the_census_takes_no_turn(make_target, tmp_path):
    target = make_target("census")
    config_path = target / ".harness" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "census_command: "
        + shlex.join([sys.executable, "-c", 'print(\'{"checks/keep.txt": 1}\')'])
        + "\n"
        + f"{KEY}: 0\n", encoding="utf-8")
    conftest.commit_setup(target, "a census and a zero bound")
    config = config_of(target)
    run_dir = tmp_path / "census-run"
    run_dir.mkdir()
    baseline = story_coordinator.capture_stage_baseline(
        run_dir, target, "a-baseline", DECLARING, ["checks/"], accounted_for=set())
    with holding(lock_of(target)):
        result = story_coordinator.suite_census_check(
            run_dir, target, config, "census-probe.json", ["checks/"], baseline,
            stage_name=DECLARING)

    assert result.ran is True
    assert kinds_of(run_dir).count(WAITED) == 0
    assert notes_about_the_turn(run_dir) == []


def test_the_clean_clone_check_under_the_same_held_lock_does_take_a_turn(
        make_target, tmp_path):
    """The control for the two absences above: the same held lock, the same
    zero bound, and a whole-suite call site — which waits and says so."""
    target = make_target("clean-clone")
    configure_bound(target, "0")
    run_dir = tmp_path / "clean-clone-run"
    run_dir.mkdir()
    with holding(lock_of(target)):
        story_coordinator.clean_clone_check(
            run_dir, target, config_of(target), "clean-clone-probe.json",
            stage_name=VERIFYING)
    assert kinds_of(run_dir)[:3] == [WAITED, NOTE, RERUN_STARTED]


# --------------------------------------------------------------------------
# The shipped artifacts
# --------------------------------------------------------------------------


def test_the_new_kind_is_in_the_execution_history_enum_and_in_no_cross_run_log():
    history = schema_validator.load_schema("execution-history")
    kinds = history["items"]["properties"]["event"]["enum"]
    assert WAITED in kinds
    assert RERUN_STARTED in kinds
    cross_run = (REPO_ROOT / "schemas" / "cross-run-history.schema.json").read_text(
        encoding="utf-8")
    assert WAITED not in cross_run
    assert RERUN_STARTED not in cross_run


def test_the_kind_is_a_literal_in_exactly_one_function_body():
    assert COORDINATOR_SOURCE.count(f'"{WAITED}"') == 1
    turn = conftest.function_source(COORDINATOR_SOURCE, "suite_turn")
    assert f'"{WAITED}"' in turn
    assert "_announcement(" in turn
    assert "append_event(" in turn
    assert "flock(" in turn or "_try_suite_lock(" in turn
    assert "os.environ" not in turn and "getenv" not in turn


def test_the_lock_is_a_kernel_lock_derived_through_the_worktree_root():
    lock_path = conftest.function_source(COORDINATOR_SOURCE, "suite_lock_path")
    assert "worktrees.worktree_root(" in lock_path
    assert "fcntl.flock(" in COORDINATOR_SOURCE
    assert "LOCK_EX | fcntl.LOCK_NB" in COORDINATOR_SOURCE


def test_only_the_three_whole_suite_call_sites_enter_the_turn():
    """Named by the function each sits in, derived from the source rather
    than written as a count of sites."""
    entering = {
        node.name
        for node in ast.parse(COORDINATOR_SOURCE).body
        if isinstance(node, ast.FunctionDef)
        for inner in ast.walk(node)
        if isinstance(inner, ast.With)
        for item in inner.items
        if isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Name)
        and item.context_expr.func.id == "suite_turn"
    }
    assert entering == {"suite_run_check", "clean_clone_check", "revert_check"}
    for name in ("run_nomination", "_run_selection", "suite_census_check"):
        assert "suite_turn(" not in conftest.function_source(COORDINATOR_SOURCE, name)
