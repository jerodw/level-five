"""Independent validation for story-030: a story branches from a declared
base, defaulting to the repository's own.

The subject is *what a branch gets cut from*, which is a fact about git and
not about the coordinator's data structures, so nearly everything here is
asserted against throwaway repositories the tests build: a target with a bare
`origin`, an `refs/remotes/origin/HEAD` that is either published or not, and a
base that is either level with its remote or deliberately pushed out of step.
The fake runner records every stage it is asked for, so "no agent was invoked"
is an observable emptiness rather than an argument.

The fixture, the fake runner and the readers are imported from
`test_foreign_work_refusal` and `test_plan_commit` rather than copied.
Those files built them for the coordinator pre-flight and the plan script this
story extends, and one home for one fact is why a regression in either shows
up here.

Every assertion here that claims an absence carries a control showing the same
check reporting the violation it exists to catch:

  * "resolve_base falls back to the literal" is asserted against a repository
    whose branch is `trunk` and whose origin/HEAD is unset, so the literal is
    the only thing that could have produced "main", and sits beside the same
    repository with origin/HEAD published, where the answer is `trunk`;
  * "the refusal created no run directory, no state.json, no log and no
    branch, and invoked no agent" sits beside the same readers after the same
    run on a repository standing where it should, where every one of them is
    created;
  * "only the not-on-base message is printed when both conditions hold" sits
    beside the same repository standing on the base with the same drift, where
    the drift message *is* printed, so the absence is an absence of that text
    and not of the reader;
  * "a repository with no remote, a base with no counterpart, and a root that
    is not a repository produce no refusal" each sit beside the same
    repository given a counterpart that differs, where the refusal appears;
  * "an existing story branch is never refused for its base" sits beside the
    same base state with the branch removed, where it is refused;
  * "l5-plan committed nothing and pushed nothing" sits beside the same
    session on the base, where HEAD moves and the remote's refs move;
  * "orchestration/ holds exactly one literal branch name" sits beside the
    same scanner over a copy of the module with a second literal planted;
  * "story_branch no longer promises that no default base branch is written
    into orchestration" sits beside the same substring read out of that
    function's pre-story source, which does promise it.

The baseline for anything read out of git is `conftest.story_commit_range`,
never HEAD and never the working tree against the repository root: the
coordinator commits the tree at the end of a successful run, so those go
vacuously green the moment this story commits.

No model is invoked anywhere in this file.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASELINE, ENDPOINT, function_source
import conftest

from test_foreign_work_refusal import (
    CONFIG,
    DEFAULT_BRANCH,
    FAIL_AT_ONCE,
    REPO_ROOT,
    STORY_BRANCH,
    STORY_ID,
    Runner,
    branches,
    build_target,
    commit,
    git,
    log_of,
    run_dir_of,
    snapshot,
    state_of,
    write,
)
from test_plan_commit import (
    Planning,
    artifact,
    bare_remote,
    make_planning,
    remote_refs,
    writes,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import story_coordinator  # noqa: E402

COORDINATOR_REL = "orchestration/story_coordinator.py"
COORDINATOR_PATH = HARNESS_ROOT / COORDINATOR_REL
L5_RUN = HARNESS_ROOT / "scripts" / "l5-run"
L5_PLAN = HARNESS_ROOT / "scripts" / "l5-plan"
VALIDATION_FILE = Path(__file__).resolve()

#: A branch name that is not the git default, used wherever a test needs the
#: base and the repository's default branch to be distinguishable.
OTHER_DEFAULT = "trunk"
ELSEWHERE = "feature-somewhere-else"


# --------------------------------------------------------------------------
# The repositories these tests build
# --------------------------------------------------------------------------


def add_remote(tmp_path: Path, root: Path, *, branch: str = DEFAULT_BRANCH,
               name: str = "origin", set_head: bool = True) -> Path:
    """A bare `origin` the target pushes `branch` to, as a real one would be.

    `set_head` publishes `refs/remotes/origin/HEAD`, which is the ref
    `resolve_base` reads and the reason the normal case needs no
    configuration. It is a separate switch because a repository that has never
    been cloned does not have one, and the fallback below is about exactly
    that repository.
    """
    remote = tmp_path / f"{name}-{root.name}.git"
    # `cwd` is stated for the same reason every other git call in the suite
    # states one: a call that names no target inherits this repository.
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(remote)],
                   cwd=tmp_path, check=True, capture_output=True)
    git(root, "remote", "add", name, str(remote))
    git(root, "push", "-q", "-u", name, branch)
    if set_head:
        git(root, "remote", "set-head", name, branch)
    return remote


@pytest.fixture
def make_based(tmp_path: Path):
    """A target repository, optionally with a remote and an origin/HEAD.

    A factory rather than a fixture value because almost every test below
    holds a subject and its control side by side, and the two differ in
    exactly one thing about the base.
    """
    def make(name: str, *, branch: str = DEFAULT_BRANCH, remote: bool = True,
             set_head: bool = True, base_branch: str | None = None) -> Path:
        root = build_target(tmp_path / name)
        if branch != DEFAULT_BRANCH:
            git(root, "branch", "-M", branch)
        if base_branch is not None:
            write(root / ".harness" / "config.yaml",
                  CONFIG + f"base_branch: {base_branch}\n")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "declare the base")
        if remote:
            add_remote(tmp_path, root, branch=branch, set_head=set_head)
        return root
    return make


@pytest.fixture
def based(make_based) -> Path:
    """The normal case: on `main`, level with `origin/main`, origin/HEAD set."""
    return make_based("based-target")


def config_of(root: Path) -> dict:
    import harness_config

    return harness_config.load_config(root)


def run(target_root: Path, *, base: str | None = None,
        start_stage: str | None = None, verdicts: list | None = None,
        runner: Runner | None = None) -> tuple[int, Runner]:
    runner = runner or Runner(target_root, verdicts)
    code = story_coordinator.run_story(
        STORY_ID, REPO_ROOT, target_root, runner,
        start_stage=start_stage, base=base,
    )
    return code, runner


def base_ahead(root: Path, branch: str = DEFAULT_BRANCH) -> None:
    """One commit on the local base that was never pushed."""
    git(root, "checkout", "-q", branch)
    git(root, "commit", "-q", "--allow-empty", "-m", "local, never pushed")


def base_behind(root: Path, branch: str = DEFAULT_BRANCH) -> None:
    """One commit on the remote base that the local one does not have."""
    git(root, "checkout", "-q", "-b", "_someone-else")
    git(root, "commit", "-q", "--allow-empty", "-m", "someone else's commit")
    git(root, "push", "-q", "origin", f"_someone-else:{branch}")
    git(root, "checkout", "-q", branch)
    git(root, "fetch", "-q", "origin")
    git(root, "branch", "-q", "-D", "_someone-else")


def elsewhere(root: Path, name: str = ELSEWHERE) -> None:
    git(root, "checkout", "-q", "-b", name)


def head_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def events(target_root: Path) -> list[str]:
    text = (run_dir_of(target_root) / "events.log").read_text(encoding="utf-8")
    return [line.split("] ", 1)[1] for line in text.splitlines() if "] " in line]


def history(target_root: Path) -> list[dict]:
    path = run_dir_of(target_root) / "execution-history.json"
    return json.loads(path.read_text(encoding="utf-8"))


def notes(target_root: Path) -> list[dict]:
    return [entry for entry in history(target_root) if entry["event"] == "note"]


# --------------------------------------------------------------------------
# resolve_base: four steps, first answer winning
# --------------------------------------------------------------------------


def test_the_normal_case_needs_no_configuration_and_no_flag(based, capsys):
    """origin/HEAD answers, and a run standing on the answer proceeds.

    The configuration assertion is part of the claim: the acceptance criterion
    is that nothing had to be written, so the config this repository ran under
    is read back and shown to say nothing about a base.
    """
    text = (based / ".harness" / "config.yaml").read_text(encoding="utf-8")
    assert "base_branch" not in text
    assert git(based, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip() \
        == "refs/remotes/origin/main"

    assert story_coordinator.resolve_base(based, config_of(based), None) == "main"

    code, runner = run(based)
    assert code == 0
    assert runner.calls != []
    assert STORY_BRANCH in branches(based)


def test_the_config_key_wins_over_the_ref_that_would_have_answered_differently(
    make_based,
):
    """`base_branch` is consulted before origin/HEAD, so the two must disagree
    for the assertion to be about precedence rather than about agreement."""
    root = make_based("config-key-target", base_branch=OTHER_DEFAULT)
    assert git(root, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip() \
        == "refs/remotes/origin/main"
    assert config_of(root).get("base_branch") == OTHER_DEFAULT

    assert story_coordinator.resolve_base(root, config_of(root), None) \
        == OTHER_DEFAULT


def test_the_flag_wins_over_both_the_config_key_and_the_ref(make_based):
    root = make_based("flag-target", base_branch=OTHER_DEFAULT)
    assert story_coordinator.resolve_base(root, config_of(root), "declared") \
        == "declared"


@pytest.mark.parametrize("remote", [True, False], ids=["no-origin-head",
                                                       "no-remote-at-all"])
def test_the_literal_fallback_is_what_answers_when_nothing_else_can(
    make_based, remote,
):
    """The repository's own branch is `trunk`, so "main" cannot have come from
    anywhere but the literal.

    This is the criterion's "exercised by a test rather than only documented":
    a fallback tested against a repository whose branch is already `main`
    would pass whether the fallback existed or not.
    """
    root = make_based(f"fallback-{remote}", branch=OTHER_DEFAULT,
                      remote=remote, set_head=False)
    assert head_branch(root) == OTHER_DEFAULT
    assert story_coordinator.resolve_base(root, config_of(root), None) == "main"


def test_that_same_repository_answers_trunk_once_origin_head_is_published(
    make_based,
):
    """The control for the fallback above: one thing differs — origin/HEAD is
    set — and the answer stops being the literal."""
    root = make_based("fallback-control", branch=OTHER_DEFAULT, set_head=True)
    assert story_coordinator.resolve_base(root, config_of(root), None) \
        == OTHER_DEFAULT


def test_resolve_base_reads_and_only_reads(based):
    before = snapshot(based)
    assert story_coordinator.resolve_base(based, config_of(based), None) == "main"
    assert story_coordinator.base_problems(based, "main", False) == []
    assert snapshot(based) == before


# --------------------------------------------------------------------------
# Leg one: HEAD is not standing on the base
# --------------------------------------------------------------------------


@pytest.fixture
def refused_elsewhere(based, capsys):
    """A fresh run started from a branch that is not the base."""
    elsewhere(based)
    before = {"head": git(based, "rev-parse", "HEAD").stdout.strip(),
              "branches": branches(based)}
    capsys.readouterr()
    code, runner = run(based)
    captured = capsys.readouterr()
    return code, runner, based, before, captured.err


def test_a_fresh_run_from_a_branch_that_is_not_the_base_is_refused(
    refused_elsewhere,
):
    code, runner, target, before, err = refused_elsewhere
    assert code == 1
    assert ELSEWHERE in err            # the branch actually checked out
    assert "main" in err               # and the base


def test_that_refusal_leaves_nothing_behind_and_invokes_no_agent(
    refused_elsewhere,
):
    """Each absence separately, because "exit 1" alone would hold for a run
    that got as far as creating a directory and then failed."""
    code, runner, target, before, _ = refused_elsewhere
    assert runner.calls == []                                 # no agent
    assert not run_dir_of(target).exists()                    # no run directory
    assert not (run_dir_of(target) / "state.json").exists()   # no state
    assert not log_of(target).exists()                        # no log
    assert branches(target) == before["branches"]             # no new branch
    assert STORY_BRANCH not in branches(target)
    assert git(target, "rev-parse", "HEAD").stdout.strip() == before["head"]
    assert head_branch(target) == ELSEWHERE


def test_the_same_run_standing_on_the_base_creates_every_one_of_those(based):
    """The control for the five absences above: one thing differs about the
    repository — which branch is checked out — and each one appears."""
    code, runner = run(based)
    assert code == 0
    assert runner.calls != []
    assert run_dir_of(based).is_dir()
    assert (run_dir_of(based) / "state.json").is_file()
    assert log_of(based).is_file()
    assert STORY_BRANCH in branches(based)


def test_a_detached_head_is_establishably_not_on_the_base_and_refuses(based):
    git(based, "checkout", "-q", "--detach", "HEAD")
    problems = story_coordinator.base_problems(based, "main", False)
    assert len(problems) == 1
    assert "detached" in problems[0]
    assert "main" in problems[0]


# --------------------------------------------------------------------------
# Leg two: the base does not match its remote-tracking counterpart
# --------------------------------------------------------------------------


DRIFTS = {
    "ahead": base_ahead,
    "behind": base_behind,
    "diverged": lambda root: (base_behind(root), base_ahead(root)),
}


@pytest.mark.parametrize("drift", sorted(DRIFTS))
def test_a_base_that_differs_from_its_remote_in_any_direction_refuses(
    based, drift, capsys,
):
    """Behind, ahead and diverged each refuse, and the message says which."""
    DRIFTS[drift](based)
    assert head_branch(based) == DEFAULT_BRANCH

    problems = story_coordinator.base_problems(based, "main", False)
    assert len(problems) == 1
    assert "main" in problems[0]
    assert "origin/main" in problems[0]
    if drift in ("ahead", "diverged"):
        assert "ahead" in problems[0]
    if drift in ("behind", "diverged"):
        assert "behind" in problems[0]

    capsys.readouterr()
    code, runner = run(based)
    err = capsys.readouterr().err
    assert code == 1
    assert problems[0] in err
    assert runner.calls == []
    assert not run_dir_of(based).exists()
    assert STORY_BRANCH not in branches(based)


def test_a_base_identical_to_its_remote_proceeds(based):
    """The control for the three drifts: same repository, no drift."""
    assert story_coordinator.base_problems(based, "main", False) == []
    assert run(based)[0] == 0


# --------------------------------------------------------------------------
# Both at once: which of the two is reported
# --------------------------------------------------------------------------


def test_when_both_conditions_hold_only_the_not_on_base_message_is_printed(
    make_based, capsys,
):
    """The absence asserted is the absence of the drift sentence, and its
    control is the same drift on the same repository with HEAD on the base,
    where that sentence is exactly what is printed."""
    control = make_based("both-control")
    base_ahead(control)
    drift = story_coordinator.base_problems(control, "main", False)
    assert len(drift) == 1 and "ahead" in drift[0]

    subject = make_based("both-subject")
    base_ahead(subject)
    elsewhere(subject)

    problems = story_coordinator.base_problems(subject, "main", False)
    assert len(problems) == 1
    assert ELSEWHERE in problems[0], "the not-on-base leg is what was reported"
    assert drift[0] not in problems, "the drift leg was reported as well"

    capsys.readouterr()
    code, _ = run(subject)
    err = capsys.readouterr().err
    assert code == 1
    assert ELSEWHERE in err
    assert drift[0] not in err

    # The control, run the same way: the drift sentence does reach stderr when
    # it is the leg that has something to say.
    capsys.readouterr()
    assert run(control)[0] == 1
    assert drift[0] in capsys.readouterr().err


# --------------------------------------------------------------------------
# --base: a deliberate departure, not a bypass
# --------------------------------------------------------------------------


def other_branch(root: Path, name: str = "story/story-000") -> str:
    """A branch standing in for another story's branch, with work of its own."""
    git(root, "checkout", "-q", "-b", name)
    write(root / "src" / "from_the_other_branch.py", "value = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "work on another story's branch")
    tip = git(root, "rev-parse", "HEAD").stdout.strip()
    git(root, "checkout", "-q", DEFAULT_BRANCH)
    return tip


def test_a_declared_base_is_what_the_new_branch_is_cut_from(based):
    tip = other_branch(based)
    code, _ = run(based, base="story/story-000")
    assert code == 0
    assert STORY_BRANCH in branches(based)
    assert git(based, "merge-base", "--is-ancestor", tip, STORY_BRANCH,
               check=False).returncode == 0
    assert (based / "src" / "from_the_other_branch.py").is_file()


def test_without_the_flag_the_branch_is_not_cut_from_that_ref(based):
    """The control for the ancestry above: the same repository, same other
    branch, no flag — and the other branch's tip is not in the history."""
    tip = other_branch(based)
    assert run(based)[0] == 0
    assert git(based, "merge-base", "--is-ancestor", tip, STORY_BRANCH,
               check=False).returncode != 0
    assert not (based / "src" / "from_the_other_branch.py").exists()


def test_a_declared_base_suppresses_both_legs(based):
    """HEAD elsewhere and the named ref ahead of its remote: neither refuses.

    This is the case the flag exists for — a story branched from another
    story's branch, which by construction is not the base and by construction
    is not level with anything shared.
    """
    other_branch(based)
    base_ahead(based)
    elsewhere(based)
    assert story_coordinator.base_problems(based, "story/story-000", True) == []
    assert story_coordinator.base_problems(based, DEFAULT_BRANCH, True) == []
    # Undeclared, the same repository in the same state refuses.
    assert story_coordinator.base_problems(based, DEFAULT_BRANCH, False) != []

    code, runner = run(based, base="story/story-000")
    assert code == 0
    assert runner.calls != []


def test_a_declared_base_that_does_not_resolve_is_refused(based, capsys):
    capsys.readouterr()
    code, runner = run(based, base="no-such-branch")
    err = capsys.readouterr().err
    assert code == 1
    assert "no-such-branch" in err
    assert runner.calls == []
    assert not run_dir_of(based).exists()
    assert STORY_BRANCH not in branches(based)


def test_an_undeclared_base_that_does_not_resolve_says_nothing(make_based):
    """The harness guessed, and a guess that resolves to nothing establishes
    nothing. Its control is the declared case above, which does refuse."""
    root = make_based("unresolvable-base", branch=OTHER_DEFAULT, remote=False,
                      set_head=False)
    assert story_coordinator.resolve_base(root, config_of(root), None) == "main"
    assert git(root, "rev-parse", "--verify", "main",
               check=False).returncode != 0
    assert story_coordinator.base_problems(root, "main", False) == []
    assert story_coordinator.base_problems(root, "main", True) != []


# --------------------------------------------------------------------------
# The one-directional bias
# --------------------------------------------------------------------------


def test_a_repository_with_no_remote_produces_no_base_refusal(make_based):
    root = make_based("no-remote", remote=False)
    assert story_coordinator.base_problems(root, DEFAULT_BRANCH, False) == []
    assert run(root)[0] == 0


def test_a_base_with_no_remote_tracking_counterpart_produces_no_refusal(
    tmp_path, make_based,
):
    """A remote exists; the base has simply never been pushed to it."""
    root = make_based("no-counterpart")
    git(root, "checkout", "-q", "-b", OTHER_DEFAULT)
    write(root / ".harness" / "config.yaml", CONFIG + f"base_branch: {OTHER_DEFAULT}\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "declare a base nobody has pushed")

    assert story_coordinator.resolve_base(root, config_of(root), None) \
        == OTHER_DEFAULT
    assert git(root, "rev-parse", "--verify", f"refs/remotes/origin/{OTHER_DEFAULT}",
               check=False).returncode != 0
    assert story_coordinator.base_problems(root, OTHER_DEFAULT, False) == []
    assert run(root)[0] == 0


def test_that_same_base_refuses_once_it_has_a_counterpart_that_differs(
    tmp_path, make_based,
):
    """The control for the two absences above: give the base a counterpart and
    let them differ, and the same check reports it."""
    root = make_based("counterpart-control")
    git(root, "checkout", "-q", "-b", OTHER_DEFAULT)
    git(root, "push", "-q", "-u", "origin", OTHER_DEFAULT)
    assert story_coordinator.base_problems(root, OTHER_DEFAULT, False) == []
    git(root, "commit", "-q", "--allow-empty", "-m", "local, never pushed")
    problems = story_coordinator.base_problems(root, OTHER_DEFAULT, False)
    assert len(problems) == 1 and OTHER_DEFAULT in problems[0]


def test_a_root_that_is_not_a_repository_produces_no_base_refusal(tmp_path):
    plain = tmp_path / "not-a-repository"
    plain.mkdir()
    assert story_coordinator.base_problems(plain, "main", False) == []
    assert story_coordinator.base_problems(plain, "main", True) == []
    assert story_coordinator.branch_behind(plain, STORY_BRANCH, "main") is None


# --------------------------------------------------------------------------
# The check is creation-time: an existing story branch is reported, not refused
# --------------------------------------------------------------------------


def test_an_existing_story_branch_is_never_refused_whatever_the_base_is_doing(
    based,
):
    git(based, "branch", STORY_BRANCH)
    base_ahead(based)
    elsewhere(based)
    assert story_coordinator.base_problems(based, "main", False) != [], \
        "the base state here is one that would refuse a branch not yet created"

    code, runner = run(based)
    assert code == 0
    assert runner.calls != []


def test_the_same_base_state_refuses_when_that_branch_does_not_exist(based):
    """The control for the test above: one thing differs — whether the story
    branch already exists — and the identical base state refuses."""
    base_ahead(based)
    elsewhere(based)
    code, runner = run(based)
    assert code == 1
    assert runner.calls == []


def test_a_resume_of_an_escalated_run_is_not_refused_for_its_base(based):
    """After an escalation HEAD is on the story branch, which is by
    construction not the base, so a guard that applied to a resume would refuse
    every one of them."""
    code, _ = run(based, verdicts=[FAIL_AT_ONCE])
    assert code == 2
    assert state_of(based)["status"] == "escalated"
    assert head_branch(based) == STORY_BRANCH

    # Something establishable has to have changed, or the resume is refused by
    # story-021's own guard rather than reaching the base question at all.
    commit(based, "the developer's own repair")
    base_ahead(based, DEFAULT_BRANCH)
    git(based, "checkout", "-q", STORY_BRANCH)

    code, runner = run(based)
    assert code == 0, "the resume was refused"
    assert runner.calls != []


# --------------------------------------------------------------------------
# The stale-base note
# --------------------------------------------------------------------------


@pytest.fixture
def stale(based) -> Path:
    """A story branch that exists and predates two commits of the base."""
    git(based, "branch", STORY_BRANCH)
    for index in range(2):
        git(based, "commit", "-q", "--allow-empty", "-m", f"base moves {index}")
    git(based, "push", "-q", "origin", DEFAULT_BRANCH)
    return based


def test_branch_behind_counts_the_commits_of_the_base_the_branch_lacks(stale):
    assert story_coordinator.branch_behind(stale, STORY_BRANCH, DEFAULT_BRANCH) == 2


def test_a_run_on_a_stale_branch_notes_it_once_in_both_renderings(stale):
    code, runner = run(stale)
    assert code == 0
    assert runner.calls != [], "the run did not proceed"

    entries = notes(stale)
    assert len(entries) == 1
    message = entries[0]["message"]
    assert STORY_BRANCH in message
    assert DEFAULT_BRANCH in message
    assert "2" in message

    # One write, two renderings: the same message, once, in events.log.
    assert [line for line in events(stale) if line == message] == [message]


def test_a_branch_that_is_not_behind_gets_no_note(based):
    """The control for the note: the same reader over the same run whose
    branch is level with the base finds nothing, so the assertion above is
    about the note and not about the reader."""
    git(based, "branch", STORY_BRANCH)
    assert story_coordinator.branch_behind(based, STORY_BRANCH, DEFAULT_BRANCH) == 0
    assert run(based)[0] == 0
    assert notes(based) == []


def test_the_note_does_not_change_where_execution_goes(stale, make_based):
    """The stale branch runs the same stages, in the same order, as a run whose
    branch is not stale."""
    code, stale_runner = run(stale)
    assert code == 0

    fresh = make_based("not-stale")
    git(fresh, "branch", STORY_BRANCH)
    assert run(fresh)[0] == 0

    assert stale_runner.calls == ["implementer", "tester", "documenter",
                                  "verifier"]
    assert notes(fresh) == []


# --------------------------------------------------------------------------
# l5-plan refuses before it commits
# --------------------------------------------------------------------------


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """A planning repository on `main`, tracking a bare origin whose HEAD is
    published — the case a developer is normally in."""
    planning = make_planning(tmp_path)
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    planning.git("remote", "set-head", "origin", "main")
    return planning


#: The workflow these sessions render against, stated rather than left to a
#: fallback: since story-072 l5-plan reads no configured workflow key, and an
#: invocation with no terminal and no --workflow is refused before the session
#: starts. This module's subject is where the plan commit lands.
PLANNED_WORKFLOW = "story-workflow"


def run_plan(planning: Planning, *argv: str, **stub) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(L5_PLAN), "--workflow", PLANNED_WORKFLOW, *argv],
        cwd=planning.root, env=planning.env(**stub),
        capture_output=True, text=True,
    )


PLANNED = ".harness/stories/story-900.yaml"


def planning_stub() -> str:
    return writes((PLANNED, artifact("story-900")))


@pytest.mark.parametrize("condition", ["not-on-base", "base-drifted"])
def test_l5_plan_refuses_on_the_same_two_conditions_before_committing(
    planning: Planning, condition,
):
    if condition == "not-on-base":
        planning.git("checkout", "-q", "-b", ELSEWHERE)
    else:
        planning.git("commit", "-q", "--allow-empty", "-m", "local, never pushed")
    before_head = planning.head()
    before_refs = remote_refs(planning.remote)

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 1
    # The refusal, and then that it happened before anything was committed.
    assert "base" in result.stderr
    assert planning.head() == before_head, "HEAD moved"
    assert remote_refs(planning.remote) == before_refs, "something was pushed"
    assert (planning.root / PLANNED).is_file(), "the artifact was removed"
    assert "?? " + PLANNED in planning.status(), \
        "the artifact is not sitting uncommitted where the session wrote it"


def test_the_same_session_on_the_base_commits_and_pushes(planning: Planning):
    """The control for both refusals above: one thing differs about the
    repository, and HEAD moves, the remote's refs move, and the working tree
    is clean of the artifact."""
    before_head = planning.head()
    before_refs = remote_refs(planning.remote)

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 0, result.stderr
    assert planning.head() != before_head
    assert remote_refs(planning.remote) != before_refs
    assert PLANNED not in planning.status()


def test_l5_plan_takes_base_ahead_of_the_request_and_passes_the_rest_unchanged(
    planning: Planning,
):
    """The flag is consumed, the request reaches the session as it was, and
    neither leg refuses while HEAD is somewhere else entirely."""
    planning.git("checkout", "-q", "-b", ELSEWHERE)
    before_head = planning.head()

    result = run_plan(planning, "--base", "main", "add a thing",
                      L5_STUB_WRITE=planning_stub())

    assert result.returncode == 0, result.stderr + result.stdout
    # The request the session was handed is the words after the flag, joined,
    # and nothing else: neither the flag nor its value reaches it.
    session = planning.session()
    assert session["argv"][-1] == "Story request: add a thing"
    assert planning.head() != before_head, "the artifact was not committed"


def test_l5_plan_still_refuses_a_declared_base_that_does_not_resolve(
    planning: Planning,
):
    before_head = planning.head()
    before_refs = remote_refs(planning.remote)
    result = run_plan(planning, "--base", "no-such-branch", "add a thing",
                      L5_STUB_WRITE=planning_stub())
    assert result.returncode == 1
    assert "no-such-branch" in result.stderr
    assert planning.head() == before_head
    assert remote_refs(planning.remote) == before_refs


# --------------------------------------------------------------------------
# One function, two entry points
# --------------------------------------------------------------------------


def test_the_two_entry_points_print_the_same_condition_identically(
    based, planning: Planning,
):
    """Both real scripts, both standing off the base, and their refusals
    compared as text: one function or the texts would not be equal.

    The control is built in — the two repositories are different repositories
    with different paths, so equality here is equality of a message derived
    from the branch names alone, which is what a shared derivation produces.
    """
    elsewhere(based)
    planning.git("checkout", "-q", "-b", ELSEWHERE)

    from_run = subprocess.run(
        [sys.executable, str(L5_RUN), STORY_ID],
        cwd=based, capture_output=True, text=True,
    )
    from_plan = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert from_run.returncode == 1
    assert from_plan.returncode == 1
    assert from_run.stderr.strip() != ""
    assert from_run.stderr == from_plan.stderr


def test_l5_run_forwards_the_base_and_decides_nothing_itself(monkeypatch):
    """The script parses and forwards; the resolution happens in one place."""
    from conftest import load_script

    script = load_script("l5-run", name="l5_run_base_under_test")
    seen: list[dict] = []

    def spy(story_id, harness_root, target_root, runner=None, start_stage=None,
            base=None):
        seen.append({"story_id": story_id, "start_stage": start_stage,
                     "base": base})
        return 0

    monkeypatch.setattr(script.story_coordinator, "run_story", spy)

    for argv, expected in [
        (["story-001"], {"start_stage": None, "base": None}),
        (["story-001", "--base", "trunk"], {"start_stage": None, "base": "trunk"}),
        (["--base", "trunk", "story-001"], {"start_stage": None, "base": "trunk"}),
        (["story-001", "--stage", "tester", "--base", "trunk"],
         {"start_stage": "tester", "base": "trunk"}),
    ]:
        seen.clear()
        monkeypatch.setattr(sys, "argv", ["l5-run", *argv])
        assert script.main() == 0
        assert seen == [{"story_id": "story-001", **expected}]


DERIVATION_MARKERS = ("symbolic-ref", "refs/remotes/origin/HEAD", "base_branch")


@pytest.mark.parametrize("relative", [
    "scripts/l5-run", "scripts/l5-plan", "orchestration/plan_commit.py",
])
def test_no_second_derivation_of_the_base_exists(relative):
    """The base is derived in exactly one place. Its control is the
    coordinator, read by the same scanner, where every marker is present."""
    text = (HARNESS_ROOT / relative).read_text(encoding="utf-8")
    for marker in DERIVATION_MARKERS:
        assert marker not in text, (relative, marker)

    coordinator = COORDINATOR_PATH.read_text(encoding="utf-8")
    for marker in DERIVATION_MARKERS:
        assert marker in coordinator, marker


# --------------------------------------------------------------------------
# Exactly one literal branch name in orchestration/
# --------------------------------------------------------------------------


BRANCH_NAMES = {"main", "master", "develop", "trunk", "mainline"}


def literal_branch_names(source: str) -> list[str]:
    """Every branch name written as a string literal in this source's *code*.

    Docstrings are excluded because a docstring naming the fallback is the
    documentation of it, not a second one; the control below plants its
    literal in code, so the exclusion cannot hide a real violation.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for statement in body:
                if (isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)):
                    docstrings.add(id(statement.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings and node.value.strip() in BRANCH_NAMES]


def test_orchestration_holds_exactly_one_literal_branch_name():
    found = {
        path.relative_to(HARNESS_ROOT).as_posix(): literal_branch_names(
            path.read_text(encoding="utf-8"))
        for path in sorted((HARNESS_ROOT / "orchestration").glob("*.py"))
    }
    everything = [name for names in found.values() for name in names]
    assert everything == ["main"], found
    assert found[COORDINATOR_REL] == ["main"]

    # And it is resolve_base's, not somewhere else's. Read out of the same
    # working-tree text the count above is taken from: the claim is about where
    # the one literal lives *here*, and reading it at a past story's endpoint
    # made a present-tense claim depend on this repository's commit graph.
    assert literal_branch_names(
        function_source(COORDINATOR_PATH.read_text(encoding="utf-8"),
                        "resolve_base")
    ) == ["main"]


@pytest.mark.parametrize("planted", [
    'DEFAULT = "master"\n',
    'def _elsewhere():\n    return "develop"\n',
])
def test_that_same_scan_reports_a_second_literal_that_was_planted(planted):
    """The control for the count above: the same scanner over the same module
    with one more literal in its code."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")
    assert sorted(literal_branch_names(source + "\n\n" + planted)) \
        == sorted(["main", planted.rsplit('"', 2)[1]])


@pytest.mark.parametrize("planted", [
    '"""A docstring naming main, master and develop."""\n',
])
def test_the_same_scan_stays_silent_about_prose(planted):
    """The exclusion above is deliberate, so it is shown to be an exclusion of
    prose rather than of everything."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")
    assert literal_branch_names(source + "\n\ndef _prose():\n    " + planted
                                + "    return None\n") == ["main"]


# --------------------------------------------------------------------------
# The promise story_branch used to make
# --------------------------------------------------------------------------


PROMISE = "no default base branch"
#: What distinguishes a promise being *made* from the same words being quoted
#: in the sentence that retires them.
RETIRING = ("used to read", "revis")


def story_branch_source(bound: str) -> str:
    """`story_branch`'s own text at one end of this story's range.

    The baseline is a frozen past text and is carried as a committed fixture
    since story-053; resolving it out of this repository's commit graph made
    the control below depend on the graph rather than on the docstring. The
    endpoint is read from the working tree, which is where the promise's
    absence has to hold: asserted at a past endpoint it said nothing about
    whether the promise has since come back.
    """
    if bound == BASELINE:
        return conftest.history_fixture(
            "story_coordinator.story_branch.at-story-030-baseline.py.txt")
    assert bound == ENDPOINT, bound
    return function_source(COORDINATOR_PATH.read_text(encoding="utf-8"),
                           "story_branch")


def promising_paragraphs(bound: str) -> list[str]:
    """Every paragraph of story_branch's docstring that *asserts* the promise.

    A paragraph that quotes the old sentence while saying it was revised is
    not the docstring claiming it — that is precisely the amendment the story
    asked for — so a paragraph naming the retirement is not counted. The
    baseline below is the control: there, the same reader finds the paragraph
    that makes the claim outright.
    """
    docstring = story_branch_source(bound).split('"""')[1]
    return [paragraph for paragraph in docstring.split("\n\n")
            if PROMISE in paragraph
            and not any(word in paragraph.lower() for word in RETIRING)]


def test_story_branch_no_longer_promises_no_default_base_branch():
    """The absence, and beside it the same reader over that same function's
    pre-story source, where the promise is asserted outright."""
    assert promising_paragraphs(BASELINE), \
        "the promise this story revises was not there to revise"
    assert promising_paragraphs(ENDPOINT) == []


def test_the_docstring_says_why_the_promise_was_revised():
    """A promise removed silently is a promise nobody can audit."""
    docstring = story_branch_source(ENDPOINT).split('"""')[1]
    assert "resolve_base" in docstring
    assert "revis" in docstring.lower()
    assert "story-030" in docstring


def test_the_prefix_half_of_the_promise_still_holds():
    """The half that was kept is the half worth keeping true: the branch name
    still comes from config, and story_branch itself writes none."""
    source = story_branch_source(ENDPOINT)
    body = source.split('"""')[2]
    assert literal_branch_names("def story_branch(config, story_id):" + body) == []
