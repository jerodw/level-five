"""Read-only status snapshot of story runs.

All logic for the l5-status command lives here; the script only parses
arguments and delegates. Nothing in this module writes to disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import harness_config
import outbox
import outbox_sweep
import story_coordinator

TAIL_LINES = 10

_LIST_HEADERS = ("STORY", "STATUS", "STAGE", "RETRIES")


class RunStatusError(Exception):
    """A status request that cannot be answered (unknown run, bad state)."""


def _runs_dir(target_root: Path) -> Path:
    config = harness_config.load_config(target_root)
    return target_root / config.get("runs_dir", ".harness/runs")


def _try_load_state(run_dir: Path) -> story_coordinator.RunState | None:
    """Load a run's state, returning None when it is missing or unparseable."""
    try:
        return story_coordinator.load_state(run_dir)
    except Exception:
        return None


def tail_events(run_dir: Path, count: int = TAIL_LINES) -> list[str]:
    path = run_dir / "events.log"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()[-count:]


def format_queue(target_root: Path) -> list[str]:
    """The outbox as it stands — both directories — read as data and nothing else.

    The queue first, which is what still has to be filed, then the receipt
    index beneath it through `format_receipts` below.

    l5-status stays instant and works offline, so this builds no transport,
    spawns no subprocess for the outbox and files nothing: it reads the queue
    through `outbox.entry_files` and `outbox.read_entry` alone, which is why a
    queue holding pending and failed entries reports identically whether or
    not sync_command is configured — nothing here looks at that key. The
    headline goes through the sweep module's own renderer, so the aside a run
    logs and the section a listing prints say the same thing about the same
    queue.

    A queue directory that does not exist is an empty queue rather than an
    error, which `entry_files` already answers. One that cannot be listed is
    reported as such: the runs the developer asked for are still printed, and
    the queue says it could not be read rather than failing the command.
    """
    queue = outbox.queue_dir(target_root)
    try:
        files = outbox.entry_files(queue)
    except OSError as error:
        return [f"outbox: the queue could not be read: {error}", f"  at {queue}"]

    pending: list[str] = []
    landed: list[str] = []
    failed: list[tuple[str, str]] = []
    poisoned: list[outbox.Poisoned] = []
    for path in files:
        entry, problems = outbox.read_entry(path)
        if entry is None:
            poisoned.append(outbox.Poisoned(path.name, tuple(problems)))
            continue
        state = entry["state"]
        if state == outbox.LANDED:
            landed.append(entry["key"])
        elif state == outbox.FAILED:
            failed.append((entry["key"], entry.get("last_error", "")))
        else:
            pending.append(entry["key"])

    summary = outbox.Summary(
        landed=len(landed),
        pending=len(pending),
        failed=len(failed),
        poisoned=len(poisoned),
        landed_keys=tuple(landed),
        pending_keys=tuple(pending),
        failed_keys=tuple(key for key, _ in failed),
        poisoned_files=tuple(poisoned),
        # Nothing was filed, because nothing here files: this is a report of
        # the queue rather than a drain of it.
        transport=False,
    )
    lines = [outbox_sweep.render(summary), f"  at {queue}"]
    for key, last_error in failed:
        lines.append(f"  failed: {key}")
        lines.append(f"      {last_error or 'no error was recorded'}")
    for entry in poisoned:
        lines.append(f"  poisoned: {entry.path}")
        for problem in entry.problems:
            lines.append(f"      {problem}")
    return lines + format_receipts(target_root)


def format_receipts(target_root: Path) -> list[str]:
    """The receipt index as it stands, beside the queue and read the same way.

    The index is the other half of the outbox: the permanent record of what
    this harness has already filed, which is what makes local dedupe work with
    no network. It is read exactly as the queue above it is — through
    `outbox.entry_files` and `outbox.read_entry` alone, no transport, no
    subprocess and no configuration key — so naming it here costs the listing
    nothing it was not already paying.

    A poisoned file is named and counted here for the reason it is in the queue
    section: it is left byte-for-byte as it is, and a reader is told it is
    there rather than left to infer it from a count that does not add up.
    """
    receipts = outbox.receipts_dir(target_root)
    try:
        files = outbox.entry_files(receipts)
    except OSError as error:
        return [f"receipts: the index could not be read: {error}",
                f"  at {receipts}"]

    held = 0
    poisoned: list[outbox.Poisoned] = []
    for path in files:
        entry, problems = outbox.read_entry(path)
        if entry is None:
            poisoned.append(outbox.Poisoned(path.name, tuple(problems)))
            continue
        held += 1

    lines = [f"receipts: {held} held, poisoned {len(poisoned)}",
             f"  at {receipts}"]
    for entry in poisoned:
        lines.append(f"  poisoned: {entry.path}")
        for problem in entry.problems:
            lines.append(f"      {problem}")
    return lines


def format_listing(target_root: Path) -> str:
    runs_dir = _runs_dir(target_root)
    run_dirs = (
        sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        if runs_dir.is_dir()
        else []
    )
    queue_section = format_queue(target_root)
    if not run_dirs:
        return "\n".join(["no runs found", ""] + queue_section)

    rows = [_LIST_HEADERS]
    for run_dir in run_dirs:
        state = _try_load_state(run_dir)
        if state is None:
            rows.append((run_dir.name, "unreadable", "-", "-"))
        else:
            rows.append(
                (
                    state.story_id,
                    state.status,
                    state.current_stage or "-",
                    str(state.retry_count),
                )
            )
    widths = [max(len(row[i]) for row in rows) for i in range(len(_LIST_HEADERS))]
    listing = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    ]
    # The queue below the runs, in every case: the runs are what the developer
    # asked for and the queue is the aside, so a queue that could not be read
    # costs the listing nothing.
    return "\n".join(listing + [""] + queue_section)


def format_detail(target_root: Path, story_id: str) -> str:
    runs_dir = _runs_dir(target_root)
    run_dir = runs_dir / story_id
    if not run_dir.is_dir():
        raise RunStatusError(f"no run found for '{story_id}' under {runs_dir}")
    state = _try_load_state(run_dir)
    if state is None:
        raise RunStatusError(f"state.json for '{story_id}' is missing or unreadable")

    fields = [
        ("story id", state.story_id),
        ("status", state.status),
        ("current stage", state.current_stage or "-"),
        ("retry count", str(state.retry_count)),
        ("branch", state.branch),
        ("verification iterations", str(state.verification_iterations)),
        # The two counters above are scoped to the entry now running: a resume
        # restores the run's attempt allowance by zeroing them. Read alone they
        # would understate a resumed run, so the entry index and the total the
        # records actually hold sit beside them rather than in place of them.
        ("entry index", str(state.resume_count)),
        (
            "attempts this run",
            str(story_coordinator.accumulated_attempts(run_dir, state)),
        ),
    ]
    width = max(len(label) for label, _ in fields)
    lines = [f"{label.ljust(width)}  {value}" for label, value in fields]

    events = tail_events(run_dir)
    lines.append("")
    lines.append(f"last {TAIL_LINES} events:")
    lines.extend(events if events else ["(no events recorded)"])
    return "\n".join(lines)


def main(target_root: Path, story_id: str | None = None) -> int:
    """Print the requested status view; return a process exit code."""
    try:
        if story_id is None:
            print(format_listing(target_root))
        else:
            print(format_detail(target_root, story_id))
    except RunStatusError as error:
        print(f"l5-status: {error}", file=sys.stderr)
        return 1
    return 0
