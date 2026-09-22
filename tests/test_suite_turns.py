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
    lock file that cannot be created or opened, and a bound of zero that found
    the turn held are each shown running the suite, recording the result it
    would have recorded with the turn, and saying in the events log why no
    turn was taken.

  * **the bound.** Observed through an injected clock and an injected sleep, so
    nothing here waits. The key counts suite runs: with an estimate of what a
    suite run takes here, established through `set_duration_estimates`, the
    wait ends at the count times the estimate and not before, and the
    announcement names both; with no estimate it ends at the sixty-minute
    fallback and the announcement says why. A holder that lets go inside the
    bound is waited for, and an absent key waits five suite runs' worth.

  * **the takeover (story-190).** A wait that expires on a holder still there
    takes the turn from it rather than running beside it: a fresh lock file
    is renamed over the path while the holder keeps its lock on the orphaned
    inode. Three waiters behind one hung holder, each on a thread of its own
    and all on one fake clock stepped from the test, are shown draining one
    per bound and never together; every waiter that did not take over says
    the turn changed hands and starts its wait over. A fresh entrant after a
    takeover queues behind the new holder. A takeover meeting a normal
    release leaves one suite in flight on either side: a taker granted the
    old lock under the takeover lock runs on the old inode and swaps nothing,
    and a waiter granted the old inode after the path has moved on lets it
    go and queues on the new one rather than running.

  * **the bound measures the holder (story-196).** Every run that takes the
    turn appends a stamp — its pid and the moment — to the lock file, and a
    waiter that sees the stamp change restarts its bound, so a queue of
    healthy runs handing the turn on never expires anyone. Six waiters behind
    healthy holders, each holding one suite's time under a bound of five, are
    granted in order with no takeover and the inode unchanged; a waiter behind
    a normal handoff announces the restart and measures the new holder from
    the handoff; a stamped holder that hangs is taken over at the bound from
    the handoff, not from the waiter's start; a stampless lock file is
    measured from the first poll that sees it exactly as a stamped one is;
    every route that takes the turn leaves the stamp before the body runs;
    and the stamp is read for equality and for nothing else — a line that is
    no pid and no time is waited behind like any other.

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
    that does, which the same scan reports;
  * "a holder that lets go, and a bound of zero, take nothing over" — the
    inode at the lock path is unchanged — sit beside the expired wait, under
    which the same reading finds a different inode;
  * "no waiter but the taker writes a takeover note" sits beside the taker's
    own log, which carries exactly one;
  * "the takeover lock is released after the swap" sits beside the same lock
    held by the test, which the same probe reports held;
  * "a taker granted the lock it waited on swaps nothing" sits beside the
    same helper called with the holder still there, which swaps;
  * "a waiter granted an orphaned inode does not run and writes no note" sits
    beside the same release with no swap in between, which is the ordinary
    grant on the old inode with no restart;
  * "the old key is declared nowhere" sits beside the new key, which the same
    schema declares;
  * "a stampless lock file carries no stamp" is read off the file itself
    before the wait, beside the stamped holder whose file does carry one;
  * "no waiter behind healthy holders takes over" sits beside the last
    waiter's queueing time, read to exceed the bound — the very wait that
    expired before the bound measured the holder;
  * "the stamp is read for nothing but equality" sits beside a planted body
    that parses one and signals its pid, which the same scan reports;
  * "no wait-driving module names the poll's number" sits beside a planted
    module that does, which the same scan reports.

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
import os
import subprocess
import sys
import threading
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

#: The key story-190 replaced the minute bound with: a count of suite runs,
#: and the key it retired, which the shipped schema must no longer declare.
KEY = "suite_turn_wait_suite_runs"
OLD_KEY = "suite_turn_wait_minutes"

#: The operation whose estimate the bound multiplies, named by the coordinator.
SUITE_RUN = story_coordinator.SUITE_RUN_OPERATION

#: The estimate the bound tests establish for a suite run, in seconds. Not a
#: whole number of minutes, so a bound derived from it cannot be confused with
#: a minute count read off the old key, and short, so the polls are few.
ESTIMATE_SECONDS = 45.0

#: The bound the coordinator falls back to where no estimate exists — sixty
#: minutes, which is what the retired key defaulted to.
FALLBACK_SECONDS = story_coordinator.SUITE_TURN_WAIT_FALLBACK_SECONDS

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


def configure_key(target: Path, key: str, value: str) -> None:
    """Set one key in a built target's configuration and commit it, so the
    run starts from a tree it can account for."""
    config = target / ".harness" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + f"{key}: {value}\n",
                      encoding="utf-8")
    conftest.commit_setup(target, f"the configured {key}")


def configure_bound(target: Path, count: str) -> None:
    """Set the bound — a count of suite runs — in a built target's configuration."""
    configure_key(target, KEY, count)


def inode_at(lock_path: Path) -> int:
    return lock_path.stat().st_ino


def inode_held(handle) -> int:
    return os.fstat(handle.fileno()).st_ino


def is_held(lock_path: Path) -> bool:
    """Whether some open file description holds the exclusive lock at the
    path — probed with a handle of its own, released on the way out."""
    with open(lock_path, "a", encoding="utf-8") as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        return False


def takeover_lock_of(target: Path) -> Path:
    return lock_of(target).with_name(story_coordinator.SUITE_TAKEOVER_LOCK_NAME)


@pytest.fixture(autouse=True)
def no_estimate_held_over():
    """The estimates are module state, established once per run and left in
    place when the run ends; a turn taken directly reads whatever the last
    run in this worker established. Each test starts and ends with none, so
    a test that wants an estimate says so through `with_estimate`."""
    story_coordinator.set_duration_estimates({})
    yield
    story_coordinator.set_duration_estimates({})


def with_estimate(seconds: float = ESTIMATE_SECONDS) -> None:
    """Establish an estimate for a suite run, as the startup read would have."""
    story_coordinator.set_duration_estimates({SUITE_RUN: seconds})


def bound_of(count: str, estimate: float = ESTIMATE_SECONDS) -> float:
    """What the bound must be, stated from the count and the estimate rather
    than read off the coordinator."""
    return float(count) * estimate


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
    """A wait event ahead of the suite-run announcement of the same stage, in
    the run that found the lock held, and none in the run that did not —
    readable off the events log as the gap between the two lines. Since
    story-196 the wait may be announced more than once: it restarts whenever
    the turn changes hands, and here the first run's clean-clone check can
    take the turn between two of the second run's polls, so a wait may be
    followed by a restart of the same stage before its suite-run line."""
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
    # release, so only the first wait is fixed; every wait there is must be
    # followed at once by a restart of the same stage's wait or by the
    # suite-run announcement of its own stage, and nothing else.
    assert announced[0] == (WAITED, DECLARING)
    for index, (kind, stage) in enumerate(announced):
        if kind == WAITED:
            assert announced[index + 1] in [(WAITED, stage), (RERUN_STARTED, stage)]
    assert announced.count((RERUN_STARTED, DECLARING)) == 1
    assert announced.count((RERUN_STARTED, VERIFYING)) == 1

    # The announcement convention: what started, then what the wait is worth,
    # and the line is in events.log ahead of the suite-run line.
    message, *restarts = [e["message"] for e in entries_of_kind(contended_dir, WAITED)
                          if e["stage"] == DECLARING]
    assert all(CHANGED_HANDS in restart for restart in restarts)
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


#: What the wait announcement says its bound came from, in the two wordings
#: the story states: the count and the estimate, or the fallback because too
#: few suite runs have been measured here. Each phrase is one only its own
#: wording carries — both wordings say "measured here", so a bare "measured
#: here" would be found in the fallback's and prove nothing by its absence.
FROM_THE_ESTIMATE = "-minute estimate measured here"
FROM_THE_FALLBACK = "too few suite runs have been measured"


def test_the_wait_ends_at_the_count_times_the_estimate_and_names_both(
        make_target, tmp_path):
    """The bound is the configured count of suite runs times what a suite run
    takes here, observed within one poll on a holder that never lets go, and
    the announcement says so. What an expired wait then does — taking the
    turn — is the subject of the takeover tests below; here only the note's
    presence is read."""
    with_estimate()
    target = make_target("bounded")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    assert story_coordinator.suite_turn_wait_seconds(config) == bound
    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "run", target, config)

    assert ran is True
    assert sum(clock.slept) == pytest.approx(bound, abs=POLL)
    assert all(0 < step <= POLL for step in clock.slept)
    assert kinds_of(tmp_path / "run") == [WAITED, NOTE]
    [waited] = entries_of_kind(tmp_path / "run", WAITED)
    assert waited["stage"] == DECLARING
    assert waited["artifacts"] == [SUITE_ARTIFACT]
    assert f"{bound / 60:g} minutes" in waited["message"]
    assert "2 suite run" in waited["message"]
    assert f"{ESTIMATE_SECONDS / 60:g}-minute estimate" in waited["message"]
    assert FROM_THE_ESTIMATE in waited["message"]
    assert FROM_THE_FALLBACK not in waited["message"]
    assert str(lock_of(target)) in waited["message"]
    [note] = notes_about_the_turn(tmp_path / "run")
    assert f"{bound / 60:g} minutes" in note


def test_with_no_estimate_the_wait_ends_at_the_sixty_minute_fallback_and_says_why(
        make_target, tmp_path):
    """The other source of the bound. No estimate is held — the autouse
    fixture cleared it — so the same count waits the fallback, which is sixty
    minutes, and the announcement says the fallback applies because too few
    suite runs have been measured."""
    assert story_coordinator.duration_estimate(SUITE_RUN) is None
    assert FALLBACK_SECONDS == 60 * 60
    target = make_target("fallback")
    config = {**config_of(target), KEY: "2"}
    assert story_coordinator.suite_turn_wait_seconds(config) == FALLBACK_SECONDS
    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "run", target, config)

    assert ran is True
    assert sum(clock.slept) == pytest.approx(FALLBACK_SECONDS, abs=POLL)
    assert kinds_of(tmp_path / "run") == [WAITED, NOTE]
    [waited] = entries_of_kind(tmp_path / "run", WAITED)
    assert "60 minutes" in waited["message"]
    assert FROM_THE_FALLBACK in waited["message"]
    assert FROM_THE_ESTIMATE not in waited["message"]
    # The control for the two absences above: the same count under an
    # estimate names the estimate and not the fallback.
    with_estimate()
    with holding(lock_of(target)):
        take_turn(tmp_path / "control", target, config)
    [control] = entries_of_kind(tmp_path / "control", WAITED)
    assert FROM_THE_ESTIMATE in control["message"]
    assert FROM_THE_FALLBACK not in control["message"]


def test_a_holder_that_lets_go_inside_the_bound_is_waited_for_and_then_taken(
        make_target, tmp_path):
    """The other side of the bound: a wait that is granted writes the wait and
    no note, stops sleeping the moment the lock is free, and takes the turn
    on the same inode — nothing is taken over from a holder that merely
    finished."""
    with_estimate()
    target = make_target("released")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = inode_at(lock)
    let_go_after = 30.0
    assert let_go_after < bound_of("2")

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
    assert inode_at(lock) == before
    # The grant was confirmed under the takeover lock — the waiter's side of
    # the race with a takeover — so that lock's file exists; nothing holds it
    # and nothing was swapped over the path.
    assert is_held(takeover_lock_of(target)) is False


def test_a_bound_of_zero_takes_the_turn_only_if_it_is_free_and_takes_nothing_over(
        make_target, tmp_path):
    """Zero keeps its meaning under an estimate and without one: a free turn
    is taken with no event, and a held one is not waited for and not taken
    over — the suite runs at once without a turn, the wait and the expiry
    note are written, and the inode at the lock path is unchanged."""
    target = make_target("zero")
    config = {**config_of(target), KEY: "0"}
    for estimate in (None, ESTIMATE_SECONDS):
        story_coordinator.set_duration_estimates(
            {} if estimate is None else {SUITE_RUN: estimate})
        assert story_coordinator.suite_turn_wait_seconds(config) == 0.0
        label = "no-estimate" if estimate is None else "estimate"

        ran, clock = take_turn(tmp_path / label / "free", target, config)
        assert ran is True and clock.slept == []
        assert kinds_of(tmp_path / label / "free") == []

        lock = lock_of(target)
        with holding(lock) as hung:
            before = inode_at(lock)
            ran, clock = take_turn(tmp_path / label / "held", target, config)
            assert inode_at(lock) == before == inode_held(hung)
        assert ran is True and clock.slept == []
        assert kinds_of(tmp_path / label / "held") == [WAITED, NOTE]
        [waited] = entries_of_kind(tmp_path / label / "held", WAITED)
        assert "0 minutes" in waited["message"]
        [note] = notes_about_the_turn(tmp_path / label / "held")
        assert "0 minutes" in note and "without one" in note
        assert "took the turn" not in note
        assert not takeover_lock_of(target).exists()


def test_an_absent_key_waits_five_suite_runs_worth_or_the_fallback(
        make_target, tmp_path):
    target = make_target("absent")
    config = config_of(target)
    assert KEY not in config and OLD_KEY not in config
    assert story_coordinator.suite_turn_wait_problems(config) == []
    assert story_coordinator.DEFAULT_SUITE_TURN_WAIT_SUITE_RUNS == "5"

    assert story_coordinator.suite_turn_wait_seconds(config) == FALLBACK_SECONDS
    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "fallback", target, config)
    assert ran is True
    assert sum(clock.slept) == pytest.approx(FALLBACK_SECONDS, abs=POLL)
    [waited] = entries_of_kind(tmp_path / "fallback", WAITED)
    assert "60 minutes" in waited["message"] and FROM_THE_FALLBACK in waited["message"]

    with_estimate()
    assert story_coordinator.suite_turn_wait_seconds(config) == bound_of("5")
    with holding(lock_of(target)):
        ran, clock = take_turn(tmp_path / "estimated", target, config)
    assert ran is True
    assert sum(clock.slept) == pytest.approx(bound_of("5"), abs=POLL)
    [waited] = entries_of_kind(tmp_path / "estimated", WAITED)
    assert "5 suite run" in waited["message"] and FROM_THE_ESTIMATE in waited["message"]


# --------------------------------------------------------------------------
# An expired wait takes the turn (story-190)
# --------------------------------------------------------------------------

#: What the takeover note says, in the three clauses the story states.
TAKEOVER_NOTE = ("took the turn", "left the previous holder running")
#: What the restart announcement says.
CHANGED_HANDS = "changed hands"


def takeover_notes(run_dir: Path) -> list[str]:
    return [note for note in notes_about_the_turn(run_dir)
            if all(clause in note for clause in TAKEOVER_NOTE)]


def enter_turn(stack: contextlib.ExitStack, run_dir: Path, target: Path,
               config: dict, clock: FakeClock) -> None:
    """Enter `suite_turn` and leave it entered, so the turn is held — as a
    suite in flight holds it — until the stack is closed."""
    run_dir.mkdir(parents=True, exist_ok=True)
    stack.enter_context(story_coordinator.suite_turn(
        run_dir, target, config, DECLARING, SUITE_ARTIFACT,
        sleep=clock.sleep, clock=clock.now))


def test_an_expired_wait_takes_the_turn_from_a_holder_that_is_still_there(
        make_target, tmp_path):
    """The takeover, from the file system's side. The waiter's bound expires
    on a holder that never lets go; the waiter's body then runs while the
    holder's handle is still open, the inode at the lock path is a fresh one
    the waiter holds exclusively, the holder keeps its lock on the orphaned
    inode, the takeover lock beside the suite lock is released, and the note
    says the bound expired, this run took the turn, and the holder was left
    running."""
    with_estimate()
    target = make_target("taken-over")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)
    with holding(lock) as hung:
        before = inode_at(lock)
        assert inode_held(hung) == before
        with contextlib.ExitStack() as stack:
            clock = FakeClock()
            enter_turn(stack, tmp_path / "taker", target, config, clock)
            # Inside the taker's body: the holder is still open and still
            # holds its lock, and the path now names a different inode.
            assert not hung.closed
            assert inode_held(hung) == before
            after = inode_at(lock)
            assert after != before
            assert is_held(lock) is True
            assert takeover_lock_of(target).is_file()
            assert is_held(takeover_lock_of(target)) is False
        # The taker's body ended: the new inode is free, and the path keeps it.
        assert inode_at(lock) == after
        assert is_held(lock) is False
        assert not hung.closed

    assert sum(clock.slept) == pytest.approx(bound_of("2"), abs=POLL)
    assert kinds_of(tmp_path / "taker") == [WAITED, NOTE]
    [note] = notes_about_the_turn(tmp_path / "taker")
    assert takeover_notes(tmp_path / "taker") == [note]
    assert f"{bound_of('2') / 60:g} minutes" in note
    assert "without one" not in note


def test_the_held_probe_reports_a_lock_the_test_holds(tmp_path):
    """The control for `is_held` reporting the takeover lock released: the
    same probe on a file this test holds reports it held, and released once
    let go."""
    probe = tmp_path / "probe.lock"
    with holding(probe):
        assert is_held(probe) is True
    assert is_held(probe) is False


def test_a_fresh_entrant_after_a_takeover_queues_behind_the_new_holder(
        make_target, tmp_path):
    """After a takeover the lock path names the taker's inode. A run opening
    the path fresh waits on that inode — not on the hung holder's — and takes
    the turn when the taker's body ends, on the same inode, with one wait
    event and no note."""
    with_estimate()
    target = make_target("fresh-entrant")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)
    with holding(lock) as hung:
        taker = contextlib.ExitStack()
        enter_turn(taker, tmp_path / "taker", target, config, FakeClock())
        held_by_taker = inode_at(lock)
        assert held_by_taker != inode_held(hung)
        ends_at = 30.0
        assert ends_at < bound_of("2")

        def taker_ends(now: float) -> None:
            if now - 1000.0 >= ends_at:
                taker.close()

        entrant_clock = FakeClock(on_advance=taker_ends)
        try:
            ran, entrant_clock = take_turn(tmp_path / "entrant", target, config,
                                           clock=entrant_clock)
        finally:
            taker.close()
        # The entrant took the turn on the taker's inode, not a third one, and
        # the hung holder was never waited on and never released.
        assert inode_at(lock) == held_by_taker
        assert not hung.closed

    assert ran is True
    assert ends_at <= sum(entrant_clock.slept) < ends_at + 2 * POLL
    assert kinds_of(tmp_path / "entrant") == [WAITED]
    assert notes_about_the_turn(tmp_path / "entrant") == []
    assert takeover_notes(tmp_path / "taker") != []


# The three waiters. `fcntl.flock` is per open file description, so three
# handles on one path in one process contend exactly as three processes
# would; each waiter runs on a thread of its own, and all three read one fake
# clock the test advances one poll at a time. Only one waiter runs between
# steps — every other is blocked inside its injected sleep, or inside its
# body waiting to be let out — so the interleaving is the test's and nothing
# here reads a real clock or depends on the machine's scheduling.

SLEEPING = "sleeping"
RUNNING = "running"
IN_BODY = "in its body"
DONE = "done"


class Waiters:
    """Several waiters on one target's lock, stepped from the test."""

    def __init__(self, target: Path, config: dict, root: Path):
        self.target, self.config, self.root = target, config, root
        self.now = 1000.0
        self.condition = threading.Condition()
        self.state: dict[str, str] = {}
        self.wake_at: dict[str, float] = {}
        self.slept: dict[str, list[float]] = {}
        self.released: dict[str, bool] = {}
        self.errors: dict[str, BaseException] = {}
        self.threads: dict[str, threading.Thread] = {}
        #: Called with the waiter's name and the time on every read of the
        #: clock, which is how a test acts at a moment no sleep marks: a
        #: waiter's final read, the one that ends its poll loop, falls after
        #: its last flock attempt and before it goes to take the turn over.
        self.on_clock = None

    def run_dir(self, name: str) -> Path:
        return self.root / name

    def _read_clock(self, name: str) -> float:
        if self.on_clock is not None:
            self.on_clock(name, self.now)
        return self.now

    def _sleep(self, name: str, seconds: float) -> None:
        with self.condition:
            self.slept[name].append(seconds)
            self.wake_at[name] = self.now + seconds
            self.state[name] = SLEEPING
            self.condition.notify_all()
            self.condition.wait_for(lambda: self.state[name] == RUNNING)

    def _run(self, name: str) -> None:
        try:
            with story_coordinator.suite_turn(
                    self.run_dir(name), self.target, self.config, DECLARING,
                    SUITE_ARTIFACT, sleep=lambda seconds: self._sleep(name, seconds),
                    clock=lambda: self._read_clock(name)):
                with self.condition:
                    self.state[name] = IN_BODY
                    self.condition.notify_all()
                    self.condition.wait_for(lambda: self.released[name])
        except BaseException as error:
            # Kept for the stepping side to re-raise: an exception on a
            # thread would otherwise be printed and lost.
            self.errors[name] = error
        finally:
            with self.condition:
                self.state[name] = DONE
                self.condition.notify_all()

    def _until_yielded(self, name: str) -> None:
        with self.condition:
            yielded = self.condition.wait_for(
                lambda: self.state[name] != RUNNING, timeout=PRECONDITION_SECONDS)
        if not yielded:
            pytest.fail(f"waiter {name} neither slept, entered its body nor "
                        f"ended after being let run")
        if name in self.errors:
            raise self.errors[name]

    def start(self, name: str, at: float) -> None:
        """Start a waiter at clock time `at`, and let it run until it sleeps."""
        assert at >= self.now
        self.now = at
        self.run_dir(name).mkdir(parents=True, exist_ok=True)
        self.state[name] = RUNNING
        self.slept[name] = []
        self.released[name] = False
        thread = threading.Thread(target=self._run, args=(name,), daemon=True)
        self.threads[name] = thread
        thread.start()
        self._until_yielded(name)

    def sleeping(self) -> list[str]:
        return sorted(n for n, s in self.state.items() if s == SLEEPING)

    def in_body(self) -> list[str]:
        return sorted(n for n, s in self.state.items() if s == IN_BODY)

    def step(self) -> str:
        """Advance the clock to the earliest pending wake — ties broken by
        name — and let that waiter run until it sleeps again, enters its body
        or ends. Never two in a body at once, checked on every step."""
        sleeping = self.sleeping()
        assert sleeping, "no waiter is sleeping"
        name = min(sleeping, key=lambda n: (self.wake_at[n], n))
        with self.condition:
            self.now = max(self.now, self.wake_at[name])
            self.state[name] = RUNNING
            self.condition.notify_all()
        self._until_yielded(name)
        assert len(self.in_body()) <= 1, self.in_body()
        return name

    def step_until(self, condition, what: str, *, limit: int = 100_000) -> None:
        steps = 0
        while not condition():
            assert steps < limit, f"{limit} steps without {what}"
            self.step()
            steps += 1

    def end_body(self, name: str) -> None:
        """Let a waiter out of its body, which ends its turn and closes its
        handle, and wait until it has."""
        assert self.state[name] == IN_BODY
        with self.condition:
            self.released[name] = True
            self.condition.notify_all()
            ended = self.condition.wait_for(
                lambda: self.state[name] == DONE, timeout=PRECONDITION_SECONDS)
        if not ended:
            pytest.fail(f"waiter {name} did not end after its body was released")
        self.threads[name].join()
        if name in self.errors:
            raise self.errors[name]


def test_three_waiters_behind_a_hung_holder_drain_one_per_bound_and_never_together(
        make_target, tmp_path):
    """The story's central claim. Three waiters queue behind a holder that
    never lets go, started a few seconds apart so their bounds expire in
    order. The first to expire takes the turn and runs its body while the
    holder's handle is still open; the other two each announce that the turn
    changed hands and are still waiting when the first's body ends; they then
    take the turn one after the other, each only after the previous body has
    ended, and never together. Only the taker writes a takeover note."""
    with_estimate()
    target = make_target("three-waiters")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    with holding(lock) as hung:
        hung_inode = inode_held(hung)
        waiters = Waiters(target, config, tmp_path / "waiters")
        waiters.start("first", 1000.0)
        waiters.start("second", 1005.0)
        waiters.start("third", 1010.0)
        for name in ("first", "second", "third"):
            assert kinds_of(waiters.run_dir(name)) == [WAITED]

        # The first's bound expires and it takes the turn, alone.
        waiters.step_until(lambda: waiters.in_body() != [],
                           "a waiter entering its body")
        assert waiters.in_body() == ["first"]
        # The bound is measured on the shared clock, which the other two
        # waiters' starts advanced while the first was asleep; so the first's
        # own sleeps cover the bound less the ten seconds of those starts.
        assert waiters.now == pytest.approx(1000.0 + bound, abs=POLL)
        assert sum(waiters.slept["first"]) == pytest.approx(
            bound - (1010.0 - 1000.0), abs=POLL)
        assert not hung.closed and inode_held(hung) == hung_inode
        new_inode = inode_at(lock)
        assert new_inode != hung_inode
        assert kinds_of(waiters.run_dir("first")) == [WAITED, NOTE]
        assert len(takeover_notes(waiters.run_dir("first"))) == 1

        # The other two notice the turn changed hands, say so, and restart.
        def both_restarted() -> bool:
            return all(len(entries_of_kind(waiters.run_dir(n), WAITED)) == 2
                       for n in ("second", "third"))
        waiters.step_until(both_restarted, "both other waiters restarting")
        assert waiters.in_body() == ["first"]
        assert waiters.sleeping() == ["second", "third"]
        for name in ("second", "third"):
            first_wait, restart = entries_of_kind(waiters.run_dir(name), WAITED)
            assert CHANGED_HANDS not in first_wait["message"]
            assert CHANGED_HANDS in restart["message"]
            assert restart["message"] == story_coordinator._announcement(
                *restart["message"].split("; ", 1))
            assert str(lock) in restart["message"]
            assert FROM_THE_ESTIMATE in restart["message"]
            assert notes_about_the_turn(waiters.run_dir(name)) == []
        # Still waiting when the first's body ends: the restart was announced
        # well inside a fresh bound, so neither has expired again.
        assert waiters.now - (1000.0 + bound) < bound / 2
        waiters.end_body("first")
        assert inode_at(lock) == new_inode

        # Then the second takes the turn on the new inode, and only then the
        # third, each after the previous body ended and neither by takeover.
        waiters.step_until(lambda: waiters.in_body() != [], "the second taking the turn")
        assert waiters.in_body() == ["second"]
        assert waiters.sleeping() == ["third"]
        assert inode_at(lock) == new_inode
        assert kinds_of(waiters.run_dir("second")) == [WAITED, WAITED]
        waiters.step()
        waiters.step()
        assert waiters.in_body() == ["second"] and waiters.sleeping() == ["third"]
        waiters.end_body("second")

        waiters.step_until(lambda: waiters.in_body() != [], "the third taking the turn")
        assert waiters.in_body() == ["third"]
        assert waiters.sleeping() == []
        assert inode_at(lock) == new_inode
        # Three waits, not two: since story-196 the third also announces a
        # restart when the second is granted normally after the taker's body
        # ends — the stamp at the path changed, so its bound started over from
        # that handoff rather than running on from the takeover.
        assert kinds_of(waiters.run_dir("third")) == [WAITED, WAITED, WAITED]
        waiters.end_body("third")

        assert waiters.errors == {}
        assert not hung.closed and inode_held(hung) == hung_inode

    # The takeover note was written by the taker alone.
    assert len(takeover_notes(waiters.run_dir("first"))) == 1
    for name in ("second", "third"):
        assert takeover_notes(waiters.run_dir(name)) == []
        assert notes_about_the_turn(waiters.run_dir(name)) == []
    # Every sleep was one poll or less, and every clock read was the fake's.
    for name in ("first", "second", "third"):
        assert all(0 < step <= POLL for step in waiters.slept[name])


def test_the_stepping_harness_reports_two_waiters_in_their_bodies_at_once(
        make_target, tmp_path):
    """The control for "never together", which `step` asserts on every step
    above: two bodies at once are something the harness can see and reports.
    A waiter bounded at zero runs at once without a turn beside the holder
    it found, which is exactly the overlap a takeover must not produce, and
    the next step over a third, queued waiter fails on it."""
    with_estimate()
    target = make_target("two-bodies")
    with holding(lock_of(target)):
        waiters = Waiters(target, {**config_of(target), KEY: "0"}, tmp_path / "w")
        waiters.start("beside-the-holder", 1000.0)
        assert waiters.in_body() == ["beside-the-holder"]
        assert kinds_of(waiters.run_dir("beside-the-holder")) == [WAITED, NOTE]
        waiters.start("also-beside-it", 1000.0)
        assert waiters.in_body() == ["also-beside-it", "beside-the-holder"]
        waiters.config = {**config_of(target), KEY: "2"}
        waiters.start("queued", 1000.0)
        assert waiters.sleeping() == ["queued"]
        with pytest.raises(AssertionError, match="beside"):
            waiters.step()
        waiters.end_body("beside-the-holder")
        waiters.end_body("also-beside-it")
        assert waiters.in_body() == []
    # The holder let go: the queued waiter takes the turn normally.
    waiters.step_until(lambda: waiters.in_body() == ["queued"],
                       "the queued waiter taking the turn")
    waiters.end_body("queued")
    assert kinds_of(waiters.run_dir("queued")) == [WAITED]


# --------------------------------------------------------------------------
# A takeover meeting a normal release: one suite in flight, on either side
# --------------------------------------------------------------------------
#
# A holder that finishes in the window between a taker's inode check and its
# rename would leave two runs holding a turn: an ordinary waiter granted the
# old inode, and the taker on the fresh one it renamed over the path. The two
# sides are driven separately, each at the one moment the race needs, and on
# each the reading is the same: exactly one run's suite is in flight.


def watching_takeovers(monkeypatch) -> list[tuple[int, bool, int]]:
    """Record every takeover attempt the turn makes: the inode it waited on,
    whether it was handed back the very handle it waited on — the holder
    having let go under the takeover lock — and the inode at the path after."""
    seen: list[tuple[int, bool, int]] = []
    real = story_coordinator._take_over_suite_turn

    def watching(lock_path, handle):
        result = real(lock_path, handle)
        seen.append((inode_held(handle), result is handle, inode_at(lock_path)))
        return result

    monkeypatch.setattr(story_coordinator, "_take_over_suite_turn", watching)
    return seen


def test_a_holder_that_lets_go_as_the_taker_expires_is_taken_on_the_old_inode_and_nothing_is_swapped(
        make_target, tmp_path, monkeypatch):
    """The taker's side. The holder lets go on the taker's final clock read —
    after the taker's last flock attempt inside its poll loop and before its
    retry under the takeover lock — so the retry is granted: the taker runs
    on the old inode, renames nothing over the path and writes no takeover
    note. A second waiter behind it is refused for as long as the taker's body
    runs and is then granted the same inode, so at no moment are two suites
    in flight. Since story-196 that second waiter announces one restart: the
    taker's grant stamped the file, and a changed stamp is the turn having
    changed hands whether or not the inode did — so its bound measures the
    taker from the grant rather than the holder from its own start."""
    with_estimate()
    target = make_target("let-go-at-expiry")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    takeovers = watching_takeovers(monkeypatch)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old_inode = inode_at(lock)
    waiters = Waiters(target, config, tmp_path / "waiters")

    def let_go(name: str, now: float) -> None:
        if name == "taker" and now - 1000.0 >= bound and not holder.closed:
            holder.close()

    waiters.on_clock = let_go
    try:
        waiters.start("taker", 1000.0)
        # Behind the taker, its bound expiring later, so it is refused by
        # the holder throughout the taker's wait and by the taker after.
        waiters.start("other", 1005.0)

        waiters.step_until(lambda: waiters.in_body() != [], "the taker entering its body")
        assert waiters.in_body() == ["taker"]
        assert waiters.now == pytest.approx(1000.0 + bound, abs=POLL)
        assert holder.closed
        # Granted the handle it waited on, under the takeover lock, and the
        # path still names that inode: nothing was swapped over it.
        assert takeovers == [(old_inode, True, old_inode)]
        assert inode_at(lock) == old_inode
        assert is_held(lock) is True
        assert kinds_of(waiters.run_dir("taker")) == [WAITED]
        assert notes_about_the_turn(waiters.run_dir("taker")) == []

        # The other is still refused, by the taker now. It restarts once — the
        # taker's stamp on the same inode is the turn having changed hands —
        # and then keeps waiting, with no note of its own.
        waiters.step()
        waiters.step()
        assert waiters.in_body() == ["taker"] and waiters.sleeping() == ["other"]
        assert kinds_of(waiters.run_dir("other")) == [WAITED, WAITED]
        first_wait, restart = entries_of_kind(waiters.run_dir("other"), WAITED)
        assert CHANGED_HANDS not in first_wait["message"]
        assert CHANGED_HANDS in restart["message"]
        waiters.end_body("taker")

        waiters.step_until(lambda: waiters.in_body() != [], "the other taking the turn")
        assert waiters.in_body() == ["other"]
        assert inode_at(lock) == old_inode
        assert takeovers == [(old_inode, True, old_inode)]
        waiters.end_body("other")
    finally:
        holder.close()

    assert waiters.errors == {}
    assert kinds_of(waiters.run_dir("other")) == [WAITED, WAITED]
    for name in ("taker", "other"):
        assert notes_about_the_turn(waiters.run_dir(name)) == []
        assert all(0 < step <= POLL for step in waiters.slept[name])


def test_the_takeover_swaps_only_a_lock_still_refused_under_the_takeover_lock(
        make_target, tmp_path):
    """The control for the helper the test above watches, from the file
    system's side: with the holder still there the same call renames a fresh
    inode over the path and hands back a different handle, and with the
    holder gone it hands back the handle it was given and moves nothing."""
    target = make_target("helper")
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with holding(lock):
        old_inode = inode_at(lock)
        with open(lock, "a", encoding="utf-8") as waited_on:
            assert story_coordinator._try_suite_lock(waited_on) is False
            fresh = story_coordinator._take_over_suite_turn(lock, waited_on)
            try:
                assert fresh is not waited_on
                assert inode_at(lock) == inode_held(fresh) != old_inode
                assert is_held(lock) is True
            finally:
                fresh.close()
    # The holder let go, and the path names the swapped inode: a waiter on it
    # that goes to take over is granted the very handle it waited on instead.
    with open(lock, "a", encoding="utf-8") as waited_on:
        swapped_inode = inode_at(lock)
        assert story_coordinator._take_over_suite_turn(lock, waited_on) is waited_on
        assert inode_at(lock) == swapped_inode == inode_held(waited_on)
        assert is_held(lock) is True
    assert is_held(takeover_lock_of(target)) is False


def test_a_waiter_granted_the_old_inode_after_the_path_moved_on_requeues_rather_than_running(
        make_target, tmp_path, monkeypatch):
    """The waiter's side. Between this waiter's inode check and its flock
    attempt, a taker whose bound expired renames a fresh file over the path
    and the holder then lets go — so the flock on the old inode is granted.
    The waiter does not run on it: it confirms under the takeover lock that
    the path has moved on, lets the orphan go, reopens the path, announces
    that the turn changed hands, and waits on the taker's inode; only when
    the taker's body ends does it run, on that inode. The taker's suite is
    the one in flight throughout."""
    with_estimate()
    target = make_target("granted-an-orphan")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old_inode = inode_at(lock)
    # Another waiter's handle on the old inode, which the takeover is made with.
    taker_waited_on = open(lock, "a", encoding="utf-8")
    taker: dict[str, object] = {}
    swap_at, taker_ends_at = 30.0, 60.0
    assert taker_ends_at < bound_of("2")
    real_inode_at = story_coordinator._inode_at
    armed = False

    def between_the_check_and_the_flock(lock_path: Path) -> int:
        nonlocal armed
        inode = real_inode_at(lock_path)
        if armed and "handle" not in taker:
            taker["handle"] = None
            fresh = story_coordinator._take_over_suite_turn(lock_path, taker_waited_on)
            assert fresh is not None and fresh is not taker_waited_on
            taker["handle"], taker["inode"] = fresh, inode_held(fresh)
            holder.close()
        return inode

    def on_advance(now: float) -> None:
        nonlocal armed
        if now - 1000.0 >= swap_at:
            armed = True
        if now - 1000.0 >= taker_ends_at and taker.get("handle") is not None:
            taker["handle"].close()

    monkeypatch.setattr(story_coordinator, "_inode_at", between_the_check_and_the_flock)
    clock = FakeClock(on_advance=on_advance)
    try:
        with contextlib.ExitStack() as stack:
            enter_turn(stack, tmp_path / "waiter", target, config, clock)
            # In the body: on the taker's inode, which the path names; the
            # old inode is nobody's, the orphan having been let go.
            assert inode_at(lock) == taker["inode"] != old_inode
            assert is_held(lock) is True
            assert story_coordinator._try_suite_lock(taker_waited_on) is True
            fcntl.flock(taker_waited_on, fcntl.LOCK_UN)
    finally:
        holder.close()
        taker_waited_on.close()
        if taker.get("handle") is not None:
            taker["handle"].close()

    # Granted the orphan at the swap, it kept waiting until the taker's body
    # ended, and ran only then.
    assert taker_ends_at <= sum(clock.slept) < taker_ends_at + 2 * POLL
    assert all(0 < step <= POLL for step in clock.slept)
    first_wait, restart = entries_of_kind(tmp_path / "waiter", WAITED)
    assert kinds_of(tmp_path / "waiter") == [WAITED, WAITED]
    assert CHANGED_HANDS not in first_wait["message"]
    assert CHANGED_HANDS in restart["message"]
    assert str(lock) in restart["message"]
    assert notes_about_the_turn(tmp_path / "waiter") == []
    assert is_held(takeover_lock_of(target)) is False


def test_the_same_drive_without_the_swap_runs_on_the_old_inode(
        make_target, tmp_path):
    """The control for the re-queue above: the holder letting go at the same
    moment with no taker in between is the ordinary grant, taken on the old
    inode with one wait event and no restart — so the restart the test above
    reads is the swap's doing and not the release's."""
    with_estimate()
    target = make_target("no-swap")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old_inode = inode_at(lock)

    def let_go(now: float) -> None:
        if now - 1000.0 >= 30.0 and not holder.closed:
            holder.close()

    try:
        ran, clock = take_turn(tmp_path / "waiter", target, config,
                               clock=FakeClock(on_advance=let_go))
    finally:
        holder.close()
    assert ran is True
    assert inode_at(lock) == old_inode
    assert kinds_of(tmp_path / "waiter") == [WAITED]
    assert 30.0 <= sum(clock.slept) < 30.0 + 2 * POLL


# --------------------------------------------------------------------------
# The bound measures the current holder (story-196)
# --------------------------------------------------------------------------
#
# Every run that takes the turn appends one line to the lock file — its pid
# and the moment it took the turn — and a waiter that sees the file's last
# line change restarts its bound from that poll. The stamp is read here from
# the file itself, never through the coordinator, so what these assert is
# what any process would find at the path.


def stamp_lines(lock_path: Path) -> list[str]:
    """Every line of the file at the lock path, oldest first."""
    return lock_path.read_text(encoding="utf-8").splitlines()


def stamp_at(lock_path: Path) -> str | None:
    """The last line at the lock path, or None where the file carries none."""
    lines = stamp_lines(lock_path)
    return lines[-1] if lines else None


def is_a_stamp_of_this_process(line: str | None) -> bool:
    """Whether a line is a stamp of this process: its pid, a space, and a
    moment that reads as a number. The waiters below run on threads of this
    process, so every stamp any of them writes carries this pid."""
    if line is None:
        return False
    pid, _, moment = line.partition(" ")
    if pid != str(os.getpid()):
        return False
    try:
        float(moment)
    except ValueError:
        return False
    return True


def stagger(names: list[str]) -> dict[str, float]:
    """A start time for each waiter, spread across one poll in the order
    given, so their polls come round in that order every poll and a handoff
    is always seen first by the waiter named first among those still
    waiting. Derived from the poll constant so no number is written here."""
    return {name: 1000.0 + index * POLL / len(names)
            for index, name in enumerate(names)}


def hold_one_suite_then(waiters: Waiters, names: list[str], release) -> None:
    """Let the holder keep the turn for one suite's time, then release it at
    a moment where the next poll is the first-named waiter still waiting: the
    step just taken was the last-named's. Every step in between asserts no
    two waiters are in their bodies at once."""
    took_at = waiters.now
    while True:
        stepped = waiters.step()
        if waiters.now >= took_at + ESTIMATE_SECONDS and stepped == names[-1]:
            break
    release()


def test_six_waiters_behind_healthy_holders_are_granted_in_order_and_none_takes_over(
        make_target, tmp_path):
    """The story's central claim. Six waiters queue behind a holder that
    takes one suite's time, under a bound of five suites; each, once granted,
    holds for one suite's time too. The sixth therefore queues for longer
    than the bound — the wait that expired while the bound measured the
    waiter's own time in the queue — and is granted normally: none writes a
    takeover note, the inode at the path never changes, and no two are ever
    in their bodies at once. Each waiter behind a handoff announces it, so
    the n-th to be granted carries n wait events."""
    with_estimate()
    target = make_target("six-healthy")
    config = {**config_of(target), KEY: "5"}
    bound = bound_of("5")
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    inode = inode_at(lock)
    names = [f"waiter-{n}" for n in range(1, 7)]
    started_at = stagger(names)
    waiters = Waiters(target, config, tmp_path / "waiters")
    try:
        for name in names:
            waiters.start(name, started_at[name])
            assert kinds_of(waiters.run_dir(name)) == [WAITED]
        assert waiters.sleeping() == sorted(names)

        hold_one_suite_then(waiters, names, holder.close)
        granted_at: dict[str, float] = {}
        for index, name in enumerate(names):
            waiters.step_until(lambda: waiters.in_body() != [],
                               f"{name} taking the turn")
            assert waiters.in_body() == [name]
            granted_at[name] = waiters.now
            assert inode_at(lock) == inode
            assert is_a_stamp_of_this_process(stamp_at(lock))
            # One wait event, then one restart per handoff seen: the first
            # saw none, the sixth saw five.
            assert kinds_of(waiters.run_dir(name)) == [WAITED] * (index + 1)
            first_wait, *restarts = entries_of_kind(waiters.run_dir(name), WAITED)
            assert CHANGED_HANDS not in first_wait["message"]
            assert all(CHANGED_HANDS in restart["message"] for restart in restarts)
            assert notes_about_the_turn(waiters.run_dir(name)) == []
            if waiters.sleeping():
                hold_one_suite_then(waiters, names, lambda: waiters.end_body(name))
                assert waiters.in_body() == []
            else:
                waiters.end_body(name)
    finally:
        holder.close()

    assert waiters.errors == {}
    assert list(granted_at) == names
    # The last waiter queued for longer than the bound, so under a bound that
    # measured its own time in the queue it would have expired and taken the
    # turn over — the absence of a takeover above is asserted against a wait
    # that would have produced one.
    last = names[-1]
    assert granted_at[last] - started_at[last] > bound + POLL
    assert inode_at(lock) == inode
    for name in names:
        assert takeover_notes(waiters.run_dir(name)) == []
        assert all(0 < step <= POLL for step in waiters.slept[name])


def test_a_waiter_behind_a_normal_handoff_announces_the_restart_and_measures_the_new_holder(
        make_target, tmp_path):
    """A holder lets go inside the bound; the waiter ahead is granted and the
    one behind announces, through a second wait event whose message says the
    turn changed hands, that its bound starts over from the handoff. The new
    holder then keeps the turn past the moment the waiter's original bound
    would have expired, and the waiter is still waiting: it is granted only
    when the holder finishes, with no takeover note, on the same inode."""
    with_estimate()
    target = make_target("normal-handoff")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    inode = inode_at(lock)
    names = ["ahead", "behind"]
    started_at = stagger(names)
    lets_go_at = 30.0
    assert lets_go_at < bound
    waiters = Waiters(target, config, tmp_path / "waiters")
    try:
        for name in names:
            waiters.start(name, started_at[name])
        while True:
            stepped = waiters.step()
            if waiters.now >= 1000.0 + lets_go_at and stepped == names[-1]:
                break
        holder.close()

        waiters.step_until(lambda: waiters.in_body() != [], "the ahead taking the turn")
        assert waiters.in_body() == ["ahead"]
        handoff_at = waiters.now
        assert inode_at(lock) == inode
        assert kinds_of(waiters.run_dir("ahead")) == [WAITED]

        waiters.step()
        assert waiters.sleeping() == ["behind"]
        assert kinds_of(waiters.run_dir("behind")) == [WAITED, WAITED]
        first_wait, restart = entries_of_kind(waiters.run_dir("behind"), WAITED)
        assert CHANGED_HANDS not in first_wait["message"]
        assert CHANGED_HANDS in restart["message"]
        assert restart["message"] == story_coordinator._announcement(
            *restart["message"].split("; ", 1))
        assert str(lock) in restart["message"]
        assert FROM_THE_ESTIMATE in restart["message"]

        # Past the waiter's original expiry and still inside the bound from
        # the handoff: the ahead holds, the behind waits, nothing was taken.
        original_expiry = started_at["behind"] + bound
        waiters.step_until(lambda: waiters.now > original_expiry + POLL,
                           "the original bound passing")
        assert waiters.now < handoff_at + bound
        assert waiters.in_body() == ["ahead"] and waiters.sleeping() == ["behind"]
        assert kinds_of(waiters.run_dir("behind")) == [WAITED, WAITED]
        assert inode_at(lock) == inode
        waiters.end_body("ahead")

        waiters.step_until(lambda: waiters.in_body() != [], "the behind taking the turn")
        assert waiters.in_body() == ["behind"]
        assert waiters.now > original_expiry
        assert inode_at(lock) == inode
        waiters.end_body("behind")
    finally:
        holder.close()

    assert waiters.errors == {}
    assert kinds_of(waiters.run_dir("behind")) == [WAITED, WAITED]
    for name in names:
        assert notes_about_the_turn(waiters.run_dir(name)) == []
        assert takeover_notes(waiters.run_dir(name)) == []
        assert all(0 < step <= POLL for step in waiters.slept[name])


def test_a_stamped_holder_that_hangs_after_a_handoff_is_taken_over_at_the_bound_from_the_handoff(
        make_target, tmp_path):
    """The other side of the restart: the bound really does start over. A
    waiter queues behind a stampless holder; the holder lets go and a run
    that takes its turn through the coordinator — so it stamps the file —
    becomes the holder and never finishes. The waiter restarts at the handoff
    and expires one bound after it, not one bound after it began waiting;
    only then does it take the turn over, with the note and the fresh inode a
    takeover leaves, and its stamp replaces the hung holder's at the path."""
    with_estimate()
    target = make_target("hung-after-handoff")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old_inode = inode_at(lock)
    lets_go_at = 30.0
    assert lets_go_at > 2 * POLL, "the two expiries must be more than a poll apart"
    waiters = Waiters(target, config, tmp_path / "waiters")
    hung = contextlib.ExitStack()
    try:
        waiters.start("behind", 1000.0)
        waiters.step_until(lambda: waiters.now >= 1000.0 + lets_go_at,
                           "the holder's time to let go")
        assert stamp_at(lock) is None
        holder.close()
        enter_turn(hung, tmp_path / "hung", target, config, FakeClock())
        hung_stamp = stamp_at(lock)
        assert is_a_stamp_of_this_process(hung_stamp)
        assert inode_at(lock) == old_inode

        waiters.step()
        assert waiters.sleeping() == ["behind"]
        assert kinds_of(waiters.run_dir("behind")) == [WAITED, WAITED]
        restarted_at = waiters.now
        [_, restart] = entries_of_kind(waiters.run_dir("behind"), WAITED)
        assert CHANGED_HANDS in restart["message"]

        waiters.step_until(lambda: waiters.in_body() != [], "the behind expiring")
        assert waiters.in_body() == ["behind"]
        # One bound after the handoff, which is later than one bound after
        # the waiter began by more than the poll's slack.
        assert waiters.now == pytest.approx(restarted_at + bound, abs=POLL)
        assert waiters.now > 1000.0 + bound + POLL
        assert inode_at(lock) != old_inode
        assert stamp_at(lock) != hung_stamp
        assert is_a_stamp_of_this_process(stamp_at(lock))
        assert kinds_of(waiters.run_dir("behind")) == [WAITED, WAITED, NOTE]
        assert len(takeover_notes(waiters.run_dir("behind"))) == 1
        waiters.end_body("behind")
    finally:
        holder.close()
        hung.close()

    assert waiters.errors == {}
    assert all(0 < step <= POLL for step in waiters.slept["behind"])


def test_a_stamped_hung_holder_is_taken_over_by_the_first_to_expire_and_the_rest_restart(
        make_target, tmp_path):
    """The three-waiter drain behind a holder that stamped the file, taken
    through the coordinator rather than a bare lock. The first to expire
    takes the turn and its stamp replaces the holder's at the path; the
    other two announce the change of hands and are still waiting when the
    taker's body ends; they then drain one after the other, the last
    announcing the second's grant too, and never together."""
    with_estimate()
    target = make_target("stamped-hung")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    hung = contextlib.ExitStack()
    enter_turn(hung, tmp_path / "hung", target, config, FakeClock())
    hung_inode = inode_at(lock)
    hung_stamp = stamp_at(lock)
    assert is_a_stamp_of_this_process(hung_stamp)
    names = ["first", "second", "third"]
    started_at = stagger(names)
    waiters = Waiters(target, config, tmp_path / "waiters")
    try:
        for name in names:
            waiters.start(name, started_at[name])

        waiters.step_until(lambda: waiters.in_body() != [], "a waiter entering its body")
        assert waiters.in_body() == ["first"]
        assert waiters.now == pytest.approx(1000.0 + bound, abs=POLL)
        new_inode = inode_at(lock)
        assert new_inode != hung_inode
        assert stamp_at(lock) != hung_stamp
        assert is_a_stamp_of_this_process(stamp_at(lock))
        assert kinds_of(waiters.run_dir("first")) == [WAITED, NOTE]
        assert len(takeover_notes(waiters.run_dir("first"))) == 1

        waiters.step()
        waiters.step()
        assert waiters.in_body() == ["first"]
        assert waiters.sleeping() == ["second", "third"]
        for name in ("second", "third"):
            first_wait, restart = entries_of_kind(waiters.run_dir(name), WAITED)
            assert CHANGED_HANDS not in first_wait["message"]
            assert CHANGED_HANDS in restart["message"]
        waiters.end_body("first")

        waiters.step_until(lambda: waiters.in_body() != [], "the second taking the turn")
        assert waiters.in_body() == ["second"]
        assert inode_at(lock) == new_inode
        waiters.step()
        assert waiters.sleeping() == ["third"]
        assert kinds_of(waiters.run_dir("third")) == [WAITED, WAITED, WAITED]
        waiters.end_body("second")

        waiters.step_until(lambda: waiters.in_body() != [], "the third taking the turn")
        assert waiters.in_body() == ["third"]
        assert inode_at(lock) == new_inode
        waiters.end_body("third")
    finally:
        hung.close()

    assert waiters.errors == {}
    for name in ("second", "third"):
        assert takeover_notes(waiters.run_dir(name)) == []
        assert notes_about_the_turn(waiters.run_dir(name)) == []
    for name in names:
        assert all(0 < step <= POLL for step in waiters.slept[name])


def test_a_stampless_lock_file_is_measured_from_the_first_poll_exactly_as_a_stamped_one(
        make_target, tmp_path):
    """A holder that took the lock without writing a stamp — a bare flock, as
    a harness older than the stamp would leave — is measured from the first
    poll that sees it: one wait event, no restart, and the takeover at the
    bound. Beside it the same wait behind a holder that did stamp, whose
    sleeps are the very same sequence."""
    with_estimate()
    target = make_target("stampless")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    with holding(lock):
        # The control for the absence the case turns on: the file carries no
        # line at all before the wait.
        assert stamp_at(lock) is None
        ran, stampless = take_turn(tmp_path / "stampless", target, config)
        assert is_a_stamp_of_this_process(stamp_at(lock))
    assert ran is True
    assert sum(stampless.slept) == pytest.approx(bound, abs=POLL)
    assert kinds_of(tmp_path / "stampless") == [WAITED, NOTE]
    assert len(takeover_notes(tmp_path / "stampless")) == 1

    hung = contextlib.ExitStack()
    try:
        enter_turn(hung, tmp_path / "hung", target, config, FakeClock())
        assert is_a_stamp_of_this_process(stamp_at(lock))
        ran, stamped = take_turn(tmp_path / "stamped", target, config)
    finally:
        hung.close()
    assert ran is True
    assert stamped.slept == stampless.slept
    assert kinds_of(tmp_path / "stamped") == [WAITED, NOTE]
    assert len(takeover_notes(tmp_path / "stamped")) == 1


def test_every_route_that_takes_the_turn_leaves_this_runs_stamp_as_the_last_line_before_the_body_runs(
        make_target, tmp_path):
    """Read from inside the body on each route: the uncontended first grant,
    a second uncontended grant (appended, the earlier line kept), a grant
    inside the bound after the holder lets go, the fresh file a takeover
    renames over the path — one line, the taker's — and the grant on the old
    inode inside the takeover helper when the holder has let go."""
    with_estimate()
    target = make_target("every-route")
    config = {**config_of(target), KEY: "2"}
    lock = lock_of(target)

    with contextlib.ExitStack() as stack:
        enter_turn(stack, tmp_path / "uncontended", target, config, FakeClock())
        [first] = stamp_lines(lock)
        assert is_a_stamp_of_this_process(first)
    with contextlib.ExitStack() as stack:
        enter_turn(stack, tmp_path / "again", target, config, FakeClock())
        lines = stamp_lines(lock)
        assert lines[:-1] == [first]
        assert is_a_stamp_of_this_process(lines[-1])
    before = stamp_lines(lock)

    holder = open(lock, "a", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def let_go(now: float) -> None:
        if now - 1000.0 >= 30.0 and not holder.closed:
            holder.close()

    try:
        with contextlib.ExitStack() as stack:
            enter_turn(stack, tmp_path / "granted", target, config,
                       FakeClock(on_advance=let_go))
            lines = stamp_lines(lock)
            assert lines[:-1] == before
            assert is_a_stamp_of_this_process(lines[-1])
    finally:
        holder.close()
    assert kinds_of(tmp_path / "granted") == [WAITED]
    before = stamp_lines(lock)

    with holding(lock) as hung:
        with contextlib.ExitStack() as stack:
            enter_turn(stack, tmp_path / "taker", target, config, FakeClock())
            assert inode_at(lock) != inode_held(hung)
            [only] = stamp_lines(lock)
            assert is_a_stamp_of_this_process(only)
            assert only not in before
    assert len(takeover_notes(tmp_path / "taker")) == 1
    before = stamp_lines(lock)

    with open(lock, "a", encoding="utf-8") as waited_on:
        assert story_coordinator._take_over_suite_turn(lock, waited_on) is waited_on
        lines = stamp_lines(lock)
        assert lines[:-1] == before
        assert is_a_stamp_of_this_process(lines[-1])


def test_a_stamp_that_is_no_pid_and_no_time_is_waited_behind_like_any_other(
        make_target, tmp_path):
    """The stamp is compared for equality and read for nothing else, shown
    from the file's side: a holder whose last line names no pid and no time
    is waited behind for the whole bound and taken over like any other, and
    so is one whose line carries this process's pid and a moment long before
    the clock — which a bound that read the time would have expired at
    once, and a takeover that signalled the pid would have aimed at this
    process."""
    with_estimate()
    target = make_target("no-pid-no-time")
    config = {**config_of(target), KEY: "2"}
    bound = bound_of("2")
    lock = lock_of(target)
    lines = {"garbage": "no-such-pid long-ago", "own-pid-long-ago": f"{os.getpid()} 0.0"}
    for label, line in lines.items():
        with holding(lock) as hung:
            hung.write(f"{line}\n")
            hung.flush()
            assert stamp_at(lock) == line
            ran, clock = take_turn(tmp_path / label, target, config)
        assert ran is True
        assert sum(clock.slept) == pytest.approx(bound, abs=POLL)
        assert all(0 < step <= POLL for step in clock.slept)
        assert kinds_of(tmp_path / label) == [WAITED, NOTE]
        assert len(takeover_notes(tmp_path / label)) == 1


def test_an_uncontended_turn_writes_nothing_and_sleeps_nothing(make_target, tmp_path):
    """The absence the two-process control above demonstrates can fail: a free
    lock is taken on the first try, and the events log gains no line."""
    target = make_target("free")
    ran, clock = take_turn(tmp_path / "run", target, config_of(target))
    assert ran is True
    assert clock.slept == []
    assert not (tmp_path / "run" / "events.log").exists()


# --------------------------------------------------------------------------
# The key: refused when malformed, accepted when a count
# --------------------------------------------------------------------------

NOT_BOUNDS = ["an hour", "-1", "", "nan", "6,0", "inf minus"]
ARE_BOUNDS = ["0", "1", "0.5", "5", "60"]


@pytest.mark.parametrize("value", ARE_BOUNDS)
def test_a_non_negative_number_of_suite_runs_is_accepted(value):
    assert story_coordinator.suite_turn_wait_problems({KEY: value}) == []
    # Without an estimate the bound is the fallback, except that zero is zero.
    expected = 0.0 if float(value) == 0 else FALLBACK_SECONDS
    assert story_coordinator.suite_turn_wait_seconds({KEY: value}) == expected
    with_estimate()
    assert story_coordinator.suite_turn_wait_seconds({KEY: value}) == bound_of(value)


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


def test_a_configuration_still_carrying_the_old_key_is_refused_naming_both_keys(
        make_target, harness_root, capsys):
    """The retired key is refused by the undeclared-key check rather than
    read: the message names the old key and lists the declared set, the new
    key among it, with no run directory created and no agent invoked."""
    stale = make_target("stale")
    configure_key(stale, OLD_KEY, "60")
    code, runner, run_dir = drive(stale, harness_root)

    assert code == 1
    assert runner.calls == []
    assert not run_dir.exists()
    message = capsys.readouterr().err
    assert OLD_KEY in message and KEY in message
    # The old key is refused as undeclared and not as a bad bound: the
    # bound's own check does not read it.
    assert story_coordinator.suite_turn_wait_problems({OLD_KEY: "an hour"}) == []


def test_the_key_is_declared_and_set_in_both_configurations():
    schema = schema_validator.load_schema("harness-config")
    assert KEY in schema["properties"]
    assert OLD_KEY not in schema["properties"]
    assert OLD_KEY not in json.dumps(schema)
    description = schema["properties"][KEY]["description"]
    assert "zero" in description.lower()
    assert "sixty minutes" in description and "fallback" in description
    assert "pre-flight" in description
    assert "takes the turn" in description
    assert conftest.repository_config()[KEY] == "5"
    assert OLD_KEY not in conftest.repository_config()
    template = (REPO_ROOT / "templates" / "config.yaml").read_text(encoding="utf-8")
    assert f"{KEY}: 5" in template and OLD_KEY not in template
    assert "counts suite runs" in template
    assert "duration" in template and "core count" in template
    shipped = (REPO_ROOT / ".harness" / "config.yaml").read_text(encoding="utf-8")
    assert f"{KEY}: 5" in shipped and OLD_KEY not in shipped
    assert "counts suite runs" in shipped
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


def test_the_takeover_and_the_restart_are_written_from_the_turns_own_body_under_no_new_kind():
    """Every event of the wait — the first announcement, the restart and the
    takeover note — is written by `suite_turn`'s own body, and the only kind
    it names is the one story-167 declared: the restart is a second event of
    that kind and the takeover a note, so the history enum gains nothing."""
    turn = conftest.function_source(COORDINATOR_SOURCE, "suite_turn")
    named_kinds = {node.value.value
                   for node in ast.walk(ast.parse(turn))
                   if isinstance(node, ast.keyword) and node.arg == "kind"
                   and isinstance(node.value, ast.Constant)}
    assert named_kinds == {WAITED}
    assert CHANGED_HANDS in turn
    for clause in TAKEOVER_NOTE:
        assert clause in turn
    history = schema_validator.load_schema("execution-history")
    kinds = history["items"]["properties"]["event"]["enum"]
    assert not [kind for kind in kinds if "takeover" in kind or "restart" in kind]


def test_the_takeover_swaps_a_locked_file_over_the_path_under_the_takeover_lock():
    """The mechanism, read off the source: the takeover renames a fresh file
    over the lock path — never unlinks it — while the takeover lock is held,
    and the lock name sits beside the suite lock's."""
    takeover = conftest.function_source(COORDINATOR_SOURCE, "_take_over_suite_turn")
    assert "SUITE_TAKEOVER_LOCK_NAME" in takeover
    assert ".replace(" in takeover or "rename(" in takeover
    assert "unlink(lock_path" not in takeover and "lock_path.unlink" not in takeover
    assert story_coordinator.SUITE_TAKEOVER_LOCK_NAME != story_coordinator.SUITE_LOCK_NAME
    assert "/" not in story_coordinator.SUITE_TAKEOVER_LOCK_NAME


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


# --------------------------------------------------------------------------
# The stamp is read for equality only, and the poll is named by its constant
# --------------------------------------------------------------------------

#: The reader of the stamp at the path, named once here: every use of what
#: it returns is what the scan below walks.
STAMP_READER = "_stamp_at"


def _is_equality(node: ast.AST) -> bool:
    return (isinstance(node, ast.Compare)
            and all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops))


def _calls(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == name)


def stamp_reads_beyond_equality(source: str) -> list[str]:
    """Every use of a stamp read off the lock file that is not a comparison
    for equality: a call to the reader whose result goes anywhere but an
    assignment or an equality, and a load of a name assigned from it that is
    anything but an operand of an equality. Each is reported as the
    statement or expression it sits in."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    holding_a_stamp = {
        target.id
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and _calls(node.value, STAMP_READER)
        for target in node.targets if isinstance(target, ast.Name)
    }
    found = []
    for node in ast.walk(tree):
        parent = parents.get(node)
        if _calls(node, STAMP_READER):
            if isinstance(parent, ast.Assign) or _is_equality(parent):
                continue
            found.append(ast.unparse(parent))
        elif (isinstance(node, ast.Name) and node.id in holding_a_stamp
              and isinstance(node.ctx, ast.Load) and not _is_equality(parent)):
            found.append(ast.unparse(parent))
    return found


def test_the_stamp_is_never_read_for_anything_but_equality():
    """No code in the coordinator signals a pid read from the lock file or
    compares a time read from it against a clock: the reader's result is
    assigned or compared for equality and nothing else, and neither the
    writer nor the reader nor the turn names a signal or parses a stamp."""
    assert stamp_reads_beyond_equality(COORDINATOR_SOURCE) == []
    reader = conftest.function_source(COORDINATOR_SOURCE, STAMP_READER)
    writer = conftest.function_source(COORDINATOR_SOURCE, "_stamp_suite_turn")
    turn = conftest.function_source(COORDINATOR_SOURCE, "suite_turn")
    takeover = conftest.function_source(COORDINATOR_SOURCE, "_take_over_suite_turn")
    for body in (reader, writer, turn, takeover):
        assert "kill" not in body
        assert "SIG" not in body
    for parse in ("int(", "float(", ".split("):
        assert parse not in reader
    assert f"{STAMP_READER}(" in turn and f"{STAMP_READER}(" not in takeover
    assert "_stamp_suite_turn(" in turn and "_stamp_suite_turn(" in takeover


PLANTED_STAMP_READS = '''\
def suite_turn(lock_path, handle, clock):
    last_stamp = _stamp_at(lock_path)
    pid, moment = last_stamp.split(" ")
    if clock() - float(moment) > 60:
        kill(int(pid), 15)
    if float(_stamp_at(lock_path)) < clock():
        pass
    return _stamp_at(lock_path) != last_stamp
'''


def test_the_same_scan_reports_a_planted_body_that_parses_and_signals_a_stamp():
    """The control for the absence above: a body that splits a stamp, reads
    its time against the clock and signals its pid is reported at each of
    those uses, and its one equality is not."""
    assert stamp_reads_beyond_equality(PLANTED_STAMP_READS) == [
        "last_stamp.split", "float(_stamp_at(lock_path))"]


def comment_above(source: str, declaration: str) -> str:
    """The `#:` comment block immediately above the line beginning with
    `declaration`, joined into one string."""
    lines = source.splitlines()
    [index] = [i for i, line in enumerate(lines) if line.startswith(declaration)]
    block = []
    while index > 0 and lines[index - 1].startswith("#:"):
        index -= 1
        block.insert(0, lines[index][2:].strip())
    return " ".join(block)


def wait_driving_modules() -> list[Path]:
    """The modules under tests/ that enter the turn, which are the ones whose
    sleeps and slacks are measured against the poll."""
    return sorted(path for path in (REPO_ROOT / "tests").glob("test_*.py")
                  if "suite_turn(" in path.read_text(encoding="utf-8"))


def floats_naming_the_poll(source: str, *, except_functions: tuple[str, ...] = ()
                           ) -> list[str]:
    """Every float literal in `source` equal to the poll interval or to the
    half second it replaced, outside the functions named — the assertion
    that the constant has its value, and this scan, which has to write the
    retired value to look for it."""
    retired = 0.5
    tree = ast.parse(source)
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.FunctionDef)
                         and node.name in except_functions)]
    return [repr(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
            and node.value in (POLL, retired)]


def test_the_poll_is_five_seconds_named_by_its_constant_and_its_comment_says_why():
    """The poll interval is five seconds; the comment above it says why five
    — measured against a suite that takes minutes, and a hung holder measured
    in suite runs — and that a blocking flock interrupted by an alarm was
    considered and not taken; and no module that drives the wait names the
    number, old or new, rather than the constant."""
    assert story_coordinator.SUITE_TURN_POLL_SECONDS == 5.0
    comment = comment_above(COORDINATOR_SOURCE, "SUITE_TURN_POLL_SECONDS =")
    assert "five" in comment.lower()
    assert "suite run" in comment
    assert "alarm" in comment and "flock" in comment
    assert "not taken" in comment
    assert wait_driving_modules() != []
    own = (test_the_poll_is_five_seconds_named_by_its_constant_and_its_comment_says_why.__name__,
           floats_naming_the_poll.__name__)
    for module in wait_driving_modules():
        except_functions = own if module.name == Path(__file__).name else ()
        assert floats_naming_the_poll(
            module.read_text(encoding="utf-8"), except_functions=except_functions
        ) == [], module.name


def test_the_same_scan_reports_a_planted_module_that_names_the_poll():
    """The control for the absence above: a module that writes the number as
    a slack, and one that writes the retired half second, are each reported,
    and one that reads the constant is not."""
    assert floats_naming_the_poll(
        f"assert sum(slept) == approx(bound, abs={POLL!r})") == [repr(POLL)]
    assert floats_naming_the_poll("assert all(step <= 0.5 for step in slept)") == ["0.5"]
    assert floats_naming_the_poll(
        "assert all(step <= story_coordinator.SUITE_TURN_POLL_SECONDS for step in slept)"
    ) == []
