"""Read-only status snapshot of story runs.

All logic for the l5-status command lives here; the script only parses
arguments and delegates. Nothing in this module writes to disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import harness_config
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


def format_listing(target_root: Path) -> str:
    runs_dir = _runs_dir(target_root)
    run_dirs = (
        sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        if runs_dir.is_dir()
        else []
    )
    if not run_dirs:
        return "no runs found"

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
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    )


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
