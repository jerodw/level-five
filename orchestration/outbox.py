"""A durable local queue for work the harness wants to file externally.

One guarantee holds the whole module up: **a failure to file never blocks a
story, never delays a commit and never refuses a run**. Durability is
unconditional and the network is opportunistic. `enqueue` is total over the
payload and the identity it is handed — an item it cannot render is refused
rather than coerced, and the run is kept; `sync` drains the queue through an
injected transport and returns a summary whatever happens; neither raises into
its caller, for any transport behaviour — one that fails, one that raises, one
that returns nonsense, and one that is absent. Every other guarantee here rests
on that one.

A refused item is not a silent one. Every drop produces a message, on stderr
always and in the run's events.log when `enqueue` is given a run directory, so
a later reader sees that something went wrong rather than inferring it from an
absence. Nothing is coerced on the way: a `default=str` would let an entry say
something the producer did not say, and a transport would deliver it later as
though the producer had said it. Refusing loses the item, which is the failure
mode this module already documents and accepts for a queue that cannot be
written to.

The failure that decides the design is the **ambiguous write**: the request
arrived, the item was created, and the response never came back. The existence
of a local file cannot distinguish that from a clean failure, so a naive sweep
re-files it and leaves a human reconciling duplicates — worse than losing one.
Every entry therefore carries a client-generated idempotency key derived from a
caller-declared *identity* rather than from the payload, and an entry that has
been attempted is looked up by that key before it is filed again. The provider
is authoritative for what was filed; local state is only evidence of what was
attempted.

The transport is injected, the way the agent runner and the sleep already are,
so every state the queue can be in is proven against a fake and nothing here
reaches a network. What a transport must provide is two operations:

    file(entry) -> Filing      files an entry and answers with a reference,
                               a terminal error, or a transient error
    look_up(key) -> str        answers with the reference the provider holds
                               for that key, or "" for a key it does not know

The three answers a filing can give map onto the three states an entry can be
in: a reference lands it, a terminal error fails it, a transient error leaves
it pending with the attempt counted and the error recorded. A transport that
*raises* is treated as a transient failure and the exception does not escape.

Nothing enqueues anything yet — the producer is a later story, and `l5-sync`
is the only drain site shipped here. That is why no sweep runs inside a story
run: there is no queue accumulating unattended for a forgotten one to strand,
and the story that adds a producer is the first point at which an automatic
sweep earns its call site. It can add one without touching this contract,
because `sync` never raises into its caller.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import schema_validator

#: Where the queue lives beneath a target repository. A constant rather than a
#: configured key: a key would have to be declared in the harness
#: configuration schema and proven to govern, and nothing about this queue is
#: a target's choice to make. It is gitignored rather than merely untracked,
#: because the clean-tree pre-flight reads `git status --porcelain`, which
#: reports untracked files — a pending entry that was only untracked would
#: refuse the next run, and the mechanism whose whole purpose is never to
#: block would be the thing blocking.
QUEUE_DIR = (".harness", "outbox")

#: The shape an entry is written in and read back against. One file, so what
#: the outbox writes and what it decides is poisoned cannot drift.
ENTRY_SCHEMA = "outbox-entry"

#: The three states, and what each one means. `pending` is written but not
#: filed. `landed` is confirmed by the provider, records the reference, and
#: drops the payload it no longer needs. `failed` is terminal and is not
#: retried unattended.
PENDING = "pending"
LANDED = "landed"
FAILED = "failed"

#: The suffix a half-written entry wears while it is being written. It is not
#: the suffix the queue is read by, so a process killed mid-write leaves
#: nothing a sync will read, rather than half an entry it would call poisoned.
PARTIAL_SUFFIX = ".partial"

#: What the queue is read by.
ENTRY_SUFFIX = ".json"

#: The same form events.log, execution-history.json and the mandate block use,
#: so a timestamp on an entry and a timestamp in a log are one form.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

#: What a drop says. Composed in one place so the reasons a drop can have
#: cannot disagree about what a drop looks like.
DROP_MESSAGE = "outbox: dropped an item for identity {identity}: {reason}"

#: What an identity renders as when even repr will not answer for it.
UNRENDERABLE_IDENTITY = "<an identity that cannot be rendered>"


def queue_dir(target_root: Path) -> Path:
    """The queue directory beneath a target repository."""
    return target_root.joinpath(*QUEUE_DIR)


# --------------------------------------------------------------------------
# The transport contract, expressed as the answers a filing can give
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Filing:
    """One answer to an attempt to file an entry.

    Three answers and three states, decided by presence rather than by a
    fourth field naming which of them this is: a `reference` lands the entry,
    an `error` marked `terminal` fails it, and an `error` that is not marked
    terminal leaves it pending for a later sync. An answer carrying neither —
    a transport that returned nonsense — is read as a transient failure, which
    is the reading that loses nothing and duplicates nothing.
    """

    reference: str = ""
    error: str = ""
    terminal: bool = False


def filed(reference: str) -> Filing:
    """The provider took it and named it. The entry lands."""
    return Filing(reference=reference)


def refused(error: str) -> Filing:
    """The provider refused it on its own terms. The entry fails, terminally."""
    return Filing(error=error, terminal=True)


def deferred(error: str) -> Filing:
    """The attempt did not settle. The entry stays pending for a later sync."""
    return Filing(error=error)


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------


def identity_key(identity) -> str:
    """The idempotency key for an identity: a digest of the identity alone.

    A sha256 over the canonical JSON of the mapping — sorted keys, no
    incidental whitespace — in the shape `story_digest` already uses. Two
    calls carrying the same identity therefore produce the same key however
    their payloads differ, which is the whole point of the identity being
    separate from the payload: an entry already filed is not filed again
    because its payload gained a field.
    """
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now(now: float | None = None) -> str:
    return time.strftime(
        TIMESTAMP_FORMAT, time.localtime(now if now is not None else time.time())
    )


# --------------------------------------------------------------------------
# Writing an entry
# --------------------------------------------------------------------------


def entry_path(queue: Path, key: str) -> Path:
    """Where the entry for a key lives. One key, one file, one name."""
    return queue / f"{key}{ENTRY_SUFFIX}"


def write_entry(queue: Path, entry: dict) -> Path:
    """Write one entry so that a process killed mid-write loses nothing.

    Through a temporary file in the same directory, replaced into place, so
    what is on disk is either the previous entry or the new one and never a
    half of either. The temporary carries a suffix the queue is not read by,
    so even the window before the replace holds nothing a sync would meet.
    """
    queue.mkdir(parents=True, exist_ok=True)
    destination = entry_path(queue, entry["key"])
    handle, temporary = tempfile.mkstemp(
        dir=str(queue), prefix=entry["key"], suffix=PARTIAL_SUFFIX
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as open_file:
            json.dump(entry, open_file, indent=2, sort_keys=True)
            open_file.write("\n")
            open_file.flush()
            os.fsync(open_file.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination


def rendered_identity(identity) -> str:
    """An identity rendered for a drop message, by means that cannot fail.

    Through `repr` rather than through json, because the identity in a drop
    message is by definition one json could not render, and a message that
    raised while explaining a drop would be the failure this reporting exists
    to remove. An object whose own `repr` raises is not rendered either, and
    then it is named as unrenderable rather than allowed to escape.
    """
    try:
        return repr(identity)
    except Exception:  # noqa: BLE001 - a total rendering has no other answer
        return UNRENDERABLE_IDENTITY


def _report_drop(message: str, run_dir: Path | None) -> None:
    """Say that an item was dropped, on every sink that can be reached.

    stderr always, and the run's events.log too when a run directory is given
    — through the coordinator's shared append, so a drop reaches the log in
    the one-line format the log already carries rather than through a second
    writer of it. That import is inside the function body for two reasons: the
    producer story will have a module the coordinator reaches importing this
    one, and an import at module scope would close that cycle; and a call that
    never drops anything then pays nothing for the capability.

    Every sink is guarded on its own, so a sink that fails degrades to the
    next one and a failure to report is never a failure to enqueue. Where no
    sink can be reached the drop is unreported — the same case as a queue
    directory that cannot be written to, stated here rather than hidden.
    """
    try:
        print(message, file=sys.stderr)
    except Exception:  # noqa: BLE001 - reporting may not become the failure
        pass
    if run_dir is None:
        return
    try:
        from story_coordinator import append_event

        append_event(Path(run_dir), message)
    except Exception:  # noqa: BLE001 - reporting may not become the failure
        pass


def enqueue(queue: Path, payload: dict, identity: dict, now: float | None = None,
            *, run_dir: Path | None = None) -> str:
    """Write one pending entry, total over the payload and the identity given.

    Whatever it is handed, it returns: an item that cannot be rendered as an
    entry is refused and the run is kept. Nothing is written, nothing is
    raised, and the empty string comes back to say that nothing landed. The
    key — the digest of the identity — is returned only once the entry is on
    disk, so calling this twice with one identity writes one entry twice
    rather than two entries, the second write replacing the first at the same
    name, which is what an idempotency key is for. Nothing is filed here and
    no transport is consulted: durability is what this call buys, and the
    network is somebody else's opportunity.

    What a drop costs is stated rather than hidden: the item is lost. A
    payload or an identity json cannot render, a recursive structure, and a
    queue that cannot be written to are all that same loss, and none of them
    is coerced into something writable — an entry that says something the
    producer did not say is delivered later as though the producer had said
    it. Losing one item is the lesser failure; the alternative is a producer
    inside a run raising, which is the queue becoming the thing that stops a
    story, the one outcome every part of this module exists to prevent.

    A refused item is not a silent one. Every drop is reported on stderr, and
    in the run's events.log as well when `run_dir` names one.
    """
    try:
        key = identity_key(identity)
        stamped = _now(now)
        write_entry(
            queue,
            {
                "key": key,
                "identity": identity,
                "state": PENDING,
                "payload": payload,
                "attempts": 0,
                "created_at": stamped,
                "updated_at": stamped,
            },
        )
    except (OSError, TypeError, ValueError) as error:
        # One drop site rather than three, so the queue that cannot be
        # written to, the value json cannot render and the recursive
        # structure cannot disagree about what a drop looks like. TypeError
        # is what json raises for a value it cannot render and ValueError is
        # what it raises for a structure that refers to itself; the key
        # derivation is inside the guard because identity_key raises the
        # first of those before write_entry is ever reached.
        _report_drop(
            DROP_MESSAGE.format(identity=rendered_identity(identity), reason=error),
            run_dir,
        )
        return ""
    return key


# --------------------------------------------------------------------------
# Reading the queue back
# --------------------------------------------------------------------------


def entry_files(queue: Path) -> list[Path]:
    """Every file the queue is read by, in a stable order.

    A directory that does not exist holds no entries rather than being an
    error: a target that has never enqueued anything has an empty queue, and
    saying so is not a failure to report.
    """
    if not queue.is_dir():
        return []
    return sorted(
        path for path in queue.iterdir()
        if path.is_file() and path.name.endswith(ENTRY_SUFFIX)
    )


def read_entry(path: Path, harness_root: Path | None = None):
    """One entry, or the reason it is poisoned.

    Returns `(entry, problems)`. A file that is not valid JSON, that is not an
    object, or that does not satisfy the entry schema is poisoned: `entry` is
    None and `problems` says which. Poisoned is a decidable condition rather
    than a judgement precisely because the schema decides it, and the schema
    is the same file `enqueue` writes to.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return None, [f"could not be read: {error}"]
    try:
        entry = json.loads(text)
    except json.JSONDecodeError as error:
        return None, [f"is not valid JSON: {error}"]
    if not isinstance(entry, dict):
        return None, ["is not a JSON object"]
    schema = schema_validator.load_schema(ENTRY_SCHEMA, harness_root)
    problems = schema_validator.validate(entry, schema)
    if problems:
        return None, problems
    return entry, []


# --------------------------------------------------------------------------
# What a sync did
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Poisoned:
    """A file in the queue that is not an entry, and what is wrong with it.

    It is named and counted and nothing else: never repaired, never rewritten,
    never deleted. The harness cannot know what a human meant by it.
    """

    path: str
    problems: tuple[str, ...]


@dataclass(frozen=True)
class Summary:
    """What a sync found and what it did, collected rather than printed.

    The module decides nothing about a terminal: it returns counts, the keys
    behind the two states a sweep cannot resolve unattended, and the poisoned
    files it left alone, and the caller decides how to say it. `blocked` is a
    fact about the queue rather than a presentation of one — whether the queue
    holds something no later sweep will clear on its own.
    """

    landed: int = 0
    pending: int = 0
    failed: int = 0
    poisoned: int = 0
    landed_keys: tuple[str, ...] = ()
    pending_keys: tuple[str, ...] = ()
    failed_keys: tuple[str, ...] = ()
    poisoned_files: tuple[Poisoned, ...] = ()
    transport: bool = True
    notes: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        """Whether the queue holds a failed or poisoned entry.

        Pending is not blocked: an entry a later sync will retry is the queue
        working as intended, and a run that left only pending entries drained
        as cleanly as it could.
        """
        return bool(self.failed_keys or self.poisoned_files)


@dataclass
class _Tally:
    """The mutable half of a summary, so `sync` has one place to record into."""

    landed: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    poisoned: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def summary(self, transport: bool) -> Summary:
        return Summary(
            landed=len(self.landed),
            pending=len(self.pending),
            failed=len(self.failed),
            poisoned=len(self.poisoned),
            landed_keys=tuple(self.landed),
            pending_keys=tuple(self.pending),
            failed_keys=tuple(self.failed),
            poisoned_files=tuple(self.poisoned),
            transport=transport,
            notes=tuple(self.notes),
        )


# --------------------------------------------------------------------------
# The drain
# --------------------------------------------------------------------------


def sync(queue: Path, transport=None, harness_root: Path | None = None,
         now: float | None = None) -> Summary:
    """Drain the queue through a transport, and never raise into the caller.

    Every path through this function returns a summary. A transport that is
    absent files nothing and the queue is reported as it stands; a transport
    that raises is treated as a transient failure and the exception does not
    escape; a transport that answers with nonsense is read as a transient
    failure too. That total-ness is not politeness — it is the guarantee the
    whole queue exists to make, and a caller may call this anywhere in a run
    knowing it cannot be the thing that stops one.
    """
    tally = _Tally()
    try:
        files = entry_files(queue)
    except OSError as error:
        tally.notes.append(f"the queue could not be listed: {error}")
        return tally.summary(transport is not None)
    for path in files:
        try:
            _sync_one(queue, path, transport, tally, harness_root, now)
        except Exception as error:  # noqa: BLE001 - the guarantee is the point
            tally.notes.append(f"{path.name}: {error}")
            tally.pending.append(path.stem)
    return tally.summary(transport is not None)


def _sync_one(queue: Path, path: Path, transport, tally: _Tally,
              harness_root: Path | None, now: float | None) -> None:
    entry, problems = read_entry(path, harness_root)
    if entry is None:
        # Left exactly as it is: named, counted, and otherwise untouched.
        tally.poisoned.append(Poisoned(path.name, tuple(problems)))
        return
    state = entry["state"]
    if state == LANDED:
        tally.landed.append(entry["key"])
        return
    if state == FAILED:
        # Terminal. A sweep does not file it again, and says so by naming it.
        tally.failed.append(entry["key"])
        return
    if transport is None:
        tally.pending.append(entry["key"])
        return
    _file_one(queue, entry, transport, tally, now)


def _file_one(queue: Path, entry: dict, transport, tally: _Tally,
              now: float | None) -> None:
    """File one pending entry, resolving an ambiguous write by asking first."""
    if entry["attempts"]:
        # The entry has been offered to a provider before, so its absence
        # here says nothing about whether it arrived. Ask about the key
        # rather than risk a duplicate a human would have to reconcile.
        reference, problem = _look_up(transport, entry["key"])
        if problem:
            # The lookup established nothing and the entry falls through to
            # filing exactly as it did before, but the reason survives rather
            # than being swallowed by the helper that met it.
            tally.notes.append(f"{entry['key']}: {problem}")
        if reference:
            _land(queue, entry, reference, tally, now)
            return
    answer = _file(transport, entry)
    if answer.reference:
        _land(queue, entry, answer.reference, tally, now)
        return
    entry["attempts"] = entry["attempts"] + 1
    entry["last_error"] = answer.error or "the transport answered with nothing"
    entry["updated_at"] = _now(now)
    if answer.terminal:
        entry["state"] = FAILED
        write_entry(queue, entry)
        tally.failed.append(entry["key"])
        return
    entry["state"] = PENDING
    write_entry(queue, entry)
    tally.pending.append(entry["key"])


def _land(queue: Path, entry: dict, reference: str, tally: _Tally,
          now: float | None) -> None:
    """Record the reference and drop the payload the entry no longer needs.

    The provider is authoritative for what was filed, so once it has named
    what it holds the local copy of the body is evidence of nothing.
    """
    entry["state"] = LANDED
    entry["reference"] = reference
    entry.pop("payload", None)
    entry["updated_at"] = _now(now)
    write_entry(queue, entry)
    tally.landed.append(entry["key"])


def _file(transport, entry: dict) -> Filing:
    """The transport's filing operation, with a raise read as transient.

    A transport that raises has told us nothing about whether the request
    arrived, which is exactly the ambiguous write — so it leaves the entry
    pending with the attempt counted, and the next sync asks about the key.
    """
    try:
        answer = transport.file(entry)
    except Exception as error:  # noqa: BLE001 - a raise is a transient failure
        return deferred(f"the transport raised: {error}")
    if not isinstance(answer, Filing):
        return deferred("the transport answered with something that is not a filing")
    return answer


def _look_up(transport, key: str) -> tuple[str, str]:
    """What the provider holds for a key, and the reason it could not say.

    Returns `(reference, problem)`. A lookup that raises, that is not offered
    at all, or that answers with something other than a reference establishes
    nothing, and the entry is filed as it would have been without the lookup.
    That is the safe direction: the filing operation is what a provider is
    asked to make idempotent, and the key is what it is asked to make it
    idempotent on.

    What changed is that a lookup that *raises* carries its reason back rather
    than having it swallowed here, so the caller can record it. A transport
    offering no lookup at all reports no problem, because nothing failed —
    there was nothing to fail.
    """
    look_up = getattr(transport, "look_up", None)
    if look_up is None:
        return "", ""
    try:
        answer = look_up(key)
    except Exception as error:  # noqa: BLE001 - establishing nothing is the answer
        return "", f"the lookup raised: {error}"
    return (answer if isinstance(answer, str) else ""), ""
