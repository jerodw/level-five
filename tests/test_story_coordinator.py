import json
import subprocess
import sys
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


def branches(target_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.split()


def rewrite_story(target_root: Path, old: str, new: str) -> Path:
    story_path = target_root / ".harness" / "stories" / "story-001.yaml"
    story_path.write_text(story_path.read_text().replace(old, new))
    return story_path


def assert_rejected_leaving_no_trace(target_root, harness_root, capsys=None):
    """A pre-flight rejection is exit 1 with no agent and no partial state."""
    before = branches(target_root)
    runner = FakeRunner(target_root, "story-001", [PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 1
    assert runner.calls == []
    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert not run_dir.exists()
    assert not (run_dir / "state.json").is_file()
    assert branches(target_root) == before
    assert "story/story-001" not in branches(target_root)


def test_malformed_story_artifact_refused(target_root, harness_root, capsys):
    rewrite_story(target_root, "acceptance_criteria:", "criteria:")
    assert_rejected_leaving_no_trace(target_root, harness_root)
    assert "acceptance_criteria" in capsys.readouterr().err


def test_a_missing_top_level_section_is_named_in_the_message(target_root, harness_root,
                                                             capsys):
    rewrite_story(target_root, "constraints:", "limits:")
    assert_rejected_leaving_no_trace(target_root, harness_root)
    err = capsys.readouterr().err
    assert "constraints" in err
    assert "missing" in err


def test_a_wrong_nested_structure_is_rejected_during_pre_flight(target_root,
                                                                harness_root, capsys):
    """Before this story a scope without do_not_modify passed pre-flight."""
    rewrite_story(target_root, "  do_not_modify:\n    - rules/\n", "")
    assert_rejected_leaving_no_trace(target_root, harness_root)
    assert "$.scope.do_not_modify" in capsys.readouterr().err


def test_an_unparseable_story_is_rejected_with_a_line_number(target_root, harness_root,
                                                             capsys):
    rewrite_story(target_root, "  - do the sample work", "\t- do the sample work")
    assert_rejected_leaving_no_trace(target_root, harness_root)
    err = capsys.readouterr().err
    assert "line 8" in err                      # the tabbed tasks entry
    assert "tab" in err


def test_story_problems_are_empty_for_a_valid_story(target_root):
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    assert story_coordinator.story_problems(story_text) == []


def test_pre_flight_reads_the_schema_file_rather_than_a_constant(tmp_path):
    """Editing the schema changes what pre-flight enforces."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    write_json(schemas / "story.schema.json",
               {"type": "object", "required": ["story", "sentinel_section"],
                "properties": {"story": {"type": "object"}}})
    problems = story_coordinator.story_problems("story:\n  id: x\n", tmp_path)
    assert problems == ["$.sentinel_section: expected a required property, found it missing"]


def test_l5_run_exits_1_on_a_rejected_story_without_invoking_an_agent(target_root):
    """End to end through the entry point: no agent can run, because the
    refusal happens before the coordinator reaches agent invocation."""
    harness_root = Path(story_coordinator.__file__).resolve().parents[1]
    rewrite_story(target_root, "verification_requirements:", "checks:")
    result = subprocess.run(
        [sys.executable, str(harness_root / "scripts" / "l5-run"), "story-001"],
        cwd=target_root, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "verification_requirements" in result.stderr
    assert not (target_root / ".harness" / "runs" / "story-001").exists()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    assert "story/story-001" not in branches(target_root)


def test_exactly_one_mechanism_validates_a_story_artifact():
    """The line-prefix helpers are gone; nothing checks a story a second way."""
    source = Path(story_coordinator.__file__).read_text()
    for obsolete in ("missing_story_sections", "load_required_story_sections",
                     "REQUIRED_STORY_SECTIONS"):
        assert obsolete not in source, obsolete
        assert not hasattr(story_coordinator, obsolete), obsolete
    assert source.count("story_problems(") == 2   # the definition and its one call


class InvalidArtifactRunner(FakeRunner):
    """Corrupts one artifact after the stage that owns it writes it."""

    def __init__(self, *args, corrupt_stage: str, artifact: str, payload, **kwargs):
        super().__init__(*args, **kwargs)
        self.corrupt_stage = corrupt_stage
        self.artifact = artifact
        self.payload = payload

    def __call__(self, prompt, *, stage, **kwargs):
        result = super().__call__(prompt, stage=stage, **kwargs)
        if stage == self.corrupt_stage:
            path = self.run_dir / self.artifact
            if isinstance(self.payload, str):
                path.write_text(self.payload, encoding="utf-8")
            else:
                write_json(path, self.payload)
        return result


def test_schema_invalid_artifact_escalates_immediately(target_root, harness_root):
    runner = InvalidArtifactRunner(
        target_root, "story-001", [PASS],
        corrupt_stage="implementer",
        artifact="changed-files.json",
        payload={"modified": ["src/app.py"], "created": []},   # no "deleted"
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    state = read_state(target_root)
    assert state["status"] == "escalated"
    assert state["retry_count"] == 0
    assert runner.calls == ["implementer"]

    run_dir = target_root / ".harness" / "runs" / "story-001"
    events = (run_dir / "events.log").read_text()
    summary = (run_dir / "escalation-summary.md").read_text()
    for text in (events, summary):
        assert "changed-files.json" in text
        assert "$.deleted" in text
        assert "required" in text
        assert "missing" in text


def test_schema_validation_runs_before_the_blocked_paths_check(target_root, harness_root):
    """A changed-files.json the blocked-paths check could not read escalates as
    a validation error, not as an exception out of that check."""
    runner = InvalidArtifactRunner(
        target_root, "story-001", [PASS],
        corrupt_stage="implementer",
        artifact="changed-files.json",
        payload={"modified": "rules/execution-rules.json", "created": [], "deleted": []},
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "$.modified" in summary
    assert "expected type array" in summary
    assert "blocked path" not in summary


def test_unparseable_artifact_escalates_with_the_decode_error(target_root, harness_root):
    runner = InvalidArtifactRunner(
        target_root, "story-001", [PASS],
        corrupt_stage="tester",
        artifact="test-results.json",
        payload="{ this is not json",
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert read_state(target_root)["retry_count"] == 0
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "test-results.json" in summary
    assert "not parseable as JSON" in summary


def test_invalid_verifier_artifact_escalates_without_a_retry(target_root, harness_root):
    runner = InvalidArtifactRunner(
        target_root, "story-001", [FAIL, PASS],
        corrupt_stage="verifier",
        artifact="retry-guidance.json",
        payload={"current_focus": ["fix it"], "preserve_behavior": []},  # no retry_scope
    )
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert read_state(target_root)["retry_count"] == 0
    assert runner.calls == ["implementer", "tester", "verifier"]
    summary = (target_root / ".harness" / "runs" / "story-001" / "escalation-summary.md").read_text()
    assert "retry-guidance.json" in summary
    assert "$.retry_scope" in summary


def test_absent_retry_guidance_is_not_a_validation_failure(target_root, harness_root):
    """The happy path never writes retry-guidance.json; that is not an error."""
    runner = FakeRunner(target_root, "story-001", [PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    run_dir = target_root / ".harness" / "runs" / "story-001"
    assert not (run_dir / "retry-guidance.json").exists()
    assert read_state(target_root)["status"] == "completed"


def test_valid_artifacts_leave_routing_unchanged(tmp_path):
    """_schema_violation is silent for artifacts that satisfy their schemas."""
    write_json(tmp_path / "changed-files.json",
               {"modified": ["src/app.py"], "created": [], "deleted": [],
                "note": "an extra key is tolerated"})
    stage = {"schemas": {"changed-files.json": "changed-files",
                         "retry-guidance.json": "retry-guidance"}}
    assert story_coordinator._schema_violation(tmp_path, stage) is None
