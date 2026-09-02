"""Independent validation for the outbox: it never blocks a run, and it is
total over what it is handed.

Written from the stories' acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the key.** A pure function over a mapping, so it is driven directly and
    compared against a digest this module computes for itself rather than
    against whatever `orchestration/outbox.py` happens to compute. Two calls
    with one identity and differing payloads, and two calls with differing
    identities, are the two directions that matter.

  * **the states and the transitions.** Driven against a fake transport built
    here, which can answer a filing with a reference, with a terminal error or
    with a transient one, can raise instead of answering, and can answer a
    lookup — and which records every call it was given, so the ambiguous-write
    case can assert that filing did not happen twice. Nothing here reaches a
    network and nothing here configures a provider; the transport is a seam and
    this is the only transport the story ships.

  * **the entry shape.** `schemas/outbox-entry.schema.json` is a live harness
    artifact and is the subject of the assertions that name it: what `enqueue`
    writes is validated against the schema the harness ships, and the manifest
    is read to confirm the schema is declared.

  * **the script.** `scripts/l5-sync` is a shipped entry point, driven as a
    subprocess against queues seeded through the module itself, so the exit
    rule is decided by running it. Its no-config failure is compared against
    another entry point's rather than against a message written here, which is
    what "identically to the other entry points, byte for byte" asks for.

  * **totality.** Every way an item can fail to become an entry, driven one at
    a time rather than as one case: a payload json cannot render, an identity
    json cannot render — which is the path a widened `except` clause alone
    would leave open, so it is driven separately — a structure that refers to
    itself, and a queue that cannot be written to. What each of them returns,
    what the queue holds afterwards, and where the drop was reported are asked
    separately, because a fix can get any one of them right while getting
    another wrong.

  * **the guarantee.** A full run through a workflow built by
    `conftest.build_workflow` and materialized into a harness root this module
    owns — the coordinator's completing a run is a mechanism, and the workflow
    it executes is an input to it. The queue is seeded pending, every transport
    call fails, and the run still passes pre-flight, still commits its work and
    still completes.

Every absence asserted here carries a demonstration that it can fail:

  * "a poisoned file is left byte for byte unchanged" sits beside a well-formed
    entry swept in the same call, whose bytes the same comparison reports as
    changed;
  * "a pending entry leaves the tree clean" sits beside the same repository
    with the ignore line absent, where the same pre-flight reports the entry
    and refuses the run;
  * "the run's commit carries no queue file" sits beside the same listing,
    which must name the files the run did commit;
  * "filing was not called a second time" sits beside the attempt-zero case,
    where the same recorder shows the lookup skipped and the filing made;
  * "a run reaches the queue only through the sweep seam" sits beside three
    demonstrations that the same scans can fail: a module outside the declared
    set with the queue's name planted in it, a declared module with the name
    taken out of it, and the coordinator with each of the queue's own writing
    operations planted as a call;
  * "a drop wrote no entry" sits beside an ordinary call into the same queue,
    which the same listing must report;
  * "a successful enqueue writes nothing to stderr" sits beside a drop in the
    same test, whose line the same capture holds;
  * "no run directory means no events.log anywhere" sits beside the same sweep
    over a tree where a run directory was given, which must find the log;
  * "the outbox imports no coordinator at module scope" sits beside the same
    scan over a source with a module-scope import planted in it, and beside
    the same scan finding the import the module does make inside a function.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import conftest
import outbox
import outbox_sweep
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import StageRef, workflow_stage

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
ORCHESTRATION = REPO_ROOT / "orchestration"

#: The queue as a repository-relative path and as the line that ignores it,
#: derived from the module's own constant rather than written here: the story's
#: claim is that *the queue* is ignored, and a second spelling of where the
#: queue lives is a second thing to keep true.
QUEUE_REL = "/".join(outbox.QUEUE_DIR)
IGNORE_LINE = f"{QUEUE_REL}/\n"

#: The stem the outbox validates an entry against, read off the module so this
#: module names no schema of its own.
ENTRY_SCHEMA = outbox.ENTRY_SCHEMA


# --------------------------------------------------------------------------
# The fake transport: the only transport this story ships
# --------------------------------------------------------------------------


class FakeTransport:
    """A transport that answers however a test tells it to, and remembers.

    `answer` is what a filing is answered with — an `outbox.Filing`, or a
    callable taking the entry — and `raises` makes the filing raise instead,
    which is the ambiguous write in its starkest form. `lookups` maps a key to
    what the provider holds for it; a key absent from it is a key the provider
    does not know, which the contract spells as the empty string.

    Every call is recorded, because several of the story's criteria are about
    which operations were invoked rather than about what came back.
    """

    def __init__(self, *, answer=None, raises=None, lookups=None,
                 lookup_raises=False):
        self.answer = answer
        self.raises = raises
        self.lookups = dict(lookups or {})
        self.lookup_raises = lookup_raises
        self.filed: list[str] = []
        self.looked_up: list[str] = []

    def file(self, entry):
        self.filed.append(entry["key"])
        if self.raises is not None:
            raise self.raises
        answer = self.answer
        return answer(entry) if callable(answer) else answer

    def look_up(self, key):
        self.looked_up.append(key)
        if self.lookup_raises:
            raise RuntimeError("the provider could not be asked")
        return self.lookups.get(key, "")


class FilingOnlyTransport:
    """A transport offering no lookup at all.

    The contract's other end: a lookup that cannot be made establishes nothing,
    and an entry whose provider cannot be asked is filed as it would have been
    without asking.
    """

    def __init__(self, answer):
        self.answer = answer
        self.filed: list[str] = []

    def file(self, entry):
        self.filed.append(entry["key"])
        return self.answer


REFERENCE = "provider-reference-1"
SECOND_REFERENCE = "provider-reference-2"


def filing(reference: str = REFERENCE) -> outbox.Filing:
    return outbox.filed(reference)


def transient() -> outbox.Filing:
    return outbox.deferred("the provider could not be reached")


def terminal() -> outbox.Filing:
    return outbox.refused("the provider rejected this outright")


# --------------------------------------------------------------------------
# Queue helpers
# --------------------------------------------------------------------------


IDENTITY = {"kind": "sample", "subject": "story-001"}
OTHER_IDENTITY = {"kind": "sample", "subject": "story-002"}
PAYLOAD = {"title": "something to file", "body": "what it says"}
RICHER_PAYLOAD = {"title": "something to file", "body": "what it says",
                  "labels": ["a field the payload gained later"]}


@pytest.fixture
def queue(tmp_path: Path) -> Path:
    return outbox.queue_dir(tmp_path / "a-target")


def entry_of(queue: Path, key: str) -> dict:
    return json.loads(outbox.entry_path(queue, key).read_text(encoding="utf-8"))


def seeded_pending(queue: Path, identity: dict = IDENTITY,
                   payload: dict = PAYLOAD) -> str:
    return outbox.enqueue(queue, payload, identity)


def bytes_in(queue: Path) -> dict[str, bytes]:
    """Every file the queue holds, by name, as bytes.

    Bytes rather than parsed objects, because "left exactly as it is" is a
    claim about the file and not about what it would parse to.
    """
    return {path.name: path.read_bytes() for path in sorted(queue.iterdir())}


# --------------------------------------------------------------------------
# The key is a digest of the identity alone
# --------------------------------------------------------------------------


def expected_key(identity: dict) -> str:
    """The digest the story specifies, computed here rather than imported.

    Comparing the module's key against the module's own derivation would assert
    only that it is deterministic. The story says what the key *is* — a sha256
    over the canonical JSON of the identity — so this module spells that out
    and compares against it.
    """
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_enqueue_returns_the_key_it_wrote_the_entry_under(queue):
    key = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    assert key == expected_key(IDENTITY)
    written = outbox.entry_path(queue, key)
    assert written.is_file()
    assert entry_of(queue, key)["key"] == key


def test_the_key_is_a_digest_of_the_identity_however_it_is_spelled(queue):
    """Canonical JSON, so a differently ordered identity is the same identity."""
    reordered = dict(reversed(list(IDENTITY.items())))
    assert list(reordered) != list(IDENTITY)
    assert outbox.identity_key(reordered) == outbox.identity_key(IDENTITY)


def test_one_identity_and_two_payloads_produce_one_key(queue):
    first = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    second = outbox.enqueue(queue, RICHER_PAYLOAD, IDENTITY)
    assert first == second
    # One key, so one entry: the payload that gained a field did not become a
    # second thing to file.
    assert list(queue.iterdir()) == [outbox.entry_path(queue, first)]
    assert entry_of(queue, first)["payload"] == RICHER_PAYLOAD


def test_two_identities_produce_two_keys(queue):
    first = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    second = outbox.enqueue(queue, PAYLOAD, OTHER_IDENTITY)
    assert first != second
    assert len(list(queue.iterdir())) == 2


# --------------------------------------------------------------------------
# The entry shape is the schema the harness ships
# --------------------------------------------------------------------------


def test_the_entry_schema_is_declared_in_the_manifest():
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    declared = manifest["schemas"] if isinstance(manifest, dict) else manifest
    assert ENTRY_SCHEMA in declared
    assert (REPO_ROOT / "schemas" / f"{ENTRY_SCHEMA}.schema.json").is_file()


def test_the_entries_the_outbox_writes_validate_against_that_schema(queue):
    schema = schema_validator.load_schema(ENTRY_SCHEMA)
    pending = entry_of(queue, seeded_pending(queue))
    assert schema_validator.validate(pending, schema) == []

    transport = FakeTransport(answer=filing())
    outbox.sync(queue, transport)
    landed = entry_of(queue, outbox.identity_key(IDENTITY))
    assert landed["state"] == outbox.LANDED
    assert schema_validator.validate(landed, schema) == []

    # The control for both: the same validator against an entry that has lost a
    # required field reports it, so an empty problem list above is a fact about
    # the entries rather than about a validator that cannot see anything.
    broken = {name: value for name, value in landed.items() if name != "state"}
    assert schema_validator.validate(broken, schema) != []


# --------------------------------------------------------------------------
# The three answers a filing can give, and the three states
# --------------------------------------------------------------------------


def test_a_reference_lands_the_entry_and_drops_its_payload(queue):
    key = seeded_pending(queue)
    transport = FakeTransport(answer=filing())
    summary = outbox.sync(queue, transport)

    entry = entry_of(queue, key)
    assert entry["state"] == outbox.LANDED
    assert entry["reference"] == REFERENCE
    assert "payload" not in entry
    assert summary.landed == 1 and summary.landed_keys == (key,)
    assert summary.pending == 0 and summary.failed == 0


def test_a_transient_error_leaves_it_pending_and_a_later_sync_lands_it(queue):
    key = seeded_pending(queue)
    failing = FakeTransport(answer=transient())
    summary = outbox.sync(queue, failing)

    entry = entry_of(queue, key)
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert entry["last_error"] == transient().error
    assert summary.pending_keys == (key,)
    assert not summary.blocked

    succeeding = FakeTransport(answer=filing(SECOND_REFERENCE),
                               lookups={})
    later = outbox.sync(queue, succeeding)
    landed = entry_of(queue, key)
    assert landed["state"] == outbox.LANDED
    assert landed["reference"] == SECOND_REFERENCE
    assert later.landed_keys == (key,)


def test_a_terminal_error_fails_it_and_a_later_sync_does_not_file_it_again(queue):
    key = seeded_pending(queue)
    refusing = FakeTransport(answer=terminal())
    summary = outbox.sync(queue, refusing)

    entry = entry_of(queue, key)
    assert entry["state"] == outbox.FAILED
    assert entry["attempts"] == 1
    assert entry["last_error"] == terminal().error
    assert summary.failed_keys == (key,)
    assert summary.blocked
    assert refusing.filed == [key]

    would_file = FakeTransport(answer=filing())
    again = outbox.sync(queue, would_file)
    assert would_file.filed == []
    assert would_file.looked_up == []
    assert again.failed_keys == (key,)
    assert entry_of(queue, key)["state"] == outbox.FAILED


def test_a_transport_that_raises_does_not_raise_into_the_caller(queue):
    key = seeded_pending(queue)
    exploding = FakeTransport(raises=RuntimeError("the socket went away"))

    summary = outbox.sync(queue, exploding)

    assert exploding.filed == [key]
    entry = entry_of(queue, key)
    assert entry["state"] == outbox.PENDING
    assert entry["attempts"] == 1
    assert "the socket went away" in entry["last_error"]
    assert summary.pending_keys == (key,)
    assert not summary.blocked


def test_a_transport_answering_with_nonsense_is_read_as_transient(queue):
    key = seeded_pending(queue)
    nonsense = FakeTransport(answer="not a filing at all")

    summary = outbox.sync(queue, nonsense)

    assert entry_of(queue, key)["state"] == outbox.PENDING
    assert entry_of(queue, key)["attempts"] == 1
    assert summary.pending_keys == (key,)


def test_no_transport_files_nothing_and_reports_the_queue_as_it_stands(queue):
    key = seeded_pending(queue)
    before = bytes_in(queue)

    summary = outbox.sync(queue)

    assert summary.transport is False
    assert summary.pending_keys == (key,)
    assert not summary.blocked
    assert bytes_in(queue) == before


def test_an_absent_queue_directory_is_an_empty_queue_rather_than_a_failure(
        tmp_path):
    summary = outbox.sync(outbox.queue_dir(tmp_path / "never-enqueued"),
                          FakeTransport(answer=filing()))
    assert (summary.landed, summary.pending, summary.failed,
            summary.poisoned) == (0, 0, 0, 0)
    assert not summary.blocked


# --------------------------------------------------------------------------
# The ambiguous write: ask before re-filing
# --------------------------------------------------------------------------


def attempted_once(queue: Path) -> str:
    """An entry that has been offered to a provider and left pending.

    Built by driving the module rather than by writing an entry with a chosen
    attempt count, so the state this fixture reaches is one a real sync
    produces.
    """
    key = seeded_pending(queue)
    outbox.sync(queue, FakeTransport(answer=transient()))
    assert entry_of(queue, key)["attempts"] == 1
    return key


def test_an_attempted_entry_is_looked_up_before_it_is_filed_again(queue):
    key = attempted_once(queue)
    knows_it = FakeTransport(answer=filing(), lookups={key: REFERENCE})

    summary = outbox.sync(queue, knows_it)

    assert knows_it.looked_up == [key]
    assert knows_it.filed == [], "the entry was filed a second time"
    entry = entry_of(queue, key)
    assert entry["state"] == outbox.LANDED
    assert entry["reference"] == REFERENCE
    assert "payload" not in entry
    assert summary.landed_keys == (key,)


def test_an_unattempted_entry_is_filed_without_being_looked_up(queue):
    """The control for the assertion above: with nothing attempted the lookup
    is skipped and the filing made, so "filed == []" there is the ask-first
    rule and not a recorder that never sees anything."""
    key = seeded_pending(queue)
    transport = FakeTransport(answer=filing(), lookups={key: REFERENCE})

    outbox.sync(queue, transport)

    assert transport.looked_up == []
    assert transport.filed == [key]


def test_a_lookup_that_establishes_nothing_falls_through_to_filing(queue):
    key = attempted_once(queue)
    unknowing = FakeTransport(answer=filing(SECOND_REFERENCE), lookups={})

    outbox.sync(queue, unknowing)

    assert unknowing.looked_up == [key]
    assert unknowing.filed == [key]
    assert entry_of(queue, key)["reference"] == SECOND_REFERENCE


def test_a_lookup_that_raises_falls_through_to_filing(queue):
    key = attempted_once(queue)
    broken = FakeTransport(answer=filing(SECOND_REFERENCE), lookup_raises=True)

    outbox.sync(queue, broken)

    assert broken.filed == [key]
    assert entry_of(queue, key)["state"] == outbox.LANDED


def test_a_transport_offering_no_lookup_falls_through_to_filing(queue):
    key = attempted_once(queue)
    no_lookup = FilingOnlyTransport(filing(SECOND_REFERENCE))

    outbox.sync(queue, no_lookup)

    assert no_lookup.filed == [key]
    assert entry_of(queue, key)["state"] == outbox.LANDED


# --------------------------------------------------------------------------
# A poisoned file is named, counted, and otherwise untouched
# --------------------------------------------------------------------------


MALFORMED_NAME = f"not-valid-json{outbox.ENTRY_SUFFIX}"
MALFORMED_BYTES = b'{"key": "half a file'

WRONG_SHAPE_NAME = f"valid-json-wrong-shape{outbox.ENTRY_SUFFIX}"
WRONG_SHAPE_BYTES = b'{"key": "a key and nothing else the schema asks for"}\n'


def poisoned_queue(queue: Path) -> str:
    """Both poisoned files, plus one well-formed pending entry beside them.

    The well-formed entry is the control: the same sync must rewrite it, so
    "the poisoned files are byte for byte what they were" is a fact about those
    files rather than about a sync that touched nothing at all.
    """
    key = seeded_pending(queue)
    (queue / MALFORMED_NAME).write_bytes(MALFORMED_BYTES)
    (queue / WRONG_SHAPE_NAME).write_bytes(WRONG_SHAPE_BYTES)
    return key


def test_a_poisoned_file_is_left_byte_for_byte_unchanged_and_is_named(queue):
    key = poisoned_queue(queue)
    before = bytes_in(queue)

    summary = outbox.sync(queue, FakeTransport(answer=transient()))

    after = bytes_in(queue)
    assert after[MALFORMED_NAME] == before[MALFORMED_NAME]
    assert after[WRONG_SHAPE_NAME] == before[WRONG_SHAPE_NAME]
    # The control: the entry that was not poisoned *was* rewritten by the same
    # call, so the comparison above can tell a changed file from an unchanged
    # one.
    entry_name = outbox.entry_path(queue, key).name
    assert after[entry_name] != before[entry_name]

    named = {poisoned.path for poisoned in summary.poisoned_files}
    assert named == {MALFORMED_NAME, WRONG_SHAPE_NAME}
    assert summary.poisoned == 2
    assert all(poisoned.problems for poisoned in summary.poisoned_files)
    assert summary.blocked


def test_a_poisoned_file_is_neither_repaired_nor_deleted(queue):
    poisoned_queue(queue)
    outbox.sync(queue, FakeTransport(answer=filing()))
    assert (queue / MALFORMED_NAME).is_file()
    assert (queue / WRONG_SHAPE_NAME).is_file()


# --------------------------------------------------------------------------
# scripts/l5-sync: the explicit drain, and the exit rule
# --------------------------------------------------------------------------


MINIMAL_CONFIG = "workflow: story-workflow\n"


@pytest.fixture
def sync_target(tmp_path: Path) -> Path:
    """A target repository l5-sync can resolve, with an empty queue."""
    root = tmp_path / "sync-target"
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(MINIMAL_CONFIG,
                                                   encoding="utf-8")
    return root


def run_entry_point(name: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def run_sync(cwd: Path) -> subprocess.CompletedProcess:
    return run_entry_point("l5-sync", cwd)


def test_l5_sync_exits_zero_for_a_queue_it_drained(sync_target):
    queue = outbox.queue_dir(sync_target)
    key = seeded_pending(queue)
    outbox.sync(queue, FakeTransport(answer=filing()))
    assert entry_of(queue, key)["state"] == outbox.LANDED

    result = run_sync(sync_target)
    assert result.returncode == 0, result.stderr
    assert str(queue) in result.stdout


def test_l5_sync_exits_zero_for_a_queue_left_holding_only_pending_entries(
        sync_target):
    key = seeded_pending(outbox.queue_dir(sync_target))

    result = run_sync(sync_target)
    assert result.returncode == 0, result.stderr
    assert key in result.stdout


def test_l5_sync_exits_nonzero_and_names_a_failed_entry(sync_target):
    queue = outbox.queue_dir(sync_target)
    key = seeded_pending(queue)
    outbox.sync(queue, FakeTransport(answer=terminal()))
    assert entry_of(queue, key)["state"] == outbox.FAILED

    result = run_sync(sync_target)
    assert result.returncode != 0
    assert key in result.stdout


def test_l5_sync_exits_nonzero_and_names_a_poisoned_entry(sync_target):
    poisoned_queue(outbox.queue_dir(sync_target))

    result = run_sync(sync_target)
    assert result.returncode != 0
    assert MALFORMED_NAME in result.stdout
    assert WRONG_SHAPE_NAME in result.stdout


def test_l5_sync_says_no_transport_is_configured_and_still_reports_the_queue(
        sync_target):
    """This story ships no provider, so an ordinary invocation files nothing.
    It still reports what the queue holds and still applies the same rule."""
    key = seeded_pending(outbox.queue_dir(sync_target))

    result = run_sync(sync_target)
    assert result.returncode == 0, result.stderr
    assert "no transport is configured" in result.stdout
    assert key in result.stdout

    # The same invocation over a queue holding a poisoned file still refuses,
    # so "no transport" is not a path that skips the rule.
    poisoned_queue(outbox.queue_dir(sync_target))
    blocked = run_sync(sync_target)
    assert blocked.returncode != 0
    assert "no transport is configured" in blocked.stdout


def test_l5_sync_fails_identically_to_another_entry_point_with_no_config(
        tmp_path):
    """Byte for byte against another entry point's refusal rather than against
    a message written here: the criterion is that they are the same, and a
    literal copied into this module would agree with itself if the shared
    lookup's message changed under both."""
    bare = tmp_path / "nowhere"
    bare.mkdir()
    ours = run_sync(bare)
    theirs = run_entry_point("l5-status", bare)

    assert ours.returncode == 1
    assert ours.returncode == theirs.returncode
    assert ours.stderr == theirs.stderr
    assert ours.stderr.strip() != ""
    assert ours.stdout == ""


def test_l5_sync_calls_the_shared_target_root_lookup():
    source = (SCRIPTS / "l5-sync").read_text(encoding="utf-8")
    assert "harness_config.find_target_root(Path.cwd())" in source


# --------------------------------------------------------------------------
# A run reaches the queue only through the sweep seam
#
# story-092 retired the two rules that stood here — that no module a run
# executes reaches the outbox, and that `l5-sync` is the only drain site the
# repository ships. Both were true only while nothing swept inside a run, and
# the coordinator now sweeps twice. What replaces them is stricter about the
# thing that actually matters: a run may reach the queue, but only through one
# seam, and the drain itself is written in exactly one place.
# --------------------------------------------------------------------------


def modules_reaching_the_outbox(sources: dict[str, str]) -> list[str]:
    """Which of `sources` mentions the outbox module by name."""
    return sorted(name for name, text in sources.items()
                  if outbox.__name__ in text)


#: The modules outside the queue that may name it, exempt by name and by
#: nothing else — the successor to the single exemption this rule carried
#: before a run swept anything. Each is named with what earns it:
#:
#:   brief_filing.py       the queue's second producer: it files one brief an
#:                         assist session was asked for, reached from its own
#:                         entry point and from the suite and from nothing a
#:                         run executes, so it enqueues and never drains.
#:   command_transport.py  answers in the `Filing` values the queue defines,
#:                         so it must name the queue to import them.
#:   inspection.py         the queue's first producer, reached from
#:                         scripts/l5-inspect and from the suite and from
#:                         nothing a run executes, so it enqueues and never
#:                         drains.
#:   outbox_sweep.py       the seam itself: the one module that calls the
#:                         queue's drain, and the only route a run has to it.
#:   run_status.py         reads the queue as data for the l5-status listing,
#:                         building no transport and filing nothing.
#:   story_brief.py        names the queue in prose and reaches it not at all:
#:                         it says what goes into the identity both producers
#:                         file under, and points at the queue as the one place
#:                         a key is derived and the one way a brief gets there.
#:                         It imports nothing from the queue and calls none of
#:                         its operations, which is what the scan below holds
#:                         by requiring the name and what the coordinator's
#:                         own check holds for the operations.
#:   story_coordinator.py  reaches the queue through the seam and nowhere
#:                         else, which is what the check below decides.
#:
#: The set is held shut from both sides: a module outside it that starts
#: naming the queue is reported, and a module inside it that stops naming the
#: queue is a stale exemption nobody would otherwise notice.
MODULES_THAT_MAY_NAME_THE_QUEUE = (
    "brief_filing.py",
    "command_transport.py",
    "inspection.py",
    f"{outbox_sweep.__name__}.py",
    "run_status.py",
    "story_brief.py",
    "story_coordinator.py",
)

#: The queue's own operations, which the coordinator must reach through the
#: seam rather than call for itself. Naming the queue is permitted above;
#: draining or writing to it directly is not, and these are the two names that
#: would say it had.
QUEUE_OPERATIONS = ("sync", "enqueue")


def orchestration_sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(ORCHESTRATION.glob("*.py"))
            if path.name != f"{outbox.__name__}.py"}


def repository_sources() -> dict[str, str]:
    """Every module and entry point this repository ships, by name.

    `scripts/` as well as `orchestration/`, because the drain site the rule
    below is about used to be a script and the question is where it is now
    rather than which directory it is in.
    """
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(list(ORCHESTRATION.glob("*.py")) +
                           [p for p in SCRIPTS.iterdir() if p.is_file()])
    }


def queue_operations_called_in(source: str) -> list[str]:
    """Which of the queue's own operations a source calls directly."""
    return [name for name in QUEUE_OPERATIONS
            if f"{outbox.__name__}.{name}(" in source]


def drain_sites(sources: dict[str, str]) -> list[str]:
    """Which of `sources` calls the queue's drain."""
    return sorted(name for name, text in sources.items()
                  if f"{outbox.__name__}.sync(" in text)


def test_a_run_reaches_the_queue_only_through_the_sweep_seam():
    sources = orchestration_sources()
    assert modules_reaching_the_outbox(sources) == sorted(
        MODULES_THAT_MAY_NAME_THE_QUEUE)
    # The other side of every exemption: a name that stopped naming the queue
    # is an exemption nobody notices has gone stale.
    for name in MODULES_THAT_MAY_NAME_THE_QUEUE:
        assert name in sources, name
        assert outbox.__name__ in sources[name], name
    # And what the coordinator's own exemption buys: it may reach the queue,
    # through the seam, and it calls neither of the queue's own operations.
    assert queue_operations_called_in(sources["story_coordinator.py"]) == []
    assert outbox_sweep.__name__ in sources["story_coordinator.py"]


def test_the_scan_reports_a_planted_call_site():
    """Control: the report above must mean no *undeclared* module reaches the
    queue, not that the scan has stopped seeing anything.

    The victim is derived rather than named, so this stays a control over
    whatever modules the repository holds rather than over one that may itself
    join the declared set later.
    """
    sources = orchestration_sources()
    victim = next(name for name in sorted(sources)
                  if name not in MODULES_THAT_MAY_NAME_THE_QUEUE)
    sources[victim] += f"\nimport {outbox.__name__}\n"
    assert victim in modules_reaching_the_outbox(sources)


@pytest.mark.parametrize("name", MODULES_THAT_MAY_NAME_THE_QUEUE)
def test_the_scan_reports_an_exemption_that_has_gone_stale(name):
    """Control: a declared module that has stopped naming the queue is
    reported, so the set above is a set of live exemptions rather than a list
    that has outlived what it exempted."""
    sources = orchestration_sources()
    sources[name] = sources[name].replace(outbox.__name__, "a_module_by_another_name")
    assert name not in modules_reaching_the_outbox(sources)


@pytest.mark.parametrize("operation", QUEUE_OPERATIONS)
def test_the_scan_reports_a_queue_operation_planted_in_the_coordinator(operation):
    """Control: the empty list above is a fact about the coordinator's source
    rather than about a check that has stopped recognising a call."""
    planted = (orchestration_sources()["story_coordinator.py"]
               + f"\n{outbox.__name__}.{operation}(queue)\n")
    assert queue_operations_called_in(planted) == [operation]


def test_the_only_module_that_calls_the_queues_drain_is_the_sweep_seam():
    """One drain, written once. Where this used to name `l5-sync`, the script
    now reaches the drain through the seam like everything else, so the seam is
    the single place `sync` is called from."""
    assert drain_sites(repository_sources()) == [f"{outbox_sweep.__name__}.py"]


def test_the_drain_scan_reports_a_second_call_site():
    """Control for the singleton above, against every module that is not the
    seam: a drain planted anywhere else is reported, so a list of one is a fact
    about this repository rather than a scan that finds at most one thing."""
    sources = repository_sources()
    victim = next(name for name in sorted(sources)
                  if name != f"{outbox_sweep.__name__}.py")
    sources[victim] += f"\n{outbox.__name__}.sync(queue, None)\n"
    assert drain_sites(sources) == sorted(
        [victim, f"{outbox_sweep.__name__}.py"])


# --------------------------------------------------------------------------
# The queue is ignored rather than merely untracked
# --------------------------------------------------------------------------


def test_this_repository_ignores_its_own_queue_directory():
    """A shipped artifact, so the subject is this repository's own .gitignore,
    asked through git rather than by reading the file for a line."""
    probe = f"{QUEUE_REL}/an-entry{outbox.ENTRY_SUFFIX}"
    ignored = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-v", probe],
        capture_output=True, text=True)
    assert ignored.returncode == 0, ignored.stderr
    assert ".gitignore" in ignored.stdout

    # The control: a path the repository tracks is not reported by the same
    # question, so a zero status above is about the queue and not about a
    # command that answers yes to everything.
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-v",
         str(Path(outbox.__file__).relative_to(REPO_ROOT))],
        capture_output=True, text=True)
    assert tracked.returncode != 0


def ignoring(root: Path) -> None:
    (root / ".gitignore").write_text(IGNORE_LINE, encoding="utf-8")


def test_a_pending_entry_leaves_the_tree_clean_where_the_queue_is_ignored(
        target_root):
    ignoring(target_root)
    conftest.commit_setup(target_root, "ignore the queue")
    assert story_coordinator.dirty_paths(target_root) == []

    seeded_pending(outbox.queue_dir(target_root))
    assert story_coordinator.dirty_paths(target_root) == []


def test_the_same_entry_dirties_a_tree_that_does_not_ignore_the_queue(
        target_root):
    """The control for the assertion above. Without the ignore line the entry
    is merely untracked, `git status --porcelain` reports it, and the pre-flight
    that reads that reports it too — which is the run this queue exists never to
    refuse."""
    assert story_coordinator.dirty_paths(target_root) == []
    seeded_pending(outbox.queue_dir(target_root))

    dirty = story_coordinator.dirty_paths(target_root)
    assert dirty != []
    assert any(path.startswith(QUEUE_REL) for path in dirty), dirty


# ==========================================================================
# The guarantee: a full run completes with the queue pending and every
# transport call failing
# ==========================================================================


#: The workflow the run below executes. Built rather than resolved: whether a
#: run completes is a property of the coordinator, and the definition it walks
#: is an input to that. A writer and a verifier are enough to reach a verdict
#: and a commit, and the names are the builder's.
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
    name="outbox-guarantee-workflow",
)

WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

STORY_ID = "story-001"

PASSED = {"status": "passed", "blocking_issues": [], "unverified": [],
          "retry_recommended": False}


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def guarantee_harness(tmp_path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "outbox-harness")


class RunnerWritingArtifacts:
    """Stands in for agent_runner.run_agent, writing each stage's outputs."""

    def __init__(self, target_root: Path):
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, **declared):
        self.calls.append(stage)
        if stage == WRITING:
            (self.run_dir / conftest.CHANGED_FILES).write_text(
                json.dumps({"modified": ["src/app.py"], "created": [],
                            "deleted": []}), encoding="utf-8")
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            (cwd / "src" / "app.py").write_text("print('the work')\n",
                                                encoding="utf-8")
        elif stage == VERIFYING:
            (self.run_dir / conftest.VERIFICATION_RESULT).write_text(
                json.dumps(PASSED), encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


def committed_paths(root: Path, revision: str = "HEAD") -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=", revision],
        capture_output=True, text=True, check=True)
    return [line for line in listed.stdout.splitlines() if line.strip()]


def test_a_run_completes_and_commits_with_the_queue_pending_and_every_call_failing(
        target_root, guarantee_harness):
    """The guarantee. Everything else in this module is the plumbing under it.

    The queue is seeded pending, every transport call fails — a raise, which is
    the failure that tells us least — and sync is driven before, during and
    after the run, so no moment of the run is one where the queue was quiet.
    """
    ignoring(target_root)
    conftest.commit_setup(target_root, "ignore the queue")
    queue = outbox.queue_dir(target_root)
    keys = [seeded_pending(queue, IDENTITY),
            seeded_pending(queue, OTHER_IDENTITY)]

    always_fails = FakeTransport(raises=RuntimeError("nothing is reachable"))

    # Before the run: the pre-flight this queue must never trip.
    assert outbox.sync(queue, always_fails).pending == len(keys)
    assert story_coordinator.dirty_paths(target_root) == []

    runner = RunnerWritingArtifacts(target_root)

    class SyncingRunner:
        """The runner, with a failing drain attempted around every stage."""

        def __init__(self, inner):
            self.inner = inner
            self.summaries = []

        def __call__(self, *args, **kwargs):
            self.summaries.append(outbox.sync(queue, always_fails))
            result = self.inner(*args, **kwargs)
            self.summaries.append(outbox.sync(queue, always_fails))
            return result

    syncing = SyncingRunner(runner)
    code = story_coordinator.run_story(STORY_ID, guarantee_harness,
                                       target_root, syncing)

    assert code == 0, "the run did not complete"
    state = json.loads(
        (target_root / ".harness" / "runs" / STORY_ID / "state.json")
        .read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert runner.calls == [WRITING, VERIFYING]

    # Every drain attempted during the run failed, and left every entry pending.
    assert syncing.summaries, "no drain was attempted during the run"
    assert all(summary.landed == 0 and summary.failed == 0 and
               summary.pending == len(keys) for summary in syncing.summaries)
    assert always_fails.filed, "the transport was never asked"
    for key in keys:
        entry = entry_of(queue, key)
        assert entry["state"] == outbox.PENDING
        assert entry["attempts"] > 0
        assert "nothing is reachable" in entry["last_error"]

    # The run committed its work, and committed nothing from the queue.
    committed = committed_paths(target_root)
    assert committed, "the run's commit names no files at all"
    assert [path for path in committed if path.startswith(QUEUE_REL)] == []
    assert story_coordinator.dirty_paths(target_root) == []


def test_the_committed_listing_would_report_a_queue_file_that_was_committed(
        target_root):
    """The control for the assertion above: with the queue not ignored, a
    commit of the working tree names the entry, so an empty list there is the
    ignore rule working rather than a listing that cannot see the queue."""
    seeded_pending(outbox.queue_dir(target_root))
    conftest.commit_setup(target_root, "commit the tree with no ignore rule")

    committed = committed_paths(target_root)
    assert [path for path in committed if path.startswith(QUEUE_REL)] != []


# ==========================================================================
# Totality: enqueue is total over the payload and the identity it is handed
# ==========================================================================


#: A value json cannot render, and the one the story says a producer will
#: reach for first: this codebase passes `Path` through nearly every seam.
UNRENDERABLE_VALUE = Path("/where/the/artifact/lives")

#: What a producer would naturally write, and what raised before this story.
PATH_PAYLOAD = {"artifact": UNRENDERABLE_VALUE}

#: An identity json cannot render. Driven separately from the payload above,
#: because the key is derived before the entry is written and a guard placed
#: only around the write would leave this one raising into the caller.
PATH_IDENTITY = {"kind": "sample", "artifact": UNRENDERABLE_VALUE}


def recursive_payload() -> dict:
    """A structure that refers to itself, which json refuses differently."""
    payload = {"title": "something to file"}
    payload["itself"] = payload
    return payload


class ReprRaises:
    """A value that cannot be rendered by json and cannot be repr'd either.

    The last resort of the drop message: reporting a drop must not itself
    become the raise the reporting exists to remove.
    """

    def __repr__(self):
        raise RuntimeError("even repr will not answer for this")


def queue_files(queue: Path) -> list[str]:
    """Every name the queue directory holds, partials included.

    Partials included deliberately: "nothing is written" is a claim about the
    directory and not only about the files a sync would read.
    """
    return sorted(path.name for path in queue.iterdir()) if queue.is_dir() else []


@pytest.fixture
def unwritable_queue(tmp_path: Path) -> Path:
    """A queue whose directory cannot be created, because a file is in the way."""
    blocked = tmp_path / "a-file-where-a-target-root-would-be"
    blocked.write_text("not a directory\n", encoding="utf-8")
    return outbox.queue_dir(blocked)


def test_the_unwritable_queue_really_cannot_be_written_to(unwritable_queue):
    """The control for every assertion that uses that fixture: the queue below
    refuses a write, so a drop there is the guard working rather than a path
    that quietly succeeded."""
    with pytest.raises(OSError):
        outbox.write_entry(unwritable_queue,
                           {"key": expected_key(IDENTITY), "payload": PAYLOAD})


def test_a_payload_json_cannot_render_is_refused_rather_than_raising(queue):
    result = outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY)
    assert result == ""
    assert queue_files(queue) == []


def test_an_identity_json_cannot_render_is_refused_rather_than_raising(queue):
    """The path a widened `except` clause alone would leave open: the key is
    derived from the identity before anything is written, so a guard that does
    not cover the derivation never sees this one."""
    result = outbox.enqueue(queue, PAYLOAD, PATH_IDENTITY)
    assert result == ""
    assert queue_files(queue) == []


def test_a_recursive_payload_is_refused_rather_than_raising(queue):
    result = outbox.enqueue(queue, recursive_payload(), IDENTITY)
    assert result == ""
    assert queue_files(queue) == []


def test_an_unwritable_queue_is_refused_and_returns_the_empty_string(
        unwritable_queue):
    """The return this story changes: the key used to come back on this path
    whether or not anything had been written."""
    assert outbox.enqueue(unwritable_queue, PAYLOAD, IDENTITY) == ""
    assert queue_files(unwritable_queue) == []


def test_the_same_listing_reports_the_entry_an_ordinary_call_writes(queue):
    """The control for the three emptiness assertions above. Every refusal is
    driven into a queue that a serializable call fills, so an empty listing is
    a fact about what the drop wrote and not about a listing that cannot see
    anything."""
    key = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    assert queue_files(queue) == [outbox.entry_path(queue, key).name]

    for payload, identity in ((PATH_PAYLOAD, IDENTITY),
                              (PAYLOAD, PATH_IDENTITY),
                              (recursive_payload(), OTHER_IDENTITY)):
        assert outbox.enqueue(queue, payload, identity) == ""
    # The refusals added nothing and removed nothing.
    assert queue_files(queue) == [outbox.entry_path(queue, key).name]


# --------------------------------------------------------------------------
# The ordinary path is exactly what it was
# --------------------------------------------------------------------------


#: A fixed instant, so the entry a call writes is fully determined and can be
#: compared against a spelling of it rather than against the module's own.
FIXED_NOW = 1_700_000_000.0


def expected_entry(identity: dict, payload: dict, now: float) -> dict:
    """The entry the story says a serializable call writes, spelled out here."""
    stamped = time.strftime(outbox.TIMESTAMP_FORMAT, time.localtime(now))
    return {
        "key": expected_key(identity),
        "identity": identity,
        "state": outbox.PENDING,
        "payload": payload,
        "attempts": 0,
        "created_at": stamped,
        "updated_at": stamped,
    }


def test_a_serializable_call_writes_exactly_the_entry_it_wrote_before(queue):
    """Same key, same name, same fields, same values, and the same bytes.

    The bytes are compared against a rendering written here — sorted keys,
    indented, one trailing newline — rather than against the module's own
    dump, so this says what the file *is* rather than that the writer agrees
    with itself.
    """
    expected = expected_entry(IDENTITY, PAYLOAD, FIXED_NOW)
    key = outbox.enqueue(queue, PAYLOAD, IDENTITY, FIXED_NOW)

    assert key == expected["key"]
    written = outbox.entry_path(queue, key)
    assert queue_files(queue) == [written.name]
    assert json.loads(written.read_text(encoding="utf-8")) == expected
    assert written.read_text(encoding="utf-8") == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n")


def test_reading_a_serializable_entry_back_is_unaffected(queue):
    key = outbox.enqueue(queue, PAYLOAD, IDENTITY, FIXED_NOW)
    entry, problems = outbox.read_entry(outbox.entry_path(queue, key))
    assert problems == []
    assert entry == expected_entry(IDENTITY, PAYLOAD, FIXED_NOW)


# --------------------------------------------------------------------------
# A refused item is not a silent one
# --------------------------------------------------------------------------


def stderr_lines(capsys, *, logged: bool = False) -> list[str]:
    """The drop's lines on stderr, with stdout held to what the sinks explain.

    The coordinator's shared append echoes every line it writes to events.log
    onto stdout, so a drop given a run directory leaves one there as well —
    that is the log sink, not a second report. A drop given no run directory
    never reaches that append, so stdout must be empty, and `logged` says
    which of the two the caller is asserting about.
    """
    captured = capsys.readouterr()
    on_stdout = [line for line in captured.out.splitlines() if line.strip()]
    if logged:
        assert on_stdout != [], "the log sink echoed nothing to stdout"
    else:
        assert on_stdout == [], f"a drop wrote to stdout: {on_stdout}"
    return [line for line in captured.err.splitlines() if line.strip()]


def one_drop_line(capsys, *, logged: bool = False) -> str:
    lines = stderr_lines(capsys, logged=logged)
    assert len(lines) == 1, lines
    return lines[0]


def test_every_drop_writes_one_line_to_stderr_naming_what_and_why(
        queue, unwritable_queue, capsys):
    """Each drop is reported, each report names the identity it lost, and the
    four reports differ — so "why" is carried rather than being one message
    four causes share."""
    drops = [
        (queue, PATH_PAYLOAD, IDENTITY),
        (queue, PAYLOAD, PATH_IDENTITY),
        (queue, recursive_payload(), IDENTITY),
        (unwritable_queue, PAYLOAD, IDENTITY),
    ]
    lines = []
    for target, payload, identity in drops:
        assert outbox.enqueue(target, payload, identity) == ""
        line = one_drop_line(capsys)
        assert repr(identity) in line, line
        # Something beyond the identity is said, and that something is the
        # reason: no two of these causes report the same sentence.
        assert line.replace(repr(identity), "").strip() != ""
        lines.append(line)
    assert len(set(lines)) == len(drops), lines


def test_a_successful_enqueue_writes_nothing_to_stderr(queue, capsys):
    key = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    assert key
    assert stderr_lines(capsys) == []

    # The control: the same capture holds a line when the same queue refuses
    # an item, so the emptiness above is silence rather than a capture that
    # sees nothing.
    assert outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY) == ""
    assert stderr_lines(capsys) != []


def test_a_drop_whose_identity_cannot_be_rendered_still_reports(queue, capsys):
    """repr is the last resort and it can fail too. A message that raised while
    explaining a drop would be the failure this reporting exists to remove."""
    unrenderable = {"kind": "sample", "subject": ReprRaises()}
    with pytest.raises(RuntimeError):
        repr(unrenderable["subject"])

    assert outbox.enqueue(queue, PAYLOAD, unrenderable) == ""
    assert queue_files(queue) == []
    line = one_drop_line(capsys)
    assert outbox.UNRENDERABLE_IDENTITY in line, line


# --------------------------------------------------------------------------
# The run's events.log
# --------------------------------------------------------------------------


def events_log_lines(run_dir: Path) -> list[str]:
    text = (run_dir / "events.log").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def message_of(line: str) -> str:
    """The message half of a `[timestamp] message` line.

    The timestamp is parsed rather than skipped, so a line that is not in the
    log's one-line format fails here instead of being trimmed into shape.
    """
    assert line.startswith("["), line
    stamp, bracket, message = line[1:].partition("] ")
    assert bracket, line
    time.strptime(stamp, outbox.TIMESTAMP_FORMAT)
    return message


def events_logs_under(root: Path) -> list[Path]:
    return sorted(root.rglob("events.log"))


@pytest.fixture
def drop_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "a-target" / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True)
    return run_dir


COORDINATOR_LINE = "a line the coordinator wrote for itself"


def test_a_drop_given_a_run_directory_appends_to_that_runs_events_log(
        queue, drop_run_dir, capsys):
    """The same message reaches both sinks, and it reaches the log in the
    format the log already carries — which is asserted against a line the
    coordinator's own append wrote into the same file rather than against a
    format spelled here."""
    assert outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY,
                          run_dir=drop_run_dir) == ""
    on_stderr = one_drop_line(capsys, logged=True)

    story_coordinator.append_event(drop_run_dir, COORDINATOR_LINE)
    lines = events_log_lines(drop_run_dir)
    assert [message_of(line) for line in lines] == [on_stderr, COORDINATOR_LINE]


def test_a_drop_given_no_run_directory_writes_no_events_log_anywhere(
        tmp_path, capsys):
    root = tmp_path / "a-target"
    queue = outbox.queue_dir(root)
    run_dir = root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True)

    assert outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY) == ""
    assert one_drop_line(capsys)
    assert events_logs_under(root) == []

    # The control: the same drop with the run directory named writes the log
    # the sweep above looked for, so the empty sweep is a fact about the call
    # that omitted it rather than about a sweep looking in the wrong place.
    assert outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY, run_dir=run_dir) == ""
    assert one_drop_line(capsys, logged=True)
    assert events_logs_under(root) == [run_dir / "events.log"]


def test_a_successful_enqueue_given_a_run_directory_writes_no_events_log(
        queue, drop_run_dir):
    assert outbox.enqueue(queue, PAYLOAD, IDENTITY, run_dir=drop_run_dir)
    assert events_logs_under(drop_run_dir) == []

    # The control: a drop through the same argument does write it.
    assert outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY,
                          run_dir=drop_run_dir) == ""
    assert events_logs_under(drop_run_dir) == [drop_run_dir / "events.log"]


def test_the_run_directory_cannot_be_passed_where_now_goes(queue):
    """Keyword-only, so an existing call renders as it did and the new argument
    cannot slide into the position `now` occupies."""
    with pytest.raises(TypeError):
        outbox.enqueue(queue, PAYLOAD, IDENTITY, None, Path("a-run-dir"))


# --------------------------------------------------------------------------
# Reporting that cannot be delivered is still not a failure to enqueue
# --------------------------------------------------------------------------


class ExplodingStream:
    """A stderr that cannot be written to."""

    def write(self, text):
        raise OSError("the stream went away")

    def flush(self):
        raise OSError("the stream went away")


def test_a_run_directory_that_does_not_exist_does_not_stop_the_drop(
        queue, tmp_path, capsys):
    missing = tmp_path / "a-run-directory-that-was-never-created"
    assert not missing.exists()

    assert outbox.enqueue(queue, PATH_PAYLOAD, IDENTITY, run_dir=missing) == ""
    assert queue_files(queue) == []
    assert not missing.exists()
    # The sink that could be reached was still written to.
    assert one_drop_line(capsys)


def test_a_drop_no_sink_can_carry_is_still_a_drop_rather_than_a_raise(
        queue, tmp_path, monkeypatch):
    """Both sinks fail at once: an unwritable stderr and a run directory that
    is not there. The item is lost, which is the same case as an unwritable
    queue, and nothing escapes into the caller."""
    stream = ExplodingStream()
    with pytest.raises(OSError):
        print("the stream really does refuse", file=stream)

    monkeypatch.setattr(sys, "stderr", stream)
    result = outbox.enqueue(
        queue, PATH_PAYLOAD, IDENTITY,
        run_dir=tmp_path / "a-run-directory-that-was-never-created")

    assert result == ""
    assert queue_files(queue) == []


def test_an_ordinary_call_still_succeeds_with_an_unwritable_stderr(
        queue, monkeypatch):
    """The control for the assertion above: with the same broken stream in
    place a serializable call still writes its entry, so the empty return there
    is the drop and not the stream having broken enqueue itself."""
    monkeypatch.setattr(sys, "stderr", ExplodingStream())
    key = outbox.enqueue(queue, PAYLOAD, IDENTITY)
    assert key == expected_key(IDENTITY)
    assert queue_files(queue) == [outbox.entry_path(queue, key).name]


# --------------------------------------------------------------------------
# The coordinator is not pulled in by importing the outbox
# --------------------------------------------------------------------------


#: The module the outbox must not import at module scope, read off the module
#: itself rather than written here.
COORDINATOR_MODULE = story_coordinator.__name__

OUTBOX_SOURCE = Path(outbox.__file__).read_text(encoding="utf-8")


def _imported_names(nodes) -> set[str]:
    names = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def module_scope_imports(source: str) -> set[str]:
    """Every module imported at the top level of a source."""
    return _imported_names(ast.parse(source).body)


def deeper_imports(source: str) -> set[str]:
    """Every module imported anywhere below the top level."""
    tree = ast.parse(source)
    top = {id(node) for node in tree.body}
    return _imported_names(node for node in ast.walk(tree)
                           if id(node) not in top)


def test_the_outbox_has_no_module_scope_import_of_the_coordinator():
    assert COORDINATOR_MODULE not in module_scope_imports(OUTBOX_SOURCE)


def test_the_scan_reports_a_planted_module_scope_import():
    """First control: the same scan over the same source with the import
    planted at module scope reports it."""
    planted = f"import {COORDINATOR_MODULE}\n{OUTBOX_SOURCE}"
    assert COORDINATOR_MODULE in module_scope_imports(planted)


def test_the_coordinator_is_imported_below_module_scope():
    """Second control, and the other half of the story's claim: the capability
    is there, reached from inside a function body. Without this the assertion
    above would pass just as happily against a module that had dropped the
    import altogether."""
    assert COORDINATOR_MODULE in deeper_imports(OUTBOX_SOURCE)


IMPORT_PROBE = (
    "import sys\n"
    "sys.path.insert(0, {orchestration!r})\n"
    "import outbox\n"
    "print(sorted(name for name in ({coordinator!r}, 'outbox')\n"
    "             if name in sys.modules))\n"
)


def test_importing_the_outbox_does_not_pull_the_coordinator_in():
    """The source scan's executable counterpart, in a fresh interpreter.

    The probe reports which of the two names the interpreter has loaded, so the
    coordinator's absence sits beside the outbox's presence in one answer: a
    probe that could see neither would fail on the second name.
    """
    probe = subprocess.run(
        [sys.executable, "-c",
         IMPORT_PROBE.format(orchestration=str(ORCHESTRATION),
                             coordinator=COORDINATOR_MODULE)],
        capture_output=True, text=True, timeout=60)
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout.replace("'", '"')) == ["outbox"]


# --------------------------------------------------------------------------
# A lookup that raises keeps its reason
# --------------------------------------------------------------------------


def test_a_failing_lookup_records_its_reason_and_still_files(queue):
    """The behaviour is unchanged — the entry falls through to filing and
    lands — and what changes is that the reason survives in the summary."""
    key = attempted_once(queue)
    broken = FakeTransport(answer=filing(SECOND_REFERENCE), lookup_raises=True)

    summary = outbox.sync(queue, broken)

    assert broken.looked_up == [key]
    assert broken.filed == [key]
    assert entry_of(queue, key)["state"] == outbox.LANDED
    assert len(summary.notes) == 1, summary.notes
    note = summary.notes[0]
    assert key in note
    # The reason is the fake's own, so the note carries what failed rather
    # than a sentence about lookups in general.
    assert "the provider could not be asked" in note


def test_a_transport_offering_no_lookup_adds_no_note(queue):
    """The absence, with the failing lookup above as its control: the same
    summary field carries a note when something failed, and carries none when
    nothing did, because there was nothing to fail."""
    key = attempted_once(queue)
    no_lookup = FilingOnlyTransport(filing(SECOND_REFERENCE))

    summary = outbox.sync(queue, no_lookup)

    assert no_lookup.filed == [key]
    assert entry_of(queue, key)["state"] == outbox.LANDED
    assert summary.notes == ()


def test_a_lookup_that_answers_with_nothing_adds_no_note(queue):
    """A provider that does not know the key established nothing, but nothing
    raised, so there is no reason to record."""
    key = attempted_once(queue)
    unknowing = FakeTransport(answer=filing(SECOND_REFERENCE), lookups={})

    summary = outbox.sync(queue, unknowing)

    assert unknowing.looked_up == [key]
    assert summary.notes == ()


# --------------------------------------------------------------------------
# The totality belongs to enqueue, and was not moved into its parts
# --------------------------------------------------------------------------


def test_identity_key_still_raises_on_an_identity_it_cannot_render():
    with pytest.raises(TypeError):
        outbox.identity_key(PATH_IDENTITY)
    with pytest.raises(ValueError):
        outbox.identity_key(recursive_payload())


def test_write_entry_still_unlinks_its_temporary_and_reraises(queue):
    key = expected_key(IDENTITY)
    entry = dict(expected_entry(IDENTITY, PATH_PAYLOAD, FIXED_NOW), key=key)

    with pytest.raises(TypeError):
        outbox.write_entry(queue, entry)

    # Neither the entry nor the temporary it was being written through.
    assert queue_files(queue) == []

    # The control: the same call with a payload json can render writes one
    # file under the entry's name, so the emptiness above is the unlink and
    # not a listing pointed at the wrong directory.
    outbox.write_entry(queue, expected_entry(IDENTITY, PAYLOAD, FIXED_NOW))
    assert queue_files(queue) == [outbox.entry_path(queue, key).name]
