import json
from dataclasses import asdict
from pathlib import Path

import pytest

import run_status
from story_coordinator import RunState

CONFIG = """\
project: sample-target
runs_dir: .harness/runs
"""


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
