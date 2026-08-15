"""Independent validation for story-027: a run is refused onto a branch that
already holds the story's finished work.

The subject is a *pre-flight that refuses*, so almost nothing here is asserted
from source. A target repository is built under tmp_path, a story is run to
completion in it, its run directory is deleted — the exact move that produced
the defect — and the coordinator is asked to run the story again. What the
check does is whatever that second run does to that directory.

The story's guarantee is negative on both sides — nothing is created when a
run is refused, and nothing is refused in the cases that must still run — so
every absence below sits beside a demonstration that the same check can report
the violation it exists to catch:

  * "the refusal left no run directory, no state.json, no log, no new branch
    and called no agent" sits beside the same repository with the completion
    commit stripped off the branch, which creates all five;
  * "an abandoned branch, a first run, and an escalated resume are not
    refused" each sit beside the same repository carrying a completion commit,
    which is;
  * "`completion_commits` reports nothing" for an escalation commit, for a
    subject with no marker, for a marker under another story's subject, for a
    base commit that restores a story artifact, for a branch that does not
    exist, for a root that is not a repository and for a failing git — each
    beside a reading of the same repository that does report;
  * "the recognizer names no branch, no base branch and no story id" is a scan
    paired with copies of those functions with each of those names planted in,
    which the same scan does report;
  * "the completed-state refusal is unchanged" is compared against the
    coordinator as it stood before this story, run against an identical target,
    and that module is shown to differ — by proceeding on the very repository
    the current one refuses, and reporting success having changed nothing;
  * "the check creates no commit, branch, stash or index change" sits beside
    the same before/after reading around calls that make each.

Nothing here invokes a model: every run goes through a fake agent runner.
"""
import ast
import difflib
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE, ENDPOINT, function_source_at,
                      repository_file_at, story_commit_range)

import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")

STORY_ID = "story-001"
STORY_TITLE = "Sample story for coordinator tests"
DEFAULT_BRANCH = "main"
STORY_BRANCH = f"story/{STORY_ID}"

#: The message `_complete` composed inline before story-027 extracted it,
#: written out here as a literal so "the bytes are unchanged" is a comparison
#: against the old code rather than against the new code restated.
PRE_EXTRACTION_MESSAGE = (
    f"{STORY_ID}: {STORY_TITLE}\n\n"
    f"Implemented by the l5 harness story workflow."
)

DOC_OUTPUT = ".harness/docs/ARCHITECTURE.md"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}

FAIL_AT_ONCE = {
    "status": "failed",
    "blocking_issues": [{
        "severity": "high",
        "issue": "the sample behavior was not implemented",
        "location": "src/app.py",
        "required_behavior": "the sample behavior exists",
    }],
    "unverified": [],
    "retry_recommended": False,
}


# --------------------------------------------------------------------------
# The target repository
#
# Built here rather than taken from the shared fixture because most tests
# below hold a subject and a control side by side, and two runs of one story
# in one target directory are one resumed run.
# --------------------------------------------------------------------------

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

CONFIG = """\
workflow: story-workflow
branch_prefix: story/
permission_mode: acceptEdits
stories_dir: .harness/stories
runs_dir: .harness/runs
logs_dir: .harness/logs
standards_dir: .harness/standards
architecture_docs:
  - .harness/docs/ARCHITECTURE.md
test_command: echo tests-ok
"""

GITIGNORE = ".harness/runs/\n.harness/logs/\n"

APP_AT_HEAD = "print('hello')\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload) -> None:
    write(path, json.dumps(payload, indent=2) + "\n")


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check)


def build_target(root: Path, config: str = CONFIG) -> Path:
    write(root / ".harness" / "config.yaml", config)
    write(root / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / DOC_OUTPUT, "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "test_existing.py",
          "def test_nothing():\n    assert True\n")
    write(root / ".gitignore", GITIGNORE)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    subprocess.run(["git", "branch", "-M", DEFAULT_BRANCH], cwd=root, check=True)
    return root


@pytest.fixture
def make_target(tmp_path: Path):
    """A factory, so a test can hold a subject and its control side by side."""
    def make(name: str, **kwargs) -> Path:
        return build_target(tmp_path / name, **kwargs)
    return make


@pytest.fixture
def target(make_target) -> Path:
    return make_target("finished-branch-target")


@pytest.fixture
def harness_root() -> Path:
    return REPO_ROOT


# --------------------------------------------------------------------------
# The fake runner
#
# Each stage writes the artifacts its declaration requires, makes the edit it
# holds in the target's working tree, and writes to the log path it was given
# — as the real runner does, which is what makes "no log line" an observable
# consequence of no agent having been invoked rather than of this fake being
# quiet.
# --------------------------------------------------------------------------


class Runner:
    def __init__(self, target_root: Path, verdicts: list | None = None):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / STORY_ID
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []

    def _nth(self, sequence: list, index: int):
        return sequence[min(index, len(sequence) - 1)]

    def __call__(self, prompt, *, stage, cwd=None, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None):
        self.calls.append(stage)
        if log_path is not None:
            log = Path(log_path)
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"{stage} ran\n")
        attempt = max(1, self.calls.count("implementer"))

        if stage == "implementer":
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('attempt {attempt}')\n")
            write_json(self.run_dir / "changed-files.json", {
                "modified": ["src/app.py"], "created": [], "deleted": [],
            })
            write(self.run_dir / "implementation-summary.md",
                  f"Implemented on attempt {attempt}.\n")
        elif stage == "tester":
            write(self.target_root / "tests" / f"test_attempt_{attempt}.py",
                  "def test_attempt():\n    assert True\n")
            write_json(self.run_dir / "test-results.json", {
                "status": "passed", "tests_written": 1, "tests_run": 1,
                "tests_passed": 1, "tests_failed": 0, "failures": [],
            })
            write_json(self.run_dir / "tester-changed-files.json", {
                "modified": [], "created": [f"tests/test_attempt_{attempt}.py"],
                "deleted": [],
            })
        elif stage == "verifier":
            write_json(self.run_dir / "verification-result.json",
                       self._nth(self.verdicts, self.calls.count(stage) - 1))
        elif stage == "documenter":
            write(self.target_root / DOC_OUTPUT,
                  f"# Architecture\n\nDocumented on attempt {attempt}.\n")
            write(self.run_dir / "documentation-report.md", "Documented.\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(target_root: Path, harness: Path = REPO_ROOT, verdicts: list | None = None,
        coordinator=story_coordinator) -> tuple[int, Runner]:
    runner = Runner(target_root, verdicts)
    code = coordinator.run_story(STORY_ID, harness, target_root, runner)
    return code, runner


def run_dir_of(target_root: Path) -> Path:
    return target_root / ".harness" / "runs" / STORY_ID


def log_of(target_root: Path) -> Path:
    return target_root / ".harness" / "logs" / f"{STORY_ID}.log"


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def branches(target_root: Path) -> list[str]:
    return sorted(line[2:].strip()
                  for line in git(target_root, "branch", "--list").stdout.splitlines())


def head_branch(target_root: Path) -> str:
    return git(target_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def artifacts_in(target_root: Path) -> list[str]:
    run_dir = run_dir_of(target_root)
    return sorted(p.relative_to(run_dir).as_posix()
                  for p in run_dir.rglob("*") if p.is_file())


def messages(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line.split("] ", 1)[1] for line in log.splitlines() if "] " in line]


def commit(target_root: Path, message: str = "the developer's own work") -> str:
    git(target_root, "add", "-A")
    git(target_root, "commit", "-q", "--allow-empty", "-m", message)
    return git(target_root, "rev-parse", "HEAD").stdout.strip()


def finished(target_root: Path, harness: Path = REPO_ROOT) -> Path:
    """A repository in which this story has already been run to completion.

    The branch carries `_complete`'s commit and the working tree is clean, which
    is the state a successful run leaves a developer standing in.
    """
    code, _ = run(target_root, harness)
    assert code == 0, "the fixture was meant to complete"
    assert state_of(target_root)["status"] == "completed"
    return target_root


def rerun_after_deleting_the_run_directory(target_root: Path,
                                           harness: Path = REPO_ROOT,
                                           coordinator=story_coordinator,
                                           on: str = DEFAULT_BRANCH):
    """The move that produced the defect: delete the run directory and re-run.

    The branch is left exactly as the finished run left it, because that is the
    whole point — `_checkout_story_branch` reuses it rather than resetting it.
    """
    shutil.rmtree(run_dir_of(target_root))
    git(target_root, "checkout", "-q", on)
    return run(target_root, harness, coordinator=coordinator)


def abandon_the_branch(target_root: Path) -> None:
    """Reset the story branch back to the base, leaving a branch with no
    completion commit — a run started and dropped before it finished."""
    git(target_root, "checkout", "-q", STORY_BRANCH)
    git(target_root, "reset", "--hard", "-q", DEFAULT_BRANCH)
    git(target_root, "checkout", "-q", DEFAULT_BRANCH)


# --------------------------------------------------------------------------
# The coordinator as it stood before this story
#
# Every "unaffected" and "this is the defect" claim is made against this rather
# than against a shape written here, so the control for each is the thing the
# story did change, observed the same way.
# --------------------------------------------------------------------------


COORDINATOR_REL = "orchestration/story_coordinator.py"


def pre_story(path: str) -> str:
    """A repository file as it stood before this story's own run.

    Through `conftest.repository_file_at` since story-029, which folded the
    eleven private copies of this reader into one. Subject and strictness
    unchanged; only where the text comes from moved.
    """
    return repository_file_at(path, validation_file=Path(__file__),
                              bound=BASELINE, repo=REPO_ROOT)


def at_story_endpoint(path: str) -> str:
    """A repository file as *this* story's own run left it.

    The counterpart of `pre_story`. Against today's working tree a "this story
    changed only X" comparison asks what the file looks like *now*, which a
    later story changes without this story having done anything.
    """
    return repository_file_at(path, validation_file=Path(__file__),
                              bound=ENDPOINT, repo=REPO_ROOT)


def coordinator_function(name: str, bound: str,
                         validation_file: Path | None = None) -> str:
    """One coordinator function's source text at one end of a story's range.

    story-029 retired the pre-story and endpoint *modules* this file used to
    load: a coordinator recovered out of history runs against today's
    workflow, schemas and config, and stops running as soon as any of them
    legitimately changes. Every comparison here only ever read a function's
    text, so it reads the text.
    """
    return function_source_at(COORDINATOR_REL, name,
                              validation_file=validation_file or Path(__file__),
                              bound=bound, repo=REPO_ROOT)


# --------------------------------------------------------------------------
# The refusal, and what it leaves behind
# --------------------------------------------------------------------------


@pytest.fixture
def refused(target, harness_root, capsys):
    """A completed story re-run after its run directory was deleted."""
    finished(target, harness_root)
    shutil.rmtree(run_dir_of(target))
    git(target, "checkout", "-q", DEFAULT_BRANCH)
    before = {
        "head": git(target, "rev-parse", "HEAD").stdout.strip(),
        "branches": branches(target),
        "log": log_of(target).read_text(encoding="utf-8"),
    }
    capsys.readouterr()
    code, runner = run(target, harness_root)
    captured = capsys.readouterr()
    return code, runner, target, before, captured


def test_a_rerun_onto_a_finished_branch_refuses_and_leaves_nothing_behind(refused):
    """Each absence separately, because "exit 1" alone would hold for a run
    that got as far as creating a directory and then failed."""
    code, runner, target, before, _ = refused
    assert code == 1
    assert runner.calls == []                                  # no agent
    assert not run_dir_of(target).exists()                     # no run directory
    assert not (run_dir_of(target) / "state.json").exists()    # no state
    assert log_of(target).read_text(encoding="utf-8") == before["log"]  # no log line
    assert branches(target) == before["branches"]              # no new branch
    assert head_branch(target) == DEFAULT_BRANCH               # no checkout
    assert git(target, "rev-parse", "HEAD").stdout.strip() == before["head"]


def test_the_same_run_without_the_completion_commit_creates_every_one_of_those(
    make_target, harness_root,
):
    """The control for the five absences above: the same repository, the same
    deleted run directory, the same existing branch — one commit apart."""
    target = make_target("stripped-target")
    finished(target, harness_root)
    shutil.rmtree(run_dir_of(target))
    log_before = log_of(target).read_text(encoding="utf-8")
    abandon_the_branch(target)

    code, runner = run(target, harness_root)

    assert code == 0
    assert runner.calls != []
    assert run_dir_of(target).is_dir()
    assert (run_dir_of(target) / "state.json").is_file()
    assert log_of(target).read_text(encoding="utf-8") != log_before
    assert STORY_BRANCH in branches(target)
    assert head_branch(target) == STORY_BRANCH


def test_the_refusal_names_the_branch_each_commit_and_the_run_directory(refused):
    """Read from the captured output, and against the commit read out of git
    rather than against a subject written here."""
    _, _, target, _, captured = refused
    message = captured.err

    sha = git(target, "log", "-1", "--format=%h", STORY_BRANCH).stdout.strip()
    subject = git(target, "log", "-1", "--format=%s", STORY_BRANCH).stdout.strip()

    assert STORY_BRANCH in message
    assert sha in message
    assert subject in message
    assert str(run_dir_of(target)) in message


def test_the_refusal_says_what_a_reset_costs_and_what_to_do_instead(refused):
    """The four things the story requires the guidance to say, each read from
    the captured output."""
    message = refused[4].err
    assert "already shipped" in message
    assert "a new story" in message
    assert "not a reset" in message
    assert "discards the finished work" in message
    assert "gitignored" in message
    assert ".harness/runs-archive/" in message


def test_the_refusal_is_the_shape_the_other_pre_flight_refusals_take(
    refused, make_target, harness_root, capsys,
):
    """One message per problem under a header, and nothing on stdout — the
    same shape the clean-tree refusal takes, which is asserted beside it rather
    than described."""
    _, _, target, _, captured = refused
    assert captured.out == ""
    listed = [line for line in captured.err.splitlines() if line.startswith("  - ")]
    assert len(listed) == 1
    assert listed[0].startswith("  - ")

    other = make_target("dirty-target")
    write(other / "stray.txt", "no stage wrote this\n")
    capsys.readouterr()
    assert run(other, harness_root)[0] == 1
    dirty = capsys.readouterr()
    assert dirty.out == ""
    assert [line for line in dirty.err.splitlines() if line.startswith("  - ")]
    assert dirty.err.splitlines()[0] != captured.err.splitlines()[0]


def test_the_refusal_holds_while_standing_on_the_finished_branch(
    make_target, harness_root, capsys,
):
    """The check is base-free, so it still fires from the branch the completed
    run left the developer on — where `<base>..<branch>` would have been asked
    from the wrong place.

    The control is the same repository with the branch abandoned, standing in
    the same place, which runs.
    """
    target = make_target("standing-on-it-target")
    finished(target, harness_root)
    shutil.rmtree(run_dir_of(target))
    assert head_branch(target) == STORY_BRANCH

    capsys.readouterr()
    code, runner = run(target, harness_root)
    assert code == 1
    assert runner.calls == []
    assert STORY_BRANCH in capsys.readouterr().err

    abandon_the_branch(target)
    git(target, "checkout", "-q", STORY_BRANCH)
    assert run(target, harness_root)[0] == 0


def test_a_finished_branch_is_refused_before_a_dirty_tree_is(
    make_target, harness_root, capsys,
):
    """Placement, asserted by which message a developer whose tree is also
    dirty is given: the one that makes the run pointless, not the one that
    makes it unaccountable.

    The control is the same dirty file with the branch abandoned, which does
    produce the clean-tree refusal — so the absence above is the ordering
    rather than the dirty file having gone unnoticed.
    """
    target = make_target("both-wrong-target")
    finished(target, harness_root)
    shutil.rmtree(run_dir_of(target))
    git(target, "checkout", "-q", DEFAULT_BRANCH)
    write(target / "stray.txt", "no stage wrote this\n")

    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    message = capsys.readouterr().err
    assert STORY_BRANCH in message
    assert "uncommitted changes" not in message

    abandon_the_branch(target)
    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    assert "uncommitted changes" in capsys.readouterr().err


def test_the_branch_the_check_asks_about_comes_from_the_configured_prefix(
    make_target, harness_root, capsys,
):
    """No branch name enters orchestration: change the prefix and the refusal
    names the branch that prefix builds.

    The control is the default-prefix name, which is absent from that message
    and present in the same refusal on the default configuration.
    """
    configured = CONFIG.replace("branch_prefix: story/", "branch_prefix: feature/")
    target = make_target("prefixed-target", config=configured)
    finished(target, harness_root)
    shutil.rmtree(run_dir_of(target))
    git(target, "checkout", "-q", DEFAULT_BRANCH)

    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    message = capsys.readouterr().err
    assert f"feature/{STORY_ID}" in message
    assert STORY_BRANCH not in message

    assert story_coordinator.story_branch({"branch_prefix": "feature/"}, STORY_ID) \
        == f"feature/{STORY_ID}"


# --------------------------------------------------------------------------
# The runs that must still happen
# --------------------------------------------------------------------------


def test_a_branch_that_exists_but_never_finished_still_runs(
    make_target, harness_root,
):
    """A branch created and then abandoned before `_complete` is a normal
    state, not an error.

    The control is the identical repository with the completion commit still on
    that branch, which refuses.
    """
    abandoned = make_target("abandoned-target")
    git(abandoned, "checkout", "-q", "-b", STORY_BRANCH)
    commit(abandoned, "half of the work, then interrupted")
    git(abandoned, "checkout", "-q", DEFAULT_BRANCH)

    code, runner = run(abandoned, harness_root)
    assert code == 0
    assert runner.calls != []
    assert state_of(abandoned)["status"] == "completed"

    kept = make_target("kept-target")
    finished(kept, harness_root)
    assert rerun_after_deleting_the_run_directory(kept, harness_root)[0] == 1


#: What a first run of this story produces on a repository whose story branch
#: does not exist: the stages it invokes in order, the run-directory artifacts
#: it leaves, the events it logs, and the commit it ends on. Named rather than
#: recovered — the check this story adds must change none of them, and a
#: regression in any one fails by name rather than as "the two runs differ".
#: The artifacts are required rather than exhaustive, per the standing rule
#: that a run directory is not asserted as an exact set: every story that adds
#: an artifact would otherwise fail an assertion about something else.
FIRST_RUN_STAGES = ["implementer", "tester", "verifier", "documenter"]
FIRST_RUN_ARTIFACTS = [
    "changed-files.json", "clean-clone-result.json", "completion-report.md",
    "events.log", "execution-history.json", "implementation-summary.md",
    "state.json", "test-results.json", "tester-changed-files.json",
    "verification-result.json", "verification/iteration-1.json",
]
FIRST_RUN_EVENTS = [
    f"workflow started for {STORY_ID}",
    "implementer stage started", "implementer stage completed",
    "tester stage started", "tester stage completed",
    "verifier stage started", "verification passed",
    "clean-clone suite passed with the story committed",
    "documenter stage started", "documenter stage completed",
    f"story completed on branch {STORY_BRANCH}",
]


def test_a_first_run_of_a_story_whose_branch_does_not_exist_is_unaffected(
    target, harness_root,
):
    """The check changes nothing about a run whose branch carries no finished
    work, stated as the run's own output rather than as equality with a module
    recovered out of git history.

    Each thing the comparison used to compare is named: the stages invoked and
    their order, the artifacts the run directory holds, the events logged and
    their order, the state the run records, and the commit message it ends on.

    Its control is the re-run below on the same repository once it *has*
    finished, which produces none of this and is refused — so "unaffected" is
    the branch being empty of finished work rather than the check never
    firing.
    """
    assert STORY_BRANCH not in branches(target)

    code, runner = run(target, harness_root)

    assert code == 0
    assert runner.calls == FIRST_RUN_STAGES
    present = artifacts_in(target)
    assert set(FIRST_RUN_ARTIFACTS) <= set(present)
    assert "escalation-summary.md" not in present
    assert messages(target) == FIRST_RUN_EVENTS

    state = state_of(target)
    assert state["status"] == "completed"
    assert state["branch"] == STORY_BRANCH
    assert state["retry_count"] == 0

    assert git(target, "log", "-1", "--format=%B").stdout.rstrip("\n") \
        == PRE_EXTRACTION_MESSAGE


def test_each_first_run_expectation_above_can_fail(target, harness_root):
    """The control for the assertion above, which names its subjects: a named
    subject no run could violate would be indistinguishable from one no run
    does. The same repository, one finished run later, satisfies none of
    them."""
    finished(target, harness_root)
    code, runner = rerun_after_deleting_the_run_directory(target, harness_root)

    assert code == 1
    assert runner.calls != FIRST_RUN_STAGES
    assert not run_dir_of(target).exists()


def test_a_resume_of_an_escalated_run_is_unaffected(make_target, harness_root):
    """An escalation's two commits carry the escalation subject rather than a
    completion subject, so the resume proceeds to the recorded stage.

    The control is the same resumed repository once it has completed, which the
    same reading does report — so the green below is the subject test and not
    the reader having stopped seeing commits.
    """
    target = make_target("escalated-target")
    code, _ = run(target, harness_root, verdicts=[FAIL_AT_ONCE])
    assert code == 2
    assert state_of(target)["status"] == "escalated"
    assert git(target, "rev-list", f"{DEFAULT_BRANCH}..{STORY_BRANCH}",
               "--count").stdout.strip() != "0"
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) == []

    write(target / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")
    commit(target, "the developer's fix")

    code, runner = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert runner.calls != []
    assert state_of(target)["status"] == "completed"
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) != []


def test_a_base_commit_that_restores_a_story_artifact_does_not_refuse(
    make_target, harness_root,
):
    """A commit that merely touches the story's files is not a finished run.

    The control is the same repository with a real completion commit on the
    same base branch, which is reported and does refuse.
    """
    target = make_target("artifact-commit-target")
    write(target / ".harness" / "stories" / f"{STORY_ID}.yaml", STORY)
    commit(target, f"Restore {STORY_ID}: {STORY_TITLE}")

    assert story_coordinator.completion_commits(
        target, DEFAULT_BRANCH, STORY_ID) == []
    code, runner = run(target, harness_root)
    assert code == 0
    assert runner.calls != []

    control = make_target("artifact-commit-control-target")
    git(control, "commit", "-q", "--allow-empty",
        "-m", story_coordinator.completion_commit_message(
            story_coordinator.RunState(story_id=STORY_ID, branch=STORY_BRANCH),
            STORY_TITLE))
    # Reachable from the branch the run would start on, which is the whole of
    # the difference between this commit and the artifact commit above.
    git(control, "branch", STORY_BRANCH)
    assert story_coordinator.completion_commits(
        control, DEFAULT_BRANCH, STORY_ID) != []
    assert run(control, harness_root)[0] == 1


# --------------------------------------------------------------------------
# The evidence, read directly
# --------------------------------------------------------------------------


def completion_commit(target_root: Path, story_id: str = STORY_ID,
                      title: str = STORY_TITLE, branch: str | None = None) -> str:
    """Put a commit on `branch` shaped exactly as `_complete` writes one."""
    if branch is not None:
        git(target_root, "checkout", "-q", "-B", branch)
    message = story_coordinator.completion_commit_message(
        story_coordinator.RunState(story_id=story_id, branch=branch or ""), title)
    git(target_root, "commit", "-q", "--allow-empty", "-m", message)
    return git(target_root, "rev-parse", "--short", "HEAD").stdout.strip()


def test_completion_commits_reports_this_storys_finished_run_and_no_other(
    target, harness_root,
):
    """One line per match, "<abbrev sha> <subject>", newest first."""
    finished(target, harness_root)
    sha = git(target, "log", "-1", "--format=%h", STORY_BRANCH).stdout.strip()
    subject = git(target, "log", "-1", "--format=%s", STORY_BRANCH).stdout.strip()

    reported = story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID)
    assert reported == [f"{sha} {subject}"]
    # The absence, with its control in the same reading: another story's id
    # reports nothing from the very branch that reported for this one.
    assert story_coordinator.completion_commits(target, STORY_BRANCH, "story-999") == []

    newest = completion_commit(target, branch=STORY_BRANCH)
    assert story_coordinator.completion_commits(
        target, STORY_BRANCH, STORY_ID)[0].startswith(newest)
    assert len(story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID)) == 2


def test_a_subject_without_the_marker_is_not_a_finished_run(target):
    """A hand-written commit about the story wears the completion subject; only
    `_complete` writes the marker.

    The control is the same subject with the marker in its body, on the same
    branch, which is reported.
    """
    git(target, "checkout", "-q", "-b", STORY_BRANCH)
    git(target, "commit", "-q", "--allow-empty",
        "-m", f"{STORY_ID}: {STORY_TITLE}\n\nI wrote this by hand.")
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) == []

    completion_commit(target)
    assert len(story_coordinator.completion_commits(
        target, STORY_BRANCH, STORY_ID)) == 1


def test_the_marker_under_another_storys_subject_is_that_storys_run(target):
    """Both pieces of evidence are required together: the marker alone would
    report another story's finished run as this one's."""
    git(target, "checkout", "-q", "-b", STORY_BRANCH)
    completion_commit(target, story_id="story-002", title="Another story")

    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) == []
    assert story_coordinator.completion_commits(
        target, STORY_BRANCH, "story-002") != []      # the control


def test_a_subject_that_is_the_prefix_and_nothing_else_is_not_reported(target):
    """A completion commit always carries a title, so `story-001: ` alone is
    not one — with the same subject plus a title as the control."""
    git(target, "checkout", "-q", "-b", STORY_BRANCH)
    git(target, "commit", "-q", "--allow-empty",
        "-m", f"{STORY_ID}: \n\n{story_coordinator.COMPLETION_COMMIT_MARKER}")
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) == []

    completion_commit(target)
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) != []


def test_a_multi_line_body_does_not_split_one_commit_into_two(target):
    """The record and unit separators, exercised: a body carrying blank lines
    and the shapes of the separators' neighbours is still one record.

    The control is the same repository's real completion commit, counted in the
    same reading.
    """
    git(target, "checkout", "-q", "-b", STORY_BRANCH)
    git(target, "commit", "-q", "--allow-empty", "-m",
        f"{STORY_ID}: {STORY_TITLE}\n\n"
        f"{story_coordinator.COMPLETION_COMMIT_MARKER}\n\n"
        f"A trailing paragraph.\n\nstory-999: not a subject\n")
    assert len(story_coordinator.completion_commits(
        target, STORY_BRANCH, STORY_ID)) == 1
    assert story_coordinator.completion_commits(
        target, STORY_BRANCH, "story-999") == []


def test_a_branch_that_does_not_exist_establishes_nothing(target):
    """The one-directional bias: refuse for what can be established, never for
    what cannot.

    The control is the same reading of a branch that does exist and carries the
    commit, so the empty list is the missing branch rather than the reader
    having stopped seeing commits.
    """
    assert STORY_BRANCH not in branches(target)
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) == []

    completion_commit(target, branch=STORY_BRANCH)
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) != []


def test_a_root_that_is_not_a_repository_establishes_nothing(tmp_path):
    """The same bias at the other failure: no repository, nothing established.

    The control is the same directory made a repository with the same commit in
    it, which does report.
    """
    root = tmp_path / "not-a-repo"
    root.mkdir()
    assert story_coordinator.completion_commits(root, STORY_BRANCH, STORY_ID) == []

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    git(root, "commit", "-q", "--allow-empty", "-m", "initial")
    completion_commit(root, branch=STORY_BRANCH)
    assert story_coordinator.completion_commits(root, STORY_BRANCH, STORY_ID) != []


def test_a_failing_git_invocation_establishes_nothing(target, monkeypatch):
    """The third failure the story names, forced rather than described: the
    branch resolves and the log invocation fails.

    The control is the same repository read without the failure planted, which
    reports the commit — so the empty list is the failure rather than the
    repository having nothing in it.
    """
    completion_commit(target, branch=STORY_BRANCH)
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) != []

    real_git = story_coordinator._git

    def failing_log(root, *args):
        if args and args[0] == "log":
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="boom")
        return real_git(root, *args)

    monkeypatch.setattr(story_coordinator, "_git", failing_log)
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) == []


def test_the_check_reads_and_only_reads(target, harness_root):
    """No commit, no branch, no checkout, no index change, no stash — read
    around the call directly and around the refused run that makes it."""
    finished(target, harness_root)
    shutil.rmtree(run_dir_of(target))
    git(target, "checkout", "-q", DEFAULT_BRANCH)

    before = snapshot(target)
    assert story_coordinator.completion_commits(target, STORY_BRANCH, STORY_ID) != []
    assert snapshot(target) == before

    assert run(target, harness_root)[0] == 1
    assert snapshot(target) == before


def snapshot(root: Path) -> dict:
    return {
        "head": git(root, "rev-parse", "HEAD").stdout,
        "branches": git(root, "branch", "--list", "--all").stdout,
        "stash": git(root, "stash", "list").stdout,
        "index": git(root, "ls-files", "--stage").stdout,
        "status": git(root, "status", "--porcelain").stdout,
    }


@pytest.mark.parametrize("mutation,field", [
    (lambda root: git(root, "add", "-A"), "index"),
    (lambda root: git(root, "stash", "push", "-u", "-q"), "stash"),
    (lambda root: git(root, "branch", "some-other-branch"), "branches"),
    (lambda root: commit(root, "a commit"), "head"),
])
def test_that_same_comparison_reports_each_thing_the_check_must_not_do(
    make_target, mutation, field,
):
    """The control for the reading above, once per thing the story forbids."""
    root = make_target(f"mutation-{field}")
    write(root / "stray.txt", "something to stage or stash\n")
    before = snapshot(root)
    mutation(root)
    after = snapshot(root)
    assert after != before
    assert after[field] != before[field]


# --------------------------------------------------------------------------
# The extraction of `_complete`'s message
# --------------------------------------------------------------------------


def test_the_composed_message_is_byte_for_byte_the_pre_extraction_string():
    """Asserted against the string the old inline f-string produced, written
    out here and read back off `_complete` as it stood before the extraction,
    rather than by inspection.

    The old text is *read* rather than loaded as a module: what it says is the
    whole subject, and a recovered coordinator no longer runs anyway.
    """
    state = story_coordinator.RunState(story_id=STORY_ID, branch=STORY_BRANCH)
    composed = story_coordinator.completion_commit_message(state, STORY_TITLE)
    assert composed == PRE_EXTRACTION_MESSAGE

    old = coordinator_function("_complete", BASELINE)
    assert '"commit", "-m", f"{state.story_id}: {title}\\n\\n' \
        'Implemented by the l5 harness story workflow."' in old


def test_the_message_a_real_run_commits_is_that_message(target, harness_root):
    """The other end of the same claim: what git holds after a run, compared
    against the composed string rather than against a subject searched for.

    The control is the commit before it, which the same reading reports as
    different — so the equality is the message and not the comparison.
    """
    parent = git(target, "rev-parse", "HEAD").stdout.strip()
    finished(target, harness_root)

    committed = git(target, "log", "-1", "--format=%B").stdout.rstrip("\n")
    assert committed == PRE_EXTRACTION_MESSAGE
    assert git(target, "log", "-1", "--format=%B", parent).stdout.rstrip("\n") \
        != PRE_EXTRACTION_MESSAGE


def test_the_extraction_is_the_only_edit_the_story_made_to_complete():
    """`_complete` differs from its pre-story text in the commit-message line
    and in nothing else.

    The control is `run_story`, read the same way, which differs in many lines —
    so "one changed line" is a diff that can report more.

    Both sides are read as text, at this story's own two bounds. The "after"
    side is this story's endpoint rather than today's working tree, so a later
    story editing `_complete` is not story-027 editing it.
    """
    def changed_lines(name: str) -> list[str]:
        before = coordinator_function(name, BASELINE)
        after = coordinator_function(name, ENDPOINT)
        return [line for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), n=0)
            if line[:1] in "+-" and not line.startswith(("---", "+++"))]

    complete = changed_lines("_complete")
    assert len(complete) == 2
    assert all('"commit", "-m"' in line for line in complete), complete
    assert len(changed_lines("run_story")) > 2


# --------------------------------------------------------------------------
# What the new code may not name
# --------------------------------------------------------------------------


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(source, node)


#: The functions the story says must carry no name of their own: the
#: recognizer, and the code that composes the message it recognizes.
NAMELESS = ("completion_commits", "completion_commit_message",
            "completion_commit_subject")


def names_in(source: str) -> list[str]:
    """Every branch name, base branch name or story id a function's executable
    code writes out for itself, read off its string constants."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            for name in ("main", "master", "story/", "story-0"):
                if name in text:
                    found.append(f"{name}:{text}")
    return sorted(set(found))


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


@pytest.mark.parametrize("name", NAMELESS)
def test_the_recognizer_names_no_branch_base_branch_or_story_id(name):
    """The branch comes from the config prefix through `story_branch` and the
    story id from the run, so neither is written into these functions."""
    body = executable_source(_function_source(COORDINATOR_SOURCE, name))
    assert names_in(body) == [], name


@pytest.mark.parametrize("planted", [
    '    branch = "story/story-001"\n',
    '    base = "main"\n',
    '    if story_id == "story-027":\n        return []\n',
])
def test_the_same_scan_reports_a_name_that_was_planted(planted):
    """The control for the scan above, once per kind of name the story forbids:
    planted into a copy of the function, which the same scan does report."""
    body = executable_source(
        _function_source(COORDINATOR_SOURCE, "completion_commits"))
    lines = body.splitlines(keepends=True)
    doctored = "".join([lines[0], planted, *lines[1:]])
    assert names_in(doctored) != []


def test_the_check_says_in_the_code_that_the_evidence_is_a_finished_run():
    """The reasoning that replaced the ahead-of-base test, stated where the
    check is rather than only in the story.

    The control is the same search over a rendering with the prose stripped
    out, which finds nothing — otherwise a search that had stopped matching
    would look the same as prose that had been deleted.
    """
    lines = COORDINATOR_SOURCE.splitlines()
    assert [line for line in lines if "finished run" in line]
    assert [line for line in lines if "ahead-of-base" in line]
    assert [line for line in lines if "partial progress" in line]
    stripped = executable_source(COORDINATOR_SOURCE).splitlines()
    assert [line for line in stripped if "finished run" in line] == []
    assert [line for line in stripped if "ahead-of-base" in line] == []


# --------------------------------------------------------------------------
# The refusal the new one must not have replaced
# --------------------------------------------------------------------------


#: The already-ended refusal's message, sliced out of `run_story`'s own text.
_ALREADY_ENDED_HEAD = 'f"{story_id} already ended with status'
_ALREADY_ENDED_TAIL = "file=sys.stderr,"


def already_ended_message(bound: str) -> str:
    body = coordinator_function("run_story", bound)
    head = body.index(_ALREADY_ENDED_HEAD)
    return body[head:body.index(_ALREADY_ENDED_TAIL, head)]


def test_a_completed_state_still_meets_the_already_ended_refusal(
    target, harness_root, capsys,
):
    """The refusal this story did not replace, named rather than compared
    against a module that produced it once.

    Two statements. What the refusal *says* is asserted as its own text — the
    status, the run directory, the branch, and what to do about each — against
    a repository whose run directory is still there. That this story left it
    alone is asserted where the text lives: the print statement is
    byte-identical at both ends of this story's own commit range, read as text
    rather than by running the module that produced it.

    The control is the new refusal on the same repository with only the run
    directory deleted, which is a different message — so this is the
    completed-state path being untouched rather than both refusals having
    collapsed into one.
    """
    finished(target, harness_root)

    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    current_message = capsys.readouterr().err

    assert f"{STORY_ID} already ended with status 'completed'." in current_message
    assert f"Inspect {run_dir_of(target)} to review it." in current_message
    assert f"delete {run_dir_of(target)} *and* reset branch" in current_message
    assert f"{STORY_BRANCH}, which still holds the finished work." in current_message
    assert already_ended_message(ENDPOINT) == already_ended_message(BASELINE)

    capsys.readouterr()
    assert rerun_after_deleting_the_run_directory(target, harness_root)[0] == 1
    assert capsys.readouterr().err != current_message


# --------------------------------------------------------------------------
# The defect this closes
# --------------------------------------------------------------------------


#: What the defect looked like, and what the reproduction that used to sit
#: here demonstrated. Frozen by story-029, which deleted the reproduction: it
#: recovered the pre-story coordinator out of git history and ran it, and that
#: module no longer executes against today's workflow at all. A defect whose
#: only account was a deleted test has lost its account, so the account is
#: committed evidence a reader can find.
RERUN_EVIDENCE = (REPO_ROOT / ".harness" / "runs-archive"
                  / "story-027-rerun-onto-finished-branch" / "evidence.json")


def test_the_defect_this_closes_is_recorded_and_the_same_move_is_refused(
    target, harness_root,
):
    """The defect, read out of the frozen evidence, and the same move made
    against today's coordinator.

    The evidence records what the earlier coordinator did on exactly this
    move: completed, invoked agents, recorded `completed`, and left the branch
    byte-identical to the finished work it re-ran onto. Today's coordinator is
    put through the identical move and refuses it, leaving the branch where it
    was — so the record is a control rather than a description.
    """
    evidence = json.loads(RERUN_EVIDENCE.read_text(encoding="utf-8"))
    demonstrated = evidence["reproduction"]["demonstrated"]

    assert demonstrated["earlier_coordinator_declares_completion_commits"] is False
    assert demonstrated["earlier_coordinator_exit_code"] == 0
    assert demonstrated["earlier_coordinator_recorded_status"] == "completed"
    assert demonstrated["diff_against_the_finished_work"] == ""
    assert demonstrated["current_coordinator_exit_code"] == 1
    assert evidence["what_holds_now"]["recogniser"] in COORDINATOR_SOURCE

    finished(target, harness_root)
    head_before = git(target, "rev-parse", STORY_BRANCH).stdout.strip()
    code, runner = rerun_after_deleting_the_run_directory(target, harness_root)

    assert code == demonstrated["current_coordinator_exit_code"]
    assert runner.calls == []
    assert git(target, "rev-parse", STORY_BRANCH).stdout.strip() == head_before


# --------------------------------------------------------------------------
# The assertion this story's pre-flight forced to be repointed
# --------------------------------------------------------------------------


def test_reverting_the_repointed_story_020_assertion_re_breaks_it():
    """story-020's `_complete` comparison read its "after" side from today's
    working tree, and this story extracted `_complete`'s message.

    Asserted both ways so the repoint is shown to have been necessary and not
    to have been made vacuous: read against the working tree the two sources
    differ, which is the red the revert restores, and read at story-020's own
    endpoint — the bound the repointed assertion uses — they are equal.
    """
    story_020 = REPO_ROOT / "tests" / "test_story_020_validation.py"
    span = story_commit_range(story_020)
    assert span.committed, "story-020 is in this history"

    def at(name: str, bound: str) -> str:
        return coordinator_function(name, bound, validation_file=story_020)

    assert inspect.getsource(story_coordinator._complete) \
        != at("_complete", BASELINE)                    # the reverted form: red
    assert at("_complete", ENDPOINT) \
        == at("_complete", BASELINE)                    # the repointed form
    assert at("_escalate", ENDPOINT) \
        != at("_escalate", BASELINE)                    # its control, unchanged
