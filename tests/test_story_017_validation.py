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
import inspect
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import STORY, story_diff

import context_assembler
import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
STORIES_DIR = REPO_ROOT / ".harness" / "stories"
TESTS_DIR = REPO_ROOT / "tests"

WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")
IMPLEMENTER_STAGE = next(s for s in WORKFLOW["stages"] if s["name"] == "implementer")
#: The artifact name and the governed prefix are read off the workflow, never
#: spelled here, for the same reason the coordinator may not spell them.
ARTIFACT = IMPLEMENTER_STAGE.get("revert_check")
PREFIX = IMPLEMENTER_STAGE["may_not_create"][0]

SCHEMA_STEM = "revert-check-result"
SCHEMA_PATH = REPO_ROOT / "schemas" / f"{SCHEMA_STEM}.schema.json"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest", "tests", "-q",
                           "-p", "no:cacheprovider"])

CONFIG = f"""\
project: suite-target
workflow: story-workflow
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: {TEST_COMMAND}
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
def harness_root() -> Path:
    return REPO_ROOT


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


def ghost_path(root: Path) -> dict:
    """A governed path with no version at HEAD, so the revert cannot happen."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    return {"modified": ["src/app.py", "tests/test_ghost.py"], "created": [],
            "deleted": []}


class Runner:
    """A fake agent runner: each stage writes its artifacts, and the stage
    holding an edit also makes that edit in the target's working tree."""

    def __init__(self, target_root: Path, edits: dict, story_id: str = "story-001"):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.edits = edits
        self.records: dict[str, dict] = {}
        self.calls: list[str] = []

    def _record(self, stage: str) -> dict:
        edit = self.edits.get(stage)
        record = edit(self.target_root) if edit else {"modified": [], "created": [],
                                                      "deleted": []}
        self.records[stage] = record
        return record

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        if stage == "implementer":
            write_json(self.run_dir / "changed-files.json", self._record(stage))
            write(self.run_dir / "implementation-summary.md", "Did it.\n")
        elif stage == "tester":
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 2,
                "tests_passed": 2, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json", self._record(stage))
        elif stage == "verifier":
            write_json(self.run_dir / "verification-result.json", PASS)
        elif stage == "documenter":
            write(self.run_dir / "documentation-report.md", "Nothing.\n")
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


def mirror_harness(tmp_path: Path, workflow: dict) -> Path:
    """A harness root identical to the real one but for its workflow file."""
    fake = tmp_path / "harness"
    (fake / "workflows").mkdir(parents=True)
    for shared in ("prompts", "schemas", "rules"):
        (fake / shared).symlink_to(REPO_ROOT / shared)
    write_json(fake / "workflows" / "story-workflow.json", workflow)
    return fake


def loaded_workflow() -> dict:
    return harness_config.load_workflow(REPO_ROOT, "story-workflow")


def append_to_story(target_root: Path, text: str) -> None:
    path = target_root / ".harness" / "stories" / "story-001.yaml"
    path.write_text(path.read_text() + text, encoding="utf-8")


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

    def spy(target_root, clone, *, revert=()):
        built.append(tuple(revert))
        return original(target_root, clone, revert=revert)

    monkeypatch.setattr(story_coordinator, "_build_clone", spy)
    return built


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
    forced_repair(target)

    reverted = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "with-revert",
        revert=["tests/test_app.py"])
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
# A forced edit is permitted; free coverage is escalated
# --------------------------------------------------------------------------


def test_a_forced_edit_under_the_governed_prefix_is_permitted(target, harness_root):
    code, runner = run(target, harness_root, {"implementer": forced_repair})
    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert runner.calls == ["implementer", "tester", "verifier", "documenter"]

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["exit_code"] != 0
    assert record["paths"] == ["tests/test_app.py"]


def test_the_run_records_why_the_forced_edit_was_permitted(target, harness_root):
    """A reader must be able to see why an implementer was allowed into the
    prefix, not only that it was."""
    assert run(target, harness_root, {"implementer": forced_repair})[0] == 0
    events = (run_dir_of(target) / "events.log").read_text()
    permitting = [line for line in events.splitlines() if "permitted" in line]
    assert len(permitting) == 1
    assert "implementer" in permitting[0]
    assert PREFIX in permitting[0]
    assert "tests/test_app.py" in permitting[0]
    assert record_of(target)["output_tail"]


def test_an_edit_that_only_adds_coverage_is_escalated(target, harness_root):
    code, runner = run(target, harness_root, {"implementer": added_coverage})
    assert code == 2
    assert state_of(target)["status"] == "escalated"
    assert runner.calls == ["implementer"]

    record = record_of(target)
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert record["paths"] == ["tests/test_app.py"]


def test_a_deleted_governed_path_the_change_broke_is_permitted(target, harness_root):
    code, _ = run(target, harness_root, {"implementer": deleted_broken_test})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]


def test_deleting_a_governed_path_that_still_passes_is_escalated(target, harness_root):
    code, _ = run(target, harness_root, {"implementer": deleted_passing_test})
    assert code == 2
    record = record_of(target)
    assert record["permitted"] is False
    assert record["paths"] == ["tests/test_extra.py"]


def test_the_escalation_names_the_stage_the_prefix_and_the_paths(target, harness_root):
    assert run(target, harness_root, {"implementer": added_coverage})[0] == 2
    events, summary = evidence(target)
    for text in (events, summary):
        assert "implementer" in text
        assert PREFIX in text
        assert "tests/test_app.py" in text


def test_the_escalation_does_not_increment_retry_count(target, harness_root):
    """It escalates the way the ownership violation beside it does, not the
    way a failed verification does."""
    assert run(target, harness_root, {"implementer": added_coverage})[0] == 2
    state = state_of(target)
    assert state["status"] == "escalated"
    assert state["retry_count"] == 0


# --------------------------------------------------------------------------
# A record naming no governed path costs nothing
# --------------------------------------------------------------------------


def test_a_record_naming_no_governed_path_builds_no_clone_and_writes_nothing(
    target, harness_root, clone_calls, builds,
):
    code, _ = run(target, harness_root, {"implementer": nothing_governed})
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
    code, _ = run(target, harness_root, {"implementer": forced_repair})
    assert code == 0
    assert (run_dir_of(target) / ARTIFACT).exists()
    assert ("tests/test_app.py",) in clone_calls
    assert ("tests/test_app.py",) in builds


# --------------------------------------------------------------------------
# The check is driven by the declaration, not by the code
# --------------------------------------------------------------------------


def test_removing_the_declaration_disables_the_check(target, tmp_path, clone_calls):
    """The record that escalates against the shipped workflow completes
    against the same workflow with one key removed - no code change."""
    workflow = loaded_workflow()
    for stage in workflow["stages"]:
        stage.pop("revert_check", None)
    fake_root = mirror_harness(tmp_path / "no-declaration", workflow)

    code, _ = run(target, fake_root, {"implementer": added_coverage})
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
        if stage["name"] == "tester":
            stage["may_not_create"] = ["src/"]
            stage["revert_check"] = ARTIFACT
    fake_root = mirror_harness(tmp_path / "moved", workflow)

    # The implementer's edit under tests/ is now ungoverned; the tester's
    # edit under src/ is the one decided, and nothing forced it.
    code, runner = run(target, fake_root, {"implementer": added_coverage,
                                           "tester": nothing_governed})
    assert code == 2
    assert runner.calls == ["implementer", "tester"]
    record = record_of(target)
    assert record["permitted"] is False
    assert record["paths"] == ["src/app.py"]
    events, summary = evidence(target)
    for text in (events, summary):
        assert "tester" in text
        assert "src/" in text
        assert "src/app.py" in text


def test_no_stage_name_no_prefix_and_no_artifact_name_is_written_in_the_code():
    """All three are read off the loaded workflow and the story. Docstrings
    and comments are stripped first: prose may name what code may not."""
    body = executable_source(
        (ORCHESTRATION / "story_coordinator.py").read_text(encoding="utf-8"))
    assert PREFIX not in body
    assert ARTIFACT not in body
    for stage in WORKFLOW["stages"]:
        if stage["name"] == "verifier":
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
        "  - stage: implementer\n"
        f"    create: {PREFIX}\n"
        "    reason: the deliverable is the suite\n"
    ))
    code, _ = run(target, harness_root, {"implementer": added_coverage})
    assert code == 0
    assert not (run_dir_of(target) / ARTIFACT).exists()
    assert clone_calls == [()]


def test_without_the_grant_the_same_record_escalates(target, harness_root):
    """The control for the grant: the story is the only difference."""
    assert run(target, harness_root, {"implementer": added_coverage})[0] == 2


# --------------------------------------------------------------------------
# A check that cannot run refuses rather than permits
# --------------------------------------------------------------------------


def test_a_governed_path_that_cannot_be_reverted_escalates_naming_why(
    target, harness_root,
):
    """A record naming a governed path with no version at HEAD. The clone
    cannot be built, so there is no suite result to read - and the check says
    so instead of letting the edits through."""
    code, _ = run(target, harness_root, {"implementer": ghost_path})
    assert code == 2
    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert record["reason"]
    _, summary = evidence(target)
    assert "could not run" in summary
    assert "tests/test_ghost.py" in summary


def test_an_unresolvable_configured_interpreter_escalates_naming_why(
    target, harness_root,
):
    """The same treatment the clean-clone check gives it."""
    configure(target, clean_clone_python="nowhere/python")
    code, _ = run(target, harness_root, {"implementer": forced_repair})
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
    code, _ = run(target, harness_root, {"implementer": mixed_set})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py", "tests/test_extra.py"]


def test_the_addition_in_that_set_was_not_forced_by_anything(target, tmp_path):
    """What the test above would look like if the check discriminated per
    file: reverting the addition alone leaves the suite green. The check
    reports the set it reverted rather than claiming it decided per file."""
    mixed_set(target)
    alone = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "extra-only",
        revert=["tests/test_extra.py"])
    assert alone.ran is True
    assert alone.exit_code == 0


def test_a_single_file_mixing_a_repair_and_an_addition_is_permitted(
    target, harness_root,
):
    """The case the granularity misses outright: one file, both acts. The
    record names the file it reverted and claims nothing about its hunks."""
    code, _ = run(target, harness_root, {"implementer": mixed_file})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]
    assert set(record) <= {"ran", "paths", "command", "python", "python_version",
                           "clone_path", "exit_code", "output_tail", "permitted",
                           "reason"}


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
    assert run(target, harness_root, {"implementer": forced_repair})[0] == 0
    record = record_of(target)
    schema = schema_validator.load_schema(SCHEMA_STEM)
    assert schema_validator.validate(record, schema) == []
    # The control: the same validator against the same schema rejects a
    # record missing what the check must always report.
    incomplete = {key: value for key, value in record.items() if key != "paths"}
    assert schema_validator.validate(incomplete, schema) != []


def test_nothing_in_orchestration_reads_the_record_back(target, harness_root):
    """It is evidence, like clean-clone-result.json and retry-history.json.
    The control is clean-clone-result.json, which orchestration *does* name -
    so a scan that had stopped matching anything would fail here."""
    named = {module.name: module.read_text(encoding="utf-8")
             for module in sorted(ORCHESTRATION.glob("*.py"))}
    assert not [name for name, text in named.items() if ARTIFACT in text]
    assert [name for name, text in named.items() if "clean-clone-result.json" in text]


def test_the_record_is_not_injected_into_any_stage_prompt(target, harness_root):
    """Routing and context are the two ways a record could become state."""
    assert run(target, harness_root, {"implementer": forced_repair})[0] == 0
    context = context_assembler.build_context(
        story_text=STORY,
        story={"acceptance_criteria": []},
        run_dir=run_dir_of(target),
        target_root=target,
        harness_root=REPO_ROOT,
        config=harness_config.load_config(target),
        rules=harness_config.load_rules(REPO_ROOT),
        retry_count=0,
    )
    injected = {key: value for key, value in context.items()
                if value and "revert" in str(value) and not key.endswith("_schema")}
    assert injected == {}
    # The control: the clean-clone record is injected, by the key that names it.
    assert context["clean_clone_result"]


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
    forced_repair(target)
    story_coordinator._build_clone(target, tmp_path / "default")
    story_coordinator._build_clone(target, tmp_path / "reverted",
                                   revert=["tests/test_app.py"])

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
    verifier = next(s for s in WORKFLOW["stages"] if s["name"] == "verifier")
    assert run(target, harness_root, {"implementer": forced_repair})[0] == 0
    run_dir = run_dir_of(target)
    clean = json.loads((run_dir / verifier["clean_clone"]).read_text())
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
# What this story left alone
# --------------------------------------------------------------------------


def test_this_story_edited_no_story_artifact():
    """The control is the file the story did edit: if the diff resolution
    had stopped seeing anything, the second assertion would fail too."""
    assert story_diff([".harness/stories/"], validation_file=Path(__file__)) == ""
    assert story_diff(["orchestration/story_coordinator.py"],
                      validation_file=Path(__file__)) != ""


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
