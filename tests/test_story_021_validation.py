"""Independent validation for story-021: a run does not commit what it did
not produce.

The subject is a *pre-flight that refuses*, so almost nothing here is asserted
from source. A target repository is built under tmp_path, something the run
did not produce is left sitting in its working tree, and the coordinator is
asked to run the story. What the check does is whatever that run does to that
directory.

The story's guarantee is negative in both of its halves — nothing is created
when a run is refused, and nothing that predated the run is in what it commits
— so every absence below is written beside a demonstration that the same check
can report the violation it exists to catch:

  * "the refusal left no run directory, no state.json, no log, no new branch
    and called no agent" sits beside the identical run on a clean tree, which
    creates all five;
  * "the refusal names the dirty paths" sits beside a path that is *not* dirty,
    which is not named, and beside the same path made dirty, which is;
  * "a clean fresh run is unaffected" is compared against the coordinator as it
    stood before the check existed, loaded out of git and run against an
    identical target — and that module is shown to differ, by proceeding on the
    very tree the current one refuses;
  * "an escalated resume with a dirty tree refuses" sits beside the same run
    with the tree committed, which resumes, and beside the same dirty tree
    under a `running` state, which is not refused;
  * "the completion commit carries nothing that predated the run" sits beside
    the developer's own commit, which does carry it, and beside the one
    excluded case — a resumed crashed run — whose commit does too;
  * "a story artifact never appears in a story commit again" sits beside
    story-013's archived patch, where one does, and beside the pre-story
    coordinator reproducing that patch's shape on the same fixture;
  * "no flag, environment variable or configuration key skips the check" is a
    scan paired with a copy of the check with a bypass planted in it, and an
    attempt that sets seven environment variables and five configuration keys
    and is refused anyway;
  * "the check creates no commit, branch, stash or index change" sits beside
    the same before/after comparison around calls that do make each.

Nothing here invokes a model: every run goes through a fake agent runner.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE, ENDPOINT, function_source_at,
                      repository_file_at,
                      story_commit_range)

import story_coordinator
from agent_runner import AgentResult

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
COORDINATOR_PATH = REPO_ROOT / "orchestration" / "story_coordinator.py"
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")

STORY_ID = "story-001"
DEFAULT_BRANCH = "main"
STORY_BRANCH = f"story/{STORY_ID}"
#: The sample story's title, which `_complete` puts in the commit subject.
STORY_TITLE = "Sample story for coordinator tests"

#: The file the documenter edits in the target, so "the documenter's outputs"
#: in the completion commit is a real edit rather than an argument.
DOC_OUTPUT = ".harness/docs/ARCHITECTURE.md"

#: A file no stage produces, used wherever the story says "already sitting
#: there uncommitted when the run started".
STRAY = "stray-nothing-produced-this.txt"

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


def failing(attempt: int, *, retry: bool) -> dict:
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
    }


FAIL_AT_ONCE = failing(1, retry=False)


# --------------------------------------------------------------------------
# The target repository
#
# Built here rather than taken from the shared fixture because most tests
# below hold a subject and a control side by side, and two runs of one story
# in one target directory are one resumed run.
#
# `.harness/runs/` and `.harness/logs/` are ignored exactly as this repository
# ignores them, which is what makes a terminal commit's contents a statement
# about the target's own files rather than about run bookkeeping.
# --------------------------------------------------------------------------

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

CONFIG = """\
project: clean-tree-target
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


def build_target(root: Path, story_text: str = STORY,
                 story_id: str = STORY_ID) -> Path:
    write(root / ".harness" / "config.yaml", CONFIG)
    write(root / ".harness" / "stories" / f"{story_id}.yaml", story_text)
    write(root / ".harness" / "standards" / "coding.md", "# Coding\n- simple\n")
    write(root / ".harness" / "standards" / "testing.md", "# Testing\n- test it\n")
    write(root / DOC_OUTPUT, "# Architecture\n")
    write(root / "src" / "app.py", APP_AT_HEAD)
    write(root / "tests" / "test_existing.py", "def test_nothing():\n    assert True\n")
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
    return make_target("clean-tree-target")


@pytest.fixture
def harness_root() -> Path:
    return REPO_ROOT


# --------------------------------------------------------------------------
# The fake runner
#
# Each stage writes the artifacts its declaration requires, makes the edit it
# holds in the target's working tree, and writes to the log path it was given
# — as the real runner does, which is what makes "no log" an observable
# consequence of no agent having been invoked rather than of this fake being
# quiet.
# --------------------------------------------------------------------------


class Runner:
    def __init__(self, target_root: Path, verdicts: list | None = None,
                 story_id: str = STORY_ID):
        self.target_root = target_root
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.verdicts = verdicts or [PASS]
        self.calls: list[str] = []
        self.logs: list[Path] = []

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
            self.logs.append(log)
        attempt = max(1, self.calls.count("implementer"))

        if stage == "implementer":
            write(self.target_root / "src" / "app.py",
                  APP_AT_HEAD + f"print('attempt {attempt}')\n")
            write(self.target_root / "src" / f"attempt_{attempt}.py",
                  f"value = {attempt}\n")
            write_json(self.run_dir / "changed-files.json", {
                "modified": ["src/app.py"],
                "created": [f"src/attempt_{attempt}.py"],
                "deleted": [],
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
                "modified": [],
                "created": [f"tests/test_attempt_{attempt}.py"],
                "deleted": [],
            })
        elif stage == "verifier":
            verdict = self._nth(self.verdicts, self.calls.count(stage) - 1)
            write_json(self.run_dir / "verification-result.json", verdict)
        elif stage == "documenter":
            write(self.target_root / DOC_OUTPUT,
                  f"# Architecture\n\nDocumented on attempt {attempt}.\n")
            write(self.run_dir / "documentation-report.md", "Documented.\n")
        return AgentResult(ok=True, result_text=f"{stage} done")


def run(target_root: Path, harness: Path = REPO_ROOT, verdicts: list | None = None,
        runner: Runner | None = None, start_stage: str | None = None,
        coordinator=story_coordinator) -> tuple[int, Runner]:
    runner = runner or Runner(target_root, verdicts)
    code = coordinator.run_story(
        STORY_ID, harness, target_root, runner, start_stage=start_stage)
    return code, runner


def run_dir_of(target_root: Path, story_id: str = STORY_ID) -> Path:
    return target_root / ".harness" / "runs" / story_id


def log_of(target_root: Path, story_id: str = STORY_ID) -> Path:
    return target_root / ".harness" / "logs" / f"{story_id}.log"


def state_of(target_root: Path) -> dict:
    return json.loads((run_dir_of(target_root) / "state.json").read_text())


def branches(target_root: Path) -> list[str]:
    return sorted(line[2:].strip()
                  for line in git(target_root, "branch", "--list").stdout.splitlines())


def files_in(root: Path, revision: str = "HEAD") -> list[str]:
    return git(root, "show", "--name-only", "--format=", revision).stdout.split()


def subject_of(root: Path, revision: str = "HEAD") -> str:
    return git(root, "log", "-1", "--format=%s", revision).stdout.strip()


def messages(target_root: Path) -> list[str]:
    log = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line.split("] ", 1)[1] for line in log.splitlines() if "] " in line]


def event_kinds(target_root: Path) -> list[str]:
    history = json.loads(
        (run_dir_of(target_root) / "execution-history.json").read_text())
    return [entry["event"] for entry in history]


def artifacts_in(target_root: Path) -> list[str]:
    run_dir = run_dir_of(target_root)
    return sorted(p.relative_to(run_dir).as_posix()
                  for p in run_dir.rglob("*") if p.is_file())


def escalate(target_root: Path, harness: Path = REPO_ROOT) -> Runner:
    code, runner = run(target_root, harness, verdicts=[FAIL_AT_ONCE])
    assert code == 2, "the shape was meant to escalate"
    assert state_of(target_root)["status"] == "escalated"
    return runner


def commit(target_root: Path, message: str = "the developer's own work") -> str:
    git(target_root, "add", "-A")
    git(target_root, "commit", "-q", "--allow-empty", "-m", message)
    return git(target_root, "rev-parse", "HEAD").stdout.strip()


def crashed_run(target_root: Path, stage: str = "tester") -> None:
    """Leave the run directory as a process that died mid-run would leave it.

    Nothing commits when a process dies, so the state says `running` and the
    working tree is whatever that run had got to.
    """
    run_dir_of(target_root).mkdir(parents=True, exist_ok=True)
    story_coordinator.save_state(
        run_dir_of(target_root),
        story_coordinator.RunState(story_id=STORY_ID, branch=STORY_BRANCH,
                                   status="running", current_stage=stage),
    )
    git(target_root, "checkout", "-q", "-b", STORY_BRANCH)


# --------------------------------------------------------------------------
# The coordinator as it stood before this story
#
# Every "unaffected" claim is made against this rather than against a shape
# written here, so the control for each is the thing the story did change,
# observed the same way.
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

    The counterpart of `pre_story`, and the upper bound a "this story did not
    change X" comparison needs. Against today's working tree it asks what the
    file looks like *now*, which a later story changes without this story
    having done anything — the HEAD-baseline trap the architecture document
    records. story-024 is where it bit: it moved the escalation summary's
    construction out of `_escalate`, which story-021 left exactly as it found
    it.
    """
    return repository_file_at(path, validation_file=Path(__file__),
                              bound=ENDPOINT, repo=REPO_ROOT)


def coordinator_function(name: str, bound: str) -> str:
    """One coordinator function's source text at one end of this story's range.

    story-029 retired the pre-story and endpoint *modules* this file used to
    load: a coordinator recovered out of history runs against today's
    workflow, schemas and config, and stops running as soon as any of them
    legitimately changes. A comparison that only ever read a function's text
    never needed a running module, so it reads the text.
    """
    return function_source_at(COORDINATOR_REL, name, validation_file=Path(__file__),
                              bound=bound, repo=REPO_ROOT)


# --------------------------------------------------------------------------
# A fresh run whose tree is dirty
# --------------------------------------------------------------------------


@pytest.fixture
def refused(target, harness_root, capsys):
    """A fresh run refused for a dirty tree, with what preceded it recorded."""
    write(target / STRAY, "no stage wrote this\n")
    before = {
        "head": git(target, "rev-parse", "HEAD").stdout.strip(),
        "branches": branches(target),
    }
    capsys.readouterr()
    code, runner = run(target, harness_root)
    captured = capsys.readouterr()
    return code, runner, target, before, captured.err


@pytest.fixture
def accepted(make_target, harness_root):
    """The same run on the same repository with nothing uncommitted."""
    target = make_target("accepted-target")
    code, runner = run(target, harness_root)
    return code, runner, target


def test_a_fresh_run_with_a_dirty_tree_refuses_and_leaves_nothing_behind(refused):
    """Each absence separately, because "exit 1" alone would hold for a run
    that got as far as creating a directory and then failed."""
    code, runner, target, before, _ = refused
    assert code == 1
    assert runner.calls == []                              # no agent
    assert not run_dir_of(target).exists()                 # no run directory
    assert not (run_dir_of(target) / "state.json").exists()  # no state
    assert not log_of(target).exists()                     # no log
    assert branches(target) == before["branches"]          # no new branch
    assert STORY_BRANCH not in branches(target)
    assert git(target, "rev-parse", "HEAD").stdout.strip() == before["head"]
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() \
        == DEFAULT_BRANCH


def test_the_same_run_on_a_clean_tree_creates_every_one_of_those(accepted):
    """The control for the five absences above: one repository state differs —
    the stray file — and each thing the refusal did not leave behind is here."""
    code, runner, target = accepted
    assert code == 0
    assert runner.calls != []
    assert run_dir_of(target).is_dir()
    assert (run_dir_of(target) / "state.json").is_file()
    assert log_of(target).is_file()
    assert STORY_BRANCH in branches(target)
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() \
        == STORY_BRANCH


def test_the_refusal_names_the_dirty_paths_and_says_what_clears_it(
    target, harness_root, capsys,
):
    """Every kind of dirtiness `git add -A` would absorb — an untracked file, a
    modified tracked file, a deleted tracked file — named individually.

    The control is `src/app.py`, which is left alone and is not named, and then
    the same file made dirty in a second run, where it is.
    """
    write(target / STRAY, "no stage wrote this\n")
    write(target / DOC_OUTPUT, "# Architecture\n\nedited by hand\n")
    (target / "tests" / "test_existing.py").unlink()

    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    message = capsys.readouterr().err

    for path in (STRAY, DOC_OUTPUT, "tests/test_existing.py"):
        assert path in message, path
    assert "Commit or stash them, then run the story again." in message
    assert "src/app.py" not in message              # the absence...

    write(target / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")
    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    assert "src/app.py" in capsys.readouterr().err  # ...and its control


def test_the_refusal_is_the_shape_the_other_pre_flight_refusals_take(
    target, harness_root, capsys,
):
    """One message per problem under a header, and nothing on stdout — the
    same shape a bad story artifact is refused in, which is asserted beside it
    rather than described."""
    write(target / STRAY, "x\n")
    write(target / "src" / "another.py", "y\n")
    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    dirty = capsys.readouterr()

    assert dirty.out == ""
    listed = [line for line in dirty.err.splitlines() if line.startswith("  - ")]
    assert listed == sorted([f"  - {STRAY}", "  - src/another.py"])

    # The control: the same repository refused for the other pre-flight reason
    # produces the same shape, so "one message per problem" is a property of
    # the refusal path rather than of this one caller.
    clean = build_target(target.parent / "bad-story-target",
                         story_text="story:\n  id: story-001\n")
    capsys.readouterr()
    assert run(clean, harness_root)[0] == 1
    other = capsys.readouterr()
    assert other.out == ""
    assert [line for line in other.err.splitlines() if line.startswith("  - ")]
    assert other.err.splitlines()[0] != dirty.err.splitlines()[0]


def test_a_gitignored_path_is_not_what_the_check_is_about(
    make_target, harness_root,
):
    """`.harness/runs/` never enters a commit, so a run does not refuse for it.

    The control is the same file at the same path with the ignore removed,
    which does refuse — so the green above is the ignore rule and not the
    check having stopped looking at untracked files.
    """
    ignored = make_target("ignored-target")
    write(ignored / ".harness" / "runs" / "leftover.txt", "from an old run\n")
    assert run(ignored, harness_root)[0] == 0

    watched = make_target("watched-target")
    write(watched / ".gitignore", ".harness/logs/\n")
    commit(watched, "stop ignoring the run directory")
    write(watched / ".harness" / "runs" / "leftover.txt", "from an old run\n")
    assert run(watched, harness_root)[0] == 1


# --------------------------------------------------------------------------
# A clean fresh run, against a run made before the check existed
# --------------------------------------------------------------------------


#: What a clean fresh run of the sample story produces, named rather than
#: recovered: the stages it invokes in order, the run-directory artifacts it
#: leaves, the events it logs, the state keys it records, and the commit it
#: ends on. Every entry is something the pre-story coordinator produced and
#: this one still produces; the point of the check this story adds is that it
#: changes none of them when the tree is clean.
CLEAN_RUN_STAGES = ["implementer", "tester", "verifier", "documenter"]
CLEAN_RUN_ARTIFACTS = [
    "changed-files.json", "clean-clone-result.json", "escalation-summary.md",
    "events.log", "execution-history.json", "implementation-summary.md",
    "state.json", "test-results.json", "tester-changed-files.json",
    "verification-result.json",
]
CLEAN_RUN_EVENTS = [
    "workflow started for story-001",
    "implementer stage started", "implementer stage completed",
    "tester stage started", "tester stage completed",
    "verifier stage started", "verification passed",
    "clean-clone suite passed with the story committed",
    "documenter stage started", "documenter stage completed",
    "story completed on branch story/story-001",
]


def test_a_clean_fresh_run_is_what_it_was_before_the_check_existed(target,
                                                                   harness_root):
    """The check changes nothing about a run whose tree is clean, stated as
    the run's own output rather than as equality with a module recovered out
    of git history.

    Each thing the comparison used to compare is named here: the stages
    invoked and their order, the artifacts the run directory holds, the events
    logged and their order, the state keys recorded and the terminal values
    among them, and the commit the run ends on. A regression in any of them
    fails this by name instead of failing as "the two runs differ".

    Its own control is the dirty-tree run above, which produces none of them:
    that is what distinguishes "the check leaves a clean run alone" from "the
    check never fires".
    """
    code, runner = run(target, harness_root)

    assert code == 0
    assert runner.calls == CLEAN_RUN_STAGES                       # routing
    present = artifacts_in(target)
    assert set(CLEAN_RUN_ARTIFACTS) - {"escalation-summary.md"} <= set(present)
    assert "escalation-summary.md" not in present                 # artifacts
    assert messages(target) == CLEAN_RUN_EVENTS                   # events
    # Both renderings of the same stream, which is what `append_event` is for.
    assert len(event_kinds(target)) == len(CLEAN_RUN_EVENTS)
    assert event_kinds(target)[0] == "workflow-started"
    assert event_kinds(target)[-1] == "story-completed"

    state = state_of(target)                                      # state keys
    assert state["status"] == "completed"
    assert state["story_id"] == STORY_ID
    assert state["branch"] == STORY_BRANCH
    assert state["retry_count"] == 0
    assert state["escalation_commit"] == ""

    assert subject_of(target) == f"{STORY_ID}: {STORY_TITLE}"     # the commit
    assert set(files_in(target)) == recorded_paths(target) | {DOC_OUTPUT}


def test_each_clean_run_expectation_above_can_fail(target, harness_root):
    """The control for the assertion above, which names its subjects rather
    than deriving them: a named subject that no run could violate would be
    indistinguishable from one no run does.

    So each list is checked against a run that did *not* happen — the same
    repository refused for a dirty tree — and every one of them differs.
    """
    write(target / STRAY, "no stage wrote this\n")
    code, runner = run(target, harness_root)

    assert code == 1
    assert runner.calls != CLEAN_RUN_STAGES
    assert not run_dir_of(target).exists()
    assert subject_of(target) != f"{STORY_ID}: {STORY_TITLE}"


# --------------------------------------------------------------------------
# The three resume cases
# --------------------------------------------------------------------------


def test_a_resume_of_an_escalated_run_with_a_dirty_tree_refuses(
    target, harness_root, capsys,
):
    """story-020 leaves the tree clean when a run escalates, so anything
    uncommitted at the resume is the developer's own work.

    The refusal is checked for leaving the escalated run exactly as it was, not
    only for its exit code: a resume that had begun would have archived the
    interrupted attempt and moved the status.
    """
    escalate(target, harness_root)
    before_state = state_of(target)
    before_artifacts = artifacts_in(target)
    before_events = messages(target)
    write(target / STRAY, "the developer's fix, not yet committed\n")

    capsys.readouterr()
    code, runner = run(target, harness_root)
    message = capsys.readouterr().err

    assert code == 1
    assert runner.calls == []
    assert STRAY in message
    assert "Commit or stash them, then run the story again." in message
    assert state_of(target) == before_state
    assert artifacts_in(target) == before_artifacts
    assert messages(target) == before_events
    assert not (run_dir_of(target) / "attempts").exists()


def test_the_same_resume_proceeds_once_that_tree_is_committed(
    make_target, harness_root,
):
    """The control for the refusal above: the same escalated run, the same
    change, committed — which is exactly what the refusal asks for."""
    target = make_target("committed-resume-target")
    escalate(target, harness_root)
    write(target / STRAY, "the developer's fix\n")
    write(target / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")
    commit(target, "the developer's fix")

    code, runner = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert runner.calls != []
    assert state_of(target)["status"] == "completed"


def test_a_resume_of_a_run_still_running_is_not_refused_for_a_dirty_tree(
    make_target, harness_root,
):
    """Nothing commits when a process dies, so that tree holds the run's own
    unfinished work and refusing it would refuse the run its own state."""
    target = make_target("crashed-target")
    crashed_run(target)
    write(target / STRAY, "the crashed run's own unfinished work\n")

    code, runner = run(target, harness_root)
    assert code == 0
    assert runner.calls[0] == "tester"


def test_the_same_dirty_tree_under_an_escalated_state_is_refused(
    make_target, harness_root,
):
    """The control for the exclusion above, and the whole of the difference
    between the two: the same repository, the same dirty file, the same
    recorded stage — one state field apart."""
    target = make_target("escalated-state-target")
    crashed_run(target)
    write(target / STRAY, "identical to the crashed case\n")

    state = story_coordinator.load_state(run_dir_of(target))
    state.status = "escalated"
    story_coordinator.save_state(run_dir_of(target), state)

    code, runner = run(target, harness_root)
    assert code == 1
    assert runner.calls == []


def test_the_three_guards_on_an_escalated_resume_say_three_different_things(
    make_target, harness_root, capsys,
):
    """A clean tree with nothing changed refuses for repeating itself, a dirty
    tree refuses naming the paths, and a clean tree with a new commit resumes.

    Asserted as three outcomes of one escalated run rather than as one, because
    the claim is that they overlap nowhere.
    """
    target = make_target("guards-target")
    escalate(target, harness_root)

    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    repeated = capsys.readouterr().err

    write(target / STRAY, "uncommitted\n")
    capsys.readouterr()
    assert run(target, harness_root)[0] == 1
    dirty = capsys.readouterr().err

    write(target / "src" / "app.py", APP_AT_HEAD + "print('by hand')\n")
    commit(target, "the developer's fix")
    code, runner = run(target, harness_root, verdicts=[PASS])

    assert code == 0 and runner.calls != []
    assert repeated != dirty
    assert STRAY in dirty and STRAY not in repeated
    assert "Commit or stash" in dirty and "Commit or stash" not in repeated


# --------------------------------------------------------------------------
# A newly planned story, and the story artifact
# --------------------------------------------------------------------------


NEW_STORY_ID = "story-002"
NEW_STORY = STORY.replace(f"id: {STORY_ID}", f"id: {NEW_STORY_ID}")


def plan(target_root: Path, *, commit_it: bool) -> Path:
    """What l5-plan does to a target: write the artifact, and since story-023
    commit it. `commit_it=False` is the planner that did not."""
    path = target_root / ".harness" / "stories" / f"{NEW_STORY_ID}.yaml"
    write(path, NEW_STORY)
    if commit_it:
        commit(target_root, f"Plan {NEW_STORY_ID}")
    return path


def test_a_first_run_of_a_newly_planned_story_works_end_to_end(
    make_target, harness_root,
):
    """The case a strict rule breaks, and the case the planner committing its
    own artifact is what keeps working."""
    target = make_target("planned-target")
    plan(target, commit_it=True)

    runner = Runner(target, story_id=NEW_STORY_ID)
    code = story_coordinator.run_story(
        NEW_STORY_ID, harness_root, target, runner)

    assert code == 0
    assert runner.calls != []
    assert state_of_id(target, NEW_STORY_ID)["status"] == "completed"


def state_of_id(target_root: Path, story_id: str) -> dict:
    return json.loads(
        (run_dir_of(target_root, story_id) / "state.json").read_text())


def test_an_uncommitted_story_artifact_refuses_the_run_and_is_named(
    make_target, harness_root, capsys,
):
    """The story artifact is not exempt, so the planner that leaves it behind
    meets the same refusal as any other uncommitted file.

    The control is the test above: the same artifact, committed, runs to
    completion — so this refusal is about the artifact being uncommitted rather
    than about the story being new.
    """
    target = make_target("unplanned-target")
    path = plan(target, commit_it=False)
    relative = path.relative_to(target).as_posix()

    capsys.readouterr()
    runner = Runner(target, story_id=NEW_STORY_ID)
    code = story_coordinator.run_story(NEW_STORY_ID, harness_root, target, runner)
    message = capsys.readouterr().err

    assert code == 1
    assert runner.calls == []
    assert relative in message
    assert "Commit or stash them, then run the story again." in message
    assert not run_dir_of(target, NEW_STORY_ID).exists()


# --------------------------------------------------------------------------
# story-013's regression case
# --------------------------------------------------------------------------


ARCHIVE = REPO_ROOT / ".harness" / "runs-archive" / "story-013-vacuous-tests"


def test_story_013s_archived_commit_is_the_regression_this_closes():
    """The observed case, read out of the archive rather than described: the
    story commit carried the story's own artifact, and the run's record of what
    changed does not name it."""
    patch = (ARCHIVE / "pre-reset-branch.patch").read_text(encoding="utf-8")
    record = json.loads(
        (ARCHIVE / "run" / "changed-files.json").read_text(encoding="utf-8"))

    assert "diff --git a/.harness/stories/story-013.yaml" in patch
    assert "Subject: [PATCH] story-013:" in patch
    named = set(record["modified"]) | set(record["created"]) | set(record["deleted"])
    assert ".harness/stories/story-013.yaml" not in named


#: What the defect looked like when it happened, and what the reproduction
#: that used to sit here demonstrated. Frozen by story-029, which deleted the
#: reproduction: its control recovered the pre-story coordinator out of git
#: history and ran it, and that module no longer executes against today's
#: workflow at all. A defect whose only account was a deleted test has lost
#: its account, so the account is committed evidence.
ABSORBED_EVIDENCE = (REPO_ROOT / ".harness" / "runs-archive"
                     / "story-021-artifact-absorbed" / "evidence.json")


def test_a_story_artifact_no_longer_reaches_a_story_commit(
    make_target, harness_root,
):
    """story-013's shape reproduced on a fresh fixture: the artifact written
    before the run and left uncommitted.

    The control is the frozen evidence beside the archive, which records what
    the same fixture did before the check existed — the artifact inside the
    story's own commit — so the absence below is the check and not the
    fixture. The evidence is read rather than described, and it is
    cross-checked against the archived patch it points at by
    `test_story_013s_archived_commit_is_the_regression_this_closes` above and
    by the assertion below.
    """
    now = make_target("regression-target")
    plan(now, commit_it=False)
    runner = Runner(now, story_id=NEW_STORY_ID)
    assert story_coordinator.run_story(
        NEW_STORY_ID, harness_root, now, runner) == 1

    plan(now, commit_it=True)   # what the planner does since story-023
    runner = Runner(now, story_id=NEW_STORY_ID)
    assert story_coordinator.run_story(
        NEW_STORY_ID, harness_root, now, runner) == 0
    assert f".harness/stories/{NEW_STORY_ID}.yaml" not in files_in(now)

    evidence = json.loads(ABSORBED_EVIDENCE.read_text(encoding="utf-8"))
    demonstrated = evidence["reproduction"]["demonstrated"]
    assert evidence["reproduction"]["fixture_artifact"] \
        == f".harness/stories/{NEW_STORY_ID}.yaml"
    assert demonstrated["earlier_coordinator_absorbed_the_artifact"] is True
    assert demonstrated["earlier_coordinator_exit_code"] == 0
    assert demonstrated["current_coordinator_exit_code_with_the_artifact_uncommitted"] == 1
    assert demonstrated["current_coordinator_absorbed_the_artifact"] is False


def test_the_frozen_evidence_says_what_the_archive_says():
    """The evidence is only evidence if it agrees with the committed instance
    it points at, so the two are read together rather than trusted apart.

    The archive is read-only committed evidence, and every fact the record
    claims about it is checked against the archive's own files.
    """
    evidence = json.loads(ABSORBED_EVIDENCE.read_text(encoding="utf-8"))
    observed = evidence["observed_instance"]
    archive = REPO_ROOT / observed["archive"]

    patch = (archive / observed["patch"]).read_text(encoding="utf-8")
    record = json.loads((archive / observed["record"]).read_text(encoding="utf-8"))
    named = set(record["modified"]) | set(record["created"]) | set(record["deleted"])

    assert f"diff --git a/{observed['artifact']}" in patch
    assert f"Subject: [PATCH] {observed['commit_subject_prefix']}" in patch
    assert observed["artifact_in_the_story_commit"] is True
    assert (observed["artifact"] in named) \
        is observed["artifact_named_by_the_runs_own_record"]
    assert observed["artifact_named_by_the_runs_own_record"] is False


# --------------------------------------------------------------------------
# What each terminal commit carries
# --------------------------------------------------------------------------


def recorded_paths(target_root: Path, story_id: str = STORY_ID) -> set[str]:
    """What the run's own records name, read off the run directory."""
    named = set()
    for name in ("changed-files.json", "tester-changed-files.json"):
        record = json.loads(
            (run_dir_of(target_root, story_id) / name).read_text())
        named |= set(record["modified"]) | set(record["created"])
    return named


def test_a_completed_runs_commit_holds_what_the_run_produced_and_nothing_older(
    target, harness_root,
):
    """The commit's contents compared against the stage records plus the
    documenter's output — not merely searched for the stray file.

    The stray file gets its two turns: refused while uncommitted, and then, once
    the developer commits it deliberately, present in *their* commit and absent
    from the run's. That commit is the control: it shows the same reading of the
    same repository does report the file when it is there.
    """
    write(target / STRAY, "no stage wrote this\n")
    assert run(target, harness_root)[0] == 1
    developers = commit(target, "the developer's own file")

    code, _ = run(target, harness_root)
    assert code == 0

    assert set(files_in(target)) == recorded_paths(target) | {DOC_OUTPUT}
    assert STRAY not in files_in(target)
    assert STRAY in files_in(target, developers)          # the control


def test_an_escalated_runs_commits_hold_what_the_run_produced_and_nothing_older(
    target, harness_root,
):
    """Both of the escalation's commits, on a fresh run — the same statement as
    above for the other terminal path, with the same control."""
    write(target / STRAY, "no stage wrote this\n")
    assert run(target, harness_root, verdicts=[FAIL_AT_ONCE])[0] == 1
    developers = commit(target, "the developer's own file")

    escalate(target, harness_root)

    assert STRAY not in files_in(target)
    assert STRAY not in files_in(target, "HEAD~1")
    assert "src/app.py" in files_in(target) + files_in(target, "HEAD~1")
    assert STRAY in files_in(target, developers)          # the control


def test_a_resumed_escalated_runs_commit_carries_nothing_that_predated_it(
    make_target, harness_root,
):
    """The same statement for the resumed escalated run, which is the case
    story-020 added and the one this story had to decide about.

    Its control is the crashed resume in the test below, where the identical
    stray file is carried through — so this absence is the pre-flight applying
    and not a resume being unable to commit anything at all.
    """
    target = make_target("resumed-escalated-target")
    escalate(target, harness_root)

    write(target / STRAY, "dirty when the resume was asked for\n")
    assert run(target, harness_root)[0] == 1
    developers = commit(target, "the developer's own file")

    code, runner = run(target, harness_root, verdicts=[PASS])
    assert code == 0
    assert runner.calls != []
    assert STRAY not in files_in(target)
    assert files_in(target) != []                      # it did commit something
    assert STRAY in files_in(target, developers)       # the control


def test_the_one_remaining_limit_is_a_resumed_crashed_run(
    make_target, harness_root,
):
    """Stated by a test rather than left for a reader to discover: a resumed
    crashed run is the one case where a terminal commit stages more than the
    run produced, because its working tree is the run's own unfinished work.

    Asserted positively — the stray file *is* in the commit — so when the
    exclusion stops holding this goes red rather than quietly passing, and the
    exclusion is what should be re-read rather than the assertion relaxed.
    """
    target = make_target("limit-target")
    crashed_run(target, stage="implementer")
    write(target / STRAY, "the crashed run's own unfinished work\n")

    code, _ = run(target, harness_root)
    assert code == 0
    assert STRAY in files_in(target)
    assert STRAY not in recorded_paths(target)

    # The control: the same file, the same repository, one state field apart —
    # the case the pre-flight does govern, which never reaches a commit.
    governed = make_target("limit-control-target")
    escalate(governed, harness_root)
    write(governed / STRAY, "identical file, escalated state\n")
    assert run(governed, harness_root)[0] == 1
    assert STRAY not in files_in(governed)


def test_the_coordinator_states_the_guarantee_and_its_one_exclusion():
    """Where the commits are made, so a reader of `git add -A` meets it there.

    The control is the same search over a rendering with the prose stripped
    out, which finds nothing — otherwise a search that had stopped matching
    would look the same as prose that had been deleted.
    """
    lines = COORDINATOR_SOURCE.splitlines()
    assert [line for line in lines if "add -A" in line and "working tree" in line]
    assert [line for line in lines if "crashed" in line]
    stripped = executable_source(COORDINATOR_SOURCE)
    assert [line for line in stripped.splitlines() if "add -A" in line] == []


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


def test_neither_terminal_commit_was_changed_to_achieve_any_of_this():
    """What the story forbade, checked against the earlier module rather than
    against a phrase: `_complete` and `_escalate` still stage what they staged.

    The control is `run_story` itself, compared the same way, which did change
    — so this is a comparison that can report a difference.

    Both sides are bounded at this story's own commit range, per
    `at_story_endpoint` above: story-024 later moved the summary's
    construction out of `_escalate`, and that is not story-021 changing it.
    """
    for name in ("_complete", "_escalate"):
        assert coordinator_function(name, ENDPOINT) \
            == coordinator_function(name, BASELINE), name
    assert coordinator_function("run_story", ENDPOINT) \
        != coordinator_function("run_story", BASELINE)


# --------------------------------------------------------------------------
# No bypass
# --------------------------------------------------------------------------


def _function(source: str, name: str) -> ast.FunctionDef:
    return next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _guard_statement(source: str) -> ast.If:
    """The `if` in run_story that decides whether the check applies."""
    return next(
        node for node in ast.walk(_function(source, "run_story"))
        if isinstance(node, ast.If)
        and any(isinstance(call, ast.Call)
                and getattr(call.func, "id", None) == "dirty_paths"
                for call in ast.walk(node))
    )


def escapes_in(source: str) -> list[str]:
    """Every way the given source could be told to stand down: an environment
    read anywhere in it, or a configuration key or callable argument the guard
    consults besides the state and the tree."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            found.append(node.attr)
        if isinstance(node, ast.Name) and node.id in {"environ", "getenv", "os"}:
            found.append(node.id)
    for node in ast.walk(_guard_statement(source).test):
        if isinstance(node, ast.Name):
            found.append(f"guard:{node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(f"guard-literal:{node.value}")
    return sorted(set(found))


def test_no_flag_environment_variable_or_configuration_key_reaches_the_check():
    """The guard's condition reads the run's state and nothing else, and the
    module reads no environment at all."""
    escapes = escapes_in(COORDINATOR_SOURCE)
    assert [e for e in escapes if not e.startswith("guard")] == []
    # The only name the condition reads is the run's own state. The literals
    # it carries are status values, which the parametrized control below
    # distinguishes from a configuration key by the name that would read one.
    assert {e for e in escapes if e.startswith("guard:")} == {"guard:state"}


@pytest.mark.parametrize("expected", ["environ", "guard:config"])
def test_the_same_scan_reports_a_bypass_that_was_planted(expected):
    """The control for the scan above, once per kind of bypass the story
    forbids: planted into a copy of the source, which the same scan does
    report."""
    anchor = "    if state is None or state.status == \"escalated\":"
    assert anchor in COORDINATOR_SOURCE
    if expected == "environ":
        planted = COORDINATOR_SOURCE.replace(
            anchor,
            '    if os.environ.get("L5_SKIP_CLEAN_TREE"):\n'
            '        return 0\n' + anchor,
            1)
    else:
        planted = COORDINATOR_SOURCE.replace(
            anchor,
            '    if (state is None or state.status == "escalated") and not '
            'config.get("allow_dirty"):',
            1)
    assert planted != COORDINATOR_SOURCE
    assert expected in escapes_in(planted)


#: Names a developer looking for an escape hatch would reach for first.
ENV_ATTEMPTS = [
    "L5_SKIP_CLEAN_TREE", "L5_ALLOW_DIRTY", "L5_FORCE", "SKIP_CLEAN_TREE",
    "ALLOW_DIRTY", "FORCE", "HARNESS_ALLOW_DIRTY",
]
CONFIG_ATTEMPTS = [
    "allow_dirty: true", "skip_clean_tree: true", "require_clean_tree: false",
    "clean_tree_check: false", "force: true",
]


def test_attempting_the_bypass_does_not_bypass_it(
    make_target, harness_root, monkeypatch, capsys,
):
    """The search above says there is no key to find; this says that setting
    them anyway changes nothing.

    The control is the same target, with every one of those variables and keys
    still set, run once the tree is clean — which proceeds. So the refusal is
    the dirty tree rather than the extra configuration having broken the run.
    """
    target = make_target("bypass-target")
    for name in ENV_ATTEMPTS:
        monkeypatch.setenv(name, "1")
    config = target / ".harness" / "config.yaml"
    write(config, config.read_text(encoding="utf-8")
          + "".join(f"{key}\n" for key in CONFIG_ATTEMPTS))
    commit(target, "every escape hatch a developer would try")

    write(target / STRAY, "no stage wrote this\n")
    capsys.readouterr()
    code, runner = run(target, harness_root)
    assert code == 1
    assert runner.calls == []
    assert STRAY in capsys.readouterr().err

    commit(target, "the developer commits it, as the message says")
    code, runner = run(target, harness_root)
    assert code == 0                                   # the control
    assert runner.calls != []


# --------------------------------------------------------------------------
# The check reads, and only reads
# --------------------------------------------------------------------------


def snapshot(root: Path) -> dict:
    return {
        "head": git(root, "rev-parse", "HEAD").stdout,
        "branches": git(root, "branch", "--list", "--all").stdout,
        "stash": git(root, "stash", "list").stdout,
        "index": git(root, "ls-files", "--stage").stdout,
        "status": git(root, "status", "--porcelain").stdout,
        "reflog": git(root, "reflog", "--format=%H %gs").stdout,
    }


def test_the_check_creates_no_commit_branch_stash_or_index_change(
    target, harness_root,
):
    """Read directly, and then again around the refused run, so the claim
    covers the call and its one caller."""
    write(target / STRAY, "no stage wrote this\n")

    before = snapshot(target)
    assert story_coordinator.dirty_paths(target) == [STRAY]
    assert snapshot(target) == before

    assert run(target, harness_root)[0] == 1
    assert snapshot(target) == before


@pytest.mark.parametrize("mutation,field", [
    (lambda root: git(root, "add", "-A"), "index"),
    (lambda root: git(root, "stash", "push", "-u", "-q"), "stash"),
    (lambda root: git(root, "branch", "some-other-branch"), "branches"),
    (lambda root: commit(root, "a commit"), "head"),
])
def test_that_same_comparison_reports_each_thing_the_check_must_not_do(
    make_target, mutation, field,
):
    """The control for the test above, once per thing the story forbids: the
    same before/after reading around an operation that does it."""
    root = make_target(f"mutation-{field}")
    write(root / STRAY, "no stage wrote this\n")
    before = snapshot(root)
    mutation(root)
    after = snapshot(root)
    assert after != before
    assert after[field] != before[field]


def test_dirty_paths_reports_what_git_add_would_absorb(target):
    """The evidence the refusal rests on, read directly: every kind of change
    `git add -A` would stage, and nothing that is ignored.

    Each absence has its counterpart in the same call — the ignored file is
    absent from a list that is not empty, and the clean tree's empty list sits
    beside the same tree dirtied.
    """
    assert story_coordinator.dirty_paths(target) == []

    write(target / STRAY, "untracked\n")
    write(target / DOC_OUTPUT, "# Architecture\n\nmodified\n")
    (target / "tests" / "test_existing.py").unlink()
    write(target / ".harness" / "runs" / "ignored.txt", "ignored\n")

    reported = story_coordinator.dirty_paths(target)
    assert reported == sorted([STRAY, DOC_OUTPUT, "tests/test_existing.py"])
    assert ".harness/runs/ignored.txt" not in reported

    git(target, "add", "-A")
    git(target, "mv", "src/app.py", "src/renamed.py")
    assert "src/renamed.py" in story_coordinator.dirty_paths(target)
    assert "src/app.py" not in story_coordinator.dirty_paths(target)


def test_a_root_that_is_not_a_repository_reports_nothing_dirty(tmp_path):
    """The one-directional bias, stated where it is decided: refuse for what
    can be established, never for what cannot.

    The control is the same directory made a repository with the same file in
    it, which does report — so the empty list is the missing repository rather
    than the reader having stopped seeing files.
    """
    root = tmp_path / "not-a-repo"
    root.mkdir()
    write(root / STRAY, "no repository here\n")
    assert story_coordinator.dirty_paths(root) == []

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert story_coordinator.dirty_paths(root) == [STRAY]
