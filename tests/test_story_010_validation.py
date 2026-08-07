"""story-010: superseded stage artifacts are archived under attempts/attempt-N/.

A retry regenerates the implementer's and tester's artifacts under their
canonical names. Before this story only the verifier's verdict survived the
overwrite. These tests check the rest of the evidence now survives it too,
that nothing routes on the archive, and that the artifact list is read from
the loaded workflow rather than written into orchestration code.
"""
import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import story_diff

import context_assembler
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing_verdict(attempt: int) -> dict:
    """A failing verdict whose text names the attempt that produced it."""
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"attempt {attempt} did not implement the sample behavior",
            "location": "src/app.py",
            "required_behavior": "sample behavior exists",
        }],
        "unverified": [],
        "retry_recommended": True,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class StampingRunner:
    """A fake agent runner whose artifacts name the attempt that wrote them.

    Every artifact carries its attempt number, so an archived copy can be
    told apart from the copy that superseded it rather than merely being
    present under the right name.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001", extra_outputs: tuple[str, ...] = ()):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = list(verdicts)
        self.extra_outputs = extra_outputs
        self.attempt = 1
        self.calls: list[str] = []
        # What the run directory looked like when each stage was entered.
        self.attempts_dirs_seen: list[tuple[str, list[str]]] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        attempts = self.run_dir / "attempts"
        self.attempts_dirs_seen.append((
            stage,
            sorted(p.name for p in attempts.iterdir()) if attempts.is_dir() else [],
        ))
        if stage == "implementer":
            write_json(self.run_dir / "changed-files.json", {
                "modified": ["src/app.py"],
                "created": [f"src/attempt_{self.attempt}.py"],
                "deleted": [],
            })
            (self.run_dir / "implementation-summary.md").write_text(
                f"Implemented on attempt {self.attempt}.\n", encoding="utf-8")
            for name in self.extra_outputs:
                (self.run_dir / name).write_text(
                    f"extra output from attempt {self.attempt}\n", encoding="utf-8")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": self.attempt,
                "tests_run": 5, "tests_passed": 5, "tests_failed": 0,
                "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json", {
                "modified": [],
                "created": [f"tests/test_attempt_{self.attempt}.py"],
                "deleted": [],
            })
        elif stage == "verifier":
            verdict = self.verdicts.pop(0)
            write_json(self.run_dir / "verification-result.json", verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / "retry-guidance.json", {
                    "current_focus": [f"guidance issued after attempt {self.attempt}"],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": ["src/app.py"],
                })
                self.attempt += 1
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text(
                f"Documented after attempt {self.attempt}.\n", encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


ATTEMPT_1_ARTIFACTS = [
    "changed-files.json",
    "implementation-summary.md",
    "retry-guidance.json",
    "test-results.json",
    "tester-changed-files.json",
    "verification-result.json",
]


# --------------------------------------------------------------------------
# A run that fails once and passes on the second attempt
# --------------------------------------------------------------------------


@pytest.fixture
def retry_then_pass(target_root, harness_root):
    runner = StampingRunner(target_root, [failing_verdict(1), PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    return runner, run_dir_of(target_root)


def test_attempt_1_archive_holds_the_six_artifacts_under_canonical_names(
    retry_then_pass,
):
    _, run_dir = retry_then_pass
    archive = run_dir / "attempts" / "attempt-1"
    assert archive.is_dir()
    assert sorted(p.name for p in archive.iterdir()) == ATTEMPT_1_ARTIFACTS


def test_the_archived_contents_are_attempt_1s_not_attempt_2s(retry_then_pass):
    _, run_dir = retry_then_pass
    archive = run_dir / "attempts" / "attempt-1"

    assert (archive / "implementation-summary.md").read_text() == (
        "Implemented on attempt 1.\n")
    changed = json.loads((archive / "changed-files.json").read_text())
    assert changed["created"] == ["src/attempt_1.py"]
    results = json.loads((archive / "test-results.json").read_text())
    assert results["tests_written"] == 1
    tester_changed = json.loads((archive / "tester-changed-files.json").read_text())
    assert tester_changed["created"] == ["tests/test_attempt_1.py"]
    verdict = json.loads((archive / "verification-result.json").read_text())
    assert verdict["status"] == "failed"
    assert "attempt 1" in verdict["blocking_issues"][0]["issue"]
    guidance = json.loads((archive / "retry-guidance.json").read_text())
    assert guidance["current_focus"] == ["guidance issued after attempt 1"]


def test_the_root_copies_describe_attempt_2(retry_then_pass):
    _, run_dir = retry_then_pass

    assert (run_dir / "implementation-summary.md").read_text() == (
        "Implemented on attempt 2.\n")
    changed = json.loads((run_dir / "changed-files.json").read_text())
    assert changed["created"] == ["src/attempt_2.py"]
    results = json.loads((run_dir / "test-results.json").read_text())
    assert results["tests_written"] == 2
    tester_changed = json.loads((run_dir / "tester-changed-files.json").read_text())
    assert tester_changed["created"] == ["tests/test_attempt_2.py"]
    verdict = json.loads((run_dir / "verification-result.json").read_text())
    assert verdict["status"] == "passed"


def test_the_archive_copies_rather_than_moves(retry_then_pass):
    """Every archived artifact still exists at the run-directory root."""
    _, run_dir = retry_then_pass
    for name in ATTEMPT_1_ARTIFACTS:
        assert (run_dir / name).is_file(), name


def test_an_artifact_the_attempt_did_not_write_is_skipped(retry_then_pass):
    """The documenter never runs before a retry, so its report is absent from
    the attempt-1 archive - skipped, not an archive failure."""
    _, run_dir = retry_then_pass
    assert not (run_dir / "attempts" / "attempt-1" / "documentation-report.md").exists()
    assert "documentation-report.md" in story_coordinator.archivable_artifacts(
        json.loads((REPO_ROOT / "workflows" / "story-workflow.json").read_text())["stages"]
    )
    assert (run_dir / "documentation-report.md").is_file()


def test_the_archive_happens_before_the_retry_begins(retry_then_pass):
    """attempts/attempt-1/ already exists when attempt 2's implementer starts,
    and did not exist when attempt 1's did."""
    runner, _ = retry_then_pass
    implementer_entries = [
        seen for stage, seen in runner.attempts_dirs_seen if stage == "implementer"
    ]
    assert implementer_entries == [[], ["attempt-1"]]


def test_the_attempt_number_matches_the_rendered_prompt_of_the_same_attempt(
    retry_then_pass,
):
    _, run_dir = retry_then_pass
    assert (run_dir / "prompt-implementer-attempt-1.md").is_file()
    assert (run_dir / "attempts" / "attempt-1").is_dir()
    assert (run_dir / "prompt-implementer-attempt-2.md").is_file()
    # Attempt 2 succeeded, so it was never superseded and is not archived.
    assert not (run_dir / "attempts" / "attempt-2").exists()


def test_no_suffixed_filename_variant_appears_anywhere_in_the_run(retry_then_pass):
    _, run_dir = retry_then_pass
    names = [p.name for p in run_dir.rglob("*")]
    for name in names:
        assert "attempt" not in name or name.startswith("prompt-") \
            or name.startswith("attempt-") or name == "attempts", name


def test_the_verifiers_own_archive_and_the_root_verdict_are_unchanged(
    retry_then_pass,
):
    """verification/iteration-N.json still records every iteration, and the
    root verification-result.json is still the routing source."""
    _, run_dir = retry_then_pass
    first = json.loads((run_dir / "verification" / "iteration-1.json").read_text())
    second = json.loads((run_dir / "verification" / "iteration-2.json").read_text())
    assert first["status"] == "failed"
    assert second["status"] == "passed"
    assert read_state(run_dir)["verification_iterations"] == 2


def test_routing_is_unchanged_by_the_archive(retry_then_pass):
    runner, run_dir = retry_then_pass
    state = read_state(run_dir)
    assert state["status"] == "completed"
    assert state["retry_count"] == 1
    assert runner.calls == [
        "implementer", "tester", "verifier",
        "implementer", "tester", "verifier", "documenter",
    ]
    assert "retry 1 of 2" in (run_dir / "events.log").read_text()


# --------------------------------------------------------------------------
# The runs that produce no archive, and the run that produces two
# --------------------------------------------------------------------------


def test_a_run_without_a_retry_produces_no_attempts_directory(target_root,
                                                              harness_root):
    runner = StampingRunner(target_root, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    run_dir = run_dir_of(target_root)
    assert read_state(run_dir)["retry_count"] == 0
    assert not (run_dir / "attempts").exists()


def test_exhausted_retries_produce_attempt_1_and_attempt_2(target_root,
                                                           harness_root):
    runner = StampingRunner(
        target_root, [failing_verdict(1), failing_verdict(2), failing_verdict(3)])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    run_dir = run_dir_of(target_root)
    assert read_state(run_dir)["status"] == "escalated"
    assert read_state(run_dir)["retry_count"] == 2

    attempts = run_dir / "attempts"
    assert sorted(p.name for p in attempts.iterdir()) == ["attempt-1", "attempt-2"]
    for number in (1, 2):
        archive = attempts / f"attempt-{number}"
        assert sorted(p.name for p in archive.iterdir()) == ATTEMPT_1_ARTIFACTS
        assert (archive / "implementation-summary.md").read_text() == (
            f"Implemented on attempt {number}.\n")
        verdict = json.loads((archive / "verification-result.json").read_text())
        assert f"attempt {number}" in verdict["blocking_issues"][0]["issue"]
    # The third, escalating attempt is not archived; its artifacts stay at the
    # root, where the escalation's reader expects them.
    assert not (attempts / "attempt-3").exists()
    assert (run_dir / "implementation-summary.md").read_text() == (
        "Implemented on attempt 3.\n")


# --------------------------------------------------------------------------
# The artifact list comes from the loaded workflow, not from the code
# --------------------------------------------------------------------------


def test_archivable_artifacts_unions_outputs_changed_files_and_schema_keys():
    stages = [
        {"name": "alpha", "outputs": ["alpha-output.md"],
         "changed_files": "alpha-record.json",
         "schemas": {"alpha-record.json": "changed-files",
                     "alpha-conditional.json": "retry-guidance"}},
        {"name": "beta", "outputs": ["beta-output.json"]},
    ]
    assert story_coordinator.archivable_artifacts(stages) == [
        "alpha-conditional.json", "alpha-output.md", "alpha-record.json",
        "beta-output.json",
    ]


def test_archivable_artifacts_tolerates_a_stage_declaring_nothing():
    assert story_coordinator.archivable_artifacts([{"name": "solo"}]) == []


def _archive_code_body(name: str) -> str:
    """The named coordinator function's source with its docstring removed."""
    module = ast.parse(Path(story_coordinator.__file__).read_text(encoding="utf-8"))
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    body = function.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


@pytest.mark.parametrize("function", ["archivable_artifacts", "archive_attempt"])
def test_no_artifact_or_stage_name_is_written_into_the_archive_code(function):
    workflow = json.loads(
        (REPO_ROOT / "workflows" / "story-workflow.json").read_text(encoding="utf-8"))
    code = _archive_code_body(function)
    for stage in workflow["stages"]:
        assert stage["name"] not in code, stage["name"]
    for artifact in story_coordinator.archivable_artifacts(workflow["stages"]):
        assert artifact not in code, artifact


@pytest.fixture
def probe_harness_root(tmp_path: Path) -> Path:
    """A harness root carrying a workflow this repository does not ship.

    Its implementer stage declares an extra artifact, design-notes.md, in a
    place the coordinator reads artifact names from. Nothing else differs.
    """
    root = tmp_path / "probe-harness"
    root.mkdir()
    for directory in ("prompts", "rules", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory)
    workflow = json.loads(
        (REPO_ROOT / "workflows" / "story-workflow.json").read_text(encoding="utf-8"))
    for stage in workflow["stages"]:
        if stage["name"] == "implementer":
            stage["outputs"] = [*stage["outputs"], "design-notes.md"]
    workflow["name"] = "archive-probe-workflow"
    (root / "workflows").mkdir()
    write_json(root / "workflows" / "archive-probe-workflow.json", workflow)
    return root


def test_a_workflow_the_repository_does_not_ship_gets_its_artifact_archived(
    target_root, probe_harness_root,
):
    """The proof that the list is derived: orchestration/story_coordinator.py
    is not touched, only the workflow definition the run loads."""
    config = target_root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "workflow: story-workflow", "workflow: archive-probe-workflow"),
        encoding="utf-8",
    )
    assert "design-notes.md" in story_coordinator.archivable_artifacts(
        json.loads((probe_harness_root / "workflows" /
                    "archive-probe-workflow.json").read_text())["stages"])

    runner = StampingRunner(
        target_root, [failing_verdict(1), PASS], extra_outputs=("design-notes.md",))
    assert story_coordinator.run_story(
        "story-001", probe_harness_root, target_root, runner) == 0

    archive = run_dir_of(target_root) / "attempts" / "attempt-1"
    assert (archive / "design-notes.md").read_text() == (
        "extra output from attempt 1\n")
    assert sorted(p.name for p in archive.iterdir()) == sorted(
        [*ATTEMPT_1_ARTIFACTS, "design-notes.md"])


# --------------------------------------------------------------------------
# archive_attempt in isolation
# --------------------------------------------------------------------------


def test_archive_attempt_skips_absent_artifacts_and_reports_what_it_copied(
    tmp_path: Path,
):
    (tmp_path / "present.json").write_text('{"a": 1}\n', encoding="utf-8")
    archived = story_coordinator.archive_attempt(
        tmp_path, ["absent.json", "present.json"], 3)
    assert archived == ["present.json"]
    destination = tmp_path / "attempts" / "attempt-3"
    assert [p.name for p in destination.iterdir()] == ["present.json"]
    assert (destination / "present.json").read_text() == '{"a": 1}\n'
    assert (tmp_path / "present.json").is_file()


def test_archive_attempt_creates_nothing_it_was_not_asked_for(tmp_path: Path):
    """An attempt that wrote nothing still leaves the directory, and leaves
    the run directory otherwise untouched."""
    story_coordinator.archive_attempt(tmp_path, ["absent.md"], 1)
    assert (tmp_path / "attempts" / "attempt-1").is_dir()
    assert list((tmp_path / "attempts" / "attempt-1").iterdir()) == []


# --------------------------------------------------------------------------
# What this story leaves alone
# --------------------------------------------------------------------------


def _unchanged_by_this_story(rel: str) -> bool:
    """Whether *this story's own change* left `rel` alone.

    Not `git diff HEAD`. That asks whether the working tree is dirty here,
    which is a question about whoever is working right now: it goes vacuously
    green the moment anything is committed, and red for every later story that
    legitimately edits one of these paths. Bound the comparison at both ends
    instead — this story's own run commit against its parent.

    Since story-015 the resolution lives once in `tests/conftest.py` rather
    than being restated here, and it keys on this validation file's own
    adding commit rather than on a marker planted in the story's source: the
    commit that added `tests/test_story_010_validation.py` *is* story-010's
    run commit.
    """
    return story_diff([rel], validation_file=Path(__file__)).strip() == ""


def test_context_assembler_is_unchanged():
    assert _unchanged_by_this_story("orchestration/context_assembler.py")


@pytest.mark.parametrize("rel", ["workflows/", "schemas/", "rules/", "prompts/"])
def test_the_definitions_this_story_reads_are_unchanged(rel):
    assert _unchanged_by_this_story(rel)


def test_no_reader_was_pointed_at_the_archive():
    """Nothing outside the archive helper reads attempts/; the run-directory
    root stays the one place stage artifacts are read from."""
    assembler = Path(context_assembler.__file__).read_text(encoding="utf-8")
    assert "attempts" not in assembler
    coordinator = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    body = _archive_code_body("archive_attempt")
    assert coordinator.count('"attempts"') == 1
    assert "attempts" in body
