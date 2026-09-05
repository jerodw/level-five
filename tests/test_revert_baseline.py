"""Independent validation for story-019: reverting an implementer's edits to
where the stage found them rather than to HEAD.

The subject is a *baseline*, so almost nothing here is asserted from source.
A target repository with a real pytest suite is built under tmp_path, fake
stage agents edit its working tree, and the coordinator is run. Whether an
edit is permitted is then whatever the suite does in a clone with that edit
restored - the same question the check asks, answered by running it.

The defect this story fixes is reproduced before it is fixed. story-018
escalated because `git checkout HEAD -- <path>` cannot restore a file the
tester created during the run: the coordinator commits once, at _complete,
so that file has no version at HEAD.
`test_the_old_head_baseline_cannot_restore_a_file_created_during_the_run`
constructs exactly that clone and exactly that checkout and watches it fail,
with a file that predates the story as its control. Every claim below about
the new baseline deciding that case is read against that reproduction.

Every absence asserted here carries a control:

  * "the check refuses" sits beside the identical run in which it decides, so
    a check that refused everything could not pass both;
  * "a path absent from the baseline is deleted, not skipped" sits beside the
    same clone built the skipping way, which returns the *opposite* verdict -
    so skipping is shown to be a permission the check never established;
  * "a re-entered stage reuses its baseline" sits beside the baseline
    recaptured after the edit, which would have reversed the decision;
  * "no code path reverts to HEAD" is a regex paired with a sample line it
    does match, and with the one legitimate HEAD use in the same function;
  * "the baseline is not in state.json" is paired with the fields that are;
  * "the schema no longer says HEAD" is paired with the pre-story schema,
    read out of git, which did.

Nothing here invokes a model: every run goes through a fake agent runner and
every clone source is a local filesystem path.
"""
import ast
import copy
import inspect
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE as BASELINE_BOUND, STORY, first_retry_route,
                      repository_file_at, story_commit_range, story_diff)
import conftest

import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"

#: The tests directory the target below configures, and the prefix the writing
#: stage declares it may not create. Written once so the config and the
#: declaration cannot drift apart, and written resolved rather than as a
#: `{{tests_dir}}` token, because several cases here hand the declaration to
#: `capture_stage_baseline` directly, outside the load that expands tokens.
TESTS_DIR = "tests/"

#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is *the revert check
#: and the baseline it decides against* — that one declaration switches both on
#: together, that the baseline is keyed by the stage that declared it, that
#: removing the key disables both — and the stage list is an input to that.
#: Every one of those is a statement about what a workflow declares, which is
#: exactly what a built workflow can state, and the two cases below that need a
#: definition the coordinator has never seen now build one instead of mutating
#: a copy of the deployed file.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"},
        may_not_create=(TESTS_DIR,),
        revert_check={"result": "revert-check-result.json",
                      "baseline": "stage-baseline"}),
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
    name="revert-baseline-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES
IMPLEMENTER_STAGE = WORKFLOW["stages"][0]

#: Both names are read off the declaration, never spelled here, for the same
#: reason the coordinator may not spell them: this story's point is that one
#: key switches the capture and the check on together.
DECLARATION = IMPLEMENTER_STAGE["revert_check"]
ARTIFACT = DECLARATION["result"]
BASELINE = DECLARATION["baseline"]
PREFIX = IMPLEMENTER_STAGE["may_not_create"][0]

SCHEMA_STEM = "revert-check-result"
SCHEMA_PATH = REPO_ROOT / "schemas" / f"{SCHEMA_STEM}.schema.json"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
FAIL = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the validation written this run does not hold",
        "location": "tests/",
        "required_behavior": "the suite passes",
    }],
    "unverified": [],
    "retry_recommended": True,
    #: Since story-028 a recommended retry names the category it routes on,
    #: read off the loaded workflow's table rather than written here.
    "retry_target": first_retry_route(WORKFLOW)[0],
}

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest", "tests", "-q",
                           "-p", "no:cacheprovider"])

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
test_command: {TEST_COMMAND}
tests_dir: {TESTS_DIR}
"""

# --------------------------------------------------------------------------
# The target repository: a real module and a real suite over it.
#
# HEAD holds `greet`, a test calling it, and an empty tests/conftest.py. What
# HEAD does *not* hold is any of the files the stages below create during the
# run, which is the whole subject.
# --------------------------------------------------------------------------

APP_AT_HEAD = '''\
def greet(name):
    return f"hello, {name}"
'''

APP_RENAMED = '''\
def salute(name):
    return f"hello, {name}"
'''

APP_ADDITIVE = APP_AT_HEAD + '''

def shout(name):
    return greet(name).upper()
'''

TEST_APP_AT_HEAD = '''\
from app import greet


def test_greet():
    assert greet("world") == "hello, world"
'''

TEST_APP_REPAIRED = '''\
from app import salute


def test_greet():
    assert salute("world") == "hello, world"
'''

#: A test function that passes against APP_AT_HEAD and APP_ADDITIVE alike:
#: appended to an existing file it is a modification nothing forced.
ADDED_COVERAGE = '''

def test_greet_again():
    assert greet("again") == "hello, again"
'''

#: The file the tester creates during the run, in its broken form: the
#: defect a clean-clone or verification failure sends the retry to repair.
#: This is story-018's shape, reduced.
TEST_NEW_BROKEN = '''\
from app import shout


def test_shout():
    assert shout("world") == "hello, world"
'''

TEST_NEW_REPAIRED = '''\
from app import shout


def test_shout():
    assert shout("world") == "HELLO, WORLD"
'''

#: The same file in a form that was correct when the tester wrote it, for the
#: run whose retry adds to it rather than repairing it.
TEST_NEW_PASSING = TEST_NEW_REPAIRED

ADDED_COVERAGE_NEW = '''

def test_shout_again():
    assert shout("again") == "HELLO, AGAIN"
'''

TESTS_CONFTEST_AT_HEAD = '''\
import pytest
'''

TESTS_CONFTEST_WITH_FIXTURE = TESTS_CONFTEST_AT_HEAD + '''

@pytest.fixture
def answer():
    return 42
'''

TEST_USES_FIXTURE = '''\
def test_answer(answer):
    assert answer == 42
'''

ROOT_CONFTEST = '''\
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
'''


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A target repository whose configured test command is a real suite."""
    root = tmp_path / "suite-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / "story-001.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "conftest.py", TESTS_CONFTEST_AT_HEAD)
    write(root / "tests" / "test_app.py", TEST_APP_AT_HEAD)
    write(root / ".gitignore", ".pytest_cache/\n__pycache__/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the definition built above, so a converted case
    drives a real coordinator loading a real file."""
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "revert-harness")


# --------------------------------------------------------------------------
# The stage edits, each paired with the record that describes it. The record
# and the tree always say the same thing: the check reads the record and
# reverts inside a clone of the tree.
# --------------------------------------------------------------------------

NO_CHANGES = {"modified": [], "created": [], "deleted": []}


def unchanged(root: Path, run_dir: Path) -> dict:
    return dict(NO_CHANGES)


def module_only(root: Path, run_dir: Path) -> dict:
    """An implementer change naming no path under the governed prefix."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    return {"modified": ["src/app.py"], "created": [], "deleted": []}


def creates_the_broken_new_test(root: Path, run_dir: Path) -> dict:
    """The tester writes a new file this run, and gets it wrong.

    Untracked and uncommitted: the coordinator commits once, at _complete, so
    this file has no version at HEAD for the whole of the run.
    """
    write(root / "tests" / "test_new.py", TEST_NEW_BROKEN)
    return {"modified": [], "created": ["tests/test_new.py"], "deleted": []}


def creates_the_passing_new_test(root: Path, run_dir: Path) -> dict:
    write(root / "tests" / "test_new.py", TEST_NEW_PASSING)
    return {"modified": [], "created": ["tests/test_new.py"], "deleted": []}


def repairs_the_new_test(root: Path, run_dir: Path) -> dict:
    """The retried implementer fixes the test the tester wrote this run."""
    write(root / "tests" / "test_new.py", TEST_NEW_REPAIRED)
    return {"modified": ["tests/test_new.py"], "created": [], "deleted": []}


def adds_to_the_new_test(root: Path, run_dir: Path) -> dict:
    """The retried implementer appends coverage nothing forced."""
    write(root / "tests" / "test_new.py", TEST_NEW_PASSING + ADDED_COVERAGE_NEW)
    return {"modified": ["tests/test_new.py"], "created": [], "deleted": []}


def forced_repair(root: Path, run_dir: Path) -> dict:
    """A rename of a pre-existing module the pre-existing test cannot survive."""
    write(root / "src" / "app.py", APP_RENAMED)
    write(root / "tests" / "test_app.py", TEST_APP_REPAIRED)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


def added_coverage(root: Path, run_dir: Path) -> dict:
    """An addition to a pre-existing module, and a test it did not force."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / "tests" / "test_app.py", TEST_APP_AT_HEAD + ADDED_COVERAGE)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


def fixture_and_a_test_that_needs_it(root: Path, run_dir: Path) -> dict:
    """A governed file the stage itself brought into existence, mis-recorded.

    The record calls `tests/test_uses_fixture.py` a modification, so the
    ownership check lets it through and the revert check receives a governed
    path the baseline does not hold. The file needs the fixture the same
    change added to `tests/conftest.py`, which is what makes deleting it and
    skipping it give opposite answers.
    """
    write(root / "tests" / "conftest.py", TESTS_CONFTEST_WITH_FIXTURE)
    write(root / "tests" / "test_uses_fixture.py", TEST_USES_FIXTURE)
    return {"modified": ["tests/conftest.py", "tests/test_uses_fixture.py"],
            "created": [], "deleted": []}


def repair_then_discard_the_baseline(root: Path, run_dir: Path) -> dict:
    """A forced repair whose baseline is gone by the time the check looks.

    A stage that declares the check with nothing captured for it. Everything
    else about this run is the run that is permitted below.
    """
    record = forced_repair(root, run_dir)
    directory = run_dir / BASELINE
    if directory.is_dir():
        subprocess.run(["rm", "-rf", str(directory)], check=True)
    return record


class Runner:
    """A fake agent runner: each stage writes its artifacts, and a stage
    holding an edit also makes that edit in the target's working tree.

    `edits` maps a stage to the list of edits it makes, one per invocation;
    the last entry repeats. `verdicts` is the verifier's, read the same way.
    `interrupt` names a stage invocation after which the runner raises, which
    is how a run is left with status running for the resume case below.
    """

    def __init__(self, target_root: Path, edits: dict | None = None,
                 verdicts: list | None = None, interrupt: tuple | None = None,
                 story_id: str = "story-001"):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.edits = edits or {}
        self.verdicts = verdicts or [PASS]
        self.interrupt = interrupt
        self.calls: list[str] = []
        self.records: list[tuple[str, dict]] = []

    def _nth(self, sequence: list, index: int):
        return sequence[min(index, len(sequence) - 1)]

    def _record(self, stage: str) -> dict:
        seen = self.calls.count(stage) - 1
        edit = self._nth(self.edits.get(stage, [unchanged]), seen)
        record = edit(self.target_root, self.run_dir)
        self.records.append((stage, record))
        return record

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       self._record(stage))
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did it.\n")
        elif stage == VALIDATING:
            record = self._record(stage)
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 1, "tests_run": 2,
                "tests_passed": 2, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES, record)
        elif stage == VERIFYING:
            seen = self.calls.count(stage) - 1
            write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                       self._nth(self.verdicts, seen))
        elif stage == DOCUMENTING:
            write(self.run_dir / conftest.DOCUMENTATION_REPORT, "Nothing.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        if self.interrupt == (stage, self.calls.count(stage)):
            raise KeyboardInterrupt(f"{stage} interrupted")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def record_of(target_root: Path, artifact: str = ARTIFACT) -> dict:
    return json.loads((run_dir_of(target_root) / artifact).read_text())


def evidence(target_root: Path) -> tuple[str, str]:
    run_dir = run_dir_of(target_root)
    return ((run_dir / "events.log").read_text(),
            (run_dir / "escalation-summary.md").read_text())


def run(target_root: Path, harness: Path, edits: dict | None = None,
        verdicts: list | None = None, interrupt: tuple | None = None,
        runner: Runner | None = None) -> tuple[int, Runner]:
    runner = runner or Runner(target_root, edits, verdicts, interrupt)
    code = story_coordinator.run_story("story-001", harness, target_root, runner)
    return code, runner


def baseline_at(target_root: Path, stage: str,
                story_id: str = "story-001") -> Path:
    """The baseline the coordinator captured for one stage."""
    return story_coordinator.stage_baseline_dir(
        run_dir_of(target_root, story_id), BASELINE, stage)


def capture(target_root: Path, scratch: Path, prefix: str = PREFIX,
            stage: str = "stage") -> Path:
    """Capture a baseline the way the coordinator captures one, into scratch."""
    return story_coordinator.capture_stage_baseline(
        scratch, target_root, BASELINE, stage, [prefix], accounted_for=set())


def suite_in(directory: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=directory, capture_output=True, text=True,
    ).returncode


def mirror_harness(tmp_path: Path, workflow: dict) -> Path:
    """A harness root carrying a definition derived from the one built above.

    The two callers each mutate the single declaration their case is about and
    hand the result here, so the coordinator loads a workflow it has never seen
    — which is the point of both cases. Since story-048 what they mutate is a
    copy of the *built* definition rather than of the deployed one.
    """
    return conftest.materialize_workflow(workflow, tmp_path / "harness")


def probe_workflow() -> dict:
    """A fresh copy of the built definition for a caller to mutate.

    Keeps the built name, so the target's configuration — which names it —
    still finds it in whichever harness root the caller materializes it into.
    """
    return copy.deepcopy(WORKFLOW)


def executable_source(text: str) -> str:
    """Strip docstrings and comment lines; prose may name what code may not."""
    kept, in_docstring = [], False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if not (len(stripped) > 3 and stripped.rstrip().endswith('"""')
                    and stripped.rstrip() != '"""'):
                in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


#: The two pre-story texts this module compares against, carried as committed
#: fixtures rather than resolved out of this repository's commit graph. Only
#: the coordinator's *docstring* is carried, because only the docstring is
#: compared: a fixture holds what an assertion reads and no more.
PRE_STORY_FIXTURES = {
    "schemas/revert-check-result.schema.json":
        "revert-check-result.schema.at-story-019-baseline.json",
    "orchestration/story_coordinator.py":
        "story_coordinator-docstring.at-story-019-baseline.txt",
}


def pre_story(path: str) -> str:
    """A repository file as it stood before this story's own run.

    Carried as a committed fixture under `tests/history-fixtures/`. It used to
    be resolved through the shared range in conftest.py, which was right about
    the *bound* — never HEAD, because a HEAD comparison survives nothing — and
    wrong about the source: the answer then moved whenever this repository was
    committed to, renamed, squashed or rebased, none of which is a property of
    what the schema said before the story. The text is the same text, lifted
    from that same baseline; it is now evidence the repository holds.
    """
    return conftest.history_fixture(PRE_STORY_FIXTURES[path])


# --------------------------------------------------------------------------
# The defect, reproduced: HEAD cannot restore what the run created
# --------------------------------------------------------------------------


def clone_with_the_working_tree_applied(target_root: Path, clone: Path) -> None:
    """The clone builder's sequence up to where the revert used to happen.

    Written out here rather than called, because the revert the story removed
    ran *before* the builder's commit: after the commit every path in the
    clone has a HEAD version, including the ones that had none in the target,
    and the failure being reproduced could not occur.
    """
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks",
                    str(target_root), str(clone)], cwd=target_root, check=True,
                   capture_output=True)
    diff = git(target_root, "diff", "--binary", "HEAD").stdout
    if diff.strip():
        subprocess.run(["git", "-C", str(clone), "apply", "--whitespace=nowarn", "-"],
                       input=diff, text=True, check=True, capture_output=True)
    untracked = git(target_root, "ls-files", "--others", "--exclude-standard",
                    "-z").stdout.split("\0")
    for rel in filter(None, untracked):
        source = target_root / rel
        if source.is_file():
            destination = clone / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def test_the_old_head_baseline_cannot_restore_a_file_created_during_the_run(
    target, tmp_path,
):
    """story-018's escalation, constructed rather than asserted from its run.

    A file the tester wrote this run reaches the clone (the builder carries
    untracked files), but it has no version at HEAD, so the checkout the
    check used to perform fails outright — which is why the check reported
    that it could not run and the coordinator escalated.

    The control is the file that predates the story: the same checkout in the
    same clone succeeds, so what fails here is the baseline, not the clone.
    """
    creates_the_broken_new_test(target, run_dir_of(target))
    clone = tmp_path / "clone"
    clone_with_the_working_tree_applied(target, clone)
    assert (clone / "tests" / "test_new.py").is_file()

    created = git(clone, "checkout", "HEAD", "--", "tests/test_new.py", check=False)
    predates = git(clone, "checkout", "HEAD", "--", "tests/test_app.py", check=False)

    assert created.returncode != 0
    assert "tests/test_new.py" in created.stderr
    assert predates.returncode == 0


def test_the_captured_baseline_restores_the_same_file_the_checkout_could_not(
    target, tmp_path,
):
    """The repair, at the level the reproduction above failed at."""
    creates_the_broken_new_test(target, run_dir_of(target))
    baseline = capture(target, tmp_path / "before")
    assert (baseline / "tests" / "test_new.py").read_text() == TEST_NEW_BROKEN

    repairs_the_new_test(target, run_dir_of(target))
    clone = tmp_path / "clone"
    story_coordinator._build_clone(target, clone, revert=["tests/test_new.py"],
                                   baseline=baseline)
    assert git(clone, "show", "HEAD:tests/test_new.py").stdout == TEST_NEW_BROKEN


# --------------------------------------------------------------------------
# The premises: the retried edits really are a repair and free coverage
# --------------------------------------------------------------------------


def test_reverting_the_repair_of_the_run_created_file_fails_the_suite(
    target, tmp_path,
):
    """The premise under the permitted case below. Without the second half,
    the check could be failing the suite for some unrelated reason and
    "permitted" would mean nothing."""
    module_only(target, run_dir_of(target))
    creates_the_broken_new_test(target, run_dir_of(target))
    baseline = capture(target, tmp_path / "before")
    repairs_the_new_test(target, run_dir_of(target))

    reverted = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "with-revert",
        revert=["tests/test_new.py"], baseline=baseline)
    intact = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "no-revert")

    assert reverted.ran is True and reverted.exit_code != 0
    assert intact.ran is True and intact.exit_code == 0


def test_the_function_appended_to_the_run_created_file_needed_no_change(tmp_path):
    """The premise under the escalated case: the appended function passes
    against the tree as the tester left it and as the implementer left it, so
    the only thing separating it from a repair is that reverting it costs
    nothing."""
    for name, new_test in (("before", TEST_NEW_PASSING),
                           ("after", TEST_NEW_PASSING + ADDED_COVERAGE_NEW)):
        scratch = tmp_path / name
        write(scratch / "conftest.py", ROOT_CONFTEST)
        write(scratch / "src" / "app.py", APP_ADDITIVE)
        write(scratch / "tests" / "conftest.py", TESTS_CONFTEST_AT_HEAD)
        write(scratch / "tests" / "test_app.py", TEST_APP_AT_HEAD)
        write(scratch / "tests" / "test_new.py", new_test)
        assert suite_in(scratch) == 0, name


# --------------------------------------------------------------------------
# story-018's run shape, decided
# --------------------------------------------------------------------------

RETRY_SHAPE = {WRITING: [module_only, repairs_the_new_test],
               VALIDATING: [creates_the_broken_new_test, unchanged]}


def test_a_forced_edit_to_a_file_created_earlier_in_the_run_is_permitted(
    target, harness_root,
):
    """The whole story, end to end: a tester creates a file this run, the
    verifier fails, the retried implementer repairs that file, and the check
    reaches a decision where it used to report that it could not run."""
    code, runner = run(target, harness_root, RETRY_SHAPE, [FAIL, PASS])
    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["exit_code"] != 0
    assert record["paths"] == ["tests/test_new.py"]
    assert record["baseline"].endswith(WRITING)


def test_the_run_records_which_run_created_path_was_reverted_and_why(
    target, harness_root,
):
    code, _ = run(target, harness_root, RETRY_SHAPE, [FAIL, PASS])
    assert code == 0
    events = (run_dir_of(target) / "events.log").read_text()
    permitting = [line for line in events.splitlines() if "permitted" in line]
    assert len(permitting) == 1
    assert WRITING in permitting[0]
    assert PREFIX in permitting[0]
    assert "tests/test_new.py" in permitting[0]
    assert record_of(target)["output_tail"]


def test_an_additive_edit_to_a_file_created_earlier_in_the_run_is_refused(
    target, harness_root,
):
    """The other half of the same decision. The retry is identical; only what
    the implementer did to the tester's file differs.

    The verdict is the subject; since story-106 a refusal undoes the edit and
    the run carries on rather than stopping, so the tree the run ends on holds
    what the baseline held for that path — the version the tester left.
    """
    code, runner = run(
        target, harness_root,
        {WRITING: [module_only, adds_to_the_new_test],
         VALIDATING: [creates_the_passing_new_test, unchanged]},
        [FAIL, PASS],
    )
    assert code == 0
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert record["paths"] == ["tests/test_new.py"]
    assert record["reverted"]["restored"] == ["tests/test_new.py"]

    events = (run_dir_of(target) / "events.log").read_text()
    reverting = [line for line in events.splitlines()
                 if "tests/test_new.py" in line and "revert" in line]
    assert reverting
    assert any(WRITING in line and PREFIX in line for line in reverting)


def test_the_baseline_of_the_retry_holds_the_file_the_tester_left(
    target, harness_root,
):
    """What the check decided against, read off the run directory: the
    tester's content, captured before the retried implementer touched it.

    The control is the enumeration the fix turns on: tracked files alone do
    not include this path, so a capture built on `git ls-files --cached`
    would have held nothing for it and the restore would have deleted the
    file instead of restoring it.

    The tracked set is read at the commit the run started from rather than at
    HEAD, because `_complete` commits the working tree — after the run this
    file has a HEAD version, and it had none for the whole of the run, which
    is the condition the check had to survive.
    """
    assert run(target, harness_root, RETRY_SHAPE, [FAIL, PASS])[0] == 0
    captured = baseline_at(target, WRITING) / "tests" / "test_new.py"
    assert captured.read_text() == TEST_NEW_BROKEN

    tracked = git(target, "ls-tree", "-r", "--name-only", "HEAD^", "--",
                  PREFIX).stdout.split()
    assert "tests/test_new.py" not in tracked
    assert "tests/test_app.py" in tracked      # the control for the listing


# --------------------------------------------------------------------------
# A path that predates the story is still decided against its HEAD content
# --------------------------------------------------------------------------


def test_a_forced_edit_to_a_pre_existing_governed_path_is_still_permitted(
    target, harness_root,
):
    code, _ = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]


def test_an_additive_edit_to_a_pre_existing_governed_path_is_still_refused(
    target, harness_root,
):
    code, _ = run(target, harness_root, {WRITING: [added_coverage]})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is False
    assert record["paths"] == ["tests/test_app.py"]
    assert record["reverted"]["restored"] == ["tests/test_app.py"]


def test_a_pre_existing_path_is_restored_to_exactly_its_head_content(
    target, tmp_path,
):
    """The baseline changed for files the run created; for a file that
    predates the story it is still, byte for byte, what HEAD holds.

    The control is the second clone: the same builder with nothing reverted
    carries the edit, so this is a statement about the revert rather than
    about a clone that never saw the change.
    """
    at_head = git(target, "show", "HEAD:tests/test_app.py").stdout
    baseline = capture(target, tmp_path / "before")
    forced_repair(target, run_dir_of(target))

    story_coordinator._build_clone(target, tmp_path / "reverted",
                                   revert=["tests/test_app.py"],
                                   baseline=baseline)
    story_coordinator._build_clone(target, tmp_path / "default")

    assert git(tmp_path / "reverted", "show", "HEAD:tests/test_app.py").stdout == at_head
    assert git(tmp_path / "default", "show", "HEAD:tests/test_app.py").stdout \
        == TEST_APP_REPAIRED
    # Everything outside the reverted path is present in both.
    for clone in ("reverted", "default"):
        assert "salute" in git(tmp_path / clone, "show", "HEAD:src/app.py").stdout


# --------------------------------------------------------------------------
# A governed path the baseline does not hold is deleted, not skipped
# --------------------------------------------------------------------------


def test_a_governed_path_absent_from_the_baseline_is_deleted_in_the_clone(
    target, tmp_path,
):
    baseline = capture(target, tmp_path / "before")
    fixture_and_a_test_that_needs_it(target, run_dir_of(target))
    paths = ["tests/conftest.py", "tests/test_uses_fixture.py"]

    story_coordinator._build_clone(target, tmp_path / "reverted", revert=paths,
                                   baseline=baseline)
    committed = git(tmp_path / "reverted", "ls-files").stdout.split()

    assert "tests/test_uses_fixture.py" not in committed
    assert not (tmp_path / "reverted" / "tests" / "test_uses_fixture.py").exists()
    # The control: the path the baseline *does* hold is present and restored,
    # so the deletion above is about absence from the baseline and not about
    # the revert list emptying the prefix.
    assert "tests/conftest.py" in committed
    assert git(tmp_path / "reverted", "show",
               "HEAD:tests/conftest.py").stdout == TESTS_CONFTEST_AT_HEAD


def test_the_decision_that_deletion_reaches_is_a_real_one(target, harness_root):
    """A governed path the stage itself brought into existence, recorded as a
    modification so the ownership check lets it through, is decided: nothing
    forced either edit, and the check refuses them."""
    code, _ = run(target, harness_root,
                  {WRITING: [fixture_and_a_test_that_needs_it]})
    assert code == 0
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["paths"] == ["tests/conftest.py", "tests/test_uses_fixture.py"]


def test_skipping_that_path_instead_would_have_reported_the_opposite(
    target, tmp_path,
):
    """The control for the two tests above, and the reason the story refuses
    to skip a path with no version at HEAD.

    The same clone built the skipping way — the governed path absent from the
    baseline left in place, everything else reverted — fails the suite,
    because the test the stage created needs the fixture the revert removed.
    A check reading that failure would have called the whole set permitted:
    a permission it never established.
    """
    baseline = capture(target, tmp_path / "before")
    fixture_and_a_test_that_needs_it(target, run_dir_of(target))

    deleting = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "deleting",
        revert=["tests/conftest.py", "tests/test_uses_fixture.py"],
        baseline=baseline)
    skipping = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "skipping",
        revert=["tests/conftest.py"], baseline=baseline)

    assert deleting.ran is True and deleting.exit_code == 0     # refuses
    assert skipping.ran is True and skipping.exit_code != 0     # would permit


# --------------------------------------------------------------------------
# No code path reverts to HEAD, and nothing is skipped for lacking one
# --------------------------------------------------------------------------

#: A git checkout carrying a HEAD-derived revision: the idiom this story
#: removed. Written as a pattern rather than a literal so a reordering of the
#: arguments is still caught.
_CHECKOUT_OF_HEAD = re.compile(r"checkout[^\n]{0,60}HEAD|HEAD[^\n]{0,60}checkout")

#: The control for the scan: a line of exactly the shape that was there.
_CHECKOUT_SAMPLE = '    reverted = _git(clone, "checkout", "HEAD", "--", *revert)'


def test_no_orchestration_module_reverts_anything_to_head():
    assert _CHECKOUT_OF_HEAD.search(_CHECKOUT_SAMPLE)      # the scan can fail
    offenders = {
        module.name
        for module in sorted(ORCHESTRATION.glob("*.py"))
        if _CHECKOUT_OF_HEAD.search(
            executable_source(module.read_text(encoding="utf-8")))
    }
    assert offenders == set()


def test_the_only_head_the_clone_builder_names_is_the_working_tree_it_applies():
    """Narrower than the scan above and pointed at the one function that
    could plausibly still hold it. The control is the first half of the same
    function, where `git diff HEAD` is how the working tree is applied and is
    not a baseline for anything."""
    source = executable_source(inspect.getsource(story_coordinator._build_clone))
    applying, separator, reverting = source.partition("if revert:")
    assert separator, source
    assert "HEAD" in applying
    assert "HEAD" not in reverting


def test_the_revert_check_names_no_head_at_all():
    body = executable_source(inspect.getsource(story_coordinator.revert_check))
    assert "baseline" in body                  # the stripping kept the code
    assert "HEAD" not in body


def test_a_governed_path_with_no_head_version_is_decided_rather_than_skipped(
    target, harness_root,
):
    """The behavioral form of "no path is skipped for lacking a HEAD
    version": the run above whose only governed path has none reaches a
    verdict, and the record names it as reverted rather than omitting it.

    HEAD^ is where the run started, and is what HEAD was for the whole of it;
    the run's own commit is the one `_complete` made after the check had
    already decided. The control is the governed path that did have a version
    there."""
    assert run(target, harness_root, RETRY_SHAPE, [FAIL, PASS])[0] == 0
    assert git(target, "cat-file", "-e", "HEAD^:tests/test_new.py",
               check=False).returncode != 0
    assert git(target, "cat-file", "-e", "HEAD^:tests/test_app.py",
               check=False).returncode == 0
    record = record_of(target)
    assert record["paths"] == ["tests/test_new.py"]
    assert record["permitted"] is True


# --------------------------------------------------------------------------
# A check that cannot run refuses rather than permits
# --------------------------------------------------------------------------


def test_a_stage_declaring_the_check_with_no_baseline_escalates_naming_it(
    target, harness_root,
):
    code, _ = run(target, harness_root,
                  {WRITING: [repair_then_discard_the_baseline]})
    assert code == 2
    assert state_of(target)["status"] == "escalated"

    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert "baseline" not in record
    assert BASELINE in record["reason"]

    _, summary = evidence(target)
    assert "could not run" in summary
    assert "tests/test_app.py" in summary


def test_the_same_run_with_its_baseline_intact_decides(target, harness_root):
    """The control for the refusal above: the identical edit, the identical
    record, and the only difference is that the baseline is still there. A
    check that refused everything could not pass both."""
    code, _ = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 0
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert BASELINE in record["baseline"]


def test_a_missing_baseline_falls_back_to_nothing(target, harness_root):
    """Not to HEAD, and not to reverting nothing. Both fallbacks would have
    produced a *decision* here — `tests/test_app.py` has a HEAD version and
    the working tree passes — so a run that refuses instead is evidence that
    neither fallback exists."""
    assert run(target, harness_root,
               {WRITING: [repair_then_discard_the_baseline]})[0] == 2
    assert record_of(target)["ran"] is False


def test_reverting_with_no_baseline_raises_rather_than_reverting_nothing(
    target, tmp_path,
):
    """The clone builder's own refusal, at the level below the check. A
    builder that silently reverted nothing would hand the check a clone
    carrying the edits, and every edit would be permitted."""
    forced_repair(target, run_dir_of(target))
    with pytest.raises(RuntimeError):
        story_coordinator._build_clone(target, tmp_path / "no-baseline",
                                       revert=["tests/test_app.py"])
    with pytest.raises(RuntimeError):
        story_coordinator._build_clone(target, tmp_path / "absent",
                                       revert=["tests/test_app.py"],
                                       baseline=tmp_path / "nowhere")
    # The control: with a baseline the same call builds the clone.
    baseline = capture(target, tmp_path / "captured")
    story_coordinator._build_clone(target, tmp_path / "built",
                                   revert=["tests/test_app.py"],
                                   baseline=baseline)
    assert (tmp_path / "built" / "tests" / "test_app.py").is_file()


def test_a_clone_that_cannot_be_built_still_escalates_naming_why(
    target, harness_root, monkeypatch,
):
    """The other cannot-run case, unchanged by this story."""
    def unbuildable(*args, **kwargs):
        raise RuntimeError("the clone could not be built")

    monkeypatch.setattr(story_coordinator, "_build_clone", unbuildable)
    code, _ = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 2
    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert "could not be built" in record["reason"]
    _, summary = evidence(target)
    assert "could not run" in summary


# --------------------------------------------------------------------------
# Capture once, reuse afterwards
# --------------------------------------------------------------------------


def test_a_second_capture_for_the_same_stage_does_not_overwrite(
    target, tmp_path,
):
    scratch = tmp_path / "run"
    first = capture(target, scratch, stage=WRITING)
    assert (first / "tests" / "test_app.py").read_text() == TEST_APP_AT_HEAD

    forced_repair(target, run_dir_of(target))
    again = capture(target, scratch, stage=WRITING)

    assert again == first
    assert (again / "tests" / "test_app.py").read_text() == TEST_APP_AT_HEAD
    # The control: a fresh capture, into a run directory holding no baseline
    # for this stage, does see the edit — so the reuse above is the first-seen
    # rule, not a capture that has stopped reading the tree. Since story-037
    # the directory is keyed by stage alone, so a fresh run directory is what
    # distinguishes it rather than a different attempt number.
    later = capture(target, tmp_path / "fresh", stage=WRITING)
    assert (later / "tests" / "test_app.py").read_text() == TEST_APP_REPAIRED


def test_the_baseline_directory_exists_even_when_it_captures_nothing(
    target, tmp_path,
):
    """Its existence answers "was a baseline taken", so an empty capture and
    an absent one are different conditions — the second is what the check
    refuses on."""
    empty = capture(target, tmp_path / "run", prefix="nothing-here/")
    assert empty.is_dir()
    assert list(empty.rglob("*")) == []
    # The control: the same call over a prefix with files captures them.
    assert (capture(target, tmp_path / "run", stage="other") / "tests").is_dir()


def test_a_re_entered_stage_is_decided_against_the_baseline_it_first_found(
    target, harness_root,
):
    """A run interrupted inside the implementer and resumed there. The stage
    is re-entered at the same attempt with its edits already in the tree, and
    the reused baseline is what makes the decision honest."""
    interrupted = Runner(target, {WRITING: [forced_repair]},
                         interrupt=(WRITING, 1))
    with pytest.raises(KeyboardInterrupt):
        story_coordinator.run_story("story-001", harness_root, target, interrupted)
    assert state_of(target)["status"] == "running"
    assert (target / "tests" / "test_app.py").read_text() == TEST_APP_REPAIRED

    code, resumed = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 0
    assert resumed.calls[0] == WRITING
    record = record_of(target)
    assert record["permitted"] is True
    assert record["baseline"].endswith(WRITING)
    # The baseline it decided against is the one taken before the first
    # invocation, not the state the interrupted stage left behind.
    captured = baseline_at(target, WRITING) / "tests" / "test_app.py"
    assert captured.read_text() == TEST_APP_AT_HEAD


def test_a_baseline_recaptured_after_the_edit_would_have_reversed_that(
    target, tmp_path,
):
    """The control for the reuse rule, and the reason it is a rule rather
    than an optimisation: a stage that snapshotted its own completed edits
    would revert nothing, the suite would pass, and the same forced repair
    that is permitted above would be escalated."""
    forced_repair(target, run_dir_of(target))
    recaptured = capture(target, tmp_path / "after")
    result = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "no-op",
        revert=["tests/test_app.py"], baseline=recaptured)
    assert result.ran is True
    assert result.exit_code == 0            # permitted would be False


# --------------------------------------------------------------------------
# Both names come off the declaration
# --------------------------------------------------------------------------


def test_removing_the_declaration_disables_the_capture_and_the_check(
    target, tmp_path,
):
    """One key, both mechanisms, no code change: the record the check refuses
    and reverts against the probe workflow is left entirely alone against the
    same workflow with `revert_check` removed, and no baseline is taken at
    all."""
    workflow = probe_workflow()
    for stage in workflow["stages"]:
        stage.pop("revert_check", None)
    fake_root = mirror_harness(tmp_path / "no-declaration", workflow)

    code, _ = run(target, fake_root, {WRITING: [added_coverage]})
    assert code == 0
    run_dir = run_dir_of(target)
    assert not (run_dir / ARTIFACT).exists()
    assert not (run_dir / BASELINE).exists()


def test_with_the_declaration_the_same_run_captures_and_decides(
    target, harness_root,
):
    """The control for the removal above."""
    code, _ = run(target, harness_root, {WRITING: [added_coverage]})
    assert code == 0
    run_dir = run_dir_of(target)
    assert (run_dir / ARTIFACT).exists()
    assert (run_dir / BASELINE / WRITING).is_dir()
    assert record_of(target)["permitted"] is False


def test_moving_the_declaration_moves_the_capture_and_the_check(
    target, tmp_path,
):
    """A workflow the coordinator has never seen, governing a different
    prefix on a different stage. The baseline follows the declaration, keyed
    by the stage that declared it."""
    workflow = probe_workflow()
    for stage in workflow["stages"]:
        stage.pop("may_not_create", None)
        stage.pop("revert_check", None)
        if stage["name"] == VALIDATING:
            stage["may_not_create"] = ["src/"]
            stage["revert_check"] = DECLARATION
    fake_root = mirror_harness(tmp_path / "moved", workflow)

    code, _ = run(target, fake_root, {WRITING: [added_coverage],
                                      VALIDATING: [module_only]})
    assert code == 0
    run_dir = run_dir_of(target)
    assert (run_dir / BASELINE / VALIDATING).is_dir()
    assert not (run_dir / BASELINE / WRITING).exists()
    assert (run_dir / BASELINE / VALIDATING / "src" / "app.py").is_file()
    assert record_of(target)["paths"] == ["src/app.py"]


def test_no_prefix_and_neither_declared_name_is_in_the_code():
    """Docstrings and comments are stripped first: prose may name what code
    may not. The control is the name that legitimately does appear.

    The stage-name half moved to tests/test_shipped_workflow_is_valid.py when
    story-048 converted this module: asked of the workflow built above it would
    be vacuous, because the builder's names are its own and no source contains
    them. It is asked there of the names this repository deploys, under
    `test_the_coordinator_source_names_no_stage_this_deployment_declares`.
    """
    body = executable_source(
        (ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8"))
    assert PREFIX not in body
    assert ARTIFACT not in body
    assert BASELINE not in body
    assert "clean-clone-result.json" not in body       # read off the workflow too
    assert "state.json" in body                        # a name the code owns
    # The negative control for the four absences: the same scan over a source
    # that does name each one reports it.
    planted = executable_source(
        f"A = {PREFIX!r}\nB = {ARTIFACT!r}\nC = {BASELINE!r}\n"
        "D = 'clean-clone-result.json'\n")
    for name in (PREFIX, ARTIFACT, BASELINE, "clean-clone-result.json"):
        assert name in planted, name


def test_the_capture_names_no_stage_and_no_prefix():
    body = executable_source(
        inspect.getsource(story_coordinator.capture_stage_baseline))
    assert "ls-files" in body                  # the stripping kept the code
    assert PREFIX not in body
    assert BASELINE not in body
    for stage in WORKFLOW["stages"]:
        assert stage["name"] not in body, stage["name"]


# --------------------------------------------------------------------------
# The baseline is evidence, never state
# --------------------------------------------------------------------------

#: state.json's fields as of story-019, written here rather than read off the
#: dataclass that produces them — a comparison against its own source could not
#: fail. A later story may add a field for its own reasons: story-020 added
#: three so a resume can tell what has changed since a run escalated. What this
#: story claims is narrower and is what the assertion below now states — the
#: *baseline* added none, and none of the fields that arrived later names it.
STATE_FIELDS = {"story_id", "branch", "status", "current_stage", "retry_count",
                "verification_iterations", "artifacts"}


def test_state_json_gains_no_field_and_never_names_the_baseline(
    target, harness_root,
):
    assert run(target, harness_root, RETRY_SHAPE, [FAIL, PASS])[0] == 0
    state_text = (run_dir_of(target) / "state.json").read_text()
    fields = set(json.loads(state_text))
    assert STATE_FIELDS <= fields
    assert [name for name in fields if "baseline" in name] == []
    assert BASELINE not in state_text
    # The control: the run this state describes did capture a baseline, so
    # the absence above is about state.json rather than about a run that
    # never took one.
    assert (run_dir_of(target) / BASELINE / WRITING).is_dir()


def test_nothing_in_run_story_routes_on_the_baseline():
    """It is consumed to build a clone and nowhere else. Routing is a branch,
    a reroute or a return, so the check is that no line mentioning the
    baseline is one — with the verdict, which *is* branched on, as the
    control for a scan that had stopped matching anything."""
    lines = executable_source(
        inspect.getsource(story_coordinator.run_story)).splitlines()

    def routing(name: str) -> list[str]:
        return [line for line in lines if name in line
                and re.search(r"\b(if|elif|else|return|continue)\b|index\s*=", line)]

    assert routing("decided")                 # the verdict is routed on
    assert routing("baseline") == []


def test_the_baseline_carries_no_schema_and_no_manifest_entry():
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert BASELINE not in manifest["schemas"]
    assert not (REPO_ROOT / "schemas" / f"{BASELINE}.schema.json").exists()
    # The control: the artifact declared beside it does carry one.
    assert SCHEMA_STEM in manifest["schemas"]
    assert SCHEMA_PATH.is_file()


# --------------------------------------------------------------------------
# The schema says what the check now reverts
# --------------------------------------------------------------------------


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_the_paths_description_names_the_baseline_and_no_longer_names_head():
    """Read against the pre-story schema rather than against a phrase written
    here, so the control is the text that actually said HEAD."""
    before = json.loads(pre_story("schemas/revert-check-result.schema.json"))
    now = schema()
    assert "HEAD" in before["properties"]["paths"]["description"]
    described = now["properties"]["paths"]["description"]
    assert "reverted from HEAD" not in described
    assert "baseline" in described.lower()


def test_the_granularity_limit_is_unchanged_in_the_schema_and_the_docstring():
    """The story changed the baseline, not what the check decides at. Both
    statements are compared with their pre-story text, and the control is the
    paths description in the same file, which did change.

    Story-077 added a short-circuit and stated its residual risk in the same
    top-level description, so equality was narrowed to `startswith`: every byte
    of the granularity statement is still asserted verbatim and in place, and
    the only edit that passes is one appending after it. It is not a
    relaxation of the subject — what this test is about is that the
    granularity limit reads as it read, and appending to a description cannot
    change it.

    Story-106 did the same to the module docstring, stating what a refused
    verdict now does in a paragraph after the granularity statement, so the
    docstring half is narrowed the same way and for the same reason: every
    byte of what this test is about is still asserted verbatim and in place.
    """
    before = json.loads(pre_story("schemas/revert-check-result.schema.json"))
    now = schema()
    assert now["description"].startswith(before["description"])
    assert now["properties"]["paths"]["description"] \
        != before["properties"]["paths"]["description"]

    module = ORCHESTRATION / "story_coordinator.py"
    pre_module = pre_story("orchestration/story_coordinator.py")
    assert ast.get_docstring(
        ast.parse(module.read_text(encoding="utf-8"))).startswith(pre_module)


def test_the_baseline_field_is_optional_and_describes_what_it_names():
    definition = schema()["properties"]["baseline"]
    assert definition["type"] == "string"
    assert "baseline" not in schema().get("required", [])
    assert "stage" in definition["description"].lower()


def test_the_record_satisfies_the_schema_with_the_baseline_and_without_it(
    target, harness_root,
):
    assert run(target, harness_root, RETRY_SHAPE, [FAIL, PASS])[0] == 0
    loaded = schema_validator.load_schema(SCHEMA_STEM)
    record = record_of(target)
    assert "baseline" in record
    assert schema_validator.validate(record, loaded) == []
    without = {k: v for k, v in record.items() if k != "baseline"}
    assert schema_validator.validate(without, loaded) == []
    # The control: the validator against the same schema still rejects a
    # record missing what the check must always report.
    assert schema_validator.validate(
        {k: v for k, v in record.items() if k != "ran"}, loaded) != []


# --------------------------------------------------------------------------
# The clean-clone check is unaffected
# --------------------------------------------------------------------------


def test_both_clone_entry_points_still_default_to_reverting_nothing():
    for function in (story_coordinator._build_clone,
                     story_coordinator.run_clean_clone):
        parameters = inspect.signature(function).parameters
        assert tuple(parameters["revert"].default) == ()
        assert parameters["baseline"].default is None


def test_the_clean_clone_check_still_writes_its_record_and_event_and_routes(
    target, harness_root,
):
    verifier = next(s for s in WORKFLOW["stages"] if "clean_clone" in s)
    assert run(target, harness_root, {WRITING: [forced_repair]})[0] == 0
    run_dir = run_dir_of(target)
    clean = json.loads((run_dir / verifier["clean_clone"]["result"]).read_text())
    events = (run_dir / "events.log").read_text()

    assert clean["ran"] is True and clean["exit_code"] == 0
    assert "clean-clone" in events
    assert "baseline" not in clean
    assert state_of(target)["status"] == "completed"


def test_the_clean_clone_check_asks_for_no_revert_and_no_baseline(
    target, harness_root, monkeypatch,
):
    """Every call into the shared clone runner during a run, with what it was
    asked to revert and restore from. The revert check's call is the control:
    if the spy had stopped seeing anything, there would be no call carrying a
    baseline either."""
    calls: list[tuple] = []
    original = story_coordinator.run_clean_clone

    def spy(*args, **kwargs):
        calls.append((tuple(kwargs.get("revert", ())), kwargs.get("baseline")))
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "run_clean_clone", spy)
    assert run(target, harness_root, {WRITING: [forced_repair]})[0] == 0

    assert ((), None) in calls                       # the clean-clone check
    with_baseline = [call for call in calls if call[1] is not None]
    assert len(with_baseline) == 1                   # the revert check
    assert with_baseline[0][0] == ("tests/test_app.py",)


def test_no_second_clone_builder_was_added():
    """One function clones, and it is the one that always did. The control is
    that the search finds it at all."""
    tree = ast.parse((ORCHESTRATION / "story_coordinator.py").read_text())

    def invokes_git_clone(node: ast.FunctionDef) -> bool:
        """An argv list that starts a `git clone`, not a variable named clone."""
        for inner in ast.walk(node):
            if not isinstance(inner, ast.List):
                continue
            words = [element.value for element in inner.elts
                     if isinstance(element, ast.Constant)]
            if words[:2] == ["git", "clone"]:
                return True
        return False

    cloners = [node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and invokes_git_clone(node)]
    assert cloners == ["_build_clone"]


# --------------------------------------------------------------------------
# What this story left alone
# --------------------------------------------------------------------------


def test_this_story_edited_no_story_artifact_and_no_schema_inventory(tmp_path):
    """The control is the file the story did edit: if the diff resolution had
    stopped seeing anything, the last assertion would fail too.

    Restated over a story this test builds. The four claims are what they were
    — records untouched, the inventory untouched, no schema added, and the one
    schema the story did edit reported — and each is now checked against a
    history constructed here rather than recalled out of this repository's own
    graph, where a rename, a squash or a rebase moved the evidence under it.
    """
    added = "schemas/revert-check-result.schema.json"
    root = conftest.constructed_story(
        tmp_path, respected=[".harness/stories/", "schemas/manifest.json"],
        violated=[added])
    assert conftest.constructed_story_diff(root, [".harness/stories/"]) == ""
    assert conftest.constructed_story_diff(root, ["schemas/manifest.json"]) == ""
    assert conftest.constructed_story_diff(root, ["schemas/"],
                                           diff_filter="A") == ""
    assert conftest.constructed_story_diff(root, [added]) != ""
