"""A refused edit is undone rather than escalated, and what still stops a run.

The revert check's verdict is unchanged: a stage's edit to a path its own
restriction governs stands exactly when reverting it makes the suite fail.
What moved is the *disposition* of a refusal. It used to stop the run. It no
longer does, and that is safe by construction rather than by judgement:
reaching a refusal means the check already built a clone with exactly those
paths restored to what the stage found and watched the suite pass in it, so
reproducing that content in the working tree reproduces the tree the harness
has already proved good. The coordinator restores those paths, keeps the
stage's own version of each in the run directory, records what it did, and the
run carries on.

Two things still stop a run, and they are different questions:

  * a check that reached **no verdict** — no baseline captured, the clone
    could not be built — because nothing was proved, so nothing may be undone;
  * a file **created** beneath a governed path, because no suite has ever been
    run against that file's absence.

Both are driven here beside the refusal, so the difference between them is a
behaviour rather than a claim.

The workflow these runs execute is built rather than deployed: the disposition
is a property of the revert check and any stage list can carry one. The target
underneath is a real module with a real suite over it, because every verdict
below is whatever that suite does in a clone.

Every absence asserted here carries a demonstration that it can fail:

  * "the run does not stop" sits beside the two cases that do stop it, through
    the same coordinator and the same fixture;
  * "the reverted path holds the baseline's content" sits beside the discarded
    copy kept in the run directory, which holds the stage's content instead —
    so the tree is shown to have changed rather than merely to have been read;
  * "a governed path the baseline does not hold is removed" is paired with a
    mutant whose one restore rule keeps it, and the same mutation is shown to
    change the clone the check decides in — which is what makes the rule one
    function rather than two that agree today;
  * "a workflow declaring neither restriction reverts nothing" sits beside the
    same run under the workflow that declares one, which reverts.

Nothing here invokes a model: every run goes through the fake runner below.
"""
from __future__ import annotations

import inspect
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import story_coordinator
from agent_runner import AgentResult
from conftest import load_mutant

REPO_ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"

STORY_ID = "story-001"

#: The prefix the writing stage is restricted from creating under, and the
#: directory the target's suite lives in.
GOVERNED_PREFIX = "tests/"

#: Where the stage's own version of each undone path is kept. Declared in the
#: workflow beside the result artifact, so the coordinator names neither.
DISCARDED_DIR = "discarded-edits"

REVERT_ARTIFACT = "revert-probe-result.json"
SUITE_ARTIFACT = "suite-probe-result.json"


def writing_stage(**extra) -> dict:
    """The stage that makes the edits, with whatever restriction the case wants.

    The suite run is declared on it too, so one run makes both the check's
    suite run and the stage's own — which is what lets an assertion say *which
    tree* the recorded suite result describes.
    """
    return conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        suite_run={"result": SUITE_ARTIFACT},
        schemas={conftest.CHANGED_FILES: "changed-files"},
        **extra)


def verifying_stage() -> dict:
    return conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        retry_routing={"the-work": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}})


CHECKED = {"result": REVERT_ARTIFACT, "baseline": "stage-baseline",
           "discarded": DISCARDED_DIR}

#: The workflow the runs below execute: a stage restricted from creating under
#: the prefix, with the revert check that decides its edits there.
WORKFLOW = conftest.build_workflow(
    writing_stage(may_not_create=(GOVERNED_PREFIX,), revert_check=CHECKED),
    verifying_stage(),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="refused-edit-workflow")

#: The same shape with the other sense of restriction, so the disposition can
#: be shown to belong to the check rather than to a stage or to a sense.
CONFINED_WORKFLOW = conftest.build_workflow(
    writing_stage(may_only_change=("src/",), revert_check=CHECKED),
    verifying_stage(),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="refused-edit-confinement-workflow")

#: And the same shape with neither restriction and no check at all: the
#: workflow that governs nothing, which is what the refactor definition this
#: repository ships looks like from the coordinator's side.
UNGOVERNED_WORKFLOW = conftest.build_workflow(
    writing_stage(), verifying_stage(),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="ungoverned-workflow")

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VERIFYING = STAGE_NAMES

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
EMPTY = {"modified": [], "created": [], "deleted": []}

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest",
                           GOVERNED_PREFIX.rstrip("/"), "-q",
                           "-p", "no:cacheprovider"])

CONFIG_TEMPLATE = """\
workflow: {workflow}
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: {test_command}
tests_dir: {tests_dir}
"""


# --------------------------------------------------------------------------
# The target: a module, a suite over it, and the contents each case needs
# --------------------------------------------------------------------------

APP_AT_HEAD = '''\
def greet(name):
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

#: Coverage nothing forced: it passes against the module before and after.
TEST_APP_PLUS_COVERAGE = TEST_APP_AT_HEAD + '''

def test_greet_again():
    assert greet("again") == "hello, again"
'''

#: A test that fails wherever it is run. Appended to the existing file it is a
#: modification the ownership check lets through, and reverting it leaves the
#: suite green — so the check refuses it, and whether the *stage's* recorded
#: suite run is red or green says which tree that run saw.
TEST_APP_PLUS_FAILURE = TEST_APP_AT_HEAD + '''

def test_this_one_never_passes():
    assert greet("world") == "goodbye"
'''

TEST_EXTRA_AT_HEAD = '''\
def test_arithmetic():
    assert 2 + 2 == 4
'''

#: A file no commit holds, recorded as a *modification* so the ownership check
#: lets it through and the revert check is the one that decides it. The
#: baseline cannot hold it, which is the removed-rather-than-restored case.
NEW_TEST_REL = f"{GOVERNED_PREFIX}test_brand_new.py"
NEW_TEST_SOURCE = '''\
def test_brand_new():
    assert True
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


def build_target(tmp_path: Path, workflow_name: str, name: str) -> Path:
    root = tmp_path / name
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG_TEMPLATE.format(workflow=workflow_name,
                                 test_command=TEST_COMMAND,
                                 tests_dir=GOVERNED_PREFIX))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / GOVERNED_PREFIX / "test_app.py", TEST_APP_AT_HEAD)
    write(root / GOVERNED_PREFIX / "test_extra.py", TEST_EXTRA_AT_HEAD)
    write(root / ".gitignore", ".pytest_cache/\n__pycache__/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root,
                   check=True)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return build_target(tmp_path, WORKFLOW["name"], "refusal-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "harness")


# --------------------------------------------------------------------------
# The stage's edits, each paired with the record describing it
# --------------------------------------------------------------------------


def free_coverage(root: Path, run_dir: Path) -> dict:
    """An addition to the module, and coverage the addition did not force."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    write(root / GOVERNED_PREFIX / "test_app.py", TEST_APP_PLUS_COVERAGE)
    return {"modified": ["src/app.py", f"{GOVERNED_PREFIX}test_app.py"],
            "created": [], "deleted": []}


def a_failing_test(root: Path, run_dir: Path) -> dict:
    """A test that never passes, appended to a file that already existed."""
    write(root / GOVERNED_PREFIX / "test_app.py", TEST_APP_PLUS_FAILURE)
    return {"modified": [f"{GOVERNED_PREFIX}test_app.py"], "created": [],
            "deleted": []}


def an_unforced_deletion(root: Path, run_dir: Path) -> dict:
    """A governed file removed, with nothing about the change needing it gone."""
    (root / GOVERNED_PREFIX / "test_extra.py").unlink()
    return {"modified": [], "created": [],
            "deleted": [f"{GOVERNED_PREFIX}test_extra.py"]}


def a_path_the_baseline_cannot_hold(root: Path, run_dir: Path) -> dict:
    """A file no commit holds, declared as a modification.

    Declared that way so the ownership check lets it through and the revert
    check is what decides it: the baseline was taken before the stage ran and
    cannot hold a path that did not exist then.
    """
    write(root / NEW_TEST_REL, NEW_TEST_SOURCE)
    return {"modified": [NEW_TEST_REL], "created": [], "deleted": []}


def a_creation_under_the_prefix(root: Path, run_dir: Path) -> dict:
    """The act that still escalates: a file *created* beneath a governed path."""
    write(root / GOVERNED_PREFIX / "test_created.py",
          "def test_created():\n    assert True\n")
    return {"modified": [], "created": [f"{GOVERNED_PREFIX}test_created.py"],
            "deleted": []}


def free_coverage_with_no_baseline(root: Path, run_dir: Path) -> dict:
    """The same free coverage, with the stage's baseline removed underneath it.

    A check that reaches no verdict, produced by taking away the thing it
    would decide against rather than by patching the coordinator.
    """
    record = free_coverage(root, run_dir)
    shutil.rmtree(run_dir / CHECKED["baseline"])
    return record


def edits_outside_the_test_location(root: Path, run_dir: Path) -> dict:
    """An unforced edit to the module, which no restriction here governs."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    return {"modified": ["src/app.py"], "created": [], "deleted": []}


class Runner:
    """A fake agent runner: each stage writes its artifacts, and the writing
    stage also makes the working-tree change the case is about."""

    def __init__(self, target_root: Path, edit=None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.edit = edit
        self.records: dict[str, dict] = {}
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        if stage == WRITING:
            record = (self.edit(self.target_root, self.run_dir) if self.edit
                      else dict(EMPTY))
            self.records[stage] = record
            write_json(self.run_dir / conftest.CHANGED_FILES, record)
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did it.\n")
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(target_root: Path, harness: Path, edit=None,
        coordinator=story_coordinator) -> tuple[int, Runner]:
    runner = Runner(target_root, edit)
    code = coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def record_of(target_root: Path, artifact: str = REVERT_ARTIFACT) -> dict:
    return json.loads((run_dir_of(target_root) / artifact).read_text(
        encoding="utf-8"))


def events_of(target_root: Path) -> str:
    return (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")


def append_to_story(target_root: Path, text: str) -> None:
    path = target_root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=target_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "the story this test runs"],
                   cwd=target_root, check=True)


# --------------------------------------------------------------------------
# The premise: this edit really is one nothing forced
# --------------------------------------------------------------------------


def test_the_added_coverage_passes_before_and_after_the_module_change(tmp_path):
    """Without this, "the suite passes with it reverted" would say nothing.

    The appended test is coverage rather than repair: it passes against the
    module as HEAD holds it and against the module the stage left.
    """
    for name, module in (("before", APP_AT_HEAD), ("after", APP_ADDITIVE)):
        scratch = tmp_path / name
        write(scratch / "conftest.py", ROOT_CONFTEST)
        write(scratch / "src" / "app.py", module)
        write(scratch / GOVERNED_PREFIX / "test_app.py",
              TEST_APP_PLUS_COVERAGE)
        assert subprocess.run(
            shlex.split(TEST_COMMAND), cwd=scratch, capture_output=True,
            text=True).returncode == 0, name


# --------------------------------------------------------------------------
# A refusal undoes the edit and the run carries on
# --------------------------------------------------------------------------


def test_the_run_reaches_every_later_stage(target, harness_root):
    code, runner = run(target, harness_root, free_coverage)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert json.loads((run_dir_of(target) / "state.json").read_text(
        encoding="utf-8"))["status"] == "completed"


def test_the_verdict_is_still_a_refusal_and_is_recorded_as_one(target,
                                                                harness_root):
    """The disposition moved; the verdict did not."""
    assert run(target, harness_root, free_coverage)[0] == 0
    record = record_of(target)

    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert record["paths"] == [f"{GOVERNED_PREFIX}test_app.py"]
    assert record["reverted"]["restored"] == [f"{GOVERNED_PREFIX}test_app.py"]
    assert record["reverted"]["removed"] == []


def test_the_reverted_path_holds_what_the_stage_baseline_captured(target,
                                                                   harness_root):
    """Read off the working tree, against the content the file held before the
    stage ran — which is the content the clone the check passed on carried.

    The ungoverned half of the same record is asserted to have survived, so
    this is the governed paths being undone rather than the change being
    thrown away.
    """
    assert run(target, harness_root, free_coverage)[0] == 0

    assert (target / GOVERNED_PREFIX / "test_app.py").read_text(
        encoding="utf-8") == TEST_APP_AT_HEAD
    assert (target / "src" / "app.py").read_text(
        encoding="utf-8") == APP_ADDITIVE


def test_the_stages_own_version_is_kept_under_the_declared_directory(
    target, harness_root,
):
    """The work a refusal discards survives in the run's evidence.

    Beneath the directory the *declaration* names, at the content the stage
    left — which is the content the working tree no longer holds, asserted
    above. Two readings of the same path, disagreeing, is what makes either
    one mean anything.
    """
    assert run(target, harness_root, free_coverage)[0] == 0

    kept = run_dir_of(target) / DISCARDED_DIR / GOVERNED_PREFIX / "test_app.py"
    assert kept.is_file()
    assert kept.read_text(encoding="utf-8") == TEST_APP_PLUS_COVERAGE
    assert record_of(target)["reverted"]["kept"] == [
        f"{GOVERNED_PREFIX}test_app.py"]
    assert record_of(target)["reverted"]["discarded"] == DISCARDED_DIR


def test_the_changed_files_record_is_byte_identical_to_what_the_stage_wrote(
    target, harness_root,
):
    """An attestation is not rewritten behind its author.

    The stage said it changed that file and it did; the revert record beside
    it is the correction. So the record still names the edit, and a reader
    learns from the revert record which of those edits no longer stands.
    """
    _, runner = run(target, harness_root, free_coverage)

    written = json.loads((run_dir_of(target) / conftest.CHANGED_FILES
                          ).read_text(encoding="utf-8"))
    assert written == runner.records[WRITING]
    assert f"{GOVERNED_PREFIX}test_app.py" in written["modified"]
    assert f"{GOVERNED_PREFIX}test_app.py" in record_of(
        target)["reverted"]["restored"]


def test_the_revert_is_announced_in_the_event_stream(target, harness_root):
    """The run does not stop, so there is no escalation summary to carry this
    and the event log is the whole of the record."""
    assert run(target, harness_root, free_coverage)[0] == 0
    reverting = [line for line in events_of(target).splitlines()
                 if "revert" in line and f"{GOVERNED_PREFIX}test_app.py" in line]
    assert reverting
    assert any(WRITING in line and GOVERNED_PREFIX in line
               for line in reverting)
    assert any(DISCARDED_DIR in line for line in reverting)


# --------------------------------------------------------------------------
# A deletion refused puts the file back
# --------------------------------------------------------------------------


def test_a_refused_deletion_leaves_the_file_present_at_the_baselines_content(
    target, harness_root,
):
    code, _ = run(target, harness_root, an_unforced_deletion)

    assert code == 0
    record = record_of(target)
    assert record["permitted"] is False
    assert record["paths"] == [f"{GOVERNED_PREFIX}test_extra.py"]
    assert record["reverted"]["restored"] == [f"{GOVERNED_PREFIX}test_extra.py"]

    restored = target / GOVERNED_PREFIX / "test_extra.py"
    assert restored.is_file()
    assert restored.read_text(encoding="utf-8") == TEST_EXTRA_AT_HEAD


# --------------------------------------------------------------------------
# A governed path the baseline does not hold is removed, by one rule
# --------------------------------------------------------------------------


def test_a_governed_path_the_baseline_lacks_is_removed_rather_than_restored(
    target, harness_root,
):
    """A path absent from the baseline did not exist when the stage started,
    so putting the tree back where the stage found it means removing it."""
    code, _ = run(target, harness_root, a_path_the_baseline_cannot_hold)

    assert code == 0
    record = record_of(target)
    assert record["permitted"] is False
    assert record["reverted"]["removed"] == [NEW_TEST_REL]
    assert record["reverted"]["restored"] == []
    assert not (target / NEW_TEST_REL).exists()
    # And the stage's version survives as evidence even though the tree does
    # not carry it.
    assert (run_dir_of(target) / DISCARDED_DIR / NEW_TEST_REL).read_text(
        encoding="utf-8") == NEW_TEST_SOURCE


#: The removal half of the restore rule, as the one function writes it. The
#: mutation below takes it away, and both callers are then shown to keep the
#: path — which is what "one function decides it" means operationally.
THE_REMOVAL = """            if destination.is_file():
                destination.unlink()
"""


def keeping_mutant(tmp_path: Path):
    return load_mutant(COORDINATOR_PATH, [(THE_REMOVAL, "            pass\n")],
                       name="coordinator_that_never_removes",
                       tmp_path=tmp_path)


def test_both_callers_change_together_when_that_one_rule_changes(tmp_path,
                                                                  target):
    """The clone the proof is taken in and the tree the proof licenses must
    decide restore-or-remove identically, or a revert could leave a tree the
    proof does not cover.

    Driven rather than argued: one mutation, made in one function, and both
    the working-tree revert and the clone build stop removing the path. The
    control is the same two calls through the real coordinator, where both
    remove it.
    """
    baseline = story_coordinator.capture_stage_baseline(
        tmp_path / "run", target, CHECKED["baseline"], WRITING,
        [GOVERNED_PREFIX], accounted_for=set())
    write(target / NEW_TEST_REL, NEW_TEST_SOURCE)

    def survives(module, where: str) -> tuple[bool, bool]:
        """Whether the path survives the working-tree revert, and the clone."""
        tree = tmp_path / f"{where}-tree"
        write(tree / NEW_TEST_REL, NEW_TEST_SOURCE)
        module.restore_from_baseline(baseline, tree, [NEW_TEST_REL])

        clone = tmp_path / f"{where}-clone"
        module._build_clone(target, clone, revert=[NEW_TEST_REL],
                            baseline=baseline)
        return (tree / NEW_TEST_REL).exists(), (clone / NEW_TEST_REL).exists()

    assert survives(story_coordinator, "real") == (False, False)
    assert survives(keeping_mutant(tmp_path), "mutant") == (True, True)


def test_the_two_callers_are_the_only_ones_and_neither_repeats_the_rule():
    """The structural half of the pairing above.

    Both call the shared function by name, and neither writes an `unlink` of
    its own — a second copy of the rule is exactly what the mutation pairing
    could not catch, because a copy would agree with the original today.
    """
    shared = story_coordinator.restore_from_baseline.__name__
    for caller in (story_coordinator._build_clone,
                   story_coordinator.revert_refused_edits):
        source = inspect.getsource(caller)
        assert shared in source, caller.__name__
        assert "unlink(" not in source, caller.__name__


# --------------------------------------------------------------------------
# The recorded suite run describes the reverted tree
# --------------------------------------------------------------------------


def test_a_suite_run_recorded_after_the_check_reports_on_the_reverted_tree(
    target, harness_root,
):
    """The ordering, read off a run rather than off the source.

    The stage leaves a test that never passes. Reverting it leaves the suite
    green, so the check refuses it and the coordinator undoes it — and the
    stage's own suite run, which happens after the check, then runs on a tree
    the failing test is no longer in and exits zero.
    """
    code, _ = run(target, harness_root, a_failing_test)

    assert code == 0
    assert record_of(target)["permitted"] is False
    assert record_of(target)["reverted"]["restored"] == [
        f"{GOVERNED_PREFIX}test_app.py"]

    suite = record_of(target, SUITE_ARTIFACT)
    assert suite["ran"] is True
    assert suite["exit_code"] == 0


def test_the_same_edit_left_in_place_makes_that_suite_run_red(target,
                                                               harness_root):
    """The control for the ordering above.

    The identical edit, with the story granting the path so no check decides
    it and nothing is undone. The stage's suite run then sees the failing test
    and exits non-zero — so the green above is the revert having happened
    first, rather than a suite that cannot fail.
    """
    append_to_story(target, (
        "\nstage_exceptions:\n"
        f"  - stage: {WRITING}\n"
        f"    create: {GOVERNED_PREFIX}test_app.py\n"
        "    reason: this story's own deliverable is that file\n"))

    run(target, harness_root, a_failing_test)

    assert not (run_dir_of(target) / REVERT_ARTIFACT).exists()
    assert record_of(target, SUITE_ARTIFACT)["exit_code"] != 0


# --------------------------------------------------------------------------
# What still stops a run
# --------------------------------------------------------------------------


def test_a_check_that_reached_no_verdict_still_escalates(target, harness_root):
    """Nothing was proved, so nothing may be undone.

    The baseline is taken away underneath the check rather than the
    coordinator being patched, so what is exercised is the coordinator's own
    handling of a check it could not decide.
    """
    code, runner = run(target, harness_root, free_coverage_with_no_baseline)

    assert code == 2
    assert runner.calls == [WRITING]
    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert "reverted" not in record


def test_that_escalation_reverts_nothing(target, harness_root):
    """The tree is left exactly as the stage left it, because the check that
    would have licensed undoing it never reached a verdict."""
    assert run(target, harness_root, free_coverage_with_no_baseline)[0] == 2
    assert (target / GOVERNED_PREFIX / "test_app.py").read_text(
        encoding="utf-8") == TEST_APP_PLUS_COVERAGE
    assert not (run_dir_of(target) / DISCARDED_DIR).exists()


def test_a_creation_beneath_a_governed_prefix_still_escalates(target,
                                                               harness_root):
    """No suite has ever been run against that file's absence, so there is no
    proof to act on. The control is the refusal above, which is the same
    coordinator on the same fixture reaching the other disposition."""
    code, runner = run(target, harness_root, a_creation_under_the_prefix)

    assert code == 2
    assert runner.calls == [WRITING]
    assert (target / GOVERNED_PREFIX / "test_created.py").is_file()
    assert not (run_dir_of(target) / REVERT_ARTIFACT).exists()


# --------------------------------------------------------------------------
# The disposition belongs to the check, not to a stage or a sense
# --------------------------------------------------------------------------


def test_a_refusal_under_a_confinement_has_the_same_disposition(tmp_path):
    """One verdict, one meaning, whichever restriction produced it.

    The same coordinator and the same unforced edit under a workflow whose
    stage is *confined* rather than restricted from creating: the run
    continues, the path holds the baseline's content, and the stage's version
    is kept. The alternative the story rejected is one verdict meaning two
    things depending on who triggered it.
    """
    root = build_target(tmp_path, CONFINED_WORKFLOW["name"], "confined-target")
    harness = conftest.materialize_workflow(CONFINED_WORKFLOW,
                                            tmp_path / "confined-harness")

    code, _ = run(root, harness, free_coverage)

    assert code == 0
    record = json.loads((run_dir_of(root) / REVERT_ARTIFACT).read_text(
        encoding="utf-8"))
    assert record["permitted"] is False
    assert record["reverted"]["restored"] == [f"{GOVERNED_PREFIX}test_app.py"]
    assert (root / GOVERNED_PREFIX / "test_app.py").read_text(
        encoding="utf-8") == TEST_APP_AT_HEAD
    assert (run_dir_of(root) / DISCARDED_DIR / GOVERNED_PREFIX
            / "test_app.py").is_file()


def test_a_workflow_declaring_no_restriction_reverts_and_escalates_nothing(
    tmp_path,
):
    """A workflow whose stages declare neither sense and no check at all.

    The stage edits a file outside the configured test location and nothing
    happens to it: no restriction derives, no baseline is captured, no record
    is written, and the edit stands. Its controls are the two workflows above,
    which are the same stages with a declaration added.
    """
    root = build_target(tmp_path, UNGOVERNED_WORKFLOW["name"],
                        "ungoverned-target")
    harness = conftest.materialize_workflow(UNGOVERNED_WORKFLOW,
                                            tmp_path / "ungoverned-harness")

    assert story_coordinator.stage_restrictions(
        UNGOVERNED_WORKFLOW["stages"]) == []

    code, runner = run(root, harness, edits_outside_the_test_location)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert not (run_dir_of(root) / REVERT_ARTIFACT).exists()
    assert not (run_dir_of(root) / DISCARDED_DIR).exists()
    assert (root / "src" / "app.py").read_text(
        encoding="utf-8") == APP_ADDITIVE
