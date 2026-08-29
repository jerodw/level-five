"""Independent validation for story-089: the outbox never blocks a run.

Written from the story's acceptance criteria rather than from the
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
  * "no module a run executes reaches the outbox" sits beside the same scan
    over a source with the call planted in it, which the scan must report.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import outbox
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
# No drain site inside a run
# --------------------------------------------------------------------------


def modules_reaching_the_outbox(sources: dict[str, str]) -> list[str]:
    """Which of `sources` mentions the outbox module by name."""
    return sorted(name for name, text in sources.items()
                  if outbox.__name__ in text)


def orchestration_sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(ORCHESTRATION.glob("*.py"))
            if path.name != f"{outbox.__name__}.py"}


def test_no_module_a_run_executes_reaches_the_outbox():
    assert modules_reaching_the_outbox(orchestration_sources()) == []


def test_the_scan_reports_a_planted_call_site():
    """Control: the emptiness above must mean no module reaches the queue, not
    that the scan has stopped seeing anything."""
    sources = orchestration_sources()
    victim = "story_coordinator.py"
    assert victim in sources
    sources[victim] += f"\nimport {outbox.__name__}\n"
    assert modules_reaching_the_outbox(sources) == [victim]


def test_the_only_drain_site_the_repository_ships_is_the_script():
    drains = sorted(
        path.name
        for path in sorted(list(ORCHESTRATION.glob("*.py")) +
                           [p for p in SCRIPTS.iterdir() if p.is_file()])
        if f"{outbox.__name__}.sync(" in path.read_text(encoding="utf-8")
    )
    assert drains == ["l5-sync"]


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
