"""Independent validation for story-037: a stage's baseline is what that stage
first found.

The subject is a *baseline*, so almost nothing here is asserted from source. A
target repository with a real pytest suite is built under tmp_path, fake stage
agents edit its working tree, and the coordinator is run. Whether an edit is
permitted is then whatever the suite does in a clone with that edit restored —
the same question the check asks, answered by running it.

The defect is reproduced before the fix is asserted. story-036's run escalated
on "the suite still passes with those edits reverted" because the implementer's
attempt-2 baseline already held attempt 1's edits, so reverting attempt 2 rolled
the tree back only as far as attempt 1's and the suite passed.
`pre_story_coordinator` reconstructs that code — today's coordinator with the
attempt component and the capture-once-per-directory early exit put back — and
`test_the_pre_story_code_refuses_the_same_run` drives the *identical* run
through it and watches it refuse for exactly that reason. Every claim below
about the two-attempt case being decided honestly is read against that
reproduction. What a refusal *does* moved with story-106 — the edit is undone
in the working tree and the run carries on rather than stopping — so what is
read of the pre-story reproduction is the verdict and what it threw away
rather than an exit status.

Every absence asserted here carries a control:

  * "the retry's edits are permitted" sits beside the same run through the
    pre-story code, which refuses and undoes them — so a check that permitted
    everything could not produce both;
  * "the fix did not blanket-permit retries" is its own run, in which the
    retry's unforced edit lands on a path the stage did *not* touch on attempt
    1 and is refused — so the permission above is about what the baseline
    holds, not about being on a retry;
  * "a capture does not overwrite what the baseline holds" sits beside a fresh
    capture into another run directory, which does see the edit, and beside a
    path new since the first capture, which the same call adds;
  * "the baseline path carries no attempt component" is paired with the
    pre-story path built by the same function in the mutant, which does;
  * "no attempt number is derived for the baseline" is a scan paired with the
    pre-story source it does match;
  * "a governed path present in the baseline is restored" sits beside the same
    clone built from a baseline lacking that path, which *deletes* it and
    returns the opposite verdict;
  * "the resumed stage captures nothing new" is paired with a fresh capture
    taken at the moment of the resume, which holds the changed content;
  * "the baseline is not in state.json" is paired with the run directory that
    does hold it;
  * "no stage name or declared name is in the code" is paired with the names
    that legitimately are.

Nothing here invokes a model: every run goes through a fake agent runner and
every clone source is a local filesystem path.
"""
import inspect
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE as PRE_STORY_BOUND, ENDPOINT, STORY,
                      first_retry_route, load_mutant)
import conftest

import harness_config
import schema_validator
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orchestration"
COORDINATOR_REL = "orchestration/story_coordinator.py"
COORDINATOR_PATH = REPO_ROOT / COORDINATOR_REL

#: The tests directory the target below configures, and the prefix the writing
#: stage declares it may not create. Written once so the config and the
#: declaration cannot drift apart, and written resolved rather than as the
#: `{{tests_dir}}` token a deployed definition carries: several cases here call
#: `capture_stage_baseline` with the declaration directly, outside the load
#: that expands tokens.
TESTS_DIR = "tests/"

#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is *the pre-stage
#: baseline* — when it is captured, what it holds, how a re-capture merges and
#: what the revert check decides against it — and the stage list is an input to
#: that question. What the baseline mechanism needs is a stage declaring a
#: revert check over a governed prefix and a stage that writes into that
#: prefix, which is what is built here; how many stages this repository happens
#: to deploy has nothing to say about any of it.
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
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    #: Named so that it shares no substring with the baseline directory the
    #: first stage declares above. story-069 made state.json record the name of
    #: the workflow a run loaded, so a fixture workflow named after the baseline
    #: would put that token into state.json without the baseline ever being
    #: recorded there -- and the absence asserted below would redden for a
    #: reason that has nothing to do with what it watches. The guard that keeps
    #: the two names disjoint sits beside that assertion.
    name="pre-stage-capture-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES
IMPLEMENTER_STAGE = WORKFLOW["stages"][0]

#: Both names are read off the declaration, never spelled here, for the same
#: reason the coordinator may not spell them.
DECLARATION = IMPLEMENTER_STAGE["revert_check"]
ARTIFACT = DECLARATION["result"]
BASELINE = DECLARATION["baseline"]
PREFIX = IMPLEMENTER_STAGE["may_not_create"][0]

SCHEMA_STEM = "revert-check-result"

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
# --------------------------------------------------------------------------

APP_AT_HEAD = '''\
def greet(name):
    return f"hello, {name}"
'''

#: The rename attempt 1 makes. The pre-existing test cannot survive it, so the
#: test edit that accompanies it is forced.
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

#: Coverage nothing forced, appended by attempt 2 on top of attempt 1's repair.
#: It passes against the tree as attempt 1 left it and as attempt 2 leaves it,
#: so reverting *it alone* costs nothing — which is the whole reason the
#: pre-story code escalated on this shape.
FREE_COVERAGE = '''

def test_salute_again():
    assert salute("again") == "hello, again"
'''

TEST_APP_WITH_FREE_COVERAGE = TEST_APP_REPAIRED + FREE_COVERAGE

TESTS_CONFTEST_AT_HEAD = '''\
import pytest
'''

#: An unused fixture: appended to tests/conftest.py it is an edit nothing
#: forced, on a path attempt 1 never touched.
TESTS_CONFTEST_WITH_FIXTURE = TESTS_CONFTEST_AT_HEAD + '''

@pytest.fixture
def unused():
    return 42
'''

#: The file the tester creates during the run, in its broken form: the case a
#: governed path first appearing *between* two invocations of the implementer
#: reduces to.
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
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "baseline-harness")


# --------------------------------------------------------------------------
# The stage edits, each paired with the record that describes it.
# --------------------------------------------------------------------------

NO_CHANGES = {"modified": [], "created": [], "deleted": []}


def unchanged(root: Path, run_dir: Path) -> dict:
    return dict(NO_CHANGES)


def module_only(root: Path, run_dir: Path) -> dict:
    """An implementer change naming no path under the governed prefix."""
    write(root / "src" / "app.py", APP_ADDITIVE)
    return {"modified": ["src/app.py"], "created": [], "deleted": []}


def forced_repair(root: Path, run_dir: Path) -> dict:
    """Attempt 1: a rename the pre-existing test cannot survive, and its repair."""
    write(root / "src" / "app.py", APP_RENAMED)
    write(root / "tests" / "test_app.py", TEST_APP_REPAIRED)
    return {"modified": ["src/app.py", "tests/test_app.py"], "created": [],
            "deleted": []}


def appends_free_coverage(root: Path, run_dir: Path) -> dict:
    """Attempt 2: more of the same governed file, and nothing forced this time.

    This is story-036's shape reduced: a retry that edits a file it also edited
    on the previous attempt.
    """
    write(root / "tests" / "test_app.py", TEST_APP_WITH_FREE_COVERAGE)
    return {"modified": ["tests/test_app.py"], "created": [], "deleted": []}


def appends_an_unused_fixture(root: Path, run_dir: Path) -> dict:
    """Attempt 2 on a governed path attempt 1 never touched, unforced."""
    write(root / "tests" / "conftest.py", TESTS_CONFTEST_WITH_FIXTURE)
    return {"modified": ["tests/conftest.py"], "created": [], "deleted": []}


def creates_the_broken_new_test(root: Path, run_dir: Path) -> dict:
    """The tester writes a governed file this run, and gets it wrong.

    Untracked and uncommitted for the whole of the run: the coordinator commits
    once, at _complete.
    """
    write(root / "tests" / "test_new.py", TEST_NEW_BROKEN)
    return {"modified": [], "created": ["tests/test_new.py"], "deleted": []}


def repairs_the_new_test(root: Path, run_dir: Path) -> dict:
    """The retried implementer fixes the file the tester wrote this run."""
    write(root / "tests" / "test_new.py", TEST_NEW_REPAIRED)
    return {"modified": ["tests/test_new.py"], "created": [], "deleted": []}


def repair_then_discard_the_baseline(root: Path, run_dir: Path) -> dict:
    """A forced repair whose baseline is gone by the time the check looks."""
    record = forced_repair(root, run_dir)
    directory = run_dir / BASELINE
    if directory.is_dir():
        shutil.rmtree(directory)
    return record


class Runner:
    """A fake agent runner: each stage writes its artifacts, and a stage
    holding an edit also makes that edit in the target's working tree.

    `edits` maps a stage to the list of edits it makes, one per invocation; the
    last entry repeats. `verdicts` is the verifier's, read the same way.
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

    def _nth(self, sequence: list, index: int):
        return sequence[min(index, len(sequence) - 1)]

    def _record(self, stage: str) -> dict:
        seen = self.calls.count(stage) - 1
        edit = self._nth(self.edits.get(stage, [unchanged]), seen)
        return edit(self.target_root, self.run_dir)

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
        coordinator=story_coordinator) -> tuple[int, Runner]:
    """One run, through the real coordinator or through a mutant of it."""
    runner = Runner(target_root, edits, verdicts, interrupt)
    code = coordinator.run_story("story-001", harness, target_root, runner)
    return code, runner


def baseline_at(target_root: Path, stage: str = WRITING) -> Path:
    """The baseline the coordinator captured for one stage."""
    return story_coordinator.stage_baseline_dir(
        run_dir_of(target_root), BASELINE, stage)


def capture(target_root: Path, scratch: Path, prefix: str = PREFIX,
            stage: str = WRITING, accounted_for: set | None = None) -> Path:
    """Capture a baseline the way the coordinator captures one, into scratch.

    `accounted_for` is what another stage's changed-files record names, which
    story-036 made the merge's admission rule: a re-capture adds a path only
    when another stage accounts for it. A caller passing nothing states that no
    other stage recorded anything, which is a first capture's situation.
    """
    return story_coordinator.capture_stage_baseline(
        scratch, target_root, BASELINE, stage, [prefix],
        accounted_for=accounted_for or set())


def contents_of(directory: Path) -> dict:
    """Every file the baseline holds, keyed by its repository-relative path."""
    return {path.relative_to(directory).as_posix(): path.read_text()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def suite_in(directory: Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=directory, capture_output=True, text=True,
    ).returncode


def scratch_suite(root: Path, app: str, test_app: str) -> int:
    """The target's suite reconstructed at a given pair of contents."""
    write(root / "conftest.py", ROOT_CONFTEST)
    write(root / "src" / "app.py", app)
    write(root / "tests" / "conftest.py", TESTS_CONFTEST_AT_HEAD)
    write(root / "tests" / "test_app.py", test_app)
    return suite_in(root)


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


# --------------------------------------------------------------------------
# The pre-story coordinator, reconstructed by putting back what this story
# removed: the attempt component of the baseline path, and the capture-once
# early exit that returned an existing directory untouched.
#
# Built out of today's source rather than loaded from git history, because a
# coordinator recovered from history runs against today's workflow, schemas and
# config and stops running as soon as any of those legitimately changes — which
# is why `conftest.load_mutant` takes a working-tree path.
# --------------------------------------------------------------------------

NEW_DIR_SIGNATURE = \
    "def stage_baseline_dir(run_dir: Path, baseline: str, stage_name: str) -> Path:"
OLD_DIR_SIGNATURE = (
    "def stage_baseline_dir(\n"
    "    run_dir: Path, baseline: str, stage_name: str, attempt: int\n"
    ") -> Path:"
)

NEW_DIR_RETURN = "    return run_dir / baseline / stage_name\n"
OLD_DIR_RETURN = '    return run_dir / baseline / f"{stage_name}-attempt-{attempt}"\n'

NEW_CAPTURE_SIGNATURE = """def capture_stage_baseline(
    run_dir: Path,
    target_root: Path,
    baseline: str,
    stage_name: str,
    prefixes: list[str],
    *,
    accounted_for: set[str],
) -> Path:"""
OLD_CAPTURE_SIGNATURE = """def capture_stage_baseline(
    run_dir: Path,
    target_root: Path,
    baseline: str,
    stage_name: str,
    attempt: int,
    prefixes: list[str],
) -> Path:"""

NEW_CAPTURE_HEAD = """    directory = stage_baseline_dir(run_dir, baseline, stage_name)
    recapture = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
"""
OLD_CAPTURE_HEAD = """    directory = stage_baseline_dir(run_dir, baseline, stage_name, attempt)
    if directory.exists():
        return directory
    directory.mkdir(parents=True)
"""

#: Re-indented by story-106, which made the capture take one listing over
#: every pathspec asked for instead of one listing per pathspec — a
#: confinement's pathspecs are the whole tree and a subtraction from it, and
#: unioning per-pathspec listings would add back exactly what the subtraction
#: removed. The loop body lost a level of nesting with the outer loop, and the
#: mutation's meaning is unchanged: it takes away the first-seen rule and the
#: authorship narrowing this module's story added.
NEW_FIRST_SEEN = """        destination = directory / rel
        if destination.exists():
            continue
        if recapture and rel not in accounted_for:
            continue
"""
OLD_FIRST_SEEN = """        destination = directory / rel
"""

#: Repointed by story-070, which moved the capture behind a comprehension
#: over the baselines a stage's declarations ask for, so the arguments the
#: substitution has to reach are spelled as the comprehension's variables
#: rather than read off one declaration. The mutation's meaning is unchanged:
#: it puts back the attempt the pre-story signature took positionally and
#: takes away the authorship narrowing it did not have.
NEW_CALL_SITE = """                baseline,
                name,
                prefixes,
                accounted_for=recorded_by_other_stages(run_dir, stages, name),
"""
OLD_CALL_SITE = """                baseline,
                name,
                attempt,
                prefixes,
"""

PRE_STORY_SUBSTITUTIONS = (
    (NEW_DIR_SIGNATURE, OLD_DIR_SIGNATURE),
    (NEW_DIR_RETURN, OLD_DIR_RETURN),
    (NEW_CAPTURE_SIGNATURE, OLD_CAPTURE_SIGNATURE),
    (NEW_CAPTURE_HEAD, OLD_CAPTURE_HEAD),
    (NEW_FIRST_SEEN, OLD_FIRST_SEEN),
    (NEW_CALL_SITE, OLD_CALL_SITE),
)


def pre_story_coordinator(tmp_path: Path):
    """Today's coordinator with the attempt keying and capture-once put back."""
    return load_mutant(COORDINATOR_PATH, list(PRE_STORY_SUBSTITUTIONS),
                       name="coordinator_before_story_037", tmp_path=tmp_path)


def pre_story_source() -> str:
    """The same reconstruction as text, for the source-level controls below."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")
    for old, new in PRE_STORY_SUBSTITUTIONS:
        assert old in source, old
        source = source.replace(old, new, 1)
    return source


# --------------------------------------------------------------------------
# The premises: what makes "permitted" and "escalated" mean anything here
# --------------------------------------------------------------------------

#: The run shape story-036 escalated on, reduced: the implementer edits a
#: governed file on attempt 1 and edits it further on attempt 2.
TWO_ATTEMPT_SHAPE = {WRITING: [forced_repair, appends_free_coverage]}


def test_reverting_the_stages_whole_edit_to_the_governed_file_fails_the_suite(
    tmp_path,
):
    """The premise under "permitted": restoring tests/test_app.py to what the
    implementer *first* found leaves a test calling a function the renamed
    module no longer has, so the suite fails and the aggregate edit is forced.

    The control is the same tree with the stage's own final content, which
    passes — so the failure is the revert and not a broken scratch tree.
    """
    assert scratch_suite(tmp_path / "reverted", APP_RENAMED, TEST_APP_AT_HEAD) != 0
    assert scratch_suite(tmp_path / "intact", APP_RENAMED,
                         TEST_APP_WITH_FREE_COVERAGE) == 0


def test_reverting_only_the_second_attempts_edit_costs_nothing(tmp_path):
    """The premise under the pre-story escalation: attempt 2's appended
    coverage passes against the tree before and after it, so a check reverting
    to attempt 1's content sees a passing suite and calls the edit unforced.

    This is precisely what the attempt-keyed baseline made the check ask.
    """
    assert scratch_suite(tmp_path / "attempt-1", APP_RENAMED,
                         TEST_APP_REPAIRED) == 0
    assert scratch_suite(tmp_path / "attempt-2", APP_RENAMED,
                         TEST_APP_WITH_FREE_COVERAGE) == 0


# --------------------------------------------------------------------------
# The defect, reproduced: the pre-story code refuses this run's edit
# --------------------------------------------------------------------------


def test_the_pre_story_code_refuses_the_same_run(target, harness_root, tmp_path):
    """story-036's failure, driven rather than argued.

    The attempt-2 capture holds attempt 1's edits, so reverting attempt 2's
    edit rolls the tree back only as far as attempt 1's, the suite passes, and
    the check refuses forced work as unforced coverage it never actually
    tested. The verdict is the defect; where the run goes afterwards is a
    separate question this module has nothing to say about, and since
    story-106 a refusal undoes the edit rather than stopping the run. What is
    read here is therefore the verdict and the undoing, and the paired test
    below is the identical run under today's capture, where the same edit is
    permitted.
    """
    coordinator = pre_story_coordinator(tmp_path)
    code, runner = run(target, harness_root, TWO_ATTEMPT_SHAPE, [FAIL, PASS],
                       coordinator=coordinator)

    assert code == 0
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["exit_code"] == 0          # the suite passed with it reverted
    assert record["paths"] == ["tests/test_app.py"]
    # And the forced work was thrown away: the tree holds what the wrong
    # baseline said the stage started from.
    assert record["reverted"]["restored"] == ["tests/test_app.py"]
    assert (target / "tests" / "test_app.py").read_text() == TEST_APP_REPAIRED

    # And the reason it asked the wrong question: two attempt-keyed
    # directories, the second already holding attempt 1's content.
    run_dir = run_dir_of(target)
    assert (run_dir / BASELINE / f"{WRITING}-attempt-2" / "tests"
            / "test_app.py").read_text() == TEST_APP_REPAIRED
    assert not (run_dir / BASELINE / WRITING).exists()


# --------------------------------------------------------------------------
# The fix: a re-entered stage is decided against what it first found
# --------------------------------------------------------------------------


def test_the_second_attempts_edit_to_a_file_it_also_edited_is_permitted(
    target, harness_root,
):
    """The story's first two acceptance criteria, end to end and as one run.

    The identical run the pre-story code escalated on completes: the check
    restores tests/test_app.py to what the implementer found when it *first*
    ran, the suite fails, and the edit is permitted.
    """
    code, runner = run(target, harness_root, TWO_ATTEMPT_SHAPE, [FAIL, PASS])

    assert code == 0
    assert state_of(target)["status"] == "completed"
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["exit_code"] != 0          # the suite failed with it reverted
    assert record["paths"] == ["tests/test_app.py"]


def test_the_baseline_that_decision_was_made_against_holds_the_original_content(
    target, harness_root,
):
    """What the check decided against, read off the run directory: the content
    the governed path held before the stage's *first* invocation, not the
    content attempt 1 left.

    The control is the tree the run ended on, which holds neither — so this is
    a statement about the baseline rather than about a capture that happens to
    agree with whatever is on disk.
    """
    assert run(target, harness_root, TWO_ATTEMPT_SHAPE, [FAIL, PASS])[0] == 0

    captured = baseline_at(target) / "tests" / "test_app.py"
    assert captured.read_text() == TEST_APP_AT_HEAD
    assert captured.read_text() != TEST_APP_REPAIRED
    assert (target / "tests" / "test_app.py").read_text() \
        == TEST_APP_WITH_FREE_COVERAGE


def test_a_retry_editing_a_path_it_did_not_touch_before_is_still_refused(
    target, harness_root,
):
    """The control for the permission above, and the reason it is not a
    blanket exemption for retries.

    The same two-attempt run, with attempt 2's unforced edit landing on a
    governed path attempt 1 never touched. The baseline holds that path at what
    the stage first found it as — which is also what attempt 1 left it as — so
    reverting costs nothing, the suite passes, and the edit is refused and
    undone. Being on a retry buys nothing; what the baseline holds decides.
    """
    code, runner = run(target, harness_root,
                       {WRITING: [forced_repair, appends_an_unused_fixture]},
                       [FAIL, PASS])

    assert code == 0
    assert runner.calls == [*STAGE_NAMES, *STAGE_NAMES]

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["paths"] == ["tests/conftest.py"]
    assert record["reverted"]["restored"] == ["tests/conftest.py"]
    assert (target / "tests" / "conftest.py").read_text() \
        == TESTS_CONFTEST_AT_HEAD


# --------------------------------------------------------------------------
# A governed path that first appears between two invocations of the stage
# --------------------------------------------------------------------------

#: The implementer's first invocation touches no governed path; the tester then
#: creates one; the retried implementer repairs it. The path exists in neither
#: HEAD nor the stage's first capture.
BETWEEN_ATTEMPTS_SHAPE = {
    WRITING: [module_only, repairs_the_new_test],
    VALIDATING: [creates_the_broken_new_test, unchanged],
}


def test_a_path_created_between_two_invocations_is_captured_at_what_it_met(
    target, harness_root,
):
    """The merge's other half: the file the tester created after the
    implementer's first invocation is added to the implementer's one baseline
    directory, at the content the tester left, and the check decides the
    retry's repair of it as permitted — restored rather than deleted.

    The control is the file the first invocation *did* capture, which is still
    at its original content in the same directory: the merge added a path
    without disturbing one already held.
    """
    code, _ = run(target, harness_root, BETWEEN_ATTEMPTS_SHAPE, [FAIL, PASS])
    assert code == 0

    baseline = baseline_at(target)
    assert (baseline / "tests" / "test_new.py").read_text() == TEST_NEW_BROKEN
    assert (baseline / "tests" / "test_app.py").read_text() == TEST_APP_AT_HEAD

    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_new.py"]

    # It had no version at HEAD for the whole of the run: HEAD^ is where the
    # run started, and the run's own commit is _complete's, made after the
    # check had already decided. The control is the path that did have one.
    assert git(target, "cat-file", "-e", "HEAD^:tests/test_new.py",
               check=False).returncode != 0
    assert git(target, "cat-file", "-e", "HEAD^:tests/test_app.py",
               check=False).returncode == 0


def test_reusing_the_earlier_capture_whole_would_have_deleted_that_path(
    target, tmp_path,
):
    """The control for the test above, and the reason the merge is per path.

    A baseline taken before the tester created the file — which is what
    reusing the earlier attempt's directory wholesale would have handed the
    check — does not hold `tests/test_new.py`, so `_build_clone` *deletes* it
    rather than restoring it, and the suite then passes: the same repair the
    merged baseline permits would be escalated.
    """
    module_only(target, run_dir_of(target))
    stale = capture(target, tmp_path / "before-the-tester")
    created = creates_the_broken_new_test(target, run_dir_of(target))["created"]
    merged = capture(target, tmp_path / "after-the-tester",
                     accounted_for=set(created))
    repairs_the_new_test(target, run_dir_of(target))

    assert not (stale / "tests" / "test_new.py").exists()
    assert (merged / "tests" / "test_new.py").read_text() == TEST_NEW_BROKEN

    deleting = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "deleting",
        revert=["tests/test_new.py"], baseline=stale)
    restoring = story_coordinator.run_clean_clone(
        target, TEST_COMMAND, None, tmp_path / "restoring",
        revert=["tests/test_new.py"], baseline=merged)

    assert deleting.ran is True and deleting.exit_code == 0      # escalates
    assert restoring.ran is True and restoring.exit_code != 0    # permits
    assert not (Path(deleting.clone_path) / "tests" / "test_new.py").exists()
    assert (Path(restoring.clone_path) / "tests" / "test_new.py").read_text() \
        == TEST_NEW_BROKEN


# --------------------------------------------------------------------------
# A capture never overwrites what the baseline already holds
# --------------------------------------------------------------------------


def test_a_second_capture_keeps_what_the_first_recorded_and_adds_what_is_new(
    target, tmp_path,
):
    """Both halves of first-seen-wins, in one call.

    The control is a fresh capture into a run directory holding no baseline for
    this stage, which does see the edit — so the preservation above is the
    first-seen rule rather than a capture that has stopped reading the tree.
    """
    scratch = tmp_path / "run"
    first = capture(target, scratch)
    assert (first / "tests" / "test_app.py").read_text() == TEST_APP_AT_HEAD
    assert not (first / "tests" / "test_new.py").exists()

    forced_repair(target, run_dir_of(target))
    created = creates_the_broken_new_test(target, run_dir_of(target))["created"]
    again = capture(target, scratch, accounted_for=set(created))

    assert again == first
    assert (again / "tests" / "test_app.py").read_text() == TEST_APP_AT_HEAD
    assert (again / "tests" / "test_new.py").read_text() == TEST_NEW_BROKEN

    fresh = capture(target, tmp_path / "fresh")
    assert (fresh / "tests" / "test_app.py").read_text() == TEST_APP_REPAIRED


def test_the_pre_story_capture_would_have_dropped_the_path_new_since_the_first(
    target, tmp_path,
):
    """The control for the adding half above, which is the half a wholesale
    reuse of the earlier capture gets wrong.

    The pre-story capture returned an existing directory untouched, so the same
    two calls against the same directory leave `tests/test_new.py` out of the
    baseline entirely — and a governed path the baseline does not hold is
    deleted in the clone rather than restored.
    """
    coordinator = pre_story_coordinator(tmp_path)
    scratch = tmp_path / "run"

    def pre_story_capture() -> Path:
        return coordinator.capture_stage_baseline(
            scratch, target, BASELINE, WRITING, 1, [PREFIX])

    first = pre_story_capture()
    assert (first / "tests" / "test_app.py").read_text() == TEST_APP_AT_HEAD

    creates_the_broken_new_test(target, run_dir_of(target))
    again = pre_story_capture()

    assert again == first
    assert not (again / "tests" / "test_new.py").exists()
    # The control on the control: today's capture, over the same tree and the
    # same directory, does add it.
    assert (capture(target, tmp_path / "today") / "tests"
            / "test_new.py").read_text() == TEST_NEW_BROKEN


def test_today_neither_function_accepts_the_attempt_the_pre_story_ones_required(
    target, tmp_path,
):
    """The behavioural form of the signature change: the pre-story call, made
    with the attempt argument it required, is a TypeError today.

    The control is the same call against the pre-story reconstruction, which
    accepts it — so the rejection below is about today's signature rather than
    about a call that was malformed either way.
    """
    coordinator = pre_story_coordinator(tmp_path)
    assert coordinator.capture_stage_baseline(
        tmp_path / "before", target, BASELINE, WRITING, 1, [PREFIX]).is_dir()
    assert coordinator.stage_baseline_dir(tmp_path / "before", BASELINE,
                                          WRITING, 1).name.endswith("-1")

    with pytest.raises(TypeError):
        story_coordinator.capture_stage_baseline(
            tmp_path / "now", target, BASELINE, WRITING, 1, [PREFIX])
    with pytest.raises(TypeError):
        story_coordinator.stage_baseline_dir(tmp_path / "now", BASELINE,
                                            WRITING, 1)


def test_the_baseline_directory_exists_even_when_it_captures_nothing(
    target, tmp_path,
):
    """The merge replaced an early exit that also created the directory, so
    the property is re-asserted here: its existence answers "was a baseline
    taken", and an absent one is the distinct condition the check refuses on.

    The control is the same call over a prefix that has files.
    """
    empty = capture(target, tmp_path / "run", prefix="nothing-here/")
    assert empty.is_dir()
    assert list(empty.rglob("*")) == []
    assert (capture(target, tmp_path / "run", stage="other") / "tests").is_dir()


def test_the_captured_set_is_still_tracked_plus_untracked_under_the_prefix(
    target, tmp_path,
):
    """A file created earlier in the run and never committed is still captured.

    The control is the tracked listing, which does not contain it: a capture
    built on `--cached` alone would have held nothing for that path, and the
    restore would have deleted it.
    """
    creates_the_broken_new_test(target, run_dir_of(target))
    baseline = capture(target, tmp_path / "run")

    assert set(contents_of(baseline)) == {
        "tests/conftest.py", "tests/test_app.py", "tests/test_new.py"}
    tracked = git(target, "ls-files", "--", PREFIX).stdout.split()
    assert "tests/test_new.py" not in tracked
    assert "tests/test_app.py" in tracked


# --------------------------------------------------------------------------
# What a confinement asks the capture for
#
# A create restriction is governed at or beneath each prefix and asks for
# that. A confinement is governed everywhere *else* and asks for the
# repository minus the prefixes it names — which is what brings a baseline
# with it, and the half a create restriction's baseline never had to cover.
#
# The pair below is the whole of the difference: the same stage, the same
# revert-check declaration, the same prefix, and the request under each sense
# read off `stage_baseline_requests` rather than composed here.
# --------------------------------------------------------------------------


def declaring(sense: str, prefix: str = PREFIX) -> dict:
    """A stage declaring the revert check and one restriction of `sense`."""
    return {"name": WRITING, "revert_check": DECLARATION, sense: [prefix]}


def test_a_confinement_asks_for_the_repository_outside_the_paths_it_names():
    """Asserted against the create restriction's request, in the same test.

    Neither pathspec list is written here — both are what the function
    returned — so what this states is that the two senses ask for *different*
    things and that the confinement's answer subtracts the prefix rather than
    selecting it.
    """
    confined = story_coordinator.stage_baseline_requests(
        declaring(story_coordinator.CONFINEMENT))[BASELINE]
    creating = story_coordinator.stage_baseline_requests(
        declaring(story_coordinator.CREATE_RESTRICTION))[BASELINE]

    assert creating == [PREFIX]
    assert confined != creating
    assert any(PREFIX in pathspec and "exclude" in pathspec
               for pathspec in confined)


def test_the_capture_a_confinement_asks_for_holds_the_tree_outside_it(
    target, tmp_path,
):
    """Driven through the real capture, not through the request alone.

    The tree the fixture builds has files on both sides of the prefix, and the
    capture is asserted to hold every one outside it and none inside — beside
    the create restriction's capture over the same tree, which holds exactly
    the opposite set. Two captures, one tree, and the sense is the only
    difference between them.
    """
    def captured(sense: str) -> set[str]:
        directory = story_coordinator.capture_stage_baseline(
            tmp_path / sense, target, BASELINE, WRITING,
            story_coordinator.stage_baseline_requests(
                declaring(sense))[BASELINE],
            accounted_for=set())
        return set(contents_of(directory))

    outside = captured(story_coordinator.CONFINEMENT)
    inside = captured(story_coordinator.CREATE_RESTRICTION)

    assert inside
    assert outside
    assert all(path.startswith(PREFIX) for path in inside)
    assert not any(path.startswith(PREFIX) for path in outside)
    # The tree really does hold files on both sides, so neither half above is
    # an empty set agreeing with anything.
    assert "src/app.py" in outside
    assert outside & inside == set()


def test_a_stage_declaring_neither_sense_asks_for_nothing():
    """The control for both: the same declaration with no restriction on it
    asks for an empty capture, so what the two above report is the restriction
    and not the revert-check declaration alone."""
    requests = story_coordinator.stage_baseline_requests(
        {"name": WRITING, "revert_check": DECLARATION})
    assert requests == {BASELINE: []}


# --------------------------------------------------------------------------
# A resumed run reuses the stage's stored baseline
# --------------------------------------------------------------------------


def test_a_resumed_stage_captures_nothing_new_and_decides_against_the_stored_one(
    target, harness_root, tmp_path,
):
    """A run interrupted inside the implementer and resumed there. The stage is
    re-entered with its edits already in the tree; the merge records nothing,
    and the reused baseline is what makes the decision honest.

    The control is a fresh capture taken at the moment of the resume, which
    holds the interrupted stage's edit — so an overwriting capture would have
    produced a different baseline and reversed the decision.
    """
    with pytest.raises(KeyboardInterrupt):
        run(target, harness_root, {WRITING: [forced_repair]},
            interrupt=(WRITING, 1))
    assert state_of(target)["status"] == "running"
    assert (target / "tests" / "test_app.py").read_text() == TEST_APP_REPAIRED

    stored = contents_of(baseline_at(target))
    assert stored["tests/test_app.py"] == TEST_APP_AT_HEAD
    fresh = contents_of(capture(target, tmp_path / "at-the-resume"))
    assert fresh["tests/test_app.py"] == TEST_APP_REPAIRED

    code, resumed = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 0
    assert resumed.calls[0] == WRITING
    assert contents_of(baseline_at(target)) == stored

    record = record_of(target)
    assert record["permitted"] is True
    assert record["baseline"] == str(baseline_at(target))


# --------------------------------------------------------------------------
# The path carries no attempt component, and neither signature takes one
# --------------------------------------------------------------------------


def test_neither_function_takes_an_attempt_argument():
    """The control is the parameter list each function does take, asserted
    whole: an absence read off a signature that had stopped being resolved
    could not also match these."""
    assert list(inspect.signature(story_coordinator.stage_baseline_dir)
                .parameters) == ["run_dir", "baseline", "stage_name"]
    assert list(inspect.signature(story_coordinator.capture_stage_baseline)
                .parameters) == ["run_dir", "target_root", "baseline",
                                 "stage_name", "prefixes", "accounted_for"]


def test_the_baseline_path_is_the_declared_name_and_the_stage_and_nothing_else(
    tmp_path,
):
    """The control is the same function in the pre-story coordinator, which
    builds the attempt-keyed path from the same three arguments — so the
    absence below is about today's keying and not about a comparison that
    could not differ.
    """
    run_dir = tmp_path / "run"
    now = story_coordinator.stage_baseline_dir(run_dir, BASELINE, WRITING)
    assert now == run_dir / BASELINE / WRITING
    assert now.relative_to(run_dir).parts == (BASELINE, WRITING)
    assert "attempt" not in str(now.relative_to(run_dir))

    before = pre_story_coordinator(tmp_path).stage_baseline_dir(
        run_dir, BASELINE, WRITING, 2)
    assert "attempt" in str(before.relative_to(run_dir))


def _baseline_regions(module_source: str) -> dict[str, str]:
    """The three places an attempt number for the baseline could be derived:
    the keying function, the capture, and the capture's call site in run_story.
    """
    keying, _, rest = module_source.partition("def capture_stage_baseline(")
    capture, _, after = rest.partition("\n@dataclass")
    _, _, call_site = after.partition('declaration = stage.get("revert_check")')
    return {
        "stage_baseline_dir": executable_source(
            "def stage_baseline_dir("
            + keying.rpartition("def stage_baseline_dir(")[2]),
        "capture_stage_baseline": executable_source(capture),
        "the call site": executable_source(
            call_site.partition("result = runner(")[0]),
    }


def test_no_attempt_number_is_derived_for_the_baseline_anywhere():
    """Neither function names an attempt, and neither does the capture's call
    site in run_story.

    The scan is paired with the pre-story source, in which all three regions do
    name one — so a scan that had stopped seeing anything could not pass both
    halves.
    """
    today = _baseline_regions(COORDINATOR_PATH.read_text(encoding="utf-8"))
    before = _baseline_regions(pre_story_source())

    for where, body in today.items():
        assert "stage_baseline_dir" in body or "baseline" in body, where
        assert "attempt" not in body, where
    for where, body in before.items():
        assert "attempt" in body, where      # the scan can fail


# --------------------------------------------------------------------------
# The record names the stage-keyed directory, and the refusal is unchanged
# --------------------------------------------------------------------------


def test_the_records_baseline_field_names_the_stage_keyed_directory(
    target, harness_root,
):
    assert run(target, harness_root, TWO_ATTEMPT_SHAPE, [FAIL, PASS])[0] == 0
    record = record_of(target)
    run_dir = run_dir_of(target)

    assert record["baseline"] == str(run_dir / BASELINE / WRITING)
    assert Path(record["baseline"]).is_dir()
    assert "attempt" not in Path(record["baseline"]).relative_to(run_dir).as_posix()
    assert schema_validator.validate(
        record, schema_validator.load_schema(SCHEMA_STEM)) == []


def test_a_stage_declaring_the_check_with_no_baseline_still_escalates_naming_it(
    target, harness_root,
):
    """Unchanged and still reachable: the merge creates the directory rather
    than requiring it, so a run whose baseline is removed after the capture
    still reaches the refusal."""
    code, _ = run(target, harness_root,
                  {WRITING: [repair_then_discard_the_baseline]})
    assert code == 2
    record = record_of(target)
    assert record["ran"] is False
    assert "permitted" not in record
    assert "baseline" not in record
    assert BASELINE in record["reason"]
    _, summary = evidence(target)
    assert "could not run" in summary


def test_the_same_run_with_its_baseline_intact_decides(target, harness_root):
    """The control for the refusal above: the identical edit, and the only
    difference is that the baseline is still there."""
    code, _ = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 0
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert BASELINE in record["baseline"]


# --------------------------------------------------------------------------
# The decision rule and the restore semantics are untouched
# --------------------------------------------------------------------------


def test_the_check_and_the_clone_builder_are_byte_for_byte_pre_story():
    """Only the state reverted to changed. The control is the capture, in the
    same file at the same bound, which did change — so a comparison that had
    stopped resolving anything could not pass both halves.

    Both bounds are this story's own commit range. The after side read the
    *working tree* until the-interpreter-is-not-assumed-to-be-python renamed
    the record's interpreter field, which this story has nothing to say
    about — the standing HEAD-baseline trap, repaired the standing way by
    bounding the comparison at both ends rather than by relaxing it.
    """
    def at(name: str, bound: str) -> str:
        # Both bounds are frozen past texts, carried as committed fixtures
        # since story-053 rather than resolved out of this repository's commit
        # graph — where a squash makes the range unresolvable in a clone and a
        # rename empties it silently, neither of which is a property of what
        # the story changed.
        assert bound in (PRE_STORY_BOUND, ENDPOINT), bound
        return conftest.history_fixture(
            f"story_coordinator.{name}.at-story-037-{bound}.py.txt")

    for name in ("revert_check", "_build_clone", "_revert_check_permitted",
                 "run_clean_clone", "governed_edits"):
        assert at(name, PRE_STORY_BOUND) == at(name, ENDPOINT), name

    assert at("capture_stage_baseline", PRE_STORY_BOUND) \
        != at("capture_stage_baseline", ENDPOINT)
    assert at("stage_baseline_dir", PRE_STORY_BOUND) \
        != at("stage_baseline_dir", ENDPOINT)


def test_a_forced_edit_and_an_unforced_one_on_a_single_attempt_still_decide(
    target, harness_root,
):
    """The decision rule, exercised at the shape that has nothing to do with
    retries: permitted exactly when reverting makes the suite fail."""
    code, _ = run(target, harness_root, {WRITING: [forced_repair]})
    assert code == 0
    assert record_of(target)["permitted"] is True


def test_an_unforced_edit_on_a_single_attempt_is_still_refused(
    target, harness_root,
):
    """The other half of the decision rule, at the same shape: refused exactly
    when reverting leaves the suite passing — and then undone rather than
    escalated, which is the disposition and not the verdict."""
    code, _ = run(target, harness_root,
                  {WRITING: [appends_an_unused_fixture]})
    assert code == 0
    record = record_of(target)
    assert record["permitted"] is False
    assert record["paths"] == ["tests/conftest.py"]
    assert record["reverted"]["restored"] == ["tests/conftest.py"]


# --------------------------------------------------------------------------
# The baseline is evidence, never state; and the code names nothing
# --------------------------------------------------------------------------


def test_state_json_gains_no_field_and_never_names_the_baseline(
    target, harness_root,
):
    assert run(target, harness_root, TWO_ATTEMPT_SHAPE, [FAIL, PASS])[0] == 0
    state_text = (run_dir_of(target) / "state.json").read_text()
    fields = set(json.loads(state_text))

    # The guard the fixture's name is chosen for: state.json legitimately
    # records the workflow the run loaded, so the absence below only says
    # anything about the baseline while the two fixture names stay disjoint.
    assert BASELINE not in WORKFLOW["name"]

    assert [name for name in fields if "baseline" in name] == []
    assert BASELINE not in state_text
    # The control: the run this state describes did capture a baseline, so the
    # absence above is about state.json and not about a run that took none.
    assert baseline_at(target).is_dir()


def test_nothing_in_run_story_routes_on_the_baseline():
    """It is consumed to build a clone and nowhere else. The control is the
    verdict, which *is* branched on."""
    lines = executable_source(
        inspect.getsource(story_coordinator.run_story)).splitlines()

    def routing(name: str) -> list[str]:
        return [line for line in lines if name in line
                and re.search(r"\b(if|elif|else|return|continue)\b|index\s*=", line)]

    assert routing("decided")
    assert routing("baseline") == []


def test_no_prefix_and_neither_declared_name_is_in_the_code():
    """story-019's property, preserved over the changed source. Docstrings and
    comments are stripped first: prose may name what code may not. The control
    is the name that legitimately does appear.

    The stage-name half of this assertion moved to
    tests/test_shipped_workflow_is_valid.py when story-048 converted this
    module: asked of the workflow built above it would be vacuous, because the
    builder's names are its own and no source contains them. It is asked there
    of the names this repository actually deploys, which is what it was
    watching for, under
    `test_the_coordinator_source_names_no_stage_this_deployment_declares`.
    """
    body = executable_source(COORDINATOR_PATH.read_text(encoding="utf-8"))
    assert PREFIX not in body
    assert ARTIFACT not in body
    assert BASELINE not in body
    assert "state.json" in body                        # a name the code owns
    # The negative control for the three absences: the same scan over a source
    # that does name each one reports it.
    planted = executable_source(
        f"GOVERNED = {PREFIX!r}\nRECORD = {ARTIFACT!r}\nDIR = {BASELINE!r}\n")
    for name in (PREFIX, ARTIFACT, BASELINE):
        assert name in planted, name


def test_the_capture_names_no_prefix_and_no_baseline_directory():
    """The stage-name half moved with the assertion above, and for the same
    reason."""
    body = executable_source(
        inspect.getsource(story_coordinator.capture_stage_baseline))
    assert "ls-files" in body                  # the stripping kept the code
    assert PREFIX not in body
    assert BASELINE not in body


def test_the_baseline_still_carries_no_schema_and_no_manifest_entry():
    manifest = json.loads(
        (REPO_ROOT / "schemas" / "manifest.json").read_text(encoding="utf-8"))
    assert BASELINE not in manifest["schemas"]
    assert not (REPO_ROOT / "schemas" / f"{BASELINE}.schema.json").exists()
    # The control: the artifact declared beside it does carry one.
    assert SCHEMA_STEM in manifest["schemas"]


# --------------------------------------------------------------------------
# What this story left alone
# --------------------------------------------------------------------------


def test_this_story_edited_no_schema_no_workflow_and_no_story_artifact(tmp_path):
    """The control is the file the story did edit: if the diff resolution had
    stopped seeing anything, the last assertion would fail too.

    Restated over a story this test builds rather than recalled out of this
    repository's own commit graph, whose answers moved whenever something was
    committed, renamed, squashed or rebased. The scoped paths, the control and
    the predicate are unchanged.
    """
    untouched_paths = [".harness/stories/", "schemas/", "workflows/", "prompts/",
                       "orchestration/context_assembler.py",
                       "orchestration/agent_runner.py",
                       "orchestration/plan_validation.py"]
    root = conftest.constructed_story(tmp_path, respected=untouched_paths,
                                      violated=[COORDINATOR_REL])
    for untouched in untouched_paths:
        assert conftest.constructed_story_diff(root, [untouched]) == "", untouched
    assert conftest.constructed_story_diff(root, [COORDINATOR_REL]) != ""
