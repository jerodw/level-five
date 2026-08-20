"""Independent validation for story-024: `escalation-summary.md` carries the
finding rather than a pointer to it.

The subject is a *rendering*, so almost everything here is asserted against
summary text produced by a real escalation. A target repository is built under
tmp_path, fake stage agents drive it into each of the escalation shapes the
story names, and the file the coordinator wrote is read back. Where a shape
does not need a run — a passing verdict standing behind an escalation, a
verdict that is present but unparseable — `escalation_summary` is called
directly against a hand-built run directory, which is itself one of the
acceptance criteria.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch. Three of the four criteria about
this summary are absences, and an absence about *rendered text* is the easiest
kind to satisfy by accident: a check for a heading that never appears passes
identically when the summary is empty, when the composition was never called,
and when the assertion is looking at the wrong text. So each one is paired
with the violation constructed against the same subject and the same check:

  * "no `## Outstanding Issues` when there is no verdict" sits beside the same
    run directory with a failing verdict dropped into it, recomposed by the
    same function, which does emit the heading;
  * "no `## Outstanding Issues` when the verdict passed" sits beside the same
    run directory whose verdict's status is flipped to failed;
  * "no `## Retry History` when the run never retried" sits beside the same
    run directory with a retry record dropped into it;
  * "no claim that anything was committed when no commit was recorded" sits
    beside the same state carrying an escalation commit, which does claim it;
  * "this story added no schema" sits beside the file this story did change,
    compared the same way.

The four pre-existing sections were checked against the summary the *pre-story*
coordinator writes for the same run, which meant recovering that coordinator
out of git history and running it. story-029 retired that instrument — a
recovered module runs against today's workflow and stops running when the
workflow legitimately changes, which is what happened. Each of those sections
now states what it says, against the summary a real escalation wrote, and
"unchanged by this story" is asserted where the text lives: the composing
source at the two ends of this story's own commit range, read as text.

Nothing here invokes a model: every run goes through a fake agent runner and
every clone source is a local filesystem path.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE, ENDPOINT, first_retry_route, function_source_at,
                      story_diff, story_commit_range)
import conftest

import harness_config
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is *what the escalation
#: summary says* — which sections it carries, which attempt each renders, what
#: it tells a developer to do next — and the stage list is an input to that
#: question rather than its subject. The retry ceiling below is a different
#: matter and still reads what this repository declares, because a summary
#: written *at the ceiling* is a summary about that number.
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
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": conftest.StageRef(0)},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="escalation-summary-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)
#: Since story-028 the route is a category-keyed table rather than a constant,
#: so the category a failing verdict names and the stage it routes to are read
#: off that table through the shared helper.
RETRY_CATEGORY, RETRY_STAGE = first_retry_route(WORKFLOW)
MAX_RETRIES = json.loads(
    (REPO_ROOT / "rules" / "execution-rules.json").read_text(encoding="utf-8")
)["max_retries"]

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(attempt: int, *, retry: bool) -> dict:
    """A failing verdict whose four fields are distinct per attempt.

    Distinct so a section that rendered some *other* attempt's issue, or one
    field where another was meant, is distinguishable from one that rendered
    what it was handed.
    """
    return {
        "status": "failed",
        "blocking_issues": [
            {
                "severity": "high",
                "issue": f"attempt {attempt} left the sample behavior unimplemented",
                "location": f"src/attempt_{attempt}.py:14",
                "required_behavior": f"the sample behavior exists after attempt {attempt}",
            },
            {
                "severity": "medium",
                "issue": f"attempt {attempt} duplicated the parse in two places",
                "location": f"src/other_{attempt}.py:3",
                "required_behavior": f"one parser after attempt {attempt}",
            },
        ],
        "unverified": [],
        "retry_recommended": retry,
        "retry_target": RETRY_CATEGORY,
    }


STORY = f"""\
story:
  id: {STORY_ID}
  title: Sample story for coordinator tests
  description: |
    A stand-in story used to exercise the workflow deterministically.

tasks:
  - do the sample work

acceptance_criteria:
  - the sample behavior exists
  - existing behavior is preserved

scope:
  modify:
    - src/
  do_not_modify:
    - rules/

verification_requirements:
  - confirm the sample behavior

constraints:
  - preserve existing behavior
"""

CONFIG = f"""\
workflow: {WORKFLOW['name']}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
tests_dir: tests/
"""

APP_AT_HEAD = "print('hello')\n"


# --------------------------------------------------------------------------
# A target repository and a fake runner
# --------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, gitignore: str = "",
                 test_command: str | None = None) -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    config = CONFIG
    if test_command is not None:
        config = config.replace("test_command: echo tests-ok",
                                f"test_command: {test_command}")
    write(root / ".harness" / "config.yaml", config)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    if gitignore:
        write(root / ".gitignore", gitignore)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path / "summary-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the definition built above, so a converted case
    drives a real coordinator loading a real file."""
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "summary-harness")


class Runner:
    """A fake agent runner: each stage writes the artifacts it declares.

    `silent` names stages that write nothing, which is how the missing-artifact
    escalation — the shape that never reaches the verifier and so has no
    verdict at all — is driven without touching the coordinator.
    """

    def __init__(self, target_root: Path, verdicts: list | None = None,
                 silent: tuple[str, ...] = (), edit: bool = True):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = verdicts or [PASS]
        self.silent = silent
        self.edit = edit
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        if stage in self.silent:
            return AgentResult(ok=True, result_text=f"{stage} wrote nothing")
        attempt = max(1, self.calls.count(RETRY_STAGE))

        if stage == WRITING:
            if self.edit:
                write(self.target_root / "src" / "app.py",
                      APP_AT_HEAD + f"print('attempt {attempt}')\n")
            write_json(self.run_dir / conftest.CHANGED_FILES, {
                "modified": ["src/app.py"] if self.edit else [],
                "created": [], "deleted": [],
            })
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on attempt {attempt}.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == VERIFYING:
            seen = self.calls.count(stage) - 1
            verdict = self.verdicts[min(seen, len(self.verdicts) - 1)]
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
        elif stage == DOCUMENTING:
            write(self.run_dir / conftest.DOCUMENTATION_REPORT, "Nothing.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def state_of(target_root: Path) -> dict:
    return json.loads(
        (run_dir_of(target_root) / "state.json").read_text(encoding="utf-8"))


def summary_of(target_root: Path) -> str:
    return (run_dir_of(target_root) / "escalation-summary.md").read_text(
        encoding="utf-8")


def escalate(target_root: Path, harness_root: Path, **runner_kwargs) -> Runner:
    runner = Runner(target_root, **runner_kwargs)
    code = story_coordinator.run_story(STORY_ID, harness_root, target_root, runner)
    assert code == 2, "the shape was meant to escalate"
    assert state_of(target_root)["status"] == "escalated"
    return runner


# --------------------------------------------------------------------------
# Reading a summary
# --------------------------------------------------------------------------


def sections(text: str) -> dict[str, str]:
    """The summary's `## ` sections, heading to body.

    `### ` subheadings inside a section do not end it, which is what the
    trailing space in the marker is for.
    """
    found: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            found[current] = ""
        elif current is not None:
            found[current] += line + "\n"
    return found


def section(text: str, heading: str) -> str:
    return sections(text).get(heading, "")


def section_body(text: str, heading: str) -> str:
    """A section's body without the blank line that separates it from the next.

    The separator belongs to the join, not to either section: the same
    section's body is followed by one when another section comes after it and
    by nothing when it is last. Stripping it is what lets a section be
    compared byte for byte across two summaries with different section counts.
    """
    return section(text, heading).rstrip("\n")


def without_the_reason(text: str) -> str:
    """The summary with its whole `## Reason` section removed.

    Cut structurally rather than by replacing the reason string, so a reason
    that is empty — or one that happens to appear elsewhere — is cut the same
    way as any other.
    """
    head, rest = text.split("## Reason", 1)
    return head + "## " + rest.split("\n## ", 1)[1]


def retry_records(run_dir: Path) -> list[dict]:
    path = run_dir / "retry-history.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def issue_fields(issue: dict) -> list[str]:
    return [issue["severity"], issue["issue"], issue["location"],
            issue["required_behavior"]]


# --------------------------------------------------------------------------
# The escalation shapes
# --------------------------------------------------------------------------


@pytest.fixture
def escalated_at_the_ceiling(target, harness_root) -> Path:
    """Two failing verdicts, two retries taken, then the ceiling.

    The shape carrying both conditional sections at once: a failing verdict
    stands behind the escalation and the run has retries to report.
    """
    escalate(target, harness_root,
             verdicts=[failing(1, retry=True), failing(2, retry=True),
                       failing(3, retry=True)])
    assert state_of(target)["retry_count"] == MAX_RETRIES
    return target


@pytest.fixture
def escalated_at_once(target, harness_root) -> Path:
    """A failing verdict recommending no retry: no retry is ever taken."""
    escalate(target, harness_root, verdicts=[failing(1, retry=False)])
    assert state_of(target)["retry_count"] == 0
    return target


@pytest.fixture
def escalated_with_no_verdict(target, harness_root) -> Path:
    """The implementer writes nothing, so the run never reaches the verifier."""
    escalate(target, harness_root, silent=(WRITING,))
    assert not (run_dir_of(target) / "verification-result.json").exists()
    return target


@pytest.fixture
def escalated_after_a_passing_verdict(tmp_path, harness_root) -> Path:
    """The clean-clone path: every verification passes and the suite fails
    where the code ships, until the retries are exhausted.

    A real escalation whose `verification-result.json` records `passed`,
    rather than one assembled by hand — the artifact is written by the run.
    """
    target = build_target(tmp_path / "clean-clone-target", test_command="false")
    escalate(target, harness_root, verdicts=[PASS])
    verdict = json.loads(
        (run_dir_of(target) / "verification-result.json").read_text(encoding="utf-8"))
    assert verdict["status"] == "passed"
    return target


@pytest.fixture
def escalated_without_a_commit(tmp_path, harness_root) -> Path:
    """An escalation that found nothing to commit.

    The run directory is ignored and no stage edits the tree, so the tree is
    clean when the escalation reaches its commit and `escalation_commit` stays
    empty — the one shape in which it does.
    """
    target = build_target(tmp_path / "quiet-target", gitignore=".harness/runs/\n")
    escalate(target, harness_root, verdicts=[failing(1, retry=False)], edit=False)
    assert state_of(target)["escalation_commit"] == ""
    return target


ALL_SHAPES = ["escalated_at_the_ceiling", "escalated_at_once",
              "escalated_with_no_verdict", "escalated_after_a_passing_verdict",
              "escalated_without_a_commit"]


# --------------------------------------------------------------------------
# Outstanding Issues
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["escalated_at_the_ceiling", "escalated_at_once"])
def test_a_failed_verdicts_blocking_issues_are_rendered_field_for_field(
    shape, request,
):
    """The finding itself, not a count and not a pointer at the artifact.

    Every field of every blocking issue the verdict recorded appears in the
    section, verbatim — which is the difference between a developer being able
    to act on the escalation and having to know `verification-result.json`
    exists.
    """
    target = request.getfixturevalue(shape)
    verdict = json.loads(
        (run_dir_of(target) / "verification-result.json").read_text(encoding="utf-8"))
    assert verdict["status"] == "failed"
    outstanding = section(summary_of(target), "Outstanding Issues")

    assert outstanding.strip(), "the section is present but empty"
    for issue in verdict["blocking_issues"]:
        for value in issue_fields(issue):
            assert value in outstanding, value


def test_the_outstanding_issues_section_names_the_verdict_it_came_from(
    escalated_at_the_ceiling,
):
    """A reader has to be able to tell which verdict the issues came from, and
    that it may predate the stage the run escalated at."""
    target = escalated_at_the_ceiling
    iterations = state_of(target)["verification_iterations"]
    outstanding = section(summary_of(target), "Outstanding Issues")

    assert "verification-result.json" in outstanding
    assert f"verification/iteration-{iterations}.json" in outstanding
    assert (run_dir_of(target) / f"verification/iteration-{iterations}.json").is_file()


def test_an_escalation_with_no_verdict_emits_no_outstanding_issues_heading(
    escalated_with_no_verdict,
):
    """The absence, and beside it the same check reporting the violation.

    The control is the same run directory with a failing verdict dropped into
    it and the summary recomposed by the same function from the same state:
    the heading appears, so its absence above is the missing artifact and not
    a check that stopped seeing anything.
    """
    target = escalated_with_no_verdict
    run_dir = run_dir_of(target)
    assert "## Outstanding Issues" not in summary_of(target)

    state = story_coordinator.RunState(**state_of(target))
    write_json(run_dir / "verification-result.json", failing(1, retry=False))
    recomposed = story_coordinator.escalation_summary(run_dir, state, "any reason")
    assert "## Outstanding Issues" in recomposed
    for value in issue_fields(failing(1, retry=False)["blocking_issues"][0]):
        assert value in section(recomposed, "Outstanding Issues")


def test_a_passing_verdict_emits_no_outstanding_issues_heading(
    escalated_after_a_passing_verdict,
):
    """The clean-clone path escalates with a verdict that passed, so there are
    no outstanding issues to report and no heading is written.

    The control flips that same artifact's status to failed and recomposes:
    the heading appears. Nothing else about the run changes, so `status` is
    what the omission is keyed on.
    """
    target = escalated_after_a_passing_verdict
    run_dir = run_dir_of(target)
    assert "## Outstanding Issues" not in summary_of(target)

    state = story_coordinator.RunState(**state_of(target))
    verdict = json.loads(
        (run_dir / "verification-result.json").read_text(encoding="utf-8"))
    write_json(run_dir / "verification-result.json",
               verdict | {"status": "failed"} | failing(9, retry=False))
    recomposed = story_coordinator.escalation_summary(run_dir, state, "any reason")
    assert "## Outstanding Issues" in recomposed


def test_an_unreadable_verdict_omits_the_section_rather_than_failing_the_report(
    escalated_at_once, tmp_path,
):
    """A report is not where an unparseable artifact raises. The control is
    the same call against the same directory with the artifact intact."""
    run_dir = run_dir_of(escalated_at_once)
    state = story_coordinator.RunState(**state_of(escalated_at_once))
    intact = story_coordinator.escalation_summary(run_dir, state, "any reason")
    assert "## Outstanding Issues" in intact

    (run_dir / "verification-result.json").write_text("{not json", encoding="utf-8")
    text = story_coordinator.escalation_summary(run_dir, state, "any reason")
    assert "## Outstanding Issues" not in text
    assert "## Recommended Investigation" in text


# --------------------------------------------------------------------------
# Retry History
# --------------------------------------------------------------------------


def test_every_recorded_retry_is_rendered_as_its_own_block(
    escalated_at_the_ceiling,
):
    """One section, one block per recorded entry, each carrying that entry's
    attempt, where execution was rerouted, what blocked it and where its
    artifacts were archived."""
    target = escalated_at_the_ceiling
    text = summary_of(target)
    records = retry_records(run_dir_of(target))
    assert len(records) == MAX_RETRIES, records
    assert text.count("## Retry History") == 1

    history = section(text, "Retry History")
    for entry in records:
        assert f"{entry['attempt']}" in history
        assert entry["retry_stage"] in history
        assert entry["archive_directory"] in history
        for issue in entry["blocking_issues"]:
            for value in issue_fields(issue):
                assert value in history, value


def test_the_retry_blocks_carry_each_attempts_own_issues_not_the_last_ones(
    escalated_at_the_ceiling,
):
    """The verdicts differ per attempt, so a section rendering the final
    verdict twice — or the artifact's first entry twice — is distinguishable
    from one rendering what each entry recorded."""
    history = section(summary_of(escalated_at_the_ceiling), "Retry History")
    assert "attempt 1 left the sample behavior unimplemented" in history
    assert "attempt 2 left the sample behavior unimplemented" in history
    # The verdict that escalated the run took no retry and is not an entry.
    assert "attempt 3 left the sample behavior unimplemented" not in history


def test_a_run_that_never_retried_emits_no_retry_history_heading(
    escalated_at_once,
):
    """The artifact is created at the first retry and never in advance, so its
    absence is the evidence. Beside it, the same directory with one record
    dropped in and the summary recomposed: the heading appears."""
    target = escalated_at_once
    run_dir = run_dir_of(target)
    assert not (run_dir / "retry-history.json").exists()
    assert "## Retry History" not in summary_of(target)

    state = story_coordinator.RunState(**state_of(target))
    write_json(run_dir / "retry-history.json", [{
        "attempt": 1,
        "blocking_issues": failing(1, retry=True)["blocking_issues"],
        "retry_stage": RETRY_STAGE,
        "archive_directory": "attempts/attempt-1",
    }])
    recomposed = story_coordinator.escalation_summary(run_dir, state, "any reason")
    assert "## Retry History" in recomposed
    assert "attempts/attempt-1" in section(recomposed, "Retry History")


def test_the_retry_history_has_one_reader_in_orchestration(
    escalated_at_the_ceiling,
):
    """The section renders whatever `load_retry_records` returns rather than
    reading the file a second way: monkeypatching that one reader empties the
    section, which a second reader would defeat."""
    run_dir = run_dir_of(escalated_at_the_ceiling)
    state = story_coordinator.RunState(**state_of(escalated_at_the_ceiling))
    assert "## Retry History" in story_coordinator.escalation_summary(
        run_dir, state, "any reason")

    original = story_coordinator.load_retry_records
    story_coordinator.load_retry_records = lambda _run_dir: []
    try:
        text = story_coordinator.escalation_summary(run_dir, state, "any reason")
    finally:
        story_coordinator.load_retry_records = original
    assert "## Retry History" not in text


# --------------------------------------------------------------------------
# Recommended Investigation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_every_escalation_says_how_the_run_is_continued(shape, request):
    """On every shape, including the ones that have no verdict and took no
    retry: resume exists, `--stage` overrides where it enters, and it is
    refused while nothing has changed."""
    target = request.getfixturevalue(shape)
    investigation = section(summary_of(target), "Recommended Investigation")

    assert investigation.strip()
    assert f"l5-run {STORY_ID}" in investigation
    assert "--stage" in investigation
    assert "refused" in investigation and "unchanged" in investigation


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_the_section_names_run_directory_artifacts_that_exist(shape, request):
    """Every file it points at is one a reader can open. A recommendation to
    read something that is not there is the pointer problem again."""
    target = request.getfixturevalue(shape)
    run_dir = run_dir_of(target)
    investigation = section(summary_of(target), "Recommended Investigation")

    named = [line[2:].strip() for line in investigation.splitlines()
             if line.startswith("- ")]
    assert named, investigation
    for name in named:
        assert (run_dir / name).is_file(), name
    assert "state.json" in named


def test_the_commit_claim_is_made_only_when_a_commit_was_recorded(
    escalated_without_a_commit, escalated_at_once,
):
    """The absence and its control, in one test and against two real runs.

    The quiet run recorded no escalation commit, so its section claims no
    commit and names no undo command. The run beside it recorded one, and
    says both — so the silence above is the empty field and not a sentence
    this summary never writes.
    """
    quiet = section(summary_of(escalated_without_a_commit),
                    "Recommended Investigation")
    assert state_of(escalated_without_a_commit)["escalation_commit"] == ""
    assert "committed" not in quiet
    assert story_coordinator.ESCALATION_UNDO_COMMAND not in quiet

    commit = state_of(escalated_at_once)["escalation_commit"]
    committed = section(summary_of(escalated_at_once), "Recommended Investigation")
    assert commit
    assert "committed" in committed
    assert commit in committed
    assert state_of(escalated_at_once)["branch"] in committed
    assert story_coordinator.ESCALATION_UNDO_COMMAND in committed


# --------------------------------------------------------------------------
# Composed from artifacts and state, reachable without a run
# --------------------------------------------------------------------------


def test_the_summary_is_composable_against_a_hand_built_run_directory(tmp_path):
    """The composition is a module-level function taking a run directory, a
    RunState and a reason: no run, no coordinator loop, no repository."""
    run_dir = tmp_path / "hand-built"
    run_dir.mkdir()
    write_json(run_dir / "verification-result.json", failing(7, retry=False))
    write_json(run_dir / "retry-history.json", [{
        "attempt": 1,
        "blocking_issues": failing(7, retry=False)["blocking_issues"],
        "retry_stage": RETRY_STAGE,
        "archive_directory": "attempts/attempt-1",
    }])
    state = story_coordinator.RunState(
        story_id=STORY_ID, branch=f"story/{STORY_ID}", status="escalated",
        current_stage=VERIFYING, retry_count=1, verification_iterations=2,
        escalation_commit="abc123",
    )

    text = story_coordinator.escalation_summary(run_dir, state, "a stated reason")

    assert text.startswith(f"# {STORY_ID} Escalation Summary")
    assert section(text, "Reason").strip() == "a stated reason"
    assert "verification/iteration-2.json" in section(text, "Outstanding Issues")
    for value in issue_fields(failing(7, retry=False)["blocking_issues"][0]):
        assert value in text
    assert "attempts/attempt-1" in section(text, "Retry History")
    assert "abc123" in section(text, "Recommended Investigation")


REASONS = [
    "verification failed and the verifier did not recommend a retry",
    "implementer did not produce required artifacts: changed-files.json",
    "tester modified blocked path: rules/execution-rules.json",
    "the clean-clone check failed and retries are exhausted: 1 failed",
    "",
]


def test_no_section_is_keyed_off_the_escalation_reasons_text(
    escalated_at_the_ceiling,
):
    """The coordinator renders recorded facts; it does not classify why the
    run stopped. Composed against one run directory with five reasons whose
    text differs in kind, every summary is identical once the Reason section
    is taken out — so no other section read the reason."""
    run_dir = run_dir_of(escalated_at_the_ceiling)
    state = story_coordinator.RunState(**state_of(escalated_at_the_ceiling))

    rendered = [story_coordinator.escalation_summary(run_dir, state, reason)
                for reason in REASONS]
    stripped = {without_the_reason(text) for text in rendered}
    assert len(stripped) == 1, "a section other than Reason read the reason"
    # The control: the reason does reach the summary, so the comparison above
    # is not over five identical strings.
    assert len({section(text, "Reason") for text in rendered}) == len(set(REASONS))


# --------------------------------------------------------------------------
# What was there before is unchanged
# --------------------------------------------------------------------------


UNCHANGED_SECTIONS = ["Status", "Reason", "Where Execution Stopped",
                      "Where to Look"]


COORDINATOR_REL = "orchestration/story_coordinator.py"

#: The escalation this section is about: three failing verdicts, so the run
#: retries to its ceiling and escalates at the verifier with a verdict, a
#: retry history and a committed tree behind it. It is the shape that
#: exercises every section of the summary at once.
EXHAUSTING_VERDICTS = 3


@pytest.fixture
def exhausted_escalation(tmp_path, harness_root):
    """One escalation, driven by today's coordinator alone.

    It used to be two: the same shape driven by a coordinator recovered out of
    git history beside this one, with every assertion below stated as equality
    between their outputs. story-029 retired that instrument — the recovered
    module runs against today's workflow and stops running when the workflow
    legitimately changes — so each assertion states what it is about instead,
    and this fixture builds only the run those statements are about.

    Splitting it is also what lets
    `test_reason_is_still_the_section_immediately_after_status` stand on
    today's output alone: it never had anything to say about the earlier
    module, and it was failing to *collect* because the fixture it shared
    could no longer build.
    """
    target = build_target(tmp_path / "escalated-target")
    verdicts = [failing(attempt, retry=True)
                for attempt in range(1, EXHAUSTING_VERDICTS + 1)]
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target,
        Runner(target, verdicts=verdicts)) == 2
    return target


def summary_composition(bound: str) -> str:
    """The source that composes the escalation summary, at one bound.

    Read as text at the two ends of this story's own commit range rather than
    by running a module recovered out of history: what the four pre-existing
    sections *say* is the subject, and that is stated in the source. Before
    this story the summary was built inline in `_escalate`; after it, in the
    extracted `escalation_summary`, which is the whole of what the story did
    to the composition — so the function to read is whichever of the two
    exists at that bound.
    """
    for name in ("escalation_summary", "_escalate"):
        try:
            return function_source_at(COORDINATOR_REL, name,
                                      validation_file=Path(__file__),
                                      bound=bound, repo=REPO_ROOT)
        except AssertionError:
            continue
    raise AssertionError(f"no source at {bound} composes the summary")


def test_the_four_pre_existing_sections_carry_the_text_they_carried(
    exhausted_escalation,
):
    """The four sections named, with their bodies stated rather than compared
    against a summary a recovered module wrote once.

    Two statements, and both are needed. What each section *says* today is
    asserted as its own text: the heading line, the status, the reason, the
    stage and attempt execution stopped at, and the three places the reader is
    sent. That this story did not change them is asserted where the text
    lives — each section's composing source is byte-identical at the two ends
    of this story's own commit range.

    The control is the section this story added, absent at the baseline and
    present at the endpoint, so the equalities above are not four readings of
    one unchanged file.
    """
    text = summary_of(exhausted_escalation)
    state = state_of(exhausted_escalation)

    assert text.splitlines()[0] == f"# {STORY_ID} Escalation Summary"
    assert section_body(text, "Status") == "Escalated"
    assert section_body(text, "Reason") == (
        "verification failed and retries are exhausted")
    assert section_body(text, "Where Execution Stopped") == (
        f"Stage: {state['current_stage']}, retry count: {state['retry_count']}")
    assert section_body(text, "Where to Look") == (
        "See events.log for the run history and the verification/ directory "
        "for verifier findings.")

    before = summary_composition(BASELINE)
    for heading in UNCHANGED_SECTIONS:
        assert f"## {heading}" in before, heading
    for literal in ("Escalation Summary",
                    "## Status\\nEscalated",
                    "## Where Execution Stopped\\nStage: ",
                    "retry count: ",
                    "See events.log for the run history and the ",
                    "verification/ directory for verifier findings."):
        assert literal in before, literal

    # The control: the sections this story added, absent at the baseline and
    # present in what the run wrote — so the presences above are not a search
    # that matches anything.
    assert "## Recommended Investigation" in text
    assert "## Recommended Investigation" not in before
    assert "## Outstanding Issues" not in before


def test_reason_is_still_the_section_immediately_after_status(
    exhausted_escalation,
):
    """`escalation_reason` splits on the next `##`, so a section inserted
    between the two would truncate every reason it reads.

    It stands on today's output alone: it never had anything to say about a
    coordinator recovered out of history, and shared the two-escalation
    fixture only because the fixture was there. story-029 split it off.
    """
    headings = [line for line in summary_of(exhausted_escalation).splitlines()
                if line.startswith("## ")]
    assert headings[:2] == ["## Status", "## Reason"]


def test_escalation_reason_returns_the_string_it_returned_before(
    exhausted_escalation,
):
    """The one reader of this file, held to the string it returns rather than
    to agreement between two implementations.

    The reason is named outright, and it is the same string the escalation
    event and `state.json`'s own reason carry — so this is the reader agreeing
    with the run rather than with itself.

    The control is a summary with a section inserted between Status and
    Reason, which the same reader truncates: that is what makes the equality
    above the reader working rather than the reader having stopped reading.
    """
    run_dir = run_dir_of(exhausted_escalation)
    reason = story_coordinator.escalation_reason(run_dir)

    assert reason == "verification failed and retries are exhausted"
    assert reason in (run_dir / "events.log").read_text(encoding="utf-8")

    summary = summary_of(exhausted_escalation)
    (run_dir / "escalation-summary.md").write_text(
        summary.replace(reason, "something else entirely", 1), encoding="utf-8")
    assert story_coordinator.escalation_reason(run_dir) == "something else entirely"


#: The files an escalation at the retry ceiling leaves at the run-directory
#: root. Every one of them predates this story: each section the story added
#: renders an artifact that already existed, so the story added no file to
#: this list and none of these names is one it introduced. The documenter's
#: two artifacts joined the set in story-045, which introduced neither: the
#: documenter now runs before the verifier, so a run escalating at the retry
#: ceiling has already written them.
ESCALATION_RUN_DIRECTORY = {
    "changed-files.json", "escalation-summary.md", "events.log",
    "execution-history.json", "implementation-summary.md",
    "retry-history.json", "state.json", "test-results.json",
    "tester-changed-files.json", "verification-result.json",
    "retry-guidance.json",
    "documentation-report.md", "documenter-changed-files.json",
}

#: The rendered prompts, one per stage and attempt. Named by shape rather than
#: enumerated, because how many there are is a property of the run's retry
#: count rather than of what this story did.
RENDERED_PROMPT = "prompt-"


def test_an_escalation_writes_no_new_file_to_the_run_directory(
    exhausted_escalation,
):
    """Every new section renders an artifact that already existed, stated as
    the run directory's own contents rather than as equality with a directory
    a recovered module produced.

    The set is named, and each name is checked to be a file this story did not
    introduce: the story's own commit range added nothing under `schemas/` and
    no artifact to the workflow, which
    `test_this_story_added_no_schema` states from the other side.

    The controls: the directory is populated rather than empty, and a file
    added to it is reported — so "no new file" is a reading that can see one.
    """
    run_dir = run_dir_of(exhausted_escalation)
    present = {path.name for path in run_dir.iterdir() if path.is_file()
               and not path.name.startswith(RENDERED_PROMPT)}

    assert present <= ESCALATION_RUN_DIRECTORY, present - ESCALATION_RUN_DIRECTORY
    assert "escalation-summary.md" in present
    assert "state.json" in present

    (run_dir / "a-new-artifact.json").write_text("{}\n", encoding="utf-8")
    assert {path.name for path in run_dir.iterdir() if path.is_file()
            and not path.name.startswith(RENDERED_PROMPT)} != present


def test_this_story_added_no_schema():
    """The control is the file this story did change, compared the same way:
    if the diff resolution had stopped seeing anything, it would report
    nothing either."""
    validation = Path(__file__)
    assert story_diff(["schemas/"], validation_file=validation) == ""
    assert story_diff(["orchestration/story_coordinator.py"],
                      validation_file=validation) != ""


# --------------------------------------------------------------------------
# The whole summary, against the standing contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ALL_SHAPES)
def test_every_shape_satisfies_the_standing_contract(shape, request):
    """The contract in `tests/test_coordinator_contract.py` states these
    sections for every escalation; each shape here is one it must hold for,
    with that run's own artifacts saying which conditional sections are due."""
    sys.path.insert(0, str(Path(__file__).parent))
    from test_coordinator_contract import escalation_summary_problems

    target = request.getfixturevalue(shape)
    run_dir = run_dir_of(target)
    state = state_of(target)
    verdict_path = run_dir / "verification-result.json"
    verdict = (json.loads(verdict_path.read_text(encoding="utf-8"))
               if verdict_path.is_file() else {})

    assert escalation_summary_problems(
        summary_of(target),
        story_id=state["story_id"],
        stage=state["current_stage"],
        retry_count=state["retry_count"],
        blocking_issues=(verdict.get("blocking_issues", [])
                         if verdict.get("status") == "failed" else []),
        retries=retry_records(run_dir),
    ) == []
