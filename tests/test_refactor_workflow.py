"""Independent validation for story-070: a refactor runs under a workflow
whose guard suits it.

The subject here is what this repository *ships* and how the one coordinator
behaves under two shipped definitions, so the workflows are read rather than
built. That is the exception the fixture rule states, not a departure from it:
an assertion about how the coordinator routes needs *a* workflow and builds one
(`tests/test_suite_census.py` does exactly that for the census mechanism), while
an assertion about what this harness deploys has to read what it deploys.

The runs below carry the comparison, and they differ in one thing — the workflow
the target configures:

  * under `refactor-workflow`, an implementer that creates a file beneath the
    configured tests directory *and* modifies an existing one completes: no
    ownership refusal, no revert check, and a census taken instead;
  * under `story-workflow`, the same creation is refused; and
  * under `story-workflow`, a modification whose reversion leaves the suite
    green is refused.

The story-workflow runs are the controls for the refactor one. Without them
"the refactor run was not refused" would be equally consistent with a
coordinator that had stopped enforcing anything at all.

Every other absence carries a control too: "the refactor workflow declares no
tester stage" is paired with the workflow that does; "its implementer declares
neither of the two keys" with the implementer that declares both; "the
story-workflow prompts are unchanged" with the prompts this story added; and
"the two lookups are identical across this story" with a mutated copy of the
same text, which the same comparison reports.

`.harness/docs/ARCHITECTURE.md` and `README.md` are not asserted on: this
story's plan assigns both to the documenter, which has not run when this module
is written. The prompt-naming convention and the recorded predictions are
recorded there, and what is checkable here — that the prompts exist, that they
say what the workflow needs them to say, and that no coordinator change was
required — is checked here.

Nothing here invokes a model: every run goes through a fake agent runner.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import context_assembler
import story_coordinator
from agent_runner import AgentResult
from conftest import BASELINE, repository_file_at, story_diff

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / "workflows"
PROMPTS = REPO_ROOT / "prompts"

#: The two definitions this repository ships, loaded the way a run loads them —
#: against this repository's own configuration, so `{{tests_dir}}` is the value
#: rather than the token.
REFACTOR = conftest.shipped_workflow(REPO_ROOT, "refactor-workflow")
STORY_WORKFLOW = conftest.shipped_workflow(REPO_ROOT, "story-workflow")

REFACTOR_STAGES = REFACTOR["stages"]
REFACTOR_NAMES = [stage["name"] for stage in REFACTOR_STAGES]

#: The stages each definition puts first, found by position rather than by a
#: name written here. Both definitions open with the stage that writes the code.
REFACTOR_WRITER = REFACTOR_STAGES[0]
STORY_WRITER = STORY_WORKFLOW["stages"][0]

#: What the configured tests directory resolves to for this repository, and so
#: what the refactor implementer's census governs and what story-workflow's
#: implementer may not create under.
TESTS_DIR = conftest.repository_config()["tests_dir"]

STORY_ID = "story-001"
PASS_VERDICT = {"status": "passed", "blocking_issues": [], "unverified": [],
                "retry_recommended": False}

SHIPPED_CENSUS = REPO_ROOT / ".harness" / "census.py"


# --------------------------------------------------------------------------
# What the shipped refactor workflow declares
# --------------------------------------------------------------------------


def test_the_refactor_workflow_declares_three_stages_in_the_order_it_states():
    assert REFACTOR_NAMES == ["implementer", "documenter", "verifier"]
    assert REFACTOR_NAMES[-1] == conftest.VERIFYING_STAGE


def test_it_declares_no_stage_that_authors_validation():
    """The stage story-workflow uses to author tests is absent here, because a
    refactor's implementer edits the validation itself. The control is the
    definition that does declare one: a comparison that had stopped seeing
    stage names would report both as tester-free."""
    story_names = [stage["name"] for stage in STORY_WORKFLOW["stages"]]
    assert "tester" in story_names
    assert "tester" not in REFACTOR_NAMES


def test_the_refactor_implementer_declares_neither_of_the_two_dropped_keys():
    """The two declarations that rest on the assumption a refactor breaks.

    The control is story-workflow's implementer, which declares both: if the
    lookup below had stopped reading a stage at all, the pair would look
    identical and the absence would mean nothing."""
    assert "may_not_create" in STORY_WRITER
    assert "revert_check" in STORY_WRITER
    assert "may_not_create" not in REFACTOR_WRITER
    assert "revert_check" not in REFACTOR_WRITER


def test_the_refactor_implementer_declares_the_census_and_the_suite_run():
    """The third proxy in their place, and the suite still gated in the tree
    the stage left."""
    assert REFACTOR_WRITER["suite_census"]["result"]
    assert REFACTOR_WRITER["suite_census"]["baseline"]
    assert REFACTOR_WRITER["suite_census"]["paths"] == [TESTS_DIR]
    assert REFACTOR_WRITER["suite_run"]["result"]


def test_the_declaration_names_the_configured_tests_directory_by_token():
    """The definition itself names no directory: what the census governs is
    the configuration's answer, resolved when the definition loads."""
    raw = (WORKFLOWS / "refactor-workflow.json").read_text(encoding="utf-8")
    assert "{{tests_dir}}" in raw
    assert TESTS_DIR not in raw


def test_the_verifier_routes_every_retry_to_a_stage_this_workflow_defines():
    assert story_coordinator.retry_routing_problems(REFACTOR_STAGES) == []
    routed = {route.stage for route in
              context_assembler.retry_routes(REFACTOR_STAGES)}
    assert routed
    assert routed <= set(REFACTOR_NAMES)


def test_the_workflow_declares_only_budgets_and_ceilings_that_are_counts():
    assert story_coordinator.self_route_problems(REFACTOR_STAGES) == []
    assert story_coordinator.cost_ceiling_problems(REFACTOR) == []


#: Every ceiling this definition declares, paired with the key that records why.
CEILING_KEYS = ("max_run_cost_usd", "max_execution_cost_usd")


def declared_ceilings() -> list[tuple[str, dict, str]]:
    found = [(REFACTOR["name"], REFACTOR, "max_run_cost_usd")]
    found += [(stage["name"], stage, "max_execution_cost_usd")
              for stage in REFACTOR_STAGES if "max_execution_cost_usd" in stage]
    return found


def test_every_declared_ceiling_records_that_this_workflow_has_no_corpus_yet():
    """A number a reader meets with no derivation beside it is a number nobody
    can tighten. This workflow has run nothing, so what the reason records is
    where the figure came from and what would replace it."""
    declarations = declared_ceilings()
    assert len(declarations) == len(REFACTOR_STAGES) + 1
    for name, declaring, key in declarations:
        reason = declaring.get(f"{key}_reason", "")
        assert "cost corpus" in reason, name
        assert "story-workflow" in reason, name


def test_every_stage_names_a_prompt_and_a_schema_this_repository_ships():
    for stage in REFACTOR_STAGES:
        assert (PROMPTS / stage["prompt"]).is_file(), stage["name"]
        for schema in stage.get("schemas", {}).values():
            assert (REPO_ROOT / "schemas" / f"{schema}.schema.json").is_file()


# --------------------------------------------------------------------------
# The target the three runs share
#
# A real module and a real suite over it, so "reverting this edit leaves the
# suite green" is answered by running it rather than by asserting about the
# code that runs it.
# --------------------------------------------------------------------------

APP_AT_HEAD = '''\
def greet(name):
    return f"hello, {name}"
'''

APP_RENAMED = '''\
def salute(name):
    return f"hello, {name}"
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

TEST_EXTRA_AT_HEAD = '''\
def test_arithmetic():
    assert 1 + 1 == 2
'''

#: A test nothing forces: it passes against the module before and after, so
#: reverting it leaves the suite green.
TEST_EXTRA_PLUS_COVERAGE = TEST_EXTRA_AT_HEAD + '''

def test_arithmetic_again():
    assert 2 + 2 == 4
'''

#: A whole file the writing stage creates beneath the configured tests
#: directory, which is the act story-workflow refuses outright.
TEST_NEW_FILE = '''\
from app import salute


def test_salute_is_still_a_greeting():
    assert salute("again").startswith("hello")
'''

ROOT_CONFTEST = '''\
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
'''

TEST_COMMAND = shlex.join([sys.executable, "-m", "pytest", "tests", "-q",
                           "-p", "no:cacheprovider"])
CENSUS_COMMAND = shlex.join([sys.executable, str(SHIPPED_CENSUS), TESTS_DIR])

CONFIG = """\
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
census_command: {census_command}
tests_dir: {tests_dir}
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def build_target(tmp_path: Path, workflow_name: str) -> Path:
    root = tmp_path / f"{workflow_name}-target"
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml",
          CONFIG.format(workflow=workflow_name, test_command=TEST_COMMAND,
                        census_command=CENSUS_COMMAND, tests_dir=TESTS_DIR))
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", conftest.STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / TESTS_DIR / "test_app.py", TEST_APP_AT_HEAD)
    write(root / TESTS_DIR / "test_extra.py", TEST_EXTRA_AT_HEAD)
    write(root / ".gitignore", ".pytest_cache/\n__pycache__/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


@pytest.fixture(scope="module")
def deployed_harness(tmp_path_factory) -> Path:
    """This repository's shipped workflows, prompts, rules and schemas, copied.

    Copied rather than reached for in place, so nothing a run does can touch
    this repository, and copied rather than symlinked because a symlink
    resolves straight back to it.
    """
    root = tmp_path_factory.mktemp("deployed-harness")
    ignore = shutil.ignore_patterns("__pycache__")
    for directory in ("workflows", "prompts", "rules", "schemas"):
        shutil.copytree(REPO_ROOT / directory, root / directory, ignore=ignore)
    return root


# --------------------------------------------------------------------------
# The working-tree changes, each paired with the record that describes it
# --------------------------------------------------------------------------


def refactor_edit(root: Path) -> dict:
    """A rename carried through the suite, plus a test file of its own.

    Both acts at once, because the criterion is about a stage that creates
    *and* modifies beneath the configured tests directory.
    """
    write(root / "src" / "app.py", APP_RENAMED)
    write(root / TESTS_DIR / "test_app.py", TEST_APP_REPAIRED)
    write(root / TESTS_DIR / "test_new.py", TEST_NEW_FILE)
    return {"modified": ["src/app.py", f"{TESTS_DIR}test_app.py"],
            "created": [f"{TESTS_DIR}test_new.py"], "deleted": []}


def weakening_edit(root: Path) -> dict:
    """An existing test deleted: the threat a refactor actually carries."""
    (root / TESTS_DIR / "test_extra.py").unlink()
    return {"modified": [], "created": [],
            "deleted": [f"{TESTS_DIR}test_extra.py"]}


def unforced_modification(root: Path) -> dict:
    """An edit to an existing test that no change elsewhere forced."""
    write(root / TESTS_DIR / "test_extra.py", TEST_EXTRA_PLUS_COVERAGE)
    return {"modified": [f"{TESTS_DIR}test_extra.py"], "created": [],
            "deleted": []}


class Runner:
    """A fake agent runner. Every stage writes its declared artifacts, and the
    writing stage makes the working-tree change the case is about.

    It answers to the stages of whichever definition the target configured, so
    one runner drives both workflows.
    """

    def __init__(self, root: Path, change):
        self.root = root
        self.run_dir = root / ".harness" / "runs" / STORY_ID
        self.change = change
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None):
        self.calls.append(stage)
        if stage == "implementer":
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       self.change(self.root))
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY, "Did it.\n")
        elif stage == "tester":
            write_json(self.run_dir / conftest.TEST_RESULTS, {"tests_written": 0})
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == "documenter":
            write(self.run_dir / conftest.DOCUMENTATION_REPORT,
                  "No documentation changes were needed.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        elif stage == conftest.VERIFYING_STAGE:
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, PASS_VERDICT)
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(root: Path, harness: Path, change) -> tuple[int, Runner]:
    runner = Runner(root, change)
    code = story_coordinator.run_story(STORY_ID, harness, root, runner)
    return code, runner


def run_dir_of(root: Path) -> Path:
    return root / ".harness" / "runs" / STORY_ID


def artifacts_of(root: Path) -> set[str]:
    return {path.name for path in run_dir_of(root).iterdir() if path.is_file()}


def summary_of(root: Path) -> str:
    return (run_dir_of(root) / "escalation-summary.md").read_text(encoding="utf-8")


def why_it_stopped(root: Path) -> str:
    """The escalation summary if the run wrote one, for a failure message.

    A run that completed writes none, so a bare read would replace the
    assertion's own failure with a FileNotFoundError from the message.
    """
    summary = run_dir_of(root) / "escalation-summary.md"
    return summary.read_text(encoding="utf-8") if summary.is_file() else (
        "the run wrote no escalation summary")


@pytest.fixture
def reverting_clones(monkeypatch):
    """Every clone the coordinator built with something reverted.

    The run's other clones — the census's own, and the verifier's clean-clone
    check — are told apart by what they revert, so a count of zero here is read
    as "no revert check ran" rather than as "no clone was built".
    """
    seen: list[tuple[str, ...]] = []
    original = story_coordinator.run_clean_clone

    def spy(*args, **kwargs):
        revert = kwargs.get("revert", args[4] if len(args) > 4 else ())
        if revert:
            seen.append(tuple(revert))
        return original(*args, **kwargs)

    monkeypatch.setattr(story_coordinator, "run_clean_clone", spy)
    return seen


# --------------------------------------------------------------------------
# The same coordinator, enforcing differently under the two definitions
# --------------------------------------------------------------------------


def test_a_refactor_run_creating_and_modifying_tests_completes(
        tmp_path, deployed_harness, reverting_clones):
    target = build_target(tmp_path, REFACTOR["name"])
    code, runner = run(target, deployed_harness, refactor_edit)

    assert code == 0, why_it_stopped(target)
    assert runner.calls == REFACTOR_NAMES
    written = artifacts_of(target)
    # No ownership refusal, and no revert check: neither the artifact the
    # check writes nor a clone with anything reverted.
    assert STORY_WRITER["revert_check"]["result"] not in written
    assert reverting_clones == []
    # The census ran in its place, and permitted the stage.
    census = json.loads(
        (run_dir_of(target) / REFACTOR_WRITER["suite_census"]["result"]
         ).read_text(encoding="utf-8"))
    assert census["ran"] is True
    assert census["permitted"] is True


def test_that_run_still_gates_the_suite_where_the_stage_left_it_and_in_a_clone(
        tmp_path, deployed_harness):
    """Dropping the two declarations dropped no suite run. The obligation
    moved to the coordinator; it did not lapse."""
    target = build_target(tmp_path, REFACTOR["name"])
    assert run(target, deployed_harness, refactor_edit)[0] == 0

    run_dir = run_dir_of(target)
    gated = json.loads((run_dir / REFACTOR_WRITER["suite_run"]["result"]).read_text(
        encoding="utf-8"))
    clean = json.loads(
        (run_dir / REFACTOR_STAGES[-1]["clean_clone"]["result"]).read_text(
            encoding="utf-8"))
    assert gated["ran"] is True and gated["exit_code"] == 0
    assert clean["ran"] is True and clean["exit_code"] == 0


def test_a_refactor_run_that_weakens_the_suite_is_refused_by_the_census(
        tmp_path, deployed_harness):
    """What the third proxy is for, driven end to end through the shipped
    definition rather than through the check alone: the stage deletes an
    existing test, and the run stops naming the counter that fell."""
    target = build_target(tmp_path, REFACTOR["name"])
    code, runner = run(target, deployed_harness, weakening_edit)

    assert code == 2
    assert runner.calls == ["implementer"]
    record = json.loads(
        (run_dir_of(target) / REFACTOR_WRITER["suite_census"]["result"]).read_text(
            encoding="utf-8"))
    assert record["permitted"] is False
    fell = {item["counter"] for item in record["regressions"]}
    assert fell
    summary = summary_of(target)
    for counter in sorted(fell):
        assert counter in summary


def test_the_same_creation_under_story_workflow_is_refused(
        tmp_path, deployed_harness):
    """The first control. One file beneath the configured tests directory,
    created by the implementer, and the run stops on ownership."""
    target = build_target(tmp_path, STORY_WORKFLOW["name"])
    code, runner = run(target, deployed_harness, refactor_edit)

    assert code == 2
    assert runner.calls == ["implementer"]
    summary = summary_of(target)
    assert TESTS_DIR in summary
    assert f"{TESTS_DIR}test_new.py" in summary


def test_a_modification_whose_reversion_leaves_the_suite_green_is_still_refused(
        tmp_path, deployed_harness):
    """The second control, and the one the refactor workflow exists for: an
    edit to an existing test that nothing forced, decided by reverting it and
    running the suite."""
    target = build_target(tmp_path, STORY_WORKFLOW["name"])
    code, runner = run(target, deployed_harness, unforced_modification)

    assert code == 2
    assert runner.calls == ["implementer"]
    record = json.loads(
        (run_dir_of(target) / STORY_WRITER["revert_check"]["result"]).read_text(
            encoding="utf-8"))
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["exit_code"] == 0
    assert record["paths"] == [f"{TESTS_DIR}test_extra.py"]


def test_the_refactor_workflow_permits_that_same_unforced_modification(
        tmp_path, deployed_harness, reverting_clones):
    """The pair the whole story turns on: the identical edit, the identical
    coordinator, and the workflow the only difference."""
    target = build_target(tmp_path, REFACTOR["name"])
    code, runner = run(target, deployed_harness, unforced_modification)

    assert code == 0
    assert runner.calls == REFACTOR_NAMES
    assert reverting_clones == []
    assert STORY_WRITER["revert_check"]["result"] not in artifacts_of(target)


# --------------------------------------------------------------------------
# Making the two declarations differ needed no coordinator change
#
# The prediction recorded before this work began, answered against the source
# rather than argued: both are read as `stage.get(...)`, both no-op when the key
# is absent, and neither line moved.
# --------------------------------------------------------------------------

#: The two lookups, each written as the whole line that performs it so a
#: similar expression elsewhere in a five-thousand-line file cannot stand in
#: for it.
LOOKUPS = (
    'enforced = list(stage.get("may_not_create", []))',
    'declaration = stage.get("revert_check") or {}',
)

COORDINATOR_REL = "orchestration/story_coordinator.py"


def lookup_lines(text: str) -> dict[str, list[str]]:
    """For each lookup, the lines of `text` that perform it.

    A mapping rather than a boolean so the comparison below can report *which*
    lookup differs, and so a lookup that appears twice is visible rather than
    collapsing into a yes.
    """
    lines = [line.strip() for line in text.splitlines()]
    return {lookup: [line for line in lines if line == lookup]
            for lookup in LOOKUPS}


def test_both_lookups_read_the_same_line_before_and_after_this_story():
    before = repository_file_at(COORDINATOR_REL, validation_file=Path(__file__),
                                bound=BASELINE)
    after = (REPO_ROOT / COORDINATOR_REL).read_text(encoding="utf-8")
    assert lookup_lines(before) == lookup_lines(after)
    assert all(len(found) == 1 for found in lookup_lines(after).values())


def test_the_comparison_reports_a_lookup_that_did_change():
    """The control for the assertion above. Green there says the two lookups
    are untouched only if the comparison can go red, so the same before-text is
    mutated at one lookup and handed to the same comparison."""
    before = repository_file_at(COORDINATOR_REL, validation_file=Path(__file__),
                                bound=BASELINE)
    mutated = before.replace(LOOKUPS[1],
                             'declaration = stage.get("revert_check", {})', 1)
    assert mutated != before
    assert lookup_lines(before) != lookup_lines(mutated)


def test_each_lookup_yields_nothing_when_the_declaration_is_absent():
    """Why no coordinator change was needed, stated as behaviour: a stage that
    declares neither key answers both lookups with an empty value, which is
    what makes each check a no-op under a workflow that drops it."""
    assert REFACTOR_WRITER.get("may_not_create", []) == []
    assert (REFACTOR_WRITER.get("revert_check") or {}) == {}
    # The control: story-workflow's implementer answers both with something.
    assert STORY_WRITER.get("may_not_create", []) != []
    assert (STORY_WRITER.get("revert_check") or {}) != {}


# --------------------------------------------------------------------------
# The two prompts this story adds, and the ones it leaves alone
# --------------------------------------------------------------------------

REFACTOR_IMPLEMENTER = "refactor-implementer.md"
REFACTOR_VERIFIER = "refactor-verifier.md"

#: The prompts that existed before this story, and which it must not touch.
#: These are the names those files carried at the revisions story-070's commit
#: range spans, and they are deliberately *not* carried forward by story-071's
#: rename: the constant is used once, as a git pathspec over that fixed
#: historical range, where `prompts/story-implementer.md` and its two siblings
#: do not exist and would therefore match nothing. A pathspec naming a file
#: absent at both bounds of a range turns an emptiness assertion into one that
#: cannot report, which is why the historical spelling is the correct one here.
PRE_EXISTING_PROMPTS = ("implementer.md", "tester.md", "verifier.md",
                        "documenter.md", "planner.md")


def prompt_text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def test_the_two_new_prompts_are_the_ones_the_workflows_stages_name():
    assert REFACTOR_WRITER["prompt"] == REFACTOR_IMPLEMENTER
    assert REFACTOR_STAGES[-1]["prompt"] == REFACTOR_VERIFIER
    for name in (REFACTOR_IMPLEMENTER, REFACTOR_VERIFIER):
        assert (PROMPTS / name).is_file()


def test_the_implementer_prompt_says_test_edits_are_its_work_and_the_census_governs():
    text = prompt_text(REFACTOR_IMPLEMENTER).lower()
    assert "census" in text
    assert "existing test" in text or "existing tests" in text


def test_the_implementer_prompt_names_no_tester_stage_and_no_revert_check():
    """The two things story-workflow's implementer is told and this one is not.

    The control is that prompt, which says both: a scan that had stopped
    matching would report the pair absent from it too."""
    shipped = prompt_text("story-implementer.md").lower()
    assert "tester stage" in shipped
    assert "revert check" in shipped

    text = prompt_text(REFACTOR_IMPLEMENTER).lower()
    assert "tester" not in text
    assert "revert check" not in text


def test_the_verifier_prompt_asks_whether_the_change_is_behaviour_preserving():
    text = prompt_text(REFACTOR_VERIFIER).lower()
    assert "behaviour-preserving" in text or "behavior-preserving" in text


#: The categories a verifier prompt may name are the ones its own workflow
#: declares routes for. Derived from each definition rather than written here.
def categories_of(stages: list[dict]) -> set[str]:
    return {route.category for route in context_assembler.retry_routes(stages)}


def test_the_verifier_prompt_names_no_category_its_workflow_does_not_declare():
    """The routing table reaches the prompt through a placeholder, so the
    template names no category at all; what it must not do is speak of one in
    prose. The control is the category story-workflow declares and this
    workflow does not, which is exactly the one a copied prompt would carry."""
    refactor_categories = categories_of(REFACTOR_STAGES)
    story_categories = categories_of(STORY_WORKFLOW["stages"])
    dropped = story_categories - refactor_categories
    assert dropped, "the two workflows declare the same categories"

    text = prompt_text(REFACTOR_VERIFIER).lower()
    for category in sorted(dropped):
        assert f"{category} category" not in text, category
    assert "{{retry_routes}}" in prompt_text(REFACTOR_VERIFIER)


def test_the_verifier_prompt_says_this_workflow_has_no_validation_category():
    """Not merely silent about it: a verifier that met a defect in the tests
    themselves would otherwise have to guess."""
    text = prompt_text(REFACTOR_VERIFIER).lower()
    assert "declares no category" in text


# --------------------------------------------------------------------------
# What this story left alone
#
# These read this repository's own commit graph, because this repository is
# their subject: the claim is about what *this story* changed. Each is paired
# with a path the story did change, so a resolution that had stopped seeing
# anything would fail rather than report a clean sheet.
# --------------------------------------------------------------------------


def this_story_diff(paths: list[str]) -> str:
    return story_diff(paths, validation_file=Path(__file__))


#: The control every emptiness below is read against: a path this story changed
#: *in place*. A path it merely added is the wrong control while the story is in
#: flight — the endpoint of the range is then the working tree, where an added
#: file is untracked and shows in no diff — so the one file that is edited
#: rather than created carries the control for all three.
CHANGED_IN_PLACE = COORDINATOR_REL


def test_the_resolution_this_story_is_bounded_at_sees_what_it_changed():
    """The control the three assertions below share, stated once and first.

    Each of them asserts an emptiness, and an emptiness means nothing from a
    resolution that has stopped resolving. This is the same call, over the file
    the story edited, required to be non-empty."""
    assert this_story_diff([CHANGED_IN_PLACE]) != ""


def test_this_story_changed_no_prompt_that_existed_before_it():
    assert this_story_diff([f"prompts/{name}" for name in PRE_EXISTING_PROMPTS]) == ""


#: A module whose own story's commit range edited one of the prompts above,
#: under the spelling that prompt carried then. It is named rather than pinned
#: to a revision, so the control survives a rebase or a squash exactly as every
#: other bounded assertion here does.
PROMPT_EDITING_STORY = Path(__file__).with_name("test_coordinator_runs_the_suite.py")


def test_the_prompt_pathspecs_report_over_a_story_that_did_edit_one():
    """The control the assertion above needs, and the shared control cannot give.

    The shared control is read over `COORDINATOR_REL`, so it shows only that the
    resolution still resolves; it says nothing about whether *these* pathspecs
    can match. This is the same call over the same pathspecs, bounded at a story
    that did edit one of those prompts, required to be non-empty — and the same
    call under story-071's new spellings, required to be empty, which is what
    makes naming the historical spelling above load-bearing rather than
    cosmetic."""
    historical = [f"prompts/{name}" for name in PRE_EXISTING_PROMPTS]
    assert story_diff(historical, validation_file=PROMPT_EDITING_STORY) != ""
    renamed = [f"prompts/story-{name}" for name in PRE_EXISTING_PROMPTS[:3]]
    assert story_diff(renamed, validation_file=PROMPT_EDITING_STORY) == ""


def test_this_story_left_the_story_workflow_definition_exactly_as_it_was():
    assert this_story_diff(["workflows/story-workflow.json"]) == ""


def test_this_story_edited_no_story_artifact():
    assert this_story_diff([".harness/stories/"]) == ""


def test_the_resolution_reports_a_story_that_did_touch_those_paths(tmp_path):
    """The control for the three assertions above, taken further: the same
    resolution, run against a repository built here in which one story *did*
    violate the paths, reports it. Without this, three empty diffs would be
    equally consistent with a resolution that had stopped resolving."""
    root = conftest.constructed_story(
        tmp_path, respected=["docs/"],
        violated=["workflows/story-workflow.json", ".harness/stories/"])
    assert conftest.constructed_story_diff(root, ["docs/"]) == ""
    assert conftest.constructed_story_diff(
        root, ["workflows/story-workflow.json"]) != ""
    assert conftest.constructed_story_diff(root, [".harness/stories/"]) != ""
