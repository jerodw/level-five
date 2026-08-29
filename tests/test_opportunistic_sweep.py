"""Independent validation for story-092: the sweep runs where failure is free.

The outbox is drained opportunistically at the points where a failure to drain
costs nothing — a sweep in the l5-run pre-flight, a sweep after the completion
commit, and the queue surfaced read-only in l5-status. The one thing every
assertion here is ultimately about is that none of those can stop a run.

The subjects are kept apart deliberately:

  * **the guarantee.** A full run whose every sweep fails, driven first because
    it is what the whole story is for. The queue is seeded pending, the
    configured sync command answers every entry with the transient exit code,
    and the run must still pass pre-flight, still invoke every stage, still
    commit its work, still complete, and exit with the status it would have had
    with an empty queue — which is asserted by running the same fixture with an
    empty queue and comparing, rather than by writing a zero here.

  * **the ordering.** Observed rather than read off the source. The sync
    command records, for every entry it is handed, the subject of the commit at
    the target's HEAD at that moment, and the fake agent runner records each
    stage it is asked for, both into one journal. So the journal itself says
    that the pre-flight sweep ran before the first stage and that the
    completion sweep ran on a HEAD the completion commit had already moved.
    Nothing about the ordering is inferred from where a call sits in a file.

  * **the non-refusal.** `outbox_sweep.sweep` is driven through every way it
    can go wrong — a timeout that is not a positive number, a limit that is
    not a positive integer, a transport that is absent, one that raises on
    every entry, one that answers with nonsense, and a queue that cannot be
    listed — and each must come back as a summary carrying the reason. Beside
    that, the shape that makes those cases exhaustive: the function is asked
    for its signature and its return, and the coordinator's two call sites are
    scanned for any use of what comes back.

  * **the runs that sweep nothing.** Every pre-flight above the sweep is driven
    to its refusal against a queue whose entries would land if anything filed
    them, and the queue must be exactly what it was. The control is the
    identical fixture with nothing broken, where the same queue does drain.

  * **the bound.** Driven at `outbox.sync` against a fake transport built here,
    because what the bound bounds is a count of filing attempts and a fake is
    the only transport that can be counted without a subprocess.

  * **the listing.** `run_status.format_queue` and `scripts/l5-status`, against
    a queue holding one of each state, with `sync_command` configured — so a
    sweep would have had a transport to build, and the assertion that none was
    built is about the listing rather than about a target with nothing to build
    from.

Every absence asserted here carries a demonstration that it can fail:

  * "a refused run left the queue exactly as it was" sits beside the same
    fixture with nothing broken, where the same comparison reports the queue
    drained;
  * "an escalated run swept nothing after its pre-flight" sits beside the
    completing run in the same shape, whose journal holds the second sweep;
  * "l5-status built no transport" sits beside the same probe running a sweep
    instead, where the same import check reports the transport module loaded,
    and beside the same queue swept, where the same byte comparison reports the
    entries rewritten;
  * "the call sites read nothing from the sweep's result" sits beside the same
    scan over a source with an assignment and a branch planted in it, which the
    scan must report;
  * "the retired sentences are gone from the two docstrings" sits beside the
    same search over the sentence itself, which must find it.

Nothing here reaches a network: every transport is either a fake built in this
module or the target's own shell script. Nothing here invokes a model.
"""
from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import agent_runner
import harness_config
import outbox
import outbox_sweep
import run_status
import story_coordinator
from agent_runner import AgentResult, CapacityStop
from conftest import StageRef, workflow_stage

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
SCRIPTS = REPO_ROOT / "scripts"

#: The queue as a repository-relative path and as the line that ignores it,
#: derived from the queue module's own constant rather than written here.
QUEUE_REL = "/".join(outbox.QUEUE_DIR)

STORY_ID = "story-001"

PASSED = {"status": "passed", "blocking_issues": [], "unverified": [],
          "retry_recommended": False}

#: One capacity signal the agent runner holds, read off the constant so this
#: module names no signal of its own.
A_CAPACITY_SIGNAL = agent_runner.CAPACITY_SIGNALS[0]


# ==========================================================================
# The workflow, the target, and the sync command that records what it was
# asked to file
# ==========================================================================


#: The definition the runs below execute. Built rather than resolved: whether a
#: run sweeps, completes, escalates or pauses is a property of the coordinator,
#: and the definition it walks is an input to it. A writer and a verifier are
#: enough to reach a verdict and a commit, and the names are the builder's.
WORKFLOW = conftest.build_workflow(
    workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result"},
        retry_routing={"the-code": {"stage": StageRef(0),
                                    "when": "the behaviour is missing"}}),
    name="opportunistic-sweep-workflow",
)

WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

#: Where the target's sync command lives inside the target, and the exit code
#: it answers with. 75 is the transport's own name for a transient failure,
#: read off the module rather than spelled, and it is what makes every sweep in
#: this module's runs *fail*: the entry stays pending, so the next sweep files
#: it again and the run has to complete over a queue that never drains.
SYNC_COMMAND_REL = "sync/records-and-fails.sh"

IDENTITY = {"kind": "sample", "subject": "story-001"}
OTHER_IDENTITY = {"kind": "sample", "subject": "story-002"}
PAYLOAD = {"title": "something to file", "body": "what it says"}


@pytest.fixture
def harness(tmp_path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "sweep-harness")


def build_target(root: Path, journal: Path, **config_keys) -> Path:
    """A target repository a run can execute in, with a recording sync command.

    The same shape `conftest.target_root` builds — its config and its story,
    read off conftest so neither is spelled twice — with three additions this
    module needs: the queue ignored, so a pending entry does not dirty the tree
    and refuse the very run the queue exists never to refuse; a sync command
    installed at the configured path; and whatever configuration the caller
    wants departed from.

    The command writes into `journal`, which is deliberately **outside** the
    target: a file the sweep wrote inside it would be work no stage produced,
    and the run would be refused for what the observation cost rather than for
    anything the story is about.
    """
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs", "src", "sync"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config = conftest.CONFIG.format(workflow=WORKFLOW["name"])
    config += f"sync_command: {SYNC_COMMAND_REL}\n"
    config += "".join(f"{key}: {value}\n"
                      for key, value in sorted(config_keys.items()))
    (root / ".harness" / "config.yaml").write_text(config, encoding="utf-8")
    (root / ".harness" / "stories" / f"{STORY_ID}.yaml").write_text(
        conftest.STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ".harness" / "docs" / "ARCHITECTURE.md").write_text(
        "# Sample Architecture\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / ".gitignore").write_text(f"{QUEUE_REL}/\n", encoding="utf-8")

    command = root / SYNC_COMMAND_REL
    command.write_text(
        "#!/bin/sh\n"
        # What was filed, and what the target's HEAD was when it was filed.
        # The second half is the whole of how the ordering below is observed.
        f'printf "filed %s at %s\\n" "$L5_SYNC_KEY" "$(git log -1 --format=%s)"'
        f' >> "{journal}"\n'
        f"exit {conftest_transient_exit_code()}\n",
        encoding="utf-8")
    command.chmod(0o755)

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", SETUP_SUBJECT)
    return root


def conftest_transient_exit_code() -> int:
    """The transport's own code for a transient failure, imported where it is
    used so this module carries no second spelling of it."""
    import command_transport

    return command_transport.TRANSIENT_EXIT_CODE


#: The subject of the commit the target is built on, which is therefore the
#: HEAD the pre-flight sweep sees. Distinctive so the journal can be read.
SETUP_SUBJECT = "the tree this run starts from"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def head_subject(root: Path) -> str:
    return _git(root, "log", "-1", "--format=%s").stdout.strip()


def journal_lines(journal: Path) -> list[str]:
    if not journal.is_file():
        return []
    return [line for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def filings(journal: Path) -> list[str]:
    """The HEAD subject recorded by each filing the sync command was asked for."""
    return [line.split(" at ", 1)[1] for line in journal_lines(journal)
            if line.startswith("filed ")]


def stages_invoked(journal: Path) -> list[str]:
    return [line.split(" ", 1)[1] for line in journal_lines(journal)
            if line.startswith("stage ")]


def seeded_pending(queue: Path, identity: dict = IDENTITY) -> str:
    return outbox.enqueue(queue, PAYLOAD, identity)


def entry_of(queue: Path, key: str) -> dict:
    return json.loads(outbox.entry_path(queue, key).read_text(encoding="utf-8"))


def queue_bytes(queue: Path) -> dict[str, bytes]:
    """Every file the queue holds, by name, as bytes.

    Bytes rather than parsed objects, because "exactly as it was" is a claim
    about the files and not about what they would parse to.
    """
    if not queue.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(queue.iterdir())}


class Runner:
    """The fake agent runner, writing each stage's outputs and journalling.

    `fails_at` names a stage whose invocation comes back not-ok, with
    `capacity` deciding whether that failure is a capacity stop — which is how
    the escalated run and the paused run below are produced from one runner.
    """

    def __init__(self, target_root: Path, journal: Path, *,
                 fails_at: str | None = None, capacity=None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.journal = journal
        self.fails_at = fails_at
        self.capacity = capacity
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 **declared):
        self.calls.append(stage)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(f"stage {stage}\n")

        if stage == self.fails_at:
            return AgentResult(ok=False, result_text=f"{stage} stopped",
                               capacity=self.capacity)

        if stage == WRITING:
            (self.run_dir / conftest.CHANGED_FILES).write_text(
                json.dumps({"modified": ["src/app.py"], "created": [],
                            "deleted": []}), encoding="utf-8")
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            (self.target_root / "src" / "app.py").write_text(
                "print('the work')\n", encoding="utf-8")
        elif stage == VERIFYING:
            (self.run_dir / conftest.VERIFICATION_RESULT).write_text(
                json.dumps(PASSED), encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(target: Path, harness: Path, runner: Runner) -> int:
    """One `run_story`, with the wait injected so nothing here actually waits."""
    return story_coordinator.run_story(
        STORY_ID, harness, target, runner, sleep=lambda _seconds: None)


def state_of(target: Path) -> dict:
    return json.loads(
        (target / ".harness" / "runs" / STORY_ID / "state.json")
        .read_text(encoding="utf-8"))


def committed_paths(root: Path) -> list[str]:
    listed = _git(root, "show", "--name-only", "--format=", "HEAD")
    return [line for line in listed.stdout.splitlines() if line.strip()]


# ==========================================================================
# The guarantee: a run whose every sweep fails still starts, still runs and
# still completes
# ==========================================================================


def test_a_run_whose_every_sweep_fails_still_starts_runs_commits_and_completes(
        tmp_path, harness):
    """The guarantee the whole story is for. Everything else here is plumbing.

    Two entries are seeded pending, the configured sync command answers every
    filing with the transport's transient code, and both sweeps therefore
    achieve nothing at all. The run must be indifferent to that: it passes
    pre-flight, invokes every stage the workflow declares, commits its work,
    completes, and leaves a clean tree.
    """
    journal = tmp_path / "journal.txt"
    target = build_target(tmp_path / "failing-sweeps", journal)
    queue = outbox.queue_dir(target)
    keys = [seeded_pending(queue, IDENTITY),
            seeded_pending(queue, OTHER_IDENTITY)]

    runner = Runner(target, journal)
    code = run(target, harness, runner)

    assert code == 0, "the run did not complete"
    assert state_of(target)["status"] == "completed"
    assert runner.calls == [WRITING, VERIFYING]

    # Every sweep the run made really did reach the command and really did
    # fail, so the run above completed over a queue that never drained.
    assert filings(journal), "no sweep reached the sync command"
    for key in keys:
        entry = entry_of(queue, key)
        assert entry["state"] == outbox.PENDING
        assert entry["attempts"] > 0
        assert entry["last_error"]

    # The run committed its work, committed nothing from the queue, and left a
    # tree the next run can account for.
    committed = committed_paths(target)
    assert committed, "the run's commit names no files at all"
    assert [path for path in committed if path.startswith(QUEUE_REL)] == []
    assert story_coordinator.dirty_paths(target) == []


def test_the_exit_status_is_the_one_the_same_run_has_with_no_queue_at_all(
        tmp_path, harness):
    """Not merely zero: the *same* status, from the same fixture differing in
    the queue alone.

    Written as a comparison rather than as `== 0` because what the story
    promises is that the queue makes no difference, and an assertion on a
    literal would still pass if the queue had begun deciding runs and both
    happened to succeed.
    """
    empty_journal = tmp_path / "empty-journal.txt"
    empty = build_target(tmp_path / "empty-queue", empty_journal)
    empty_runner = Runner(empty, empty_journal)
    empty_code = run(empty, harness, empty_runner)

    seeded_journal = tmp_path / "seeded-journal.txt"
    seeded = build_target(tmp_path / "seeded-queue", seeded_journal)
    seeded_pending(outbox.queue_dir(seeded))
    seeded_runner = Runner(seeded, seeded_journal)
    seeded_code = run(seeded, harness, seeded_runner)

    assert seeded_code == empty_code
    assert seeded_runner.calls == empty_runner.calls
    assert state_of(seeded)["status"] == state_of(empty)["status"]
    # And the two runs really did differ in the queue: one reached the command
    # and the other had nothing to reach it with.
    assert filings(seeded_journal) != []
    assert filings(empty_journal) == []


# ==========================================================================
# The ordering, observed rather than inferred
# ==========================================================================


def test_the_preflight_sweep_runs_before_the_first_stage_is_invoked(
        tmp_path, harness):
    """The journal is the observation: the first thing in it is a filing, and
    the stages come after it.

    Nothing here reads the coordinator's source. The sync command and the fake
    runner append to one file in the order they are called, so the order in the
    file is the order the run took.
    """
    journal = tmp_path / "journal.txt"
    target = build_target(tmp_path / "preflight", journal)
    seeded_pending(outbox.queue_dir(target))

    runner = Runner(target, journal)
    assert run(target, harness, runner) == 0

    lines = journal_lines(journal)
    assert lines[0].startswith("filed "), lines
    assert stages_invoked(journal) == [WRITING, VERIFYING]


def test_the_preflight_sweep_reports_what_it_did_in_the_runs_events_log(
        tmp_path, harness):
    """The pre-flight sweep says what it found where the run records what it
    did, and it says it before the first stage is entered."""
    journal = tmp_path / "journal.txt"
    target = build_target(tmp_path / "reported", journal)
    seeded_pending(outbox.queue_dir(target))

    assert run(target, harness, Runner(target, journal)) == 0

    events = (target / ".harness" / "runs" / STORY_ID / "events.log").read_text(
        encoding="utf-8").splitlines()
    swept = [index for index, line in enumerate(events) if "outbox:" in line]
    assert swept, events
    entered = [index for index, line in enumerate(events)
               if WRITING in line and "stage started" in line]
    assert entered, events
    assert swept[0] < entered[0], events


def test_the_completion_sweep_runs_after_the_completion_commit(
        tmp_path, harness):
    """Observed at the transport rather than read off the source.

    Every filing records the subject of the commit the target's HEAD stood on
    when it was made. The last filing of a completing run must record the
    commit the run *finished* with — so it happened after that commit was
    written — and the first must record the tree the run started from, which is
    what makes the two distinguishable rather than merely equal to something.
    """
    journal = tmp_path / "journal.txt"
    target = build_target(tmp_path / "completion-order", journal)
    seeded_pending(outbox.queue_dir(target))

    assert run(target, harness, Runner(target, journal)) == 0

    completion_subject = head_subject(target)
    assert completion_subject != SETUP_SUBJECT, "the run committed nothing"

    recorded = filings(journal)
    assert len(recorded) >= 2, recorded
    assert recorded[0] == SETUP_SUBJECT
    assert recorded[-1] == completion_subject


# ==========================================================================
# The runs that sweep nothing
# ==========================================================================


def unresolvable_mandate(story: str) -> str:
    """The fixture story with a mandate that terminates on nothing.

    A source kind the walk cannot resolve and an id nothing answers for, which
    is the shape `tests/test_config_keys_are_obeyed.py` already drives this
    refusal with.
    """
    stripped = story.replace(conftest.MANDATE_BLOCK, "")
    assert stripped != story, "the fixture story carried no mandate"
    return stripped + (
        "\nmandate:\n"
        "  source:\n"
        "    kind: a-record-nothing-answers-for\n"
        "    id: no-such-source\n"
        "  conferred_at: 2026-08-28 09:00:00\n"
        "  conferred_by: ''\n"
        "  recorded_by: l5-plan\n"
    )


def break_undeclared_key(target: Path) -> None:
    _append_config(target, "a_key_no_schema_declares: something\n")


def break_pause_wait(target: Path) -> None:
    _append_config(target, "max_pause_wait_seconds: not-a-number\n")


def break_story(target: Path) -> None:
    (target / ".harness" / "stories" / f"{STORY_ID}.yaml").unlink()


def break_mandate(target: Path) -> None:
    path = target / ".harness" / "stories" / f"{STORY_ID}.yaml"
    path.write_text(unresolvable_mandate(path.read_text(encoding="utf-8")),
                    encoding="utf-8")


def break_workflow(target: Path) -> None:
    _append_config(target, "workflow: no-workflow-of-this-name\n")


def break_with_a_finished_branch(target: Path) -> None:
    """A story branch already carrying this story's completion commit.

    Composed through the coordinator's own writer rather than spelled here, so
    what makes a branch look finished is the harness's own message.
    """
    branch = story_coordinator.story_branch(
        harness_config.load_config(target), STORY_ID)
    message = story_coordinator.completion_commit_message(
        story_coordinator.RunState(story_id=STORY_ID, branch=branch),
        "Sample story for coordinator tests")
    _git(target, "checkout", "-q", "-b", branch)
    _git(target, "commit", "-q", "--allow-empty", "-m", message)


def break_base(target: Path) -> None:
    """A configured base that exists and is not the branch HEAD is on.

    A base named in configuration is an undeclared base as far as
    `base_problems` is concerned — only the `--base` argument declares one —
    and an undeclared base that does not resolve reports nothing rather than a
    guess. So the break is a base that does resolve: a real branch the run
    would have to be standing on and is not, which is the first leg of that
    check and the refusal a developer actually meets.
    """
    _git(target, "branch", "the-shared-base")
    _append_config(target, "base_branch: the-shared-base\n")


def break_clean_tree(target: Path) -> None:
    (target / "src" / "left_behind.py").write_text(
        "print('work no stage produced')\n", encoding="utf-8")


#: Every pre-flight that stands above the sweep, each named by what it refuses
#: for and paired with the one edit that provokes it. A run refused by any of
#: them must leave the queue exactly as it was, because the sweep sits below
#: all of them.
#:
#: The dirty tree is deliberately last in the list and last in the run's own
#: order: it is the refusal a badly-placed sweep would be most likely to slip
#: past, since a sweep that ran above it would already have rewritten the
#: queue by the time the tree was judged.
REFUSALS = {
    "the undeclared key": break_undeclared_key,
    "the bad pause wait": break_pause_wait,
    "the unreadable story": break_story,
    "the unresolved mandate": break_mandate,
    "the unknown workflow": break_workflow,
    "the finished branch": break_with_a_finished_branch,
    "the bad base": break_base,
    "the dirty tree": break_clean_tree,
}


def _append_config(target: Path, text: str) -> None:
    path = target / ".harness" / "config.yaml"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def refused_run(tmp_path: Path, harness: Path, name: str, break_it) -> tuple:
    """One run refused by one pre-flight, and the queue on either side of it.

    The break is applied and committed, so the tree the run starts from is one
    it could account for if the pre-flight under test were the only thing wrong
    with it — except for the dirty-tree case, whose whole point is a tree that
    is not committed.
    """
    journal = tmp_path / f"{name}-journal.txt"
    target = build_target(tmp_path / name.replace(" ", "-"), journal)
    key = seeded_pending(outbox.queue_dir(target))
    break_it(target)
    if break_it is not break_clean_tree:
        _git(target, "add", "-A")
        _git(target, "commit", "-q", "--allow-empty", "-m", "the break")

    before = queue_bytes(outbox.queue_dir(target))
    runner = Runner(target, journal)
    code = run(target, harness, runner)
    return target, journal, key, before, code, runner


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_a_run_refused_above_the_sweep_leaves_the_queue_exactly_as_it_was(
        name, tmp_path, harness):
    """For every pre-flight above the sweep, not for a sample of them."""
    target, journal, _key, before, code, runner = refused_run(
        tmp_path, harness, name, REFUSALS[name])

    assert code != 0, f"{name} did not refuse the run"
    assert runner.calls == [], f"{name} invoked a stage"
    assert queue_bytes(outbox.queue_dir(target)) == before
    assert filings(journal) == []


def test_the_same_fixture_with_nothing_broken_does_drain_the_queue(
        tmp_path, harness):
    """The control for all of the above. Every one of those assertions is an
    absence — nothing filed, nothing rewritten — and each would pass just as
    happily against a fixture whose sync command was never going to run at all.

    So the same builder, the same seeding and the same comparison are driven
    with no break applied, and there the queue *is* reached: the command is
    invoked and the entry on disk is rewritten with an attempt recorded.
    """
    journal = tmp_path / "unbroken-journal.txt"
    target = build_target(tmp_path / "unbroken", journal)
    key = seeded_pending(outbox.queue_dir(target))
    before = queue_bytes(outbox.queue_dir(target))

    assert run(target, harness, Runner(target, journal)) == 0

    assert filings(journal) != []
    assert queue_bytes(outbox.queue_dir(target)) != before
    assert entry_of(outbox.queue_dir(target), key)["attempts"] > 0


def test_a_refused_run_leaves_its_entries_for_a_later_sweep(tmp_path, harness):
    """What the queue being untouched is worth: the entry a refused run did not
    attempt is still pending, and the next run — with the break repaired —
    attempts it."""
    journal = tmp_path / "later-journal.txt"
    target = build_target(tmp_path / "repaired", journal)
    key = seeded_pending(outbox.queue_dir(target))
    break_clean_tree(target)

    assert run(target, harness, Runner(target, journal)) != 0
    assert entry_of(outbox.queue_dir(target), key)["attempts"] == 0

    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "the developer's own fix")
    assert run(target, harness, Runner(target, journal)) == 0
    assert entry_of(outbox.queue_dir(target), key)["attempts"] > 0


# ==========================================================================
# An escalated run and a paused run sweep nothing
# ==========================================================================


def test_an_escalated_run_sweeps_nothing_after_its_preflight(tmp_path, harness):
    """A run that stops does not sweep on the way out.

    The pre-flight sweep has already happened by the time anything can fail, so
    what is asserted is that there is no *second* one — which is exactly the
    comparison the completing run supplies: the same fixture, the same seeding,
    two filings there and one here.
    """
    journal = tmp_path / "escalated-journal.txt"
    target = build_target(tmp_path / "escalated", journal)
    key = seeded_pending(outbox.queue_dir(target))

    runner = Runner(target, journal, fails_at=WRITING)
    code = run(target, harness, runner)

    assert code != 0
    assert state_of(target)["status"] == "escalated"
    assert filings(journal) == [SETUP_SUBJECT], journal_lines(journal)
    # The entry is still there, attempted once by the pre-flight and waiting
    # for the next run or for an explicit l5-sync.
    assert entry_of(outbox.queue_dir(target), key)["state"] == outbox.PENDING


def test_a_paused_run_sweeps_nothing_after_its_preflight(tmp_path, harness):
    """The same for the third way a run stops. A capacity stop carrying no
    reset time, against a target configuring no wait, pauses in place."""
    journal = tmp_path / "paused-journal.txt"
    target = build_target(tmp_path / "paused", journal)
    key = seeded_pending(outbox.queue_dir(target))

    runner = Runner(target, journal, fails_at=WRITING,
                    capacity=CapacityStop(signal=A_CAPACITY_SIGNAL))
    code = run(target, harness, runner)

    assert code != 0
    assert state_of(target)["status"] == "paused"
    assert filings(journal) == [SETUP_SUBJECT], journal_lines(journal)
    assert entry_of(outbox.queue_dir(target), key)["state"] == outbox.PENDING


def test_the_completing_run_is_the_control_for_both(tmp_path, harness):
    """The control for the two assertions above: the same fixture, seeded the
    same way, that does not stop, sweeps a second time. So "exactly one filing"
    is a fact about a run that stopped rather than about a fixture in which one
    filing is all that ever happens."""
    journal = tmp_path / "completing-journal.txt"
    target = build_target(tmp_path / "completing", journal)
    seeded_pending(outbox.queue_dir(target))

    assert run(target, harness, Runner(target, journal)) == 0
    assert len(filings(journal)) == 2, journal_lines(journal)


# ==========================================================================
# The sweep is total: every way it can go wrong comes back as a summary
# ==========================================================================


class RaisingTransport:
    """A transport that raises on every entry, which is the failure that tells
    a caller least."""

    def file(self, entry):
        raise RuntimeError("nothing is reachable")


class NonsenseTransport:
    """A transport that answers with something that is not a filing."""

    def file(self, entry):
        return "a string where a Filing belongs"


@pytest.fixture
def swept_target(tmp_path: Path) -> Path:
    """A directory a sweep can be pointed at, with one pending entry in it.

    Not a repository and not a target the coordinator would run in: what the
    cases below drive is `sweep` itself, which needs a queue and a mapping and
    nothing else.
    """
    root = tmp_path / "swept"
    root.mkdir()
    seeded_pending(outbox.queue_dir(root))
    return root


def swept(root: Path, **config) -> outbox.Summary:
    return outbox_sweep.sweep(root, dict(config), REPO_ROOT)


def notes_of(summary: outbox.Summary) -> str:
    return " | ".join(summary.notes)


def test_a_sweep_with_no_transport_configured_reports_the_queue_and_files_nothing(
        swept_target):
    summary = swept(swept_target)
    assert isinstance(summary, outbox.Summary)
    assert summary.pending == 1
    assert summary.landed == 0
    assert summary.transport is False


@pytest.mark.parametrize("value", ["", "0", "-1", "not-a-number", "1e"])
def test_a_sweep_whose_timeout_is_not_a_positive_number_notes_it_and_files_nothing(
        value, swept_target):
    summary = swept(swept_target, sync_command="/bin/true",
                    sync_timeout_seconds=value)
    assert isinstance(summary, outbox.Summary)
    assert outbox_sweep.TIMEOUT_KEY in notes_of(summary), summary.notes
    assert summary.transport is False
    assert summary.pending == 1


@pytest.mark.parametrize("value", ["", "0", "-3", "two", "1.5"])
def test_a_sweep_whose_limit_is_not_a_positive_integer_notes_it_and_files_nothing(
        value, swept_target):
    summary = swept(swept_target, sync_command="/bin/true",
                    sweep_max_entries=value)
    assert isinstance(summary, outbox.Summary)
    assert outbox_sweep.LIMIT_KEY in notes_of(summary), summary.notes
    assert summary.transport is False
    assert summary.pending == 1


def test_a_sweep_whose_transport_raises_on_every_entry_returns_a_summary(
        swept_target):
    """Driven at the drain with a fake rather than through the configured
    command, because a transport that *raises* is not something a subprocess
    can be made to do.

    The reason is asserted on the **summary**, not only on the entry file: a
    pending entry's last_error reaches no reader of a sweep — not the one line
    a coordinator sweep writes to events.log, and not the l5-status queue
    section, which prints a last error only for a terminally failed entry. The
    unreachable provider is the case this whole design is for, so it is the one
    case that must not report a count with no cause.
    """
    queue = outbox.queue_dir(swept_target)
    summary = outbox_sweep.drain(queue, RaisingTransport(), REPO_ROOT)
    assert isinstance(summary, outbox.Summary)
    assert summary.pending == 1
    assert "nothing is reachable" in entry_of(
        queue, outbox.entry_files(queue)[0].stem)["last_error"]
    assert notes_naming("nothing is reachable", summary), summary.notes
    assert "nothing is reachable" in outbox_sweep.render(summary)


def test_a_sweep_whose_transport_answers_with_nonsense_returns_a_summary(
        swept_target):
    """The same reach for an answer that is not a Filing. There is no error
    text of the transport's own to carry here, so the note has to name the
    shape of the failure itself."""
    queue = outbox.queue_dir(swept_target)
    summary = outbox_sweep.drain(queue, NonsenseTransport(), REPO_ROOT)
    assert isinstance(summary, outbox.Summary)
    assert summary.pending == 1
    assert summary.failed == 0
    assert notes_naming("filing", summary), summary.notes
    rendered = outbox_sweep.render(summary)
    assert any(note in rendered for note in summary.notes), rendered


def notes_naming(reason: str, summary: outbox.Summary) -> list[str]:
    """The notes saying an entry could not be filed for the given reason.

    Both halves are required: "could not be filed" is what makes a note a
    report of a *sweep's* failure rather than of a provider's answer, and the
    reason is what makes it this failure rather than any other.
    """
    return [note for note in summary.notes
            if "could not be filed" in note and reason in note]


def test_a_sweep_that_filed_everything_carries_no_such_note(swept_target):
    """The control for the two assertions above.

    Without it each of them passes just as happily against a summary that
    carries that note unconditionally, and neither would then be a fact about
    the failure it names. Same queue, same drain, same helper reading the
    notes; only the transport differs, and it lands what it is given.
    """
    queue = outbox.queue_dir(swept_target)
    summary = outbox_sweep.drain(queue, CountingTransport(), REPO_ROOT)
    assert summary.landed == 1
    assert summary.pending == 0
    assert notes_naming("", summary) == [], summary.notes
    assert "could not be filed" not in outbox_sweep.render(summary)


@pytest.fixture
def unlistable_queue(tmp_path: Path) -> Path:
    """A target whose queue directory exists and cannot be listed."""
    root = tmp_path / "unlistable"
    queue = outbox.queue_dir(root)
    seeded_pending(queue)
    queue.chmod(0o000)
    yield root
    queue.chmod(0o700)


def test_the_unlistable_queue_really_cannot_be_listed(unlistable_queue):
    """The control for the two assertions that use that fixture: the directory
    below refuses to be listed, so a note about it is the guard working rather
    than a path that quietly succeeded.

    A developer running the suite as root can read a directory with no read
    bit, which would make the two assertions vacuous rather than false — so
    that case is skipped by name rather than passed silently.
    """
    queue = outbox.queue_dir(unlistable_queue)
    try:
        list(queue.iterdir())
    except OSError:
        return
    pytest.skip("this process can list a directory with no read permission")


def test_a_sweep_of_a_queue_that_cannot_be_listed_notes_it_and_does_not_raise(
        unlistable_queue):
    queue = outbox.queue_dir(unlistable_queue)
    try:
        list(queue.iterdir())
        pytest.skip("this process can list a directory with no read permission")
    except OSError:
        pass

    summary = swept(unlistable_queue)
    assert isinstance(summary, outbox.Summary)
    assert "could not be listed" in notes_of(summary), summary.notes


def test_a_sweep_of_a_queue_that_does_not_exist_is_an_empty_queue(tmp_path):
    summary = swept(tmp_path / "never-enqueued-anything")
    assert isinstance(summary, outbox.Summary)
    assert (summary.landed, summary.pending, summary.failed,
            summary.poisoned) == (0, 0, 0, 0)


# ==========================================================================
# The sweep has no way to refuse, and the call sites read nothing from it
# ==========================================================================


def test_the_sweep_and_the_refusable_build_are_different_functions():
    """The split the module exists to hold. The refusable half returns a
    problem a caller can act on; the total half has no such value in it."""
    assert outbox_sweep.sweep is not outbox_sweep.build_transport
    assert outbox_sweep.sweep is not outbox_sweep.sweep_limit

    # The refusable halves answer with a problem beside their value; the sweep
    # answers with a summary and nothing else.
    _transport, problem = outbox_sweep.build_transport(
        {"sync_timeout_seconds": "nonsense"}, REPO_ROOT)
    assert problem
    _limit, limit_problem = outbox_sweep.sweep_limit(
        {"sweep_max_entries": "nonsense"})
    assert limit_problem


def test_the_sweep_takes_no_parameter_a_caller_could_refuse_through():
    """Its whole signature, so a parameter added later that a call site could
    set to make a sweep refuse is reported rather than absorbed."""
    parameters = inspect.signature(outbox_sweep.sweep).parameters
    assert list(parameters) == [
        "target_root", "config", "harness_root", "run_dir"]


def sweep_calls_in(source: str) -> list[ast.AST]:
    """Every call to the sweep in a source, as the node that encloses it.

    The enclosing statement rather than the call, because what the rule is
    about is not that the sweep is called but what is done with what it
    answers: a bare expression statement discards it, and anything else — an
    assignment, a return, a condition — is a call site that could act on it.
    """
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.Call):
                continue
            target = child.func
            if (isinstance(target, ast.Attribute)
                    and target.attr == outbox_sweep.sweep.__name__
                    and isinstance(target.value, ast.Name)
                    and target.value.id == outbox_sweep.__name__):
                found.append(node)
    return found


def call_sites_reading_the_result(source: str) -> list[str]:
    """The sweep call sites whose answer is used for anything at all."""
    return [type(node).__name__ for node in sweep_calls_in(source)
            if not isinstance(node, ast.Expr)]


COORDINATOR_SOURCE = (
    ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8")


def test_the_coordinator_sweeps_twice_and_reads_nothing_from_either_sweep():
    """Two call sites, and neither of them can turn a sweep into a decision:
    the answer is discarded at both, so there is no branch, no early return and
    no status taken from it."""
    assert len(sweep_calls_in(COORDINATOR_SOURCE)) == 2
    assert call_sites_reading_the_result(COORDINATOR_SOURCE) == []


@pytest.mark.parametrize("planted", [
    "    swept = outbox_sweep.sweep(target_root, config, harness_root)\n",
    "    if outbox_sweep.sweep(target_root, config, harness_root).blocked:\n"
    "        return 1\n",
    "    return outbox_sweep.sweep(target_root, config, harness_root)\n",
])
def test_the_scan_reports_a_call_site_that_reads_what_the_sweep_answered(planted):
    """Control: the empty list above is a fact about the coordinator's two call
    sites rather than about a scan that has stopped seeing a use.

    Each planted form is a way a later reader could give the sweep back the
    power to stop a run — bind it, branch on it, return it — and each must be
    reported.
    """
    source = f"def a_function(target_root, config, harness_root):\n{planted}"
    assert call_sites_reading_the_result(source) != []


def test_the_bare_call_the_coordinator_makes_is_not_reported():
    """The other half of the control: the shape the coordinator does use is
    seen by the scan and judged to read nothing, so an empty list above is not
    a scan that finds nothing anywhere."""
    source = ("def a_function(target_root, config, harness_root):\n"
              "    outbox_sweep.sweep(target_root, config, harness_root)\n")
    assert len(sweep_calls_in(source)) == 1
    assert call_sites_reading_the_result(source) == []


# ==========================================================================
# The non-refusal is stated where it is written
# ==========================================================================


#: What a comment has to say for a later reader to meet the rule before they
#: change the thing it protects.
THE_RULE = "must not refuse"

SWEEP_SOURCE = (ORCHESTRATION / "outbox_sweep.py").read_text(encoding="utf-8")
SYNC_SCRIPT_SOURCE = (SCRIPTS / "l5-sync").read_text(encoding="utf-8")


def test_the_sweep_module_says_that_this_sweep_may_not_refuse():
    assert THE_RULE in SWEEP_SOURCE


def test_both_coordinator_call_sites_say_it_too():
    """Both, and each in its own comment: the rule is met where the call is
    written rather than once at the top of a five-thousand-line module.

    The check is the text immediately above each call, taken back to the last
    line that is neither a comment nor blank, which is the comment block a
    reader about to change that line would be reading.
    """
    lines = COORDINATOR_SOURCE.splitlines()
    calls = [index for index, line in enumerate(lines)
             if f"{outbox_sweep.__name__}.{outbox_sweep.sweep.__name__}(" in line]
    assert len(calls) == 2, calls
    for index in calls:
        above = []
        cursor = index - 1
        while cursor >= 0 and (lines[cursor].strip().startswith("#")
                               or not lines[cursor].strip()):
            above.append(lines[cursor])
            cursor -= 1
        assert THE_RULE in "\n".join(above), lines[index]


#: The sentence each of the two docstrings carried before this story, which the
#: story makes false. Held as text rather than as a phrase to search for, so
#: the assertion that it is gone can be shown to be capable of finding it.
RETIRED_SENTENCES = {
    "orchestration/outbox.py":
        "No sweep runs inside a story run",
    "scripts/l5-sync":
        "the only drain site the harness ships",
}


@pytest.mark.parametrize("relative", sorted(RETIRED_SENTENCES))
def test_the_docstrings_this_story_makes_false_no_longer_say_it(relative):
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert RETIRED_SENTENCES[relative] not in source
    # And the successor is there rather than the paragraph merely having been
    # deleted: each of them now names the seam the sweeps go through.
    assert outbox_sweep.__name__ in source


@pytest.mark.parametrize("relative", sorted(RETIRED_SENTENCES))
def test_the_search_for_the_retired_sentence_can_find_it(relative):
    """Control: the absence above is a fact about the file rather than about a
    search that no longer matches anything."""
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    planted = f'"""{RETIRED_SENTENCES[relative]}."""\n' + source
    assert RETIRED_SENTENCES[relative] in planted


# ==========================================================================
# scripts/l5-sync: unchanged, plus one refusal in the same shape
# ==========================================================================


MINIMAL_CONFIG = "workflow: story-workflow\n"


@pytest.fixture
def sync_target(tmp_path: Path) -> Path:
    root = tmp_path / "sync-target"
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(MINIMAL_CONFIG,
                                                   encoding="utf-8")
    return root


def run_sync(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "l5-sync")],
                          cwd=cwd, capture_output=True, text=True, timeout=60)


def configured(target: Path, **keys) -> None:
    path = target / ".harness" / "config.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "".join(f"{key}: {value}\n" for key, value in sorted(keys.items())),
        encoding="utf-8")


BAD_TIMEOUT = "not-a-duration"
BAD_LIMIT = "not-a-count"


def test_l5_sync_still_refuses_a_timeout_that_is_not_a_positive_number(
        sync_target):
    """Exactly as it did before this story: the key and the value both named,
    nothing filed, and a non-zero exit."""
    seeded_pending(outbox.queue_dir(sync_target))
    configured(sync_target, sync_command="/bin/true",
               sync_timeout_seconds=BAD_TIMEOUT)

    result = run_sync(sync_target)
    assert result.returncode != 0
    assert outbox_sweep.TIMEOUT_KEY in result.stderr
    assert BAD_TIMEOUT in result.stderr


def test_l5_sync_refuses_a_sweep_limit_that_is_not_a_positive_integer(
        sync_target):
    """The new key, refused in the shape the timeout is refused in."""
    seeded_pending(outbox.queue_dir(sync_target))
    configured(sync_target, sync_command="/bin/true",
               sweep_max_entries=BAD_LIMIT)

    result = run_sync(sync_target)
    assert result.returncode != 0
    assert outbox_sweep.LIMIT_KEY in result.stderr
    assert BAD_LIMIT in result.stderr


def test_l5_sync_accepts_what_it_accepted_before(sync_target):
    """The control for the two refusals: the same script, the same queue and
    the same invocation with both keys well-formed exits zero and reports the
    queue, so a refusal above is the value being refused rather than the script
    failing for a reason neither test named."""
    key = seeded_pending(outbox.queue_dir(sync_target))
    configured(sync_target, sync_timeout_seconds="30", sweep_max_entries="5")

    result = run_sync(sync_target)
    assert result.returncode == 0, result.stderr
    assert key in result.stdout


def test_l5_sync_calls_the_refusable_build_and_not_the_total_sweep():
    """Which of the two halves the explicit drain reaches, read off the script.

    The script is where a bad configuration is refused, so it must call the
    half that can refuse — and must not call the half that cannot, which would
    make its refusals unreachable.
    """
    assert outbox_sweep.build_transport.__name__ in SYNC_SCRIPT_SOURCE
    assert outbox_sweep.sweep_limit.__name__ in SYNC_SCRIPT_SOURCE
    assert sweep_calls_in(SYNC_SCRIPT_SOURCE) == []


def test_the_scan_would_report_a_sweep_call_in_the_script():
    """Control for the absence above."""
    planted = SYNC_SCRIPT_SOURCE + (
        "\ndef a_later_addition(target_root, config, harness_root):\n"
        "    outbox_sweep.sweep(target_root, config, harness_root)\n")
    assert len(sweep_calls_in(planted)) == 1


# ==========================================================================
# The bound: what one sweep attempts, and what it says it left
# ==========================================================================


class CountingTransport:
    """A transport that lands everything and remembers what it was asked for.

    A fake rather than the configured command, because what the bound bounds is
    a count of filing attempts and counting them is the whole assertion.
    """

    def __init__(self, reference: str = "landed"):
        self.reference = reference
        self.filed: list[str] = []

    def file(self, entry):
        self.filed.append(entry["key"])
        return outbox.filed(f"{self.reference}-{len(self.filed)}")


MALFORMED_NAME = f"not-json{outbox.ENTRY_SUFFIX}"


class RefusingTransport:
    """A transport that refuses every entry on the provider's own terms, which
    is what makes an entry terminally failed."""

    def __init__(self, error: str = "the provider rejected this outright"):
        self.error = error

    def file(self, entry):
        return outbox.refused(self.error)


def seeded_queue(root: Path, *, pending: int = 0, landed: int = 0,
                 failed: int = 0, poisoned: int = 0) -> Path:
    """A queue holding the states the caller asked for, and nothing else.

    The landed and failed entries are produced by driving the queue's own
    transitions rather than by writing files this module composed, so what they
    are is decided by the module under test.

    The pending entries are written **last**, deliberately: a drain reaches
    every pending entry in the queue, so seeding them before the two drains
    below would land or fail the very entries the caller asked to be left
    pending. Landed and failed entries are inert to a later drain, which is
    what makes this order the one that produces what was asked for.
    """
    queue = outbox.queue_dir(root)
    for ordinal in range(landed):
        outbox.enqueue(queue, PAYLOAD, {"kind": "landed", "n": ordinal})
        outbox.sync(queue, CountingTransport())
    for ordinal in range(failed):
        outbox.enqueue(queue, PAYLOAD, {"kind": "failed", "n": ordinal})
        outbox.sync(queue, RefusingTransport())
    for ordinal in range(poisoned):
        (queue / f"{ordinal}-{MALFORMED_NAME}").write_bytes(b'{"key": "half a ')
    for ordinal in range(pending):
        outbox.enqueue(queue, PAYLOAD, {"kind": "pending", "n": ordinal})
    return queue


def test_the_bound_attempts_exactly_the_limit_and_leaves_the_rest_pending(
        tmp_path):
    """Pinned from both sides: exactly the limit is offered to the transport,
    and exactly the remainder is left pending with nothing attempted on it."""
    queue = seeded_queue(tmp_path / "bounded", pending=5)
    transport = CountingTransport()

    summary = outbox.sync(queue, transport, limit=2)

    assert len(transport.filed) == 2
    assert summary.landed == 2
    assert summary.pending == 3
    for path in outbox.entry_files(queue):
        entry, problems = outbox.read_entry(path)
        assert entry is not None, problems
        if entry["state"] == outbox.PENDING:
            assert entry["attempts"] == 0, "an entry past the bound was attempted"


def test_the_bound_names_the_limit_and_what_it_left_undone(tmp_path):
    """No silent cap: what the bound left behind is stated in what the sweep
    reports, and both numbers are in it."""
    queue = seeded_queue(tmp_path / "named", pending=5)

    summary = outbox.sync(queue, CountingTransport(), limit=2)

    stated = " | ".join(summary.notes)
    assert "2" in stated, stated
    assert "3" in stated, stated


def test_an_unbounded_sweep_attempts_the_whole_queue(tmp_path):
    """The control for both assertions above: the same queue with no bound has
    every entry attempted and no note about a bound, so what those two report
    is the bound rather than a drain that stops early on its own."""
    queue = seeded_queue(tmp_path / "unbounded", pending=5)
    transport = CountingTransport()

    summary = outbox.sync(queue, transport)

    assert len(transport.filed) == 5
    assert summary.landed == 5
    assert summary.pending == 0
    assert summary.notes == ()


def test_the_bound_counts_filings_and_not_entries_read(tmp_path):
    """A queue of landed, failed and poisoned entries is fully reported under a
    bound smaller than it, because none of those reaches a transport at all.

    The one pending entry is what makes the bound bite on something: it is
    attempted, the bound is spent on it, and the other nine are still counted
    and named.
    """
    queue = seeded_queue(tmp_path / "every-state",
                         pending=1, landed=3, failed=3, poisoned=3)
    transport = CountingTransport()

    summary = outbox.sync(queue, transport, limit=1)

    assert len(transport.filed) == 1
    assert summary.landed == 4      # the three already landed, plus this one
    assert summary.failed == 3
    assert summary.poisoned == 3
    assert summary.pending == 0
    assert len(summary.poisoned_files) == 3


def test_a_bound_smaller_than_the_pending_count_still_reports_every_other_state(
        tmp_path):
    """The same, with the bound spent before the pending entries are reached:
    everything that is not pending is still read and still counted."""
    queue = seeded_queue(tmp_path / "mixed",
                         pending=4, landed=2, failed=2, poisoned=2)
    transport = CountingTransport()

    summary = outbox.sync(queue, transport, limit=1)

    assert len(transport.filed) == 1
    assert summary.failed == 2
    assert summary.poisoned == 2
    assert summary.landed == 3      # the two already landed, plus this one
    assert summary.pending == 3


# ==========================================================================
# l5-status: the queue reported, with no transport and nothing filed
# ==========================================================================


@pytest.fixture
def listing_target(tmp_path: Path) -> Path:
    """A target with runs and a queue holding one of each state.

    `sync_command` is configured deliberately: a sweep pointed at this target
    would have a transport to build, so "no transport was built" is a fact
    about the listing rather than about a target with nothing to build from.
    """
    root = tmp_path / "listing-target"
    (root / ".harness" / "runs" / STORY_ID).mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(
        "runs_dir: .harness/runs\n"
        f"sync_command: {SYNC_COMMAND_REL}\n", encoding="utf-8")
    (root / ".harness" / "runs" / STORY_ID / "state.json").write_text(
        json.dumps({"story_id": STORY_ID, "branch": f"story/{STORY_ID}",
                    "status": "completed", "current_stage": "",
                    "retry_count": 0, "verification_iterations": 0,
                    "artifacts": []}) + "\n", encoding="utf-8")
    seeded_queue(root, pending=2, failed=1, poisoned=1)
    return root


THE_REFUSAL = "the provider rejected this outright"


def test_the_listing_reports_the_pending_count_the_failures_and_the_poison(
        listing_target):
    listing = run_status.format_listing(listing_target)

    assert "pending 2" in listing, listing
    assert "failed 1" in listing, listing
    assert "poisoned 1" in listing, listing
    # Each failed entry with the error it last met, and each poisoned file by
    # name, so a developer can act on the listing without opening the queue.
    assert THE_REFUSAL in listing, listing
    assert MALFORMED_NAME in listing, listing
    # And the runs the developer actually asked for are still there.
    assert STORY_ID in listing, listing


def test_the_listing_is_the_same_whether_or_not_a_sync_command_is_configured(
        listing_target):
    """The key nothing in the listing reads. The two renderings are of one
    target, differing in that key and in nothing else, so a difference could
    only be the listing having consulted it."""
    with_command = run_status.format_listing(listing_target)

    config = listing_target / ".harness" / "config.yaml"
    text = config.read_text(encoding="utf-8")
    without = text.replace(f"sync_command: {SYNC_COMMAND_REL}\n", "")
    assert without != text, "the fixture configured no sync command"
    config.write_text(without, encoding="utf-8")

    assert run_status.format_listing(listing_target) == with_command


#: A probe run in a fresh interpreter, so what it says about `sys.modules` is
#: about the imports its own work made rather than about whatever this suite
#: has imported by the time it runs.
PROBE = """\
import json, sys
sys.path.insert(0, {orchestration!r})
from pathlib import Path
{work}
print(json.dumps({{"transport_imported": {transport_module!r} in sys.modules}}))
"""

LISTING_WORK = """\
import run_status
run_status.format_listing(Path({target!r}))
"""

SWEEP_WORK = """\
import harness_config, outbox_sweep
outbox_sweep.sweep(Path({target!r}), harness_config.load_config(Path({target!r})),
                   Path({harness!r}))
"""


def probe(work: str, tmp_path: Path, name: str) -> dict:
    script = tmp_path / f"{name}.py"
    script.write_text(
        PROBE.format(orchestration=str(ORCHESTRATION), work=work,
                     transport_module="command_transport"),
        encoding="utf-8")
    result = subprocess.run([sys.executable, str(script)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_listing_builds_no_transport_and_files_nothing(
        listing_target, tmp_path):
    """Two absences at once, each with its own demonstration below: the module
    that spawns a subprocess for the outbox is never imported, and the queue on
    disk is byte for byte what it was."""
    before = queue_bytes(outbox.queue_dir(listing_target))

    answer = probe(LISTING_WORK.format(target=str(listing_target)),
                   tmp_path, "listing-probe")

    assert answer["transport_imported"] is False
    assert queue_bytes(outbox.queue_dir(listing_target)) == before


def test_the_same_probe_reports_a_transport_when_a_sweep_builds_one(
        listing_target, tmp_path):
    """The control for both halves above. The same probe, the same target and
    the same byte comparison, running a sweep instead of a listing: there the
    transport module *is* imported and the queue *is* rewritten.

    The sync command this target configures does not exist, so the sweep files
    nothing successfully — which is beside the point. What matters is that it
    reached for a transport at all, and that the entries record the attempt.
    """
    before = queue_bytes(outbox.queue_dir(listing_target))

    answer = probe(
        SWEEP_WORK.format(target=str(listing_target), harness=str(REPO_ROOT)),
        tmp_path, "sweep-probe")

    assert answer["transport_imported"] is True
    assert queue_bytes(outbox.queue_dir(listing_target)) != before


def test_the_listing_reports_a_queue_it_cannot_read_and_still_lists_the_runs(
        listing_target):
    queue = outbox.queue_dir(listing_target)
    queue.chmod(0o000)
    try:
        try:
            list(queue.iterdir())
            pytest.skip(
                "this process can list a directory with no read permission")
        except OSError:
            pass
        listing = run_status.format_listing(listing_target)
    finally:
        queue.chmod(0o700)

    assert "could not be read" in listing, listing
    # The runs the developer asked for are what the command is for, and an
    # unreadable queue does not cost them.
    assert STORY_ID in listing, listing


def test_the_listing_of_a_target_with_no_queue_at_all_still_lists_its_runs(
        tmp_path):
    root = tmp_path / "no-queue"
    (root / ".harness" / "runs" / STORY_ID).mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text("runs_dir: .harness/runs\n",
                                                   encoding="utf-8")
    (root / ".harness" / "runs" / STORY_ID / "state.json").write_text(
        json.dumps({"story_id": STORY_ID, "branch": f"story/{STORY_ID}",
                    "status": "running", "current_stage": "",
                    "retry_count": 0, "verification_iterations": 0,
                    "artifacts": []}) + "\n", encoding="utf-8")

    listing = run_status.format_listing(root)
    assert STORY_ID in listing
    assert "pending 0" in listing, listing


def test_the_cli_reports_the_queue_and_writes_nothing(listing_target):
    """The shipped entry point rather than the module beneath it, driven as a
    subprocess and required to leave every file it was pointed at alone."""
    before = {
        (str(path.relative_to(listing_target)), path.stat().st_size)
        for path in listing_target.rglob("*") if path.is_file()
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "l5-status")],
        cwd=listing_target, capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert "pending 2" in result.stdout, result.stdout
    assert THE_REFUSAL in result.stdout, result.stdout
    assert MALFORMED_NAME in result.stdout, result.stdout
    assert {
        (str(path.relative_to(listing_target)), path.stat().st_size)
        for path in listing_target.rglob("*") if path.is_file()
    } == before
