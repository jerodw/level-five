"""Independent validation for the split: the queue holds what is still to be
filed, and the receipt index holds what was.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **where each state lives.** Driven through the queue's own transitions
    against a fake transport, so what an entry *is* stays the module's decision
    and only where it ends up is asserted here.

  * **what each state still decides.** Not asserted about the files at all, but
    driven through the two readers the split exists to serve: the Inspector's
    three drop reasons and `brief_filing`'s three outcomes. Landed suppresses,
    pending is reported as already queued, failed suppresses nothing and is
    filed — exactly as before, now with the answer coming from two directories
    rather than one.

  * **what a sweep opens.** Observed at `outbox.read_entry`, recorded through a
    wrapper this module installs, so "the receipts were not opened" is a fact
    about the calls that were made rather than an inference from a summary.

  * **the migration.** A landed entry left in the queue, which is the shape
    every deployment holds today, built by running the module's own move
    backwards — so the entry a sweep must relocate is one the module itself
    produced.

  * **the shipped artifacts.** `.gitignore`, `schemas/harness-config.schema.json`
    and `templates/config.yaml` are live harness artifacts and are the subjects
    of the assertions that name them: what this harness ships is what those
    criteria are about. The harness root the inspections run against is a
    mirrored one this module does not ship, because *how* the Inspector routes
    is a mechanism and the definition it walks is an input to it.

Every absence asserted here carries a demonstration that it can fail:

  * "the queue holds no landed entry" sits beside a pending entry in the same
    directory, which the same listing reports;
  * "the index holds neither a pending nor a failed entry" sits beside a
    receipt in the same directory, which the same listing reports;
  * "a sweep opened no receipt" sits beside the queue files the same recorder
    caught in the same call, and beside the same recorder over the local index,
    which does open them;
  * "the poisoned receipts were never read" sits beside the same bytes in the
    queue, where the same sweep reports them poisoned;
  * "a poisoned file contributes no key" sits beside the same two directories
    without it, where the same read reports the same two keys;
  * "the two configured artifacts are unchanged by this story" sits beside the
    same diff over the module the split rewrote, which is not empty;
  * "the harness configuration declares what it declared" sits beside the
    requirement that it declares anything at all;
  * "the receipt index is ignored" sits beside a tracked path, which the same
    question does not report;
  * "neither reader reaches the two directories" sits beside the same scan over
    a source that reaches each of them by name;
  * "the moved derivations live in the outbox" sits beside two functions each
    reader does define of its own, which the same question answers with the
    reader's own name.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import brief_filing
import conftest
import filed_query
import inspection
import outbox
import run_status

import test_inspection as producer
import test_outbox as queue_tests

REPO_ROOT = Path(outbox.__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

#: The two directories as repository-relative paths, derived from the module's
#: own constants rather than written here: the story's claims are about *the
#: queue* and *the index*, and a second spelling of where either lives is a
#: second thing to keep true.
QUEUE_REL = "/".join(outbox.QUEUE_DIR)
RECEIPTS_REL = "/".join(outbox.RECEIPTS_DIR)

#: The three states, read off the queue's own module so no state name is
#: spelled here beside the definition that decides them.
PENDING = outbox.PENDING
LANDED = outbox.LANDED
FAILED = outbox.FAILED

#: The Inspector's three already-filed reasons and `brief_filing`'s three
#: outcomes, read off the modules that own them.
ALREADY_FILED = inspection.ALREADY_FILED
ALREADY_FILED_LOCALLY = inspection.ALREADY_FILED_LOCALLY
ALREADY_QUEUED = inspection.ALREADY_QUEUED
FILED_HERE = brief_filing.FILED
FILED_LOCALLY_OUTCOME = brief_filing.ALREADY_FILED_LOCALLY
QUEUED_OUTCOME = brief_filing.ALREADY_QUEUED

IDENTITY = {"kind": "zzz-receipt-split", "subject": "the-first"}
OTHER_IDENTITY = {"kind": "zzz-receipt-split", "subject": "the-second"}
PAYLOAD = {"title": "something to file", "body": "what it says"}

#: How many receipts the deployment in the read-cost assertions holds. A
#: measurement rather than a restatement: it is the number of files that must
#: not be opened, and there is nothing adjacent for a reader to count.
RECEIPTS_HELD = 12

POISON_NAME = f"zzz-not-an-entry{outbox.ENTRY_SUFFIX}"
POISON_BYTES = b'{"key": "half a file'


# --------------------------------------------------------------------------
# Driving the queue's own transitions
# --------------------------------------------------------------------------


def lands():
    """A transport that lands whatever it is handed."""
    return queue_tests.FakeTransport(answer=queue_tests.filing())


def refuses():
    """A transport that refuses on the provider's own terms, which is terminal."""
    return queue_tests.FakeTransport(answer=queue_tests.terminal())


def names_in(directory: Path) -> list[str]:
    """Every file name a directory holds, partials included.

    Bare `iterdir` rather than `outbox.entry_files`, because "the queue does
    not hold it" is a claim about the directory and not only about the files a
    sweep would read.
    """
    return sorted(path.name for path in directory.iterdir()) \
        if directory.is_dir() else []


def entry_name(key: str) -> str:
    """The file name a key is written under, in whichever directory holds it."""
    return outbox.entry_path(Path("."), key).name


def landed_receipt(target: Path, identity: dict = IDENTITY) -> str:
    """One entry driven all the way to landed, and the key it landed under.

    Enqueued and then swept through a transport that answers with a reference,
    so the receipt this returns is one the module under test wrote rather than
    one this module composed.
    """
    queue = outbox.queue_dir(target)
    key = outbox.enqueue(queue, PAYLOAD, identity)
    assert key, "the fixture's own enqueue lost the entry it meant to land"
    summary = outbox.sync(queue, lands())
    assert summary.landed_keys == (key,), summary
    return key


def landed_in_the_queue(target: Path, identity: dict = IDENTITY) -> str:
    """A landed entry left in the queue, as this deployment holds them today.

    Built by running the module's own move backwards — `relocate_entry` from
    the index into the queue — so what sits in the queue is byte for byte the
    receipt landing produced, in the place landing used to leave it. Only
    *where* it lives is composed here.
    """
    key = landed_receipt(target, identity)
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)
    entry, problems = outbox.read_entry(outbox.entry_path(receipts, key))
    assert entry is not None, problems
    outbox.relocate_entry(receipts, queue, entry)
    assert outbox.entry_path(queue, key).is_file()
    assert not outbox.entry_path(receipts, key).is_file()
    return key


def planted_in_the_old_location(target: Path, finding: dict) -> str:
    """The producer module's landed entry, moved back into the queue.

    The Inspector's fixtures plant a landed entry where the split now keeps
    one; this puts that same entry where a pre-split deployment holds it, by
    the same move.
    """
    key = producer.planted(target, finding, LANDED)
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)
    entry, problems = outbox.read_entry(outbox.entry_path(receipts, key))
    assert entry is not None, problems
    outbox.relocate_entry(receipts, queue, entry)
    return key


def recording_reads(monkeypatch) -> list[Path]:
    """Every file `outbox.read_entry` is asked to open, in call order.

    The list is live: a caller installs it, does the work, and reads what was
    opened. What it wraps is the module's own single reader, which is the one
    seam both directories are opened through, so a read that avoided it would
    not be a read of an entry at all.
    """
    opened: list[Path] = []
    real = outbox.read_entry

    def recording(path, harness_root=None):
        opened.append(Path(path))
        return real(path, harness_root)

    monkeypatch.setattr(outbox, "read_entry", recording)
    return opened


def under(directory: Path, opened: list[Path]) -> list[Path]:
    return [path for path in opened if path.parent == directory]


# ==========================================================================
# Which of the two directories each state lives in
# ==========================================================================


def test_a_landed_entry_is_in_the_index_and_not_in_the_queue(tmp_path):
    """The split, at its narrowest.

    The control is the second entry: a pending one, under a different identity,
    which the same listing of the same queue reports — so an empty queue above
    is the relocation rather than a listing pointed at a directory that holds
    nothing.
    """
    target = tmp_path / "a-target"
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)

    key = landed_receipt(target)

    assert names_in(receipts) == [entry_name(key)]
    assert names_in(queue) == []

    still_to_file = outbox.enqueue(queue, PAYLOAD, OTHER_IDENTITY)
    assert names_in(queue) == [entry_name(still_to_file)]
    assert names_in(receipts) == [entry_name(key)]


def test_a_pending_and_a_failed_entry_are_in_the_queue_and_not_in_the_index(
        tmp_path):
    """The other half, driven in one sweep so the two states are told apart by
    what the transport answered and by nothing else.

    The control is the second target: one landed entry through the same
    transitions, whose receipt the same listing of the same-named directory
    reports — so an empty index above is these two states staying in the queue.
    """
    target = tmp_path / "still-to-file"
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)
    pending = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    failing = outbox.enqueue(queue, PAYLOAD, OTHER_IDENTITY)

    summary = outbox.sync(queue, queue_tests.FakeTransport(
        answer=lambda entry: queue_tests.terminal()
        if entry["key"] == failing else queue_tests.transient()))

    assert summary.pending_keys == (pending,)
    assert summary.failed_keys == (failing,)
    assert names_in(queue) == sorted([entry_name(pending), entry_name(failing)])
    assert names_in(receipts) == []

    landed_elsewhere = tmp_path / "already-filed"
    key = landed_receipt(landed_elsewhere)
    assert names_in(outbox.receipts_dir(landed_elsewhere)) == [entry_name(key)]


def test_the_two_directories_answer_one_rule_for_both_readers(tmp_path):
    """`local_index` and `local_state` agree about every state, which is what
    "one derivation" means where a reader can observe it.

    Each of the three is driven to its own target, because two of them are
    states of one key and a single target could only hold one at a time.
    """
    landed_target = tmp_path / "answers-landed"
    landed_key = landed_receipt(landed_target)
    assert outbox.local_state(landed_target, landed_key) == LANDED
    landed_index = outbox.local_index(landed_target)
    assert landed_index.landed == frozenset({landed_key})
    assert landed_index.queued == frozenset()

    pending_target = tmp_path / "answers-pending"
    pending_key = outbox.enqueue(outbox.queue_dir(pending_target), PAYLOAD,
                                 IDENTITY)
    assert outbox.local_state(pending_target, pending_key) == PENDING
    pending_index = outbox.local_index(pending_target)
    assert pending_index.queued == frozenset({pending_key})
    assert pending_index.landed == frozenset()

    failed_target = tmp_path / "answers-failed"
    failed_key = outbox.enqueue(outbox.queue_dir(failed_target), PAYLOAD,
                                IDENTITY)
    outbox.sync(outbox.queue_dir(failed_target), refuses())
    assert outbox.local_state(failed_target, failed_key) == FAILED
    failed_index = outbox.local_index(failed_target)
    # A failed entry is terminal and contributes to neither set, which is what
    # makes the finding it carries get another chance rather than be lost.
    assert failed_index.landed == frozenset()
    assert failed_index.queued == frozenset()

    # And a key neither directory holds is the empty string rather than a state.
    assert outbox.local_state(landed_target, "zzz-a-key-nothing-holds") == ""


# ==========================================================================
# The three states still decide suppression, through the two readers
# ==========================================================================


def test_a_finding_matching_a_landed_receipt_is_dropped_by_the_local_tier(
        tmp_path):
    """Tier one, answering from the index with no filed-query command at all.

    That configuration is asserted rather than assumed, because it is the
    point: this repository configures no query, so the drop can only have come
    from the receipt. The finding beside the match is filed, so the drop is
    about the match rather than about an inspection that stopped filing.
    """
    config = producer.configuration()
    assert filed_query.COMMAND_KEY not in config

    target = producer.target_repository(tmp_path)
    known = producer.brief(slug="the-one-this-harness-already-filed")
    fresh = producer.brief(slug="the-one-nobody-has-filed")
    key = producer.planted(target, known, LANDED)
    assert outbox.entry_path(outbox.receipts_dir(target), key).is_file()
    assert not outbox.entry_path(outbox.queue_dir(target), key).is_file()

    found = producer.inspecting(tmp_path, target=target, config=config,
                                act=producer.writes(known, fresh))

    assert known["slug"] in found.detail(ALREADY_FILED_LOCALLY)
    assert found.dropped(ALREADY_FILED) == ()
    assert found.report.local_index.landed == frozenset({key})
    assert found.report.dedupe_ran is False
    assert found.filed_slugs == [fresh["slug"]]


def test_a_pending_queue_entry_is_reported_as_queued_and_a_failed_one_files(
        tmp_path):
    """The other two states, side by side with the landed one.

    Three targets differing only in the state their entry is in, so what
    separates the three answers can only be that state. The pending drop and
    the landed drop are required to differ, which is what "distinctly from one
    matching a landed receipt" asks for.
    """
    finding = producer.brief(slug="the-one-in-every-state")

    queued_target = producer.target_repository(tmp_path, name="holds-it-pending")
    queued_key = producer.planted(queued_target, finding, PENDING)
    landed_target = producer.target_repository(tmp_path, name="holds-it-landed")
    producer.planted(landed_target, finding, LANDED)
    failed_target = producer.target_repository(tmp_path, name="holds-it-failed")
    failed_key = producer.planted(failed_target, finding, FAILED)

    queued = producer.inspecting(tmp_path, target=queued_target,
                                 act=producer.writes(finding))
    landed = producer.inspecting(tmp_path, target=landed_target,
                                 act=producer.writes(finding))
    failed = producer.inspecting(tmp_path, target=failed_target,
                                 act=producer.writes(finding))

    assert len(queued.dropped(ALREADY_QUEUED)) == 1
    assert queued.dropped(ALREADY_FILED_LOCALLY) == ()
    assert queued.report.local_index.queued == frozenset({queued_key})

    assert len(landed.dropped(ALREADY_FILED_LOCALLY)) == 1
    assert landed.dropped(ALREADY_QUEUED) == ()

    assert queued.dropped(ALREADY_QUEUED)[0].reason != \
        landed.dropped(ALREADY_FILED_LOCALLY)[0].reason

    # A failed entry suppresses nothing: the finding is filed again and
    # replaces it at the same key with a pending one.
    assert failed.report.dropped == ()
    assert failed.filed_slugs == [finding["slug"]]
    assert [entry["key"] for entry in failed.entries] == [failed_key]
    assert failed.entries[0]["state"] == PENDING


def test_file_brief_answers_landed_from_the_index_and_the_rest_from_the_queue(
        tmp_path):
    """`brief_filing`'s three outcomes, each driven to its own target.

    Every state is reached by driving the queue's own transitions rather than
    by writing an entry this module composed, so the outcome is decided by what
    the module put where.
    """
    harness = producer.harness_mirror(tmp_path)
    brief = producer.brief()

    filed = producer.target_repository(tmp_path, name="filed-target")
    first = brief_filing.file_brief(brief, {}, filed, harness)
    assert first.outcome == FILED_HERE
    outbox.sync(outbox.queue_dir(filed), lands())
    assert outbox.entry_path(outbox.receipts_dir(filed), first.key).is_file()
    assert not outbox.entry_path(outbox.queue_dir(filed), first.key).is_file()
    assert brief_filing.file_brief(brief, {}, filed, harness).outcome == \
        FILED_LOCALLY_OUTCOME

    queued = producer.target_repository(tmp_path, name="queued-target")
    brief_filing.file_brief(brief, {}, queued, harness)
    assert brief_filing.file_brief(brief, {}, queued, harness).outcome == \
        QUEUED_OUTCOME

    refused = producer.target_repository(tmp_path, name="refused-target")
    brief_filing.file_brief(brief, {}, refused, harness)
    outbox.sync(outbox.queue_dir(refused), refuses())
    again = brief_filing.file_brief(brief, {}, refused, harness)
    assert again.outcome == FILED_HERE
    assert again.key == first.key
    assert [entry["state"] for entry in producer_entries(refused)] == [PENDING]


def producer_entries(target: Path) -> list[dict]:
    """Every entry the target's queue holds, read as the sweep reads them."""
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in outbox.entry_files(outbox.queue_dir(target))]


# ==========================================================================
# A sweep opens only what it might file
# ==========================================================================


def a_deployment_holding_receipts(tmp_path: Path, name: str) -> tuple:
    """Many receipts and one pending entry, which is the steady state.

    The receipts are landed through the module's own transitions before
    anything is recorded, so what a later sweep opens is uncontaminated by what
    producing them opened.
    """
    target = tmp_path / name
    for ordinal in range(RECEIPTS_HELD):
        landed_receipt(target, {"kind": "zzz-receipt", "n": ordinal})
    queue = outbox.queue_dir(target)
    pending = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    assert len(names_in(outbox.receipts_dir(target))) == RECEIPTS_HELD
    return target, pending


def test_a_sweep_of_a_deployment_holding_many_receipts_opens_none_of_them(
        tmp_path, monkeypatch):
    """The read cost this story exists to remove, observed at the reader.

    Two controls, both against the same recorder: the queue files it *did*
    catch in the same call, so the recorder is live, and the local index driven
    through the same recorder afterwards, which does open every receipt — so an
    empty list here is the sweep listing one directory rather than a recorder
    that cannot see the other.
    """
    target, pending = a_deployment_holding_receipts(tmp_path, "swept")
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)

    opened = recording_reads(monkeypatch)
    summary = outbox.sync(queue, lands())

    assert summary.landed_keys == (pending,)
    assert under(receipts, opened) == []
    assert under(queue, opened) != []

    of_the_sweep = list(opened)
    outbox.local_index(target)
    assert under(receipts, opened[len(of_the_sweep):]) != []


def test_receipts_the_sweep_never_opened_are_not_reported_as_poisoned(
        tmp_path):
    """The same absence, observed a second way and without a recorder.

    Every receipt is overwritten with bytes no reader can parse. A sweep that
    opened them would count every one of them poisoned; it reports none. The
    control is the same bytes under the same name in the queue, where the same
    sweep does report them.
    """
    target, pending = a_deployment_holding_receipts(tmp_path, "poisoned-index")
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)
    for path in receipts.iterdir():
        path.write_bytes(POISON_BYTES)

    summary = outbox.sync(queue, lands())

    assert summary.poisoned == 0, summary.poisoned_files
    assert summary.landed_keys == (pending,)

    control = tmp_path / "poisoned-queue"
    control_queue = outbox.queue_dir(control)
    control_queue.mkdir(parents=True)
    for ordinal in range(RECEIPTS_HELD):
        (control_queue / f"{ordinal}-{POISON_NAME}").write_bytes(POISON_BYTES)

    assert outbox.sync(control_queue, lands()).poisoned == RECEIPTS_HELD


# ==========================================================================
# The migration: a landed entry the sweep still finds in the queue
# ==========================================================================


def test_a_landed_entry_the_sweep_meets_in_the_queue_is_relocated_and_counted(
        tmp_path):
    """The whole of the migration, and both halves of what it must not change.

    Before the sweep the index does not answer for the entry, which is what
    makes the relocation observable rather than a move between two places that
    already agreed. After it the entry is in the index, gone from the queue,
    and counted as landed in the summary that sweep returned — the same tally
    it produced before the split.
    """
    target = tmp_path / "a-pre-split-deployment"
    queue, receipts = outbox.queue_dir(target), outbox.receipts_dir(target)
    key = landed_in_the_queue(target)

    assert outbox.local_index(target).landed == frozenset()
    assert outbox.local_state(target, key) == LANDED, \
        "the single-key read answers from the queue as well as the index"

    summary = outbox.sync(queue, lands())

    assert summary.landed_keys == (key,)
    assert summary.landed == 1
    assert names_in(receipts) == [entry_name(key)]
    assert names_in(queue) == []
    assert outbox.local_index(target).landed == frozenset({key})


def test_a_receipt_seeded_in_the_old_location_still_suppresses_after_a_sweep(
        tmp_path):
    """The deployment's own receipts, which is what "cannot silently lose
    dedupe" is about.

    The finding beside the match is filed by the same inspection, so the drop
    is about the relocated receipt rather than about an inspection that stopped
    filing.
    """
    target = producer.target_repository(tmp_path, name="holds-a-pre-split-entry")
    known = producer.brief(slug="the-one-filed-before-the-split")
    fresh = producer.brief(slug="the-one-nobody-has-filed")
    key = planted_in_the_old_location(target, known)

    summary = outbox.sync(outbox.queue_dir(target), lands())
    assert summary.landed_keys == (key,)

    found = producer.inspecting(tmp_path, target=target,
                                act=producer.writes(known, fresh))

    assert known["slug"] in found.detail(ALREADY_FILED_LOCALLY)
    assert found.report.local_index.landed == frozenset({key})
    assert found.filed_slugs == [fresh["slug"]]


def test_a_relocation_that_cannot_be_made_costs_the_migration_and_nothing_else(
        tmp_path):
    """The standing guarantee, at the one place the split added a write.

    The index is replaced by a file, so the move fails. The entry is still
    tallied as landed, the sweep still returns a summary, the entry is still
    there to be relocated by a later sweep, and nothing is raised into the
    caller.
    """
    target = tmp_path / "an-unwritable-index"
    queue = outbox.queue_dir(target)
    key = landed_in_the_queue(target)

    receipts = outbox.receipts_dir(target)
    receipts.rmdir()
    receipts.write_text("a file where the index would be\n", encoding="utf-8")
    # The control for the assertions below: the index really does refuse a
    # write, so a note about a move that failed is the guard working rather
    # than a path that quietly succeeded.
    with pytest.raises(OSError):
        outbox.write_entry(receipts, {"key": key, "state": LANDED})

    summary = outbox.sync(queue, lands())

    assert summary.landed_keys == (key,)
    assert not summary.blocked
    assert outbox.entry_path(queue, key).is_file(), \
        "the entry that could not be moved was lost"
    assert any(key in note for note in summary.notes), summary.notes


# ==========================================================================
# An index that cannot be listed, and a poisoned file in either directory
# ==========================================================================


@pytest.fixture
def unlistable_index_target(tmp_path: Path) -> Path:
    """A target whose receipt index can be written to and cannot be listed.

    Written to, deliberately: an index that could not be written either would
    make "the inspection still files what it found" unobservable, and the claim
    is that losing tier one costs tier one and nothing else.
    """
    target = producer.target_repository(tmp_path, name="an-unlistable-index")
    receipts = outbox.receipts_dir(target)
    receipts.mkdir(parents=True, exist_ok=True)
    receipts.chmod(0o300)
    yield target
    receipts.chmod(0o700)


def refuses_to_be_listed(directory: Path) -> None:
    """Skip unless the directory really cannot be listed.

    A developer running the suite as root can read a directory with no read
    bit, which would make the assertions resting on it vacuous rather than
    false — so that case is skipped by name rather than passed silently.
    """
    try:
        list(directory.iterdir())
    except OSError:
        return
    pytest.skip("this process can list a directory with no read permission")


def test_the_unlistable_index_really_cannot_be_listed(unlistable_index_target):
    """The control for the assertion below: the directory refuses to be listed,
    so a report saying the index could not be read is the guard working rather
    than a path that quietly succeeded."""
    receipts = outbox.receipts_dir(unlistable_index_target)
    refuses_to_be_listed(receipts)
    with pytest.raises(OSError):
        outbox.entry_files(receipts)


def test_an_index_that_cannot_be_listed_costs_the_local_tier_and_nothing_else(
        unlistable_index_target, tmp_path):
    """The inspection runs, invokes, files what it found, and says what it lost.

    The reason names the index rather than the queue, which is the half a
    single shared message would have lost: a developer told only that "a
    directory could not be listed" has two directories to look in.
    """
    receipts = outbox.receipts_dir(unlistable_index_target)
    refuses_to_be_listed(receipts)

    found = producer.inspecting(tmp_path, target=unlistable_index_target,
                                act=producer.writes(producer.brief()))

    index = found.report.local_index
    assert index.read is False
    assert str(receipts) in index.reason
    assert index.landed == frozenset()
    assert len(found.invocations) == 1
    assert found.filed_slugs == [producer.brief()["slug"]]
    assert found.report.dropped == ()
    assert index.reason in producer.report_text(found)

    receipts.chmod(0o700)
    assert [entry["payload"]["slug"] for entry in producer_entries(
        unlistable_index_target) if "payload" in entry] == \
        [producer.brief()["slug"]]


@pytest.mark.parametrize("directory_of", (outbox.queue_dir, outbox.receipts_dir),
                         ids=lambda accessor: accessor.__name__)
def test_a_poisoned_file_in_either_directory_contributes_no_key(
        tmp_path, directory_of):
    """Counted, left byte for byte as it is, and contributing nothing.

    The control is in the same test: the same two directories with the poison
    removed report the same two keys and a count of zero, so "it contributed no
    key" is a fact about the file rather than about a read that found nothing.
    """
    target = tmp_path / f"poisoned-{directory_of.__name__}"
    receipt = landed_receipt(target)
    pending = outbox.enqueue(outbox.queue_dir(target), PAYLOAD, OTHER_IDENTITY)

    poison = directory_of(target) / POISON_NAME
    poison.write_bytes(POISON_BYTES)

    index = outbox.local_index(target)

    assert index.read is True
    assert index.unreadable == 1
    assert index.landed == frozenset({receipt})
    assert index.queued == frozenset({pending})
    assert poison.read_bytes() == POISON_BYTES
    assert poison.is_file()

    poison.unlink()
    without = outbox.local_index(target)
    assert without.unreadable == 0
    assert (without.landed, without.queued) == (index.landed, index.queued)


# ==========================================================================
# l5-status names the index and how many receipts it holds
# ==========================================================================


def status_target(tmp_path: Path, name: str = "status-target") -> Path:
    """A target l5-status can resolve, with one completed run to list."""
    root = tmp_path / name
    run_dir = root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text("runs_dir: .harness/runs\n",
                                                   encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps({"story_id": "story-001", "branch": "story/story-001",
                    "status": "completed", "current_stage": "",
                    "retry_count": 0, "verification_iterations": 0,
                    "artifacts": []}) + "\n", encoding="utf-8")
    return root


def lines_only_in(listing: str, other: str) -> list[str]:
    return [line for line in listing.splitlines()
            if line not in other.splitlines()]


def test_the_listing_names_the_receipt_index_and_how_many_receipts_it_holds(
        tmp_path):
    """Both halves, and the count asserted as a difference rather than against
    a wording read off the module.

    The same target is listed before and after its receipts are landed, so the
    only line that may differ is the one carrying the count — and a listing
    printing a constant, or printing the queue's number a second time, cannot
    pass that.
    """
    target = status_target(tmp_path)
    receipts = outbox.receipts_dir(target)

    empty = run_status.format_listing(target)
    assert str(receipts) in empty, empty

    for ordinal in range(RECEIPTS_HELD):
        landed_receipt(target, {"kind": "zzz-receipt", "n": ordinal})
    held = run_status.format_listing(target)

    differing = lines_only_in(held, empty)
    assert len(differing) == 1, differing
    assert str(RECEIPTS_HELD) in differing[0], differing
    assert str(receipts) in held, held
    # And the run the developer actually asked for is still listed.
    assert "story-001" in held, held


def test_the_listing_names_a_poisoned_receipt_and_files_nothing(tmp_path):
    """A poisoned receipt is named rather than left to be inferred from a count
    that does not add up, and the listing leaves both directories alone."""
    target = status_target(tmp_path, name="a-poisoned-receipt")
    landed_receipt(target)
    poison = outbox.receipts_dir(target) / POISON_NAME
    poison.write_bytes(POISON_BYTES)
    before = {path: path.read_bytes()
              for path in outbox.receipts_dir(target).iterdir()}

    listing = run_status.format_listing(target)

    assert POISON_NAME in listing, listing
    assert {path: path.read_bytes()
            for path in outbox.receipts_dir(target).iterdir()} == before


def test_the_shipped_status_command_names_the_index(tmp_path):
    """The entry point rather than the module beneath it, run as a subprocess.

    What the count and the naming *are* is pinned in the test above; what this
    one adds is that the shipped command reaches them at all, which is asserted
    by requiring the listing the module produced to be what the command
    printed.
    """
    target = status_target(tmp_path, name="a-status-cli-target")
    for ordinal in range(RECEIPTS_HELD):
        landed_receipt(target, {"kind": "zzz-receipt", "n": ordinal})

    result = subprocess.run([sys.executable, str(SCRIPTS / "l5-status")],
                            cwd=target, capture_output=True, text=True,
                            timeout=60)

    assert result.returncode == 0, result.stderr
    assert str(outbox.receipts_dir(target)) in result.stdout, result.stdout
    assert run_status.format_listing(target) in result.stdout, result.stdout


# ==========================================================================
# The harness configuration gains no key, and the index is ignored
# ==========================================================================


HARNESS_CONFIG_SCHEMA = "schemas/harness-config.schema.json"
CONFIG_TEMPLATE = "templates/config.yaml"

#: A path this story did rewrite, so the emptiness asserted over the two above
#: sits beside a diff of the same shape that is not empty.
REWRITTEN_BY_THIS_STORY = "orchestration/outbox.py"


def test_this_story_left_the_configured_shape_and_the_template_alone():
    """No new key, decided by the story's own commit range rather than against
    HEAD or the working tree — which would go vacuously green the moment the
    story commits."""
    assert conftest.story_diff([HARNESS_CONFIG_SCHEMA, CONFIG_TEMPLATE],
                               validation_file=Path(__file__)) == ""
    assert conftest.story_diff([REWRITTEN_BY_THIS_STORY],
                               validation_file=Path(__file__)) != ""


def test_the_harness_configuration_declares_the_keys_it_declared_before():
    """The same claim through what the shape says rather than through a diff.

    The second assertion is the control the first needs: two empty sets would
    compare equal, so the shape is required to declare something.
    """
    today = json.loads(
        (REPO_ROOT / HARNESS_CONFIG_SCHEMA).read_text(encoding="utf-8"))
    before = json.loads(conftest.repository_file_at(
        HARNESS_CONFIG_SCHEMA, validation_file=Path(__file__),
        bound=conftest.BASELINE))

    assert set(today["properties"]) == set(before["properties"])
    assert set(today["properties"]) != set()


def test_the_index_location_is_a_constant_rather_than_something_configured(
        tmp_path):
    """Where the index lives is derived from the module's constant and from the
    target root alone, so no configuration is consulted to find it."""
    assert outbox.receipts_dir(tmp_path) == tmp_path.joinpath(*outbox.RECEIPTS_DIR)
    # The two constants are siblings, which is what lets a caller holding only a
    # queue derive the index that belongs to it.
    assert outbox.receipts_beside(outbox.queue_dir(tmp_path)) == \
        outbox.receipts_dir(tmp_path)
    assert outbox.RECEIPTS_DIR != outbox.QUEUE_DIR


def check_ignore(probe: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-v", probe],
        capture_output=True, text=True)


def test_this_repository_ignores_its_own_receipt_index():
    """A shipped artifact, so the subject is this repository's own .gitignore,
    asked through git rather than by reading the file for a line.

    The control is a path the repository tracks, which the same question does
    not report — so a zero status above is about the index rather than about a
    command that answers yes to everything.
    """
    ignored = check_ignore(f"{RECEIPTS_REL}/a-receipt{outbox.ENTRY_SUFFIX}")
    assert ignored.returncode == 0, ignored.stderr
    assert ".gitignore" in ignored.stdout

    tracked = check_ignore(str(Path(outbox.__file__).relative_to(REPO_ROOT)))
    assert tracked.returncode != 0, tracked.stdout


# ==========================================================================
# One derivation, in the one module where the two directories are defined
# ==========================================================================


#: What a module would have to name to reach one of the two directories for
#: itself: the three reads, and the accessor for the directory the split added.
#: `queue_dir` is deliberately not among them — both readers still enqueue, and
#: naming where the queue *is* to write into it is not reading it.
READS_A_DIRECTORY = ("entry_files", "entry_path", "read_entry", "receipts_dir")

#: The two modules the derivation moved out of, which must now reach the answer
#: through the outbox rather than through the directories.
THE_TWO_READERS = (inspection, brief_filing)


@pytest.mark.parametrize("module", THE_TWO_READERS,
                         ids=lambda module: module.__name__)
def test_neither_reader_reaches_the_two_directories_on_its_own(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    reached = producer.outbox_attributes(source) & set(READS_A_DIRECTORY)
    assert reached == set(), (module.__name__, sorted(reached))


@pytest.mark.parametrize("operation", READS_A_DIRECTORY)
def test_the_same_scan_reports_a_source_that_reaches_a_directory(operation):
    """The control for each absence above, one operation at a time: a fix can
    stop naming three of them while still naming the fourth."""
    planted = ("import outbox\n\n\n"
               "def reach(directory, key):\n"
               f"    return outbox.{operation}(directory, key)\n")
    assert producer.outbox_attributes(planted) == {operation}


def test_the_moved_derivations_live_in_the_outbox_and_both_readers_use_them():
    """Where a function lives, asked of the function rather than of a source.

    The control is the pair beneath: a function each reader does define of its
    own answers with that reader's own name, so the three above are facts about
    what moved rather than about a question that always answers "outbox".
    """
    assert inspection.local_index.__module__ == outbox.__name__
    assert inspection.LocalIndex.__module__ == outbox.__name__
    assert brief_filing.local_state.__module__ == outbox.__name__

    assert inspection.local_index is outbox.local_index
    assert brief_filing.local_state is outbox.local_state

    assert inspection.inspect_scope.__module__ == inspection.__name__
    assert brief_filing.file_brief.__module__ == brief_filing.__name__
