import json
from dataclasses import asdict
from pathlib import Path

import pytest

from conftest import load_mutant

import run_status
import story_coordinator
from story_coordinator import RunState

CONFIG = """\
runs_dir: .harness/runs
"""

RUN_STATUS_PATH = Path(story_coordinator.__file__).resolve().parent / "run_status.py"


@pytest.fixture
def status_root(tmp_path: Path) -> Path:
    root = tmp_path / "status-target"
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(CONFIG, encoding="utf-8")
    return root


def make_run(root: Path, story_id: str, **overrides) -> Path:
    run_dir = root / ".harness" / "runs" / story_id
    run_dir.mkdir(parents=True)
    state = RunState(story_id=story_id, branch=f"story/{story_id}", **overrides)
    (run_dir / "state.json").write_text(
        json.dumps(asdict(state)) + "\n", encoding="utf-8"
    )
    return run_dir


def test_listing_shows_all_runs_sorted_by_story_id(status_root, capsys):
    make_run(status_root, "story-002", status="running", current_stage="tester")
    make_run(status_root, "story-001", status="completed", retry_count=1)

    assert run_status.main(status_root) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].split() == ["STORY", "STATUS", "STAGE", "RETRIES"]
    assert lines[1].split() == ["story-001", "completed", "-", "1"]
    assert lines[2].split() == ["story-002", "running", "tester", "0"]


def test_listing_flags_missing_state_without_aborting(status_root, capsys):
    make_run(status_root, "story-001", status="completed")
    (status_root / ".harness" / "runs" / "story-002").mkdir(parents=True)

    assert run_status.main(status_root) == 0
    out = capsys.readouterr().out
    assert "story-001" in out
    assert ["story-002", "unreadable", "-", "-"] in [
        line.split() for line in out.splitlines()
    ]


def test_listing_flags_unparseable_state_without_aborting(status_root, capsys):
    run_dir = make_run(status_root, "story-001")
    (run_dir / "state.json").write_text("{not json", encoding="utf-8")

    assert run_status.main(status_root) == 0
    assert "unreadable" in capsys.readouterr().out


def test_listing_with_no_runs_directory_exits_zero(status_root, capsys):
    assert run_status.main(status_root) == 0
    assert "no runs found" in capsys.readouterr().out


def test_listing_with_empty_runs_directory_exits_zero(status_root, capsys):
    (status_root / ".harness" / "runs").mkdir(parents=True)

    assert run_status.main(status_root) == 0
    assert "no runs found" in capsys.readouterr().out


def test_detail_view_shows_full_state_and_events(status_root, capsys):
    run_dir = make_run(
        status_root,
        "story-001",
        status="running",
        current_stage="verifier",
        retry_count=2,
        verification_iterations=3,
    )
    events = [f"[stamp] event {n}" for n in range(15)]
    (run_dir / "events.log").write_text("\n".join(events) + "\n", encoding="utf-8")

    assert run_status.main(status_root, "story-001") == 0
    out = capsys.readouterr().out
    assert "story id" in out and "story-001" in out
    assert "running" in out
    assert "verifier" in out
    assert "story/story-001" in out
    assert "retry count" in out and "2" in out
    assert "verification iterations" in out and "3" in out
    assert "event 5" in out and "event 14" in out
    assert "event 4" not in out


def test_detail_view_without_events_log(status_root, capsys):
    make_run(status_root, "story-001")

    assert run_status.main(status_root, "story-001") == 0
    assert "(no events recorded)" in capsys.readouterr().out


def test_unknown_story_id_errors_to_stderr(status_root, capsys):
    make_run(status_root, "story-001")

    assert run_status.main(status_root, "story-999") != 0
    captured = capsys.readouterr()
    assert "story-999" in captured.err
    assert captured.out == ""


def test_detail_with_unreadable_state_errors(status_root, capsys):
    run_dir = make_run(status_root, "story-001")
    (run_dir / "state.json").write_text("{not json", encoding="utf-8")

    assert run_status.main(status_root, "story-001") != 0
    assert "unreadable" in capsys.readouterr().err


def test_tail_events_returns_last_ten_lines(status_root):
    run_dir = make_run(status_root, "story-001")
    (run_dir / "events.log").write_text(
        "\n".join(f"line {n}" for n in range(12)) + "\n", encoding="utf-8"
    )

    tail = run_status.tail_events(run_dir)
    assert tail == [f"line {n}" for n in range(2, 12)]


def test_tail_events_missing_log_is_empty(status_root):
    run_dir = make_run(status_root, "story-001")
    assert run_status.tail_events(run_dir) == []


# --------------------------------------------------------------------------
# The entry index and what the whole run has cost
#
# story-062 made `retry count` and `verification iterations` entry-scoped: a
# resume restores the run's attempt allowance by zeroing them and archives the
# entry it ends. Read alone those two now understate a resumed run, so the
# entry index and the attempts the records hold print beside them -- beside,
# not instead of, which is what the second test here is about.
# --------------------------------------------------------------------------


#: The rows this story added to the detail view. Written here rather than read
#: off the module under test, because a test that derived the labels from the
#: implementation would pass whatever the implementation printed.
ADDED_ROWS = ("entry index", "attempts this run")

#: The two rows they were added to explain, which the pre-existing assertions
#: above already read.
ENTRY_SCOPED_ROWS = ("retry count", "verification iterations")

#: The added rows as they stand in the module, so taking them out leaves what
#: the story found. `load_mutant` fails if the anchor has moved, so this cannot
#: quietly become a mutation that changes nothing.
THE_ADDED_ROWS = """\
        ("entry index", str(state.resume_count)),
        (
            "attempts this run",
            str(story_coordinator.accumulated_attempts(run_dir, state)),
        ),
"""


def resumed_twice(root: Path, story_id: str = "story-001") -> Path:
    """A run in its third entry, with a retry recorded in each of them.

    Three entries have been opened, each opening costs one attempt, and each
    recorded a retry -- so the run has taken six attempts, of which the live
    counter can only account for the one retry of the entry now running.
    """
    run_dir = make_run(root, story_id, status="escalated",
                       current_stage="verifier", resume_count=2,
                       retry_count=1, verification_iterations=2)
    (run_dir / "retry-history.json").write_text(
        json.dumps([{"attempt": n, "blocking_issues": [],
                     "retry_stage": "implementer",
                     "archive_directory": f"attempts/attempt-{n}"}
                    for n in (1, 1, 1)]) + "\n",
        encoding="utf-8")
    return run_dir


def row(text: str, label: str) -> str:
    """The single printed line carrying one label."""
    found = [line for line in text.splitlines() if line.startswith(label)]
    assert len(found) == 1, (label, text)
    return found[0]


def test_the_detail_view_reports_the_entry_index_and_the_accumulated_total(
    status_root, capsys,
):
    """The two numbers a developer needs to read the two above them: which
    entry of the run is executing, and how many attempts the run has taken
    across all of them.

    The total is stated rather than recomposed the way the module does: the
    fixture opened three entries and recorded three retries, so six attempts
    have been taken. Reading it off `accumulated_attempts` would assert only
    that the printer prints what it was handed.
    """
    resumed_twice(status_root)

    assert run_status.main(status_root, "story-001") == 0
    out = capsys.readouterr().out

    assert row(out, "entry index").split() == ["entry", "index", "2"]
    assert row(out, "attempts this run").split() == ["attempts", "this", "run", "6"]
    # The entry-scoped counters are still printed, and still say what the entry
    # now running has spent rather than what the run has.
    assert row(out, "retry count").split() == ["retry", "count", "1"]
    assert row(out, "verification iterations").split() == [
        "verification", "iterations", "2"]


def test_the_lines_printed_before_this_story_are_printed_unchanged(
    status_root, tmp_path,
):
    """Beside, not instead of: every line the detail view printed before this
    story prints byte for byte as it did, including its column alignment.

    The comparison is against today's module with the two rows taken out -- a
    working-tree mutation, which is what a control showing that a change is
    confined is allowed to be. The control on the control is the last pair of
    assertions: the two renderings do differ, and they differ only by the added
    rows, so the equality above is not two readings of one unchanged file.
    """
    resumed_twice(status_root)
    without = load_mutant(RUN_STATUS_PATH, [(THE_ADDED_ROWS, "")],
                          name="run_status_without_the_entry_rows",
                          tmp_path=tmp_path)

    text = run_status.format_detail(status_root, "story-001")
    before = without.format_detail(status_root, "story-001")

    assert text != before
    kept = [line for line in text.splitlines()
            if not line.startswith(ADDED_ROWS)]
    assert kept == before.splitlines()
    for label in ENTRY_SCOPED_ROWS:
        assert row(text, label) == row(before, label), label
