import json
from pathlib import Path

import story_coordinator
from agent_runner import AgentResult


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class FakeRunner:
    """Stands in for agent_runner.run_agent; writes stage artifacts directly."""

    def __init__(self, target_root: Path, story_id: str, verifier_verdicts: list[dict],
                 changed_files: dict | None = None,
                 tester_changed_files: dict | None = None):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verifier_verdicts = list(verifier_verdicts)
        self.changed_files = changed_files or {
            "modified": ["src/app.py"], "created": [], "deleted": []
        }
        self.tester_changed_files = tester_changed_files or {
            "modified": [], "created": ["tests/test_app.py"], "deleted": []
        }
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        if stage == "implementer":
            write_json(self.run_dir / "changed-files.json", self.changed_files)
            (self.run_dir / "implementation-summary.md").write_text("Did the work.\n")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 2, "tests_run": 5,
                "tests_passed": 5, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json", self.tester_changed_files)
        elif stage == "verifier":
            verdict = self.verifier_verdicts.pop(0)
            write_json(self.run_dir / "verification-result.json", verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / "retry-guidance.json", {
                    "current_focus": ["fix the sample behavior"],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": ["src/app.py"],
                })
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text("No changes needed.\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


PASS = {"status": "passed", "blocking_issues": [], "unverified": [], "retry_recommended": False}
FAIL = {"status": "failed",
        "blocking_issues": [{"severity": "high", "issue": "sample behavior missing",
                             "location": "src/app.py", "required_behavior": "sample behavior exists"}],
        "unverified": [], "retry_recommended": True}


def read_state(target_root: Path) -> dict:
    path = target_root / ".harness" / "runs" / "story-001" / "state.json"
    return json.loads(path.read_text())


def test_happy_path_completes(target_root, harness_root):
    runner = FakeRunner(target_root, "story-001", [PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    state = read_state(target_root)
    assert state["status"] == "completed"
    assert state["retry_count"] == 0
    assert runner.calls == ["implementer", "tester", "verifier", "documenter"]
    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert (run_dir / "completion-report.md").is_file()
    assert (run_dir / "verification" / "iteration-1.json").is_file()
    events = (run_dir / "events.log").read_text()
    assert "verification passed" in events


def test_verification_failure_retries_then_completes(target_root, harness_root):
    runner = FakeRunner(target_root, "story-001", [FAIL, PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    state = read_state(target_root)
    assert state["status"] == "completed"
    assert state["retry_count"] == 1
    assert runner.calls == [
        "implementer", "tester", "verifier",
        "implementer", "tester", "verifier", "documenter",
    ]
    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert (run_dir / "verification" / "iteration-2.json").is_file()
    assert "retry 1 of 2" in (run_dir / "events.log").read_text()


def test_exhausted_retries_escalate(target_root, harness_root):
    runner = FakeRunner(target_root, "story-001", [FAIL, FAIL, FAIL])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    state = read_state(target_root)
    assert state["status"] == "escalated"
    assert state["retry_count"] == 2
    assert runner.calls.count("implementer") == 3
    run_dir = target_root / ".harness" / "runs" / "story-001"
    summary = (run_dir / "escalation-summary.md").read_text()
    assert "retries are exhausted" in summary


def test_blocked_path_modification_escalates(target_root, harness_root):
    runner = FakeRunner(
        target_root, "story-001", [PASS],
        changed_files={"modified": ["rules/execution-rules.json"], "created": [], "deleted": []},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert read_state(target_root)["status"] == "escalated"
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "blocked path" in summary


def test_tester_blocked_path_modification_escalates(target_root, harness_root):
    runner = FakeRunner(
        target_root, "story-001", [PASS],
        tester_changed_files={"modified": [], "created": ["rules/new-rule.json"], "deleted": []},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert read_state(target_root)["status"] == "escalated"
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "tester modified blocked path" in summary


def test_missing_tester_changed_files_escalates(target_root, harness_root):
    class NoTesterRecordRunner(FakeRunner):
        def __call__(self, prompt, *, stage, **kwargs):
            result = super().__call__(prompt, stage=stage, **kwargs)
            if stage == "tester":
                (self.run_dir / "tester-changed-files.json").unlink()
            return result

    runner = NoTesterRecordRunner(target_root, "story-001", [PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert read_state(target_root)["status"] == "escalated"
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "tester-changed-files.json" in summary


def test_missing_artifact_escalates(target_root, harness_root):
    class NoArtifactRunner(FakeRunner):
        def __call__(self, prompt, **kwargs):
            self.calls.append(kwargs["stage"])
            return AgentResult(ok=True, result_text="did nothing")

    runner = NoArtifactRunner(target_root, "story-001", [])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "changed-files.json" in summary


def test_completed_story_refuses_rerun(target_root, harness_root):
    runner = FakeRunner(target_root, "story-001", [PASS])
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 0
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 1


def test_malformed_story_artifact_refused(target_root, harness_root):
    story_path = target_root / ".harness" / "stories" / "story-001.yaml"
    story_text = story_path.read_text()
    story_path.write_text(story_text.replace("acceptance_criteria:", "criteria:"))

    runner = FakeRunner(target_root, "story-001", [PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 1
    assert runner.calls == []
    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert not (run_dir / "state.json").is_file()


def test_missing_story_sections_reports_each_absent_key():
    text = "story:\n  id: x\ntasks:\n  - t\nscope:\n  modify: []\n"
    missing = story_coordinator.missing_story_sections(text)
    assert missing == ["acceptance_criteria", "verification_requirements", "constraints"]
