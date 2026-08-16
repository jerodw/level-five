"""story-012: retry-history.json, the backward-looking record of each attempt.

retry-guidance.json looks forward — it tells the next implementer attempt
what to fix. This story adds the artifact that looks backward: one entry per
retry a run actually took, naming the attempt that failed, the blocking
issues the verifier recorded against it, the stage execution was rerouted to,
the guidance that failure produced, and the attempts/attempt-N/ directory
story-010 archived that attempt's own artifacts into.

What these tests cover: the entry count for each run shape, the absence of
the file on a run that never retried, the two escalation paths that take no
retry and so write nothing, the correspondence between an entry and the
archive it names, the blocking issues carried field for field rather than
summarized, schema conformance, and that nothing routes on any of it.

Every absence asserted here is paired with a demonstration that the same
check reports the violation it exists to catch — against a mutated copy of
the coordinator loaded as its own module, or against a run shape where the
subject is present. `orchestration/` is never written to.
"""
import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import first_retry_route, load_mutant, story_diff

import context_assembler
import run_status
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
WORKFLOW = json.loads(
    (REPO_ROOT / "workflows" / "story-workflow.json").read_text(encoding="utf-8"))

#: Read off the loaded workflow rather than written here, so this file names
#: no stage the definition does not.
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)
#: Since story-028 the route is a category-keyed table rather than a constant,
#: so the category a failing verdict names and the stage it routes to are read
#: off that table through the shared helper.
RETRY_CATEGORY, RETRY_STAGE = first_retry_route(WORKFLOW)
MAX_RETRIES = json.loads(
    (REPO_ROOT / "rules" / "execution-rules.json").read_text(encoding="utf-8")
)["max_retries"]

ARTIFACT = "retry-history.json"
GUIDANCE_ARTIFACT = "retry-guidance.json"
HISTORY_SCHEMA = schema_validator.load_schema("retry-history")
GUIDANCE_SCHEMA = schema_validator.load_schema("retry-guidance")

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing_verdict(attempt: int) -> dict:
    """A failing verdict whose fields all name the attempt that produced it.

    Every field differs per attempt, so an entry carrying the wrong attempt's
    findings — or a prose summary of them — is distinguishable from one
    carrying the right ones.
    """
    return {
        "status": "failed",
        "blocking_issues": [
            {
                "severity": "high",
                "issue": f"attempt {attempt} did not implement the sample behavior",
                "location": f"src/attempt_{attempt}.py",
                "required_behavior": f"the sample behavior exists after attempt {attempt}",
            },
            {
                "severity": "low",
                "issue": f"attempt {attempt} left a stray note",
                "location": f"src/notes_{attempt}.md",
                "required_behavior": f"no stray note after attempt {attempt}",
            },
        ],
        "unverified": [],
        "retry_recommended": True,
        "retry_target": RETRY_CATEGORY,
    }


def guidance_for(attempt: int) -> dict:
    """The guidance the verifier writes for the attempt *after* `attempt`."""
    return {
        "current_focus": [f"guidance issued after attempt {attempt}"],
        "preserve_behavior": ["existing behavior"],
        "retry_scope": [f"src/attempt_{attempt}.py"],
    }


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class RetryRunner:
    """A fake agent runner whose artifacts name the attempt that wrote them.

    It also records, at the entry to every stage, whether the retry history
    existed yet — which is how "created at the first retry, never in advance"
    is checked as a fact about the run rather than about its final state.
    """

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001",
                 delete_history_each_stage: bool = False):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = list(verdicts)
        self.delete_history_each_stage = delete_history_each_stage
        self.attempt = 1
        self.calls: list[str] = []
        #: (stage, whether retry-history.json existed when the stage started)
        self.history_seen: list[tuple[str, bool]] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        self.history_seen.append((stage, (self.run_dir / ARTIFACT).is_file()))
        if self.delete_history_each_stage:
            path = self.run_dir / ARTIFACT
            if path.is_file():
                path.unlink()
        # Both reroute paths return to the same stage, so counting its calls
        # is the attempt number whichever path routed here.
        self.attempt = max(1, self.calls.count(RETRY_STAGE))

        if stage == "implementer":
            write_json(self.run_dir / "changed-files.json", {
                "modified": ["src/app.py"],
                "created": [f"src/attempt_{self.attempt}.py"],
                "deleted": [],
            })
            (self.run_dir / "implementation-summary.md").write_text(
                f"Implemented on attempt {self.attempt}.\n", encoding="utf-8")
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
                write_json(self.run_dir / GUIDANCE_ARTIFACT,
                           guidance_for(self.attempt))
        elif stage == "documenter":
            (self.run_dir / "documentation-report.md").write_text(
                f"Documented after attempt {self.attempt}.\n", encoding="utf-8")
            write_json(self.run_dir / "documenter-changed-files.json",
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def records_of(run_dir: Path) -> list[dict]:
    return json.loads((run_dir / ARTIFACT).read_text(encoding="utf-8"))


def history_was_written(run_dir: Path) -> bool:
    """The one predicate every absence assertion below goes through.

    Stated once so the controls can drive the same code that the absence
    assertions do: a control that exercised a different check would say
    nothing about whether the assertion can fail.
    """
    return (run_dir / ARTIFACT).exists()


# --------------------------------------------------------------------------
# The four run shapes
# --------------------------------------------------------------------------


@pytest.fixture
def retry_then_pass(target_root, harness_root):
    runner = RetryRunner(target_root, [failing_verdict(1), PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    return runner, run_dir_of(target_root)


@pytest.fixture
def retries_exhausted(target_root, harness_root):
    """Two retries taken, then the ceiling escalates the third attempt."""
    runner = RetryRunner(
        target_root, [failing_verdict(1), failing_verdict(2), failing_verdict(3)])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    return runner, run_dir_of(target_root)


@pytest.fixture
def never_retried(target_root, harness_root):
    runner = RetryRunner(target_root, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    return runner, run_dir_of(target_root)


# --------------------------------------------------------------------------
# One entry per retry taken
# --------------------------------------------------------------------------


def test_two_retries_then_an_escalation_produce_exactly_two_entries(
    retries_exhausted,
):
    _, run_dir = retries_exhausted
    records = records_of(run_dir)
    assert [entry["attempt"] for entry in records] == [1, 2]
    assert [entry["retry_stage"] for entry in records] == [RETRY_STAGE, RETRY_STAGE]
    for attempt, entry in enumerate(records, start=1):
        assert entry["blocking_issues"] == failing_verdict(attempt)["blocking_issues"]


def test_one_retry_then_a_pass_produces_exactly_one_entry(retry_then_pass):
    _, run_dir = retry_then_pass
    records = records_of(run_dir)
    assert len(records) == 1
    assert records[0]["attempt"] == 1
    assert records[0]["retry_stage"] == RETRY_STAGE
    assert records[0]["blocking_issues"] == failing_verdict(1)["blocking_issues"]


@pytest.mark.parametrize("shape", ["retry_then_pass", "retries_exhausted"])
def test_the_entry_count_is_the_number_of_retries_the_run_took(shape, request):
    """The artifact and state.json agree about how many retries happened,
    without the artifact being what state.json is derived from."""
    _, run_dir = request.getfixturevalue(shape)
    assert len(records_of(run_dir)) == read_state(run_dir)["retry_count"]


def test_the_ceiling_escalation_adds_no_third_entry(retries_exhausted):
    """A third attempt ran and failed; it took no retry, so it has no entry.

    The three verifications and the third rendered prompt are asserted
    alongside, so this is not passing because the run stopped early.
    """
    _, run_dir = retries_exhausted
    state = read_state(run_dir)
    assert state["verification_iterations"] == 3
    assert (run_dir / "verification" / "iteration-3.json").is_file()
    assert (run_dir / f"prompt-{RETRY_STAGE}-attempt-3.md").is_file()
    assert [entry["attempt"] for entry in records_of(run_dir)] == [1, 2]


def _second_run(target_root: Path, harness_root: Path, tmp_path: Path,
                verdicts: list[dict], expected_code: int) -> Path:
    """Run a second shape against a pristine copy of the target repository.

    A run leaves its target finished — the coordinator refuses to re-run a
    story whose state has ended — so a test comparing two shapes needs two
    repositories. The copy is taken before either run touches it.
    """
    other = tmp_path / "second-target"
    shutil.copytree(target_root, other)
    runner = RetryRunner(other, verdicts)
    assert story_coordinator.run_story(
        "story-001", harness_root, other, runner) == expected_code
    return run_dir_of(other)


def test_a_run_that_never_retried_writes_no_history_at_all(
    target_root, harness_root, tmp_path,
):
    """The absence, with the run that does produce the file as its control.

    Both runs go through `history_was_written`; the completing run without a
    retry answers False and the retrying run answers True, so the predicate is
    looking at something that can differ.
    """
    retried = _second_run(target_root, harness_root, tmp_path,
                          [failing_verdict(1), PASS], 0)
    runner = RetryRunner(target_root, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0

    completed = run_dir_of(target_root)
    assert read_state(completed)["retry_count"] == 0
    assert read_state(completed)["status"] == "completed"
    assert not history_was_written(completed)

    assert read_state(retried)["retry_count"] == 1
    assert history_was_written(retried)


def test_an_escalation_without_a_recommended_retry_writes_no_entry(
    target_root, harness_root, tmp_path,
):
    """The same failing verdict routes here; only retry_recommended differs.

    The control is that other run: identical blocking issues, a retry taken,
    and an entry written.
    """
    retried = _second_run(target_root, harness_root, tmp_path,
                          [failing_verdict(1), PASS], 0)
    verdict = {**failing_verdict(1), "retry_recommended": False}
    runner = RetryRunner(target_root, [verdict])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2

    escalated = run_dir_of(target_root)
    state = read_state(escalated)
    assert state["status"] == "escalated"
    assert state["retry_count"] == 0
    assert state["verification_iterations"] == 1
    assert not history_was_written(escalated)

    assert history_was_written(retried)
    assert records_of(retried)[0]["blocking_issues"] == verdict["blocking_issues"]


def test_no_empty_file_and_no_empty_array_is_left_behind(never_retried):
    """Absent is not the same as present-and-empty: a reader that globs the
    run directory must not find an artifact claiming zero retries."""
    _, run_dir = never_retried
    assert ARTIFACT not in [p.name for p in run_dir.rglob("*")]


def test_the_file_appears_at_the_first_retry_and_not_before(retry_then_pass):
    """Observed during the run, not inferred from its end state: absent when
    every stage of attempt 1 started, present when attempt 2's first stage
    did."""
    runner, _ = retry_then_pass
    assert runner.history_seen == [
        ("implementer", False), ("tester", False), ("verifier", False),
        ("implementer", True), ("tester", True), ("verifier", True),
        ("documenter", True),
    ]


# --------------------------------------------------------------------------
# What an entry says about the attempt it describes
# --------------------------------------------------------------------------


def test_each_entry_names_an_archive_directory_that_exists_in_the_run(
    retries_exhausted,
):
    _, run_dir = retries_exhausted
    for entry in records_of(run_dir):
        named = run_dir / entry["archive_directory"]
        assert entry["archive_directory"] == f"attempts/attempt-{entry['attempt']}"
        assert named.is_dir()
        assert list(named.iterdir())


def test_the_archive_an_entry_names_holds_that_attempts_own_artifacts(
    retries_exhausted,
):
    """The correspondence is checked against the archive's contents, not its
    name: attempt N's entry must point at the directory holding attempt N's
    work, which the stamped artifacts make distinguishable."""
    _, run_dir = retries_exhausted
    for entry in records_of(run_dir):
        attempt = entry["attempt"]
        archive = run_dir / entry["archive_directory"]
        assert (archive / "implementation-summary.md").read_text(
            encoding="utf-8") == f"Implemented on attempt {attempt}.\n"
        archived_verdict = json.loads(
            (archive / "verification-result.json").read_text(encoding="utf-8"))
        assert archived_verdict["blocking_issues"] == entry["blocking_issues"]


def test_the_blocking_issues_are_the_verifiers_own_record_field_for_field(
    retries_exhausted,
):
    """Not a prose summary: every field the verifier recorded survives, with
    the same values, in the same order, and nothing is added."""
    _, run_dir = retries_exhausted
    for entry in records_of(run_dir):
        recorded = json.loads(
            (run_dir / "verification" / f"iteration-{entry['attempt']}.json"
             ).read_text(encoding="utf-8"))["blocking_issues"]
        assert entry["blocking_issues"] == recorded
        assert isinstance(entry["blocking_issues"], list)
        for issue in entry["blocking_issues"]:
            assert set(issue) == {"severity", "issue", "location",
                                  "required_behavior"}
        # Distinct severities in the fixture, so a check that collapsed them
        # to one value would show here.
        assert [issue["severity"] for issue in entry["blocking_issues"]] == [
            "high", "low"]


def test_each_entry_carries_the_guidance_written_for_the_following_attempt(
    retries_exhausted,
):
    _, run_dir = retries_exhausted
    for entry in records_of(run_dir):
        assert entry["guidance"] == guidance_for(entry["attempt"])
        assert schema_validator.validate(entry["guidance"], GUIDANCE_SCHEMA) == []


def test_the_entries_carry_different_attempts_findings(retries_exhausted):
    """The pair, compared against each other rather than each against a
    constant: an implementation that recorded the latest verdict twice would
    satisfy every per-entry check above and fail this one."""
    _, run_dir = retries_exhausted
    first, second = records_of(run_dir)
    assert first["blocking_issues"] != second["blocking_issues"]
    assert first["guidance"] != second["guidance"]
    assert first["archive_directory"] != second["archive_directory"]


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------


def test_the_schema_uses_only_the_keywords_the_validator_supports():
    assert schema_validator.unsupported_keywords(HISTORY_SCHEMA) == []
    assert schema_validator.validate([], HISTORY_SCHEMA) == []


def test_the_schema_is_declared_in_the_shipped_inventory():
    assert "retry-history" in schema_validator.shipped_schemas()
    assert (REPO_ROOT / "schemas" / "retry-history.schema.json").is_file()


def test_the_guidance_is_optional_and_the_rest_of_an_entry_is_not():
    item = HISTORY_SCHEMA["items"]
    assert set(item["required"]) == {
        "attempt", "blocking_issues", "retry_stage", "archive_directory"}
    assert set(item["properties"]) - set(item["required"]) == {"guidance"}


def test_no_union_keyword_appears_anywhere_in_the_schema():
    """A union keyword is how a schema smuggles in a constraint this
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
    # The control: the same walk over a schema that does carry one finds it.
    walk({"type": "array", "items": {"anyOf": [{"type": "string"}]}})
    assert found == ["anyOf"]


@pytest.mark.parametrize("shape", ["retry_then_pass", "retries_exhausted"])
def test_the_history_a_retrying_run_produced_validates_against_the_schema(
    shape, request,
):
    _, run_dir = request.getfixturevalue(shape)
    assert schema_validator.validate(records_of(run_dir), HISTORY_SCHEMA) == []


def test_the_schema_catches_an_entry_missing_a_required_field():
    """The schema constrains something: it is not vacuously satisfied."""
    errors = schema_validator.validate(
        [{"attempt": 1, "blocking_issues": [], "retry_stage": RETRY_STAGE}],
        HISTORY_SCHEMA,
    )
    assert errors == [
        "$[0].archive_directory: expected a required property, found it missing"]


def test_the_schema_catches_a_severity_it_does_not_define():
    errors = schema_validator.validate(
        [{"attempt": 1, "retry_stage": RETRY_STAGE,
          "archive_directory": "attempts/attempt-1",
          "blocking_issues": [{"severity": "catastrophic", "issue": "x",
                               "location": "y", "required_behavior": "z"}]}],
        HISTORY_SCHEMA,
    )
    assert len(errors) == 1
    assert "$[0].blocking_issues[0].severity" in errors[0]


def test_the_schema_catches_an_attempt_that_is_not_a_number():
    errors = schema_validator.validate(
        [{"attempt": "one", "blocking_issues": [], "retry_stage": RETRY_STAGE,
          "archive_directory": "attempts/attempt-1"}],
        HISTORY_SCHEMA,
    )
    assert len(errors) == 1
    assert "$[0].attempt" in errors[0]


# --------------------------------------------------------------------------
# retry-guidance.json is untouched
# --------------------------------------------------------------------------


def _left_alone_by_this_story(rel: str) -> bool:
    """Whether *this story's own change* left `rel` alone.

    Resolved through the shared baseline in tests/conftest.py — this story's
    run commit against its parent — rather than as the working tree against
    HEAD, which goes vacuously green the moment the coordinator commits.
    """
    return story_diff([rel], validation_file=Path(__file__)).strip() == ""


def test_the_retry_guidance_schema_is_unchanged():
    assert _left_alone_by_this_story("schemas/retry-guidance.schema.json")


def test_the_reader_that_injects_the_guidance_is_unchanged():
    assert _left_alone_by_this_story("orchestration/context_assembler.py")


def test_the_unchanged_assertions_above_can_fail():
    """The control for the two absences: the same resolution, over a path this
    story did change, reports the change.

    Without this, both assertions above would read identically if the baseline
    had silently resolved to something with nothing on either side of it.
    """
    assert not _left_alone_by_this_story("orchestration/story_coordinator.py")


def test_the_guidance_is_still_written_by_the_verifier_in_the_same_place(
    retry_then_pass,
):
    """Same content, same location, same schema. The coordinator writes no
    guidance of its own: the file at the run root is byte-for-byte what the
    verifier stage wrote."""
    _, run_dir = retry_then_pass
    path = run_dir / GUIDANCE_ARTIFACT
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == (
        json.dumps(guidance_for(1), indent=2) + "\n")
    assert schema_validator.validate(
        json.loads(path.read_text(encoding="utf-8")), GUIDANCE_SCHEMA) == []
    assert VERIFIER_STAGE["schemas"][GUIDANCE_ARTIFACT] == "retry-guidance"


def test_the_new_artifact_did_not_displace_the_guidance_in_the_prompt_context(
    retry_then_pass,
):
    """The forward-looking artifact still reaches the next attempt's prompt."""
    _, run_dir = retry_then_pass
    source = Path(context_assembler.__file__).read_text(encoding="utf-8")
    assert GUIDANCE_ARTIFACT in source
    rendered = (run_dir / f"prompt-{RETRY_STAGE}-attempt-2.md").read_text(
        encoding="utf-8")
    assert "guidance issued after attempt 1" in rendered
    # And the backward-looking record is not smuggled into the same prompt.
    assert "attempts/attempt-1" not in rendered


# --------------------------------------------------------------------------
# History is evidence, never state
# --------------------------------------------------------------------------

#: The only functions permitted to know the artifact exists: the path helper,
#: the reader the writer uses, and the writer — plus, since story-024, the one
#: function that renders the record into `escalation-summary.md`. Rendering a
#: recorded fact into a human report is not a routing decision: nothing
#: branches on what it reads and the summary routes nothing, which is exactly
#: what the scan below and its planted control still hold everywhere else.
RECORD_AWARE = ("_retry_record_file", "load_retry_records", "append_retry_record",
                "_retry_history_section")

#: The three ways a function could reach the artifact.
RECORD_REFERENCES = ("load_retry_records", "_retry_record_file", "retry-history")


def record_readers(source: str) -> list[str]:
    """Every function outside the writer trio that reaches the artifact.

    Stated as a function so the control below can run the identical scan over
    a source that does reach it. `append_retry_record` call sites are writes
    and are not references this looks for.
    """
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name in RECORD_AWARE:
            continue
        text = ast.unparse(node)
        if any(reference in text for reference in RECORD_REFERENCES):
            offenders.append(node.name)
    return offenders


COORDINATOR_PATH = Path(story_coordinator.__file__)
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")


def test_no_routing_decision_reads_the_retry_history():
    assert record_readers(COORDINATOR_SOURCE) == []
    # The reads in the module are the writer appending to what is there, and
    # the escalation summary rendering it.
    assert COORDINATOR_SOURCE.count("load_retry_records(") == 3
    body = ast.unparse(next(
        node for node in ast.parse(COORDINATOR_SOURCE).body
        if isinstance(node, ast.FunctionDef) and node.name == "append_retry_record"
    ))
    assert "load_retry_records(" in body


def test_the_reader_scan_reports_a_routing_read_that_was_planted():
    """The control: the same scan over a coordinator that branches on the
    artifact names the function that does it."""
    planted = COORDINATOR_SOURCE.replace(
        "    index = stage_names.index(state.current_stage)",
        "    if load_retry_records(run_dir):\n        return 9\n"
        "    index = stage_names.index(state.current_stage)",
        1,
    )
    assert planted != COORDINATOR_SOURCE
    assert record_readers(planted) == ["run_story"]


def test_no_other_module_reaches_the_artifact():
    for module in (context_assembler, run_status):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for reference in RECORD_REFERENCES:
            assert reference not in source, (module.__name__, reference)
    # The control for the same three strings: they are present where the
    # artifact is actually written.
    assert all(reference in COORDINATOR_SOURCE for reference in RECORD_REFERENCES)


def test_a_run_whose_retry_history_keeps_disappearing_routes_identically(
    target_root, harness_root, tmp_path,
):
    """The functional proof that nothing routes on it: delete the artifact
    before every stage and the run takes the same path, reaches the same
    state, and writes the same log as a run that kept it."""
    control_root = tmp_path / "control-target"
    shutil.copytree(target_root, control_root)
    control = RetryRunner(control_root, [failing_verdict(1), failing_verdict(2),
                                         failing_verdict(3)])
    assert story_coordinator.run_story(
        "story-001", harness_root, control_root, control) == 2

    runner = RetryRunner(target_root, [failing_verdict(1), failing_verdict(2),
                                       failing_verdict(3)],
                         delete_history_each_stage=True)
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2

    run_dir, control_dir = run_dir_of(target_root), run_dir_of(control_root)
    assert runner.calls == control.calls
    # Every field but the escalation commit, which since story-020 records the
    # commit each escalation makes on its own branch: these are two copies of
    # one repository, so the two shas differ for a reason that has nothing to
    # do with routing. Compared field by field so a new field is included by
    # default rather than needing to be added here.
    volatile = {"escalation_commit"}
    assert ({k: v for k, v in read_state(run_dir).items() if k not in volatile}
            == {k: v for k, v in read_state(control_dir).items()
                if k not in volatile})
    assert _log_messages(run_dir) == _log_messages(control_dir)
    # And the control run did keep a full history, so the comparison above is
    # between a run missing the artifact and one that had it.
    assert [entry["attempt"] for entry in records_of(control_dir)] == [1, 2]


def _log_messages(run_dir: Path) -> list[str]:
    return [line.split("] ", 1)[1]
            for line in (run_dir / "events.log").read_text(
                encoding="utf-8").splitlines() if "] " in line]


def test_the_retry_ceiling_and_its_counters_are_unchanged(retries_exhausted):
    runner, run_dir = retries_exhausted
    state = read_state(run_dir)
    assert state["status"] == "escalated"
    assert state["retry_count"] == MAX_RETRIES == 2
    assert state["verification_iterations"] == MAX_RETRIES + 1
    assert runner.calls.count(RETRY_STAGE) == MAX_RETRIES + 1
    assert "documenter" not in runner.calls
    assert (run_dir / "escalation-summary.md").is_file()


def test_execution_history_still_records_each_retry_decision(retries_exhausted):
    """The overlap is expected. This artifact is the retry-scoped record; the
    chronological stream still carries the same decisions among all events."""
    _, run_dir = retries_exhausted
    history = json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))
    decisions = [entry["retry_decision"] for entry in history
                 if "retry_decision" in entry]
    assert decisions == ["retry", "retry", "escalate"]
    assert schema_validator.validate(
        history, schema_validator.load_schema("execution-history")) == []
    assert len(history) == len(_log_messages(run_dir))


# --------------------------------------------------------------------------
# The absences above, shown failing against a mutated coordinator
#
# Each mutant breaks exactly one guarantee this story is responsible for. The
# mutation is applied to a copy loaded as its own module; orchestration/ is
# never written to.
# --------------------------------------------------------------------------


#: The call the two escalation-path mutants insert, at the indentation of the
#: `return _escalate(` they precede.
_PLANTED_APPEND = (
    '                append_retry_record(run_dir, state.retry_count + 1, '
    'stage["on_failure"]["retry_routing"][verdict["retry_target"]]["stage"], '
    'verdict, conditional_artifacts(stage))\n'
)

MUTANTS = {
    "the artifact created in advance": (
        '    (run_dir / "verification").mkdir(exist_ok=True)\n',
        '    (run_dir / "verification").mkdir(exist_ok=True)\n'
        '    _retry_record_file(run_dir).write_text("[]\\n", encoding="utf-8")\n',
    ),
    "an entry written at the retry ceiling": (
        '                return _escalate(\n'
        '                    run_dir,\n'
        '                    state,\n'
        '                    "verification failed and retries are exhausted",\n',
        _PLANTED_APPEND
        + '                return _escalate(\n'
          '                    run_dir,\n'
          '                    state,\n'
          '                    "verification failed and retries are exhausted",\n',
    ),
    "an entry written where no retry was recommended": (
        '                return _escalate(\n'
        '                    run_dir,\n'
        '                    state,\n'
        '                    "verification failed and the verifier did not '
        'recommend a retry",\n',
        _PLANTED_APPEND
        + '                return _escalate(\n'
          '                    run_dir,\n'
          '                    state,\n'
          '                    "verification failed and the verifier did not '
          'recommend a retry",\n',
    ),
    # The read the guidance fix replaced: the artifacts the workflow declares
    # a stage may write, rather than the ones this attempt actually wrote.
    "the guidance read off the run root unconditionally": (
        '                            artifacts_written_since(\n'
        '                                run_dir, conditional, artifacts_before\n'
        '                            ),\n',
        '                            conditional,\n',
    ),
}


def mutant(name: str, tmp_path: Path):
    """The named mutation applied to the working-tree coordinator.

    Built through `conftest.load_mutant` since story-029: the mutation-loading
    idiom this file shared byte for byte with `tests/test_story_014_validation.py`
    lives in one place, and it takes a working-tree path and its replacements
    rather than arbitrary source text, so source recovered out of git history
    is not a value it accepts. The mutations, their anchors and every
    assertion below are unchanged.
    """
    return load_mutant(
        COORDINATOR_PATH, [MUTANTS[name]],
        name=f"mutant_story_coordinator_{abs(hash(name))}", tmp_path=tmp_path)


def test_an_artifact_created_in_advance_is_caught(tmp_path, target_root,
                                                  harness_root):
    """The control for `test_a_run_that_never_retried_writes_no_history_at_all`
    and for `test_no_empty_file_and_no_empty_array_is_left_behind`."""
    module = mutant("the artifact created in advance", tmp_path)
    runner = RetryRunner(target_root, [PASS])
    assert module.run_story("story-001", harness_root, target_root, runner) == 0

    run_dir = run_dir_of(target_root)
    assert read_state(run_dir)["retry_count"] == 0
    assert history_was_written(run_dir)
    assert records_of(run_dir) == []
    assert ARTIFACT in [p.name for p in run_dir.rglob("*")]


def test_an_entry_written_at_the_retry_ceiling_is_caught(tmp_path, target_root,
                                                         harness_root):
    """The control for `test_the_ceiling_escalation_adds_no_third_entry` and
    for the two-entry count."""
    module = mutant("an entry written at the retry ceiling", tmp_path)
    runner = RetryRunner(
        target_root, [failing_verdict(1), failing_verdict(2), failing_verdict(3)])
    assert module.run_story("story-001", harness_root, target_root, runner) == 2

    run_dir = run_dir_of(target_root)
    assert [entry["attempt"] for entry in records_of(run_dir)] == [1, 2, 3]
    assert len(records_of(run_dir)) != read_state(run_dir)["retry_count"]


def test_an_entry_written_without_a_recommended_retry_is_caught(
    tmp_path, target_root, harness_root,
):
    """The control for
    `test_an_escalation_without_a_recommended_retry_writes_no_entry`."""
    module = mutant("an entry written where no retry was recommended", tmp_path)
    verdict = {**failing_verdict(1), "retry_recommended": False}
    runner = RetryRunner(target_root, [verdict])
    assert module.run_story("story-001", harness_root, target_root, runner) == 2

    run_dir = run_dir_of(target_root)
    assert read_state(run_dir)["retry_count"] == 0
    assert history_was_written(run_dir)
    assert [entry["attempt"] for entry in records_of(run_dir)] == [1]


# --------------------------------------------------------------------------
# The other path that reroutes: a clean-clone failure
#
# story-014 added a second rerouting path, after this story was planned. It
# takes a retry and increments retry_count, so the artifact must agree with
# state.json there too.
# --------------------------------------------------------------------------


def _make_the_test_command_fail(target_root: Path) -> None:
    """Point the target's configured test command at a command that fails.

    Only the configuration changes; the clean-clone failure below is the real
    check running the real command in a real clone. The edit is committed so
    the clone carries it.
    """
    config = target_root / ".harness" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "test_command: echo tests-ok", "test_command: false"),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(target_root), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(target_root), "-c", "user.email=t@t",
         "-c", "user.name=t", "commit", "-q", "-m", "a failing test command"],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def clean_clone_failing(target_root, harness_root):
    """A run whose verifications all pass and whose clean-clone check fails."""
    _make_the_test_command_fail(target_root)
    runner = RetryRunner(target_root, [PASS, PASS, PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    return runner, run_dir_of(target_root)


@pytest.fixture
def guidance_then_clean_clone_failure(target_root, harness_root):
    """Attempt 1 fails verification; attempt 2 passes it and fails the clone.

    The shape where the two reroute paths meet: guidance exists at the run
    root, written by the verifier after attempt 1 and addressed to attempt 2,
    and attempt 2's retry is routed by a check that follows a *passing*
    verdict, so no guidance was written for attempt 3.
    """
    _make_the_test_command_fail(target_root)
    runner = RetryRunner(target_root, [failing_verdict(1), PASS, PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    return runner, run_dir_of(target_root)


def test_a_clean_clone_reroute_is_recorded_like_any_other_retry(
    clean_clone_failing,
):
    _, run_dir = clean_clone_failing
    state = read_state(run_dir)
    assert state["status"] == "escalated"
    assert state["retry_count"] == MAX_RETRIES
    records = records_of(run_dir)
    assert [entry["attempt"] for entry in records] == [1, 2]
    assert len(records) == state["retry_count"]
    for entry in records:
        assert (run_dir / entry["archive_directory"]).is_dir()
        assert entry["retry_stage"] == RETRY_STAGE


def test_a_reroute_after_a_passing_verdict_carries_no_guidance(
    clean_clone_failing,
):
    """Nothing failed the verification, so no guidance was written for the
    next attempt, and the optional field is absent rather than invented."""
    _, run_dir = clean_clone_failing
    assert not (run_dir / GUIDANCE_ARTIFACT).exists()
    for entry in records_of(run_dir):
        assert "guidance" not in entry
        assert entry["blocking_issues"] == []
    assert schema_validator.validate(records_of(run_dir), HISTORY_SCHEMA) == []


def test_no_entry_carries_guidance_addressed_to_a_different_attempt(
    guidance_then_clean_clone_failure,
):
    """"The retry guidance written for the following attempt" — and no other.

    The guidance at the run root is not cleared between attempts, so it
    outlives the attempt it was written for. Attempt 1 failed verification and
    its guidance was written for attempt 2; attempt 2 then passed verification
    and was rerouted by the clean-clone check, which writes no guidance at
    all. Attempt 2's entry must therefore carry none — the field is optional
    precisely so an entry can stand without one. Carrying the file that
    happens to be lying at the root attributes attempt 1's guidance to
    attempt 2 and tells the documenter, the assist agent and the adjudicator
    that attempt 3 was instructed to do something nobody asked of it.

    The fixture stamps each guidance with the attempt it followed, so a
    correct entry for attempt N carries guidance_for(N) or nothing.
    """
    _, run_dir = guidance_then_clean_clone_failure
    records = records_of(run_dir)
    assert [entry["attempt"] for entry in records] == [1, 2]
    # Attempt 1's entry is the control: guidance was written for the attempt
    # that followed it, and the entry carries exactly that.
    assert records[0]["guidance"] == guidance_for(1)
    assert "guidance" not in records[1], records[1]["guidance"]
    # And the stale file really is lying at the root, so the assertion above
    # is not passing because there was nothing there to pick up.
    assert json.loads((run_dir / GUIDANCE_ARTIFACT).read_text(
        encoding="utf-8")) == guidance_for(1)


def test_a_coordinator_that_reads_the_root_guidance_carries_the_stale_one(
    tmp_path, target_root, harness_root,
):
    """The control for the absence in the test above.

    It asserts a field is *not* on an entry, which would read the same way if
    the shape never produced a stale file, if the entry never carried guidance
    at all, or if the check were looking at the wrong entry. So: the same run
    shape, against a coordinator whose reroute passes the artifacts the
    workflow declares instead of the ones this attempt actually wrote — the
    read the fix replaced — and attempt 2's entry does carry attempt 1's
    guidance, addressed to an attempt nobody wrote it for.
    """
    module = mutant("the guidance read off the run root unconditionally",
                    tmp_path)
    _make_the_test_command_fail(target_root)
    runner = RetryRunner(target_root, [failing_verdict(1), PASS, PASS])
    assert module.run_story("story-001", harness_root, target_root, runner) == 2

    records = records_of(run_dir_of(target_root))
    assert [entry["attempt"] for entry in records] == [1, 2]
    assert records[1]["guidance"] == guidance_for(1)


def test_the_conditional_artifact_set_is_the_verifiers_guidance_and_no_other():
    """What the coordinator narrows is derived from the loaded workflow, not
    from an artifact name written into orchestration code."""
    assert story_coordinator.conditional_artifacts(VERIFIER_STAGE) == [
        GUIDANCE_ARTIFACT]
    for stage in WORKFLOW["stages"]:
        if stage is not VERIFIER_STAGE:
            assert story_coordinator.conditional_artifacts(stage) == []
    # The name appears nowhere in the code that locates or records it — it is
    # read off the stage. (It does appear in prose elsewhere in the module,
    # which is why this is scoped to the two functions rather than the file.)
    tree = ast.parse(COORDINATOR_SOURCE)
    for name in ("conditional_artifacts", "append_retry_record"):
        node = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        assert GUIDANCE_ARTIFACT not in ast.unparse(node), name
    # The control for that absence: the same scan over a copy that does name
    # the artifact reports it.
    planted = ast.parse(COORDINATOR_SOURCE.replace(
        'def conditional_artifacts(stage: dict) -> list[str]:',
        'def conditional_artifacts(stage: dict) -> list[str]:\n'
        f'    _ = "{GUIDANCE_ARTIFACT}"', 1))
    node = next(n for n in planted.body
                if isinstance(n, ast.FunctionDef)
                and n.name == "conditional_artifacts")
    assert GUIDANCE_ARTIFACT in ast.unparse(node)


def test_an_artifact_left_untouched_across_a_window_counts_as_unwritten(
    tmp_path,
):
    """The predicate the fix turns on, exercised directly.

    The absence — "this attempt wrote no guidance" — is asserted against a
    file that is present the whole time, and the control is the same window
    with a write inside it.
    """
    (tmp_path / GUIDANCE_ARTIFACT).write_text(
        json.dumps(guidance_for(1)), encoding="utf-8")
    artifacts = [GUIDANCE_ARTIFACT]

    before = story_coordinator.artifact_signatures(tmp_path, artifacts)
    assert before  # the file is there; the window is not looking at nothing
    assert story_coordinator.artifacts_written_since(
        tmp_path, artifacts, before) == []

    write_json(tmp_path / GUIDANCE_ARTIFACT, guidance_for(2))
    assert story_coordinator.artifacts_written_since(
        tmp_path, artifacts, before) == [GUIDANCE_ARTIFACT]


def test_an_artifact_that_did_not_exist_before_the_window_counts_as_written(
    tmp_path,
):
    artifacts = [GUIDANCE_ARTIFACT]
    before = story_coordinator.artifact_signatures(tmp_path, artifacts)
    assert before == {}
    assert story_coordinator.artifacts_written_since(
        tmp_path, artifacts, before) == []

    write_json(tmp_path / GUIDANCE_ARTIFACT, guidance_for(1))
    assert story_coordinator.artifacts_written_since(
        tmp_path, artifacts, before) == [GUIDANCE_ARTIFACT]
