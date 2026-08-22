"""Independent validation for story-020: resuming an escalated run, and
committing its work when it escalates.

The subject is a *terminal state that is no longer terminal*, so almost
nothing here is asserted from source. A target repository is built under
tmp_path, fake stage agents drive it into an escalation, and the coordinator
is then run again against the run directory the escalation left. What resume
does is whatever the second run does to that directory.

The property the story exists to guarantee is checked the way the story words
it — escalate, check out another branch, and look at that branch — rather than
by reading the commit the escalation made.

Every absence asserted here carries a demonstration that the same check can
report the violation it exists to catch:

  * "the other branch is untouched after an escalation" sits beside the same
    checkout performed with the work left uncommitted, which does carry it
    across — so the escalation's commit is what the assertion is about;
  * "the escalation subject is not a completion's" is a pattern paired with a
    real completion subject, which it does match;
  * "the escalated attempt's verification iteration is unmodified" sits beside
    the identical resume with the counters reset, which overwrites it;
  * "the resume refuses when nothing changed" sits beside the same resume with
    one input changed, which proceeds — once per input, so no comparison can
    be the only one carrying the decision;
  * "an unknown stage starts no agent and creates nothing" sits beside the
    same call with a stage the workflow does define;
  * "nothing routes on the escalation summary, the archive or the baseline" is
    a run with all three removed, which routes identically, plus a scan whose
    control is the field that *is* routed on;
  * "a pre-story state.json still loads" is written by the pre-story module
    itself, read out of git, and paired with a field neither module declares,
    which still fails to load.

The place this file used to assert a *positive* fact about a limit rather than
a guarantee was `test_neither_terminal_commit_establishes_what_it_commits`.
story-021 closed that limit, from the start of the run rather than the end,
and the test went red exactly as it was written to. It is repointed rather
than deleted, keeping its subject and its strictness:
`test_the_escalation_commit_carries_only_what_the_run_produced` and
`test_the_completion_commit_stages_the_same_way` now assert the guarantee, and
the second also states the one case it does not cover — a resumed crashed run,
whose dirty tree is that run's own unfinished work.

Nothing here invokes a model: every run goes through a fake agent runner and
every clone source is a local filesystem path.
"""
import ast
import dataclasses
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE as BASELINE_BOUND, ENDPOINT, first_retry_route,
                      load_script)
import conftest

import harness_config
import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]

#: The tests directory the target below configures, and the one the writing
#: stage declares it may not create. Written once so the config and the
#: declaration cannot drift apart, and written *resolved* rather than as the
#: `{{tests_dir}}` token the shipped definition carries: one case here calls
#: `capture_stage_baseline` with this declaration directly, outside the load
#: that expands tokens, and a token reaching it would capture nothing.
TESTS_DIR = "tests/"

#: The workflow these runs execute, assembled by the builder in
#: `tests/conftest.py` rather than resolved out of what this repository
#: deploys. story-048 made the change: the subject here is what an *escalated
#: run* leaves behind and what a resume of it does — where it re-enters, what
#: it archives, what the guard refuses — and the stage list is an input to that
#: question rather than its subject. A definition with a writing stage carrying
#: a revert check, a validating stage, a documenting stage and a judging stage
#: that routes a retry drives every case below identically.
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
    name="escalation-resume-workflow",
)
STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, DOCUMENTING, VERIFYING = STAGE_NAMES
VERIFIER_STAGE = next(s for s in WORKFLOW["stages"] if "on_failure" in s)
#: Since story-028 the route is a category-keyed table rather than a constant,
#: so the category a failing verdict names and the stage it routes to are read
#: off that table through the shared helper.
RETRY_CATEGORY, RETRY_STAGE = first_retry_route(WORKFLOW)
IMPLEMENTER_STAGE = next(s for s in WORKFLOW["stages"]
                         if "revert_check" in s)
BASELINE = IMPLEMENTER_STAGE["revert_check"]["baseline"]


def stages_from(name: str) -> list[str]:
    """The stages a run entering at `name` invokes, in workflow order.

    Read off the loaded definition rather than written out, so a reorder of
    the stage list - story-045 moved the documenter ahead of the verifier -
    changes what a resume is expected to run without these cases stating an
    order of their own.
    """
    return STAGE_NAMES[STAGE_NAMES.index(name):]

STORY_ID = "story-001"
STORY_TITLE = "Sample story for coordinator tests"
DEFAULT_BRANCH = "main"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(attempt: int, *, retry: bool) -> dict:
    """A failing verdict whose text names the attempt that produced it.

    Every attempt's verdict differs, so an iteration file that has been
    written over by a later attempt is distinguishable from one that was not.
    """
    return {
        "status": "failed",
        "blocking_issues": [{
            "severity": "high",
            "issue": f"attempt {attempt} did not implement the sample behavior",
            "location": f"src/attempt_{attempt}.py",
            "required_behavior": f"the sample behavior exists after attempt {attempt}",
        }],
        "unverified": [],
        "retry_recommended": retry,
        "retry_target": RETRY_CATEGORY,
    }


#: The two escalation reasons the verifier routing produces, spelled here only
#: as the shapes the tests below drive; the reason text itself is always read
#: back off the run rather than compared with a literal.
FAIL_RETRY = failing(1, retry=True)
FAIL_FINAL = failing(2, retry=False)
FAIL_AT_ONCE = failing(1, retry=False)

STORY = f"""\
story:
  id: {STORY_ID}
  title: {STORY_TITLE}
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
tests_dir: {TESTS_DIR}
"""

APP_AT_HEAD = "print('hello')\n"
TEST_AT_HEAD = "def test_nothing():\n    assert True\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    # A deterministic name for the branch a developer checks out *away* to.
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)


def build_target(root: Path, gitignore: str = "") -> Path:
    for sub in (".harness/standards", ".harness/stories", ".harness/runs",
                ".harness/logs", ".harness/docs"):
        (root / sub).mkdir(parents=True)
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / ".harness" / "docs" / "ARCHITECTURE.md", "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "test_existing.py", TEST_AT_HEAD)
    if gitignore:
        write(root / ".gitignore", gitignore)
    init_repo(root)
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A target repository whose run directory is tracked, as a project's is."""
    return build_target(tmp_path / "resume-target")


@pytest.fixture
def quiet_target(tmp_path: Path) -> Path:
    """The same repository with its run directory ignored.

    The one shape in which an escalation can find nothing to commit: with the
    run directory tracked, every run dirties the tree by writing state.json.
    """
    return build_target(tmp_path / "quiet-target", gitignore=".harness/runs/\n")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the definition built above.

    A real directory a real coordinator loads a real file out of, so a
    converted case exercises the same code path the shipped-workflow case did.

    Committed as a git repository because the escalation records the harness's
    revision and the guard's third comparison reads it back: a harness root git
    cannot resolve records "" and clears the guard, which is a different case
    and has a test of its own below.
    """
    root = conftest.materialize_workflow(WORKFLOW, tmp_path / "resume-harness")
    init_repo(root)
    return root


# --------------------------------------------------------------------------
# The stage edits and the fake runner
# --------------------------------------------------------------------------


def unchanged(root: Path, attempt: int) -> dict:
    return {"modified": [], "created": [], "deleted": []}


def edits_the_module(root: Path, attempt: int) -> dict:
    write(root / "src" / "app.py", APP_AT_HEAD + f"print('attempt {attempt}')\n")
    return {"modified": ["src/app.py"], "created": [], "deleted": []}


def creates_a_module(root: Path, attempt: int) -> dict:
    write(root / "src" / f"attempt_{attempt}.py", f"value = {attempt}\n")
    return {"modified": [], "created": [f"src/attempt_{attempt}.py"], "deleted": []}


def attempt_directories(run_dir: Path) -> list[str]:
    """Every archived attempt in the run, wherever an entry boundary put it.

    A resume moves attempts/ under the entry directory it opens, so a listing
    of the run root alone stops seeing the attempts an earlier entry took. The
    names are what the assertions here compare, so both places are searched and
    the subject stays "which attempts were archived". The directory name is
    derived from `attempt_dir` rather than written here.
    """
    attempts = story_coordinator.attempt_dir(run_dir, 1).parent.name
    return sorted(path.name for path in run_dir.glob(f"**/{attempts}/*"))


def archived_attempt(run_dir: Path, attempt: int, entry: int = 1) -> Path:
    """Where the archive of one attempt is found after the resume that made it.

    A resume archives the interrupted attempt into attempts/attempt-N/ and then
    moves attempts/ into the entry it opens, so the archive is one directory
    further down than `attempt_dir` alone names. Composed from the two helpers
    that name those directories, so neither is spelled here.
    """
    return story_coordinator.entry_dir(run_dir, entry) / (
        story_coordinator.attempt_dir(run_dir, attempt).relative_to(run_dir)
    )


class Runner:
    """A fake agent runner: each stage writes its artifacts, and a stage
    holding an edit also makes that edit in the target's working tree.

    It records, at the entry to every stage, which attempt directories already
    existed — which is how "archived *before* the resumed stage runs" is
    checked as a fact about the run rather than about its final state. The
    listing covers the whole run directory rather than its root alone, because
    a resume moves attempts/ into the entry it opens and the subject of every
    assertion below is *which* attempts have been archived rather than which
    entry is holding them.
    """

    def __init__(self, target_root: Path, edits: dict | None = None,
                 verdicts: list | None = None, story_id: str = STORY_ID):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.edits = edits or {}
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []
        #: (stage, the attempt directories present when the stage started)
        self.archives_seen: list[tuple[str, list[str]]] = []
        self.verdicts_written: list[dict] = []

    def _nth(self, sequence: list, index: int):
        return sequence[min(index, len(sequence) - 1)]

    def _edit(self, stage: str, attempt: int) -> dict:
        seen = self.calls.count(stage) - 1
        edit = self._nth(self.edits.get(stage, [unchanged]), seen)
        return edit(self.target_root, attempt)

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None, max_budget_usd=None):
        self.calls.append(stage)
        self.archives_seen.append((stage, attempt_directories(self.run_dir)))
        attempt = max(1, self.calls.count(RETRY_STAGE))

        if stage == WRITING:
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       self._edit(stage, attempt))
            write(self.run_dir / conftest.IMPLEMENTATION_SUMMARY,
                  f"Implemented on attempt {attempt}.\n")
        elif stage == VALIDATING:
            write_json(self.run_dir / conftest.TEST_RESULTS, {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       self._edit(stage, attempt))
        elif stage == VERIFYING:
            seen = self.calls.count(stage) - 1
            verdict = self._nth(self.verdicts, seen)
            self.verdicts_written.append(verdict)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT, verdict)
        elif stage == DOCUMENTING:
            write(self.run_dir / conftest.DOCUMENTATION_REPORT, "Nothing.\n")
            write_json(self.run_dir / conftest.DOCUMENTER_CHANGED_FILES,
                       {"modified": [], "created": [], "deleted": []})
        return AgentResult(ok=True, result_text=f"{stage} done")


def run_dir_of(target_root: Path, story_id: str = STORY_ID) -> Path:
    return target_root / ".harness" / "runs" / story_id


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def write_state(target_root: Path, **changes) -> None:
    """Rewrite state.json in place, the way an inspecting developer would."""
    path = run_dir_of(target_root) / "state.json"
    state = json.loads(path.read_text())
    state.update(changes)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def strip_new_fields(target_root: Path) -> dict:
    """Reduce state.json to the fields it carried before this story.

    The pre-story coordinator loads state.json through `RunState(**json)`, so
    it cannot read a file carrying fields it does not declare. Handing it the
    pre-story form is what lets the two modules be run against one run
    directory; the fields removed are not read by anything being compared.
    Returns the original so the caller can put it back.
    """
    path = run_dir_of(target_root) / "state.json"
    original = json.loads(path.read_text())
    path.write_text(
        json.dumps({key: value for key, value in original.items()
                    if key not in NEW_FIELDS}, indent=2) + "\n",
        encoding="utf-8",
    )
    return original


def restore_state(target_root: Path, original: dict) -> None:
    (run_dir_of(target_root) / "state.json").write_text(
        json.dumps(original, indent=2) + "\n", encoding="utf-8")


def run(target_root: Path, harness: Path, edits: dict | None = None,
        verdicts: list | None = None, runner: Runner | None = None,
        start_stage: str | None = None) -> tuple[int, Runner]:
    runner = runner or Runner(target_root, edits, verdicts)
    code = story_coordinator.run_story(
        STORY_ID, harness, target_root, runner, start_stage=start_stage)
    return code, runner


#: The run shape that escalates at the verifier with no retry taken.
AT_ONCE = ([FAIL_AT_ONCE], {WRITING: [edits_the_module]})
#: The run shape that retries once and then escalates, so the resumed run has
#: a non-zero retry count and a second verification iteration to carry.
AFTER_A_RETRY = ([FAIL_RETRY, FAIL_FINAL],
                 {WRITING: [creates_a_module, creates_a_module]})


def escalate(target_root: Path, harness: Path,
             shape: tuple = AT_ONCE) -> Runner:
    verdicts, edits = shape
    code, runner = run(target_root, harness, edits, verdicts)
    assert code == 2, "the shape was meant to escalate"
    assert state_of(target_root)["status"] == "escalated"
    return runner


def change_the_code(target_root: Path) -> None:
    """One of the three things the refusal message tells a developer to do.

    Used to clear the guard in tests whose subject is what the resume *does*,
    so that clearing it is a single, named act rather than a side effect.
    """
    write(target_root / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")


def amend_the_story(target_root: Path) -> None:
    write(target_root / ".harness" / "stories" / f"{STORY_ID}.yaml",
          STORY + "  - and keep the sample behavior working\n")


def ready_to_resume(target_root: Path,
                    message: str = "what the developer decided to do") -> None:
    """Commit whatever the test set up, so the resume starts from a clean tree.

    story-021 refuses a resume of an *escalated* run whose target tree is
    dirty, and it is right to: this story leaves the tree clean when a run
    escalates, so anything uncommitted afterwards is the developer's own work
    and committing it deliberately is exactly what that check is for. Every
    test below whose subject is what the resume *does* therefore does the
    thing the refusal asks for before running.

    The guard-level tests are deliberately not routed through here. Their
    subject is `unchanged_since_escalation`, which is called directly and
    never meets the pre-flight, so `change_the_code` on its own still means
    there what it always meant: a dirty tree the guard reads as changed.

    `--allow-empty`, so a repository that ignores its run directory and has
    nothing uncommitted is not a special case for the caller.
    """
    git(target_root, "add", "-A")
    git(target_root, "commit", "-q", "--allow-empty", "-m", message)


def subject_of(root: Path, revision: str = "HEAD") -> str:
    return git(root, "log", "-1", "--format=%s", revision).stdout.strip()


def body_of(root: Path, revision: str = "HEAD") -> str:
    return git(root, "log", "-1", "--format=%b", revision).stdout


def files_in(root: Path, revision: str = "HEAD") -> list[str]:
    return git(root, "show", "--name-only", "--format=", revision).stdout.split()


def messages(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text()
    return [line.split("] ", 1)[1] for line in log.splitlines() if "] " in line]


def history(target_root: Path) -> list[dict]:
    return json.loads(
        (run_dir_of(target_root) / "execution-history.json").read_text())


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


COORDINATOR_REL = "orchestration/story_coordinator.py"


def coordinator_function(name: str, bound: str) -> str:
    """One coordinator function's source text at one end of this story's range.

    story-029 retired the pre-story and endpoint *modules* this file used to
    load — a coordinator recovered out of history runs against today's
    workflow, schemas and config, and stops running as soon as any of them
    legitimately changes. The comparisons that only ever read a function's
    text never needed a running module, so they read the text.

    story-053 moved where the text comes from. Both ends of this story's range
    are frozen past texts, and resolving them out of this repository's commit
    graph made every comparison below depend on facts about the graph rather
    than about the code: a squash makes the range unresolvable in a clone, and
    a rename gives a path a new add-commit and empties it silently. The two
    bounds are carried as committed fixtures instead, lifted from exactly the
    bounds this used to resolve — the same evidence, in the tree, diffable, and
    unable to move under an assertion that has nothing to say about it.
    """
    assert bound in (BASELINE_BOUND, ENDPOINT), bound
    return conftest.history_fixture(
        f"story_coordinator.{name}.at-story-020-{bound}.py.txt")


def pre_story_run_state() -> str:
    """The `RunState` declaration as it stood before this story's own run.

    Carried as a fixture for the reason `coordinator_function` is, and carrying
    the declaration alone rather than the whole module: a fixture holds what an
    assertion reads and no more.
    """
    return conftest.history_fixture(
        "story_coordinator.RunState.at-story-020-baseline.py.txt")


# --------------------------------------------------------------------------
# An escalated run's work survives a checkout
# --------------------------------------------------------------------------


def test_an_escalated_run_leaves_a_clean_working_tree(target, harness_root):
    """The first half of the property the story exists to guarantee.

    The control is `test_the_same_checkout_carries_uncommitted_work_across`
    below, which shows what an unclean tree costs at the checkout.
    """
    escalate(target, harness_root)

    assert git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() \
        == f"story/{STORY_ID}"
    assert (target / "src" / "app.py").read_text() != APP_AT_HEAD
    assert git(target, "status", "--porcelain").stdout.strip() == ""


def test_an_escalated_run_leaves_another_branch_untouched(target, harness_root):
    """The property checked the way the story words it: escalate, check out
    another branch, and look at that branch.

    Checking out is the act the story exists to make safe, so the checkout's
    own exit code is asserted before what it left behind — a checkout that
    aborts is a stronger failure than one that carries work across, and it is
    invisible to an assertion that only reads the tree afterwards.
    """
    escalate(target, harness_root)

    checkout = git(target, "checkout", DEFAULT_BRANCH, check=False)

    assert checkout.returncode == 0, checkout.stderr
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() \
        == DEFAULT_BRANCH
    assert (target / "src" / "app.py").read_text() == APP_AT_HEAD
    assert git(target, "status", "--porcelain").stdout.strip() == ""


def test_the_escalations_own_evidence_is_committed_with_the_work(
    target, harness_root,
):
    """What the escalation commit has to carry for the two above to hold.

    state.json, the two renderings of the event stream and the escalation
    summary are written by `_escalate` itself, and they are the evidence the
    resume reads. Each is compared as content rather than as presence: an
    earlier stage's state.json is in the commit whatever the ordering, and a
    stale copy of it would satisfy a check that only asked whether the path
    was there.

    The control is the artifact written before the escalation began, whose
    committed content does match — so this is about *when* the commit is made
    rather than about the commit missing the run directory.
    """
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    run_relative = f".harness/runs/{STORY_ID}"

    def committed(name: str) -> str:
        return git(target, "show", f"HEAD:{run_relative}/{name}",
                   check=False).stdout

    assert committed("changed-files.json") \
        == (run_dir / "changed-files.json").read_text()           # the control
    for evidence in ("state.json", "events.log", "execution-history.json",
                     "escalation-summary.md"):
        assert committed(evidence) == (run_dir / evidence).read_text(), evidence


def test_the_same_checkout_carries_uncommitted_work_across(target):
    """The control for the assertion above, and the failure story-018 hit.

    Nothing about the checkout is different — only that the work was left in
    the working tree, which is what an escalation used to do.
    """
    git(target, "checkout", "-q", "-b", f"story/{STORY_ID}")
    write(target / "src" / "app.py", APP_AT_HEAD + "print('uncommitted')\n")

    git(target, "checkout", DEFAULT_BRANCH)

    assert (target / "src" / "app.py").read_text() != APP_AT_HEAD
    assert git(target, "status", "--porcelain").stdout.strip() != ""


def test_the_escalation_commit_is_recorded_on_state_and_is_on_the_branch(
    target, harness_root,
):
    """What the recorded commit has to be for anything to read it.

    Asserted as "a real commit, on the story branch, directly under the tree
    the escalation ends on" rather than as "the branch tip". The field is
    written into the state.json that the commit it names contains, and a commit
    cannot name itself: its sha is hashed from a tree that would have to hold
    that sha already. So the escalation records its own state and event stream
    in one commit, whose sha this field carries, and commits the work on top —
    and what the field exists for, telling an escalation the harness committed
    from one a developer committed from one where nothing was committed, an
    ancestor establishes exactly as a tip would.

    The relationship is asserted rather than left implicit because two other
    things are derived from it: the guard compares HEAD~1 and the undo command
    names HEAD~2. A recorded commit that were two back, or on no branch at all,
    would leave both silently wrong while this field still looked populated.
    """
    escalate(target, harness_root)
    state = state_of(target)
    commit = state["escalation_commit"]

    assert commit
    assert git(target, "cat-file", "-t", commit).stdout.strip() == "commit"
    assert git(target, "merge-base", "--is-ancestor", commit,
               f"story/{STORY_ID}", check=False).returncode == 0
    assert git(target, "rev-parse", "HEAD~1").stdout.strip() == commit
    assert state["harness_revision"] == git(
        harness_root, "rev-parse", "HEAD").stdout.strip()
    assert state["story_digest"] == story_coordinator.story_digest(
        (target / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text())

    # The control for the ancestry assertion: a commit made elsewhere, which
    # the same check reports as not on the story branch.
    git(target, "checkout", "-q", DEFAULT_BRANCH)
    write(target / "elsewhere.txt", "not on the story branch\n")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "a commit on another branch")
    assert git(target, "merge-base", "--is-ancestor",
               git(target, "rev-parse", "HEAD").stdout.strip(),
               f"story/{STORY_ID}", check=False).returncode != 0


def test_the_escalation_adds_exactly_the_commits_its_undo_command_names(
    target, harness_root,
):
    """The one number two other things depend on, read off the branch.

    The undo command's revision count and the number of commits an escalation
    adds are the same fact written twice, and nothing else notices if they
    drift: an undo naming one revision too few would leave a commit behind and
    still put work in the tree, which the undo test alone cannot see. Both are
    read off the run here and compared with each other.

    Every commit added is also checked to read as an escalation, so a developer
    scanning `git log --oneline` meets the escalation on each line rather than
    a bookkeeping entry on one of them. The control is a real completion
    subject, which the same pattern does match.
    """
    escalate(target, harness_root)
    added = git(target, "rev-list", f"{DEFAULT_BRANCH}..story/{STORY_ID}"
                ).stdout.split()
    named = re.search(r"HEAD~(\d+)\s*$",
                      story_coordinator.ESCALATION_UNDO_COMMAND)

    assert named, story_coordinator.ESCALATION_UNDO_COMMAND
    assert int(named.group(1)) == len(added)
    for revision in added:
        subject = subject_of(target, revision)
        assert subject.startswith(story_coordinator.ESCALATION_COMMIT_MARKER)
        assert not COMPLETION_SUBJECT.match(subject)

    completed = build_target(target.parent / "completed-for-count")
    code, _ = run(completed, harness_root, {WRITING: [edits_the_module]})
    assert code == 0
    assert COMPLETION_SUBJECT.match(subject_of(completed))         # the control


# --------------------------------------------------------------------------
# The escalation commit's form, and the way back out of it
# --------------------------------------------------------------------------

#: `_complete`'s subject form: the story id, a colon, the title. Written as a
#: pattern so the escalation subject is tested against the *shape* a reader
#: would mistake it for rather than against one completion's prose.
COMPLETION_SUBJECT = re.compile(rf"^{re.escape(STORY_ID)}: \S")


def test_the_escalation_subject_cannot_be_read_as_a_completion(
    target, harness_root,
):
    """Asserted on form. The control is a real completion subject from the
    same story id, which the same pattern does match — so the pattern is
    capable of reporting the confusion it is checking for."""
    escalate(target, harness_root)
    escalation = subject_of(target)

    assert escalation.startswith(story_coordinator.ESCALATION_COMMIT_MARKER)
    assert not COMPLETION_SUBJECT.match(escalation)
    assert VERIFIER_STAGE["name"] in escalation      # where execution stopped

    completed = build_target(target.parent / "completed-target")
    code, _ = run(completed, harness_root, {WRITING: [edits_the_module]})
    assert code == 0
    assert COMPLETION_SUBJECT.match(subject_of(completed))    # the control


def test_the_escalation_body_names_the_reason_and_the_way_back(
    target, harness_root,
):
    escalate(target, harness_root)
    reason = json.loads(
        (run_dir_of(target) / "verification-result.json").read_text())["status"]
    summary = (run_dir_of(target) / "escalation-summary.md").read_text()
    recorded = summary.split("## Reason", 1)[1].split("##", 1)[0].strip()
    body = body_of(target)

    assert recorded in body
    assert reason == "failed"                        # the run really did fail
    assert story_coordinator.ESCALATION_UNDO_COMMAND in body
    assert "not a decision" in body or "holding place" in body


def test_the_named_undo_command_actually_returns_the_changes_to_the_tree(
    target, harness_root,
):
    """The body's instruction, executed rather than read. A command that named
    the wrong revision would leave the tree clean or the branch short."""
    escalate(target, harness_root)
    commit = state_of(target)["escalation_commit"]
    edited = (target / "src" / "app.py").read_text()
    assert edited != APP_AT_HEAD

    undo = story_coordinator.ESCALATION_UNDO_COMMAND.split()
    subprocess.run(undo, cwd=target, check=True, capture_output=True)

    assert git(target, "rev-parse", "HEAD").stdout.strip() != commit
    assert (target / "src" / "app.py").read_text() == edited
    assert "src/app.py" in git(target, "status", "--porcelain").stdout


def test_the_undo_command_is_right_in_a_repository_that_ignores_its_runs(
    quiet_target, harness_root,
):
    """The same command, executed in the other repository shape.

    One command is named in every escalation's body, so it has to be right
    wherever the escalation happens — and the two shapes differ in exactly what
    there is to commit: a tracked run directory gives the escalation's own
    record something to carry, an ignored one gives it nothing. If the shapes
    left different numbers of commits, this command would be right in one of
    them and would either strand a commit or unwind past the run in the other.

    The control is the same command in the tracked shape, one test above.
    """
    escalate(quiet_target, harness_root)
    started_from = git(quiet_target, "rev-parse", DEFAULT_BRANCH).stdout.strip()
    edited = (quiet_target / "src" / "app.py").read_text()
    assert edited != APP_AT_HEAD

    subprocess.run(story_coordinator.ESCALATION_UNDO_COMMAND.split(),
                   cwd=quiet_target, check=True, capture_output=True)

    assert git(quiet_target, "rev-parse", "HEAD").stdout.strip() == started_from
    assert (quiet_target / "src" / "app.py").read_text() == edited
    assert "src/app.py" in git(quiet_target, "status", "--porcelain").stdout


def test_an_escalation_with_nothing_to_commit_records_none_and_does_not_fail(
    quiet_target, harness_root,
):
    """A repository whose run directory is ignored, and stages that touch
    nothing: the tree at escalation is clean.

    The control is the same run in the same repository with one edit, which
    does record a commit — so the empty record above is about there being
    nothing to commit rather than about the commit never being attempted.
    """
    head_before = git(quiet_target, "rev-parse", "HEAD").stdout.strip()
    escalate(quiet_target, harness_root, ([FAIL_AT_ONCE], {}))

    assert state_of(quiet_target)["escalation_commit"] == ""
    assert git(quiet_target, "rev-parse", "HEAD").stdout.strip() == head_before

    with_an_edit = build_target(quiet_target.parent / "quiet-edited",
                                gitignore=".harness/runs/\n")
    escalate(with_an_edit, harness_root, AT_ONCE)
    assert state_of(with_an_edit)["escalation_commit"] != ""


def test_commit_escalated_work_returns_empty_on_a_clean_tree(target, tmp_path):
    """The same fact at the level below the run, so the end-to-end result is
    read against a direct call. The control is the identical call one edit
    later."""
    state = story_coordinator.RunState(
        story_id=STORY_ID, branch=f"story/{STORY_ID}", current_stage=VERIFYING)
    head = git(target, "rev-parse", "HEAD").stdout.strip()

    assert story_coordinator.commit_escalated_work(target, state, "nothing") == ""
    assert git(target, "rev-parse", "HEAD").stdout.strip() == head

    write(target / "src" / "app.py", APP_AT_HEAD + "print('now something')\n")
    made = story_coordinator.commit_escalated_work(target, state, "something")
    assert made == git(target, "rev-parse", "HEAD").stdout.strip()
    assert made != head


# --------------------------------------------------------------------------
# _complete is not what this story is fixing
# --------------------------------------------------------------------------


def test_the_completion_commit_is_byte_for_byte_the_code_it_was(tmp_path):
    """Same message, same contents, same behavior, stated as sameness of the
    function that produces them. The control is `_escalate`, compared the same
    way against the same pre-story module, which did change.

    Bounded at *this* story's endpoint rather than at today's working tree, for
    the reason `at_story_endpoint` records: read against the working tree the
    comparison asks what `_complete` looks like now, which a later story
    changes without story-020 having done anything. story-027 is where it bit —
    it extracted `_complete`'s inline message into `completion_commit_message`,
    which story-020 has nothing to say about — and the extraction is held to
    producing identical bytes by its own story's coverage, not by this."""
    assert coordinator_function("_complete", ENDPOINT) \
        == coordinator_function("_complete", BASELINE_BOUND)
    assert coordinator_function("_escalate", ENDPOINT) \
        != coordinator_function("_escalate", BASELINE_BOUND)


def test_a_successful_run_still_commits_its_work_under_the_completion_subject(
    target, harness_root,
):
    """The behavioral half: a run that never escalates makes exactly one
    commit, in the completion form, carrying the story's work."""
    head_before = git(target, "rev-parse", "HEAD").stdout.strip()
    code, _ = run(target, harness_root, {WRITING: [edits_the_module]})
    assert code == 0

    assert COMPLETION_SUBJECT.match(subject_of(target))
    assert STORY_TITLE in subject_of(target)
    assert not subject_of(target).startswith(
        story_coordinator.ESCALATION_COMMIT_MARKER)
    assert git(target, "rev-list", "--count", f"{head_before}..HEAD").stdout.strip() == "1"
    assert "src/app.py" in files_in(target)
    assert (run_dir_of(target) / "completion-report.md").is_file()


# --------------------------------------------------------------------------
# Which runs resume and which still refuse
# --------------------------------------------------------------------------


def test_an_escalated_run_resumes_at_the_recorded_stage(target, harness_root):
    escalate(target, harness_root)
    assert state_of(target)["current_stage"] == VERIFIER_STAGE["name"]
    change_the_code(target)
    ready_to_resume(target)

    code, resumed = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert resumed.calls == stages_from(VERIFIER_STAGE["name"])
    assert state_of(target)["status"] == "completed"


def test_a_completed_run_still_refuses_with_the_message_it_always_had(
    target, harness_root, capsys,
):
    """The message, named rather than compared against a module that produced
    it once.

    Two statements, and both are needed. What the refusal *says* today is
    asserted as its own text: the status, the run directory, the branch, and
    each of the three things it tells the developer to do about them. That the
    story did not change it is asserted where the text lives — the print
    statement is byte-identical at both ends of this story's own commit range,
    read as text rather than by running the module that produced it.

    The control is the guard's *condition*, read the same way at the same two
    ends, which did change: this story narrowed it. So the equality above is
    the message being untouched rather than two readings of one unchanged
    file.
    """
    code, _ = run(target, harness_root, {WRITING: [edits_the_module]})
    assert code == 0
    capsys.readouterr()

    again = story_coordinator.run_story(
        STORY_ID, harness_root, target, Runner(target))
    now = capsys.readouterr().err

    assert again == 1
    assert f"{STORY_ID} already ended with status 'completed'." in now
    assert f"Inspect {run_dir_of(target)} to review it." in now
    assert f"delete {run_dir_of(target)} *and* reset branch" in now
    assert f"story/{STORY_ID}, which still holds the finished work." in now
    assert "gitignored" in now

    assert already_ended_message(ENDPOINT) == already_ended_message(BASELINE_BOUND)
    assert already_ended_condition(ENDPOINT) != already_ended_condition(BASELINE_BOUND)


#: The two halves of the already-ended refusal, sliced out of `run_story`'s
#: own text: what it prints, and what decides that it prints.
_MESSAGE_HEAD = 'f"{story_id} already ended with status'
_MESSAGE_TAIL = "file=sys.stderr,"


def already_ended_message(bound: str) -> str:
    body = coordinator_function("run_story", bound)
    head = body.index(_MESSAGE_HEAD)
    return body[head:body.index(_MESSAGE_TAIL, head)]


def already_ended_condition(bound: str) -> str:
    body = coordinator_function("run_story", bound)
    head = body.rindex("if state", 0, body.index(_MESSAGE_HEAD))
    return body[head:body.index("\n", head)]


def test_the_completed_refusal_starts_no_agent_and_the_escalated_one_does(
    target, harness_root, capsys,
):
    """The narrowing, as behavior: the same coordinator, the same run
    directory, and the only difference is the status recorded in it."""
    escalate(target, harness_root)
    change_the_code(target)
    write_state(target, status="completed")

    refused = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, refused) == 1
    assert refused.calls == []

    write_state(target, status="escalated")
    ready_to_resume(target)
    code, resumed = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert resumed.calls != []


def test_the_refusal_this_story_narrowed_is_named_at_both_ends_of_its_range(
    target, harness_root,
):
    """What "narrowed" means, stated as the two conditions rather than by
    running the code that carried the first one.

    Before this story the already-ended refusal fired on any status that was
    not `running`, which is what refused an escalated run; after it, on
    `completed` alone. Both are read as text at the two ends of this story's
    own commit range, so the change is established from both sides and stays
    established once the range is history.

    The behavioural half sits beside it and is what the narrowing is *for*:
    the same escalated run directory resumes, at the recorded stage. Its
    control is the completed status on the same repository, which still
    refuses and starts nothing — that is
    `test_the_completed_refusal_starts_no_agent_and_the_escalated_one_does`
    above, driven through today's coordinator alone.
    """
    before = already_ended_condition(BASELINE_BOUND)
    after = already_ended_condition(ENDPOINT)
    assert 'status != "running"' in before
    assert 'status == "completed"' in after
    assert "escalated" not in after

    escalate(target, harness_root)
    change_the_code(target)
    ready_to_resume(target)

    code, resumed = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]


def test_a_crashed_run_still_resumes_exactly_as_it_did(target, harness_root):
    """A run left `running` resumes at the recorded stage with no guard — the
    guard is escalated-only, because a crashed run has no escalation commit to
    compare against.

    What this test used to assert is the half story-061 reverses: it required a
    crashed resume to leave no `attempts/` directory, on the reading that the
    archive belonged to the escalation path. The archive is now the guarantee
    both resumed statuses get, so the assertion states that instead — the
    interrupted attempt is archived, before the resumed stage runs, under the
    attempt number the crashed run's counters resolve to. The subject is
    unchanged: this is still a crashed run's resume.
    """
    crashed = Runner(target, {WRITING: [edits_the_module]})
    run_dir = run_dir_of(target)
    run_dir.mkdir(parents=True, exist_ok=True)
    story_coordinator.save_state(
        run_dir,
        story_coordinator.RunState(story_id=STORY_ID, branch=f"story/{STORY_ID}",
                                   status="running", current_stage=VALIDATING),
    )

    code, _ = run(target, harness_root, runner=crashed, verdicts=[PASS])

    assert code == 0
    assert crashed.calls[0] == VALIDATING
    first_stage, archives_at_entry = crashed.archives_seen[0]
    assert first_stage == VALIDATING
    assert archives_at_entry == ["attempt-1"]
    assert archived_attempt(run_dir, 1).is_dir()


# --------------------------------------------------------------------------
# The counters are reset, and the escalated attempt survives anyway
#
# These two used to assert the counters were carried across a resume, and the
# second was the control saying why they had to be: a reset would have written
# over the escalated attempt's verification iteration. story-062 reverses the
# policy and keeps the evidence by moving the entry rather than by refusing to
# reset, so each states the guarantee that now holds. The subject of the pair
# is unchanged — the escalated attempt's rendered prompt and its verification
# iteration are still readable, unmodified, after the resumed run finishes.
# --------------------------------------------------------------------------


def test_a_resumed_run_restores_the_counters_and_preserves_the_attempt(
    target, harness_root,
):
    """The escalated attempt's rendered prompt and verification iteration are
    still there after the resumed run has finished — under the entry directory
    the resume archived them into, at the bytes they had before it ran — while
    the counters have gone back to zero and the resumed run's own writing is at
    the run root under the names a first attempt uses.

    Compared against content captured before the resume rather than against a
    path existing, and both copies are asserted, so an archive that had simply
    copied the fresh rendering back over itself fails here.
    """
    escalate(target, harness_root, AFTER_A_RETRY)
    escalated = state_of(target)
    assert escalated["retry_count"] == 1
    assert escalated["verification_iterations"] == 2
    assert escalated["resume_count"] == 0

    run_dir = run_dir_of(target)
    prompt_name = story_coordinator.prompt_file(VERIFIER_STAGE["name"], 2)
    prompt = (run_dir / prompt_name).read_text()
    iteration_1 = (run_dir / "verification" / "iteration-1.json").read_text()
    iteration_2 = (run_dir / "verification" / "iteration-2.json").read_text()

    change_the_code(target)
    ready_to_resume(target)
    code, resumed = run(target, harness_root, verdicts=[PASS])
    assert code == 0

    state = state_of(target)
    assert state["retry_count"] == 0
    assert state["verification_iterations"] == 1
    assert state["resume_count"] == 1

    entry = story_coordinator.entry_dir(run_dir, 1)
    assert (entry / "verification" / "iteration-1.json").read_text() == iteration_1
    assert (entry / "verification" / "iteration-2.json").read_text() == iteration_2
    assert (entry / prompt_name).read_text() == prompt
    # The escalated entry's copy of the interrupted attempt is archived under
    # it too, and the run root now holds the resumed run's own writing.
    assert (archived_attempt(run_dir, 2) / prompt_name).read_text() == prompt
    assert json.loads(
        (run_dir / "verification" / "iteration-1.json").read_text()) == PASS
    fresh = run_dir / story_coordinator.prompt_file(VERIFIER_STAGE["name"], 1)
    assert fresh.is_file()
    assert fresh.read_text() != prompt
    assert resumed.calls == stages_from(VERIFIER_STAGE["name"])


def test_the_move_is_what_makes_resetting_the_counters_safe(
    target, harness_root,
):
    """The control for the test above, and the reason the entry directory is a
    constraint rather than a convenience.

    The same escalated run resumed twice: once by the coordinator, whose reset
    lands the resumed verifier back on iteration-1.json with the escalated
    verdict preserved under the entry it opened, and once against a run
    directory where that move is prevented — the entry directory occupied
    beforehand — which refuses rather than writing over the verdict.
    """
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    escalated_verdict = (run_dir / "verification" / "iteration-1.json").read_text()
    assert json.loads(escalated_verdict) == FAIL_AT_ONCE

    change_the_code(target)
    ready_to_resume(target)
    code, _ = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    # The reset put the resumed verification back on iteration 1, and the
    # escalated one is still readable with the content it had.
    assert json.loads(
        (run_dir / "verification" / "iteration-1.json").read_text()) == PASS
    assert (story_coordinator.entry_dir(run_dir, 1) / "verification"
            / "iteration-1.json").read_text() == escalated_verdict

    # The control: the same reset with the move prevented writes nothing at all.
    blocked_target = build_target(target.parent / "move-blocked")
    escalate(blocked_target, harness_root)
    blocked_dir = run_dir_of(blocked_target)
    occupied = story_coordinator.entry_dir(blocked_dir, 1)
    occupied.mkdir(parents=True)
    change_the_code(blocked_target)
    ready_to_resume(blocked_target)
    blocked = Runner(blocked_target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, blocked_target, blocked) == 1
    assert blocked.calls == []
    assert json.loads((blocked_dir / "verification"
                       / "iteration-1.json").read_text()) == FAIL_AT_ONCE
    assert state_of(blocked_target)["verification_iterations"] == 1


# --------------------------------------------------------------------------
# The interrupted attempt is archived, and never written over
# --------------------------------------------------------------------------


def test_the_interrupted_attempt_is_archived_before_the_resumed_stage_runs(
    target, harness_root,
):
    """Observed at the entry to the resumed stage rather than after the run,
    so "before" is a fact about ordering."""
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    assert not (run_dir / "attempts").exists()
    escalated_verdict = (run_dir / "verification-result.json").read_text()
    escalated_prompt = (run_dir / "prompt-verifier-attempt-1.md").read_text()

    change_the_code(target)
    ready_to_resume(target)
    code, resumed = run(target, harness_root, verdicts=[PASS])
    assert code == 0

    first_stage, archives_at_entry = resumed.archives_seen[0]
    assert first_stage == VERIFIER_STAGE["name"]
    assert archives_at_entry == ["attempt-1"]

    archived = archived_attempt(run_dir, 1)
    assert (archived / "verification-result.json").read_text() == escalated_verdict
    assert (archived / "prompt-verifier-attempt-1.md").read_text() == escalated_prompt
    # The stage artifacts the workflow declares are archived too, so the
    # archive is the attempt rather than the prompts alone.
    assert (archived / "changed-files.json").is_file()
    assert (archived / "test-results.json").is_file()


def test_the_archive_carries_every_stage_artifact_the_workflow_declares():
    """The archive list is derived, not written: the prompts are the addition,
    and the control is that every declared stage artifact is still in it."""
    stages = WORKFLOW["stages"]
    listed = story_coordinator.interrupted_attempt_artifacts(stages, 3)

    for artifact in story_coordinator.archivable_artifacts(stages):
        assert artifact in listed
    for stage in stages:
        assert f"prompt-{stage['name']}-attempt-3.md" in listed
    assert f"prompt-{stages[0]['name']}-attempt-2.md" not in listed


def test_a_resume_whose_archive_directory_exists_refuses_naming_it(
    target, harness_root, capsys,
):
    """The case story-010 recorded as open. The control is the identical
    resume in a repository where the directory does not exist, which proceeds
    — so the refusal is about the directory rather than about resuming."""
    escalate(target, harness_root)
    change_the_code(target)
    occupied = story_coordinator.attempt_dir(run_dir_of(target), 1)
    occupied.mkdir(parents=True)
    write(occupied / "verification-result.json", "hand-written evidence\n")
    # Committed, so the refusal below can only be the archive directory's:
    # a dirty tree would refuse first, for story-021's reason instead of this
    # one, and the message assertion would be checking the wrong refusal.
    ready_to_resume(target)
    capsys.readouterr()

    blocked = Runner(target)
    code = story_coordinator.run_story(STORY_ID, harness_root, target, blocked)
    message = capsys.readouterr().err

    assert code == 1
    assert blocked.calls == []
    assert str(occupied) in message
    assert (occupied / "verification-result.json").read_text() \
        == "hand-written evidence\n"
    assert state_of(target)["status"] == "escalated"

    elsewhere = build_target(target.parent / "unoccupied")
    escalate(elsewhere, harness_root)
    change_the_code(elsewhere)
    ready_to_resume(elsewhere)
    proceeded, runner = run(elsewhere, harness_root, verdicts=[PASS])
    assert proceeded == 0
    assert runner.calls != []


# --------------------------------------------------------------------------
# The same archive, reached from the other recorded status
# --------------------------------------------------------------------------
#
# The cases below drive the branch the escalated-resume cases above drive,
# entered from a `running` state instead. Writing that status onto the run
# directory an escalation already filled is the cheaper fixture than killing a
# coordinator mid-stage and reaches exactly the same code, because the resume
# decides from the recorded status and from nothing else. The escalation's own
# `escalation_commit` is deliberately left on the state: a guard that ran would
# have something to refuse on, which is what makes "no guard decides a crashed
# resume" evidence rather than a coincidence about an empty field.
#
# Every name here is derived - the resumed stage off the loaded definition, the
# archive directory off `attempt_dir`, the prompt off `prompt_file`, the stage
# artifacts off the conftest constants the builder assembles the workflow from.

#: The stage an escalated run stops at, and so the stage both resumes below
#: re-enter at. Read off the definition rather than written.
RESUMED_STAGE = VERIFIER_STAGE["name"]


def crashed(target_root: Path) -> None:
    """Leave the run recorded as `running`, the way a dead process leaves it."""
    write_state(target_root, status="running")


def archived_names(run_dir: Path, attempt: int) -> list[str]:
    return sorted(p.name for p in archived_attempt(run_dir, attempt).iterdir())


def test_a_crashed_resume_archives_under_the_attempt_number_its_counters_resolve_to(
    target, harness_root,
):
    """The number is the one the crashed run's own counters resolve to, not a
    fresh one: a resume carries `retry_count` forward, so the stage loop
    re-renders under exactly the number the dead attempt used.

    Read at the entry to the resumed stage rather than after the run, so
    "before the resumed stage runs" is a fact about ordering. The list is
    compared whole against the directories present before the resume plus the
    one this story adds, so an archive written under any other number fails
    here. The shape is the one that took a retry, so the run already carries
    the archive that retry made — which is what makes "under the number the
    counters resolve to" distinguishable from "under the first free number".
    """
    escalate(target, harness_root, AFTER_A_RETRY)
    run_dir = run_dir_of(target)
    assert state_of(target)["retry_count"] == 1
    retried = story_coordinator.attempt_dir(run_dir, 1).name
    interrupted = story_coordinator.attempt_dir(run_dir, 2).name
    assert sorted(p.name for p in (run_dir / "attempts").iterdir()) == [retried]
    crashed(target)

    code, resumed = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    first_stage, archives_at_entry = resumed.archives_seen[0]
    assert first_stage == RESUMED_STAGE
    assert archives_at_entry == [retried, interrupted]


def test_the_crashed_attempts_prompt_is_archived_beside_the_fresh_rendering(
    target, harness_root,
):
    """The archive holds the prompt the interrupted stage was actually given,
    byte for byte, while the resumed stage's own rendering sits at the run root
    under the canonical name.

    Compared against bytes captured *before* the resume, not against the file
    the resumed run leaves at the root. Asserting only that the archive
    directory exists would pass while the wrong file was preserved, and
    asserting the two copies are equal would pass if the archive had simply
    copied the fresh rendering back over itself — so both halves are here: the
    archived copy equals what was captured, and the live copy does not.

    The resume restores the allowance, so the fresh rendering is the entry's
    attempt 1 rather than a second file under the interrupted attempt's number.
    Both names come off `prompt_file`, and the two numbers come off the
    counters either side of the reset rather than being written here.
    """
    escalate(target, harness_root, AFTER_A_RETRY)
    run_dir = run_dir_of(target)
    interrupted_attempt = state_of(target)["retry_count"] + 1
    rendered = story_coordinator.prompt_file(RESUMED_STAGE, interrupted_attempt)
    interrupted = (run_dir / rendered).read_bytes()
    crashed(target)

    code, _ = run(target, harness_root, verdicts=[PASS])
    assert code == 0

    archived = archived_attempt(run_dir, interrupted_attempt) / rendered
    assert archived.read_bytes() == interrupted
    live = run_dir / story_coordinator.prompt_file(RESUMED_STAGE, 1)
    assert live.is_file()
    assert live.read_bytes() != interrupted
    # And the interrupted attempt's name is not reused at the run root at all.
    assert not (run_dir / rendered).exists()


def test_the_crashed_archive_holds_what_that_attempt_wrote_and_skips_the_rest(
    target, harness_root,
):
    """An artifact the interrupted attempt never wrote is skipped rather than
    treated as an error, exactly as `archive_attempt` already skips an absent
    conditional artifact.

    The control is the identical resume in a repository where that artifact is
    present, whose archive does hold it — so the absence below is the missing
    file's doing rather than the archive having stopped seeing anything.
    """
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    (run_dir / conftest.TEST_RESULTS).unlink()
    crashed(target)

    code, _ = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    without = archived_names(run_dir, 1)
    assert conftest.TEST_RESULTS not in without
    assert conftest.VERIFICATION_RESULT in without
    assert conftest.CHANGED_FILES in without

    kept = build_target(target.parent / "artifact-kept")
    escalate(kept, harness_root)
    crashed(kept)
    proceeded, _ = run(kept, harness_root, verdicts=[PASS])
    assert proceeded == 0
    assert conftest.TEST_RESULTS in archived_names(run_dir_of(kept), 1)


def test_a_crashed_attempt_that_wrote_nothing_still_refuses_a_second_resume(
    target, harness_root,
):
    """The archive is not conditional on what the dying stage left behind. An
    attempt that wrote nothing still produces the directory, and the directory
    is what the second resume refuses on.

    The control for "the directory is empty" is the resume above, whose archive
    of an attempt that did write is not — so an empty listing here is the
    attempt's emptiness rather than a listing that reads nothing.
    """
    run_dir = run_dir_of(target)
    run_dir.mkdir(parents=True, exist_ok=True)
    story_coordinator.save_state(
        run_dir,
        story_coordinator.RunState(story_id=STORY_ID, branch=f"story/{STORY_ID}",
                                   status="running", current_stage=RESUMED_STAGE),
    )

    code, _ = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert archived_attempt(run_dir, 1).is_dir()
    assert archived_names(run_dir, 1) == []

    crashed(target)
    blocked = Runner(target)
    refused = story_coordinator.run_story(STORY_ID, harness_root, target, blocked)
    assert refused == 1
    assert blocked.calls == []


def test_both_resumed_statuses_refuse_a_colliding_archive_with_the_same_text(
    target, harness_root, capsys,
):
    """One refusal, reached twice. The two messages are compared byte for byte
    against one another rather than each against a literal, so two texts that
    merely agree today would have to keep agreeing to pass.

    Both refusals are taken from one run directory, so the destination, the
    story and the attempt number they interpolate are identical and any
    difference in the printed text is a difference in the expression that
    produced it.
    """
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    occupied = story_coordinator.attempt_dir(run_dir, 1)
    occupied.mkdir(parents=True)
    write(occupied / conftest.VERIFICATION_RESULT, "hand-written evidence\n")
    change_the_code(target)
    # Committed, so the escalated refusal below can only be the archive
    # directory's: a dirty tree refuses first, for story-021's reason.
    ready_to_resume(target)
    capsys.readouterr()

    from_escalated = Runner(target)
    escalated_code = story_coordinator.run_story(
        STORY_ID, harness_root, target, from_escalated)
    escalated_message = capsys.readouterr().err

    crashed(target)
    from_running = Runner(target)
    running_code = story_coordinator.run_story(
        STORY_ID, harness_root, target, from_running)
    running_message = capsys.readouterr().err

    assert escalated_code == 1 and running_code == 1
    assert from_escalated.calls == [] and from_running.calls == []
    assert escalated_message.strip() != ""
    assert running_message == escalated_message
    assert str(occupied) in running_message
    # Nothing was archived over: the refusal is what the archive exists for.
    assert (occupied / conftest.VERIFICATION_RESULT).read_text() \
        == "hand-written evidence\n"


def refusals_naming_the_destination(source: str) -> list[tuple[str, int]]:
    """The prints in `run_story` that name the archive destination, each as its
    exact source segment and the column it starts at.

    Structural rather than textual: the message itself is interpolated at
    runtime, and a test that matched on its words would be a second copy of
    the very text it exists to keep from being copied. The segment is returned
    verbatim so the control below can duplicate the real statement rather than
    a re-rendering of it.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        names = {inner.id for inner in ast.walk(node)
                 if isinstance(inner, ast.Name)}
        if "destination" in names:
            found.append((ast.get_source_segment(source, node), node.col_offset))
    return found


def test_the_collision_refusal_is_one_expression_rather_than_one_per_path(
    target, harness_root,
):
    """Both resumed statuses reach the refusal above by construction, not by
    two copies that happen to agree.

    The control is the same scan over a copy of the source with that print
    duplicated, which reports two — so "exactly one" is the source's doing
    rather than a scan that has stopped finding prints.
    """
    source = inspect.getsource(story_coordinator.run_story)
    found = refusals_naming_the_destination(source)
    assert len(found) == 1

    segment, column = found[0]
    duplicated = source.replace(
        segment, f"{segment}\n{' ' * column}{segment}", 1)
    assert duplicated != source
    assert len(refusals_naming_the_destination(duplicated)) == 2


def _tests_the_escalated_status(test: ast.expr) -> bool:
    """Whether an `if` test is keyed on the escalated status.

    The compare itself, or a conjunct of an `and` that carries it. Since
    story-063 the guard's branch is narrowed by a second conjunct — a run
    stopped on a cost ceiling is exempted from it — and a recogniser that read
    only the bare compare would stop seeing that branch and report the scan's
    subject as absent rather than as narrowed. Only `and` is walked: a
    disjunct would make the branch reachable on some *other* status, which is
    not a branch conditional on this one.
    """
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_tests_the_escalated_status(v) for v in test.values)
    return (isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Attribute)
            and test.left.attr == "status"
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "escalated")


def escalated_branches(tree: ast.AST) -> list[ast.If]:
    """Every branch keyed on `state.status == "escalated"` in a parsed function.

    The subject of the two readers below is what this coordinator still makes
    conditional on the escalated status, so they read the shipped coordinator
    rather than a fixture. Nested statements inside those blocks are walked,
    which is the point: an archive re-indented back inside one would show up.
    """
    branches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _tests_the_escalated_status(node.test):
            branches.append(node)
    return branches


def calls_inside_the_escalated_branches(source: str) -> set[str]:
    """Every function called inside one of those blocks."""
    inside: set[str] = set()
    for node in escalated_branches(ast.parse(source)):
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                    inside.add(inner.func.id)
    return inside


def statuses_assigned_inside_the_escalated_branches(source: str) -> set[str]:
    """The literal statuses `state.status` is assigned inside those blocks."""
    assigned: set[str] = set()
    for node in escalated_branches(ast.parse(source)):
        for statement in node.body:
            for inner in ast.walk(statement):
                if not (isinstance(inner, ast.Assign)
                        and isinstance(inner.value, ast.Constant)):
                    continue
                for target in inner.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "status":
                        assigned.add(inner.value.value)
    return assigned


def test_the_escalated_branch_kept_the_transition_and_gave_up_the_archive(
    target, harness_root,
):
    """What generalized and what did not, read off the coordinator itself.

    The archive and its collision refusal are no longer conditional on the
    escalated status; the state transition, its `save_state` and the
    `unchanged_since_escalation` guard still are.

    The control is the same scan over `run_story` as this repository carried it
    at story-020's endpoint, where the archive *was* inside that branch and the
    scan reports it — so "not inside" is this story's hoist rather than a scan
    that never looked in the right place. `archive_attempt` is called from the
    retry paths in both texts, outside any escalated branch, which is why the
    scan is scoped to those branches rather than counting call sites.
    """
    now = calls_inside_the_escalated_branches(
        inspect.getsource(story_coordinator.run_story))
    assert "archive_attempt" not in now
    assert "attempt_dir" not in now
    assert "save_state" in now
    assert "unchanged_since_escalation" in now
    assert statuses_assigned_inside_the_escalated_branches(
        inspect.getsource(story_coordinator.run_story)) == {"running"}

    before = calls_inside_the_escalated_branches(
        coordinator_function("run_story", ENDPOINT))
    assert "archive_attempt" in before
    assert "attempt_dir" in before


def test_the_status_transition_runs_for_an_escalated_resume_and_not_a_crashed_one(
    target, harness_root, monkeypatch,
):
    """A crashed run is already running, so assigning that status would state a
    transition that did not happen.

    Every `save_state` of the run is recorded, for the same escalation resumed
    once from each status. The escalated resume's sequence is the crashed one's
    with exactly one extra write at the front, carrying the status the
    transition assigns — so the transition is present in one run, absent in the
    other, and nothing else about the two runs differs.
    """
    saved: dict[str, list[str]] = {}
    real_save = story_coordinator.save_state

    def recording(bucket: list[str]):
        def save(run_dir, state):
            bucket.append(state.status)
            return real_save(run_dir, state)
        return save

    from_escalated = build_target(target.parent / "transition-escalated")
    escalate(from_escalated, harness_root)
    change_the_code(from_escalated)
    ready_to_resume(from_escalated)
    saved["escalated"] = []
    monkeypatch.setattr(story_coordinator, "save_state",
                        recording(saved["escalated"]))
    code, _ = run(from_escalated, harness_root, verdicts=[PASS])
    assert code == 0

    monkeypatch.setattr(story_coordinator, "save_state", real_save)
    from_running = build_target(target.parent / "transition-running")
    escalate(from_running, harness_root)
    change_the_code(from_running)
    ready_to_resume(from_running)
    crashed(from_running)
    saved["running"] = []
    monkeypatch.setattr(story_coordinator, "save_state",
                        recording(saved["running"]))
    code, _ = run(from_running, harness_root, verdicts=[PASS])
    assert code == 0

    assert saved["escalated"] == ["running"] + saved["running"]


def test_a_crashed_resume_is_refused_by_nothing_the_unchanged_guard_decides(
    target, harness_root,
):
    """`unchanged_since_escalation` stays escalated-only. A crashed run has no
    escalation commit to compare against, so its refusal would be meaningless.

    Nothing is changed before the resume, and the escalation's own commit is
    left on the state — so the guard has exactly the evidence it refuses on.
    The control is the identical repository resumed from the escalated status,
    which does refuse.
    """
    escalate(target, harness_root)
    assert state_of(target)["escalation_commit"]
    crashed(target)

    code, resumed = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert resumed.calls == stages_from(RESUMED_STAGE)

    still_escalated = build_target(target.parent / "guard-control")
    escalate(still_escalated, harness_root)
    blocked = Runner(still_escalated)
    refused = story_coordinator.run_story(
        STORY_ID, harness_root, still_escalated, blocked)
    assert refused == 1
    assert blocked.calls == []


def test_a_crashed_resume_still_exempts_a_dirty_tree_and_the_archive_leaves_it_alone(
    target, harness_root, monkeypatch,
):
    """The pre-flight exemption and the archive do not interact: the pre-flight
    reads the working tree and the archive touches only the run directory.

    The dirty paths are read either side of the archive call itself. Paths
    outside the run directory are unchanged across it; paths inside it are not,
    which is the control — a comparison that saw nothing at all would satisfy
    the first assertion and fail the second.
    """
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    write(target / "src" / "half-finished.py", "value = ")
    crashed(target)

    seen: list[tuple[set[str], set[str]]] = []
    real_archive = story_coordinator.archive_attempt

    def watching(run_directory, artifacts, attempt):
        before = set(story_coordinator.dirty_paths(target))
        archived = real_archive(run_directory, artifacts, attempt)
        seen.append((before, set(story_coordinator.dirty_paths(target))))
        return archived

    monkeypatch.setattr(story_coordinator, "archive_attempt", watching)
    code, resumed = run(target, harness_root, runner=Runner(target),
                        verdicts=[PASS])

    assert code == 0
    assert resumed.calls == stages_from(RESUMED_STAGE)
    assert len(seen) == 1
    before, after = seen[0]
    run_relative = str(run_dir.relative_to(target))
    outside = lambda paths: {p for p in paths if not p.startswith(run_relative)}
    assert outside(after) == outside(before)
    assert outside(before) != set(), "the tree was meant to be dirty"
    assert after - before != set(), "the archive was meant to write something"

    control = build_target(target.parent / "dirty-control")
    escalate(control, harness_root)
    write(control / "src" / "half-finished.py", "value = ")
    blocked = Runner(control)
    refused = story_coordinator.run_story(
        STORY_ID, harness_root, control, blocked)
    assert refused == 1
    assert blocked.calls == []


def test_the_archive_set_a_crashed_resume_passes_is_the_escalated_ones(
    target, harness_root, monkeypatch,
):
    """This story adds a caller, not a second archive set. The list and the
    attempt number handed to `archive_attempt` are recorded for the same
    escalation resumed once from each status, and compared with each other."""
    passed: dict[str, tuple[list[str], int]] = {}
    real_archive = story_coordinator.archive_attempt

    def recording(key: str):
        def archive(run_directory, artifacts, attempt):
            passed[key] = (list(artifacts), attempt)
            return real_archive(run_directory, artifacts, attempt)
        return archive

    from_escalated = build_target(target.parent / "set-escalated")
    escalate(from_escalated, harness_root, AFTER_A_RETRY)
    change_the_code(from_escalated)
    ready_to_resume(from_escalated)
    monkeypatch.setattr(story_coordinator, "archive_attempt",
                        recording("escalated"))
    assert run(from_escalated, harness_root, verdicts=[PASS])[0] == 0

    # Unpatched while the second escalation runs: its retry archives too, and
    # the recorder would otherwise keep that call instead of the resume's.
    monkeypatch.setattr(story_coordinator, "archive_attempt", real_archive)
    from_running = build_target(target.parent / "set-running")
    escalate(from_running, harness_root, AFTER_A_RETRY)
    crashed(from_running)
    monkeypatch.setattr(story_coordinator, "archive_attempt",
                        recording("running"))
    assert run(from_running, harness_root, verdicts=[PASS])[0] == 0

    assert passed["running"] == passed["escalated"]
    assert passed["running"][0] != []


def test_a_truncated_artifact_the_dying_stage_left_is_archived_as_it_is(
    target, harness_root,
):
    """Nothing this story adds inspects a copied artifact's content or
    validity. A stage that died mid-write leaves a file no schema accepts, and
    preserving it exactly is the point rather than a failure of it."""
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    half_written = '{"status": "pa'
    write(run_dir / conftest.VERIFICATION_RESULT, half_written)
    with pytest.raises(json.JSONDecodeError):
        json.loads(half_written)
    crashed(target)

    code, _ = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    archived = archived_attempt(run_dir, 1) / conftest.VERIFICATION_RESULT
    assert archived.read_text() == half_written


def test_an_escalated_resume_gives_the_guarantee_it_gave_before_this_story(
    target, harness_root,
):
    """story-020's archive is not what this story changes, so it is restated
    whole here beside the crashed cases above: the same archive, at the same
    point, under the same number, with the escalated attempt's rendered prompt
    and verification iteration untouched.

    What story-062 does change is where that archive comes to rest and what the
    counters read afterwards, so those two are stated as the guarantee that now
    holds rather than dropped: the entry the resume opened holds the archive
    and the escalated iteration, and the counters are back at the start of a
    fresh allowance.
    """
    escalate(target, harness_root, AFTER_A_RETRY)
    run_dir = run_dir_of(target)
    interrupted_attempt = state_of(target)["retry_count"] + 1
    rendered = story_coordinator.prompt_file(RESUMED_STAGE, interrupted_attempt)
    interrupted = (run_dir / rendered).read_bytes()
    iteration_2 = (run_dir / "verification" / "iteration-2.json").read_bytes()
    change_the_code(target)
    ready_to_resume(target)

    code, resumed = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert resumed.calls == stages_from(RESUMED_STAGE)
    first_stage, archives_at_entry = resumed.archives_seen[0]
    assert first_stage == RESUMED_STAGE
    assert archives_at_entry == [story_coordinator.attempt_dir(run_dir, 1).name,
                                 story_coordinator.attempt_dir(run_dir, 2).name]
    assert (archived_attempt(run_dir, interrupted_attempt)
            / rendered).read_bytes() == interrupted
    assert not (run_dir / rendered).exists()
    entry = story_coordinator.entry_dir(run_dir, 1)
    assert (entry / "verification" / "iteration-2.json").read_bytes() == iteration_2
    state = state_of(target)
    assert state["retry_count"] == 0
    assert state["verification_iterations"] == 1
    assert state["resume_count"] == 1
    # The escalated entry's iteration is under the entry and nowhere else: the
    # run root is the resumed entry's, which has taken one verification.
    assert not (run_dir / "verification" / "iteration-2.json").exists()


# --------------------------------------------------------------------------
# The stage argument
# --------------------------------------------------------------------------


def test_a_stage_argument_overrides_the_recorded_stage(target, harness_root):
    """The control is the same resume with no argument, which enters at the
    stage state.json records — so the override is the argument's doing."""
    escalate(target, harness_root)
    assert state_of(target)["current_stage"] == VERIFIER_STAGE["name"]
    change_the_code(target)
    ready_to_resume(target)

    code, overridden = run(target, harness_root, verdicts=[PASS],
                           edits={WRITING: [edits_the_module]},
                           start_stage=RETRY_STAGE)

    assert code == 0
    assert overridden.calls[0] == RETRY_STAGE
    assert overridden.calls == stages_from(RETRY_STAGE)

    elsewhere = build_target(target.parent / "no-override")
    escalate(elsewhere, harness_root)
    change_the_code(elsewhere)
    ready_to_resume(elsewhere)
    assert run(elsewhere, harness_root, verdicts=[PASS])[1].calls[0] \
        == VERIFIER_STAGE["name"]


def test_a_stage_the_workflow_does_not_define_refuses_and_creates_nothing(
    target, harness_root, capsys,
):
    undefined = "reviewer"
    assert undefined not in STAGE_NAMES
    runner = Runner(target)

    code = story_coordinator.run_story(
        STORY_ID, harness_root, target, runner, start_stage=undefined)
    message = capsys.readouterr().err

    assert code == 1
    assert runner.calls == []
    assert not run_dir_of(target).exists()
    assert undefined in message
    for name in STAGE_NAMES:
        assert name in message
    # The control: the same call naming a stage the workflow does define runs.
    accepted = Runner(target, {WRITING: [edits_the_module]})
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, accepted, start_stage=STAGE_NAMES[0]) == 0
    assert accepted.calls != []
    assert run_dir_of(target).is_dir()


def test_a_fresh_run_started_at_a_later_stage_records_that_stage(
    target, harness_root,
):
    """The argument is not resume-only: on a fresh run it is where the
    workflow is entered. The control is a fresh run with no argument, which
    enters at the first stage."""
    code, started = run(target, harness_root, verdicts=[PASS],
                        start_stage=VERIFIER_STAGE["name"])
    assert code == 0
    assert started.calls == stages_from(VERIFIER_STAGE["name"])

    elsewhere = build_target(target.parent / "fresh-default")
    assert run(elsewhere, harness_root, verdicts=[PASS])[1].calls[0] \
        == STAGE_NAMES[0]


def load_l5_run():
    """`scripts/l5-run` as a module, through the shared script loader.

    story-029 folded the extensionless-script loader this file shared with
    `tests/test_story_025_validation.py` into `conftest.load_script`, so
    building a module happens in one place under tests/.
    """
    return load_script("l5-run", name="l5_run_script")


def test_l5_run_passes_the_stage_through_and_decides_nothing_itself(
    target, monkeypatch,
):
    """The script stays a thin entry point: it forwards whatever it was given,
    including a stage the workflow does not define, and the refusal is the
    coordinator's. The control is the invocation with no argument, which
    forwards None rather than a default of its own."""
    script = load_l5_run()
    seen: list[dict] = []

    # `base` is story-030's addition to run_story's signature; the spy accepts
    # it so this test keeps asking what it asked — that the script forwards the
    # stage it was given and defaults nothing itself.
    def spy(story_id, harness_root, target_root, runner=None, start_stage=None,
            base=None):
        seen.append({"story_id": story_id, "start_stage": start_stage})
        return 0

    monkeypatch.setattr(story_coordinator, "run_story", spy)
    monkeypatch.setattr(harness_config, "find_target_root", lambda cwd: target)

    monkeypatch.setattr(sys, "argv", ["l5-run", STORY_ID, "--stage", RETRY_STAGE])
    assert script.main() == 0
    monkeypatch.setattr(sys, "argv", ["l5-run", STORY_ID, "--stage", "reviewer"])
    assert script.main() == 0
    monkeypatch.setattr(sys, "argv", ["l5-run", STORY_ID])
    assert script.main() == 0

    assert [call["start_stage"] for call in seen] == [RETRY_STAGE, "reviewer", None]
    assert {call["story_id"] for call in seen} == {STORY_ID}


def test_l5_run_still_refuses_an_argument_list_it_cannot_read(target, monkeypatch):
    script = load_l5_run()
    started: list = []
    monkeypatch.setattr(story_coordinator, "run_story",
                        lambda *a, **k: started.append(a) or 0)
    monkeypatch.setattr(harness_config, "find_target_root", lambda cwd: target)

    for argv in (["l5-run"], ["l5-run", STORY_ID, "extra"],
                 ["l5-run", STORY_ID, "--stage"]):
        monkeypatch.setattr(sys, "argv", argv)
        assert script.main() == 1, argv
    assert started == []


# --------------------------------------------------------------------------
# The unchanged guard, in both directions
# --------------------------------------------------------------------------


def guard(target_root: Path, harness: Path,
          story_text: str | None = None) -> list[str]:
    state = story_coordinator.load_state(run_dir_of(target_root))
    if story_text is None:
        story_text = (target_root / ".harness" / "stories"
                      / f"{STORY_ID}.yaml").read_text()
    return story_coordinator.unchanged_since_escalation(
        state, story_text, target_root, harness)


#: Every guard test below runs against `quiet_target`, whose run directory is
#: ignored, because that is the only shape in which an escalation currently
#: leaves the clean tree the guard's second comparison requires. The tracked
#: shape — the one the harness itself sets up — is the subject of
#: `test_the_refusal_fires_in_a_repository_that_tracks_its_run_directory`.


def test_a_resume_with_nothing_changed_refuses_naming_the_reason_and_the_fix(
    quiet_target, harness_root, capsys,
):
    escalate(quiet_target, harness_root)
    summary = (run_dir_of(quiet_target) / "escalation-summary.md").read_text()
    reason = summary.split("## Reason", 1)[1].split("##", 1)[0].strip()
    capsys.readouterr()

    refused = Runner(quiet_target)
    code = story_coordinator.run_story(
        STORY_ID, harness_root, quiet_target, refused)
    message = capsys.readouterr().err

    assert code == 1
    assert refused.calls == []
    assert reason in message
    assert str(quiet_target / ".harness" / "stories" / f"{STORY_ID}.yaml") in message
    assert f"story/{STORY_ID}" in message
    assert state_of(quiet_target)["status"] == "escalated"
    assert not (run_dir_of(quiet_target) / "attempts").exists()


def test_the_refusal_fires_in_a_repository_that_tracks_its_run_directory(
    target, harness_root, capsys,
):
    """The same refusal in the shape `l5-init` produces, where the run
    directory is a tracked part of the repository — this repository's own
    `.harness/runs/` is tracked, and so is the target fixture's.

    The control is `test_a_resume_with_nothing_changed_refuses_...` above,
    which is the identical run in the identical state with the run directory
    ignored: if the guard's logic were wrong, that one would fail too.
    """
    escalate(target, harness_root)
    capsys.readouterr()

    refused = Runner(target)
    code = story_coordinator.run_story(STORY_ID, harness_root, target, refused)

    assert code == 1
    assert refused.calls == []


def test_amending_the_story_clears_the_refusal(quiet_target, harness_root):
    """The story is the input the guard names first, and amending it is the
    response the message asks for. The control is the run immediately above,
    which refused with the same run directory and the story untouched."""
    escalate(quiet_target, harness_root)
    assert guard(quiet_target, harness_root) != []

    amend_the_story(quiet_target)
    assert guard(quiet_target, harness_root) == []

    # The guard assertion above is what carries the claim; committing after it
    # is story-021's clean-tree pre-flight being satisfied, not a second way of
    # clearing the guard sneaking into the subject.
    ready_to_resume(quiet_target, "the amended story")
    code, resumed = run(quiet_target, harness_root, verdicts=[PASS])
    assert code == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]


def test_each_input_alone_clears_the_guard(quiet_target, harness_root):
    """One comparison at a time, so no single input can be the only one
    carrying the decision. The positive case is asserted first and is the
    control for all three."""
    target = quiet_target
    escalate(target, harness_root)
    story_text = (target / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text()
    assert len(guard(target, harness_root)) == 3

    # The story artifact, changed with the tree and the harness untouched.
    assert guard(target, harness_root, story_text + "\n# amended\n") == []

    # The harness, at a revision the run did not record.
    assert guard(target, harness_root) != []
    write_state(target, harness_revision="0" * 40)
    assert guard(target, harness_root) == []
    write_state(target, harness_revision=git(
        harness_root, "rev-parse", "HEAD").stdout.strip())
    assert guard(target, harness_root) != []

    # The tree, dirtied without moving HEAD.
    change_the_code(target)
    assert guard(target, harness_root) == []


def test_a_developer_commit_after_the_escalation_clears_the_guard(
    quiet_target, harness_root,
):
    """The distinction the recorded commit exists to draw, driven rather than
    reasoned about: an escalation the harness committed, and the same run after
    a developer has committed on top of it.

    The tree is clean in both cases and the story and harness are untouched in
    both, so the branch comparison is the only thing that can tell them apart.
    It matters here more than it reads: the guard compares the recorded commit
    against the tip's *parent*, and a developer commit moves the tip — the
    parent it then finds is the escalation's own work commit, not the recorded
    one. The control is the assertion immediately before the commit is made,
    which does refuse.
    """
    target = quiet_target
    escalate(target, harness_root)
    assert guard(target, harness_root) != []                       # the control

    change_the_code(target)
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "a developer's fix on the story branch")
    assert git(target, "status", "--porcelain").stdout.strip() == ""

    assert guard(target, harness_root) == []


def test_the_guard_refuses_only_on_evidence_it_can_establish(
    quiet_target, harness_root,
):
    """Anything not established counts as not-the-same. Each of the three
    records is emptied in turn, which is what a state file written before this
    story, an escalation that committed nothing, and a harness that is not a
    repository all look like."""
    target = quiet_target
    escalate(target, harness_root)
    assert guard(target, harness_root) != []           # the control

    for field in ("story_digest", "escalation_commit", "harness_revision"):
        recorded = state_of(target)[field]
        write_state(target, **{field: ""})
        assert guard(target, harness_root) == [], field
        write_state(target, **{field: recorded})
    assert guard(target, harness_root) != []


def test_a_harness_root_that_is_not_a_repository_produces_no_false_refusal(
    quiet_target, harness_root, tmp_path,
):
    """The escalation records "" for a harness it cannot resolve, and the
    guard reads that as not-established rather than as a value to compare.

    The control is the identical run against the real harness root, which does
    refuse — so what differs is the establishability of the harness revision.
    """
    target = quiet_target
    fake = conftest.materialize_workflow(WORKFLOW,
                                         tmp_path / "harness-not-a-repo")
    assert story_coordinator._revision(fake) == ""
    assert story_coordinator._revision(harness_root) != ""

    escalate(target, fake)
    assert state_of(target)["harness_revision"] == ""
    assert guard(target, fake) == []

    code, resumed = run(target, fake, verdicts=[PASS])
    assert code == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]

    elsewhere = build_target(target.parent / "real-harness",
                             gitignore=".harness/runs/\n")
    escalate(elsewhere, harness_root)
    assert guard(elsewhere, harness_root) != []        # the control


def test_the_digest_neither_authorizes_nor_triggers_a_resume(
    target, harness_root, capsys,
):
    """An amended story does not restart a finished run, and does not make a
    run resumable that its status does not. Routing is on the status alone.

    The control is the same amended story against the same run directory with
    the status put back, which does resume.
    """
    escalate(target, harness_root)
    amend_the_story(target)
    write_state(target, status="completed")
    capsys.readouterr()

    refused = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, refused) == 1
    assert refused.calls == []
    assert "completed" in capsys.readouterr().err

    write_state(target, status="escalated")
    ready_to_resume(target)
    assert run(target, harness_root, verdicts=[PASS])[1].calls != []


def test_no_line_in_run_story_routes_on_the_digest(harness_root):
    """The digest informs a message; it is never a branch, a reroute or a
    return. The control is the status, which is routed on in the same
    function — so a scan that had stopped matching would fail here first."""
    lines = executable_source(
        inspect.getsource(story_coordinator.run_story)).splitlines()

    def routing(name: str) -> list[str]:
        return [line for line in lines if name in line
                and re.search(r"\b(if|elif|else|return|continue)\b|index\s*=", line)]

    assert routing("status")
    assert routing("digest") == []


# --------------------------------------------------------------------------
# state.json is the only routing source
# --------------------------------------------------------------------------


def test_the_resumed_stage_comes_from_state_json(target, harness_root):
    """Changed in state.json and nowhere else, the resume enters somewhere
    else. Nothing else in the run directory was touched."""
    escalate(target, harness_root)
    assert state_of(target)["current_stage"] == VERIFIER_STAGE["name"]
    change_the_code(target)
    write_state(target, current_stage=DOCUMENTING)
    ready_to_resume(target)

    code, resumed = run(target, harness_root)

    assert code == 0
    assert resumed.calls == stages_from(DOCUMENTING)


def test_nothing_routes_on_the_summary_the_archive_or_the_baseline(
    target, harness_root,
):
    """All three removed from the run directory, and the resume routes
    identically to the run below that keeps them.

    The escalation summary is read for the refusal message only, so removing
    it costs that message a sentence; here the guard is already cleared and it
    costs nothing at all.
    """
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    change_the_code(target)
    (run_dir / "escalation-summary.md").unlink()
    subprocess.run(["rm", "-rf", str(run_dir / BASELINE)], check=True)
    ready_to_resume(target, "the run directory with all three removed")

    code, stripped = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert stripped.calls == stages_from(VERIFIER_STAGE["name"])

    intact = build_target(target.parent / "intact")
    escalate(intact, harness_root)
    change_the_code(intact)
    ready_to_resume(intact)
    code, kept = run(intact, harness_root, verdicts=[PASS])
    assert code == 0
    assert kept.calls == stripped.calls


def test_removing_the_summary_costs_the_refusal_a_sentence_and_no_decision(
    quiet_target, harness_root, capsys,
):
    """The other half: with the guard *not* cleared, the run still refuses
    with the summary gone — the decision came from state.json — and only the
    reason drops out of the message."""
    target = quiet_target
    escalate(target, harness_root)
    summary = (run_dir_of(target) / "escalation-summary.md").read_text()
    reason = summary.split("## Reason", 1)[1].split("##", 1)[0].strip()
    capsys.readouterr()

    assert story_coordinator.run_story(STORY_ID, harness_root, target,
                                       Runner(target)) == 1
    with_summary = capsys.readouterr().err

    (run_dir_of(target) / "escalation-summary.md").unlink()
    assert story_coordinator.run_story(STORY_ID, harness_root, target,
                                       Runner(target)) == 1
    without_summary = capsys.readouterr().err

    assert reason in with_summary
    assert reason not in without_summary
    assert f"story/{STORY_ID}" in without_summary


def test_a_resumed_stage_reuses_the_baseline_recorded_for_it(
    target, harness_root, tmp_path,
):
    """story-019's capture-once-reuse rule, inherited rather than
    re-implemented. A run escalated *inside* the implementer is resumed there
    with that stage's edits already in the tree; the baseline it decides
    against is the one taken before the stage first ran.

    The control is a fresh capture taken after the edit into a scratch run
    directory, which holds the edited content — so the reuse above is the
    keying rather than a capture that has stopped reading the tree. It takes
    the fresh capture into a scratch directory because since story-037 the
    baseline is keyed by stage alone, and an attempt number distinguishes
    nothing.
    """
    class Incomplete(Runner):
        """An implementer that edits the tree and writes no artifacts, so the
        run escalates inside the stage that made the edit."""

        def __call__(self, prompt, *, stage, **kwargs):
            self.calls.append(stage)
            write(self.target_root / "tests" / "test_existing.py",
                  TEST_AT_HEAD + "\n\ndef test_added():\n    assert True\n")
            return AgentResult(ok=True, result_text="no artifacts")

    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, Incomplete(target)) == 2
    run_dir = run_dir_of(target)
    captured = story_coordinator.stage_baseline_dir(
        run_dir, BASELINE, IMPLEMENTER_STAGE["name"])
    assert (captured / "tests" / "test_existing.py").read_text() == TEST_AT_HEAD

    change_the_code(target)
    ready_to_resume(target)
    code, _ = run(target, harness_root, verdicts=[PASS],
                  edits={WRITING: [edits_the_module]})
    assert code == 0

    assert (captured / "tests" / "test_existing.py").read_text() == TEST_AT_HEAD
    recaptured = story_coordinator.capture_stage_baseline(
        tmp_path / "scratch-run", target, BASELINE, IMPLEMENTER_STAGE["name"],
        IMPLEMENTER_STAGE["may_not_create"], accounted_for=set())
    assert (recaptured / "tests" / "test_existing.py").read_text() != TEST_AT_HEAD


# --------------------------------------------------------------------------
# The event stream reconstructs the whole run
# --------------------------------------------------------------------------


def test_one_resumed_event_names_the_stage_in_both_renderings(
    target, harness_root,
):
    escalate(target, harness_root)
    change_the_code(target)
    ready_to_resume(target)
    run(target, harness_root, verdicts=[PASS])

    resumed = [line for line in messages(target) if line.startswith("resumed")]
    entries = [entry for entry in history(target) if entry["event"] == "resumed"]

    assert len(resumed) == 1
    assert VERIFIER_STAGE["name"] in resumed[0]
    assert len(entries) == 1
    assert entries[0]["message"] == resumed[0]
    assert entries[0]["stage"] == VERIFIER_STAGE["name"]


def test_the_whole_run_including_the_escalation_and_the_resume_reconstructs(
    target, harness_root,
):
    """One stream, one run: the escalation and the resume are both in it, in
    order, and the sequence numbers are contiguous across the two invocations.

    The control is the ordering assertion itself — a stream that had lost the
    escalation or the resume could not satisfy it.
    """
    escalate(target, harness_root, AFTER_A_RETRY)
    change_the_code(target)
    ready_to_resume(target)
    assert run(target, harness_root, verdicts=[PASS])[0] == 0

    entries = history(target)
    assert [entry["sequence"] for entry in entries] == list(range(1, len(entries) + 1))
    kinds = [entry["event"] for entry in entries]
    for kind in ("workflow-started", "verification-failed", "escalated",
                 "resumed", "verification-passed", "story-completed"):
        assert kind in kinds, kind
    assert kinds.index("escalated") < kinds.index("resumed")
    assert kinds.index("resumed") < kinds.index("story-completed")
    assert kinds[-1] == "story-completed"
    assert [entry["message"] for entry in entries] == messages(target)

    assert (run_dir_of(target) / "completion-report.md").is_file()
    assert COMPLETION_SUBJECT.match(subject_of(target))
    # The escalation commit is left in place; the completion commits on top.
    escalation = state_of(target)["escalation_commit"]
    assert git(target, "merge-base", "--is-ancestor", escalation, "HEAD",
               check=False).returncode == 0


# --------------------------------------------------------------------------
# state.json's new fields, and the files written before them
# --------------------------------------------------------------------------

#: The fields state.json carried before this story, read out of the pre-story
#: dataclass in the test below rather than written here.
NEW_FIELDS = {"story_digest", "escalation_commit", "harness_revision"}

#: Fields `RunState` gained after this story, which this assertion is not
#: about. Named rather than tolerated silently: a later story adding a field
#: records it here deliberately, so a field that appears without anyone
#: noticing still turns the assertion below red.
FIELDS_ADDED_SINCE = {"self_route_count", "guidance_in_force",
                      "correction_pass_count", "resume_count",
                      "entry_cost_usd", "stopped_on_cost"}


def pre_story_state_fields() -> list[str]:
    """The fields `RunState` declared before this story, read as text.

    Out of the pre-story `RunState` declaration rather than out of a module
    loaded from it: the field set is a fact stated in the source, and reading
    it is what this ever needed. story-029 retired the loading.
    """
    for node in ast.parse(pre_story_run_state()).body:
        if isinstance(node, ast.ClassDef) and node.name == "RunState":
            return [item.target.id for item in node.body
                    if isinstance(item, ast.AnnAssign)]
    raise AssertionError("the pre-story coordinator declares no RunState")


def test_a_state_file_written_before_this_story_still_loads(target):
    """A state.json in the pre-story shape loads, with the new fields
    defaulting.

    The shape is named rather than produced by a recovered module: the field
    set is read out of the pre-story `RunState` declaration as text, held to
    being today's set minus exactly the three fields this story added and the
    fields later stories added, and a file carrying precisely those fields is
    what is written and loaded.

    The control is a field neither shape declares, which still fails to load —
    so the tolerance is the defaults rather than a loader that has stopped
    checking anything.
    """
    fields = pre_story_state_fields()
    declared_today = {field.name for field in
                      dataclasses.fields(story_coordinator.RunState)}
    assert set(fields) == declared_today - NEW_FIELDS - FIELDS_ADDED_SINCE
    assert set(fields) & NEW_FIELDS == set()

    run_dir = run_dir_of(target)
    run_dir.mkdir(parents=True)
    today = dataclasses.asdict(story_coordinator.RunState(
        story_id=STORY_ID, branch=f"story/{STORY_ID}", status="escalated",
        current_stage=VERIFIER_STAGE["name"], retry_count=1,
        verification_iterations=2))
    written = {name: today[name] for name in fields}
    (run_dir / "state.json").write_text(
        json.dumps(written, indent=2) + "\n", encoding="utf-8")

    loaded = story_coordinator.load_state(run_dir)
    assert loaded.status == "escalated"
    assert loaded.retry_count == 1
    assert loaded.verification_iterations == 2
    assert (loaded.story_digest, loaded.escalation_commit,
            loaded.harness_revision) == ("", "", "")

    (run_dir / "state.json").write_text(
        json.dumps({**written, "invented": "field"}, indent=2), encoding="utf-8")
    with pytest.raises(TypeError):
        story_coordinator.load_state(run_dir)


def test_a_run_escalated_before_this_story_resumes_without_a_false_refusal(
    target, harness_root, tmp_path,
):
    """The upgrade path, end to end: a run left escalated by the pre-story
    coordinator has none of the three records, so the guard establishes
    nothing and the resume proceeds."""
    escalate(target, harness_root)
    run_dir = run_dir_of(target)
    old = {key: value for key, value in state_of(target).items()
           if key not in NEW_FIELDS}
    (run_dir / "state.json").write_text(json.dumps(old, indent=2) + "\n",
                                        encoding="utf-8")
    ready_to_resume(target, "the pre-story state file")

    code, resumed = run(target, harness_root, verdicts=[PASS])

    assert code == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]


def test_the_new_fields_are_written_by_every_run_and_default_to_empty(
    target, harness_root,
):
    """A fresh run records the digest at the start and leaves the other two
    empty until it ends. The control is the escalated run, where all three are
    populated."""
    code, _ = run(target, harness_root, {WRITING: [edits_the_module]})
    assert code == 0
    completed = state_of(target)
    assert NEW_FIELDS <= set(completed)
    assert completed["story_digest"]
    assert completed["escalation_commit"] == ""
    assert completed["harness_revision"] == ""

    elsewhere = build_target(target.parent / "escalated-fields")
    escalate(elsewhere, harness_root)
    escalated = state_of(elsewhere)
    assert all(escalated[field] for field in NEW_FIELDS)


# --------------------------------------------------------------------------
# What the terminal commits establish
#
# story-020 stated a limit here: neither terminal commit established that the
# tree it staged was the tree the run produced. story-021 closed it, from the
# start of the run rather than the end — neither commit changed — so the two
# assertions below are repointed to the guarantee that now holds and to the
# one case it does not, keeping their subject, their strictness and their
# stray file. Repointing is what story-020 said should happen when it landed.
# --------------------------------------------------------------------------

STRAY = "stray-nothing-produced-this.txt"


def test_the_escalation_commit_carries_only_what_the_run_produced(
    target, harness_root,
):
    """Still a property of the coordinator rather than of one commit, and
    still asserted on the same stray file the working tree held before the run.

    What changed is the answer. The stray file is no longer absorbed, because
    the run never starts: the clean-tree pre-flight refuses it, having created
    nothing and moved nothing. Once the developer does what the refusal asks,
    the escalation's commits carry the run's own work and not a second copy of
    what predated it.

    The control is the developer's own commit, which does carry the stray file
    — so the two absences below are statements about what the coordinator
    stages rather than about the file being absent from the repository.
    """
    tip_before = git(target, "rev-parse", "HEAD").stdout.strip()
    write(target / STRAY, "no stage wrote this\n")

    refused = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, refused) == 1
    assert refused.calls == []
    assert git(target, "rev-parse", "HEAD").stdout.strip() == tip_before

    git(target, "add", "--", STRAY)
    git(target, "commit", "-q", "-m", "the developer's own file")
    developers = git(target, "rev-parse", "HEAD").stdout.strip()

    escalate(target, harness_root)

    # An escalation leaves two commits and neither of them carries it.
    assert STRAY not in files_in(target)
    assert STRAY not in files_in(target, "HEAD~1")
    assert "src/app.py" in files_in(target)
    assert STRAY in files_in(target, developers)          # the control


def test_the_completion_commit_stages_the_same_way(target, harness_root):
    """The other half of the same statement, so it is about both terminal
    commits and neither can regress alone without this going red — and the one
    case the guarantee does not cover, stated here rather than left to be
    discovered.

    A resumed *crashed* run is that case: nothing commits when a process dies,
    so its working tree holds the run's own unfinished work and the pre-flight
    does not apply. Its completion commit therefore stages whatever the tree
    held, the stray file included. When that stops being true this fails, and
    the exclusion is what should be re-read rather than the assertion relaxed.
    """
    write(target / STRAY, "no stage wrote this\n")

    refused = Runner(target)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target, refused) == 1
    assert refused.calls == []

    git(target, "add", "--", STRAY)
    git(target, "commit", "-q", "-m", "the developer's own file")

    code, _ = run(target, harness_root, {WRITING: [edits_the_module]})

    assert code == 0
    assert "src/app.py" in files_in(target)
    assert STRAY not in files_in(target)

    # The exclusion: a run left `running` is resumed on its own dirty tree, so
    # its completion commit does carry a file no stage recorded.
    crashed = build_target(target.parent / "crashed")
    run_dir_of(crashed).mkdir(parents=True, exist_ok=True)
    story_coordinator.save_state(
        run_dir_of(crashed),
        story_coordinator.RunState(story_id=STORY_ID,
                                   branch=f"story/{STORY_ID}",
                                   status="running", current_stage=VALIDATING),
    )
    write(crashed / STRAY, "the crashed run's own unfinished work\n")

    resumed = Runner(crashed, {WRITING: [edits_the_module]})
    assert run(crashed, harness_root, runner=resumed, verdicts=[PASS])[0] == 0
    assert resumed.calls[0] == VALIDATING
    assert STRAY in files_in(crashed)


def test_the_coordinator_states_that_where_the_commits_are_made():
    """Stated in the coordinator as a property of the coordinator. story-020
    put a limit here and story-021 repointed it to the guarantee and its one
    exclusion, which is why one statement moved rather than prose being hunted
    for. The control is that the same search finds nothing in a rendering with
    the prose stripped out."""
    source = (REPO_ROOT / "orchestration" / "story_coordinator.py").read_text(
        encoding="utf-8")
    stated = [line for line in source.splitlines()
              if "working tree" in line and ("add -A" in line or "stage" in line)]
    assert stated
    assert any("crashed" in line for line in source.splitlines())
    assert [line for line in executable_source(source).splitlines()
            if "working tree" in line and "add -A" in line] == []


# --------------------------------------------------------------------------
# What this story left alone
# --------------------------------------------------------------------------


def test_this_story_edited_no_blocked_path_and_added_no_artifact(harness_root,
                                                                 tmp_path):
    """The control is the file the story did edit: if the diff resolution had
    stopped seeing anything, the last assertion would fail too.

    Restated over a story this test builds rather than recalled out of this
    repository's own commit graph, where the evidence moved whenever something
    was committed, renamed, squashed or rebased. The blocked paths, the
    control and the predicate are unchanged.
    """
    blocked = ["rules/", "workflows/", "schemas/", "prompts/",
               ".harness/stories/"]
    edited = "orchestration/story_coordinator.py"
    root = conftest.constructed_story(tmp_path, respected=blocked,
                                      violated=[edited])
    for path in blocked:
        assert conftest.constructed_story_diff(root, [path]) == "", path
    assert conftest.constructed_story_diff(root, [edited]) != ""


def test_the_escalation_summary_is_the_text_it_was(tmp_path):
    """A separate request owns its content, sequenced after this story. The
    control is `_escalate`'s own source, which did change in the same file.

    Bounded at *this* story's endpoint rather than at the working tree, per
    `at_story_endpoint` above: story-024 is the separate request, and its
    rewrite of the summary is not this story changing it.
    """
    summary_body = coordinator_function("_escalate", ENDPOINT).split(
        "summary = (", 1)[1]
    before_body = coordinator_function("_escalate", BASELINE_BOUND).split(
        "summary = (", 1)[1]
    assert summary_body == before_body
    assert coordinator_function("_escalate", ENDPOINT) \
        != coordinator_function("_escalate", BASELINE_BOUND)
