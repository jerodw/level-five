"""The coordinator's output contract, stated directly.

These are standing guarantees about what a run writes — `state.json`'s
shape, `events.log`'s line format, `escalation-summary.md`'s parts, and the
artifacts a completed run must produce. They are not one story's evidence,
and they are expected to be edited by any later story that deliberately
changes one of these shapes.

They replace the differential comparison story-011 used, which asserted that
the coordinator's output may never differ from what one implementation
produced on one day. That was the right instrument for story-011, whose
constraint was that adding `execution-history.json` changed nothing else;
once merged it froze the run directory against every later story that adds
an artifact. A shape stated outright can be read, argued with, and changed
on purpose. Equality with a frozen implementation can only be broken.

Each contract is a `*_problems()` function returning a list of violations,
so the shape is checkable against data that is not a real run — which is how
these assertions are shown to fail when the shape they name is violated,
here and in `tests/test_story_016_validation.py`.
"""
import ast
import dataclasses
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import story_coordinator
from agent_runner import AgentResult
from conftest import first_retry_route
import conftest

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is *the contract the
#: coordinator's output keeps* — which files a run writes, what statuses it may
#: record, what an escalation says — and a workflow is an input to that
#: question rather than its subject. Any workflow states the contract; reading
#: the deployed one made the contract's statement depend on the deployment.
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
    name="coordinator-contract-workflow",
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
        WORKFLOW, tmp_path / "contract-harness")

#: The retry category a failing verdict names, read off the loaded workflow.
#: Since story-028 a recommended retry must name a category the workflow's
#: retry_routing table defines, or the coordinator escalates rather than
#: routing it, so every failing verdict below carries one.
RETRY_CATEGORY, RETRY_STAGE = first_retry_route(WORKFLOW)

# Every status the coordinator may write. A run starts `running` and ends in
# one of ENDING_STATUSES; the source check below fails if a fourth appears.
# Ending is not the same as final: since story-020 an `escalated` run can be
# resumed, which returns its status to `running`. `completed` is the one a
# rerun still refuses.
ENDING_STATUSES = {"completed", "escalated"}
STATUSES = {"running", *ENDING_STATUSES}

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
FAIL = {"status": "failed",
        "blocking_issues": [{"severity": "high", "issue": "sample behavior missing",
                             "location": "src/app.py",
                             "required_behavior": "sample behavior exists"}],
        "unverified": [], "retry_recommended": True,
        "retry_target": RETRY_CATEGORY}


# --------------------------------------------------------------------------
# The contracts
# --------------------------------------------------------------------------


#: `events.log`'s frozen line format: `[%Y-%m-%d %H:%M:%S] <message>`. The
#: message class is deliberately narrow at its first character — a separator
#: inserted between the bracket and the prose ("] :: started") is exactly the
#: drift a `.*` tail would wave through.
LOG_LINE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ([A-Za-z0-9][^\n]*)$")

#: Beyond the stage outputs the workflow declares, what a completed run must
#: leave behind. This is a *required subset*: a run directory carrying more
#: than these satisfies it, which is what lets a later story add an artifact.
COORDINATOR_ARTIFACTS = ("state.json", "events.log", "execution-history.json",
                         "completion-report.md")


def required_artifacts(workflow: dict = WORKFLOW) -> set[str]:
    """The artifacts a completed run must produce, derived from the workflow.

    Stage outputs come off the loaded definition rather than a list written
    here, for the same reason the coordinator reads them there.
    """
    names = set(COORDINATOR_ARTIFACTS)
    for stage in workflow["stages"]:
        names.update(stage.get("outputs", []))
    return names


def state_contract_problems(state: dict) -> list[str]:
    """Violations of `state.json`'s contract, one string each.

    The field set is read from `RunState` rather than typed out, so this
    cannot silently disagree with the definition it describes: adding,
    removing or renaming a field of the dataclass without the coordinator
    writing it — or the reverse — is a violation.
    """
    expected_types = {
        "story_id": str,
        "branch": str,
        "status": str,
        "current_stage": str,
        "retry_count": int,
        "verification_iterations": int,
        "artifacts": list,
        # story-020's resume fields. Each defaults to empty, which is what a
        # state file written before this story loads as and what every reader
        # treats as "not established".
        "story_digest": str,
        "escalation_commit": str,
        "harness_revision": str,
        # story-036's self-route budget counter. Defaulted like the fields
        # above, so a state file written before this story loads with a zero
        # count, which is what "no self-route has occurred" means. It is the
        # live count for the current stage only; how many self-routes a run
        # took is read from the history.
        "self_route_count": int,
        # story-050's guidance in force: the entries of the retry guidance
        # directing the attempt now running, recorded so the check on the
        # next verdict reads state rather than retry-history.json or the
        # attempts/ archive. Defaulted like the fields above, and empty means
        # no guidance is in force.
        "guidance_in_force": list,
        # story-055's correction-pass counter: how many correction passes this
        # run has taken, bounded by the budget the workflow declares.
        # Defaulted like the fields above, and zero means none has been taken.
        # Unlike self_route_count it is cumulative over the run, because the
        # bound is one pass per run and that bound is what makes the mechanism
        # terminate.
        "correction_pass_count": int,
    }
    declared = {f.name for f in dataclasses.fields(story_coordinator.RunState)}
    problems = []
    if declared != set(expected_types):
        problems.append(
            f"RunState declares {sorted(declared)}, this contract pins types for "
            f"{sorted(expected_types)}"
        )
    if set(state) != declared:
        problems.append(
            f"state.json holds {sorted(state)}, RunState declares {sorted(declared)}"
        )
    for field, expected in expected_types.items():
        if field not in state:
            continue
        value = state[field]
        if isinstance(value, bool) or not isinstance(value, expected):
            problems.append(
                f"{field}: expected {expected.__name__}, found "
                f"{type(value).__name__} ({value!r})"
            )
    if isinstance(state.get("artifacts"), list):
        for item in state["artifacts"]:
            if not isinstance(item, str):
                problems.append(f"artifacts: expected str items, found {item!r}")
    if isinstance(state.get("status"), str) and state["status"] not in STATUSES:
        problems.append(
            f"status: expected one of {sorted(STATUSES)}, found {state['status']!r}")
    return problems


def log_format_problems(lines: list[str]) -> list[str]:
    """Violations of `events.log`'s frozen line format, one string each."""
    problems = []
    for line in lines:
        match = LOG_LINE.match(line)
        if match is None:
            problems.append(f"line does not match [stamp] message: {line!r}")
            continue
        try:
            datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            problems.append(f"timestamp is not %Y-%m-%d %H:%M:%S: {line!r}")
    return problems


def completion_commit_problems(message: str, *, story_id: str,
                               title: str) -> list[str]:
    """Violations of the completion commit's message contract, one string each.

    What `_complete` commits is a standing guarantee and not one story's
    evidence: the subject is `<story-id>: <title>`, a blank line follows it,
    and the body carries the marker sentence. Since story-027 that shape is
    also *read* — the pre-flight that refuses a re-run onto a branch already
    holding the story's finished work recognizes a completion commit by it — so
    a story that changes the message has to change this deliberately and knows
    what else moves when it does.

    The marker is read off `COMPLETION_COMMIT_MARKER` rather than typed out, so
    this cannot silently disagree with the constant the coordinator writes and
    the recognizer matches.
    """
    problems = []
    lines = message.rstrip("\n").split("\n")
    expected_subject = f"{story_id}: {title}"
    if lines[0] != expected_subject:
        problems.append(
            f"subject: expected {expected_subject!r}, found {lines[0]!r}")
    if len(lines) < 2 or lines[1] != "":
        problems.append("no blank line separates the subject from the body")
    body = "\n".join(lines[2:])
    if story_coordinator.COMPLETION_COMMIT_MARKER not in body:
        problems.append(
            f"body does not carry "
            f"{story_coordinator.COMPLETION_COMMIT_MARKER!r}: {body!r}")
    return problems


def section_of(text: str, heading: str) -> str:
    """One `## heading` section's body, or "" when the section is absent.

    Split on `\\n## ` with its trailing space, so a `###` subheading inside a
    section — which `## Retry History` writes, one per recorded retry — does
    not end it.
    """
    marker = f"## {heading}"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].split("\n## ", 1)[0]


def issue_problems(where: str, section: str, issues: list[dict]) -> list[str]:
    """Each blocking issue's four fields, as the verifier recorded them.

    The four are checked individually rather than as one rendered line, so
    the contract states the *fields* a reader must be handed and leaves the
    layout free. A section carrying a count, a summary, or a pointer at the
    artifact the issues came from fails every one of them.
    """
    problems = []
    for issue in issues:
        for field in ("severity", "issue", "location", "required_behavior"):
            value = str(issue.get(field, ""))
            if value and value not in section:
                problems.append(f"{where}: {field} {value!r} is not rendered")
    return problems


def escalation_summary_problems(
    text: str, *, story_id: str, stage: str, retry_count: int,
    blocking_issues: list[dict] | None = None,
    retries: list[dict] | None = None,
) -> list[str]:
    """Violations of `escalation-summary.md`'s contract, one string each.

    Its five unconditional parts: a heading naming the story, the escalated
    status, the reason, the stage and retry count where execution stopped,
    and a pointer at events.log and the verification directory.

    Since story-024 the summary carries the finding rather than a pointer to
    it, and this states three further parts:

      * `## Recommended Investigation`, on every escalation, naming how the
        run is continued and when that is refused;
      * `## Outstanding Issues`, iff a failing verdict stands behind the
        escalation, carrying each blocking issue's four recorded fields;
      * `## Retry History`, iff the run took a retry, carrying each recorded
        entry's attempt, destination, archive and blocking issues.

    The two conditional sections are stated in *both* directions, which is
    what makes them assertable: `blocking_issues=[]` and `retries=[]` say the
    run had none and require the heading to be absent — not present and
    empty, which is the failure mode story-024 exists to remove. `None` is
    the caller declining to say, and asserts nothing either way.
    """
    problems = []
    heading = text.splitlines()[0] if text.splitlines() else ""
    if not (heading.startswith("# ") and story_id in heading):
        problems.append(f"heading does not name the story: {heading!r}")
    if "## Status" not in text or "Escalated" not in text:
        problems.append("the escalated status is missing")
    if "## Reason" not in text:
        problems.append("the reason section is missing")
    else:
        reason = text.split("## Reason", 1)[1].split("##", 1)[0].strip()
        if not reason:
            problems.append("the reason section is empty")
    if f"Stage: {stage}" not in text:
        problems.append(f"the stage execution stopped at ({stage}) is missing")
    if f"retry count: {retry_count}" not in text:
        problems.append(f"the retry count ({retry_count}) is missing")
    # Read inside `## Where to Look` rather than anywhere in the summary.
    # Since story-024 other sections name `verification/iteration-N.json` and
    # list `events.log` among the run's artifacts, so a whole-text search
    # would be satisfied by them and this part could be deleted unnoticed —
    # which is what `tests/test_story_016_validation.py` mutates to check.
    where_to_look = section_of(text, "Where to Look")
    if "events.log" not in where_to_look:
        problems.append("the pointer to events.log is missing")
    if "verification/" not in where_to_look:
        problems.append("the pointer to the verification directory is missing")

    investigation = section_of(text, "Recommended Investigation")
    if not investigation.strip():
        problems.append("the recommended investigation section is missing")
    else:
        if f"l5-run {story_id}" not in investigation:
            problems.append(
                f"the way to continue the run (l5-run {story_id}) is missing")
        if "--stage" not in investigation:
            problems.append("the --stage override is missing")
        if not ("refused" in investigation and "unchanged" in investigation):
            problems.append(
                "the refusal of a resume while nothing has changed is missing")

    if blocking_issues is not None:
        outstanding = section_of(text, "Outstanding Issues")
        if blocking_issues and "## Outstanding Issues" not in text:
            problems.append(
                f"a failing verdict stands behind this escalation and its "
                f"{len(blocking_issues)} blocking issue(s) are not reported")
        elif not blocking_issues and "## Outstanding Issues" in text:
            problems.append(
                "an outstanding issues section is emitted with no failing "
                "verdict behind it")
        else:
            problems += issue_problems(
                "outstanding issues", outstanding, blocking_issues)

    if retries is not None:
        history = section_of(text, "Retry History")
        if retries and "## Retry History" not in text:
            problems.append(
                f"this run took {len(retries)} retr(ies) and none are reported")
        elif not retries and "## Retry History" in text:
            problems.append(
                "a retry history section is emitted by a run that never retried")
        elif text.count("## Retry History") > 1:
            problems.append("the retry history is split across sections")
        else:
            for entry in retries:
                attempt = entry.get("attempt")
                if f"{attempt}" not in history:
                    problems.append(f"retry {attempt} is not reported")
                for field in ("retry_stage", "archive_directory"):
                    value = str(entry.get(field, ""))
                    if value and value not in history:
                        problems.append(
                            f"retry {attempt}: {field} {value!r} is not reported")
                problems += issue_problems(
                    f"retry {attempt}", history, entry.get("blocking_issues", []))
    return problems


# --------------------------------------------------------------------------
# Real runs to assert them against
# --------------------------------------------------------------------------


class FakeRunner:
    """Writes each stage's declared artifacts; never invokes a model."""

    def __init__(self, target_root: Path, verdicts: list[dict],
                 story_id: str = "story-001"):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = list(verdicts)
        self.calls: list[str] = []

    def _write_json(self, name: str, payload) -> None:
        (self.run_dir / name).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None):
        self.calls.append(stage)
        if stage == WRITING:
            self._write_json(conftest.CHANGED_FILES, {
                "modified": ["src/app.py"], "created": [], "deleted": [],
            })
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VALIDATING:
            self._write_json(conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 2, "tests_run": 5,
                "tests_passed": 5, "tests_failed": 0, "failures": [],
            })
            self._write_json(conftest.TESTER_CHANGED_FILES, {
                "modified": [], "created": ["tests/test_app.py"], "deleted": [],
            })
        elif stage == VERIFYING:
            # A failed verdict accounts for the guidance in force for the
            # attempt it judges, reporting every entry unmet — the ordinary
            # under-delivery case, which routes as it always has.
            verdict = conftest.answering_guidance(
                self.verdicts.pop(0), self.run_dir)
            self._write_json(conftest.VERIFICATION_RESULT, verdict)
            if verdict["status"] == "failed":
                self._write_json(conftest.RETRY_GUIDANCE, {
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
            self._write_json(conftest.DOCUMENTER_CHANGED_FILES, {
                "modified": [], "created": [], "deleted": [],
            })
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def log_lines(run_dir: Path) -> list[str]:
    return (run_dir / "events.log").read_text(encoding="utf-8").splitlines()


def read_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


@pytest.fixture
def completed_run(target_root, harness_root) -> Path:
    runner = FakeRunner(target_root, [PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    return run_dir_of(target_root)


@pytest.fixture
def retried_run(target_root, harness_root) -> Path:
    runner = FakeRunner(target_root, [FAIL, PASS])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 0
    return run_dir_of(target_root)


@pytest.fixture
def escalated_run(target_root, harness_root) -> Path:
    runner = FakeRunner(target_root, [FAIL, FAIL, FAIL])
    assert story_coordinator.run_story(
        "story-001", harness_root, target_root, runner) == 2
    return run_dir_of(target_root)


# --------------------------------------------------------------------------
# state.json
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["completed_run", "retried_run", "escalated_run"])
def test_state_json_holds_the_fields_run_state_declares(shape, request):
    """Asserted against the file a real run wrote, not a dictionary built here."""
    run_dir = request.getfixturevalue(shape)
    assert state_contract_problems(read_state(run_dir)) == []


def test_the_state_field_set_is_read_from_the_dataclass_not_typed_out():
    """The contract cannot drift from the definition it describes: a field
    added to RunState and not written to state.json is a violation, and so is
    the reverse."""
    declared = {f.name for f in dataclasses.fields(story_coordinator.RunState)}
    assert state_contract_problems({name: object() for name in declared})
    assert any(
        "RunState declares" in problem
        for problem in state_contract_problems(
            {f.name: "" for f in dataclasses.fields(story_coordinator.RunState)}
            | {"invented_field": ""})
    )


def test_a_completed_run_ends_completed(completed_run):
    """The pinned ending statuses are the ones runs actually reach."""
    assert read_state(completed_run)["status"] == "completed"
    assert read_state(completed_run)["status"] in ENDING_STATUSES


def test_an_escalated_run_ends_escalated(escalated_run):
    assert read_state(escalated_run)["status"] == "escalated"
    assert read_state(escalated_run)["status"] in ENDING_STATUSES
    assert ENDING_STATUSES == {"completed", "escalated"}


def test_a_running_run_is_running():
    state = story_coordinator.RunState(story_id="story-001", branch="story/story-001")
    assert state.status == "running"


def test_the_coordinator_writes_no_status_outside_the_pinned_set():
    """Read the coordinator: every status it assigns is one this contract
    pins, so the set cannot grow unnoticed."""
    module = ast.parse(Path(story_coordinator.__file__).read_text(encoding="utf-8"))
    assigned = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and target.attr == "status"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                assigned.add(node.value.value)
    assert assigned, "no status assignment found in the coordinator"
    assert assigned <= STATUSES, assigned - STATUSES


@pytest.mark.parametrize("violation, marker", [
    ({"status": "finished"}, "status:"),
    ({"retry_count": "2"}, "retry_count:"),
    ({"artifacts": "changed-files.json"}, "artifacts:"),
    ({"branch": None}, "branch:"),
])
def test_the_state_contract_fails_on_a_violated_field(
    completed_run, violation, marker,
):
    """Non-vacuity, field by field: a wrong type or an unpinned status is
    caught rather than waved through."""
    state = read_state(completed_run) | violation
    problems = state_contract_problems(state)
    assert any(problem.startswith(marker) for problem in problems), problems


def test_the_state_contract_fails_when_a_field_is_added_or_removed(completed_run):
    state = read_state(completed_run)
    assert state_contract_problems(state | {"mood": "hopeful"})
    assert state_contract_problems({k: v for k, v in state.items() if k != "branch"})
    renamed = {("retries" if k == "retry_count" else k): v for k, v in state.items()}
    assert state_contract_problems(renamed)


# --------------------------------------------------------------------------
# events.log's line format
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["completed_run", "retried_run", "escalated_run"])
def test_every_events_log_line_matches_the_frozen_format(shape, request):
    run_dir = request.getfixturevalue(shape)
    lines = log_lines(run_dir)
    assert lines, "the run recorded no events"
    assert log_format_problems(lines) == []


@pytest.mark.parametrize("line", [
    "2026-01-01 00:00:00 implementer stage started",     # no bracketing
    "[2026-01-01 00:00:00]implementer stage started",    # no separator
    "[2026-01-01 00:00:00]  implementer stage started",  # two spaces
    "[2026-01-01 00:00:00] :: implementer stage started",  # a separator added
    "[2026-01-01T00:00:00] implementer stage started",   # a changed timestamp
    "[2026-01-01 00:00] implementer stage started",      # no seconds
    "[01/01/2026 00:00:00] implementer stage started",   # a reordered timestamp
    "[2026-13-45 99:99:99] implementer stage started",   # a shaped non-date
])
def test_the_log_format_assertion_fails_on_a_changed_line(line):
    """Non-vacuity: the pattern catches the bracketing, the separator and the
    timestamp format, which is what "frozen" has to mean to be worth stating."""
    assert log_format_problems([line]), line


# --------------------------------------------------------------------------
# escalation-summary.md
# --------------------------------------------------------------------------


def summary_expectations(run_dir: Path) -> dict:
    """What this run's own artifacts say its summary must carry.

    Read off the run directory rather than written here, so the conditional
    sections are asserted against the run that produced them: a failing
    verdict's blocking issues, and whatever retries were recorded.
    """
    verdict_path = run_dir / "verification-result.json"
    verdict = (json.loads(verdict_path.read_text(encoding="utf-8"))
               if verdict_path.is_file() else {})
    history = run_dir / "retry-history.json"
    return {
        "blocking_issues": (verdict.get("blocking_issues", [])
                            if verdict.get("status") == "failed" else []),
        "retries": (json.loads(history.read_text(encoding="utf-8"))
                    if history.is_file() else []),
    }


def test_the_escalation_summary_holds_its_five_parts(escalated_run):
    """Driven by a real escalated run, not by a string written here.

    The name is the one `tests/test_story_016_validation.py` names as the
    assertion each escalation-summary mutation must turn red, and the five
    parts it was written for are still five of the parts checked here.
    """
    state = read_state(escalated_run)
    text = (escalated_run / "escalation-summary.md").read_text(encoding="utf-8")
    assert escalation_summary_problems(
        text,
        story_id=state["story_id"],
        stage=state["current_stage"],
        retry_count=state["retry_count"],
        **summary_expectations(escalated_run),
    ) == []
    assert state["current_stage"] in STAGE_NAMES


@pytest.fixture
def escalated_summary(escalated_run) -> tuple[str, dict, dict]:
    state = read_state(escalated_run)
    text = (escalated_run / "escalation-summary.md").read_text(encoding="utf-8")
    return text, state, summary_expectations(escalated_run)


@pytest.mark.parametrize("part", [
    "# story-001 Escalation Summary",
    "## Status",
    "Escalated",
    "## Reason",
    "Stage: verifier",
    "retry count: 2",
    "events.log",
    "verification/",
    # story-024's parts. Each is an absence assertion about a summary that
    # dropped it, so each is struck out of a real summary here and the
    # contract must report it.
    "## Outstanding Issues",
    "sample behavior missing",   # a blocking issue's `issue`
    "src/app.py",                # its `location`
    "sample behavior exists",    # its `required_behavior`
    "## Retry History",
    "attempts/attempt-1",        # a recorded retry's `archive_directory`
    "## Recommended Investigation",
    "l5-run story-001",
    "--stage",
    "refused",
])
def test_the_summary_assertion_fails_when_a_part_is_removed(escalated_summary, part):
    """Non-vacuity, part by part: strike any one of the parts out of a real
    summary and the contract reports it."""
    text, state, expected = escalated_summary
    assert part in text, part
    problems = escalation_summary_problems(
        text.replace(part, ""),
        story_id=state["story_id"],
        stage=state["current_stage"],
        retry_count=state["retry_count"],
        **expected,
    )
    assert problems, part


def test_the_summary_assertion_fails_on_an_empty_reason(escalated_summary):
    text, state, expected = escalated_summary
    reason = text.split("## Reason", 1)[1].split("##", 1)[0]
    problems = escalation_summary_problems(
        text.replace(reason, "\n\n"),
        story_id=state["story_id"],
        stage=state["current_stage"],
        retry_count=state["retry_count"],
        **expected,
    )
    assert problems == ["the reason section is empty"]


def test_the_summary_assertion_fails_on_a_section_with_no_source_behind_it(
    escalated_summary,
):
    """The other direction, which is the one story-024 exists for: a heading
    over nothing. This run has a failing verdict and two retries; told it had
    neither, the contract reports both sections as emitted without a source
    rather than accepting them."""
    text, state, _ = escalated_summary
    problems = escalation_summary_problems(
        text,
        story_id=state["story_id"],
        stage=state["current_stage"],
        retry_count=state["retry_count"],
        blocking_issues=[],
        retries=[],
    )
    assert problems == [
        "an outstanding issues section is emitted with no failing verdict behind it",
        "a retry history section is emitted by a run that never retried",
    ]


def test_the_summary_assertion_fails_when_a_section_reports_a_count_not_the_finding(
    escalated_summary,
):
    """A section that says how many issues there are, or points at the file
    they are in, satisfies nothing: the four recorded fields are what the
    contract asks for."""
    text, state, expected = escalated_summary
    gutted = (
        text.split("## Outstanding Issues", 1)[0]
        + "## Outstanding Issues\n2 blocking issues; see verification-result.json.\n\n"
        + "## " + text.split("## Outstanding Issues", 1)[1].split("\n## ", 1)[1]
    )
    problems = escalation_summary_problems(
        gutted,
        story_id=state["story_id"],
        stage=state["current_stage"],
        retry_count=state["retry_count"],
        **expected,
    )
    assert any("is not rendered" in problem for problem in problems), problems


# --------------------------------------------------------------------------
# The run directory
# --------------------------------------------------------------------------


def test_a_completed_run_produces_the_artifacts_it_must(completed_run):
    present = {path.name for path in completed_run.iterdir()}
    assert required_artifacts() <= present, required_artifacts() - present


def test_the_required_artifacts_are_derived_from_the_workflow_definition():
    """Not a list typed here: a stage output the workflow declares is required
    because the workflow declares it."""
    for stage in WORKFLOW["stages"]:
        for output in stage.get("outputs", []):
            assert output in required_artifacts(), output


def test_the_run_directory_assertion_permits_an_artifact_it_does_not_name(
    completed_run,
):
    """The whole point of a required subset. story-012 adds retry-history.json
    and story-014 adds clean-clone-result.json; neither may fail an assertion
    about artifacts it has nothing to do with."""
    (completed_run / "an-artifact-a-later-story-adds.json").write_text(
        "{}\n", encoding="utf-8")
    present = {path.name for path in completed_run.iterdir()}
    assert required_artifacts() <= present
    assert "an-artifact-a-later-story-adds.json" in present


def test_a_run_gaining_an_artifact_and_an_event_still_satisfies_every_contract(
    completed_run,
):
    """The change this file exists to permit, demonstrated rather than argued:
    a later story adds an artifact and appends an event, and nothing here
    fails."""
    (completed_run / "clean-clone-result.json").write_text("{}\n", encoding="utf-8")
    story_coordinator.append_event(
        completed_run, "clean clone suite passed", kind="note")

    present = {path.name for path in completed_run.iterdir()}
    assert required_artifacts() <= present
    assert log_format_problems(log_lines(completed_run)) == []
    assert state_contract_problems(read_state(completed_run)) == []
    assert log_lines(completed_run)[-1].endswith("clean clone suite passed")


def test_the_run_directory_assertion_fails_when_a_required_artifact_is_missing(
    completed_run,
):
    """Non-vacuity: a subset check still has to bite on absence."""
    (completed_run / "completion-report.md").unlink()
    present = {path.name for path in completed_run.iterdir()}
    assert not required_artifacts() <= present


# --------------------------------------------------------------------------
# The completion commit
# --------------------------------------------------------------------------


#: The title of the story the shared fixture installs, so the subject asserted
#: below is the one a real run of that story writes.
STORY_TITLE = "Sample story for coordinator tests"


def commit_message(target_root: Path, revision: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "-C", str(target_root), "log", "-1", "--format=%B", revision],
        capture_output=True, text=True, check=True).stdout


def test_a_completed_runs_commit_holds_the_message_the_contract_names(
    completed_run, target_root,
):
    """Read off the repository a real run committed into, not off the function
    that composes it."""
    assert completion_commit_problems(
        commit_message(target_root), story_id="story-001",
        title=STORY_TITLE) == []


def test_the_composed_message_and_the_committed_one_are_the_same_message(
    completed_run, target_root,
):
    """The composition the coordinator exposes is what reaches git, so the
    pre-flight that recognizes a completion commit and the code that writes one
    are held to one shape rather than to two that agree today."""
    state = story_coordinator.RunState(story_id="story-001",
                                       branch="story/story-001")
    composed = story_coordinator.completion_commit_message(state, STORY_TITLE)
    assert commit_message(target_root).rstrip("\n") == composed
    assert completion_commit_problems(
        composed, story_id="story-001", title=STORY_TITLE) == []


@pytest.mark.parametrize("message,expected", [
    ("story-001: Sample story for coordinator tests\n\nWritten by hand.",
     "does not carry"),
    ("Sample story for coordinator tests\n\n"
     "Implemented by the l5 harness story workflow.", "subject"),
    ("story-001: Sample story for coordinator tests\n"
     "Implemented by the l5 harness story workflow.", "blank line"),
])
def test_the_completion_commit_contract_fails_on_each_part_removed(
    message, expected,
):
    """Non-vacuity, once per part: a message missing the marker, wearing
    another subject, or running the body into the subject is a violation."""
    problems = completion_commit_problems(
        message, story_id="story-001", title=STORY_TITLE)
    assert problems
    assert any(expected in problem for problem in problems), problems


def test_the_marker_the_contract_checks_is_the_constant_the_coordinator_writes():
    """Read from the module rather than typed here, so the contract cannot
    drift from what `_complete` commits and the pre-flight matches on."""
    assert story_coordinator.COMPLETION_COMMIT_MARKER \
        in story_coordinator.completion_commit_message(
            story_coordinator.RunState(story_id="story-001", branch="b"), "T")
    assert completion_commit_problems(
        f"story-001: T\n\n{story_coordinator.COMPLETION_COMMIT_MARKER}",
        story_id="story-001", title="T") == []
