"""story-011: execution-history.json, written from the same path as events.log.

The story adds a structured rendering of the events events.log already
carries, beside it and never in place of it. These tests check the two
renderings cannot drift, that the log's line format is byte-identical to
what it was before this story, that the artifacts an entry names come from
the workflow definition, that the artifact validates against its schema for
a passing, a retried and an escalated run, and that nothing routes on it.

The frozen-format checks were originally differential: the pre-story
coordinator was loaded out of git history and run beside this one, so
"unchanged" meant measured against the previous implementation. story-016
removed those six comparisons. Merged, they no longer said "this story
changed nothing else" — they said the coordinator's output may never differ
from what one implementation produced on one day, which failed every later
story that legitimately adds an artifact or an event. The guarantees they
carried are stated directly in tests/test_coordinator_contract.py. What
survives here is what never depended on the historical coordinator: the
log-line-to-history correspondence, the ordering and retry-stream checks,
the schema conformance of a run's history, and the prompt-scope assertion
with the baseline resolution it needs.
"""
import ast
import inspect
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import context_assembler
import run_status
import schema_validator
import story_coordinator
from agent_runner import AgentResult
from conftest import commit_setup, first_retry_route, load_mutant
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is *what the run
#: records* — one event per write, in one order, naming the artifacts the stage
#: that produced them declared — and the stage list is an input to that
#: question rather than its subject.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
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
    name="execution-history-workflow",
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
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "history-harness")


#: Since story-028 a recommended retry must name a category the workflow's
#: retry_routing table defines, or the coordinator escalates rather than
#: routing it. Read off the loaded workflow, so this file still names no
#: stage and no category the definition does not.
RETRY_CATEGORY, RETRY_STAGE = first_retry_route(WORKFLOW)

LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
FAIL = {"status": "failed",
        "blocking_issues": [{"severity": "high", "issue": "sample behavior missing",
                             "location": "src/app.py",
                             "required_behavior": "sample behavior exists"}],
        "unverified": [], "retry_recommended": True,
        "retry_target": RETRY_CATEGORY}
FAIL_NO_RETRY = {**FAIL, "retry_recommended": False}


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class HistoryRunner:
    """A fake agent runner that writes each stage's declared artifacts.

    extra_outputs lets a stage produce an artifact this repository's workflow
    does not declare, which is how the artifact-list-is-derived test drives a
    workflow the repository does not ship.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001",
                 extra_outputs: tuple[str, ...] = (),
                 delete_history_each_stage: bool = False):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = list(verdicts)
        self.extra_outputs = extra_outputs
        self.delete_history_each_stage = delete_history_each_stage
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        if self.delete_history_each_stage:
            history = self.run_dir / "execution-history.json"
            if history.is_file():
                history.unlink()
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES, {
                "modified": ["src/app.py"], "created": [], "deleted": [],
            })
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
            for name in self.extra_outputs:
                (self.run_dir / name).write_text("extra output\n", encoding="utf-8")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 2, "tests_run": 5,
                "tests_passed": 5, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES, {
                "modified": [], "created": ["tests/test_app.py"], "deleted": [],
            })
        elif stage == VERIFYING:
            # A failed verdict accounts for the guidance in force for the
            # attempt it judges, reporting every entry unmet — the ordinary
            # under-delivery case, which routes as it always has.
            verdict = conftest.answering_guidance(
                self.verdicts.pop(0), self.run_dir)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                write_json(self.run_dir / conftest.RETRY_GUIDANCE, {
                    "current_focus": [{
                        "focus": "fix the sample behavior",
                        "satisfied_when": "the sample behavior exists",
                    }],
                    "preserve_behavior": ["existing behavior"],
                    "retry_scope": ["src/app.py"],
                })
        elif stage == DOCUMENTING:
            (self.run_dir / conftest.DOCUMENTATION_REPORT).write_text(
                "No changes needed.\n", encoding="utf-8")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def log_lines(run_dir: Path) -> list[str]:
    return (run_dir / "events.log").read_text(encoding="utf-8").splitlines()


def history_of(run_dir: Path) -> list[dict]:
    return json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def messages(lines: list[str]) -> list[str]:
    """The prose of each log line, with its timestamp prefix removed."""
    prose = []
    for line in lines:
        match = LINE.match(line)
        assert match is not None, f"line does not match the frozen format: {line!r}"
        prose.append(match.group(2))
    return prose


HISTORY_SCHEMA = schema_validator.load_schema("execution-history")


# --------------------------------------------------------------------------
# The three run shapes this story must hold for
# --------------------------------------------------------------------------


@pytest.fixture
def happy_path(target_root, harness_root):
    runner = HistoryRunner(target_root, [PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    return runner, run_dir_of(target_root)


@pytest.fixture
def retry_then_pass(target_root, harness_root):
    runner = HistoryRunner(target_root, [FAIL, PASS])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 0
    return runner, run_dir_of(target_root)


@pytest.fixture
def escalated(target_root, harness_root):
    runner = HistoryRunner(target_root, [FAIL, FAIL, FAIL])
    code = story_coordinator.run_story("story-001", harness_root, target_root, runner)
    assert code == 2
    return runner, run_dir_of(target_root)


# --------------------------------------------------------------------------
# Every line has an entry, in the same order, over a whole run
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["happy_path", "retry_then_pass", "escalated"])
def test_every_log_line_has_one_history_entry_in_the_same_order(shape, request):
    """The correspondence asserted over a full run, not over a single call.

    One entry per line, the same prose in the same order, and each entry's
    own timestamp reproducing the line it was written beside.
    """
    _, run_dir = request.getfixturevalue(shape)
    lines = log_lines(run_dir)
    history = history_of(run_dir)

    assert lines, "the run recorded no events"
    assert len(history) == len(lines)
    for index, (line, entry) in enumerate(zip(lines, history), start=1):
        assert line == f"[{entry['timestamp']}] {entry['message']}"
        assert entry["sequence"] == index


def test_the_retried_run_records_both_attempts_in_one_stream(retry_then_pass):
    """A retry does not restart, truncate or archive the history: it is one
    chronological stream across attempts, which is what makes a retried run
    reconstructable from it."""
    runner, run_dir = retry_then_pass
    history = history_of(run_dir)

    started = [e["stage"] for e in history if e["event"] == "stage-started"]
    assert started == runner.calls
    assert started == [
        *STAGE_NAMES, *STAGE_NAMES,
    ]
    assert [e["sequence"] for e in history] == list(range(1, len(history) + 1))
    # Not a stage output, so the retry archive neither copies nor overwrites it.
    assert not (run_dir / "attempts" / "attempt-1" / "execution-history.json").exists()


@pytest.mark.parametrize("shape", ["happy_path", "retry_then_pass", "escalated"])
def test_every_entry_naming_a_stage_names_one_the_workflow_defines(shape, request):
    _, run_dir = request.getfixturevalue(shape)
    for entry in history_of(run_dir):
        if "stage" in entry:
            assert entry["stage"] in STAGE_NAMES, entry


def test_each_stage_started_entry_matches_its_own_log_line(retry_then_pass):
    _, run_dir = retry_then_pass
    for entry in history_of(run_dir):
        if entry["event"] == "stage-started":
            assert entry["message"] == f"{entry['stage']} stage started"


def test_a_passing_runs_history_starts_and_ends_with_the_run(happy_path):
    _, run_dir = happy_path
    history = history_of(run_dir)
    assert history[0]["event"] == "workflow-started"
    assert history[-1]["event"] == "story-completed"


def test_an_escalated_runs_history_ends_with_its_escalation(escalated):
    """A run that failed is as reconstructable as one that passed."""
    _, run_dir = escalated
    history = history_of(run_dir)
    assert history[0]["event"] == "workflow-started"
    assert history[-1]["event"] == "escalated"
    assert "retries are exhausted" in history[-1]["message"]


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


def test_every_entry_carries_a_timestamp_in_the_logs_own_format(retry_then_pass):
    _, run_dir = retry_then_pass
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    for entry in history_of(run_dir):
        assert stamp.match(entry["timestamp"]), entry


def test_a_completed_stage_carries_an_elapsed_duration(retry_then_pass):
    """The log made a duration derivable by subtracting two stamps; the entry
    states it."""
    _, run_dir = retry_then_pass
    completions = [e for e in history_of(run_dir) if e["event"] == "stage-completed"]
    # implementer, tester and documenter twice: since story-045 the
    # documenter runs before the verifier, so a failed attempt has
    # already completed it.
    assert len(completions) == 6
    for entry in completions:
        assert isinstance(entry["duration_seconds"], (int, float))
        assert not isinstance(entry["duration_seconds"], bool)
        assert entry["duration_seconds"] >= 0


def test_a_stage_start_carries_no_duration_yet(retry_then_pass):
    _, run_dir = retry_then_pass
    for entry in history_of(run_dir):
        if entry["event"] == "stage-started":
            assert "duration_seconds" not in entry


@pytest.mark.parametrize("shape", ["happy_path", "retry_then_pass", "escalated"])
def test_every_event_that_ends_a_stage_carries_a_duration(shape, request):
    ending = ("stage-completed", "verification-passed", "verification-failed",
              "escalated")
    _, run_dir = request.getfixturevalue(shape)
    ended = [e for e in history_of(run_dir) if e["event"] in ending]
    assert ended
    for entry in ended:
        assert entry["duration_seconds"] >= 0, entry


# --------------------------------------------------------------------------
# Verifier outcome and retry decision
# --------------------------------------------------------------------------


def test_a_failed_verification_entry_carries_the_verifier_outcome(retry_then_pass):
    _, run_dir = retry_then_pass
    failed = [e for e in history_of(run_dir) if e["event"] == "verification-failed"]
    assert len(failed) == 1
    assert failed[0]["verifier_outcome"] == "failed"
    assert failed[0]["stage"] == VERIFYING


def test_a_rerouted_retry_entry_carries_the_decision_and_its_reason(retry_then_pass):
    _, run_dir = retry_then_pass
    entry = next(e for e in history_of(run_dir) if e["event"] == "verification-failed")
    assert entry["retry_decision"] == "retry"
    assert entry["retry_reason"]
    assert "retry" in entry["retry_reason"]
    assert "1 of 2" in entry["message"]


def test_a_passing_verification_entry_carries_the_passing_outcome(happy_path):
    _, run_dir = happy_path
    entry = next(e for e in history_of(run_dir) if e["event"] == "verification-passed")
    assert entry["verifier_outcome"] == "passed"
    assert "retry_decision" not in entry
    assert "retry_reason" not in entry


def test_the_exhausted_escalation_records_the_decision_that_routed_there(escalated):
    _, run_dir = escalated
    entry = history_of(run_dir)[-1]
    assert entry["event"] == "escalated"
    assert entry["verifier_outcome"] == "failed"
    assert entry["retry_decision"] == "escalate"
    assert "2" in entry["retry_reason"]


def test_an_escalation_without_a_recommended_retry_records_its_own_reason(
    target_root, harness_root,
):
    runner = HistoryRunner(target_root, [FAIL_NO_RETRY])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    entry = history_of(run_dir_of(target_root))[-1]
    assert entry["event"] == "escalated"
    assert entry["verifier_outcome"] == "failed"
    assert entry["retry_decision"] == "escalate"
    assert "did not recommend" in entry["retry_reason"]


def test_a_resumed_run_continues_the_one_stream_it_left_behind(
    target_root, harness_root,
):
    """The resume call site, and the numbering risk it carries.

    The log is appended to; the history is read back, extended and rewritten.
    A resumed run is where those two could most easily disagree — a restarted
    sequence, or a history that starts over while the log does not — so the
    correspondence is asserted across the seam rather than only within a
    single process.
    """
    run_dir = run_dir_of(target_root)
    run_dir.mkdir(parents=True)
    (run_dir / "verification").mkdir()
    story_coordinator.save_state(run_dir, story_coordinator.RunState(
        story_id="story-001", branch="story/story-001", current_stage=VALIDATING))
    story_coordinator.append_event(
        run_dir, "implementer stage started", kind="stage-started",
        stage=WRITING)
    story_coordinator.append_event(
        run_dir, "implementer stage completed", kind="stage-completed",
        stage=WRITING, artifacts=[conftest.CHANGED_FILES], duration_seconds=0.5)
    before = len(history_of(run_dir))

    runner = HistoryRunner(target_root, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0

    lines = log_lines(run_dir)
    history = history_of(run_dir)
    assert len(history) == len(lines) > before
    for index, (line, entry) in enumerate(zip(lines, history), start=1):
        assert line == f"[{entry['timestamp']}] {entry['message']}"
        assert entry["sequence"] == index

    resumed = history[before]
    assert resumed["event"] == "resumed"
    assert resumed["stage"] == VALIDATING
    assert resumed["sequence"] == before + 1
    assert schema_validator.validate(history, HISTORY_SCHEMA) == []


def test_an_escalation_before_the_verifier_still_writes_its_entry(
    target_root, harness_root,
):
    """A run that fails at the implementer is as reconstructable as one that
    reached a verdict: the history ends with the escalation either way."""
    class FailingRunner(HistoryRunner):
        def __call__(self, prompt, *, stage, **kwargs):
            super().__call__(prompt, stage=stage, **kwargs)
            return AgentResult(ok=False, result_text="process failed")

    runner = FailingRunner(target_root, [])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    history = history_of(run_dir_of(target_root))
    assert history[-1]["event"] == "escalated"
    assert history[-1]["stage"] == WRITING
    assert "verifier_outcome" not in history[-1]
    assert log_lines(run_dir_of(target_root))[-1].endswith(history[-1]["message"])


# --------------------------------------------------------------------------
# The artifacts an entry names come from the workflow definition
# --------------------------------------------------------------------------


def test_a_stage_completion_names_that_stages_declared_outputs(retry_then_pass):
    _, run_dir = retry_then_pass
    declared = {stage["name"]: stage.get("outputs", []) for stage in WORKFLOW["stages"]}
    completions = [e for e in history_of(run_dir) if e["event"] == "stage-completed"]
    assert completions
    for entry in completions:
        assert entry["artifacts"] == declared[entry["stage"]]


def test_the_verifier_entries_name_the_verifiers_declared_outputs(retry_then_pass):
    _, run_dir = retry_then_pass
    declared = next(
        s for s in WORKFLOW["stages"] if s["name"] == VERIFYING)["outputs"]
    for entry in history_of(run_dir):
        if entry["event"] in ("verification-passed", "verification-failed"):
            assert entry["artifacts"] == declared


@pytest.fixture
def probe_harness_root(tmp_path: Path) -> Path:
    """A harness root carrying a variant of the workflow built above, whose
    writing stage declares one extra output."""
    workflow = json.loads(json.dumps(WORKFLOW))
    for stage in workflow["stages"]:
        if stage["name"] == WRITING:
            stage["outputs"] = [*stage["outputs"], "design-notes.md"]
    workflow["name"] = "history-probe-workflow"
    return conftest.materialize_workflow(workflow, tmp_path / "probe-harness")


def test_an_artifact_list_no_orchestration_code_knows_about_reaches_the_entry(
    target_root, probe_harness_root,
):
    """The proof the list is derived: only the workflow definition changes, and
    the entry names the artifact the coordinator has never heard of."""
    config = target_root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            f"workflow: {WORKFLOW['name']}", "workflow: history-probe-workflow"),
        encoding="utf-8",
    )
    commit_setup(target_root, "run the history probe workflow")
    assert "design-notes.md" not in Path(
        story_coordinator.__file__).read_text(encoding="utf-8")

    runner = HistoryRunner(target_root, [PASS], extra_outputs=("design-notes.md",))
    assert story_coordinator.run_story(
        "story-001", probe_harness_root, target_root, runner) == 0

    entry = next(
        e for e in history_of(run_dir_of(target_root))
        if e["event"] == "stage-completed" and e["stage"] == WRITING
    )
    assert entry["artifacts"] == [
        conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY,
        "design-notes.md"]


def _function_body(name: str) -> str:
    """The named coordinator function's source, docstring removed."""
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


def _string_literals(function_name: str) -> set[str]:
    module = ast.parse(Path(story_coordinator.__file__).read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return {
        node.value for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_append_event_names_no_stage_and_no_stage_artifact_of_its_own():
    """The structured values are whatever the call site passed. A stage or
    artifact name appearing here would be a second, code-resident definition
    of what the workflow already declares."""
    literals = _string_literals("append_event")
    for stage in WORKFLOW["stages"]:
        assert stage["name"] not in literals, stage["name"]
    for artifact in story_coordinator.archivable_artifacts(WORKFLOW["stages"]):
        assert artifact not in literals, artifact


# --------------------------------------------------------------------------
# One write path
# --------------------------------------------------------------------------


def test_append_event_is_the_only_writer_of_the_history_file():
    """A second write path, however correct, is the drift this story exists to
    prevent: only append_event writes execution-history.json."""
    module = ast.parse(Path(story_coordinator.__file__).read_text(encoding="utf-8"))
    writers = []
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.unparse(node)
        if "_history_path" in body and "write_text" in body:
            writers.append(node.name)
    assert writers == ["append_event"]


def test_the_log_line_and_the_entry_are_written_by_the_same_call(tmp_path: Path):
    story_coordinator.append_event(tmp_path, "a bare note")
    story_coordinator.append_event(
        tmp_path, "a structured note", kind="stage-completed", stage=VALIDATING,
        artifacts=["test-results.json"], duration_seconds=1.5,
    )
    lines = log_lines(tmp_path)
    history = history_of(tmp_path)
    assert len(lines) == len(history) == 2
    assert messages(lines) == ["a bare note", "a structured note"]
    assert [e["sequence"] for e in history] == [1, 2]


def test_a_call_with_nothing_extra_to_say_is_unchanged(tmp_path: Path):
    """The message stays positional and the defaults keep an old call site
    working; an event with no structured values carries no empty keys."""
    story_coordinator.append_event(tmp_path, "just a message")
    entry = history_of(tmp_path)[0]
    assert entry["event"] == "note"
    assert set(entry) == {"sequence", "timestamp", "event", "message"}
    assert log_lines(tmp_path) == [f"[{entry['timestamp']}] just a message"]


def test_optional_values_are_omitted_rather_than_written_as_null(tmp_path: Path):
    story_coordinator.append_event(
        tmp_path, "partial", kind="verification-failed", stage=VERIFYING,
        verifier_outcome="failed",
    )
    entry = history_of(tmp_path)[0]
    assert "duration_seconds" not in entry
    assert "artifacts" not in entry
    assert None not in entry.values()


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------


def test_the_schema_uses_only_the_keywords_the_validator_supports():
    assert schema_validator.unsupported_keywords(HISTORY_SCHEMA) == []
    assert schema_validator.validate([], HISTORY_SCHEMA) == []


#: Every field an entry may carry and is not required to, held as a declared
#: set and compared in both directions: a field added to the schema without an
#: entry here fails, and an entry naming a field the schema no longer declares
#: fails too. The three counts at the end are an inspection's, present on an
#: entry that is one and absent from every entry that is not — which is exactly
#: what "optional" means here. The four beside them are an inspection's too:
#: which mode it was, what it cost, how large its scope was and how many
#: invocations that cost covers. `cost_usd` is absent where the invocation
#: reported no cost rather than written as zero, which is the same reading of
#: "optional" one step sharper — absent means the harness was told no figure.
OPTIONAL_FIELDS = {
    "stage", "artifacts", "duration_seconds", "verifier_outcome",
    "retry_decision", "retry_reason", "retry_category", "retry_stage",
    "findings", "filed", "dropped",
    "mode", "cost_usd", "scope_files", "invocations",
}


def test_optional_fields_are_expressed_by_absence_from_required():
    item = HISTORY_SCHEMA["items"]
    assert set(item["required"]) == {"sequence", "timestamp", "event", "message"}
    optional = set(item["properties"]) - set(item["required"])
    assert optional == OPTIONAL_FIELDS


def test_every_optional_field_is_one_append_event_can_be_given():
    """The other direction, against the writer rather than the schema.

    A declared optional field nothing can write would be a shape the history
    promises and no run can produce; a keyword the writer takes and the schema
    does not declare would be a field written into a document that forbids it.
    """
    keywords = set(inspect.signature(story_coordinator.append_event).parameters)
    assert OPTIONAL_FIELDS <= keywords
    assert keywords - OPTIONAL_FIELDS == {"run_dir", "message", "kind"}


def test_no_union_keyword_appears_anywhere_in_the_schema():
    """A union keyword would be how a schema smuggles in a constraint the
    validator cannot honor; absence from required is the supported way."""
    unions = {"oneOf", "anyOf", "allOf", "not", "nullable"}
    found = []

    def walk(node) -> None:
        if isinstance(node, dict):
            found.extend(key for key in node if key in unions)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(HISTORY_SCHEMA)
    assert found == []
    for value in ("type",):
        assert isinstance(HISTORY_SCHEMA[value], str)


@pytest.mark.parametrize("shape", ["happy_path", "retry_then_pass", "escalated"])
def test_the_history_a_run_produced_validates_against_the_schema(shape, request):
    _, run_dir = request.getfixturevalue(shape)
    assert schema_validator.validate(history_of(run_dir), HISTORY_SCHEMA) == []


@pytest.mark.parametrize("shape", ["happy_path", "retry_then_pass", "escalated"])
def test_every_recorded_event_kind_is_in_the_schemas_enum(shape, request):
    _, run_dir = request.getfixturevalue(shape)
    allowed = HISTORY_SCHEMA["items"]["properties"]["event"]["enum"]
    for entry in history_of(run_dir):
        assert entry["event"] in allowed, entry


def test_the_schema_catches_an_entry_missing_a_required_field():
    """The schema constrains something: it is not vacuously satisfied."""
    errors = schema_validator.validate(
        [{"sequence": 1, "timestamp": "2026-01-01 00:00:00", "event": "note"}],
        HISTORY_SCHEMA,
    )
    assert errors == ["$[0].message: expected a required property, found it missing"]


def test_the_schema_catches_an_event_kind_it_does_not_define():
    errors = schema_validator.validate(
        [{"sequence": 1, "timestamp": "2026-01-01 00:00:00",
          "event": "invented-kind", "message": "x"}],
        HISTORY_SCHEMA,
    )
    assert len(errors) == 1
    assert "$[0].event" in errors[0]


# --------------------------------------------------------------------------
# History is evidence, never state
# --------------------------------------------------------------------------


def test_no_routing_decision_reads_the_history_file():
    """Read the coordinator: the only reader is append_event, getting the next
    sequence number. Nothing branches on what it read."""
    source = Path(story_coordinator.__file__).read_text(encoding="utf-8")
    assert source.count("load_history(") == 2      # the definition and one call
    assert "load_history(" in _function_body("append_event")

    # The three functions that may mention the artifact at all: the path
    # helper, the reader, and the one writer.
    history_aware = {"_history_path", "load_history", "append_event"}
    module = ast.parse(source)
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name not in history_aware:
            assert "load_history" not in ast.unparse(node), node.name
            assert "_history_path" not in ast.unparse(node), node.name
            assert "execution-history" not in ast.unparse(node), node.name


def test_no_other_module_reads_the_history_file():
    for module in (context_assembler, run_status):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "execution-history" not in source
        assert "load_history" not in source


def test_a_run_whose_history_keeps_disappearing_routes_identically(
    target_root, harness_root, tmp_path,
):
    """The functional proof: delete the artifact before every stage and the
    run still retries, still escalates nowhere new, and ends in the same
    state a run that kept its history reached.

    The control is a run of this same coordinator with the artifact left
    alone. It used to be a run of the pre-story coordinator, loaded out of
    git history; story-016 replaced that baseline because the guarantee is
    "nothing routes on the history", which the control states directly, and
    the historical implementation grows more artificial to run with every
    story that passes.
    """
    control_root = tmp_path / "control-target"
    shutil.copytree(target_root, control_root)
    control_runner = HistoryRunner(control_root, [FAIL, PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, control_root, control_runner) == 0

    runner = HistoryRunner(target_root, [FAIL, PASS], delete_history_each_stage=True)
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0

    assert runner.calls == control_runner.calls
    assert read_state(run_dir_of(target_root)) == read_state(run_dir_of(control_root))
    assert messages(log_lines(run_dir_of(target_root))) == \
        messages(log_lines(run_dir_of(control_root)))


def test_the_retry_ceiling_and_its_counters_are_untouched(escalated):
    runner, run_dir = escalated
    state = read_state(run_dir)
    assert state["status"] == "escalated"
    assert state["retry_count"] == 2
    assert state["verification_iterations"] == 3
    assert runner.calls.count(WRITING) == 3
    # The run did not finish. Stated as the completion report's
    # absence rather than as the documenter never running: since
    # story-045 the documenter runs before the verifier, so an
    # escalating run has invoked it.
    assert not (run_dir / "completion-report.md").is_file()
    assert (run_dir / "verification" / "iteration-3.json").is_file()


# --------------------------------------------------------------------------
# The new schema file does not disturb any prompt
# --------------------------------------------------------------------------


def test_the_new_schema_becomes_an_injectable_placeholder(harness_root):
    context = context_assembler.schema_context(harness_root)
    assert "execution_history_schema" in context
    assert json.loads(context["execution_history_schema"]) == HISTORY_SCHEMA


def test_every_prompt_still_renders_with_no_leftover_placeholder(
    target_root, harness_root,
):
    import harness_config

    config = harness_config.load_config(target_root)
    rules = harness_config.load_rules(harness_root)
    story_text = (target_root / ".harness" / "stories" / "story-001.yaml").read_text(
        encoding="utf-8")
    run_dir = run_dir_of(target_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    context = context_assembler.build_context(
        story_text=story_text,
        story=story_coordinator.read_story(story_text).parsed,
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=config,
        rules=rules,
        workflow=WORKFLOW,
        retry_count=0,
    )
    for prompt_file in sorted(p.name for p in (harness_root / "prompts").glob("*.md")):
        template = context_assembler.load_template(harness_root, prompt_file)
        rendered = context_assembler.render(template, context)
        assert "{{" not in rendered, prompt_file


# --------------------------------------------------------------------------
# The checks above are not vacuous
# --------------------------------------------------------------------------


COORDINATOR_PATH = Path(story_coordinator.__file__)
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")

# Each mutant breaks exactly one guarantee this story is responsible for. The
# mutation is applied to a copy loaded as its own module; the repository's
# coordinator is never written to.
MUTANTS = {
    "a changed events.log line format": (
        'log.write(f"[{stamp}] {message}\\n")',
        'log.write(f"[{stamp}] :: {message}\\n")',
    ),
    "an event appended to only one rendering": (
        "    history.append(entry)",
        '    if kind != "stage-started":\n        history.append(entry)',
    ),
    "an artifact list written into orchestration code": (
        'artifacts=stage.get("outputs", []),\n                duration_seconds=elapsed(),',
        'artifacts=["changed-files.json"],\n                duration_seconds=elapsed(),',
    ),
    "a completed stage with no elapsed duration": (
        'artifacts=stage.get("outputs", []),\n                duration_seconds=elapsed(),',
        'artifacts=stage.get("outputs", []),',
    ),
    "a retry rerouted without its decision": (
        '                    retry_decision="retry",\n',
        "",
    ),
}


def mutant_history(name: str, tmp_path: Path, target_root: Path, harness_root: Path):
    """Run a retry-then-pass run through the named mutant; return its run dir.

    Built through `conftest.load_mutant` since story-029, the one place under
    tests/ that builds a module. The mutations, their anchors and every
    assertion below are unchanged.
    """
    module = load_mutant(
        COORDINATOR_PATH, [MUTANTS[name]], name="mutant_story_coordinator",
        tmp_path=tmp_path)
    runner = HistoryRunner(target_root, [FAIL, PASS])
    assert module.run_story("story-001", harness_root, target_root, runner) == 0
    return run_dir_of(target_root)


def test_a_changed_log_line_format_is_caught(tmp_path, target_root, harness_root):
    run_dir = mutant_history(
        "a changed events.log line format", tmp_path, target_root, harness_root)
    lines = log_lines(run_dir)
    history = history_of(run_dir)
    assert any(
        line != f"[{entry['timestamp']}] {entry['message']}"
        for line, entry in zip(lines, history)
    )


def test_an_event_written_to_only_one_rendering_is_caught(
    tmp_path, target_root, harness_root,
):
    run_dir = mutant_history(
        "an event appended to only one rendering", tmp_path, target_root, harness_root)
    assert len(history_of(run_dir)) != len(log_lines(run_dir))


def test_a_hard_coded_artifact_list_is_caught(tmp_path, target_root, harness_root):
    run_dir = mutant_history(
        "an artifact list written into orchestration code",
        tmp_path, target_root, harness_root)
    declared = {stage["name"]: stage.get("outputs", []) for stage in WORKFLOW["stages"]}
    assert any(
        entry["artifacts"] != declared[entry["stage"]]
        for entry in history_of(run_dir) if entry["event"] == "stage-completed"
    )


def test_a_missing_duration_is_caught(tmp_path, target_root, harness_root):
    run_dir = mutant_history(
        "a completed stage with no elapsed duration", tmp_path, target_root, harness_root)
    assert any(
        "duration_seconds" not in entry
        for entry in history_of(run_dir) if entry["event"] == "stage-completed"
    )


def test_a_history_that_restarts_on_resume_is_caught(
    tmp_path, target_root, harness_root,
):
    """Non-vacuity for the resume seam: a coordinator that starts a fresh
    history instead of reading back the one on disk breaks the
    line-for-entry correspondence, and the seam test must see it."""
    module = load_mutant(
        COORDINATOR_PATH,
        [("    history = load_history(run_dir)", "    history = []")],
        name="restarting_story_coordinator", tmp_path=tmp_path)
    assert "    history = load_history(run_dir)" in COORDINATOR_SOURCE

    run_dir = run_dir_of(target_root)
    run_dir.mkdir(parents=True)
    (run_dir / "verification").mkdir()
    module.save_state(run_dir, module.RunState(
        story_id="story-001", branch="story/story-001", current_stage=VALIDATING))
    module.append_event(run_dir, "implementer stage started", kind="stage-started",
                        stage=WRITING)

    runner = HistoryRunner(target_root, [PASS])
    assert module.run_story("story-001", harness_root, target_root, runner) == 0
    assert len(history_of(run_dir)) != len(log_lines(run_dir))


def test_a_retry_recorded_without_its_decision_is_caught(
    tmp_path, target_root, harness_root,
):
    run_dir = mutant_history(
        "a retry rerouted without its decision", tmp_path, target_root, harness_root)
    entry = next(e for e in history_of(run_dir) if e["event"] == "verification-failed")
    assert "retry_decision" not in entry


def test_no_prompt_template_was_changed_by_this_story(tmp_path):
    """Bounded at both ends of the story's own commit range.

    `git diff HEAD` answers "is the working tree dirty here", which stops
    being a statement about this story the moment the story is committed.
    Diffing a pre-story revision against the working tree fixes that and
    overshoots: it asserts nobody may ever change these paths again, and
    goes red on the next legitimate prompt edit by any later story.

    Both ends bounded, the question is the one the name asks — did *this
    story* touch these paths — and it stays answerable forever without
    freezing them. The range is resolved by `conftest.story_diff`, the
    shared resolution of a story's own commit range: the oldest commit that
    added this validation file, against that commit's parent. story-026
    folded this file's own copy of that resolution into the shared one, so
    the subject and the strictness are unchanged and only the two bounds
    come from somewhere else. While the story is still in flight the file
    has no adding commit and the range degrades to HEAD against the working
    tree, which is the correct pre-story baseline at that moment.

    Since story-053 the repository the range is resolved in is one this test
    builds. Asked of this repository's own graph the assertion re-stated a
    frozen fact and took its evidence from a history that moves under it: a
    rename gives a path a new add-commit and empties the range, a squash makes
    it unresolvable in a clone. The predicate and the paths are unchanged, and
    the control below shows the same call reporting a story that does touch
    them.
    """
    paths = ["prompts/", "workflows/", "rules/"]
    respecting = conftest.constructed_story(tmp_path, respected=paths,
                                            name="templates-left-alone")
    assert _templates_unchanged_by_this_story(respecting, paths)
    violating = conftest.constructed_story(tmp_path, violated=paths,
                                           name="templates-edited")
    assert not _templates_unchanged_by_this_story(violating, paths)


def _templates_unchanged_by_this_story(repo, paths) -> bool:
    """Whether a story's own run commit left `paths` alone, in `repo`.

    The predicate the assertion above is made of, named so it can be driven
    against a repository somebody else built:
    `tests/test_baseline_resolution_is_single.py` runs it over its own
    synthetic histories to show that the folded resolution still turns this
    module's assertion red when a story touches one of the three paths.
    """
    return conftest.constructed_story_diff(repo, list(paths)).strip() == ""


# --------------------------------------------------------------------------
# Every kind the coordinator can pass to append_event, whether or not a
# fixture run reaches it
#
# The runs above assert that the kinds *they* produced are declared in the
# schema, which leaves a kind no fixture happens to emit unchecked. story-058
# found one: `revert-check-permitted` had been emitted since story-017 and the
# enum never declared it, invisible because no schema-validating run had
# governed edits. These read the emitting code instead, so a kind added
# without its enum entry is reported rather than waited for.
# --------------------------------------------------------------------------


def kind_spellings(source: str) -> dict[str, set[str]]:
    """Each event kind an `append_event` call in `source` names, mapped to the
    names of the functions that spell it.

    `source` is a parameter rather than the coordinator's own text because the
    controls below drive this same scan over a copy carrying the defect it is
    meant to report. A kind assembled at runtime rather than written as a
    literal would be invisible to the scan, so one is refused here rather than
    silently dropped.
    """
    spellings: dict[str, set[str]] = {}

    def visit(node, enclosing: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "append_event"):
                kind = next(
                    (kw.value for kw in child.keywords if kw.arg == "kind"), None)
                if kind is not None:
                    assert isinstance(kind, ast.Constant), (
                        "a kind assembled at runtime cannot be checked against "
                        f"the enum: {ast.unparse(child)}")
                    spellings.setdefault(kind.value, set()).add(enclosing)
            inner = (child.name
                     if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else enclosing)
            visit(child, inner)

    visit(ast.parse(source), None)
    return spellings


def default_kind(source: str) -> str:
    """The kind a call that names none emits, read off `append_event`'s own
    signature. The API allows such a call, so it is a kind the coordinator can
    pass and the enum has to declare."""
    module = ast.parse(source)
    function = next(
        node for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "append_event")
    index = [arg.arg for arg in function.args.kwonlyargs].index("kind")
    return function.args.kw_defaults[index].value


def emittable_kinds(source: str) -> set[str]:
    return set(kind_spellings(source)) | {default_kind(source)}


#: A second emitter, appended to a copy of the coordinator's source. The kind
#: it names is substituted by the caller, which is what lets one template serve
#: both controls: an undeclared kind for the enum assertion, and a kind already
#: spelled elsewhere for the one-function assertion.
PLANTED_EMITTER = '''

def _planted_emitter(run_dir):
    append_event(run_dir, "planted", kind="{kind}")
'''

ALLOWED_KINDS = HISTORY_SCHEMA["items"]["properties"]["event"]["enum"]


def test_every_kind_the_coordinator_can_pass_to_append_event_is_declared():
    emitted = emittable_kinds(COORDINATOR_SOURCE)
    assert emitted, "the scan found no kinds at all, so it is looking nowhere"
    undeclared = sorted(emitted - set(ALLOWED_KINDS))
    assert undeclared == []


def test_a_kind_the_schema_does_not_declare_is_reported():
    """The control for the assertion above, and the state
    `revert-check-permitted` was in before story-058: an emitter naming a kind
    the enum has never heard of, which the same scan must report."""
    planted = "a-kind-the-enum-never-declared"
    assert planted not in ALLOWED_KINDS
    emitted = emittable_kinds(
        COORDINATOR_SOURCE + PLANTED_EMITTER.format(kind=planted))
    assert planted in emitted
    assert sorted(emitted - set(ALLOWED_KINDS)) == [planted]


def test_a_history_carrying_any_kind_the_coordinator_can_pass_validates():
    """Not only that the enum lists the kinds: that an entry carrying each one
    passes the validator the coordinator's own history is checked with. The
    control is `test_the_schema_catches_an_event_kind_it_does_not_define`
    above, where an invented kind is rejected."""
    for kind in sorted(emittable_kinds(COORDINATOR_SOURCE)):
        entry = {"sequence": 1, "timestamp": "2026-01-01 00:00:00",
                 "event": kind, "message": "something happened"}
        assert schema_validator.validate([entry], HISTORY_SCHEMA) == [], kind


def test_each_event_kind_is_spelled_in_exactly_one_function():
    """One kind, one emitter — the rule `append_event` itself is the instance
    of. A kind spelled at two call sites is two definitions of one event, and
    the second drifts from the first in message, stage or artifacts without
    anything noticing."""
    spellings = kind_spellings(COORDINATOR_SOURCE)
    assert spellings
    assert {kind: sorted(f for f in functions)
            for kind, functions in spellings.items() if len(functions) != 1} == {}


def test_a_kind_spelled_in_a_second_function_is_reported():
    """The control for the assertion above: an existing kind, taken from the
    scan rather than written down, spelled again somewhere else."""
    already = sorted(kind_spellings(COORDINATOR_SOURCE))[0]
    spellings = kind_spellings(
        COORDINATOR_SOURCE + PLANTED_EMITTER.format(kind=already))
    assert spellings[already] == {*kind_spellings(COORDINATOR_SOURCE)[already],
                                  "_planted_emitter"}
    assert len(spellings[already]) == 2
