"""A stage confined to the paths it may write is governed everywhere else.

The subject is the `may_only_change` declaration: the mirror image of the
create restriction the harness already had. A create restriction names the
paths a stage may not *add* to, and governs a path at or beneath one of them.
A confinement names the paths a stage may write, and governs every repository
path *outside* all of them. One workflow key, two opposite readings of a
prefix, and the whole of what this module is about is that the coordinator
asks the restriction which reading applies rather than comparing prefixes
itself.

Driven, not read. The workflow these runs execute is built by
`tests/conftest.py`'s builder rather than resolved out of what this repository
deploys: the question here is what a confinement *does*, and any stage list can
carry one. Which stage this deployment confines, and under what prefix, is a
fact about the deployment and is asked in
`tests/test_shipped_workflow_is_valid.py`. The target repository underneath is
a real module with a real suite over it, because the revert check's verdict is
whatever that suite does in a clone with the edits restored.

Every absence asserted here carries a demonstration that it can fail:

  * "a stage writing only inside its confinement is not decided at all" sits
    beside the same stage, the same run and the same machinery with one path
    moved outside it, which is decided — so silence is the confinement's
    boundary rather than a check that stopped running;
  * "a creation outside the confinement escalates" sits beside the same
    creation inside it, which does not, so the escalation is the boundary
    rather than a rule against creating anything;
  * "a granted path is exempt from both checks" sits beside the identical run
    without the grant, where the ownership check escalates on the creation and
    the revert check writes a record for the modification.

Nothing here invokes a model: every run goes through the fake runner below.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import story_coordinator
from agent_runner import AgentResult

STORY_ID = "story-001"

#: The prefix the validating stage is confined to, and the directory the
#: target's suite lives in. One value, stated once here, because the fixture
#: defines its own names and everything else derives them from it.
CONFINED_TO = "tests/"

#: Where the module under test lives, which is *outside* the confinement and
#: so is where every governed case below lands.
SOURCE_DIR = "src/"

WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        may_only_change=(CONFINED_TO,),
        revert_check={"result": "confinement-revert-result.json",
                      "baseline": "stage-baseline",
                      "discarded": "discarded-edits"},
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
        retry_routing={"the-work": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="confined-stage-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, CONFINED, DOCUMENTING, VERIFYING = STAGE_NAMES

#: The confined stage's declarations, found by the confinement rather than by
#: a name written here.
CONFINED_STAGE = next(stage for stage in WORKFLOW["stages"]
                      if story_coordinator.CONFINEMENT in stage)
DECLARATION = CONFINED_STAGE["revert_check"]
ARTIFACT = DECLARATION["result"]
PREFIX = CONFINED_STAGE[story_coordinator.CONFINEMENT][0]

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}
EMPTY = {"modified": [], "created": [], "deleted": []}

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest",
                           CONFINED_TO.rstrip("/"), "-q",
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
tests_dir: {CONFINED_TO}
"""


# --------------------------------------------------------------------------
# The target: a module outside the confinement, and a suite inside it
# --------------------------------------------------------------------------

APP_AT_HEAD = '''\
def greet(name):
    return f"hello, {name}"
'''

#: The module with the helper the confined stage's new coverage needs. Adding
#: it is an edit *outside* the confinement that the stage's own work forces:
#: revert it and the new test cannot import what it calls.
APP_WITH_HELPER = APP_AT_HEAD + '''

def shout(name):
    return greet(name).upper()
'''

TEST_APP_AT_HEAD = '''\
from app import greet


def test_greet():
    assert greet("world") == "hello, world"
'''

#: Coverage the confined stage may add freely: it is inside the confinement
#: and needs nothing outside it.
TEST_APP_PLUS_COVERAGE = TEST_APP_AT_HEAD + '''

def test_greet_is_lower_case():
    assert greet("world").islower()
'''

#: The new module the helper exists for, and the reason reverting the helper
#: breaks the suite.
TEST_SHOUT = '''\
from app import shout


def test_shout():
    assert shout("world") == "HELLO, WORLD"
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


@pytest.fixture
def target(tmp_path: Path) -> Path:
    root = tmp_path / "confinement-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / SOURCE_DIR / "app.py", APP_AT_HEAD)
    write(root / CONFINED_TO / "test_app.py", TEST_APP_AT_HEAD)
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
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "harness")


# --------------------------------------------------------------------------
# The confined stage's working-tree changes, each with the record for it
# --------------------------------------------------------------------------


def only_inside(root: Path) -> dict:
    """Coverage added inside the confinement, and nothing outside it."""
    write(root / CONFINED_TO / "test_app.py", TEST_APP_PLUS_COVERAGE)
    return {"modified": [f"{CONFINED_TO}test_app.py"], "created": [],
            "deleted": []}


def forced_edit_outside(root: Path) -> dict:
    """New coverage inside, and the helper outside that the coverage needs.

    The edit outside the confinement is the one the stage's own work forced:
    revert `src/app.py` and the new module cannot import what it calls, so the
    suite fails without it.
    """
    write(root / SOURCE_DIR / "app.py", APP_WITH_HELPER)
    write(root / CONFINED_TO / "test_shout.py", TEST_SHOUT)
    return {"modified": [f"{SOURCE_DIR}app.py"],
            "created": [f"{CONFINED_TO}test_shout.py"], "deleted": []}


def unforced_edit_outside(root: Path) -> dict:
    """The same edit outside, with nothing inside that needs it."""
    write(root / SOURCE_DIR / "app.py", APP_WITH_HELPER)
    return {"modified": [f"{SOURCE_DIR}app.py"], "created": [], "deleted": []}


def creation_outside(root: Path) -> dict:
    """A file brought into existence outside the confinement."""
    write(root / SOURCE_DIR / "helper.py", "VALUE = 1\n")
    return {"modified": [], "created": [f"{SOURCE_DIR}helper.py"],
            "deleted": []}


def creation_inside(root: Path) -> dict:
    """The same act inside the confinement, which is where it may write."""
    write(root / CONFINED_TO / "test_more.py",
          "def test_more():\n    assert True\n")
    return {"modified": [], "created": [f"{CONFINED_TO}test_more.py"],
            "deleted": []}


class Runner:
    """A fake agent runner: each stage writes its artifacts, and the confined
    stage also makes the working-tree change the case is about."""

    def __init__(self, target_root: Path, edit=None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.edit = edit
        self.records: dict[str, dict] = {}
        self.calls: list[str] = []

    def _record(self, stage: str) -> dict:
        made = self.edit(self.target_root) if (
            stage == CONFINED and self.edit) else dict(EMPTY)
        self.records[stage] = made
        return made

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       self._record(stage))
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did it.\n")
        elif stage == CONFINED:
            write_json(self.run_dir / conftest.TEST_RESULTS,
                       {"tests_written": 1})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       self._record(stage))
        elif stage == DOCUMENTING:
            write(self.run_dir / conftest.DOCUMENTATION_REPORT, "Nothing.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       dict(EMPTY))
        elif stage == VERIFYING:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(target_root: Path, harness: Path, edit=None) -> tuple[int, Runner]:
    runner = Runner(target_root, edit)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def record_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / ARTIFACT).read_text(
        encoding="utf-8"))


def evidence(target_root: Path) -> tuple[str, str]:
    run_dir = run_dir_of(target_root)
    return ((run_dir / "events.log").read_text(encoding="utf-8"),
            (run_dir / "escalation-summary.md").read_text(encoding="utf-8"))


def append_to_story(target_root: Path, text: str) -> None:
    path = target_root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=target_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "the story this test runs"],
                   cwd=target_root, check=True)


def grant(path: str, stage: str = None) -> str:
    return (
        "\nstage_exceptions:\n"
        f"  - stage: {stage or CONFINED}\n"
        f"    create: {path}\n"
        "    reason: this story's own deliverable is that file\n"
    )


# --------------------------------------------------------------------------
# What the declaration means: outside is governed, inside is not
# --------------------------------------------------------------------------


def test_the_confinement_governs_every_path_outside_it_and_none_inside_it():
    """The predicate, asked directly, over paths on both sides of the prefix.

    Neither reading is written here — `governs` is asked — and the pair is
    what makes the answer a boundary rather than a constant. A create
    restriction over the *same* prefix is asked the same questions beside it
    and answers the opposite way, which is the whole difference between the
    two senses.
    """
    confinement, = story_coordinator.restrictions_on(CONFINED_STAGE)
    creating, = story_coordinator.restrictions_on(
        {"name": CONFINED_STAGE["name"],
         story_coordinator.CREATE_RESTRICTION: [PREFIX]})

    inside = f"{PREFIX}test_app.py"
    outside = f"{SOURCE_DIR}app.py"

    assert confinement.governs(outside)
    assert not confinement.governs(inside)
    assert not confinement.governs(PREFIX)
    # The same prefix under the other sense, answering the other way.
    assert creating.governs(inside)
    assert not creating.governs(outside)


def test_a_stage_confined_to_several_prefixes_is_governed_only_outside_both():
    """A confinement is a statement about the whole list it named.

    Read for its own sake because the built workflow names one prefix and so
    cannot exhibit it: being confined to two directories means being governed
    outside *both*, and a restriction that spoke for its own prefix alone
    would claim a path its sibling covers.
    """
    restrictions = story_coordinator.restrictions_on(
        {"name": "somewhere", story_coordinator.CONFINEMENT: ["one/", "two/"]})

    # One restriction per declared prefix, so a reader meets each in its own
    # wording, and every one of them agrees about what is governed.
    assert [restriction.prefix for restriction in restrictions] == ["one/",
                                                                    "two/"]
    for restriction in restrictions:
        assert not restriction.governs("one/a.py")
        assert not restriction.governs("two/b.py")
        assert restriction.governs("three/c.py")


def test_a_stage_declaring_the_key_nowhere_is_governed_by_nothing():
    """The control the two above need: the same accessor over a stage with no
    declaration answers with nothing, so what they report is the declaration
    rather than a reader that governs everything."""
    assert story_coordinator.restrictions_on({"name": "somewhere"}) == []


# --------------------------------------------------------------------------
# A stage writing only inside its confinement is not decided at all
# --------------------------------------------------------------------------


def test_a_stage_writing_only_inside_its_confinement_is_unaffected(
    target, harness_root,
):
    code, runner = run(target, harness_root, only_inside)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    # No governed path, so the check has nothing to decide and writes nothing.
    assert not (run_dir_of(target) / ARTIFACT).exists()
    # And the stage's work stands.
    assert (target / CONFINED_TO / "test_app.py").read_text(
        encoding="utf-8") == TEST_APP_PLUS_COVERAGE


def test_the_same_run_with_one_path_moved_outside_is_decided(target,
                                                              harness_root):
    """The control for the silence above: identical machinery, one path on the
    other side of the confinement, and the check runs and writes a record."""
    code, _ = run(target, harness_root, forced_edit_outside)

    assert code == 0
    record = record_of(target)
    assert record["paths"] == [f"{SOURCE_DIR}app.py"]
    # The paths *inside* the confinement are not decided, so the check is
    # narrowed to what the confinement governs rather than to the record.
    assert f"{CONFINED_TO}test_shout.py" not in record["paths"]


# --------------------------------------------------------------------------
# A modification outside it stands exactly when reverting it breaks the suite
# --------------------------------------------------------------------------


def test_an_edit_outside_the_confinement_that_the_suite_needs_is_permitted(
    target, harness_root,
):
    code, runner = run(target, harness_root, forced_edit_outside)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["exit_code"] != 0          # the suite failed without it
    # The file keeps the stage's content.
    assert (target / SOURCE_DIR / "app.py").read_text(
        encoding="utf-8") == APP_WITH_HELPER


def test_the_same_edit_with_nothing_needing_it_is_refused(target, harness_root):
    """The control for the permission above, and the difference is the suite.

    The identical edit to the identical path by the identical stage, with the
    coverage that needed it left out. Reverting it now costs nothing, so it is
    refused — and undone, which is the disposition
    `tests/test_refused_edit_is_reverted.py` is about.
    """
    code, _ = run(target, harness_root, unforced_edit_outside)

    assert code == 0
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert (target / SOURCE_DIR / "app.py").read_text(
        encoding="utf-8") == APP_AT_HEAD


# --------------------------------------------------------------------------
# A creation outside it escalates, and the tree is left as the stage left it
# --------------------------------------------------------------------------


def test_a_creation_outside_the_confinement_escalates(target, harness_root):
    """No suite has ever been run against that file's absence, so there is no
    proof to act on and the run stops."""
    code, runner = run(target, harness_root, creation_outside)

    assert code == 2
    assert runner.calls == [WRITING, CONFINED]

    events, summary = evidence(target)
    (wording,) = [restriction.wording for restriction
                  in story_coordinator.stage_restrictions(WORKFLOW["stages"])]
    for text in (events, summary):
        assert CONFINED in text
        assert f"{SOURCE_DIR}helper.py" in text
        assert PREFIX in text
        # In the confinement's own words, so a reader of the escalation is not
        # told a confinement under a creation label.
        assert wording in text


def test_that_escalation_leaves_the_tree_as_the_stage_left_it(target,
                                                               harness_root):
    """Nothing was proved about the file, so nothing about it is undone."""
    assert run(target, harness_root, creation_outside)[0] == 2
    assert (target / SOURCE_DIR / "helper.py").read_text(
        encoding="utf-8") == "VALUE = 1\n"
    assert not (run_dir_of(target) / ARTIFACT).exists()


def test_the_same_creation_inside_the_confinement_does_not_escalate(
    target, harness_root,
):
    """The control: the same act by the same stage, on the other side of the
    prefix, which is where it may write."""
    code, runner = run(target, harness_root, creation_inside)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert (target / CONFINED_TO / "test_more.py").is_file()


# --------------------------------------------------------------------------
# A grant naming an outside path exempts it from both checks
# --------------------------------------------------------------------------


def test_a_grant_naming_an_outside_path_is_accepted_by_the_cross_check():
    """The plan-time half: a confinement governs a value outside the paths it
    names, so that is what a grant on it may name — and a value inside them,
    which the stage may write anyway, grants nothing."""
    outside = {"stage_exceptions": [
        {"stage": CONFINED, "create": f"{SOURCE_DIR}app.py",
         "reason": "the deliverable needs it"}]}
    inside = {"stage_exceptions": [
        {"stage": CONFINED, "create": f"{PREFIX}test_app.py",
         "reason": "the deliverable needs it"}]}

    assert story_coordinator.stage_exception_problems(
        outside, WORKFLOW["stages"]) == []
    (problem,) = story_coordinator.stage_exception_problems(
        inside, WORKFLOW["stages"])
    assert f"{PREFIX}test_app.py" in problem


def test_a_granted_creation_outside_the_confinement_does_not_escalate(
    target, harness_root,
):
    """The ownership half. Its control is
    `test_a_creation_outside_the_confinement_escalates`, which is the same run
    with the story the only difference."""
    append_to_story(target, grant(f"{SOURCE_DIR}helper.py"))
    code, runner = run(target, harness_root, creation_outside)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert (target / SOURCE_DIR / "helper.py").is_file()


def test_a_granted_modification_outside_it_is_not_put_to_the_revert_check(
    target, harness_root,
):
    """The revert-check half, and the same grant decides both.

    Its control is `test_the_same_edit_with_nothing_needing_it_is_refused`:
    the identical unforced edit, which without the grant is decided and
    refused. With the grant no record is written at all, and the edit stands.
    """
    append_to_story(target, grant(f"{SOURCE_DIR}app.py"))
    code, runner = run(target, harness_root, unforced_edit_outside)

    assert code == 0
    assert runner.calls == STAGE_NAMES
    assert not (run_dir_of(target) / ARTIFACT).exists()
    assert (target / SOURCE_DIR / "app.py").read_text(
        encoding="utf-8") == APP_WITH_HELPER


# --------------------------------------------------------------------------
# The stage baseline the confinement brings with it
# --------------------------------------------------------------------------


def test_the_baseline_captured_for_the_confined_stage_covers_what_it_governs(
    target, harness_root,
):
    """The half that closes the evidence gap.

    A stage confined to one directory has a baseline over the tree it may not
    freely change — which is what a revert needs and what a create
    restriction's baseline never had to cover. Asserted as two halves over the
    same directory: it holds the module outside the confinement, and it holds
    nothing inside it.
    """
    assert run(target, harness_root, forced_edit_outside)[0] == 0

    baseline = story_coordinator.stage_baseline_dir(
        run_dir_of(target), DECLARATION["baseline"], CONFINED)
    captured = {path.relative_to(baseline).as_posix()
                for path in baseline.rglob("*") if path.is_file()}

    assert f"{SOURCE_DIR}app.py" in captured
    assert not [path for path in captured if path.startswith(PREFIX)]
    # And what it holds is what the tree held *before* the stage ran.
    assert (baseline / SOURCE_DIR / "app.py").read_text(
        encoding="utf-8") == APP_AT_HEAD
