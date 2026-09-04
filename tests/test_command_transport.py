"""Independent validation for the command transport: one command files a story.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the mapping from an exit code to a state.** Driven through
    `outbox.sync` against a real queue, so what is asserted is the entry
    *on disk* — landed with its reference, pending with the attempt counted
    and the error recorded, or failed terminally — rather than the `Filing`
    the transport returned. The queue is the thing a human reads afterwards.

  * **the four transient-rather-than-terminal outcomes.** The timeout, the
    command that could not be launched, the zero exit that named no
    reference, and the reference past the bound. Each is driven on its own,
    because a repair can get one right while getting another wrong, and the
    reasons are required to differ from one another: "transient" is not the
    whole answer, "which of them it was" is.

  * **the process group.** A fixture command that backgrounds a child which
    outlives it, killed at the timeout, and the child observed gone. The
    control beside it kills the leader alone and observes the same child
    survive and write its marker, so the marker's absence is a fact about
    the kill rather than about a child that was never going to write.

  * **what the command is handed.** stdin as JSON and `L5_SYNC_KEY` in the
    environment, captured by a fixture command that writes both to files the
    test reads back.

  * **the absent lookup.** The transport offers none, so an entry with a
    prior attempt files through the command exactly once and records no
    lookup note. The control is a subclass carrying a lookup that raises,
    against the same entry, whose note the same summary holds.

  * **idempotency given the key.** A fixture standing in for an idempotent
    provider — it searches a ledger for the key before it writes — invoked
    twice under one key, which is the ambiguous write resolved by the
    command rather than by the harness.

  * **the shipped wiring.** `scripts/l5-sync`, `scripts/l5-init`,
    `templates/config.yaml` and this repository's own `.harness/` are live
    harness artifacts and are the subjects of the assertions that name them:
    what this repository ships is read as it ships.

Every command driven here is a file the test wrote, and
`fixture_command_problems` is what makes that a checked property rather than
a habit: it refuses a path that already exists and a path inside this
repository, and the control below shows it reporting both against
`scripts/l5-sync`. Nothing here reaches a network, invokes `gh`, or depends
on a tracker existing.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import command_transport
import conftest
import harness_config
import outbox

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
TEMPLATES = REPO_ROOT / "templates"

#: Where a target's sync commands are installed, and the reference command
#: itself, derived from what the harness ships rather than written here: the
#: template directory is the declaration of what l5-init installs.
SYNC_DIR = "sync"
REFERENCE_SCRIPTS = sorted(path.name for path in (TEMPLATES / SYNC_DIR).glob("*.sh"))

#: What the transport says it will do, read off the module so this file names
#: no exit code, bound or variable of its own.
TRANSIENT_EXIT_CODE = command_transport.TRANSIENT_EXIT_CODE
DEFAULT_TIMEOUT_SECONDS = command_transport.DEFAULT_TIMEOUT_SECONDS
REFERENCE_MAX_LENGTH = command_transport.REFERENCE_MAX_LENGTH
KEY_ENVIRONMENT_VARIABLE = command_transport.KEY_ENVIRONMENT_VARIABLE

#: An exit code that is neither zero nor the transient one, so "every other
#: non-zero code is terminal" is driven at a code the transport has no opinion
#: about rather than at a code it names.
TERMINAL_EXIT_CODE = 3

IDENTITY = {"kind": "finding", "subject": "story-091"}
PAYLOAD = {"title": "something to file", "body": "what it says"}

REFERENCE = "https://tracker.example/issues/17"
#: A reference that is not a URL. Nothing in the harness parses a reference,
#: so this must land exactly as the one above does.
OPAQUE_REFERENCE = "PROJ-4219"

#: How long a command that is meant to be killed is asked to run. Far longer
#: than any bound a test configures, so a test that observed it finish would
#: be observing the kill not happening rather than a race.
LONGER_THAN_ANY_BOUND = 45

#: The bound a killed command is given, and how long the tests wait on a
#: process to disappear or a file to appear. A bound rather than a sleep: the
#: waits below poll, so a fast machine pays for none of this.
KILL_BOUND_SECONDS = 1.0
PATIENCE_SECONDS = 20.0

#: The bound the process-group tests give the command they kill. Larger than
#: the one above because it is asked for something more: those tests require
#: the command to have *got somewhere* — to have started an interpreter and
#: backgrounded a child — before the kill arrives, where a bound that only has
#: to be exceeded is satisfied by a command that never started at all. The
#: suite runs its workers in parallel and each one spawns processes of its own,
#: so a second is not reliably enough for an interpreter to reach its first
#: line on a loaded machine, and a test that times out before the thing it is
#: about has happened fails saying nothing about the group.
SPAWN_BOUND_SECONDS = 5.0

#: How long the child sleeps before it writes its marker. Longer than the
#: moment its leader is killed, so a marker that appears can only have been
#: written by a child that outlived the leader.
CHILD_SLEEP_SECONDS = 8


def test_the_bounds_the_process_group_tests_rest_on_are_ordered():
    """The ordering the separately declared bounds above have to keep.

    Each of the process-group assertions is decided by where the kill lands
    between the two sleeps, and nothing else states that arrangement: the
    child's sleep has to outlast the bound or a surviving child would be
    indistinguishable from a killed one, and the leader's has to outlast both
    or the command would finish rather than be killed. Written here so that
    retuning one of them for a slower machine reddens this rather than
    quietly turning the tests below into ones that pass for another reason.
    """
    assert SPAWN_BOUND_SECONDS < CHILD_SLEEP_SECONDS < LONGER_THAN_ANY_BOUND
    # And the control waits for a marker the child writes after that sleep,
    # so its patience has to outlast the sleep it is waiting through.
    assert CHILD_SLEEP_SECONDS < PATIENCE_SECONDS


# --------------------------------------------------------------------------
# Every command this module drives is a file this module wrote
# --------------------------------------------------------------------------


def fixture_command_problems(path: Path) -> list[str]:
    """What would stop `path` from being a command this module wrote itself.

    A predicate rather than a pair of inline assertions, so it can be *shown*
    reporting a violation rather than only observed to be silent. A path that
    already exists is one somebody else wrote, and a path inside this
    repository is a shipped artifact — driving either would make this module's
    behaviour depend on a program it does not control.
    """
    problems = []
    if path.exists():
        problems.append(f"{path} already exists, so this module did not write it")
    if REPO_ROOT in path.parents:
        problems.append(f"{path} is inside {REPO_ROOT}, so it is a shipped file "
                        f"rather than one this module wrote")
    return problems


def fixture_file(directory: Path, name: str, text: str, *,
                 executable: bool = True) -> Path:
    """One file this module writes for itself, and drives as a command.

    Every command handed to a transport below comes through here, so "every
    command the suite drives is a file the test wrote" is enforced at the
    moment of writing rather than restated afterwards.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    problems = fixture_command_problems(path)
    assert problems == [], problems
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def fixture_command(directory: Path, name: str, body: str, **kwargs) -> Path:
    """A shell script this module wrote, ready to be a `sync_command`."""
    return fixture_file(directory, name, "#!/bin/sh\n" + body, **kwargs)


def test_the_fixture_check_reports_a_command_this_module_did_not_write():
    """The control for the property every fixture command above rests on.

    Silence from the check means nothing until it has been shown to speak, so
    it is pointed at a shipped entry point — which exists, and lives inside
    this repository — and must report both.
    """
    problems = fixture_command_problems(SCRIPTS / "l5-sync")
    assert len(problems) == 2, problems
    assert any("already exists" in problem for problem in problems)
    assert any(str(REPO_ROOT) in problem for problem in problems)


# --------------------------------------------------------------------------
# The fixture commands, each a file the test writes
# --------------------------------------------------------------------------


def lands(directory: Path, reference: str = REFERENCE,
          name: str = "lands.sh") -> Path:
    return fixture_command(directory, name, f'echo "{reference}"\n')


def noisy(directory: Path) -> Path:
    """Prints a search it made and a board it updated before its reference."""
    return fixture_command(
        directory, "noisy.sh",
        'echo "searched the tracker for the key"\n'
        'echo "added the issue to the board"\n'
        f'echo "{REFERENCE}"\n'
        'echo ""\n')


def exits(directory: Path, code: int, message: str, name: str) -> Path:
    return fixture_command(directory, name,
                           f'echo "{message}" >&2\nexit {code}\n')


def counting(directory: Path, ledger: Path, code: int) -> Path:
    """A command that records every invocation before answering with `code`."""
    return fixture_command(
        directory, "counting.sh",
        f'echo invoked >> "{ledger}"\n'
        'echo "the provider refused it outright" >&2\n'
        f'exit {code}\n')


def sleeps_forever(directory: Path) -> Path:
    return fixture_command(directory, "sleeps.sh",
                           f"sleep {LONGER_THAN_ANY_BOUND}\n")


def spawns_a_child(directory: Path, child_pid: Path, marker: Path,
                   name: str = "spawns.sh") -> Path:
    """A command that backgrounds a child which would outlive it.

    The child's pid is recorded so the test can ask the operating system
    whether it is still there, and the marker it writes after sleeping is the
    other half of the same question: a child that survived its leader writes
    it, and a child killed with the group never does.
    """
    return fixture_command(
        directory, name,
        f'sh -c \'sleep {CHILD_SLEEP_SECONDS}; echo survived > "{marker}"\' &\n'
        f'echo $! > "{child_pid}"\n'
        f"sleep {LONGER_THAN_ANY_BOUND}\n")


def silent(directory: Path) -> Path:
    """Exits zero having named nothing, which establishes nothing."""
    return fixture_command(directory, "silent.sh", "exit 0\n")


def oversized(directory: Path) -> Path:
    reference = "x" * (REFERENCE_MAX_LENGTH + 1)
    return fixture_command(directory, "oversized.sh", f'echo "{reference}"\n')


def captures(directory: Path, document: Path, environment: Path) -> Path:
    """Writes down what it was handed, then lands the entry."""
    return fixture_command(
        directory, "captures.sh",
        f'cat > "{document}"\n'
        f'printf %s "${KEY_ENVIRONMENT_VARIABLE}" > "{environment}"\n'
        f'echo "{REFERENCE}"\n')


def idempotent(directory: Path, ledger: Path) -> Path:
    """The fixture standing in for an idempotent provider.

    It searches the ledger for the key before it writes anything, and answers
    with what it found. Invoked twice under one key it creates once, which is
    the promise the whole design rests on and the reason the harness makes no
    second call of its own.
    """
    return fixture_command(
        directory, "idempotent.sh",
        f'key="${KEY_ENVIRONMENT_VARIABLE}"\n'
        f'if grep -q "^$key " "{ledger}" 2>/dev/null; then\n'
        f'  grep "^$key " "{ledger}" | tail -1 | cut -d" " -f2\n'
        "  exit 0\n"
        "fi\n"
        f'echo "$key {REFERENCE}" >> "{ledger}"\n'
        f'echo "{REFERENCE}"\n')


# --------------------------------------------------------------------------
# Driving the transport through the queue
# --------------------------------------------------------------------------


@pytest.fixture
def queue(tmp_path: Path) -> Path:
    return outbox.queue_dir(tmp_path / "a-target")


@pytest.fixture
def commands(tmp_path: Path) -> Path:
    """Where this module writes the commands it drives."""
    return tmp_path / "fixture-commands"


def transport_for(command, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                  cwd: Path | None = None):
    return command_transport.CommandTransport(command=str(command),
                                              timeout=timeout, cwd=cwd)


def seeded(queue: Path, identity: dict = IDENTITY) -> str:
    return outbox.enqueue(queue, PAYLOAD, identity)


def entry_of(queue: Path, key: str) -> dict:
    return json.loads(outbox.entry_path(queue, key).read_text(encoding="utf-8"))


def drained(queue: Path, transport) -> outbox.Summary:
    return outbox.sync(queue, transport, harness_root=REPO_ROOT)


def filed_through(queue: Path, transport) -> dict:
    """One pending entry driven through one transport, as it ends up on disk."""
    key = seeded(queue)
    drained(queue, transport)
    return entry_of(queue, key)


# --------------------------------------------------------------------------
# Zero lands the entry, and the reference is the last non-empty line
# --------------------------------------------------------------------------


def test_a_command_that_exits_zero_and_names_a_reference_lands_the_entry(
        queue, commands):
    entry = filed_through(queue, transport_for(lands(commands)))
    assert entry["state"] == outbox.LANDED
    assert entry["reference"] == REFERENCE
    # The payload the provider is now authoritative for is gone, which is the
    # queue's own behaviour on landing and what makes "landed" observable as
    # more than a word.
    assert "payload" not in entry


def test_the_reference_is_the_last_non_empty_line_the_command_printed(
        queue, commands):
    """A command free to print whatever it likes on the way still lands the
    reference it named last, and the lines before it reach nothing."""
    entry = filed_through(queue, transport_for(noisy(commands)))
    assert entry["state"] == outbox.LANDED
    assert entry["reference"] == REFERENCE
    assert "searched the tracker" not in json.dumps(entry)


def test_a_reference_that_is_not_a_url_lands_exactly_as_one_that_is(
        queue, commands, tmp_path):
    """Nothing is decided from a reference's content, so the two are one case.

    Driven as a comparison rather than as an assertion about the opaque one
    alone: the entries are required to differ in the reference and in nothing
    else, which is what "never parsed" means in practice.
    """
    opaque_queue = outbox.queue_dir(tmp_path / "opaque-target")
    url = filed_through(queue, transport_for(lands(commands)))
    opaque = filed_through(
        opaque_queue,
        transport_for(lands(commands, OPAQUE_REFERENCE, "lands-opaque.sh")))

    assert opaque["state"] == url["state"] == outbox.LANDED
    assert opaque["reference"] == OPAQUE_REFERENCE
    assert url["reference"] == REFERENCE
    assert {field: value for field, value in opaque.items()
            if field not in ("reference", "updated_at", "created_at")} == \
           {field: value for field, value in url.items()
            if field not in ("reference", "updated_at", "created_at")}


# --------------------------------------------------------------------------
# The transient code, and every other non-zero code
# --------------------------------------------------------------------------


STDERR_MESSAGE = "the provider could not be reached just now"


def test_the_transient_code_leaves_the_entry_pending_with_the_attempt_counted(
        queue, commands):
    entry = filed_through(
        queue,
        transport_for(exits(commands, TRANSIENT_EXIT_CODE, STDERR_MESSAGE,
                            "transient.sh")))
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert STDERR_MESSAGE in entry["last_error"]
    assert "reference" not in entry


def test_any_other_non_zero_code_fails_the_entry_terminally(queue, commands):
    """And a later sync does not invoke the command for it again.

    The command counts its own invocations, so "not invoked again" is read off
    the ledger rather than inferred from the state — and the first sync's line
    in that same ledger is what shows the counting works.
    """
    ledger = commands / "invocations"
    transport = transport_for(counting(commands, ledger, TERMINAL_EXIT_CODE))
    key = seeded(queue)

    drained(queue, transport)
    entry = entry_of(queue, key)
    assert entry["state"] == outbox.FAILED
    assert entry["attempts"] == 1
    assert "refused it outright" in entry["last_error"]
    assert ledger.read_text(encoding="utf-8").split() == ["invoked"]

    summary = drained(queue, transport)
    assert summary.failed_keys == (key,)
    assert ledger.read_text(encoding="utf-8").split() == ["invoked"]
    assert entry_of(queue, key) == entry


# --------------------------------------------------------------------------
# The four transient-rather-than-terminal outcomes
# --------------------------------------------------------------------------


def test_a_command_that_runs_past_the_timeout_is_killed_and_left_pending(
        queue, commands):
    started = time.monotonic()
    entry = filed_through(
        queue,
        transport_for(sleeps_forever(commands), timeout=KILL_BOUND_SECONDS))
    elapsed = time.monotonic() - started

    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert "timeout" in entry["last_error"]
    assert str(KILL_BOUND_SECONDS) in entry["last_error"]
    # The command was killed rather than waited out: it was asked to run far
    # longer than the bound, and the call came back near the bound instead.
    assert elapsed < LONGER_THAN_ANY_BOUND


@pytest.mark.parametrize("case", ["a path that does not exist",
                                  "a file that is not executable"])
def test_a_command_that_cannot_be_launched_leaves_the_entry_pending(
        queue, commands, case):
    if case == "a path that does not exist":
        command = commands / "no-such-command.sh"
        assert fixture_command_problems(command) == []
    else:
        command = fixture_file(commands, "not-executable.sh",
                               "#!/bin/sh\necho never runs\n", executable=False)
        assert not os.access(command, os.X_OK)

    # Nothing raises into the caller: the answer comes back as a Filing, which
    # is what keeps the queue's totality out of a target's script's hands.
    answer = transport_for(command).file(
        {"key": "a-key", "identity": IDENTITY, "state": outbox.PENDING,
         "payload": PAYLOAD})
    assert isinstance(answer, outbox.Filing)
    assert answer.terminal is False

    entry = filed_through(queue, transport_for(command))
    assert entry["state"] == outbox.PENDING
    assert "launch" in entry["last_error"]
    assert str(command) in entry["last_error"]


def test_a_command_that_exits_zero_naming_no_reference_is_left_pending(
        queue, commands):
    entry = filed_through(queue, transport_for(silent(commands)))
    assert entry["state"] == outbox.PENDING
    assert "no reference" in entry["last_error"]
    assert "reference" not in entry


def test_a_reference_past_the_bound_is_left_pending_and_never_written(
        queue, commands):
    entry = filed_through(queue, transport_for(oversized(commands)))
    assert entry["state"] == outbox.PENDING
    assert str(REFERENCE_MAX_LENGTH) in entry["last_error"]
    assert "reference" not in entry
    # Nothing oversized reached the durable file at all, which is the whole
    # reason the bound exists: the entry is not merely un-landed, it is
    # un-enlarged.
    assert len(outbox.entry_path(queue, entry["key"]).read_bytes()) < \
        REFERENCE_MAX_LENGTH


def transient_reasons(queue_root: Path, commands: Path) -> dict[str, str]:
    """The reason each transient-rather-than-terminal outcome recorded.

    Collected together so the claim that each *says which one it was* is
    decided by comparing them rather than by reading four assertions and
    trusting that they differ.
    """
    cases = {
        "timeout": (transport_for(sleeps_forever(commands),
                                  timeout=KILL_BOUND_SECONDS)),
        "not launchable": transport_for(commands / "still-no-such-command.sh"),
        "named no reference": transport_for(silent(commands)),
        "oversized reference": transport_for(oversized(commands)),
    }
    reasons = {}
    for name, transport in cases.items():
        queue = outbox.queue_dir(queue_root / name.replace(" ", "-"))
        reasons[name] = filed_through(queue, transport)["last_error"]
    return reasons


def test_each_transient_outcome_says_which_one_it_was(tmp_path):
    """Four outcomes, four reasons, and no two of them the same.

    They are transient for one reason — nothing was established about whether
    the request arrived — and a human fixing a misconfiguration needs to know
    which of them happened. A single shared "the command failed" would satisfy
    every state assertion above and fail here.
    """
    reasons = transient_reasons(tmp_path / "reasons", tmp_path / "reason-commands")
    assert len(set(reasons.values())) == len(reasons), reasons
    assert all(reason.strip() for reason in reasons.values())


# --------------------------------------------------------------------------
# The kill reaches the process group
# --------------------------------------------------------------------------


def alive(pid: int) -> bool:
    """Whether a process this test did not parent is still there."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(predicate, patience: float = PATIENCE_SECONDS) -> bool:
    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_the_kill_reaches_the_process_group_and_not_only_the_command(
        queue, commands, tmp_path):
    child_pid = tmp_path / "child.pid"
    marker = tmp_path / "child-marker"
    command = spawns_a_child(commands, child_pid, marker)

    entry = filed_through(queue, transport_for(command,
                                               timeout=SPAWN_BOUND_SECONDS))
    assert entry["state"] == outbox.PENDING

    assert child_pid.exists(), (
        "the command was killed before it had spawned the child this test is "
        "about, so nothing was proven about the group")
    pid = int(child_pid.read_text(encoding="utf-8").strip())
    assert wait_until(lambda: not alive(pid)), (
        f"the child {pid} the command spawned was still running once the "
        f"transport had returned")
    # And it never got to do its work: the marker it writes after sleeping is
    # what a surviving child leaves behind.
    time.sleep(CHILD_SLEEP_SECONDS)
    assert not marker.exists()


def test_the_same_child_survives_when_only_the_leader_is_killed(tmp_path):
    """The control for the absence above, constructed rather than argued.

    A marker that never appears is also what a child that was never going to
    write one leaves behind. So the same fixture command is spawned in the same
    way and its *leader alone* is killed — which is what `process.kill()` does
    and what spawning into a new session exists to improve on — and the child
    must then outlive it and write the marker the assertion above requires to
    be absent.
    """
    commands = tmp_path / "control-commands"
    child_pid = tmp_path / "control-child.pid"
    marker = tmp_path / "control-marker"
    command = spawns_a_child(commands, child_pid, marker, "control-spawns.sh")

    process = subprocess.Popen(
        [str(command)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        assert wait_until(lambda: child_pid.exists())
        process.kill()
        process.wait(timeout=PATIENCE_SECONDS)
        pid = int(child_pid.read_text(encoding="utf-8").strip())
        assert wait_until(lambda: marker.exists()), (
            "the child outlived a leader-only kill and still wrote no marker, "
            "so the assertion it controls for could not have failed either")
        assert marker.read_text(encoding="utf-8").strip() == "survived"
        # Reaped by init rather than by this test, since it was never this
        # test's child; what matters is that it ran to completion.
        assert wait_until(lambda: not alive(pid))
    finally:
        if process.poll() is None:  # pragma: no cover - the kill above succeeded
            process.kill()


# --------------------------------------------------------------------------
# What the command is handed
# --------------------------------------------------------------------------


def test_the_command_receives_the_entry_as_json_on_stdin(queue, commands,
                                                         tmp_path):
    document = tmp_path / "stdin.json"
    environment = tmp_path / "key.env"
    key = seeded(queue)
    drained(queue, transport_for(captures(commands, document, environment)))

    handed = json.loads(document.read_text(encoding="utf-8"))
    assert handed["key"] == key
    assert handed["identity"] == IDENTITY
    assert handed["state"] == outbox.PENDING
    assert handed["payload"] == PAYLOAD


def test_the_key_is_in_the_environment_and_equals_the_one_on_stdin(
        queue, commands, tmp_path):
    """Both copies come from the same field, so they cannot disagree — which
    is a claim about two observed values rather than about one."""
    document = tmp_path / "stdin.json"
    environment = tmp_path / "key.env"
    key = seeded(queue)
    drained(queue, transport_for(captures(commands, document, environment)))

    from_environment = environment.read_text(encoding="utf-8")
    assert from_environment == key
    assert from_environment == json.loads(
        document.read_text(encoding="utf-8"))["key"]


# --------------------------------------------------------------------------
# The transport offers no lookup
# --------------------------------------------------------------------------


class TransportWithALookup(command_transport.CommandTransport):
    """The transport with a lookup that establishes nothing, for the control.

    `outbox._look_up` records a problem when a lookup raises and records
    nothing when a transport offers none. Both halves are the same code path,
    so the absence below is only meaningful beside a subject that produces
    the note.
    """

    def look_up(self, key):
        raise RuntimeError("the provider could not be asked about the key")


def attempted_once(queue: Path, commands: Path) -> str:
    """One entry that has already been offered to a provider and deferred."""
    key = seeded(queue)
    drained(queue, transport_for(exits(commands, TRANSIENT_EXIT_CODE,
                                       STDERR_MESSAGE, "first-attempt.sh")))
    assert entry_of(queue, key)["attempts"] == 1
    return key


def test_the_transport_exposes_no_look_up_attribute():
    assert not hasattr(command_transport.CommandTransport, "look_up")
    assert not hasattr(transport_for("does-not-matter"), "look_up")
    # The control for that absence: a transport that does offer one is
    # recognised by the same question, so "no attribute" is a fact about this
    # class rather than about how it was asked.
    assert hasattr(TransportWithALookup(command="does-not-matter"), "look_up")


def test_an_entry_with_a_prior_attempt_files_once_and_records_no_lookup_note(
        queue, commands):
    key = attempted_once(queue, commands)
    ledger = commands / "second-attempt-invocations"
    landing = fixture_command(
        commands, "lands-and-counts.sh",
        f'echo invoked >> "{ledger}"\n'
        f'echo "{REFERENCE}"\n')

    summary = drained(queue, transport_for(landing))

    assert summary.notes == ()
    assert summary.landed_keys == (key,)
    assert entry_of(queue, key)["reference"] == REFERENCE
    assert ledger.read_text(encoding="utf-8").split() == ["invoked"]


def test_a_transport_that_did_offer_a_lookup_would_record_its_note(
        tmp_path):
    """The control: the same entry, the same sync, a lookup that raises.

    The note the summary then carries is what the assertion above requires to
    be absent, so its absence is about the transport offering none rather than
    about a summary that records nothing.
    """
    queue = outbox.queue_dir(tmp_path / "lookup-target")
    commands = tmp_path / "lookup-commands"
    key = attempted_once(queue, commands)

    summary = drained(queue, TransportWithALookup(
        command=str(lands(commands)), timeout=DEFAULT_TIMEOUT_SECONDS))

    assert [note for note in summary.notes if key in note], summary.notes
    assert summary.landed_keys == (key,)


# --------------------------------------------------------------------------
# The ambiguous write, resolved by the command
# --------------------------------------------------------------------------


def test_an_idempotent_command_invoked_twice_under_one_key_creates_once(
        queue, commands):
    """The sentence the whole design rests on, driven rather than described.

    The first invocation is made directly and its answer thrown away — the
    ambiguous write, in which the provider created the issue and the response
    never came back. The sync that follows invokes the same command again for
    the same key, and the fixture, which searches before it writes, leaves one
    filing behind and names what it found.
    """
    ledger = commands / "ledger"
    transport = transport_for(idempotent(commands, ledger))
    key = seeded(queue)
    entry = entry_of(queue, key)

    lost = transport.file(entry)
    assert lost.reference == REFERENCE

    summary = drained(queue, transport)

    assert summary.landed_keys == (key,)
    landed = entry_of(queue, key)
    assert landed["state"] == outbox.LANDED
    assert landed["reference"] == REFERENCE
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    assert lines == [f"{key} {REFERENCE}"], lines


# --------------------------------------------------------------------------
# scripts/l5-sync: the wiring, and what it was before this story
# --------------------------------------------------------------------------


NO_TRANSPORT = "no transport is configured"
CONFIG = "workflow: story-workflow\n"


def sync_target(tmp_path: Path, name: str, **keys: str) -> Path:
    """A target l5-sync can resolve, configured with the keys named."""
    root = tmp_path / name
    (root / ".harness").mkdir(parents=True)
    lines = CONFIG + "".join(f"{key}: {value}\n" for key, value in keys.items())
    (root / ".harness" / "config.yaml").write_text(lines, encoding="utf-8")
    return root


def run_sync(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / "l5-sync")],
                          cwd=cwd, capture_output=True, text=True, timeout=120)


def test_l5_sync_with_a_command_configured_files_through_it(tmp_path):
    target = sync_target(tmp_path, "configured",
                         sync_command=f".harness/{SYNC_DIR}/lands.sh")
    lands(target / ".harness" / SYNC_DIR)
    key = seeded(outbox.queue_dir(target))

    result = run_sync(target)

    assert result.returncode == 0, result.stderr
    assert NO_TRANSPORT not in result.stdout
    entry = entry_of(outbox.queue_dir(target), key)
    assert entry["state"] == outbox.LANDED
    assert entry["reference"] == REFERENCE


def test_l5_sync_with_no_command_configured_behaves_as_it_did_before(tmp_path):
    """The control for the absence above, and the criterion in its own right.

    The same queue, the same script, one key removed from the configuration:
    the sentence the test above requires to be gone is here, nothing was
    filed, the queue is reported as it stands, and the exit rule is the same.
    """
    target = sync_target(tmp_path, "unconfigured")
    key = seeded(outbox.queue_dir(target))

    result = run_sync(target)

    assert result.returncode == 0, result.stderr
    assert NO_TRANSPORT in result.stdout
    assert key in result.stdout
    assert entry_of(outbox.queue_dir(target), key)["state"] == outbox.PENDING
    assert entry_of(outbox.queue_dir(target), key)["attempts"] == 0


@pytest.mark.parametrize("value", ["soon", "0", "-3"])
def test_l5_sync_refuses_a_timeout_that_is_not_a_positive_number(tmp_path,
                                                                value):
    """Naming the key and the value, and filing nothing.

    The command is configured too and counts its own invocations, so "files
    nothing" is read off a ledger that stayed empty rather than off the entry
    alone.
    """
    target = sync_target(tmp_path, f"refused-{value}",
                         sync_command=f".harness/{SYNC_DIR}/counting.sh",
                         sync_timeout_seconds=value)
    ledger = target / "invocations"
    counting(target / ".harness" / SYNC_DIR, ledger, 0)
    key = seeded(outbox.queue_dir(target))

    result = run_sync(target)

    assert result.returncode == 1
    assert "sync_timeout_seconds" in result.stderr
    assert value in result.stderr
    assert result.stdout == ""
    assert not ledger.exists()
    assert entry_of(outbox.queue_dir(target), key)["state"] == outbox.PENDING


def test_an_unset_timeout_is_the_default_duration_rather_than_zero(tmp_path):
    """What "not zero" means for a bound nobody configured, in three halves.

    The transport `build_transport` returns carries the default duration; a
    command that takes a moment still lands under it, which a bound of zero
    would have killed; and the bound is still a bound, which is asserted at
    the transport rather than by waiting a minute out to watch a kill.
    """
    target = sync_target(tmp_path, "defaulted",
                         sync_command=f".harness/{SYNC_DIR}/lands.sh")
    lands(target / ".harness" / SYNC_DIR)
    key = seeded(outbox.queue_dir(target))

    result = run_sync(target)
    assert result.returncode == 0, result.stderr
    assert entry_of(outbox.queue_dir(target), key)["state"] == outbox.LANDED

    l5_sync = conftest.load_script("l5-sync")
    transport, problem = l5_sync.build_transport(
        harness_config.load_config(target), target)
    assert problem == ""
    assert transport.timeout == DEFAULT_TIMEOUT_SECONDS
    assert transport.timeout > 0

    # A command that does not answer instantly still lands under the default,
    # which is what separates a real duration from a bound of zero: zero would
    # have killed this one and left the entry pending with a timeout to
    # explain it.
    slow = fixture_command(tmp_path / "slow-command", "takes-a-moment.sh",
                           f'sleep 1\necho "{REFERENCE}"\n')
    entry = filed_through(outbox.queue_dir(tmp_path / "slow-target"),
                          transport_for(slow, timeout=transport.timeout))
    assert entry["state"] == outbox.LANDED
    assert entry["reference"] == REFERENCE


# --------------------------------------------------------------------------
# scripts/l5-init installs the reference command
# --------------------------------------------------------------------------


@pytest.fixture
def initialized(tmp_path: Path) -> Path:
    """A target built by running the real l5-init, as a target's own would be."""
    root = tmp_path / "fresh"
    root.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "l5-init"), "--test-command",
         "echo tests-ok"],
        cwd=root, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return root


def test_l5_init_installs_the_reference_command_executable(initialized):
    installed = initialized / ".harness" / SYNC_DIR
    assert installed.is_dir()
    assert sorted(path.name for path in installed.iterdir()) == REFERENCE_SCRIPTS
    assert REFERENCE_SCRIPTS, "the harness ships no reference sync command"
    for name in REFERENCE_SCRIPTS:
        copy = installed / name
        assert copy.read_bytes() == (TEMPLATES / SYNC_DIR / name).read_bytes()
        assert copy.stat().st_mode & stat.S_IXUSR
        assert os.access(copy, os.X_OK)


def test_a_freshly_initialized_target_sets_neither_new_key(initialized):
    """It opts in to filing rather than discovering that it files.

    The control is this repository's own configuration in the test below,
    where the same reader over the same keys finds both set — so "unset" is a
    fact about the template rather than about a reader that finds nothing.
    """
    config = harness_config.load_config(initialized)
    assert "sync_command" not in config
    assert "sync_timeout_seconds" not in config
    # The keys are in the file, commented out with their explanation, which is
    # what makes opting in an uncomment rather than a search of the schema.
    written = (initialized / ".harness" / "config.yaml").read_text(
        encoding="utf-8")
    assert "# sync_command:" in written
    assert "# sync_timeout_seconds:" in written


def test_the_template_carries_both_keys_commented_out(tmp_path):
    """The same claim at the template l5-init copies, loaded as a config.

    Read through the real loader against a throwaway target, because the
    template becomes a target's configuration verbatim but for one
    substitution.
    """
    target = tmp_path / "from-template"
    (target / ".harness").mkdir(parents=True)
    text = (TEMPLATES / "config.yaml").read_text(encoding="utf-8")
    (target / ".harness" / "config.yaml").write_text(
        text.replace("{test_command}", "echo tests-ok"), encoding="utf-8")

    config = harness_config.load_config(target)
    assert "sync_command" not in config
    assert "sync_timeout_seconds" not in config
    assert "# sync_command:" in text
    assert "# sync_timeout_seconds:" in text


def test_a_target_that_opts_in_files_through_the_command_it_named(initialized):
    """What the two absences above are worth: opting in is one line.

    The installed reference script is replaced by a fixture command this test
    wrote — the shipped one reaches a tracker, and nothing here reaches a
    network — so what is under test is that the configured path is the one the
    harness runs.
    """
    installed = initialized / ".harness" / SYNC_DIR / "opted-in.sh"
    fixture_command(installed.parent, installed.name, f'echo "{REFERENCE}"\n')
    config = initialized / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").rstrip("\n")
        + f"\nsync_command: .harness/{SYNC_DIR}/{installed.name}\n",
        encoding="utf-8")
    key = seeded(outbox.queue_dir(initialized))

    result = run_sync(initialized)

    assert result.returncode == 0, result.stderr
    assert NO_TRANSPORT not in result.stdout
    assert entry_of(outbox.queue_dir(initialized), key)["state"] == \
        outbox.LANDED


# --------------------------------------------------------------------------
# This repository is wired to the script it ships
# --------------------------------------------------------------------------


def test_this_repository_carries_the_installed_script_and_both_live_keys():
    """A shipped artifact, so this repository's own `.harness/` is the subject.

    The reference implementation is exercised by the repository that ships it,
    which is what stops the template being a file nobody ever runs.
    """
    config = harness_config.load_config(REPO_ROOT)
    assert config["sync_command"]
    assert config["sync_timeout_seconds"]
    assert float(config["sync_timeout_seconds"]) > 0

    command = REPO_ROOT / config["sync_command"]
    assert command.is_file()
    assert os.access(command, os.X_OK)
    for name in REFERENCE_SCRIPTS:
        installed = REPO_ROOT / ".harness" / SYNC_DIR / name
        assert installed.read_bytes() == (TEMPLATES / SYNC_DIR / name).read_bytes()
        assert os.access(installed, os.X_OK)


def test_the_reference_script_states_the_contract_it_satisfies():
    """Its header is the documentation a target writing its own script reads.

    The vocabulary is derived from the transport rather than written here, so
    a code or a variable that changed in the module and not in the script is
    reported instead of being restated identically in two places.
    """
    header = (TEMPLATES / SYNC_DIR / "github.sh").read_text(encoding="utf-8")
    assert KEY_ENVIRONMENT_VARIABLE in header
    assert str(TRANSIENT_EXIT_CODE) in header
    assert "idempotent" in header.lower()
    assert "must not commit" in header.lower()


def committing_lines(script: str) -> list[str]:
    """The lines of a shell script that would run git, comments excluded.

    A predicate rather than a substring assertion, because the shipped script
    *says* "do not add a git commit" in its header and a whole-text search
    therefore reports the sentence promising the opposite of what it looks
    for. What the contract is about is what the script executes.
    """
    return [line for line in script.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
            and "git " in line]


def test_the_reference_script_keeps_the_promise_that_it_does_not_commit(
        tmp_path):
    """It writes to a tracker; a human or a run commits.

    The harness does not enforce this — it says so rather than implying a
    check that does not exist — so what is checked here is the script this
    repository ships, and the control is the same script with a commit added,
    which the same predicate must report.
    """
    shipped = (TEMPLATES / SYNC_DIR / "github.sh").read_text(encoding="utf-8")
    assert committing_lines(shipped) == []

    violating = shipped + 'git commit -m "filed the entry"\n'
    assert committing_lines(violating) == ['git commit -m "filed the entry"']


# --------------------------------------------------------------------------
# The queue module this transport was written against is untouched
# --------------------------------------------------------------------------


UNCHANGED = ("orchestration/outbox.py", "orchestration/story_coordinator.py")


@pytest.mark.parametrize("relative", UNCHANGED)
def test_this_story_left_the_queue_and_the_coordinator_alone(relative, tmp_path):
    """Restated over a story this test builds rather than recalled out of this
    repository's own commit graph.

    The claim is the story's: a transport written against the queue's contract
    does not edit the queue, and no sweep call site is added inside a run. The
    predicate is the shared resolution's, and the control beside it shows the
    same call reporting the violation — so an empty diff here is a fact about
    a story that respected the path rather than about a comparison bounded at
    commits where nothing could differ.
    """
    respecting = conftest.constructed_story(tmp_path, respected=[relative],
                                            name="scope-respected")
    assert conftest.constructed_story_diff(respecting, [relative]) == ""
    violating = conftest.constructed_story(tmp_path, violated=[relative],
                                           name="scope-violated")
    assert conftest.constructed_story_diff(violating, [relative]) != ""


def test_the_transport_answers_only_in_the_filings_the_queue_defines():
    """Which is what makes the state one decision, taken in the queue.

    Driven at the answers rather than at the source: each fixture outcome above
    came back as an `outbox.Filing`, and the three constructors the module
    imports are the queue's own.
    """
    assert command_transport.filed is outbox.filed
    assert command_transport.refused is outbox.refused
    assert command_transport.deferred is outbox.deferred
    assert command_transport.Filing is outbox.Filing


