"""End-to-end validation of scripts/l5-status against story-001's acceptance criteria.

These tests invoke the real CLI in a subprocess against fixture run
directories, independently of the run_status module's own unit tests.
No model invocations occur.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1]
L5_STATUS = HARNESS_ROOT / "scripts" / "l5-status"

CONFIG = """\
project: cli-target
runs_dir: .harness/runs
"""


def write_state(run_dir: Path, **fields) -> None:
    state = {
        "story_id": run_dir.name,
        "branch": f"story/{run_dir.name}",
        "status": "running",
        "current_stage": "",
        "retry_count": 0,
        "verification_iterations": 0,
        "artifacts": [],
    }
    state.update(fields)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(state) + "\n", encoding="utf-8")


@pytest.fixture
def cli_root(tmp_path: Path) -> Path:
    root = tmp_path / "cli-target"
    (root / ".harness").mkdir(parents=True)
    (root / ".harness" / "config.yaml").write_text(CONFIG, encoding="utf-8")
    return root


def run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(L5_STATUS), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def snapshot(root: Path) -> set[tuple[str, int]]:
    return {
        (str(p.relative_to(root)), p.stat().st_size)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_cli_lists_runs_sorted_with_required_columns(cli_root):
    runs = cli_root / ".harness" / "runs"
    write_state(runs / "story-003", status="running", current_stage="implementer")
    write_state(runs / "story-001", status="completed", retry_count=2)

    result = run_cli(cli_root)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0].split() == ["STORY", "STATUS", "STAGE", "RETRIES"]
    assert lines[1].split()[0] == "story-001"
    assert lines[2].split()[0] == "story-003"
    assert "completed" in lines[1] and "2" in lines[1].split()
    assert "implementer" in lines[2]


def test_cli_detail_shows_full_state_and_last_ten_events(cli_root):
    run_dir = cli_root / ".harness" / "runs" / "story-001"
    write_state(
        run_dir,
        status="running",
        current_stage="tester",
        retry_count=1,
        verification_iterations=4,
    )
    (run_dir / "events.log").write_text(
        "\n".join(f"[t] event-{n:02d}" for n in range(25)) + "\n", encoding="utf-8"
    )

    result = run_cli(cli_root, "story-001")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for expected in ("story-001", "running", "tester", "story/story-001", "4"):
        assert expected in out
    assert "event-15" in out and "event-24" in out
    assert "event-14" not in out


def test_cli_unknown_story_id_exits_nonzero_with_stderr(cli_root):
    write_state(cli_root / ".harness" / "runs" / "story-001")

    result = run_cli(cli_root, "story-404")
    assert result.returncode != 0
    assert "story-404" in result.stderr
    assert result.stdout == ""


def test_cli_absent_and_empty_runs_dir_report_no_runs(cli_root):
    result = run_cli(cli_root)
    assert result.returncode == 0
    assert "no runs" in result.stdout.lower()

    (cli_root / ".harness" / "runs").mkdir()
    result = run_cli(cli_root)
    assert result.returncode == 0
    assert "no runs" in result.stdout.lower()


def test_cli_unreadable_state_flagged_without_aborting_listing(cli_root):
    runs = cli_root / ".harness" / "runs"
    write_state(runs / "story-001", status="completed")
    broken = runs / "story-002"
    broken.mkdir()
    (broken / "state.json").write_text("{{{", encoding="utf-8")
    (runs / "story-003").mkdir()  # missing state.json entirely

    result = run_cli(cli_root)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "story-001" in out and "completed" in out
    for run_id in ("story-002", "story-003"):
        line = next(l for l in out.splitlines() if run_id in l)
        assert "unreadable" in line


def test_cli_finds_target_root_from_subdirectory(cli_root):
    write_state(cli_root / ".harness" / "runs" / "story-001", status="completed")
    nested = cli_root / "src" / "deep"
    nested.mkdir(parents=True)

    result = run_cli(nested)
    assert result.returncode == 0, result.stderr
    assert "story-001" in result.stdout


def test_cli_outside_any_harness_root_fails(tmp_path):
    result = run_cli(tmp_path)
    assert result.returncode != 0
    assert "config.yaml" in result.stderr


def test_cli_rejects_extra_arguments(cli_root):
    result = run_cli(cli_root, "story-001", "extra")
    assert result.returncode != 0
    assert result.stderr != ""


def test_cli_is_read_only(cli_root):
    run_dir = cli_root / ".harness" / "runs" / "story-001"
    write_state(run_dir, status="running", current_stage="verifier")
    (run_dir / "events.log").write_text("[t] one\n", encoding="utf-8")
    before = snapshot(cli_root)

    assert run_cli(cli_root).returncode == 0
    assert run_cli(cli_root, "story-001").returncode == 0
    assert run_cli(cli_root, "story-404").returncode != 0

    assert snapshot(cli_root) == before
