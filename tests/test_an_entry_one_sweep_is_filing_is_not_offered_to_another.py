"""Independent validation for story-166: an entry one sweep is filing is not
offered to a sweep running beside it.

Written from the acceptance criteria rather than from the implementation. The
subjects, kept apart:

  * **two sweeps, one entry.** Driven for real: one sync is held inside its
    transport's file call on a thread while a second sync runs to completion
    over the same queue, and what is asserted is which transport was called,
    what each summary says, and what the three directories hold afterwards.

  * **the claim.** Where the entry file is between the claim and the answer,
    under what name, in what state, and what the readers that predate the
    claim — the queue listing, the local index, the status listing — make of
    a queue whose entry is in flight.

  * **what settles a claim.** Each of the three answers a transport can give,
    and the exit that is not an answer at all, observed for what it leaves in
    flight and in the queue and in what order it wrote and unlinked.

  * **the reclaim.** A killed sweep is simulated by making the claim and never
    settling it; the clock is an injected `now` set against the in-flight
    file's own change time, so nothing here sleeps and nothing is compared
    against the machine.

  * **the seam.** That the bound reaches `sync` through `drain`, derived from
    the transport it was handed, and that the explicit drain and the sweep
    both go through it.

  * **the shipped artifacts.** The entry schema is a live artifact and the
    subject of the assertion that reads it; the module and the script are read
    as sources only where the criterion is about what this repository ships.

Every absence asserted here sits beside a demonstration that it can fail:

  * "the second transport was not called" sits beside the first, which the
    same recorder caught being called once, and beside the fresh entry the
    same sync filed;
  * "the queue does not list the entry" sits beside the same listing before
    the claim, which reported it;
  * "nothing is in flight afterwards" sits beside the in-flight copy the
    transport itself saw while it was being called;
  * "the lookup was not consulted" for a fresh entry sits beside the reclaimed
    entry in the same sync, for which it was;
  * "the fresh claim was left alone" sits beside the stale claim under the
    same transport, which the same sync reclaimed and filed;
  * "the claim is one rename" sits beside the recorder catching that one.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

import command_transport
import machine_load
import outbox
import outbox_sweep
import run_status
import schema_validator

import test_outbox as queue_tests
from test_outbox import (
    FakeTransport, IDENTITY, OTHER_IDENTITY, REFERENCE, SECOND_REFERENCE,
    filing, seeded_pending, terminal, transient,
)

REPO_ROOT = Path(outbox.__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

#: The reclaim bound these tests hand `sync`, in seconds. An input rather than
#: anything the harness ships: the tests set `now` against it explicitly.
BOUND = 120.0

#: How long a test waits for a thread to reach a point it must reach before
#: the question can be asked. A precondition and not the question: a thread
#: that has not got there is reported through `machine_load`, not failed.
PRECONDITION_SECONDS = 60


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


@pytest.fixture
def target(tmp_path: Path) -> Path:
    root = tmp_path / "a-target"
    root.mkdir()
    return root


@pytest.fixture
def queue(target: Path) -> Path:
    return outbox.queue_dir(target)


def in_flight_copy(queue: Path, key: str) -> Path:
    return outbox.entry_path(outbox.in_flight_beside(queue), key)


def queue_copy(queue: Path, key: str) -> Path:
    return outbox.entry_path(queue, key)


def receipt(queue: Path, key: str) -> Path:
    return outbox.entry_path(outbox.receipts_beside(queue), key)


def in_flight_names(queue: Path) -> list[str]:
    """What the in-flight directory holds; a directory that is gone holds
    nothing, which is the same emptiness."""
    in_flight = outbox.in_flight_beside(queue)
    if not in_flight.exists():
        return []
    return sorted(path.name for path in in_flight.iterdir())


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def stranded(queue: Path, identity: dict = IDENTITY) -> str:
    """An entry a sweep claimed and never settled — a sweep killed outright.

    Made through the module's own claim so what is stranded is exactly what a
    real claim leaves, and never touched again, so its change time is the
    rename's.
    """
    key = seeded_pending(queue, identity)
    claim = outbox.claim_entry(queue, key)
    assert claim.held, claim
    assert in_flight_copy(queue, key).is_file()
    return key


def claimed_at(queue: Path, key: str) -> float:
    return in_flight_copy(queue, key).stat().st_ctime


class ObservingTransport:
    """A transport that records where the entry is at the moment it is asked.

    `file` and `look_up` each note whether the entry's in-flight copy and its
    queue copy exist as they are called, and what the in-flight copy says, so
    "nothing is in flight afterwards" can be set beside "something was in
    flight during", observed by the same code at the only moment it matters.
    """

    def __init__(self, queue: Path, answer, *, lookups=None):
        self.queue = queue
        self.answer = answer
        self.lookups = dict(lookups or {})
        self.filed: list[str] = []
        self.looked_up: list[str] = []
        self.seen: dict[str, dict] = {}

    def _observe(self, operation: str, key: str) -> None:
        held = in_flight_copy(self.queue, key)
        self.seen[f"{operation}:{key}"] = {
            "in_flight": held.is_file(),
            "in_queue": queue_copy(self.queue, key).is_file(),
            "entry": read(held) if held.is_file() else None,
        }

    def file(self, entry):
        self.filed.append(entry["key"])
        self._observe("file", entry["key"])
        answer = self.answer
        return answer(entry) if callable(answer) else answer

    def look_up(self, key):
        self.looked_up.append(key)
        self._observe("look_up", key)
        return self.lookups.get(key, "")


# --------------------------------------------------------------------------
# Two sweeps, one entry: filed once
# --------------------------------------------------------------------------


class HeldTransport:
    """A transport whose file call parks until it is released.

    It sets `reached` on entry so the test knows the first sync is inside the
    call, then waits on `release`, which the test sets once the second sync has
    run to completion. What it records is the same as the fake's: which keys
    it was asked to file, and what the directories held as it was asked.
    """

    timeout = BOUND / 2

    def __init__(self, queue: Path):
        self.queue = queue
        self.reached = threading.Event()
        self.release = threading.Event()
        self.filed: list[str] = []
        self.seen: dict | None = None

    def file(self, entry):
        self.filed.append(entry["key"])
        key = entry["key"]
        self.seen = {
            "in_flight": in_flight_copy(self.queue, key).is_file(),
            "in_queue": queue_copy(self.queue, key).is_file(),
        }
        self.reached.set()
        self.release.wait()
        return filing(REFERENCE)

    def look_up(self, key):
        raise AssertionError("a first-attempt entry is not looked up")


def test_two_syncs_over_one_entry_call_the_transport_once_between_them(queue):
    key = seeded_pending(queue)
    first = HeldTransport(queue)
    second = FakeTransport(answer=filing(SECOND_REFERENCE),
                           lookups={key: SECOND_REFERENCE})
    outcome: dict = {}

    def run_first():
        outcome["summary"] = outbox.sync(queue, first, reclaim_after=BOUND)

    thread = threading.Thread(target=run_first, daemon=True)
    thread.start()
    try:
        if not first.reached.wait(PRECONDITION_SECONDS):
            if thread.is_alive():
                machine_load.inconclusive(
                    "the first sync did not reach its transport's file call")
            pytest.fail(f"the first sync returned without calling its "
                        f"transport: {outcome}")

        # The first sync is inside its file call. Run the second to completion.
        second_summary = outbox.sync(queue, second, reclaim_after=BOUND)
    finally:
        first.release.set()
        thread.join(PRECONDITION_SECONDS)

    assert not thread.is_alive()
    first_summary = outcome["summary"]

    # Exactly one file call between them, and it was the first sweep's.
    assert first.filed == [key]
    assert second.filed == []
    assert second.looked_up == []

    # The sweep that found the entry claimed reports it as pending, notes it,
    # and did not raise (it returned a Summary, which is how we got here).
    assert second_summary.pending_keys == (key,)
    assert second_summary.landed == 0
    assert second_summary.failed == 0
    assert second_summary.poisoned == 0
    another_sweep = [note for note in second_summary.notes
                     if "another sweep" in note]
    assert len(another_sweep) == 1, second_summary.notes

    # The first landed it.
    assert first_summary.landed_keys == (key,)

    # Afterwards: one receipt, no queue file at the key, nothing in flight.
    landed = read(receipt(queue, key))
    assert landed["state"] == outbox.LANDED
    assert landed["reference"] == REFERENCE
    assert not queue_copy(queue, key).exists()
    assert in_flight_names(queue) == []
    # The control for that emptiness: the first transport saw the entry in
    # flight and not in the queue at the moment it was called.
    assert first.seen == {"in_flight": True, "in_queue": False}


# --------------------------------------------------------------------------
# The claim: where the entry is between the claim and the answer
# --------------------------------------------------------------------------


def test_a_claimed_entry_leaves_the_queue_for_the_in_flight_directory(
        target, queue):
    key = seeded_pending(queue)
    before = queue_copy(queue, key).read_bytes()
    # The control for every "not listed" below: before the claim the queue
    # listing, the local index and the status listing all report the entry.
    assert [path.name for path in outbox.entry_files(queue)] == [
        queue_copy(queue, key).name]
    assert outbox.local_index(target).queued == frozenset({key})
    assert "pending 1" in "\n".join(run_status.format_queue(target))

    claim = outbox.claim_entry(queue, key)

    assert claim.held and not claim.gone and not claim.problem, claim
    held = in_flight_copy(queue, key)
    # The shipped location is the subject here: the criterion names it.
    assert held.relative_to(target) == (
        Path(".harness", "outbox", "in-flight") / f"{key}{outbox.ENTRY_SUFFIX}")
    assert held.name == queue_copy(queue, key).name
    # A rename and not a rewrite: the same bytes, still a valid pending entry.
    assert held.read_bytes() == before
    entry, problems = outbox.read_entry(held)
    assert problems == []
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 0
    # The queue no longer lists it, and the readers that predate the claim
    # read the queue exactly as before: no error, nothing unreadable, and the
    # in-flight directory beneath the queue is not mistaken for an entry.
    assert not queue_copy(queue, key).exists()
    assert outbox.entry_files(queue) == []
    index = outbox.local_index(target)
    assert index.read, index.reason
    assert index.queued == frozenset()
    assert index.unreadable == 0
    listing = "\n".join(run_status.format_queue(target))
    assert "could not be read" not in listing
    assert "pending 0" in listing
    assert "poisoned 0" in listing


def test_a_second_claim_on_the_same_key_finds_it_gone(queue):
    key = seeded_pending(queue)
    first = outbox.claim_entry(queue, key)
    second = outbox.claim_entry(queue, key)

    assert first.held
    assert second.gone and not second.held and not second.problem, second
    # Gone is a fact about the source and not a refusal of the claim: the
    # copy the first claim moved is exactly where it was.
    assert in_flight_copy(queue, key).is_file()


def test_the_claim_is_one_rename_from_the_queue_path_to_the_in_flight_path(
        queue, monkeypatch):
    key = seeded_pending(queue)
    renames: list[tuple[str, str]] = []
    real_rename = os.rename

    def recording(source, destination, *args, **kwargs):
        renames.append((str(source), str(destination)))
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "rename", recording)
    claim = outbox.claim_entry(queue, key)

    assert claim.held
    assert renames == [(str(queue_copy(queue, key)),
                        str(in_flight_copy(queue, key)))]


def test_the_transport_is_called_with_the_entry_in_flight_and_not_in_the_queue(
        queue):
    key = seeded_pending(queue)
    transport = ObservingTransport(queue, filing())

    outbox.sync(queue, transport, reclaim_after=BOUND)

    assert transport.filed == [key]
    seen = transport.seen[f"file:{key}"]
    assert seen["in_flight"] is True
    assert seen["in_queue"] is False
    assert seen["entry"]["state"] == outbox.PENDING


# --------------------------------------------------------------------------
# What settles a claim: the three answers, and the exit that is not one
# --------------------------------------------------------------------------


def test_a_landed_entry_leaves_no_copy_in_flight_and_none_in_the_queue(queue):
    key = seeded_pending(queue)
    transport = ObservingTransport(queue, filing())

    summary = outbox.sync(queue, transport, reclaim_after=BOUND)

    assert summary.landed_keys == (key,)
    assert read(receipt(queue, key))["state"] == outbox.LANDED
    assert not queue_copy(queue, key).exists()
    assert in_flight_names(queue) == []
    # Control: it *was* in flight while the transport was being called.
    assert transport.seen[f"file:{key}"]["in_flight"] is True


def test_a_transient_outcome_leaves_it_pending_in_the_queue_with_nothing_in_flight(
        queue):
    key = seeded_pending(queue)
    transport = ObservingTransport(queue, transient())

    summary = outbox.sync(queue, transport, reclaim_after=BOUND)

    assert summary.pending_keys == (key,)
    entry = read(queue_copy(queue, key))
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert "payload" in entry
    assert in_flight_names(queue) == []
    assert transport.seen[f"file:{key}"]["in_flight"] is True


def test_a_terminal_outcome_leaves_it_failed_in_the_queue_with_nothing_in_flight(
        queue):
    key = seeded_pending(queue)
    transport = ObservingTransport(queue, terminal())

    summary = outbox.sync(queue, transport, reclaim_after=BOUND)

    assert summary.failed_keys == (key,)
    entry = read(queue_copy(queue, key))
    assert entry["state"] == outbox.FAILED
    assert entry["attempts"] == 1
    assert in_flight_names(queue) == []
    assert transport.seen[f"file:{key}"]["in_flight"] is True


def test_every_outcome_writes_its_result_before_unlinking_the_in_flight_copy(
        queue, monkeypatch):
    """The order, observed rather than inferred: every write and every unlink
    the sync makes is recorded in sequence, and for each answer the write of
    the outcome comes before the unlink of the in-flight copy."""
    key_by_answer = {}
    for identity, answer in ((IDENTITY, filing()),
                             (OTHER_IDENTITY, transient()),
                             ({"kind": "sample", "subject": "story-003"},
                              terminal())):
        key_by_answer[seeded_pending(queue, identity)] = answer

    events: list[tuple[str, str]] = []
    real_write, real_unlink = outbox.write_entry, Path.unlink

    def recording_write(directory, entry):
        events.append(("write", str(outbox.entry_path(directory, entry["key"]))))
        return real_write(directory, entry)

    def recording_unlink(self, *args, **kwargs):
        events.append(("unlink", str(self)))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(outbox, "write_entry", recording_write)
    monkeypatch.setattr(Path, "unlink", recording_unlink)
    transport = FakeTransport(answer=lambda entry: key_by_answer[entry["key"]])
    outbox.sync(queue, transport, reclaim_after=BOUND)

    assert sorted(transport.filed) == sorted(key_by_answer)
    for key, answer in key_by_answer.items():
        held = str(in_flight_copy(queue, key))
        settled_into = (outbox.receipts_beside(queue) if answer.reference
                        else queue)
        written = events.index(("write", str(outbox.entry_path(settled_into, key))))
        unlinked = events.index(("unlink", held))
        assert written < unlinked, (key, events)


def test_a_base_exception_out_of_the_transport_propagates_and_returns_the_entry(
        queue):
    key = seeded_pending(queue)
    killed = FakeTransport(raises=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        outbox.sync(queue, killed, reclaim_after=BOUND)

    assert killed.filed == [key]
    entry = read(queue_copy(queue, key))
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert entry["last_error"]
    assert in_flight_names(queue) == []

    # Because its attempt is counted, the next offer asks first.
    knows_it = FakeTransport(answer=filing(), lookups={key: REFERENCE})
    outbox.sync(queue, knows_it, reclaim_after=BOUND)
    assert knows_it.looked_up == [key]
    assert knows_it.filed == []
    assert read(receipt(queue, key))["state"] == outbox.LANDED


def test_an_ordinary_exception_out_of_the_transport_still_does_not_propagate(
        queue):
    """The control for the propagation above: the same transport raising an
    Exception is the transient failure it always was, and the entry is left
    exactly as the BaseException left it — pending, counted, nothing in
    flight."""
    key = seeded_pending(queue)
    summary = outbox.sync(queue, FakeTransport(raises=RuntimeError("down")),
                          reclaim_after=BOUND)
    assert summary.pending_keys == (key,)
    entry = read(queue_copy(queue, key))
    assert entry["attempts"] == 1
    assert in_flight_names(queue) == []


# --------------------------------------------------------------------------
# The reclaim: a killed sweep's claim goes back to the queue, and asks first
# --------------------------------------------------------------------------


def test_a_stale_claim_is_reclaimed_and_offered_with_the_lookup_consulted(
        queue):
    """One sync, two entries: a claim older than the bound, and a fresh entry
    nobody has claimed. The stale one is reclaimed and then looked up; the
    fresh one is filed without a lookup, which is the control for the lookup
    being *because* of the reclaimed attempt count."""
    stale = stranded(queue, IDENTITY)
    fresh = seeded_pending(queue, OTHER_IDENTITY)
    now = claimed_at(queue, stale) + BOUND + 1
    transport = ObservingTransport(queue, filing(SECOND_REFERENCE),
                                   lookups={stale: REFERENCE})

    summary = outbox.sync(queue, transport, now=now, reclaim_after=BOUND)

    # The stale one was looked up and found; the fresh one was filed unasked.
    assert transport.looked_up == [stale]
    assert transport.filed == [fresh]
    # What the lookup saw: the entry as the reclaim wrote it back — pending,
    # its attempt counted, and saying it was claimed and not settled.
    seen = transport.seen[f"look_up:{stale}"]["entry"]
    assert seen["state"] == outbox.PENDING
    assert seen["attempts"] == 1
    assert "claimed" in seen["last_error"] and "settled" in seen["last_error"]
    # Both landed, in the index, with nothing left in flight or in the queue.
    assert sorted(summary.landed_keys) == sorted((stale, fresh))
    assert read(receipt(queue, stale))["reference"] == REFERENCE
    assert read(receipt(queue, fresh))["reference"] == SECOND_REFERENCE
    assert outbox.entry_files(queue) == []
    assert in_flight_names(queue) == []


def test_a_stale_claim_is_returned_to_the_queue_with_no_transport_present(
        queue):
    """The reclaim is not the transport's: with none present, the stranded
    entry still comes back to the queue as pending, counted, with the error
    recorded, and its in-flight copy removed."""
    key = stranded(queue)
    now = claimed_at(queue, key) + BOUND + 1

    summary = outbox.sync(queue, None, now=now, reclaim_after=BOUND)

    assert not summary.transport
    assert summary.pending_keys == (key,)
    entry = read(queue_copy(queue, key))
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert entry["last_error"]
    assert "payload" in entry
    assert in_flight_names(queue) == []


def test_a_fresh_claim_is_left_where_it_is(queue):
    """The other side of the bound, under the same transport the stale test
    uses: younger than the bound, the claim is somebody's, and the sync
    neither reads it through the transport nor moves it."""
    key = stranded(queue)
    before = in_flight_copy(queue, key).read_bytes()
    now = claimed_at(queue, key) + BOUND / 2
    transport = ObservingTransport(queue, filing(), lookups={key: REFERENCE})

    summary = outbox.sync(queue, transport, now=now, reclaim_after=BOUND)

    assert transport.filed == []
    assert transport.looked_up == []
    assert in_flight_copy(queue, key).read_bytes() == before
    assert not queue_copy(queue, key).exists()
    assert not receipt(queue, key).exists()
    # Reported as pending with the note, and nothing else counted.
    assert summary.pending_keys == (key,)
    assert (summary.landed, summary.failed, summary.poisoned) == (0, 0, 0)
    another_sweep = [note for note in summary.notes if "another sweep" in note]
    assert len(another_sweep) == 1, summary.notes


def test_a_claim_short_of_the_bound_is_left_and_one_past_it_is_reclaimed(queue):
    """The bound is decided by age against `now`: a `now` one second short of
    it leaves the claim and one past it reclaims — the same claim, the same
    transport, differing only in `now`."""
    key = stranded(queue)
    at = claimed_at(queue, key)
    left = outbox.sync(queue, None, now=at + BOUND - 1, reclaim_after=BOUND)
    assert in_flight_copy(queue, key).is_file()
    assert left.pending_keys == (key,)

    taken = outbox.sync(queue, None, now=at + BOUND + 1, reclaim_after=BOUND)
    assert not in_flight_copy(queue, key).exists()
    assert read(queue_copy(queue, key))["attempts"] == 1
    assert taken.pending_keys == (key,)


def test_a_sync_given_no_bound_reclaims_nothing(queue):
    """`sync` reads no configuration key: the bound arrives as a parameter, and
    a caller giving none gets no reclaim however old the claim. The control
    is the same call with a bound, which does reclaim it."""
    key = stranded(queue)
    far_future = claimed_at(queue, key) + BOUND * 1000

    outbox.sync(queue, None, now=far_future)
    assert in_flight_copy(queue, key).is_file()

    outbox.sync(queue, None, now=far_future, reclaim_after=BOUND)
    assert not in_flight_copy(queue, key).exists()
    assert queue_copy(queue, key).is_file()


def test_two_syncs_reclaiming_the_same_stale_claim_agree(queue):
    """Idempotent: reclaimed once, the entry is in the queue with one attempt
    counted; a second sync over the same `now` finds nothing in flight and
    changes nothing."""
    key = stranded(queue)
    now = claimed_at(queue, key) + BOUND + 1

    outbox.sync(queue, None, now=now, reclaim_after=BOUND)
    after_first = queue_copy(queue, key).read_bytes()
    outbox.sync(queue, None, now=now, reclaim_after=BOUND)

    assert queue_copy(queue, key).read_bytes() == after_first
    assert read(queue_copy(queue, key))["attempts"] == 1
    assert in_flight_names(queue) == []


# --------------------------------------------------------------------------
# A claim that cannot be made files the entry unclaimed
# --------------------------------------------------------------------------


def test_a_claim_that_cannot_be_made_files_the_entry_unclaimed_and_says_why(
        queue):
    key = seeded_pending(queue)
    # The in-flight directory cannot be created: a regular file stands where
    # it would go.
    in_flight = outbox.in_flight_beside(queue)
    in_flight.write_text("not a directory\n", encoding="utf-8")
    transport = ObservingTransport(queue, filing())

    summary = outbox.sync(queue, transport, reclaim_after=BOUND)

    # Filed as an unclaimed entry is: from the queue, never in flight.
    assert transport.filed == [key]
    assert transport.seen[f"file:{key}"] == {
        "in_flight": False, "in_queue": True, "entry": None}
    assert summary.landed_keys == (key,)
    assert read(receipt(queue, key))["state"] == outbox.LANDED
    assert not queue_copy(queue, key).exists()
    unclaimed = [note for note in summary.notes if "unclaimed" in note]
    assert len(unclaimed) == 1, summary.notes
    # And why: the reason the operating system gave, which names the path.
    assert in_flight.name in unclaimed[0], unclaimed[0]
    # The obstacle is left as it was.
    assert in_flight.read_text(encoding="utf-8") == "not a directory\n"


def test_a_rename_that_raises_still_files_and_returns_a_summary(
        queue, monkeypatch):
    key = seeded_pending(queue)

    def refusing(source, destination, *args, **kwargs):
        raise PermissionError(f"refused: {source}")

    monkeypatch.setattr(os, "rename", refusing)
    transport = FakeTransport(answer=filing())

    summary = outbox.sync(queue, transport, reclaim_after=BOUND)

    assert isinstance(summary, outbox.Summary)
    assert transport.filed == [key]
    assert summary.landed_keys == (key,)
    assert any("unclaimed" in note and "refused" in note
               for note in summary.notes), summary.notes


# --------------------------------------------------------------------------
# A Summary on every path the story adds
# --------------------------------------------------------------------------


def test_an_in_flight_directory_that_cannot_be_listed_returns_a_summary(
        queue, monkeypatch):
    key = seeded_pending(queue)
    in_flight = outbox.in_flight_beside(queue)
    real_entry_files = outbox.entry_files

    def failing_on_in_flight(directory):
        if Path(directory) == in_flight:
            raise PermissionError("the in-flight directory is off limits")
        return real_entry_files(directory)

    monkeypatch.setattr(outbox, "entry_files", failing_on_in_flight)
    transport = FakeTransport(answer=filing())

    summary = outbox.sync(queue, transport, reclaim_after=BOUND)

    assert isinstance(summary, outbox.Summary)
    # The queue itself was still swept.
    assert transport.filed == [key]
    assert summary.landed_keys == (key,)
    assert any("in-flight" in note and "off limits" in note
               for note in summary.notes), summary.notes


def test_an_in_flight_file_that_cannot_be_read_is_noted_and_left_alone(queue):
    """A stale in-flight file that is not an entry: the sync returns, names
    it, and does not move it into the queue where it would read as poison."""
    key = seeded_pending(queue)
    in_flight = outbox.in_flight_beside(queue)
    in_flight.mkdir(parents=True)
    garbage = in_flight / f"garbage{outbox.ENTRY_SUFFIX}"
    garbage.write_bytes(b"{not an entry")
    now = garbage.stat().st_ctime + BOUND + 1
    transport = FakeTransport(answer=filing())

    summary = outbox.sync(queue, transport, now=now, reclaim_after=BOUND)

    assert isinstance(summary, outbox.Summary)
    assert transport.filed == [key]
    assert garbage.read_bytes() == b"{not an entry"
    assert not (queue / garbage.name).exists()
    assert summary.poisoned == 0
    assert any(garbage.name in note for note in summary.notes), summary.notes


def test_the_sweep_returns_a_summary_over_a_queue_holding_only_a_claim(target):
    queue = outbox.queue_dir(target)
    key = stranded(queue)

    summary = outbox_sweep.sweep(target, {}, harness_root=REPO_ROOT)

    assert isinstance(summary, outbox.Summary)
    assert summary.pending_keys == (key,)
    assert in_flight_copy(queue, key).is_file()


# --------------------------------------------------------------------------
# The seam: the bound is derived in drain and reaches sync through it
# --------------------------------------------------------------------------


class Timed:
    def __init__(self, timeout):
        self.timeout = timeout

    def file(self, entry):
        raise AssertionError("not called")


def test_the_bound_is_twice_the_transports_timeout_or_twice_the_default():
    real = command_transport.CommandTransport(command="true", timeout=7,
                                              cwd=Path("."))
    assert outbox_sweep.reclaim_bound(real) == 14
    assert outbox_sweep.reclaim_bound(Timed(0.5)) == 1.0
    assert outbox_sweep.reclaim_bound(None) == (
        2 * command_transport.DEFAULT_TIMEOUT_SECONDS)
    # A transport carrying no usable timeout is bounded by the default too.
    assert outbox_sweep.reclaim_bound(FakeTransport(answer=filing())) == (
        2 * command_transport.DEFAULT_TIMEOUT_SECONDS)


def test_drain_hands_sync_the_bound_it_derived(queue, monkeypatch):
    handed: list[dict] = []

    def recording_sync(queue_given, transport=None, harness_root=None,
                       now=None, **kwargs):
        handed.append(dict(kwargs, transport=transport))
        return outbox.Summary(transport=transport is not None)

    monkeypatch.setattr(outbox, "sync", recording_sync)

    outbox_sweep.drain(queue, Timed(7))
    outbox_sweep.drain(queue, None)
    outbox_sweep.drain(queue, Timed(7), reclaim_after=3)

    assert [call["reclaim_after"] for call in handed] == [
        14, 2 * command_transport.DEFAULT_TIMEOUT_SECONDS, 3]


def test_the_sweep_reaches_sync_through_drain(target, monkeypatch):
    """Both coordinator sweeps call `sweep`; `sweep` calls `drain`; `drain` is
    the single caller of `sync` (held by the scan in tests/test_outbox.py).
    Here the middle link: a sweep with no transport configured still passes
    through `drain`, and `sync` receives the default bound."""
    handed: list[dict] = []
    real_drain = outbox_sweep.drain

    def recording_drain(*args, **kwargs):
        handed.append({"args": args, "kwargs": kwargs})
        return real_drain(*args, **kwargs)

    def recording_sync(queue_given, transport=None, harness_root=None,
                       now=None, **kwargs):
        handed.append({"sync": dict(kwargs)})
        return outbox.Summary(transport=transport is not None)

    monkeypatch.setattr(outbox_sweep, "drain", recording_drain)
    monkeypatch.setattr(outbox, "sync", recording_sync)

    outbox_sweep.sweep(target, {}, harness_root=REPO_ROOT)

    assert len(handed) == 2, handed
    assert "args" in handed[0]
    assert handed[1]["sync"]["reclaim_after"] == (
        2 * command_transport.DEFAULT_TIMEOUT_SECONDS)


def test_the_explicit_drain_reaches_sync_through_drain():
    source = (SCRIPTS / "l5-sync").read_text(encoding="utf-8")
    assert f"{outbox_sweep.__name__}.drain(" in source
    assert f"{outbox.__name__}.sync(" not in source


def test_the_module_takes_the_bound_as_a_parameter_and_reads_no_configuration():
    """`sync` has a `reclaim_after` keyword and the queue module names no
    configuration read; the control is the seam, which does."""
    import inspect

    assert "reclaim_after" in inspect.signature(outbox.sync).parameters
    assert inspect.signature(outbox.sync).parameters["reclaim_after"].kind \
        is inspect.Parameter.KEYWORD_ONLY
    module_source = Path(outbox.__file__).read_text(encoding="utf-8")
    seam_source = Path(outbox_sweep.__file__).read_text(encoding="utf-8")
    assert "config.get(" not in module_source
    assert "config.get(" in seam_source


# --------------------------------------------------------------------------
# scripts/l5-sync over a queue whose only entry is in flight
# --------------------------------------------------------------------------


def test_l5_sync_over_a_queue_holding_only_a_fresh_claim_exits_zero(tmp_path):
    root = tmp_path / "sync-target"
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(queue_tests.MINIMAL_CONFIG,
                                                   encoding="utf-8")
    queue = outbox.queue_dir(root)
    key = stranded(queue)

    result = queue_tests.run_sync(root)

    assert result.returncode == 0, result.stderr
    assert "pending 1" in result.stdout, result.stdout
    assert key in result.stdout
    assert "another sweep" in result.stdout, result.stdout
    # The claim was somebody's, so the script left it exactly where it was.
    assert in_flight_copy(queue, key).is_file()
    assert not queue_copy(queue, key).exists()


# --------------------------------------------------------------------------
# The schema says where a held entry lives, and adds no state
# --------------------------------------------------------------------------


def test_the_entry_schema_names_the_in_flight_directory_and_keeps_its_states():
    schema = schema_validator.load_schema(outbox.ENTRY_SCHEMA)
    in_flight_rel = "/".join(outbox.QUEUE_DIR + (outbox.IN_FLIGHT_DIR,))

    assert in_flight_rel in schema["description"]
    assert "pending" in schema["description"]
    state = schema["properties"]["state"]
    assert state["enum"] == [outbox.PENDING, outbox.LANDED, outbox.FAILED]
    assert outbox.IN_FLIGHT_DIR in state["description"]


def test_the_in_flight_directory_is_derived_beneath_the_queue(queue):
    """Derived from the queue it was handed, in the shape the receipt index
    is, so the three cannot disagree about which repository they belong to."""
    assert outbox.in_flight_beside(queue) == queue / outbox.IN_FLIGHT_DIR
    assert outbox.in_flight_beside(queue).parent == queue
    assert outbox.receipts_beside(queue).parent == queue.parent
