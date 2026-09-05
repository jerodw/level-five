"""Independent validation for story-017: deciding an implementer's test
edits by reverting them.

Written from the story's acceptance criteria. The subject is a decision
about *edits*, so almost nothing here is asserted from source: a target
repository with a real pytest suite is built under tmp_path, a fake
implementer edits its working tree, and the coordinator is run. Whether an
edit is permitted is then whatever the suite does in a clone with that edit
restored from HEAD - the same question the check asks, answered by running
it rather than by reading the code that runs it.

The two premises the routing rests on are reconstructed first, before any
routing is asserted:

  * the forced repair really is forced - reverting `tests/test_app.py`
    alone makes the suite fail, and the same clone with nothing reverted
    passes; and
  * the added coverage really is coverage - the test function the fake
    implementer appends passes against the module both before and after the
    implementer's change, so the only thing separating it from a repair is
    that reverting it costs nothing.

Without those two, a check that permitted everything and a check that
permitted nothing would both look green below.

Every absence asserted here carries a control. "No artifact is written"
sits beside a run that writes one; "no clone is built" is a count against a
run that builds one; "the artifact name appears in no orchestration module"
is paired with the name that does appear; "this story edited no story
artifact" is paired with the file it did edit.

Nothing here invokes a model: every run goes through a fake agent runner,
and every clone source is a local filesystem path.
"""
import ast
import inspect
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import STORY, story_diff
import conftest

import context_assembler
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
STORIES_DIR = REPO_ROOT / ".harness" / "stories"
TESTS_DIR = REPO_ROOT / "tests"

#: The prefix the writing stage is restricted from creating under, and the
#: directory the target's suite lives in. One value, stated once, because the
#: target repository below is built with its tests there and the workflow is
#: built to govern that same place — a fixture defines its names in one place
#: and everything else derives them from it.
GOVERNED_PREFIX = "tests/"

#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py`. story-048 made the change: the subject here is *how the
#: revert check decides an edit*, and the declaration that turns the check on is
#: an input to that question rather than its subject. Reading the deployed one
#: made "which stage this deployment restricts, and under what prefix" into
#: something this module enforced.
#:
#: Four stages, because the check must be shown to run for the stage that
#: declares it and for no other, and because the run has to reach a verdict.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        may_not_create=(GOVERNED_PREFIX,),
        revert_check={"result": "revert-check-result.json",
                      "baseline": "stage-baseline"},
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
        # Declared because several assertions below are about the revert check
        # *not* being the run's only clone: the clean-clone check is the other
        # one, and the counts here are stated against it rather than against
        # nothing.
        clean_clone={"result": conftest.CLEAN_CLONE_RESULT,
                     "retry_stage": conftest.StageRef(0)},
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="revert-check-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES

#: The stage that declares the check, found by the declaration rather than by
#: name, so this file names no stage the definition does not.
IMPLEMENTER_STAGE = next(s for s in WORKFLOW["stages"] if "revert_check" in s)
#: The artifact name and the governed prefix are read off the workflow, never
#: spelled here, for the same reason the coordinator may not spell them. Since
#: story-019 the declaration is an object naming both the result artifact and
#: the baseline directory the check restores from; both names are read off it.
DECLARATION = IMPLEMENTER_STAGE.get("revert_check")
ARTIFACT = DECLARATION["result"]
BASELINE = DECLARATION["baseline"]
PREFIX = IMPLEMENTER_STAGE["may_not_create"][0]

SCHEMA_STEM = "revert-check-result"
SCHEMA_PATH = REPO_ROOT / "schemas" / f"{SCHEMA_STEM}.schema.json"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest", "tests", "-q",
                           "-p", "no:cacheprovider"])

CONFIG = f"""\
workflow: {WORKFLOW["name"]}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: {TEST_COMMAND}
tests_dir: {GOVERNED_PREFIX}
"""

# --------------------------------------------------------------------------
# The target repository: a real module and a real suite over it.
#
# HEAD holds `greet` and a test calling it. The two implementer changes below
# are the two cases the check must tell apart, reduced to their smallest
# honest form: a rename, which forces the test to change, and an addition,
# which forces nothing.
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

#: A test function that passes against APP_AT_HEAD and against APP_ADDITIVE
#: alike. Appended to an existing file, it is a modification under the
#: governed prefix that no change forced.
ADDED_COVERAGE = '''

def test_greet_again():
    assert greet("again") == "hello, again"
'''

#: Added coverage that depends on nothing in the module at all, for the
#: file that mixes a forced repair with an addition.
INDEPENDENT_COVERAGE = '''

def test_addition_is_still_addition():
    assert 1 + 1 == 2
'''

TEST_EXTRA_AT_HEAD = '''\
def test_arithmetic():
    assert 1 + 1 == 2
'''

TEST_EXTRA_PLUS_COVERAGE = TEST_EXTRA_AT_HEAD + '''

def test_arithmetic_again():
    assert 2 + 2 == 4
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


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


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
    write(root / "tests" / "test_app.py", TEST_APP_AT_HEAD)
    write(root / "tests" / "test_extra.py", TEST_EXTRA_AT_HEAD)
    write(root / ".gitignore", ".pytest_cache/\n__pycache__/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the workflow built above.

    The rules and the schemas are the harness's rather than the workflow's, so
    they stay linked at the shipped ones: converting away from the shipped
    *workflow* is not building a second rule set.
    """
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "revert-harness")


# --------------------------------------------------------------------------
# The implementer's working-tree changes, each paired with the record that
# describes it. The record and the tree always say the same thing: the check
# reads the record and reverts inside a clone of the tree.
# --------------------------------------------------------------------------


def forced_repair(root: Path) -> dict:
    """A rename the existing test cannot survive, and the test updated."""
    write(root / "src" / "app.py", APP_RENAMED)
    write(root / "tests" / "test_app.py", TEST_APP_REPAIRED)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


def added_coverage(root: Path) -> dict:
    """An addition to the module, and a test the addition did not force."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / "tests" / "test_app.py", TEST_APP_AT_HEAD + ADDED_COVERAGE)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


def deleted_broken_test(root: Path) -> dict:
    """The same rename, with the broken test deleted rather than repaired."""
    write(root / "src" / "app.py", APP_RENAMED)
    (root / "tests" / "test_app.py").unlink()
    return {"modified": ["src/app.py"], "created": [],
            "deleted": ["tests/test_app.py"]}


def deleted_passing_test(root: Path) -> dict:
    """A deletion nothing forced: the test still passes after the change."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    (root / "tests" / "test_extra.py").unlink()
    return {"modified": ["src/app.py"], "created": [],
            "deleted": ["tests/test_extra.py"]}


def mixed_set(root: Path) -> dict:
    """One forced repair and one addition, in two different governed files."""
    write(root / "src" / "app.py", APP_RENAMED)
    write(root / "tests" / "test_app.py", TEST_APP_REPAIRED)
    write(root / "tests" / "test_extra.py", TEST_EXTRA_PLUS_COVERAGE)
    return {"modified": ["src/app.py", "tests/test_app.py", "tests/test_extra.py"],
            "created": [], "deleted": []}


def mixed_file(root: Path) -> dict:
    """One forced repair and one addition, inside a single governed file."""
    write(root / "src" / "app.py", APP_RENAMED)
    write(root / "tests" / "test_app.py", TEST_APP_REPAIRED + INDEPENDENT_COVERAGE)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


def nothing_governed(root: Path) -> dict:
    """A change that names no path under the governed prefix at all."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    return {"modified": ["src/app.py"], "created": [], "deleted": []}


class Runner:
    """A fake agent runner: each stage writes its artifacts, and the stage
    holding an edit also makes that edit in the target's working tree."""

    def __init__(self, target_root: Path, edits: dict, story_id: str = "story-001"):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.edits = edits
        self.records: dict[str, dict] = {}
        self.calls: list[str] = []
        #: What each stage was actually handed, so an assertion about a
        #: rendered prompt reads the run's own render rather than rebuilding
        #: one beside it.
        self.prompts: dict[str, str] = {}

    def _record(self, stage: str) -> dict:
        edit = self.edits.get(stage)
        record = edit(self.target_root) if edit else {"modified": [], "created": [],
                                                      "deleted": []}
        self.records[stage] = record
        return record

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        self.prompts[stage] = prompt
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES, self._record(stage))
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did it.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 1, "tests_run": 2,
                "tests_passed": 2, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       self._record(stage))
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
        elif stage == DOCUMENTING:
            write(self.run_dir / conftest.DOCUMENTATION_REPORT, "Nothing.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = "story-001") -> Path:
    return target_root / ".harness" / "runs" / story_id


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def evidence(target_root: Path) -> tuple[str, str]:
    """The two places an escalation reason must appear."""
    run_dir = run_dir_of(target_root)
    return ((run_dir / "events.log").read_text(),
            (run_dir / "escalation-summary.md").read_text())


def record_of(target_root: Path, artifact: str = ARTIFACT) -> dict:
    return json.loads((run_dir_of(target_root) / artifact).read_text())


def run(target_root: Path, harness: Path, edits: dict) -> tuple[int, Runner]:
    runner = Runner(target_root, edits)
    code = story_coordinator.run_story("story-001", harness, target_root, runner)
    return code, runner


def commit_setup(target_root: Path, message: str) -> None:
    """Commit setup a test made after the fixture built the repository.

    story-021's clean-tree pre-flight refuses a run whose target tree already
    holds work no stage produced, and a test's configuration or story artifact
    is exactly that: part of the repository the run starts *from*, not
    something the run is meant to commit. Committing it keeps every assertion
    below pointed at what it was pointed at.
    """
    subprocess.run(["git", "add", "-A"], cwd=target_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=target_root,
                   check=True)


def configure(target_root: Path, **overrides) -> None:
    path = target_root / ".harness" / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in overrides.items():
        for index, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                lines[index] = f"{key}: {value}"
                break
        else:
            lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    commit_setup(target_root, "configure the target for this test")


def mirror_harness(tmp_path: Path, workflow: dict) -> Path:
    """A harness root carrying a variant of the workflow built above."""
    workflow = {**workflow, "name": WORKFLOW["name"]}
    return conftest.materialize_workflow(workflow, tmp_path / "harness")


def loaded_workflow() -> dict:
    """A fresh copy of the built definition, for a caller about to mutate it."""
    return json.loads(json.dumps(WORKFLOW))


def append_to_story(target_root: Path, text: str) -> None:
    path = target_root / ".harness" / "stories" / "story-001.yaml"
    path.write_text(path.read_text() + text, encoding="utf-8")
    commit_setup(target_root, "the story artifact this test runs")


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


@pytest.fixture
def clone_calls(monkeypatch):
    """Every call into the shared clone runner, with what it reverted.

    The verifier's clean-clone check goes through the same runner, so the
    counts below are read as "a run with something reverted" rather than as
    "a run at all" - which is also how the no-governed-path criterion has to
    be read, since that run still performs the clean-clone check.
    """
    calls: list[tuple[str, ...]] = []
    original = story_coordinator.run_clean_clone

    def spy(*args, **kwargs):
        revert = kwargs.get("revert", args[4] if len(args) > 4 else ())
        calls.append(tuple(revert))
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "run_clean_clone", spy)
    return calls


@pytest.fixture
def builds(monkeypatch):
    """Every clone the coordinator builds during a run."""
    built: list[tuple[str, ...]] = []
    original = story_coordinator._build_clone

    def spy(target_root, clone, *, revert=(), baseline=None):
        built.append(tuple(revert))
        return original(target_root, clone, revert=revert, baseline=baseline)

    monkeypatch.setattr(story_coordinator, "_build_clone", spy)
    return built


def baseline_of(target_root: Path, scratch: Path, prefix: str = PREFIX) -> Path:
    """The governed prefix's content as it stands now, captured the way the
    coordinator captures it immediately before invoking a stage agent.

    Since story-019 a revert is a restore from such a capture rather than a
    checkout of HEAD, so a direct call into the clone builders needs one.
    Taken before the edit under test, which is where the coordinator takes it.
    """
    return story_coordinator.capture_stage_baseline(
        scratch, target_root, BASELINE, "stage", [prefix], accounted_for=set())


def suite_in(directory: Path) -> int:
    """Run the same suite shape the target configures, in a scratch tree."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=directory, capture_output=True, text=True,
    ).returncode


# --------------------------------------------------------------------------
# The premises: the fixtures really are a forced repair and free coverage
# --------------------------------------------------------------------------


def test_reverting_the_repair_fails_the_suite_and_reverting_nothing_passes(
    target, tmp_path,
):
    """The premise under every permitted case below. Without the second half
    of this the check could be failing the suite for some unrelated reason,
    and "permitted" would mean nothing."""
    baseline = baseline_of(target, tmp_path / "before")
    forced_repair(target)

    reverted = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "with-revert",
        revert=["tests/test_app.py"], baseline=baseline)
    intact = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "no-revert")

    assert reverted.ran is True and reverted.exit_code != 0
    assert intact.ran is True and intact.exit_code == 0


def test_the_added_test_passes_against_the_module_before_and_after(tmp_path):
    """The premise under every escalated case: the appended test function is
    coverage, not repair - the implementer's change neither forced it nor
    breaks it."""
    for name, module in (("before", APP_AT_HEAD), ("after", APP_ADDITIVE)):
        scratch = tmp_path / name
        write(scratch / "conftest.py", ROOT_CONFTEST)
        write(scratch / "src" / "app.py", module)
        write(scratch / "tests" / "test_app.py", TEST_APP_AT_HEAD + ADDED_COVERAGE)
        assert suite_in(scratch) == 0, name


# --------------------------------------------------------------------------
# A forced edit is permitted; free coverage is undone and the run carries on
#
# The refusal used to stop the run. It no longer does, and that is not
# leniency: reaching a refusal means the check built a clone with exactly
# those paths restored to what the stage found and watched the suite pass in
# it, so undoing them in the working tree reproduces a tree the harness has
# already proved good. What still stops a run is a check that reached no
# verdict, which the section further down is about.
# --------------------------------------------------------------------------


def test_a_forced_edit_under_the_governed_prefix_is_permitted(target, harness_root):
    code, runner = run(target, harness_root, {WRITING: forced_repair})
    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert runner.calls == STAGE_NAMES

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["exit_code"] != 0
    assert record["paths"] == ["tests/test_app.py"]


def test_the_run_records_why_the_forced_edit_was_permitted(target, harness_root):
    """A reader must be able to see why an implementer was allowed into the
    prefix, not only that it was."""
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    events = (run_dir_of(target) / "events.log").read_text()
    permitting = [line for line in events.splitlines() if "permitted" in line]
    assert len(permitting) == 1
    assert WRITING in permitting[0]
    assert PREFIX in permitting[0]
    assert "tests/test_app.py" in permitting[0]
    assert record_of(target)["output_tail"]


def test_an_edit_that_only_adds_coverage_is_reverted_and_the_run_continues(
    target, harness_root,
):
    """The refusal, and what it now does.

    The verdict is unchanged — the suite passes with the edit reverted, so
    nothing forced it — and the disposition is the whole of what moved: the
    run reaches every later stage instead of stopping at the one that made the
    edit. Its control is the permitted case above, which reaches the same
    stages by having earned them.
    """
    code, runner = run(target, harness_root, {WRITING: added_coverage})
    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert runner.calls == STAGE_NAMES

    record = record_of(target)
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert record["paths"] == ["tests/test_app.py"]


def test_the_reverted_path_holds_what_the_stage_baseline_captured(target,
                                                                  harness_root):
    """The tree after the revert is the tree the proof was taken on.

    Read off the working tree rather than off the record, and asserted against
    the content the file held before the stage ran — which is what the clone
    the check passed on carried. The ungoverned half of the same record is
    asserted to have survived, so this is the governed paths being restored
    rather than the whole change being thrown away.
    """
    assert run(target, harness_root, {WRITING: added_coverage})[0] == 0

    assert (target / "tests" / "test_app.py").read_text(
        encoding="utf-8") == TEST_APP_AT_HEAD
    assert (target / "src" / "app.py").read_text(
        encoding="utf-8") == APP_ADDITIVE


def test_the_record_says_what_was_undone_and_the_stages_own_record_does_not(
    target, harness_root,
):
    """The correction sits beside the attestation rather than inside it.

    An attestation is not rewritten behind its author, so the stage's
    changed-files record still names the edit exactly as the stage wrote it,
    and the revert-check record is where a reader learns it no longer stands.
    """
    _, runner = run(target, harness_root, {WRITING: added_coverage})

    reverted = record_of(target)["reverted"]
    assert reverted["restored"] == ["tests/test_app.py"]
    assert reverted["removed"] == []

    written = json.loads(
        (run_dir_of(target) / conftest.CHANGED_FILES).read_text())
    assert written == runner.records[WRITING]
    assert "tests/test_app.py" in written["modified"]


def test_a_deleted_governed_path_the_change_broke_is_permitted(target, harness_root):
    code, _ = run(target, harness_root, {WRITING: deleted_broken_test})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]


def test_a_refused_deletion_puts_the_file_back(target, harness_root):
    """The deletion half of the disposition: restored, not merely un-refused."""
    code, _ = run(target, harness_root, {WRITING: deleted_passing_test})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is False
    assert record["paths"] == ["tests/test_extra.py"]
    assert record["reverted"]["restored"] == ["tests/test_extra.py"]

    restored = target / "tests" / "test_extra.py"
    assert restored.is_file()
    assert restored.read_text(encoding="utf-8") == TEST_EXTRA_AT_HEAD


def test_the_revert_event_names_the_stage_the_restriction_and_the_paths(
    target, harness_root,
):
    """What a reader of the event stream can see, without the run stopping.

    The run completes, so there is no escalation summary to carry this — the
    event log is the whole of the record, which is why all three are asserted
    of it.
    """
    assert run(target, harness_root, {WRITING: added_coverage})[0] == 0
    events = (run_dir_of(target) / "events.log").read_text()
    reverting = [line for line in events.splitlines()
                 if "tests/test_app.py" in line and "revert" in line]
    assert reverting
    assert any(WRITING in line and PREFIX in line for line in reverting)


def test_a_reverted_run_leaves_the_retry_count_where_it_was(target, harness_root):
    """Nothing was retried and nothing failed: the stage's work stands minus
    the part the suite did not need."""
    assert run(target, harness_root, {WRITING: added_coverage})[0] == 0
    state = state_of(target)
    assert state["status"] == "completed"
    assert state["retry_count"] == 0


# --------------------------------------------------------------------------
# A record naming no governed path costs nothing
# --------------------------------------------------------------------------


def test_a_record_naming_no_governed_path_builds_no_clone_and_writes_nothing(
    target, harness_root, clone_calls, builds,
):
    code, _ = run(target, harness_root, {WRITING: nothing_governed})
    assert code == 0
    assert not (run_dir_of(target) / ARTIFACT).exists()
    # The one clone and the one suite run are the verifier's clean-clone
    # check, which reverts nothing; the revert check contributed neither.
    assert clone_calls == [()]
    assert builds == [()]


def test_the_same_run_with_a_governed_path_does_build_a_clone_and_write_one(
    target, harness_root, clone_calls, builds,
):
    """The control for the assertion above: identical machinery, one governed
    path added, and the clone, the suite run and the artifact all appear."""
    code, _ = run(target, harness_root, {WRITING: forced_repair})
    assert code == 0
    assert (run_dir_of(target) / ARTIFACT).exists()
    assert ("tests/test_app.py",) in clone_calls
    assert ("tests/test_app.py",) in builds


# --------------------------------------------------------------------------
# The check is driven by the declaration, not by the code
# --------------------------------------------------------------------------


def test_removing_the_declaration_disables_the_check(target, tmp_path, clone_calls):
    """The record the check refuses and reverts against the built workflow is
    left entirely alone against the same workflow with one key removed — no
    code change, no clone reverting anything, and no record written."""
    workflow = loaded_workflow()
    for stage in workflow["stages"]:
        stage.pop("revert_check", None)
    fake_root = mirror_harness(tmp_path / "no-declaration", workflow)

    code, _ = run(target, fake_root, {WRITING: added_coverage})
    assert code == 0
    assert not (run_dir_of(target) / ARTIFACT).exists()
    assert clone_calls == [()]


def test_moving_the_declaration_moves_the_check(target, tmp_path):
    """The strongest form of "no stage name and no prefix in the code": a
    workflow the coordinator has never seen, governing a different prefix on
    a different stage, and the check follows the declaration."""
    workflow = loaded_workflow()
    for stage in workflow["stages"]:
        stage.pop("may_not_create", None)
        stage.pop("revert_check", None)
        if stage["name"] == VALIDATING:
            stage["may_not_create"] = ["src/"]
            stage["revert_check"] = DECLARATION
    fake_root = mirror_harness(tmp_path / "moved", workflow)

    # The implementer's edit under tests/ is now ungoverned; the tester's
    # edit under src/ is the one decided, and nothing forced it.
    code, runner = run(target, fake_root, {WRITING: added_coverage,
                                           VALIDATING: nothing_governed})
    assert code == 0
    assert runner.calls == STAGE_NAMES
    record = record_of(target)
    assert record["permitted"] is False
    # The path decided is the one the moved declaration governs, and the
    # implementer's edit under the prefix the declaration left behind is not
    # decided at all — which is the whole of "the check follows the
    # declaration".
    assert record["paths"] == ["src/app.py"]
    events = (run_dir_of(target) / "events.log").read_text()
    reverting = [line for line in events.splitlines()
                 if "src/app.py" in line and "revert" in line]
    assert reverting
    assert any(VALIDATING in line and "src/" in line for line in reverting)


# --------------------------------------------------------------------------
# The check says it is about to re-run the suite (story-058)
#
# This check's silence only ever looked fine because it sits between the
# implementer's started and completed lines, so a reader already had a line on
# screen saying work was in progress - hidden by position rather than by
# design. The rule is general: a check that re-runs the configured test
# command announces itself first, from the same helper the clean-clone check
# announces from.
# --------------------------------------------------------------------------

#: The kind the coordinator appends before a check re-runs the configured test
#: command. An event kind is the coordinator's own vocabulary rather than a
#: name the workflow declares, so it is spelled here rather than read off the
#: definition. The workflow above declares both checks, so a run of it carries
#: an announcement for each and every assertion below says which stage it means.
ANNOUNCEMENT = "suite-rerun-started"


def history_of(target_root: Path) -> list[dict]:
    return json.loads(
        (run_dir_of(target_root) / "execution-history.json").read_text(
            encoding="utf-8"))


def announcements(target_root: Path, stage: str) -> list[dict]:
    return [e for e in history_of(target_root)
            if e["event"] == ANNOUNCEMENT and e.get("stage") == stage]


def test_the_check_announces_itself_before_its_result(target, harness_root):
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0

    history = history_of(target)
    indices = [index for index, entry in enumerate(history)
               if entry["event"] == ANNOUNCEMENT and entry["stage"] == WRITING]
    assert len(indices) == 1
    assert history[indices[0] + 1]["event"] == "revert-check-permitted"


def test_the_announcement_names_the_stage_and_the_declarations_artifact(
    target, harness_root,
):
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    entry = announcements(target, WRITING)[0]
    assert entry["stage"] == WRITING
    assert entry["artifacts"] == [ARTIFACT]


def test_the_announcement_names_the_reverted_paths_and_what_is_decided(
    target, harness_root,
):
    """What a reader learns while the suite runs a second time: which edits
    are being taken away, and that taking them away is how the check decides
    whether they were needed."""
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    message = announcements(target, WRITING)[0]["message"].lower()
    for path in record_of(target)["paths"]:
        assert path in message
    assert "decide" in message
    assert "needed" in message


def test_the_announcement_appears_in_both_renderings(target, harness_root):
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    entry = announcements(target, WRITING)[0]
    log = (run_dir_of(target) / "events.log").read_text(encoding="utf-8")
    assert f"[{entry['timestamp']}] {entry['message']}" in log.splitlines()


def test_the_announcement_is_on_disk_before_the_reverting_clone_is_built(
    target, harness_root, monkeypatch,
):
    """The ordering that makes it worth appending at all: written before the
    wait starts rather than beside the result once the wait is over. The run's
    other clone is the verifier's clean-clone check, so the calls are told
    apart by what they revert, the way `clone_calls` above tells them apart.
    """
    run_dir = run_dir_of(target)
    seen: list[tuple[tuple[str, ...], list[str]]] = []
    original = story_coordinator.run_clean_clone

    def spy(*args, **kwargs):
        revert = tuple(kwargs.get("revert", args[4] if len(args) > 4 else ()))
        history = json.loads(
            (run_dir / "execution-history.json").read_text(encoding="utf-8"))
        seen.append((revert, [entry["event"] for entry in history]))
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "run_clean_clone", spy)
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0

    reverting = [kinds for revert, kinds in seen if revert]
    assert len(reverting) == 1
    assert reverting[0][-1] == ANNOUNCEMENT
    assert "revert-check-permitted" not in reverting[0]


def test_the_path_where_no_baseline_was_captured_announces_nothing(
    target, tmp_path,
):
    """That path runs no suite, so it has nothing to announce: a reader told
    the suite is re-running would be waiting for work that never starts."""
    run_dir = tmp_path / "no-baseline-run"
    run_dir.mkdir()
    decided = story_coordinator.revert_check(
        run_dir, target, {"test_command": TEST_COMMAND}, ARTIFACT,
        ("tests/test_app.py",), None, stage_name=WRITING)

    assert decided.result.ran is False
    assert not (run_dir / "execution-history.json").exists()
    assert not (run_dir / "events.log").exists()


def test_the_same_call_with_a_baseline_does_announce(target, tmp_path):
    """The control for the assertion above: the identical call, one captured
    baseline added, and the check runs the suite and says so first."""
    baseline = baseline_of(target, tmp_path / "before")
    forced_repair(target)
    run_dir = tmp_path / "baselined-run"
    run_dir.mkdir()

    decided = story_coordinator.revert_check(
        run_dir, target, {"test_command": TEST_COMMAND}, ARTIFACT,
        ("tests/test_app.py",), baseline, stage_name=WRITING)

    assert decided.result.ran is True
    history = json.loads(
        (run_dir / "execution-history.json").read_text(encoding="utf-8"))
    assert [entry["event"] for entry in history] == [ANNOUNCEMENT]
    assert history[0]["stage"] == WRITING


def test_removing_the_declaration_silences_the_announcement_too(
    target, tmp_path,
):
    """The declaration is one switch, not two: with it gone the check neither
    announces nor decides, and no orchestration code changed. The verifier's
    own announcement is still there, which is what says this is looking at a
    stream that carries announcements at all."""
    workflow = loaded_workflow()
    for stage in workflow["stages"]:
        stage.pop("revert_check", None)
    fake_root = mirror_harness(tmp_path / "no-declaration", workflow)

    assert run(target, fake_root, {WRITING: added_coverage})[0] == 0
    assert announcements(target, WRITING) == []
    assert not any(entry["event"] == "revert-check-permitted"
                   for entry in history_of(target))
    assert announcements(target, VERIFYING)


def test_both_checks_announce_through_the_same_helper(target, harness_root):
    """One helper, one spelling of the kind — the way `append_event` is
    already the only writer of the history file. The emitter is found by
    reading the source for the kind the run above produced, so a second
    spelling of it anywhere would be reported here rather than drifting."""
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    assert announcements(target, WRITING) and announcements(target, VERIFYING)

    source = (ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    emitters = [
        node.name for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(inner, ast.Constant) and inner.value == ANNOUNCEMENT
                for inner in ast.walk(node))
    ]
    assert len(emitters) == 1, emitters
    helper = emitters[0]
    for check in (story_coordinator.clean_clone_check,
                  story_coordinator.revert_check):
        assert helper in executable_source(inspect.getsource(check)), check.__name__


def test_no_stage_name_no_prefix_and_no_artifact_name_is_written_in_the_code():
    """All three are read off the loaded workflow and the story. Docstrings
    and comments are stripped first: prose may name what code may not."""
    body = executable_source(
        (ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8"))
    assert PREFIX not in body
    assert ARTIFACT not in body
    for stage in WORKFLOW["stages"]:
        if stage["name"] == VERIFYING:
            continue      # the verifier routing branch predates this story
        assert stage["name"] not in body, stage["name"]


def test_the_governed_path_helper_names_no_stage_and_no_prefix():
    body = executable_source(inspect.getsource(story_coordinator.governed_edits))
    assert "modified" in body and "deleted" in body     # stripping kept code
    assert PREFIX not in body
    for stage in WORKFLOW["stages"]:
        assert stage["name"] not in body, stage["name"]


# --------------------------------------------------------------------------
# The story's granted prefixes are subtracted first
# --------------------------------------------------------------------------


def test_a_story_granting_the_prefix_is_not_subject_to_the_check_on_it(
    target, harness_root, clone_calls,
):
    append_to_story(target, (
        "\nstage_exceptions:\n"
        f"  - stage: {WRITING}\n"
        f"    create: {PREFIX}\n"
        "    reason: the deliverable is the suite\n"
    ))
    code, _ = run(target, harness_root, {WRITING: added_coverage})
    assert code == 0
    assert not (run_dir_of(target) / ARTIFACT).exists()
    assert clone_calls == [()]


def test_without_the_grant_the_same_record_is_checked_and_refused(target,
                                                                  harness_root):
    """The control for the grant: the story is the only difference.

    A grant exempts the path from the check, so no record is written and no
    clone reverting it is built. Without the grant the check runs, refuses,
    and the edit is undone — which is a different outcome from the grant's
    silence even though neither stops the run.
    """
    assert run(target, harness_root, {WRITING: added_coverage})[0] == 0
    record = record_of(target)
    assert record["permitted"] is False
    assert record["reverted"]["restored"] == ["tests/test_app.py"]


# --------------------------------------------------------------------------
# A check that cannot run refuses rather than permits
# --------------------------------------------------------------------------


def test_a_clone_that_cannot_be_built_escalates_naming_why(
    target, harness_root, monkeypatch,
):
    """A clone the builder cannot produce, so there is no suite result to
    read - and the check says so instead of letting the edits through.

    Repointed by story-019, not relaxed. The case this drove before was a
    governed path with no version at HEAD, and that is no longer a case at
    all: the baseline the check restores from records such a path's pre-stage
    state, so it is decided rather than refused. A clone that genuinely
    cannot be built is still refused, which is what this drives directly."""
    def unbuildable(*args, **kwargs):
        raise RuntimeError("the clone could not be built")

    monkeypatch.setattr(story_coordinator, "_build_clone", unbuildable)

    code, _ = run(target, harness_root, {WRITING: forced_repair})
    assert code == 2
    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert record["reason"]
    _, summary = evidence(target)
    assert "could not run" in summary
    assert "tests/test_app.py" in summary


def test_an_unresolvable_configured_interpreter_escalates_naming_why(
    target, harness_root,
):
    """The same treatment the clean-clone check gives it."""
    configure(target, verification_runner="nowhere/python")
    code, _ = run(target, harness_root, {WRITING: forced_repair})
    assert code == 2
    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert "nowhere/python" in record["reason"]
    _, summary = evidence(target)
    assert "could not run" in summary


# --------------------------------------------------------------------------
# The granularity the check decides at, and what it does not catch
# --------------------------------------------------------------------------


def test_a_set_containing_one_forced_repair_is_permitted_in_full(
    target, harness_root,
):
    """The limit, constructed: two governed files, one forced and one not.
    The check reverts both at once, the suite fails, and the whole set is
    permitted - the addition included."""
    code, _ = run(target, harness_root, {WRITING: mixed_set})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py", "tests/test_extra.py"]


def test_the_addition_in_that_set_was_not_forced_by_anything(target, tmp_path):
    """What the test above would look like if the check discriminated per
    file: reverting the addition alone leaves the suite green. The check
    reports the set it reverted rather than claiming it decided per file."""
    baseline = baseline_of(target, tmp_path / "before")
    mixed_set(target)
    alone = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "extra-only",
        revert=["tests/test_extra.py"], baseline=baseline)
    assert alone.ran is True
    assert alone.exit_code == 0


def test_a_single_file_mixing_a_repair_and_an_addition_is_permitted(
    target, harness_root,
):
    """The case the granularity misses outright: one file, both acts. The
    record names the file it reverted and claims nothing about its hunks."""
    code, _ = run(target, harness_root, {WRITING: mixed_file})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]
    assert set(record) <= {"ran", "paths", "command", "runner", "scope",
                           "clone_path", "exit_code", "output_tail", "output_path",
                           "permitted", "baseline", "reason", "nomination"}


def test_the_addition_inside_that_file_needed_no_change_to_pass(tmp_path):
    """The control for the case above: the appended function passes against
    the module as HEAD has it, so nothing about the implementer's change
    forced it into the file."""
    scratch = tmp_path / "independent"
    write(scratch / "conftest.py", ROOT_CONFTEST)
    write(scratch / "src" / "app.py", APP_AT_HEAD)
    write(scratch / "tests" / "test_app.py", TEST_APP_AT_HEAD + INDEPENDENT_COVERAGE)
    assert suite_in(scratch) == 0


def test_the_granularity_limit_is_stated_where_the_check_is_defined():
    """In the module docstring and in the schema's own description, so a
    reader of either the code or the artifact learns it without inferring
    it."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for text in (story_coordinator.__doc__, schema["description"]):
        lowered = text.lower()
        assert any(phrase in lowered for phrase in
                   ("whole set", "every governed path at once", "in a single run",
                    "at once")), lowered
        assert "in full" in lowered
        assert "not caught" in lowered
        assert "mixing" in lowered


# --------------------------------------------------------------------------
# The schema, the inventory, and the record as evidence
# --------------------------------------------------------------------------


def test_the_schema_exists_and_is_listed_in_the_manifest():
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert SCHEMA_PATH.is_file()
    assert SCHEMA_STEM in manifest["schemas"]
    assert schema_validator.load_schema(SCHEMA_STEM)["title"] == SCHEMA_STEM


def test_the_schema_appears_in_no_stages_schemas_map():
    """No agent is asked to satisfy it: the coordinator writes it. The
    control is the record that *is* in a stage's map, so a lookup that had
    stopped seeing anything would fail here."""
    mapped = {name for stage in WORKFLOW["stages"]
              for name in stage.get("schemas", {}).values()}
    assert SCHEMA_STEM not in mapped
    assert "changed-files" in mapped


def test_the_written_record_satisfies_the_schema(target, harness_root):
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    record = record_of(target)
    schema = schema_validator.load_schema(SCHEMA_STEM)
    assert schema_validator.validate(record, schema) == []
    # The control: the same validator against the same schema rejects a
    # record missing what the check must always report.
    incomplete = {key: value for key, value in record.items() if key != "paths"}
    assert schema_validator.validate(incomplete, schema) != []


def test_no_module_in_orchestration_names_the_record(target, harness_root):
    """The artifact's name is the workflow's, so no module may spell it.

    The coordinator does now read the record back — a stage that judges the
    run is given it — but it reaches it through the declaring stage's own
    declaration rather than through a name written in the source, which is
    what this scan is about. The control is clean-clone-result.json, which
    orchestration *does* name, so a scan that had stopped matching anything
    would fail here.
    """
    named = {module.name: module.read_text(encoding="utf-8")
             for module in sorted(ORCHESTRATION.glob("*.py"))}
    assert not [name for name, text in named.items() if ARTIFACT in text]
    assert [name for name, text in named.items() if "clean-clone-result.json" in text]


def test_the_record_is_injected_only_where_the_coordinator_passes_it(target,
                                                                     harness_root):
    """The assembler reads no run-directory file for this one.

    Every other record in the context is read off the run directory by a fixed
    name; this one cannot be, because the file it lives in is named by the
    workflow rather than by the harness. So the assembler takes it as an
    argument, and a call that does not pass it renders nothing — which is what
    the first half asserts, beside the same call passing one, which renders it.
    The control beneath is the clean-clone record, read off the run directory
    by its fixed name, so "not read here" is this record and not the reader
    having stopped working.
    """
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    record = (run_dir_of(target) / ARTIFACT).read_text(encoding="utf-8")

    def context_with(**extra) -> dict:
        return context_assembler.build_context(
            story_text=STORY,
            story={"acceptance_criteria": []},
            run_dir=run_dir_of(target),
            target_root=target,
            harness_root=REPO_ROOT,
            config=harness_config.load_config(target),
            rules=harness_config.load_rules(REPO_ROOT),
            workflow=WORKFLOW,
            retry_count=0,
            **extra,
        )

    assert context_with()["revert_check_result"] is None
    assert context_with(revert_check_result=record)["revert_check_result"] == record
    # The control: the clean-clone record is read off the run directory.
    assert context_with()["clean_clone_result"]


def test_a_run_hands_the_record_to_the_stage_that_judges_it(target, harness_root):
    """Observed on the prompt the run actually rendered, not on the assembler.

    A reverted edit is a fact about the run the judging stage has to be able to
    see, since the tree it judges no longer holds the edit the stage's own
    changed-files record still names. Its control is the test beneath, which is
    the same reading of the same prompt from a run where no path was governed
    at all.
    """
    _, runner = run(target, harness_root, {WRITING: added_coverage})
    assert record_of(target)["reverted"]["restored"] == ["tests/test_app.py"]

    judged = runner.prompts[VERIFYING]
    assert "restored" in judged
    assert "tests/test_app.py" in judged


def test_a_run_with_nothing_governed_hands_that_stage_no_record(target,
                                                                harness_root):
    """The control: no check ran, so there is no record and nothing rendered."""
    _, runner = run(target, harness_root, {WRITING: nothing_governed})
    assert not (run_dir_of(target) / ARTIFACT).exists()
    assert "restored" not in runner.prompts[VERIFYING]


# --------------------------------------------------------------------------
# The clean-clone check is unaffected
# --------------------------------------------------------------------------


def test_the_clone_builders_revert_parameter_defaults_to_reverting_nothing():
    for function in (story_coordinator._build_clone, story_coordinator.run_clean_clone):
        default = inspect.signature(function).parameters["revert"].default
        assert tuple(default) == ()


def test_a_clone_built_with_the_default_carries_the_edit_and_one_reverted_does_not(
    target, tmp_path,
):
    """The behavioral half: same builder, same tree, one parameter apart."""
    baseline = baseline_of(target, tmp_path / "before")
    forced_repair(target)
    story_coordinator._build_clone(target, tmp_path / "default")
    story_coordinator._build_clone(target, tmp_path / "reverted",
                                   revert=["tests/test_app.py"],
                                   baseline=baseline)

    assert "salute" in git(tmp_path / "default", "show",
                           "HEAD:tests/test_app.py").stdout
    assert "salute" not in git(tmp_path / "reverted", "show",
                               "HEAD:tests/test_app.py").stdout
    # Everything outside the reverted path is present in both.
    for clone in ("default", "reverted"):
        assert "salute" in git(tmp_path / clone, "show", "HEAD:src/app.py").stdout


def test_the_clean_clone_check_still_writes_its_record_and_event_and_routes(
    target, harness_root,
):
    verifier = next(s for s in WORKFLOW["stages"] if s["name"] == VERIFYING)
    assert run(target, harness_root, {WRITING: forced_repair})[0] == 0
    run_dir = run_dir_of(target)
    clean = json.loads((run_dir / verifier["clean_clone"]["result"]).read_text())
    events = (run_dir / "events.log").read_text()

    assert clean["ran"] is True and clean["exit_code"] == 0
    assert "clean-clone" in events
    assert state_of(target)["status"] == "completed"


# --------------------------------------------------------------------------
# The planner guidance reaches a rendered prompt
# --------------------------------------------------------------------------


def rendered_planner_prompt() -> str:
    context = context_assembler.schema_context(REPO_ROOT)
    context.update(context_assembler.workflow_context(
        loaded_workflow(), harness_config.load_rules(REPO_ROOT)))
    return context_assembler.render(
        context_assembler.load_template(REPO_ROOT, "planner.md"), context)


def test_the_rendered_planner_prompt_tells_a_plan_not_to_tighten_a_restriction():
    """Rendered through context_assembler rather than read off the template,
    because what a planner receives is the rendered prompt."""
    rendered = rendered_planner_prompt()
    paragraphs = [p for p in re.split(r"\n\s*\n", rendered) if "restate" in p.lower()]
    assert len(paragraphs) == 1
    guidance = paragraphs[0].lower()
    assert "restriction" in guidance
    assert "workflow" in guidance
    assert context_assembler.PLACEHOLDER.search(rendered) is None


def test_the_planner_template_still_names_no_stage_and_no_restricted_prefix():
    """The guidance is general, so the story-009 property it could have
    broken still holds."""
    template = context_assembler.PLACEHOLDER.sub(
        "", context_assembler.load_template(REPO_ROOT, "planner.md"))
    assert PREFIX not in template
    for stage in WORKFLOW["stages"]:
        assert not re.search(rf"\b{stage['name']}\b", template), stage["name"]


# --------------------------------------------------------------------------
# The nomination: one test asked before the suite (story-077)
#
# The check establishes a *failure*, and one failing test proves a failing
# suite while no number of passing tests proves a passing suite. That
# asymmetry is what lets a subset stand in for the whole here, so a stage may
# nominate the test that fails without its change and have the check decide on
# that test alone.
#
# Nothing below reads the nomination's worth off the code that computes it.
# Each case builds the tree, configures the selection command, runs the
# coordinator, and reads what the check recorded: which exit codes the two
# selector runs produced, which command ran in the clone that reverted
# something, and what the verdict was. Every fall-through is paired with the
# short-circuit it did not take, so "the whole suite decided this" is asserted
# against a run in which the whole suite demonstrably did not.
#
# The standing rule that the harness names no framework and parses no runner
# output is not restated here: test_no_target_stack_in_harness_source.py scans
# the whole coordinator source, so the code added for this reaches it already.
# --------------------------------------------------------------------------

#: The substitution point, spelled here rather than imported from the code
#: that looks for it: it is a term of the target's configuration format, so a
#: test that took it from the harness could never report the harness changing
#: it out from under every config already written. The schema is held to the
#: same spelling below.
SUBSTITUTION = "{test}"

#: The key the target sets to enable the short-circuit.
SELECTION_KEY = "test_selection_command"

#: The target's own selector syntax, which the harness neither parses nor
#: understands. Assembled as a string rather than through `shlex.join`, so the
#: substitution point stays the bare literal a developer would write.
TEST_SELECTION_COMMAND = (
    f"{shlex.quote(sys.executable)} -m pytest {SUBSTITUTION} -q "
    f"-p no:cacheprovider"
)

#: The test the forced repair repairs. It passes on the tree the implementer
#: leaves and cannot even be collected once `test_app.py` is reverted, because
#: the reverted file imports a name the renamed module no longer exports.
NOMINATION_REPAIRED = f"{PREFIX}test_app.py::test_greet"

#: A test in the file no edit touches. It passes in both trees, which is the
#: nomination that shows nothing and must fall through.
NOMINATION_UNTOUCHED = f"{PREFIX}test_extra.py::test_arithmetic"

#: A test no version of any file defines, so the selector naming it fails on
#: the tree the stage left exactly as a broken selector does.
NOMINATION_ABSENT = f"{PREFIX}test_app.py::test_no_such_test"

#: A nomination whose substituted command cannot be split into an argument
#: list at all: the quotation it opens is never closed.
NOMINATION_UNSPLITTABLE = f'{PREFIX}test_app.py::test_"greet'


def with_nomination(edit, test: str):
    """The same working-tree edit, with its record naming a nominated test."""
    def edited(root: Path) -> dict:
        return {**edit(root), "test_that_fails_without_this_change": test}
    return edited


def enable_selection(target_root: Path, command: str = TEST_SELECTION_COMMAND) -> None:
    """Configure the selection command, and check it survived the parser.

    The value carries a brace pair, which is exactly the shape a configuration
    reader is most likely to mangle. If it arrived mangled every assertion
    below about a fall-through would still pass — for the wrong reason — so it
    is read back through the harness's own loader here.
    """
    configure(target_root, **{SELECTION_KEY: command})
    assert harness_config.load_config(target_root)[SELECTION_KEY] == command


@pytest.fixture
def clone_runs(monkeypatch):
    """Every call into the shared clone runner, as (command, reverted paths).

    `clone_calls` above records what each call reverted and not what it ran,
    which was all there was to record while every call ran the configured test
    command. The nomination runs a different command through the same builder,
    so *what* a call ran is what several assertions below are about: "the
    configured test command never ran" is a claim about the commands, and a
    fixture that only saw the reverts could not tell it from "no clone was
    built".
    """
    calls: list[tuple[str, tuple[str, ...]]] = []
    original = story_coordinator.run_clean_clone

    def spy(*args, **kwargs):
        command = kwargs.get("command_to_run", args[1] if len(args) > 1 else "")
        revert = kwargs.get("revert", args[4] if len(args) > 4 else ())
        calls.append((command, tuple(revert)))
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "run_clean_clone", spy)
    return calls


def suite_runs(calls) -> list[tuple[str, tuple[str, ...]]]:
    """The calls that ran the target's configured test command."""
    return [call for call in calls if call[0] == TEST_COMMAND]


def selector_runs(calls) -> list[tuple[str, tuple[str, ...]]]:
    """The calls that ran something other than the configured test command."""
    return [call for call in calls if call[0] != TEST_COMMAND]


def nomination_of(target_root: Path) -> dict:
    return record_of(target_root)["nomination"]


def suite_output(target_root: Path) -> Path:
    return run_dir_of(target_root) / story_coordinator.suite_output_file(ARTIFACT)


# --- the permission the nomination can establish --------------------------


def test_a_nomination_that_passes_applied_and_fails_reverted_permits_the_edits(
    target, harness_root, clone_runs,
):
    """The short-circuit, read off what the check recorded rather than timed.

    Both selector runs are recorded, and both are what the permission is made
    of: zero on the tree the implementer left, non-zero with the governed path
    reverted.
    """
    enable_selection(target)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair, NOMINATION_REPAIRED)})
    assert code == 0
    assert state_of(target)["status"] == "completed"

    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]

    nomination = record["nomination"]
    assert nomination["short_circuited"] is True
    assert nomination["test"] == NOMINATION_REPAIRED
    assert nomination["applied_exit_code"] == 0
    assert nomination["reverted_exit_code"] != 0
    assert "fell_through_because" not in nomination


def test_the_permitting_nomination_never_ran_the_configured_test_command(
    target, harness_root, clone_runs,
):
    """What the short-circuit is *for*, asserted against the commands that ran.

    The run's other clone is the verifier's clean-clone check, which reverts
    nothing and runs the configured command — so the absence claimed here is
    "the configured command never ran with anything reverted" and the control
    for it is the call that ran it with nothing reverted. A spy that had
    stopped seeing commands would fail that control rather than pass this.
    """
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_REPAIRED)})[0] == 0

    reverting = [call for call in clone_runs if call[1]]
    assert [shlex.split(command) for command, _ in reverting] == \
        [shlex.split(nomination_of(target)["command"])] * len(reverting)
    assert TEST_COMMAND not in [command for command, _ in reverting]
    assert suite_runs(clone_runs) == [(TEST_COMMAND, ())]


def test_the_command_the_nomination_ran_is_the_configured_one_substituted(
    target, harness_root,
):
    """The recorded command, compared as an argument list.

    The configured command's own arguments, with the substitution point and
    nothing else replaced by the nominated test. Compared after splitting
    rather than as text, because how the harness re-joins an argument list is
    not what the target configured.
    """
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_REPAIRED)})[0] == 0
    assert shlex.split(nomination_of(target)["command"]) == [
        sys.executable, "-m", "pytest", NOMINATION_REPAIRED, "-q",
        "-p", "no:cacheprovider"]


def test_a_short_circuited_check_records_ran_false_and_writes_no_suite_output(
    target, harness_root,
):
    """`ran` still means the configured test command ran in the reverted clone.

    So a check the nomination decided records it false while carrying
    `permitted` true — the two stopped being the same question, which is what
    the escalation predicate had to change for. The output file is the
    control's other half: the fall-through case below writes one, so its
    absence here is the suite not having run rather than the path having moved.
    """
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_REPAIRED)})[0] == 0
    record = record_of(target)
    assert record["ran"] is False
    assert record["permitted"] is True
    assert "exit_code" not in record
    assert "output_tail" not in record
    assert not suite_output(target).exists()


def test_the_selector_runs_announce_a_wait_of_seconds_not_a_suite_run(
    target, harness_root,
):
    """The announcement takes the cost it actually takes.

    Paired with the no-nomination run below, whose announcement claims a suite
    run's wait: the same helper, the same stage, and the difference is what
    the check is about to do.
    """
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_REPAIRED)})[0] == 0
    entries = announcements(target, WRITING)
    assert len(entries) == 1
    message = entries[0]["message"]
    assert NOMINATION_REPAIRED in message
    assert "seconds" in message
    assert entries[0]["artifacts"] == [ARTIFACT]


# --- every other outcome falls through to the whole suite ------------------


def test_a_record_carrying_no_nomination_reaches_todays_verdict_by_todays_path(
    target, harness_root, clone_runs,
):
    """Nothing new is required of a record that names no test.

    The same edits as the permitted case above, minus the nomination: the
    whole suite runs in the reverted clone, decides, and writes its output —
    and the announcement says a suite run's wait rather than seconds.
    """
    enable_selection(target)
    code, _ = run(target, harness_root, {WRITING: forced_repair})
    assert code == 0

    record = record_of(target)
    assert record["ran"] is True
    assert record["exit_code"] != 0
    assert record["permitted"] is True
    assert suite_output(target).exists()

    assert selector_runs(clone_runs) == []
    assert (TEST_COMMAND, ("tests/test_app.py",)) in clone_runs
    assert record["nomination"]["short_circuited"] is False
    assert record["nomination"]["fell_through_because"]
    assert "test" not in record["nomination"]
    assert "seconds" not in announcements(target, WRITING)[0]["message"]


def test_a_target_configuring_no_selection_command_runs_exactly_as_today(
    target, harness_root, clone_runs,
):
    """A nomination the target gave the check no way to run.

    The shape a target with no `test_command` already uses: reported as a check
    that did not short-circuit, never as a permission. The fixture's own
    configuration is read back, so this is a target that really omits the key
    rather than one whose value went unread.
    """
    assert SELECTION_KEY not in harness_config.load_config(target)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair, NOMINATION_REPAIRED)})
    assert code == 0

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert selector_runs(clone_runs) == []
    nomination = record["nomination"]
    assert nomination["short_circuited"] is False
    assert nomination["test"] == NOMINATION_REPAIRED
    assert SELECTION_KEY in nomination["fell_through_because"]
    assert "command" not in nomination


def test_a_nomination_that_passes_reverted_falls_through_and_the_suite_decides(
    target, harness_root, clone_runs,
):
    """A test the revert cannot touch shows nothing, so the suite decides.

    Both selector runs happened and both passed, and the verdict is the whole
    suite's: it failed with the governed path reverted, which is the same
    answer this record got before nominations existed.
    """
    enable_selection(target)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair, NOMINATION_UNTOUCHED)})
    assert code == 0

    record = record_of(target)
    nomination = record["nomination"]
    assert nomination["short_circuited"] is False
    assert nomination["applied_exit_code"] == 0
    assert nomination["reverted_exit_code"] == 0
    assert nomination["fell_through_because"]
    assert record["ran"] is True
    assert record["exit_code"] != 0
    assert record["permitted"] is True
    assert (TEST_COMMAND, ("tests/test_app.py",)) in clone_runs


def test_a_refusal_is_still_a_refusal_and_no_nomination_makes_it_a_permission(
    target, harness_root,
):
    """The shape of a run whose reverted suite passes, with a nomination that
    passes in both trees attached to it.

    This is the case the whole mechanism has to not break: added coverage,
    which reverting costs nothing, and a nomination that establishes nothing.
    The suite passes with the edits reverted and the edits are undone, exactly
    as they are with no nomination at all — the disposition applies to the
    verdict however the verdict was reached.
    """
    enable_selection(target)
    code, runner = run(target, harness_root,
                       {WRITING: with_nomination(added_coverage,
                                                 NOMINATION_UNTOUCHED)})
    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert runner.calls == STAGE_NAMES

    record = record_of(target)
    assert record["ran"] is True
    assert record["exit_code"] == 0
    assert record["permitted"] is False
    assert record["nomination"]["short_circuited"] is False
    assert record["reverted"]["restored"] == ["tests/test_app.py"]


def test_a_nomination_that_fails_on_the_tree_the_stage_left_falls_through(
    target, harness_root, clone_runs,
):
    """A selector that fails to collect exits non-zero exactly as a failing
    test does, and the harness may not read *which* non-zero. So the applied
    run is what tells them apart: a nomination that cannot pass on the tree the
    stage left discriminates nothing, and it falls through rather than being
    read as half a permission.

    The reverted run is never reached — one selector run, not two — which is
    what makes a bad nomination cost one extra run over today's runtime.
    """
    enable_selection(target)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair, NOMINATION_ABSENT)})
    assert code == 0

    nomination = nomination_of(target)
    assert nomination["short_circuited"] is False
    assert nomination["applied_exit_code"] != 0
    assert "reverted_exit_code" not in nomination
    assert nomination["fell_through_because"]
    assert len(selector_runs(clone_runs)) == 1
    assert selector_runs(clone_runs)[0][1] == ()

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True


def test_a_selection_command_with_no_substitution_point_falls_through_naming_it(
    target, harness_root, clone_runs,
):
    """A configured command the nomination cannot be substituted into.

    Nothing is run at all — the selector count is zero against a run that
    reaches the suite — and the recorded reason names the substitution point
    that was missing rather than reporting an unexplained fall-through.
    """
    enable_selection(target, f"{shlex.quote(sys.executable)} -m pytest -q")
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair, NOMINATION_REPAIRED)})
    assert code == 0

    nomination = nomination_of(target)
    assert nomination["short_circuited"] is False
    assert SUBSTITUTION in nomination["fell_through_because"]
    assert "command" not in nomination
    assert selector_runs(clone_runs) == []
    assert record_of(target)["ran"] is True
    assert record_of(target)["permitted"] is True


def test_a_substituted_command_that_cannot_be_run_at_all_falls_through(
    target, harness_root, clone_runs,
):
    """A nomination that opens a quotation it never closes.

    The harness substitutes a plain string, because quoting it would be the
    harness deciding something about a selector syntax that belongs to the
    target. The cost is that a substitution can produce a command no argument
    list can be made of, and that falls through with the reason naming what
    stopped it rather than raising out of the check.
    """
    enable_selection(target)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair,
                                            NOMINATION_UNSPLITTABLE)})
    assert code == 0

    nomination = nomination_of(target)
    assert nomination["short_circuited"] is False
    assert nomination["test"] == NOMINATION_UNSPLITTABLE
    assert nomination["command"] == \
        TEST_SELECTION_COMMAND.replace(SUBSTITUTION, NOMINATION_UNSPLITTABLE)
    assert "could not be run at all" in nomination["fell_through_because"]
    assert selector_runs(clone_runs) == []
    assert record_of(target)["ran"] is True
    assert record_of(target)["permitted"] is True


def test_the_substituted_command_really_cannot_be_split(tmp_path):
    """The premise under the case above: without this, that fall-through
    could be happening for some reason other than the one it names."""
    with pytest.raises(ValueError):
        shlex.split(TEST_SELECTION_COMMAND.replace(SUBSTITUTION,
                                                   NOMINATION_UNSPLITTABLE))
    # And the control: every other nomination in this module splits.
    for nomination in (NOMINATION_REPAIRED, NOMINATION_UNTOUCHED,
                       NOMINATION_ABSENT):
        assert shlex.split(
            TEST_SELECTION_COMMAND.replace(SUBSTITUTION, nomination))[3] == \
            nomination


# --- a check that reached no verdict is not a check that decided -----------


def test_a_check_that_reached_no_verdict_escalates_even_with_a_nomination(
    target, harness_root, monkeypatch,
):
    """The other side of the escalation predicate.

    The permitted case above is a check that reached a verdict without running
    the suite, and it completes. This is a check that reached no verdict, with
    a nomination attached that would have permitted the edits had it been
    runnable — and it escalates, because a nomination that could not be run
    decided nothing either.
    """
    def unbuildable(*args, **kwargs):
        raise RuntimeError("the clone could not be built")

    enable_selection(target)
    monkeypatch.setattr(story_coordinator, "_build_clone", unbuildable)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(forced_repair, NOMINATION_REPAIRED)})
    assert code == 2

    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert record["reason"]
    assert record["nomination"]["short_circuited"] is False
    assert record["nomination"]["fell_through_because"]


# --- what the record and the schemas say -----------------------------------


def test_the_short_circuited_record_satisfies_the_schema(target, harness_root):
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_REPAIRED)})[0] == 0
    record = record_of(target)
    schema = schema_validator.load_schema(SCHEMA_STEM)
    assert schema_validator.validate(record, schema) == []
    # The control: the same validator rejects the same record with the
    # nomination's own required field taken out of it.
    broken = json.loads(json.dumps(record))
    del broken["nomination"]["short_circuited"]
    assert schema_validator.validate(broken, schema) != []


def test_a_fallen_through_record_satisfies_the_schema(target, harness_root):
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_UNTOUCHED)})[0] == 0
    record = record_of(target)
    assert schema_validator.validate(
        record, schema_validator.load_schema(SCHEMA_STEM)) == []
    assert set(record) <= {"ran", "paths", "command", "runner", "scope", "clone_path",
                           "exit_code", "output_tail", "output_path",
                           "permitted", "baseline", "reason", "nomination"}


def test_the_configuration_schema_declares_the_selection_command_key():
    """The declaration is also the pre-flight check since story-043, so an
    undeclared key would refuse every run under a config that sets it — which
    is what every run above would then be reporting instead of what it means
    to report."""
    assert SELECTION_KEY in harness_config.declared_config_keys()
    declaration = schema_validator.load_schema("harness-config")[
        "properties"][SELECTION_KEY]
    assert SUBSTITUTION in declaration["description"]


def test_a_config_carrying_the_selection_command_is_accepted_at_pre_flight(
    target,
):
    """Accepted rather than refused, with the control that the same check
    refuses a key the schema does not declare."""
    enable_selection(target)
    config = harness_config.load_config(target)
    assert harness_config.undeclared_config_problems(config) == []
    problems = harness_config.undeclared_config_problems(
        {**config, "test_selection_kommand": "a mistyping of the above"})
    assert len(problems) == 1
    assert "test_selection_kommand" in problems[0]


def test_the_nomination_field_leaves_both_writing_stages_records_valid(
    target, harness_root,
):
    """The field is optional, so both records validate with it and without it.

    Asked of the records two stages of a real run actually wrote — the
    implementer's carrying a nomination and the tester's carrying none — and
    then of each with the field's presence flipped, so neither direction rests
    on which stage happened to fill it in.
    """
    enable_selection(target)
    assert run(target, harness_root,
               {WRITING: with_nomination(forced_repair,
                                         NOMINATION_REPAIRED)})[0] == 0
    schema = schema_validator.load_schema("changed-files")
    run_dir = run_dir_of(target)

    written = record_of(target, conftest.CHANGED_FILES)
    tester = record_of(target, conftest.TESTER_CHANGED_FILES)
    assert written["test_that_fails_without_this_change"] == NOMINATION_REPAIRED
    assert "test_that_fails_without_this_change" not in tester

    for record in (written, tester):
        without = {key: value for key, value in record.items()
                   if key != "test_that_fails_without_this_change"}
        assert schema_validator.validate(record, schema) == []
        assert schema_validator.validate(without, schema) == []
        assert schema_validator.validate(
            {**without, "test_that_fails_without_this_change":
                NOMINATION_UNTOUCHED}, schema) == []
    # The control: optional is not "anything goes" — the field is declared a
    # string, and the same validator rejects a record that carries a list.
    assert schema_validator.validate(
        {**written, "test_that_fails_without_this_change": [NOMINATION_REPAIRED]},
        schema) != []
    assert (run_dir / conftest.CHANGED_FILES).is_file()


def test_the_residual_false_permit_risk_is_recorded_beside_the_check():
    """A reader meets this check in two places — the function that decides and
    the schema of what it records — and both say that a test failing in
    isolation and passing inside the full suite is a false permit the applied
    run narrows and does not close."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for text in (inspect.getdoc(story_coordinator.run_nomination),
                 schema["description"]):
        lowered = text.lower()
        assert "isolation" in lowered
        assert "false permit" in lowered
        assert "narrow" in lowered
        assert "does not close" in lowered


def test_the_new_fields_description_distinguishes_it_from_the_plans_declaration():
    """A reader of the field is told what it is not: story-068's
    reverting_breaks_the_suite is plan-time prose a human reviewer weighs,
    while this one is named after the edit exists and is decided by running
    it. The control is the schema that carries the plan, which really does
    declare the other field — so this is two live declarations being compared
    rather than one string being searched for a phrase."""
    described = schema_validator.load_schema("changed-files")["properties"][
        "test_that_fails_without_this_change"]["description"]
    assert "reverting_breaks_the_suite" in described
    assert "plan" in described.lower()
    assert "reverting_breaks_the_suite" in json.dumps(
        schema_validator.load_schema("story"))


def test_the_granularity_the_check_decides_at_is_unchanged_by_the_nomination(
    target, harness_root,
):
    """A permission the nomination established covers the same set the whole
    suite's would have: every governed path reverted in one run, one verdict,
    and the record naming what it reverted. The set here is the mixed one, one
    of whose two files nothing forced."""
    enable_selection(target)
    code, _ = run(target, harness_root,
                  {WRITING: with_nomination(mixed_set, NOMINATION_REPAIRED)})
    assert code == 0
    record = record_of(target)
    assert record["nomination"]["short_circuited"] is True
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py", "tests/test_extra.py"]


# --------------------------------------------------------------------------
# What this story left alone
# --------------------------------------------------------------------------


def test_this_story_edited_no_story_artifact(tmp_path):
    """The control is the file the story did edit: if the diff resolution
    had stopped seeing anything, the second assertion would fail too.

    Both are asked of a story this test builds. Asked of this repository's own
    commit graph the pair re-stated a frozen past fact, and its evidence moved
    whenever something was committed, renamed, squashed or rebased. The claim
    and the control are unchanged; the history under them is constructed.
    """
    root = conftest.constructed_story(
        tmp_path, respected=[".harness/stories/"],
        violated=["orchestration/story_coordinator.py"])
    assert conftest.constructed_story_diff(root, [".harness/stories/"]) == ""
    assert conftest.constructed_story_diff(
        root, ["orchestration/story_coordinator.py"]) != ""


def test_no_test_in_the_suite_states_the_prose_rule_this_story_supersedes():
    """The superseded rule is the claim that an implementer's record lists
    *nothing* under the governed prefix - stricter than may_not_create, which
    governs creation alone.

    This is a source scan for that claim being stated in the suite, and it is
    narrow in exactly the way test_baseline_honesty.py is narrow: it catches
    the phrasings the story artifacts used, not every way the claim could be
    written. The control below constructs one and shows the scan reports it.

    This file is excluded from the scan, and only this file: it is where the
    control sentence is written, so a scan including it would report the
    control as an offender and could never pass.
    """
    assert states_the_prose_rule(SUPERSEDED_RULE_SAMPLE)
    offenders = {path.name for path in sorted(TESTS_DIR.glob("test_*.py"))
                 if path.name != Path(__file__).name
                 and states_the_prose_rule(path.read_text(encoding="utf-8"))}
    assert offenders == set()


#: A sentence of the shape the story artifacts used, as the negative control
#: for the scan above.
SUPERSEDED_RULE_SAMPLE = (
    'assert record["modified"] == []  # the implementer\'s changed-files\n'
    "# record lists nothing under tests/\n"
)

_PROSE_RULE = re.compile(
    r"(lists nothing under|touches nothing under|leaves .{0,40}untouched|"
    r"must not touch|does not touch)[^\n]{0,60}tests/",
    re.IGNORECASE,
)


def states_the_prose_rule(text: str) -> bool:
    return _PROSE_RULE.search(text) is not None
