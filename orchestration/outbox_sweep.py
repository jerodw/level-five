"""One drain, three call sites, and two entry points whose types differ.

The outbox is drained opportunistically, at the points where a failure to
drain costs nothing: a sweep in the l5-run pre-flight, a sweep after the
completion commit, and — reading the queue as data rather than through this
module — the read-only report in l5-status. `l5-sync` stays the explicit entry
point; these are the paths that mean a developer rarely has to run it.

The drain lives here so the sites cannot drift, which is the shape story-080
used for the two capacity resumes. What that leaves is a single module holding
two entry points with **deliberately different types**, because they answer to
different callers:

    build_transport(config, target_root) -> (transport, problem)
        The refusable build. `l5-sync` is a terminal invocation the developer
        asked for, so a configuration it cannot obey is refused rather than
        worked around, and the problem it returns is what refuses it.

    sweep(target_root, config, harness_root, *, run_dir=None) -> Summary
        The total sweep. It returns a Summary on every path and there is no
        parameter, no return value and no exception by which it could tell a
        caller to stop.

**This sweep must not refuse.** Every other pre-flight in the coordinator
refuses, and that consistency is the point of them; this one is the exception
and the exception is the mechanism. A sweep at the top of a run is an attempt
to be helpful with a network that may be down. If it can refuse, the outbox
becomes exactly the blocker the whole design exists to prevent: a queue that
cannot be drained would stop the story that was going to drain it. A later
reader who restores consistency by giving `sweep` a way to say no — a raise, a
boolean, a problem string a call site could branch on — has removed the
guarantee the queue exists to make. The two functions are separate for that
reason and not for tidiness: the refusable one is the one `l5-sync` calls, and
nothing inside a run may reach it.

The configuration reads live here rather than in the queue module, which reads
no configured key at all, and they are written in the literal-resolvable idiom
the rest of the harness's reads are written in so the declared-keys scan can
see them.
"""
from __future__ import annotations

from pathlib import Path

import outbox

#: How long a sync command may run before it is killed.
TIMEOUT_KEY = "sync_timeout_seconds"

#: How many pending entries one sweep may attempt to file.
LIMIT_KEY = "sweep_max_entries"

#: What a sweep attempts when the target declares no limit. A count rather
#: than a duration, and small deliberately: the worst case of one sweep is
#: this multiplied by sync_timeout_seconds, and an opportunistic sweep sits in
#: front of a run that a developer is waiting on.
DEFAULT_MAX_ENTRIES = 20


def _transport_module():
    """The transport module, imported where it is used rather than at module
    scope.

    Two reasons, the same pair the queue module's own deferred import of the
    coordinator carries. `render` and the queue reading beside it are wanted by
    l5-status, which builds no transport and spawns no subprocess for the
    outbox, and a module-scope import would pull the one module in the harness
    that does spawn one into that path. And a caller that never builds a
    transport pays nothing for the capability.
    """
    import command_transport

    return command_transport


def build_transport(config: dict, target_root: Path):
    """The transport this configuration describes, or the reason it is refused.

    Returns `(transport, problem)`. No configured command is not a problem: a
    target that files nothing is the ordinary case, and it gets no transport
    and no complaint. A sync_timeout_seconds that is not a positive number is a
    problem, and it is refused here rather than obeyed — a timeout of zero or
    of a word is no timeout at all, which is the failure the bound exists to
    prevent.

    This is the **refusable** half of the module, and the only caller that may
    reach it is a terminal invocation a developer asked for. `sweep` below
    consumes the problem rather than passing it on, because a sweep that could
    refuse would be the blocker the queue exists to prevent.
    """
    command_transport = _transport_module()
    command = config.get("sync_command")
    declared = config.get(TIMEOUT_KEY)
    if declared is None:
        timeout = command_transport.DEFAULT_TIMEOUT_SECONDS
    else:
        try:
            timeout = float(declared)
        except (TypeError, ValueError):
            timeout = None
        if timeout is None or timeout <= 0:
            return None, (
                f"{TIMEOUT_KEY}: {declared!r} is not a positive number of "
                "seconds, and a sync command must be bounded in time"
            )
    if not command:
        return None, ""
    return command_transport.CommandTransport(
        command=command, timeout=timeout, cwd=target_root
    ), ""


def sweep_limit(config: dict):
    """How many entries one sweep may attempt, or the reason it is refused.

    Returns `(limit, problem)`, in `build_transport`'s shape and for its
    reason: an explicit drain refuses a bound it cannot obey, and an
    opportunistic one notes it and files nothing. A bound that is not a
    positive integer cannot bound anything, and falling back to the default
    would obey a number the target did not declare.
    """
    declared = config.get(LIMIT_KEY)
    if declared is None:
        return DEFAULT_MAX_ENTRIES, ""
    try:
        limit = int(str(declared))
    except (TypeError, ValueError):
        limit = None
    if limit is None or limit <= 0:
        return None, (
            f"{LIMIT_KEY}: {declared!r} is not a positive integer, and a "
            "sweep must be bounded in how many entries it attempts"
        )
    return limit, ""


def drain(queue: Path, transport, harness_root: Path | None = None,
          *, limit: int | None = None) -> outbox.Summary:
    """The one call into the queue's drain that this repository makes.

    Both entry points go through here — `sweep` below with whatever the total
    resolution produced, and `l5-sync` with whatever survived its refusals —
    so the drain itself is written once and the two halves cannot drift into
    passing the queue different things. It decides nothing: what a transport
    and a limit are is settled above it, by the caller whose refusal rules
    apply.
    """
    return outbox.sync(queue, transport, harness_root=harness_root, limit=limit)


def render(summary: outbox.Summary) -> str:
    """What a sweep did, in one line.

    One rendering shared by the sites that mention a queue in passing — the
    two coordinator sweeps and the l5-status listing — so they say the same
    thing about the same queue rather than each composing their own sentence.
    `l5-sync` keeps its own multi-line report: a terminal invocation the
    developer asked for says more than an aside inside a run.
    """
    line = (
        f"outbox: landed {summary.landed}, pending {summary.pending}, "
        f"failed {summary.failed}, poisoned {summary.poisoned}"
    )
    if not summary.transport:
        # "was used" rather than "is configured": l5-status renders through
        # here and reads no configuration key at all, so a queue it reports
        # must read identically whether or not sync_command is set.
        line += "; no transport was used, so nothing was filed"
    for note in summary.notes:
        line += f"; {note}"
    return line


# This sweep must not refuse, and restoring consistency with the refusing
# pre-flights around it would defeat the mechanism. It is the total half of the
# module: a Summary on every path, no exception out of any of them, and no
# value a call site could read as "stop". A sweep is an attempt to be helpful
# with a network that may be down; a sweep that could say no would make an
# undrainable queue the thing that blocks the run that was going to drain it.
def sweep(target_root: Path, config: dict, harness_root: Path | None = None,
          *, run_dir: Path | None = None) -> outbox.Summary:
    """Drain the queue opportunistically, and tell no caller to stop.

    Every way this can go wrong comes back as a note on the returned summary:
    a timeout that is not a positive number, a limit that is not a positive
    integer, a transport that is absent, one that raises on every entry, one
    that answers with nonsense, and a queue directory that cannot be listed.
    Where the build reports a problem the queue is swept with **no transport**,
    so the entries are still read and reported and nothing is filed, and the
    problem is carried into the summary's notes rather than raised.

    When `run_dir` names a run directory the sweep reports what it did into
    that run's events.log, through the coordinator's shared append. Nothing
    about the result is read by either coordinator call site: no branch, no
    early return, no status.
    """
    try:
        transport, problem = build_transport(config, target_root)
        limit, limit_problem = sweep_limit(config)
        if problem or limit_problem:
            # A configuration this sweep cannot obey files nothing rather than
            # refusing anything. The queue is still read and still reported, so
            # a developer sees the queue and the reason in one place.
            transport = None
            limit = None
        summary = drain(
            outbox.queue_dir(target_root),
            transport,
            harness_root=harness_root,
            limit=limit,
        )
        notes = tuple(
            note for note in (problem, limit_problem) if note
        ) + summary.notes
        summary = replace_notes(summary, notes)
    except Exception as error:  # noqa: BLE001 - the totality is the guarantee
        # Nothing above this is expected to raise — the queue module is total
        # and the two resolvers are pure — but "expected" is not the standard
        # this function is held to. A Summary on every path means every path,
        # including one nobody has thought of.
        summary = outbox.Summary(
            transport=False,
            notes=(f"the sweep could not run: {error}",),
        )
    if run_dir is not None:
        _report(run_dir, summary)
    return summary


def replace_notes(summary: outbox.Summary, notes) -> outbox.Summary:
    """The same summary carrying a different set of notes.

    A small helper rather than a mutation, because `Summary` is frozen: the
    build's problems belong on the summary a call site reads, and they are
    known before the drain rather than during it.
    """
    return outbox.Summary(
        landed=summary.landed,
        pending=summary.pending,
        failed=summary.failed,
        poisoned=summary.poisoned,
        landed_keys=summary.landed_keys,
        pending_keys=summary.pending_keys,
        failed_keys=summary.failed_keys,
        poisoned_files=summary.poisoned_files,
        transport=summary.transport,
        notes=tuple(notes),
    )


def worth_saying(summary: outbox.Summary) -> bool:
    """Whether this sweep found anything a reader of the log needs told.

    A sweep of a queue that holds nothing and met no problem did nothing, and
    saying so on every run of every target — almost all of which file nothing
    — would put a line in every run's events.log that is only ever the same
    line. That is the rule the history prune beside it already follows, where
    only an actual drop is announced, and the suite census, where a target
    configuring no census command announces nothing.
    """
    return bool(
        summary.landed or summary.pending or summary.failed
        or summary.poisoned or summary.notes
    )


def _report(run_dir: Path, summary: outbox.Summary) -> None:
    """Say what the sweep did in the run's own events.log.

    Through the coordinator's shared append, so the line lands in the one-line
    format the log already carries rather than through a second writer of it,
    and imported inside the body because the coordinator imports this module.
    Guarded for the reason everything else here is guarded: a sweep that could
    not report is still a sweep that must not stop a run.
    """
    if not worth_saying(summary):
        return
    try:
        from story_coordinator import append_event

        append_event(Path(run_dir), render(summary), kind="note")
    except Exception:  # noqa: BLE001 - reporting may not become the failure
        pass
