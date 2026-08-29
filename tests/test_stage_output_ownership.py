"""Independent validation for story-007: coordinator-enforced stage output
ownership.

Written from the story's acceptance criteria rather than from the
implementation. The story exists because a boundary stated in a prompt was
only a suggestion, so these tests prefer observable behavior over source
inspection wherever a behavior is available: what a real coordinator run
writes into events.log and escalation-summary.md, what a pre-flight refusal
leaves on disk, and — for the criterion that the rule is data-driven — what
happens when the declaration is moved to a different stage in a workflow
definition the code has never seen.
"""
import ast
import inspect
import json
import subprocess
from pathlib import Path

import pytest

from conftest import commit_setup, story_diff
import conftest

import context_assembler
import harness_config
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
STORIES_DIR = REPO_ROOT / ".harness" / "stories"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

EMPTY_RECORD = {"modified": [], "created": [], "deleted": []}


#: The loaded workflow build_context has taken as a required argument
#: since story-028, which injects the workflow's own facts — its stages,
#: its create restrictions, its retry routes — into every stage prompt.
#:
#: Assembled by the builder in `tests/conftest.py` rather than resolved out of
#: what this repository deploys. story-048 made the change: the subject here is
#: *ownership enforcement* — a stage may not create under a prefix its own
#: declaration names — and any stage list states that. Reading the deployed one
#: made "which stage this deployment restricts" a fact this module enforced;
#: that question moved to tests/test_shipped_workflow_is_valid.py.
GOVERNED_PREFIX = "tests/"

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        may_not_create=(GOVERNED_PREFIX,),
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.DOCUMENTATION_REPORT,
                 conftest.DOCUMENTER_CHANGED_FILES),
        changed_files=conftest.DOCUMENTER_CHANGED_FILES,
        schemas={conftest.DOCUMENTER_CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="stage-ownership-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES


@pytest.fixture
def configured_workflow() -> str:
    """Point the shared target fixture at the definition built above."""
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying that definition."""
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "ownership-harness")


def writing_template() -> str:
    """The prompt template the writing stage declares, off the built definition."""
    return next(stage["prompt"] for stage in WORKFLOW["stages"]
                if stage["name"] == WRITING)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class Runner:
    """A fake agent runner whose per-stage changed-files record is the input.

    Every stage writes exactly the artifacts the workflow declares for it, so
    the only variable under test is what each stage claims it touched.
    """

    def __init__(self, target_root: Path, story_id: str = "story-001", *,
                 records: dict[str, dict] | None = None,
                 verdicts: list[dict] | None = None,
                 skip_outputs: tuple[str, ...] = ()):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.records = records or {}
        self.verdicts = list(verdicts or [PASS])
        self.skip_outputs = skip_outputs
        self.calls: list[str] = []

    def _record(self, stage: str) -> dict:
        return self.records.get(stage, dict(EMPTY_RECORD))

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        if stage == WRITING:
            write_json(self.run_dir / "changed-files.json", self._record(stage))
            if "implementation-summary.md" not in self.skip_outputs:
                (self.run_dir / "implementation-summary.md").write_text("Did it.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json", self._record(stage))
        elif stage == VERIFYING:
            write_json(self.run_dir / "verification-result.json", self.verdicts.pop(0))
        elif stage == DOCUMENTING:
            (self.run_dir / "documentation-report.md").write_text("Nothing.\n")
            write_json(self.run_dir / "documenter-changed-files.json",
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def state_of(target_root: Path, story_id: str = "story-001") -> dict:
    return json.loads((run_dir_of(target_root, story_id) / "state.json").read_text())


def evidence(target_root: Path, story_id: str = "story-001") -> tuple[str, str]:
    """The two places an escalation reason must appear."""
    run_dir = run_dir_of(target_root, story_id)
    return (
        (run_dir / "events.log").read_text(),
        (run_dir / "escalation-summary.md").read_text(),
    )


def branches(target_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.split()


def append_to_story(target_root: Path, text: str, story_id: str = "story-001") -> Path:
    path = target_root / ".harness" / "stories" / f"{story_id}.yaml"
    path.write_text(path.read_text() + text, encoding="utf-8")
    # The artifact is what a run reads, not what it produces. story-021's
    # clean-tree pre-flight refuses a run whose target tree holds anything
    # uncommitted, so committing it here keeps the refusals below refusing for
    # the reason each one names — the stage-exception check runs above the
    # clean-tree one and still fires first.
    commit_setup(target_root, "the story artifact this test runs")
    return path


def exception_block(stage: str, create: str, reason: str = "the deliverable is the suite") -> str:
    return (
        "\nstage_exceptions:\n"
        f"  - stage: {stage}\n"
        f"    create: {create}\n"
        f"    reason: {reason}\n"
    )


def workflow_stages(harness_root: Path) -> list[dict]:
    return harness_config.load_workflow(
        harness_root, WORKFLOW["name"], conftest.repository_config())["stages"]


def executable_source(text: str) -> str:
    """Strip docstrings and comment lines; prose may name what code may not."""
    kept, in_docstring = [], False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # A one-line docstring opens and closes on the same line.
            if not (len(stripped) > 3 and stripped.rstrip().endswith('"""')
                    and stripped.rstrip() != '"""'):
                in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


# --------------------------------------------------------------------------
# The declaration lives in the workflow definition
# --------------------------------------------------------------------------


def test_only_the_implementer_stage_declares_paths_it_may_not_create(harness_root):
    """Of the definition these runs execute. That *this repository* restricts
    exactly its implementer, and under the configured tests directory, is a
    question about what it deploys and is asked in
    tests/test_shipped_workflow_is_valid.py."""
    declared = {
        stage["name"]: stage.get("may_not_create")
        for stage in workflow_stages(harness_root)
    }
    assert declared[WRITING] == [GOVERNED_PREFIX]
    assert [name for name, value in declared.items() if value] == [WRITING]


def test_the_declaring_stage_also_declares_the_record_the_check_reads(harness_root):
    """may_not_create is only enforceable on a stage that produces a record."""
    for stage in workflow_stages(harness_root):
        if stage.get("may_not_create"):
            assert stage.get("changed_files"), stage["name"]


# --------------------------------------------------------------------------
# Enforcement: created is checked, modified and deleted are not
# --------------------------------------------------------------------------


def test_an_implementer_creating_a_test_file_escalates(target_root, harness_root):
    runner = Runner(target_root, records={
        WRITING: {"modified": ["src/app.py"],
                        "created": ["tests/test_new.py"], "deleted": []},
    })
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    assert state_of(target_root)["status"] == "escalated"
    assert runner.calls == [WRITING]


def test_the_escalation_names_the_stage_the_path_and_the_prefix(target_root,
                                                                harness_root):
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_new.py"],
                        "deleted": []},
    })
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 2
    events, summary = evidence(target_root)
    for text in (events, summary):
        assert WRITING in text
        assert "tests/test_new.py" in text
        assert "tests/" in text


def test_an_ownership_escalation_does_not_increment_retry_count(target_root,
                                                                harness_root):
    """It escalates the way a blocked-path violation does, not the way a
    failed verification does."""
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_new.py"],
                        "deleted": []},
    })
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 2
    assert state_of(target_root)["retry_count"] == 0


def ownership_only(tmp_path: Path, harness_root: Path) -> Path:
    """The shipped workflow with the implementer's revert_check declaration off.

    story-017 added a second check reading this same record: an edit under a
    governed prefix is permitted only if reverting it makes the suite fail.
    That is a decision about *modifications*, and these two tests are about the
    ownership rule, which reads `created` alone. Removing the declaration takes
    the newer check out of the picture — the subject, the record and the
    assertions below are exactly what they were — so what they show is that
    ownership does not escalate on a modification or a deletion. The revert
    check's own behavior on those records is story-017's to demonstrate.
    """
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        stage.pop("revert_check", None)
    return mirror_harness(tmp_path, harness_root, workflow)


def test_an_implementer_modifying_an_existing_test_does_not_escalate(target_root,
                                                                     harness_root,
                                                                     tmp_path):
    """A changed signature must be allowed to leave the suite compiling."""
    runner = Runner(target_root, records={
        WRITING: {"modified": ["src/app.py", "tests/test_app.py"],
                        "created": [], "deleted": []},
    })
    fake_root = ownership_only(tmp_path, harness_root)
    code = story_coordinator.run_story("story-001", fake_root, target_root, runner)
    assert code == 0
    assert state_of(target_root)["status"] == "completed"


def test_an_implementer_deleting_under_the_prefix_does_not_escalate(target_root,
                                                                    harness_root,
                                                                    tmp_path):
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": [],
                        "deleted": ["tests/test_obsolete.py"]},
    })
    fake_root = ownership_only(tmp_path, harness_root)
    assert story_coordinator.run_story("story-001", fake_root, target_root, runner) == 0


def test_a_path_merely_containing_the_prefix_is_not_a_violation(target_root,
                                                                harness_root):
    """The declaration is a path prefix, not a substring match."""
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["src/tests/helper.py"],
                        "deleted": []},
    })
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 0


def test_a_stage_that_declares_nothing_may_create_under_the_prefix(target_root,
                                                                   harness_root):
    """The tester's whole job is creating files under tests/."""
    runner = Runner(target_root, records={
        VALIDATING: {"modified": [], "created": ["tests/test_story_001.py"],
                   "deleted": []},
    })
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    assert runner.calls == STAGE_NAMES


# --------------------------------------------------------------------------
# The fixed post-stage order
# --------------------------------------------------------------------------


def test_a_missing_required_artifact_is_reported_before_ownership(target_root,
                                                                  harness_root):
    runner = Runner(
        target_root,
        records={WRITING: {"modified": [], "created": ["tests/t.py"],
                                 "deleted": []}},
        skip_outputs=("implementation-summary.md",),
    )
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 2
    _, summary = evidence(target_root)
    assert "implementation-summary.md" in summary
    assert "must not create" not in summary


def test_an_invalid_record_is_reported_before_ownership(target_root, harness_root):
    """A record the ownership check could not trust is a schema failure."""
    class InvalidRecordRunner(Runner):
        def __call__(self, prompt, *, stage, **kwargs):
            result = super().__call__(prompt, stage=stage, **kwargs)
            if stage == WRITING:
                write_json(self.run_dir / "changed-files.json",
                           {"modified": [], "created": ["tests/t.py"]})  # no deleted
            return result

    runner = InvalidRecordRunner(target_root)
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 2
    _, summary = evidence(target_root)
    assert "$.deleted" in summary
    assert "must not create" not in summary


def test_the_blocked_paths_check_runs_before_the_ownership_check(target_root,
                                                                 harness_root):
    """A record that violates both escalates as the blocked-path violation."""
    runner = Runner(target_root, records={
        WRITING: {"modified": ["rules/execution-rules.json"],
                        "created": ["tests/test_new.py"], "deleted": []},
    })
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 2
    _, summary = evidence(target_root)
    assert "blocked path" in summary
    assert "must not create" not in summary


# --------------------------------------------------------------------------
# The rule is read from the workflow definition, not written into the code
# --------------------------------------------------------------------------


def mirror_harness(tmp_path: Path, harness_root: Path, workflow: dict) -> Path:
    """A harness root identical to the real one but for its workflow file."""
    workflow = {**workflow, "name": WORKFLOW["name"]}
    return conftest.materialize_workflow(workflow, tmp_path / "harness")


def test_moving_the_declaration_moves_the_enforcement(target_root, harness_root,
                                                       tmp_path):
    """The strongest form of "no stage name and no prefix in the code": give
    the coordinator a workflow it has never seen, declaring a different prefix
    on a different stage, and the rule follows the declaration."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        stage.pop("may_not_create", None)
        if stage["name"] == VALIDATING:
            stage["may_not_create"] = ["src/"]
    fake_root = mirror_harness(tmp_path, harness_root, workflow)

    # The implementer creating under tests/ is now unrestricted; the tester
    # creating under src/ is the violation.
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_new.py"],
                        "deleted": []},
        VALIDATING: {"modified": [], "created": ["src/helper.py"], "deleted": []},
    })
    assert story_coordinator.run_story("story-001", fake_root, target_root, runner) == 2
    assert runner.calls == [WRITING, VALIDATING]
    events, summary = evidence(target_root)
    for text in (events, summary):
        assert VALIDATING in text
        assert "src/helper.py" in text
        assert "src/" in text


def test_a_workflow_declaring_nothing_enforces_nothing(target_root, harness_root,
                                                        tmp_path):
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        stage.pop("may_not_create", None)
    fake_root = mirror_harness(tmp_path, harness_root, workflow)

    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_new.py"],
                        "deleted": []},
    })
    assert story_coordinator.run_story("story-001", fake_root, target_root, runner) == 0


def test_no_path_prefix_is_named_in_orchestration_code():
    """tests/ is a fact about this workflow, not about the harness. The
    mechanism key may appear where the declaration is enforced
    (story_coordinator, this story) and where it is rendered for the planner
    (context_assembler, story-009); no module may name the prefix itself.

    story-040 adds the one module whose deliverable *is* to name it:
    harness_source.py declares the target-layout shapes its scan looks for,
    and cannot declare them without spelling them. It is exempt by name,
    and the exemption is held shut from both sides — that module must exist
    and must actually name the prefix, or the exemption is stale, and every
    other module is held to the assertion unchanged. It gains no latitude
    on the mechanism key, which it still may not name."""
    allowed_to_read_the_key = ("story_coordinator.py", "context_assembler.py")
    declares_the_prefix = "harness_source.py"

    declaring = ORCHESTRATION / declares_the_prefix
    assert declaring.is_file(), declares_the_prefix
    assert "tests/" in executable_source(declaring.read_text(encoding="utf-8"))

    for module in sorted(ORCHESTRATION.glob("*.py")):
        body = executable_source(module.read_text(encoding="utf-8"))
        if module.name != declares_the_prefix:
            assert "tests/" not in body, module.name
        assert ("may_not_create" not in body
                or module.name in allowed_to_read_the_key), module.name


def test_the_ownership_check_names_no_stage(target_root, harness_root):
    """Every stage name in the workflow, absent from the executable lines of
    the ownership machinery. The verifier routing branch predates this story
    and lives elsewhere, so the check is scoped to what this story added."""
    names = [stage["name"] for stage in workflow_stages(harness_root)]
    for function in (story_coordinator._ownership_violation,
                     story_coordinator.granted_paths,
                     story_coordinator.stage_exception_problems):
        body = executable_source(inspect.getsource(function))
        assert "created" in body or "stage" in body      # stripping kept code
        for name in names:
            assert name not in body, (function.__name__, name)


# --------------------------------------------------------------------------
# The story-granted exception
# --------------------------------------------------------------------------


def test_an_exception_lets_the_named_stage_create_under_the_prefix(target_root,
                                                                   harness_root):
    append_to_story(target_root, exception_block(WRITING, GOVERNED_PREFIX))
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_regression.py"],
                        "deleted": []},
    })
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    assert state_of(target_root)["status"] == "completed"


def test_the_applied_exception_is_recorded_in_the_event_log(target_root,
                                                            harness_root):
    append_to_story(target_root, exception_block(WRITING, GOVERNED_PREFIX))
    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_regression.py"],
                        "deleted": []},
    })
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 0
    events = (run_dir_of(target_root) / "events.log").read_text()
    assert "exception" in events
    assert WRITING in events
    assert "tests/" in events


def test_an_exception_does_not_lift_the_rule_for_another_stage(target_root,
                                                                harness_root,
                                                                tmp_path):
    """The grant is per stage: a workflow restricting two stages and a story
    granting one leaves the other restricted."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        if stage["name"] in (WRITING, VALIDATING):
            stage["may_not_create"] = ["tests/"]
    fake_root = mirror_harness(tmp_path, harness_root, workflow)
    append_to_story(target_root, exception_block(WRITING, GOVERNED_PREFIX))

    runner = Runner(target_root, records={
        WRITING: {"modified": [], "created": ["tests/test_a.py"], "deleted": []},
        VALIDATING: {"modified": [], "created": ["tests/test_b.py"], "deleted": []},
    })
    assert story_coordinator.run_story("story-001", fake_root, target_root, runner) == 2
    assert runner.calls == [WRITING, VALIDATING]
    _, summary = evidence(target_root)
    assert "tests/test_b.py" in summary
    assert "tests/test_a.py" not in summary


# --------------------------------------------------------------------------
# Pre-flight refusals
# --------------------------------------------------------------------------


def assert_refused_leaving_no_trace(target_root, harness_root, runner):
    before = branches(target_root)
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 1
    assert runner.calls == []
    run_dir = run_dir_of(target_root)
    assert not run_dir.exists()
    assert not (run_dir / "state.json").is_file()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    assert branches(target_root) == before
    assert "story/story-001" not in branches(target_root)


def test_an_exception_naming_an_unknown_stage_is_refused(target_root, harness_root,
                                                          capsys):
    append_to_story(target_root, exception_block("reviewer", "tests/"))
    assert_refused_leaving_no_trace(target_root, harness_root, Runner(target_root))
    assert "reviewer" in capsys.readouterr().err


def test_an_exception_granting_an_unrestricted_path_is_refused(target_root,
                                                                harness_root,
                                                                capsys):
    """An exception that grants nothing is a planning error, not a harmless
    one: the tester was never restricted from creating under tests/."""
    append_to_story(target_root, exception_block(VALIDATING, GOVERNED_PREFIX))
    assert_refused_leaving_no_trace(target_root, harness_root, Runner(target_root))
    err = capsys.readouterr().err
    assert VALIDATING in err
    assert "tests/" in err


def test_a_grant_naming_a_prefix_the_stage_never_declared_is_refused(target_root,
                                                                     harness_root,
                                                                     capsys):
    append_to_story(target_root, exception_block(WRITING, "docs/"))
    assert_refused_leaving_no_trace(target_root, harness_root, Runner(target_root))
    assert "docs/" in capsys.readouterr().err


def test_the_refusal_reaches_the_entry_point_the_same_way(target_root, tmp_path):
    """End to end: no agent can run, because the refusal happens above run
    directory creation and branch checkout."""
    # A harness root of its own, carrying the built definition *and* the entry
    # point: a script under `scripts/` resolves its own harness root from its
    # own location, so it is copied rather than reached for.
    harness_root = conftest.materialize_workflow(
        WORKFLOW, tmp_path / "entry-point-harness",
        copy=("orchestration", "scripts"))
    append_to_story(target_root, exception_block("reviewer", GOVERNED_PREFIX))
    # The timeout is a guard, not a tolerance: a refusal returns immediately,
    # and anything slower means the entry point reached agent invocation.
    result = subprocess.run(
        ["python3", str(harness_root / "scripts" / "l5-run"), "story-001"],
        cwd=target_root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1
    assert "reviewer" in result.stderr
    assert not run_dir_of(target_root).exists()
    assert not (target_root / ".harness" / "logs" / "story-001.log").exists()
    assert "story/story-001" not in branches(target_root)


def test_the_cross_check_is_a_function_over_the_story_and_the_workflow(harness_root):
    """It takes what it needs as arguments rather than reading the workflow
    itself, so a caller can hold it to a workflow the code never loaded."""
    stages = workflow_stages(harness_root)
    story = {"stage_exceptions": [
        {"stage": WRITING, "create": GOVERNED_PREFIX, "reason": "why"}]}
    assert story_coordinator.stage_exception_problems(story, stages) == []
    assert story_coordinator.stage_exception_problems({}, stages) == []

    unknown = {"stage_exceptions": [
        {"stage": "reviewer", "create": "tests/", "reason": "why"}]}
    problems = story_coordinator.stage_exception_problems(unknown, stages)
    assert len(problems) == 1
    assert "reviewer" in problems[0]

    ungranted = {"stage_exceptions": [
        {"stage": VALIDATING, "create": GOVERNED_PREFIX, "reason": "why"}]}
    problems = story_coordinator.stage_exception_problems(ungranted, stages)
    assert len(problems) == 1
    assert VALIDATING in problems[0]


def test_read_story_still_does_schema_conformance_only(target_root, harness_root):
    """A story whose exception names a stage no workflow defines is a
    conforming story: read_story has nothing to say about it."""
    path = append_to_story(target_root, exception_block("reviewer", "tests/"))
    reading = story_coordinator.read_story(path.read_text())
    assert reading.problems == []
    assert reading.parsed["stage_exceptions"][0]["stage"] == "reviewer"

    source = executable_source(inspect.getsource(story_coordinator.read_story))
    assert "stage_exception_problems" not in source
    assert "may_not_create" not in source


def test_both_refusals_happen_above_run_directory_creation():
    """Source order, because the behavior only shows the two cases tested
    above; this holds for any future problem the cross-check reports."""
    source = story_coordinator.run_story.__code__
    text = executable_source(inspect.getsource(story_coordinator.run_story))
    assert text.index("read_story(") < text.index("stage_exception_problems(")
    assert text.index("stage_exception_problems(") < text.index("run_dir.mkdir")
    assert text.index("stage_exception_problems(") < text.index("_checkout_story_branch")
    assert source is not None


# --------------------------------------------------------------------------
# The story schema
# --------------------------------------------------------------------------


def story_schema() -> dict:
    return schema_validator.load_schema("story")


def test_stage_exceptions_is_an_optional_top_level_property():
    schema = story_schema()
    assert "stage_exceptions" in schema["properties"]
    assert "stage_exceptions" not in schema["required"]
    section = schema["properties"]["stage_exceptions"]
    assert section["type"] == "array"
    item = section["items"]
    assert item["type"] == "object"
    assert set(item["required"]) == {"stage", "create", "reason"}
    for name in ("stage", "create", "reason"):
        assert item["properties"][name]["type"] == "string"


@pytest.mark.parametrize("missing", ["stage", "create", "reason"])
def test_an_exception_missing_any_required_field_fails_validation(missing):
    entry = {"stage": WRITING, "create": GOVERNED_PREFIX, "reason": "why"}
    del entry[missing]
    story = {
        "story": {"id": "story-x", "title": "t", "description": "d"},
        "tasks": ["t"], "acceptance_criteria": ["a"],
        "scope": {"modify": ["src/"], "do_not_modify": ["rules/"]},
        "verification_requirements": ["v"], "constraints": ["c"],
        "stage_exceptions": [entry],
    }
    problems = schema_validator.validate(story, story_schema())
    assert any(f"stage_exceptions[0].{missing}" in p or missing in p
               for p in problems), problems


def test_likely_file_changes_items_require_a_stage():
    schema = story_schema()
    item = (schema["properties"]["technical_plan"]["properties"]
            ["likely_file_changes"]["items"])
    assert set(item["required"]) == {"file", "reason", "stage"}
    assert item["properties"]["stage"]["type"] == "string"


def test_a_plan_entry_without_a_stage_fails_validation():
    story = {
        "story": {"id": "story-x", "title": "t", "description": "d"},
        "tasks": ["t"], "acceptance_criteria": ["a"],
        "technical_plan": {"likely_file_changes": [
            {"file": "src/app.py", "reason": "because"}]},
        "scope": {"modify": ["src/"], "do_not_modify": ["rules/"]},
        "verification_requirements": ["v"], "constraints": ["c"],
        # story-087 requires a mandate of every story. It is here so the one
        # problem below is the missing stage, which is this test's subject.
        "mandate": conftest.MANDATE_VALUE,
    }
    problems = schema_validator.validate(story, story_schema())
    assert len(problems) == 1
    assert "stage" in problems[0]
    assert "likely_file_changes" in problems[0]


# --------------------------------------------------------------------------
# The plan attribution stays advisory
# --------------------------------------------------------------------------


PLANNED_STORY = """\
story:
  id: story-001
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists

technical_plan:
  implementation_steps:
    - edit the predicted file
  likely_file_changes:
    - file: src/predicted.py
      stage: implementer
      reason: the plan expects this file to change
    - file: tests/test_predicted.py
      stage: tester
      reason: the tester writes the validation

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior

mandate:
  source:
    kind: human
  conferred_at: 2026-08-28 09:00:00
  conferred_by: A Developer <developer@example.com>
  recorded_by: l5-plan
"""


def test_a_stage_touching_a_file_the_plan_did_not_predict_does_not_escalate(
    target_root, harness_root
):
    (target_root / ".harness" / "stories" / "story-001.yaml").write_text(
        PLANNED_STORY, encoding="utf-8"
    )
    commit_setup(target_root, "the story artifact this test runs")
    runner = Runner(target_root, records={
        WRITING: {"modified": ["src/unpredicted.py"], "created": [],
                        "deleted": []},
        VALIDATING: {"modified": [], "created": ["tests/test_unpredicted.py"],
                   "deleted": []},
    })
    assert story_coordinator.run_story("story-001", harness_root, target_root, runner) == 0


#: Since story-032 the plan attribution has exactly one reader, and it reads
#: the plan at *plan time* to refuse an artifact rather than at run time to
#: route a stage. story-007's subject — the attribution is advisory, and no
#: run compares it against what a stage actually changed — is unchanged by
#: that and is what the assertions below still hold, now stated as "the
#: coordinator does not read it" plus "only this one module does".
PLAN_ATTRIBUTION_READER = "plan_validation.py"


def test_nothing_in_orchestration_reads_the_plan_attribution():
    readers = []
    for module in sorted(ORCHESTRATION.glob("*.py")):
        body = executable_source(module.read_text(encoding="utf-8"))
        if module.name == PLAN_ATTRIBUTION_READER:
            readers.append(module.name)
            continue
        assert "likely_file_changes" not in body, module.name
        assert "technical_plan" not in body, module.name
    # The exemption is not a hole: the one exempt module must exist and must
    # actually be a reader, or this test would pass by naming a file that is
    # gone or that never mentioned the field.
    assert readers == [PLAN_ATTRIBUTION_READER]
    exempt = executable_source(
        (ORCHESTRATION / PLAN_ATTRIBUTION_READER).read_text(encoding="utf-8")
    )
    assert "likely_file_changes" in exempt and "technical_plan" in exempt
    # And the load-bearing half of story-007's subject, stated directly: the
    # coordinator — every run-time routing decision there is — reads neither
    # name, so no run compares the attribution to what a stage did.
    coordinator = executable_source(
        (ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8")
    )
    assert "likely_file_changes" not in coordinator
    assert "technical_plan" not in coordinator


# --------------------------------------------------------------------------
# The schema era
# --------------------------------------------------------------------------


ERA_DEFINITIONS = ("tests/test_story_parser.py",
                   "tests/test_schema_directed_parsing.py")


def era_constant(relative: str) -> str:
    """The era constant one module defines, read out of its assignment.

    Parsed rather than executed. story-029 states, mechanically, that no
    module under `tests/` runs source in-process except the one shared loader
    in `conftest.py`, because that is the only way to say "no recovered module
    is loaded here" without naming helpers a rename can evade — and `exec` of
    a line lifted out of another module is running source. `ast.literal_eval`
    answers the same question and cannot run anything.

    Subject and strictness are unchanged: the value comes from the module's
    own assignment, and a module that does not define it still raises.
    """
    for node in ast.parse(
            (REPO_ROOT / relative).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name)
                and target.id == "FIRST_SCHEMA_ERA_STORY"
                for target in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError("FIRST_SCHEMA_ERA_STORY")


def test_both_definitions_of_the_era_constant_are_story_007():
    values = {path: era_constant(path) for path in ERA_DEFINITIONS}
    assert set(values.values()) == {"story-007"}, values


def test_stories_003_through_006_are_scoped_out_of_corpus_validation():
    era = era_constant(ERA_DEFINITIONS[0])
    scoped_out = [p.stem for p in sorted(STORIES_DIR.glob("*.yaml")) if p.stem < era]
    assert "story-003" in scoped_out
    assert "story-006" in scoped_out
    assert "story-007" not in scoped_out


def test_every_pre_era_story_still_parses():
    """Scoped out of validation, not out of parsing: the companion assertion
    still covers each of them."""
    era = era_constant(ERA_DEFINITIONS[0])
    schema = story_schema()
    legacy = [p for p in sorted(STORIES_DIR.glob("*.yaml")) if p.stem < era]
    assert len(legacy) >= 6
    for path in legacy:
        parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
        assert parsed["story"]["id"] == path.stem


def _no_committed_story_artifact_edited(repo: Path) -> bool:
    """Whether the story `repo` carries rewrote or removed a story artifact.

    Scoped to modifications and deletions, which is what "edited" has always
    meant here: a story's own commit *adds* its own artifact, and an addition
    was never an edit. The baseline is the story's own run commit against its
    parent, resolved by `conftest.story_commit_range` — not `git diff HEAD`,
    which asks whether the working tree is dirty and goes vacuously green the
    moment the story commits.
    """
    return story_diff(
        [".harness/stories/"],
        validation_file=Path(repo) / conftest.CONSTRUCTED_VALIDATION_REL,
        repo=Path(repo), diff_filter="MD", options=("--name-only",),
    ).strip() == ""


def test_no_committed_story_artifact_was_edited(tmp_path):
    """Execution records are never rewritten to satisfy a later contract.

    Restated over a story this test builds. Asked of this repository's own
    commit graph the assertion re-stated a frozen past fact and drew its
    evidence from a history that moves under it — a rename gives a path a new
    add-commit and silently empties the range, a squash makes the range
    unresolvable in a clone. The claim, the pathspec and the narrowing are
    unchanged; what moved is where the evidence comes from, and the two
    controls beside it show the same call reporting a rewrite and permitting
    an addition.
    """
    assert _no_committed_story_artifact_edited(
        conftest.constructed_story(tmp_path, respected=[".harness/stories/"],
                                   name="records-left-alone"))
    assert not _no_committed_story_artifact_edited(
        conftest.constructed_story(tmp_path, violated=[".harness/stories/"],
                                   name="records-rewritten"))
    # And what the narrowing lets through: an addition inside the story's own
    # run commit, which was never an edit.
    assert _no_committed_story_artifact_edited(
        conftest.constructed_story(tmp_path, violated=[".harness/stories/"],
                                   violation="add", name="records-added"))


def test_this_storys_own_artifact_parses_and_validates_under_the_new_schema():
    path = STORIES_DIR / "story-007.yaml"
    assert path.is_file()
    schema = story_schema()
    parsed = story_parser.parse(path.read_text(encoding="utf-8"), schema)
    # This artifact predates the mandate era, and a committed artifact is an
    # execution record that is not edited to satisfy a contract written after
    # it. So it is held to every part of the schema but the one requirement
    # added after it was written, through the shared relaxation that drops that
    # requirement and nothing else.
    assert path.stem < conftest.MANDATE_ERA_STORY
    assert schema_validator.validate(
        parsed, conftest.schema_without_the_mandate_requirement(schema)) == []
    for entry in parsed["technical_plan"]["likely_file_changes"]:
        assert entry["stage"]


# --------------------------------------------------------------------------
# Context assembly and the implementer prompt
# --------------------------------------------------------------------------


def build(target_root: Path, harness_root: Path, story_text: str) -> dict:
    run_dir = run_dir_of(target_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    return context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text, story_schema()),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(harness_root),
        workflow=WORKFLOW,
        retry_count=0,
    )


def test_stage_exceptions_render_as_one_dash_prefixed_line_each(target_root,
                                                                 harness_root):
    path = append_to_story(
        target_root, exception_block(WRITING, GOVERNED_PREFIX, "the suite is the story")
    )
    context = build(target_root, harness_root, path.read_text())
    lines = context["stage_exceptions"].splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("- ")
    assert WRITING in lines[0]
    assert "tests/" in lines[0]
    assert "the suite is the story" in lines[0]


def test_two_exceptions_render_as_two_lines(target_root, harness_root):
    path = append_to_story(
        target_root,
        exception_block(WRITING, GOVERNED_PREFIX, "first reason")
        + "  - stage: implementer\n    create: tests/\n    reason: second reason\n",
    )
    context = build(target_root, harness_root, path.read_text())
    lines = context["stage_exceptions"].splitlines()
    assert len(lines) == 2
    assert "first reason" in lines[0]
    assert "second reason" in lines[1]


def test_stage_exceptions_render_as_none_when_the_story_declares_none(target_root,
                                                                       harness_root):
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text()
    assert "stage_exceptions" not in story_text
    context = build(target_root, harness_root, story_text)
    assert context["stage_exceptions"] is None
    rendered = context_assembler.render(
        context_assembler.load_template(harness_root, writing_template()), context
    )
    assert "{{" not in rendered
    assert "None" in rendered


def test_the_implementer_prompt_injects_the_exceptions(harness_root):
    template = context_assembler.load_template(harness_root, writing_template())
    assert "{{stage_exceptions}}" in template


def test_the_implementer_prompt_states_the_create_modify_distinction():
    """The one assertion in this module whose subject is a *shipped* artifact:
    the prose this repository's own implementer template carries. A template
    the fixture generated could not carry it, so this reads what is deployed
    and says why."""
    shipped = Path(story_coordinator.__file__).resolve().parents[1]
    text = context_assembler.load_template(shipped, "story-implementer.md").lower()
    do_not = text.split("do not:")[1].split("[workflow layer]")[0]
    assert "modify" in do_not
    assert "create" in do_not
    assert "exception" in do_not
    # The old absolute phrasing is gone.
    assert "do not create new tests" not in text


def test_the_granted_exception_reaches_the_rendered_implementer_prompt(target_root,
                                                                       harness_root):
    """Asserted against the rendered dash-line rather than the reason text,
    which also appears in the raw {{story}} the same prompt injects."""
    path = append_to_story(
        target_root, exception_block(WRITING, GOVERNED_PREFIX, "the suite is the story")
    )
    context = build(target_root, harness_root, path.read_text())
    rendered = context_assembler.render(
        context_assembler.load_template(harness_root, writing_template()), context
    )
    assert context["stage_exceptions"] in rendered
    assert "{{" not in rendered


def test_every_stage_prompt_renders_with_no_leftover_placeholder(target_root,
                                                                  harness_root):
    stages = workflow_stages(harness_root)
    for story_suffix in ("", exception_block(WRITING, GOVERNED_PREFIX)):
        path = target_root / ".harness" / "stories" / "story-001.yaml"
        story_text = path.read_text() + story_suffix
        context = build(target_root, harness_root, story_text)
        for stage in stages:
            rendered = context_assembler.render(
                context_assembler.load_template(harness_root, stage["prompt"]), context
            )
            assert "{{" not in rendered, stage["prompt"]
            assert "}}" not in rendered, stage["prompt"]
