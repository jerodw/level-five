"""Independent validation for story-002: per-stage changed-files records.

Validates the acceptance criteria from the artifacts outward — the workflow
definition, the prompt templates, and the coordinator's declaration-driven
blocked-path enforcement — using its own fake runner rather than the one in
test_story_coordinator.py.
"""
import json
import shutil
from pathlib import Path

import story_coordinator
from agent_runner import AgentResult


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class StageRunner:
    """Minimal fake runner: every stage succeeds and writes its artifacts."""

    def __init__(self, target_root: Path, story_id: str = "story-001",
                 tester_record: dict | None = None,
                 write_tester_record: bool = True):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.tester_record = tester_record or {
            "modified": [], "created": ["tests/test_app.py"], "deleted": []
        }
        self.write_tester_record = write_tester_record
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode,
                 model, allowed_tools=None):
        self.calls.append(stage)
        if stage == "implementer":
            _write(self.run_dir / "changed-files.json",
                   {"modified": ["src/app.py"], "created": [], "deleted": []})
            (self.run_dir / "implementation-summary.md").write_text("done\n")
        elif stage == "tester":
            _write(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            if self.write_tester_record:
                _write(self.run_dir / "tester-changed-files.json", self.tester_record)
        elif stage == "verifier":
            _write(self.run_dir / "verification-result.json", {
                "status": "passed", "blocking_issues": [],
                "unverified": [], "retry_recommended": False,
            })
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text("n/a\n")
        return AgentResult(ok=True, result_text=f"{stage} ok")


def test_workflow_declares_per_stage_changed_files_records(harness_root):
    workflow = json.loads(
        (harness_root / "workflows" / "story-workflow.json").read_text()
    )
    stages = {s["name"]: s for s in workflow["stages"]}
    assert "tester-changed-files.json" in stages["tester"]["outputs"]
    assert stages["implementer"]["changed_files"] == "changed-files.json"
    assert stages["tester"]["changed_files"] == "tester-changed-files.json"
    assert "changed_files" not in stages["documenter"]


def test_tester_prompt_requires_tester_changed_files_record(harness_root):
    prompt = (harness_root / "prompts" / "tester.md").read_text()
    assert "tester-changed-files.json" in prompt
    assert "same schema as changed-files.json" in prompt
    for group in ("modified", "created", "deleted"):
        assert f'"{group}"' in prompt


def test_verifier_prompt_injects_both_records_with_distinct_guidance(harness_root):
    prompt = (harness_root / "prompts" / "verifier.md").read_text()
    assert "{{changed_files}}" in prompt
    assert "{{tester_changed_files}}" in prompt
    implementer_pos = prompt.index("{{changed_files}}")
    tester_pos = prompt.index("{{tester_changed_files}}")
    implementer_guidance = prompt[:implementer_pos].rsplit("\n\n", 1)[-1]
    tester_guidance = prompt[implementer_pos:tester_pos]
    assert "scope" in implementer_guidance
    assert "expected additions" in tester_guidance
    assert "not" in tester_guidance and "violations" in tester_guidance


def test_blocked_violation_checks_only_the_named_record(tmp_path):
    _write(tmp_path / "changed-files.json",
           {"modified": ["src/app.py"], "created": [], "deleted": []})
    _write(tmp_path / "tester-changed-files.json",
           {"modified": [], "created": [], "deleted": ["rules/execution-rules.json"]})
    blocked = [".git/", ".harness/runs/", "rules/"]
    assert story_coordinator._blocked_violation(
        tmp_path, "changed-files.json", blocked) is None
    assert story_coordinator._blocked_violation(
        tmp_path, "tester-changed-files.json", blocked) == "rules/execution-rules.json"


def test_tester_deleting_blocked_path_escalates(target_root, harness_root):
    runner = StageRunner(
        target_root,
        tester_record={"modified": [], "created": [],
                       "deleted": ["rules/execution-rules.json"]},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == ["implementer", "tester"]
    summary = (runner.run_dir / "escalation-summary.md").read_text()
    assert "tester modified blocked path: rules/execution-rules.json" in summary


def test_tester_without_record_escalates_before_verifier(target_root, harness_root):
    runner = StageRunner(target_root, write_tester_record=False)
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert runner.calls == ["implementer", "tester"]
    summary = (runner.run_dir / "escalation-summary.md").read_text()
    assert "tester did not produce required artifacts" in summary
    assert "tester-changed-files.json" in summary


def test_enforcement_follows_declaration_not_stage_name(target_root, harness_root, tmp_path):
    """Removing the tester's changed_files declaration disables its check,
    proving enforcement is driven by the workflow definition."""
    harness_copy = tmp_path / "harness-copy"
    for sub in ("prompts", "workflows", "rules"):
        shutil.copytree(harness_root / sub, harness_copy / sub)
    workflow_path = harness_copy / "workflows" / "story-workflow.json"
    workflow = json.loads(workflow_path.read_text())
    for stage in workflow["stages"]:
        if stage["name"] == "tester":
            del stage["changed_files"]
    _write(workflow_path, workflow)

    runner = StageRunner(
        target_root,
        tester_record={"modified": ["rules/execution-rules.json"],
                       "created": [], "deleted": []},
    )
    code = story_coordinator.run_story("story-001", harness_copy, target_root, runner)
    assert code == 0
    assert runner.calls == ["implementer", "tester", "verifier", "documenter"]
