"""Independent validation for the third set: what the local queue holds
failed, and what an inspection says about a brief it filed over one.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the third set.** Driven through the queue's own transitions against a
    transport that refuses on the provider's own terms, so what makes an entry
    terminal is the module's decision and only which set its key lands in is
    asserted here. A transport that *raises* is a transient failure and would
    prove nothing about a terminal one, so none is used.

  * **that the set decides nothing.** Not asserted about the sets at all, but
    driven through the Inspector: a finding whose key the queue holds failed is
    still filed, is dropped for none of the three reasons, and is counted among
    what the inspection filed.

  * **what a developer is told.** Read through `scripts/l5-inspect` itself, so
    the wording is read where it lives rather than restated here. The count on
    the local-queue line and the note beneath a brief are located by deriving
    them from the report and from `outbox.FAILED`, not by matching prose.

Every absence asserted here carries a demonstration that it can fail:

  * "adding a failed entry changed neither other set" sits beside the same read
    of the same queue reporting the new key in the third set, so an unchanged
    pair is about those two sets rather than about a read that stopped seeing;
  * "the queue was listed once" sits beside the same recorder catching the
    receipt index being listed in the same call;
  * "a poisoned file contributes no key" sits beside the same three-state queue
    without it, where the same read reports the same three keys and no
    unreadable file;
  * "an unread index claims nothing about failed identities" sits beside the
    line the same script prints for an index that *was* read, which does carry
    the count;
  * "this brief carries no refile note" sits beside the brief in the same
    printed report that does carry one;
  * "no filed brief is marked" sits beside the inspection over the same finding
    whose entry is failed instead, where one is;
  * "no inspection here runs without a fake runner" sits beside a planted
    source the same scan reports.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import inspection
import outbox
import worktrees

import test_inspection as producer
import test_outbox as queue_tests
import test_the_queue_holds_work_and_the_index_holds_receipts as split

#: The three states, read off the queue's own module so no state name is
#: spelled here beside the definition that decides them.
PENDING = outbox.PENDING
LANDED = outbox.LANDED
FAILED = outbox.FAILED

#: The Inspector's three already-filed reasons, read off the module that owns
#: them: a finding whose key is held failed must be dropped for none of them.
SUPPRESSION_REASONS = (
    inspection.ALREADY_FILED,
    inspection.ALREADY_FILED_LOCALLY,
    inspection.ALREADY_QUEUED,
)

#: Three identities, one per state, so a single queue can hold all three at
#: once and the three sets have something to tell apart.
LANDED_IDENTITY = {"kind": "zzz-refile", "subject": "the-one-that-landed"}
PENDING_IDENTITY = {"kind": "zzz-refile", "subject": "the-one-still-waiting"}
FAILED_IDENTITY = {"kind": "zzz-refile", "subject": "the-one-refused"}

PAYLOAD = {"title": "something to file", "body": "what it says"}

POISON_NAME = f"zzz-not-an-entry{outbox.ENTRY_SUFFIX}"
POISON_BYTES = b'{"key": "half a file'

#: How a filed brief and the lines belonging to it are told apart in what the
#: script prints: a brief is one line, and everything indented further beneath
#: it until the next brief is that brief's. The two are read as shape rather
#: than as wording, so a note's phrasing is the script's to change.
BRIEF_LINE_PREFIX = "  severity "
NOTE_LINE_PREFIX = "      "


# --------------------------------------------------------------------------
# Driving an entry to the terminal failed state
# --------------------------------------------------------------------------


def refused_entry(target: Path, identity: dict, payload: dict = PAYLOAD) -> str:
    """One entry driven to failed by a transport that refused it, and its key.

    Refused rather than raised: a transport that raises has established nothing
    and leaves the entry pending, so it would prove nothing about the terminal
    state this story is about. The state is read back before the key is
    returned, which is the control every assertion resting on this fixture
    needs — an entry that was left pending would make "the failed set holds it"
    fail loudly rather than quietly hold nothing.
    """
    queue = outbox.queue_dir(target)
    key = outbox.enqueue(queue, payload, identity)
    assert key, "the fixture's own enqueue lost the entry it meant to refuse"
    summary = outbox.sync(queue, split.refuses())
    # A sweep meets every entry the queue holds, so this asks about the one it
    # was called for rather than about the whole tally: a second call plants a
    # second failed entry beside the first, which the same sweep reports again.
    assert key in summary.failed_keys, summary
    assert outbox.local_state(target, key) == FAILED
    return key


def refused_for(target: Path, finding: dict) -> str:
    """The same, under the key the Inspector derives for a finding.

    The identity comes from the module that owns it, so the entry this plants
    and the entry an inspection would write are one key rather than two
    spellings of one.
    """
    return refused_entry(target, inspection.identity(finding),
                         {"slug": finding["slug"]})


def three_state_queue(target: Path) -> tuple[str, str, str]:
    """One landed, one pending and one failed entry, all at once.

    Each is driven by the module's own transitions, and in an order that lets
    each sweep meet only the entry it is meant to settle: the landed one is
    relocated into the receipt index before the failed one is enqueued, and the
    pending one is never offered to a transport at all.
    """
    landed = split.landed_receipt(target, LANDED_IDENTITY)
    failed = refused_entry(target, FAILED_IDENTITY)
    pending = outbox.enqueue(outbox.queue_dir(target), PAYLOAD,
                             PENDING_IDENTITY)
    assert pending, "the fixture's own enqueue lost the entry it meant to queue"
    assert len({landed, pending, failed}) == 3, \
        "the three states were planted under fewer than three keys"
    return landed, pending, failed


def recording_listings(monkeypatch) -> list[Path]:
    """Every directory `outbox.entry_files` is asked to list, in call order.

    The list is live: a caller installs it, does the work, and reads what was
    listed. What it wraps is the module's own single listing, which is the one
    seam both directories are read through.
    """
    listed: list[Path] = []
    real = outbox.entry_files

    def recording(directory):
        listed.append(Path(directory))
        return real(directory)

    monkeypatch.setattr(outbox, "entry_files", recording)
    return listed


# ==========================================================================
# The third set, and the two it must not disturb
# ==========================================================================


def test_a_queue_holding_all_three_states_tells_the_three_sets_apart(tmp_path):
    """The narrowest form of the claim: three entries, three states, three sets.

    One queue rather than three targets, so the three answers are separated by
    the state each entry is in and by nothing about where it was read from.
    """
    target = tmp_path / "holds-all-three"
    landed, pending, failed = three_state_queue(target)

    index = outbox.local_index(target)

    assert index.read is True
    assert index.landed == frozenset({landed})
    assert index.queued == frozenset({pending})
    assert index.failed == frozenset({failed})
    assert index.unreadable == 0


def test_adding_a_failed_entry_changes_neither_the_landed_nor_the_queued_set(
        tmp_path):
    """The addition is confined to the set it was added to.

    One queue read twice, so what separates the two answers is the entry that
    arrived between them. The sweep that plants it answers terminally for that
    entry alone and defers the pending one, which is what leaves the queue
    holding both states at once — the two answers are the transport's, and only
    which set each key lands in is asserted here.

    The control is in the same test: the same read of the same queue does
    report the new key in the third set, so two sets that did not move is a
    fact about the addition rather than about a read that has stopped seeing
    anything.
    """
    target = tmp_path / "gains-a-failed-entry"
    queue = outbox.queue_dir(target)
    landed = split.landed_receipt(target, LANDED_IDENTITY)
    pending = outbox.enqueue(queue, PAYLOAD, PENDING_IDENTITY)

    before = outbox.local_index(target)
    assert before.landed == frozenset({landed})
    assert before.queued == frozenset({pending})
    assert before.failed == frozenset()

    failed = outbox.enqueue(queue, PAYLOAD, FAILED_IDENTITY)
    summary = outbox.sync(queue, queue_tests.FakeTransport(
        answer=lambda entry: queue_tests.terminal() if entry["key"] == failed
        else queue_tests.transient()))
    assert summary.failed_keys == (failed,), summary
    assert summary.pending_keys == (pending,), summary

    after = outbox.local_index(target)

    assert after.landed == before.landed
    assert after.queued == before.queued
    assert after.failed == frozenset({failed})


def test_the_queue_is_listed_once_for_both_the_pending_and_the_failed_keys(
        tmp_path, monkeypatch):
    """The failed keys come from the listing the pending keys already came from.

    The control is the receipt index in the same recorded call: the recorder
    catches it being listed, so one listing of the queue is a fact about the
    read rather than about a recorder that saw nothing.
    """
    target = tmp_path / "listed-once"
    three_state_queue(target)
    listed = recording_listings(monkeypatch)

    index = outbox.local_index(target)

    assert index.failed and index.queued
    assert listed.count(outbox.queue_dir(target)) == 1, listed
    assert listed.count(outbox.receipts_dir(target)) == 1, listed


@pytest.mark.parametrize("directory_of",
                         (outbox.queue_dir, outbox.receipts_dir),
                         ids=lambda accessor: accessor.__name__)
def test_a_poisoned_file_contributes_no_key_to_any_of_the_three_sets(
        tmp_path, directory_of):
    """Counted once, left byte for byte as it is, and stopping nothing.

    Counted *once* is the half the third set could have broken: a queue listed
    a second time for its failed keys would meet the same poisoned file again
    and report two unreadable files where there is one.

    The control is in the same test: the same two directories with the poison
    removed report the same three keys and a count of zero.
    """
    target = tmp_path / f"poisoned-{directory_of.__name__}"
    landed, pending, failed = three_state_queue(target)

    poison = directory_of(target) / POISON_NAME
    poison.write_bytes(POISON_BYTES)

    index = outbox.local_index(target)

    assert index.read is True
    assert index.unreadable == 1
    assert index.landed == frozenset({landed})
    assert index.queued == frozenset({pending})
    assert index.failed == frozenset({failed})
    assert poison.is_file()
    assert poison.read_bytes() == POISON_BYTES

    poison.unlink()
    without = outbox.local_index(target)
    assert without.unreadable == 0
    assert (without.landed, without.queued, without.failed) == \
        (index.landed, index.queued, index.failed)


# ==========================================================================
# The set decides nothing: the Inspector still files what it holds failed
# ==========================================================================


def marked_slugs(found) -> list[str]:
    """The slugs of the briefs this inspection reported as refiled."""
    return [one.slug for one in found.report.filed if one.refiled_over_failure]


def test_a_finding_the_queue_holds_failed_is_filed_counted_and_marked(tmp_path):
    """Nothing in the drop decisions reads the third set.

    The finding is filed, dropped for none of the three already-filed reasons,
    counted among what the inspection filed, and marked. The finding beside it
    is filed by the same inspection and is not marked, so the mark is about the
    entry rather than about an inspection that marks whatever it files.
    """
    target = producer.target_repository(tmp_path, name="holds-one-refusal")
    refiled = producer.brief(slug="the-one-that-reached-nobody",
                             title="The one a provider refused outright")
    fresh = producer.brief(slug="the-one-nobody-has-filed",
                           title="The one no provider has been offered")
    key = refused_for(target, refiled)

    found = producer.inspecting(tmp_path, target=target,
                                act=producer.writes(refiled, fresh))

    assert found.report.local_index.failed == frozenset({key})
    assert sorted(found.filed_slugs) == sorted([refiled["slug"], fresh["slug"]])
    assert found.report.dropped == ()
    for reason in SUPPRESSION_REASONS:
        assert found.dropped(reason) == (), reason
    assert marked_slugs(found) == [refiled["slug"]]

    # The refile overwrote the failed entry at the same key with a pending one,
    # so the queue grew by nothing and the mark was taken from the index read
    # before that write.
    assert [entry["key"] for entry in found.entries] == \
        sorted({key, outbox.identity_key(inspection.identity(fresh))})
    assert outbox.local_state(target, key) == PENDING


def test_every_finding_an_inspection_holds_failed_is_still_filed(tmp_path):
    """The set drops nothing at any size, not merely nothing on one member.

    Both findings are held failed, so an implementation suppressing on the set
    would file nothing at all. The control is that both are marked, which only
    a set the report read could produce.
    """
    target = producer.target_repository(tmp_path, name="holds-two-refusals")
    first = producer.brief(slug="the-first-one-refused",
                           title="The first one a provider refused")
    second = producer.brief(slug="the-second-one-refused",
                            title="The second one a provider refused")
    keys = {refused_for(target, first), refused_for(target, second)}

    found = producer.inspecting(tmp_path, target=target,
                                act=producer.writes(first, second))

    assert found.report.local_index.failed == frozenset(keys)
    assert sorted(found.filed_slugs) == sorted([first["slug"], second["slug"]])
    assert found.report.dropped == ()
    assert sorted(marked_slugs(found)) == sorted([first["slug"],
                                                  second["slug"]])


@pytest.mark.parametrize("state", (None, LANDED, PENDING),
                         ids=("held-in-no-state", "held-landed", "held-pending"))
def test_a_brief_the_queue_did_not_hold_failed_is_not_reported_as_a_refile(
        tmp_path, state):
    """Three states the mark must not be claimed for, and the one it is.

    A landed or pending entry suppresses, so the inspection files nothing for
    that finding and nothing can carry the mark; a key held in no state is
    filed and must carry no mark either. The control is the same finding
    against the same fixture with its entry refused instead, where exactly one
    brief is marked — so an empty list here is the state of the entry rather
    than a report that never marks anything.
    """
    finding = producer.brief(slug="the-one-in-every-state",
                             title="The one every state was tried on")
    target = producer.target_repository(tmp_path, name=f"holds-it-{state}")
    if state is not None:
        producer.planted(target, finding, state)

    found = producer.inspecting(tmp_path, target=target,
                                act=producer.writes(finding))

    assert marked_slugs(found) == []

    refiled = producer.target_repository(tmp_path, name="holds-it-refused")
    refused_for(refiled, finding)
    control = producer.inspecting(tmp_path, target=refiled,
                                  act=producer.writes(finding))
    assert marked_slugs(control) == [finding["slug"]]


def test_dedupe_ran_says_nothing_about_what_the_queue_holds_failed(tmp_path):
    """`Report.dedupe_ran` stays a statement about the filed query alone.

    The same queue, holding the same failed entry, under a query that could not
    answer and under one that did: the third set is identical and the verdict
    differs, so nothing the local index says about failed entries reaches it.
    """
    finding = producer.brief(slug="the-one-under-two-queries",
                             title="The one two queries were asked about")

    silent = producer.target_repository(tmp_path, name="a-query-that-failed")
    key = refused_for(silent, finding)
    unanswered = producer.inspecting(
        tmp_path, target=silent, act=producer.writes(finding),
        config=producer.configuration(**{
            producer.filed_query.COMMAND_KEY: producer.failing_query(tmp_path)}))

    answering = producer.target_repository(tmp_path, name="a-query-that-spoke")
    assert refused_for(answering, finding) == key
    answered = producer.inspecting(
        tmp_path, target=answering, act=producer.writes(finding),
        config=producer.configuration(**{
            producer.filed_query.COMMAND_KEY: producer.answering_query(
                tmp_path)}))

    assert unanswered.report.local_index.failed == frozenset({key})
    assert answered.report.local_index.failed == frozenset({key})
    assert unanswered.report.dedupe_ran is False
    assert answered.report.dedupe_ran is True
    assert marked_slugs(unanswered) == [finding["slug"]]
    assert marked_slugs(answered) == [finding["slug"]]


# ==========================================================================
# What a developer is told, through the script that says all of it
# ==========================================================================


def notes_beneath(printed: str, slug: str) -> list[str]:
    """The lines the report indents beneath the brief carrying `slug`.

    Derived from the shape of the section rather than from any wording: a
    filed brief is one line, and everything indented under it until the next
    brief belongs to it.
    """
    lines = printed.splitlines()
    heads = [index for index, line in enumerate(lines)
             if line.startswith(BRIEF_LINE_PREFIX) and slug in line]
    assert len(heads) == 1, (slug, printed)
    notes = []
    for line in lines[heads[0] + 1:]:
        if not line.startswith(NOTE_LINE_PREFIX):
            break
        notes.append(line.strip())
    return notes


def test_the_report_names_the_brief_it_filed_over_a_terminal_failure(tmp_path):
    """The note is beneath the brief it belongs to, and beneath no other.

    Both briefs are filed by one inspection and printed in one report, so the
    brief without the note is the control for the brief with it: a report that
    printed the note for everything, or for nothing, fails one half or the
    other.
    """
    target = producer.target_repository(tmp_path, name="prints-one-refile")
    refiled = producer.brief(slug="the-one-a-provider-refused",
                             title="The one a provider refused outright")
    fresh = producer.brief(slug="the-one-offered-to-nobody",
                           title="The one no provider has been offered")
    refused_for(target, refiled)

    found = producer.inspecting(tmp_path, target=target,
                                act=producer.writes(refiled, fresh))
    printed = producer.report_text(found)

    assert marked_slugs(found) == [refiled["slug"]]
    marked = notes_beneath(printed, refiled["slug"])
    unmarked = notes_beneath(printed, fresh["slug"])

    assert any(FAILED in note for note in marked), marked
    assert not any(FAILED in note for note in unmarked), unmarked
    # The brief is named where the note is said, so a developer reading the
    # note knows which brief it is about, and it is counted among what the
    # inspection filed rather than reported apart from it.
    assert refiled["slug"] in printed
    assert refiled["title"] in printed
    assert f"{len(found.report.filed)} brief(s)" in printed


def test_the_local_queue_line_says_how_many_it_held_failed_even_when_none(
        tmp_path):
    """A source silent when it found nothing cannot be told from one that did
    not run, so the count is said on every inspection the index was read on.

    The control is the second inspection, whose queue holds one failed entry
    and whose line differs from the first only in that count.
    """
    empty = producer.inspecting(tmp_path, act=producer.writes(producer.brief()),
                                name="an-empty-queue")
    assert empty.report.local_index.read is True
    assert empty.report.local_index.failed == frozenset()

    nothing = producer.local_index_lines(producer.report_text(empty))
    assert len(nothing) == 1, nothing
    assert f"0 {FAILED}" in nothing[0]

    target = producer.target_repository(tmp_path, name="a-queue-holding-one")
    refused_entry(target, FAILED_IDENTITY)
    held = producer.inspecting(tmp_path, target=target,
                               act=producer.writes(producer.brief()))

    assert len(held.report.local_index.failed) == 1
    said = producer.local_index_lines(producer.report_text(held))
    assert len(said) == 1, said
    assert f"1 {FAILED}" in said[0]
    assert said != nothing


def test_an_index_that_could_not_be_read_claims_nothing_about_failed(
        producer_unlistable_queue, tmp_path):
    """It says it could not be read and says why, and stops there.

    The control is the line the same script prints for an index that *was*
    read, which does carry the count — so a line that makes no claim here is
    about the index being unread rather than about a report that never says it.
    """
    producer.refuses_to_be_listed(producer_unlistable_queue)
    queue = outbox.queue_dir(producer_unlistable_queue)

    found = producer.inspecting(tmp_path, target=producer_unlistable_queue,
                                act=producer.writes(producer.brief()))

    index = found.report.local_index
    assert index.read is False
    assert index.failed == frozenset()
    assert str(queue) in index.reason

    lines = producer.local_index_lines(producer.report_text(found))
    assert len(lines) == 1, lines
    assert index.reason in lines[0]
    # The reason names the directory, and a temporary directory is named after
    # the test that asked for it — so the claim is about what the line says
    # beside the reason rather than about the path inside it.
    assert FAILED not in lines[0].replace(index.reason, ""), lines
    assert marked_slugs(found) == []

    read = producer.local_index_lines(producer.report_text(
        producer.inspecting(tmp_path, act=producer.writes(producer.brief()),
                            name="a-queue-that-can-be-listed")))
    assert FAILED in read[0], read

    queue.chmod(0o700)


@pytest.fixture
def producer_unlistable_queue(tmp_path: Path) -> Path:
    """A target whose queue can be written to and cannot be listed.

    The producer module's own fixture, reached through a name of this module's
    own rather than reproduced: what makes a directory refuse to be listed is
    decided in one place.
    """
    target = producer.target_repository(tmp_path, name="an-unlistable-queue")
    queue = outbox.queue_dir(target)
    queue.mkdir(parents=True, exist_ok=True)
    queue.chmod(0o300)
    yield target
    queue.chmod(0o700)


# ==========================================================================
# Nothing here invokes a model, reaches a network or touches a tracker
# ==========================================================================


def test_no_inspection_in_this_module_runs_without_a_fake_runner():
    """Every inspection here is driven through the producer module's fake
    runner, which reaches no model, no network and no tracker.

    The control is below: the same scan, over a planted source that would have
    invoked one, reports it.
    """
    assert producer.inspections_without_a_fake_runner(
        Path(__file__).read_text(encoding="utf-8")) == []


def test_that_scan_reports_a_call_that_would_have_invoked_a_model():
    planted = ("import inspection\n\n\n"
               "def go(target, config, harness):\n"
               "    return inspection.inspect(target, config, harness)\n")
    assert producer.inspections_without_a_fake_runner(planted) == [5]


def test_the_transport_that_plants_a_failed_entry_refuses_rather_than_raises():
    """The terminal state here is reached by a provider refusing on its own
    terms, which is the only answer that reaches it.

    A transport that raises has established nothing and leaves the entry
    pending, so a failed entry planted that way would not exist and every
    assertion about the third set would hold nothing. What the fixture answers
    with is read off the queue's own contract rather than taken from prose.
    """
    transport = split.refuses()
    assert transport.raises is None
    assert isinstance(transport.answer, outbox.Filing)
    assert transport.answer.terminal is True
    assert transport.answer.reference == ""
    # The other two answers, so "terminal" is a distinction rather than a
    # property every answer happens to have.
    assert queue_tests.transient().terminal is False
    assert queue_tests.filing().terminal is False


def test_every_queue_this_module_drives_is_one_the_test_built(tmp_path):
    """Nothing here reaches a tracker, and nothing here writes into this
    repository's own queue: every entry goes beneath a temporary directory.

    The control is the same predicate pointed at this repository's own root,
    which it reports — so silence for the fixture target is a fact about where
    the entry went rather than about a check that answers nothing. Since
    story-118 a repository's queue sits beneath its *primary* working tree
    rather than beneath whichever tree asked for it, and this suite is run from
    a worktree, so the control names that tree — the one this repository's
    queue actually belongs to — with the predicate and its strictness
    unchanged.
    """
    repository = Path(outbox.__file__).resolve().parents[1]
    owned = tmp_path / "a-queue-this-test-owns"
    key = refused_entry(owned, FAILED_IDENTITY)

    assert repository not in outbox.queue_dir(owned).parents
    assert worktrees.primary_root(repository) in outbox.queue_dir(repository).parents
    assert outbox.local_index(owned).failed == frozenset({key})
    assert json.loads(outbox.entry_path(outbox.queue_dir(owned), key)
                      .read_text(encoding="utf-8"))["state"] == FAILED
