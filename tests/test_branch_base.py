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
    function's pre-story source, which does promise it;
  * "the check has nothing to say about a base whose tracking ref is stale"
    is asserted before the refresh and is the demonstration that the refusal
    beside it is the refresh's doing rather than something that held anyway;
  * "the refresh wrote no ref but the one the check reads" sits beside a
    second tracking ref made stale in the identical way, which the same
    function moves the moment it is the base being refreshed;
  * "a refresh that was made prints nothing and appends no note", at both
    entry points, sits beside the same readers over the same sessions with a
    refresh that could not be made, where the line and the note appear;
  * "a repository with nothing to refresh reports no failed refresh" sits
    beside the same function over a repository that does have something to
    refresh and cannot reach it;
  * "a run whose story branch already exists attempted no refresh" sits beside
    the same repository, same unreachable remote, with the branch not yet
    created, where the refresh is attempted and reported;
  * "the refresh fetched the branch git records as the upstream" sits beside
    the same repository with that record removed, where the same call fetches
    the base's own name instead and the check falls silent;
  * "a fetch the platform will not spawn is reported rather than raised" sits
    beside the same repository's refresh without the refused spawn, which
    succeeds and says nothing.

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
    """The run's notes other than the one every run writes about its mandate.

    Since story-087 a run records what its pre-flight mandate walk resolved,
    as a note, on every entry. This module is about the note a *stale branch*
    produces, so the mandate's is dropped — by exact equality with the message
    conftest's shared mandate produces, so a change to either wording brings
    the dropped note back rather than silently widening the filter.
    """
    return [entry for entry in history(target_root)
            if entry["event"] == "note"
            and entry["message"] != conftest.MANDATE_NOTE]


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
def ran_from_elsewhere(based, capsys):
    """A fresh run started from a branch that is not the base.

    With a commit of its own on that branch, so "cut from the base" and "cut
    from where the developer was standing" are two different histories rather
    than the same commit under two names.
    """
    elsewhere(based)
    write(based / "src" / "from_elsewhere.py", "value = 1\n")
    git(based, "add", "-A")
    git(based, "commit", "-q", "-m", "work on the branch the developer is on")
    before = {"head": git(based, "rev-parse", "HEAD").stdout.strip(),
              "branches": branches(based)}
    capsys.readouterr()
    code, runner = run(based)
    captured = capsys.readouterr()
    return code, runner, based, before, captured.err


def test_a_fresh_run_from_a_branch_that_is_not_the_base_cuts_from_the_base(
    ran_from_elsewhere,
):
    """Where the developer is standing decides nothing about what the branch
    is cut from, because since story-117 the run cuts it in a worktree of its
    own from the base *by name*.

    So the run proceeds, and what it cut from is asserted rather than inferred:
    the base's tip is in the story branch's history and the branch the
    developer was standing on is not.
    """
    code, runner, target, before, err = ran_from_elsewhere
    assert code == 0
    assert runner.calls != []
    assert ELSEWHERE not in err
    base_tip = git(target, "rev-parse", DEFAULT_BRANCH).stdout.strip()
    stood_on = git(target, "rev-parse", ELSEWHERE).stdout.strip()
    assert git(target, "merge-base", "--is-ancestor", base_tip, STORY_BRANCH,
               check=False).returncode == 0
    assert git(target, "merge-base", "--is-ancestor", stood_on, STORY_BRANCH,
               check=False).returncode != 0


def test_that_run_leaves_the_branch_it_was_invoked_from_alone(
    ran_from_elsewhere,
):
    """Each thing the developer's own checkout keeps, separately: the run
    worked somewhere else entirely, so nothing here moved.

    `test_the_head_leg_is_asked_only_of_a_caller_that_cuts_from_head` below is
    what shows the leg those absences used to come from is still there.
    """
    code, runner, target, before, _ = ran_from_elsewhere
    assert not (target / ".harness" / "runs" / STORY_ID).exists()  # not here
    assert not (target / ".harness" / "logs" / f"{STORY_ID}.log").exists()
    assert branches(target) == before["branches"] + [STORY_BRANCH]
    assert git(target, "rev-parse", "HEAD").stdout.strip() == before["head"]
    assert head_branch(target) == ELSEWHERE
    assert git(target, "status", "--porcelain").stdout == ""
    # And the run did work, in the tree it cut for itself.
    assert run_dir_of(target).is_dir()
    assert (run_dir_of(target) / "state.json").is_file()
    assert log_of(target).is_file()


def test_the_head_leg_is_asked_only_of_a_caller_that_cuts_from_head(based):
    """The leg is still there, and `from_head` is the whole of what asks it.

    Both directions on one repository in one state: asked as a caller that cuts
    from HEAD asks it, the leg reports where the developer is standing; asked
    as a caller that cuts from a named ref asks it — which since story-117 is
    every caller in the harness — it says nothing, while the other two
    questions are still asked wherever a branch is cut.
    """
    elsewhere(based)

    from_head = story_coordinator.base_problems(based, "main", False)
    assert len(from_head) == 1
    assert ELSEWHERE in from_head[0]
    assert "main" in from_head[0]
    # The default is the from-HEAD behaviour, so a caller written before the
    # parameter existed is unchanged by it.
    assert story_coordinator.base_problems(
        based, "main", False, from_head=True) == from_head

    assert story_coordinator.base_problems(
        based, "main", False, from_head=False) == []
    # The other two questions are asked either way: a ref that does not resolve
    # and a base that has drifted from its remote both still report.
    assert story_coordinator.base_problems(
        based, "no-such-branch", True, from_head=False) != []
    base_ahead(based)
    drifted = story_coordinator.base_problems(
        based, "main", False, from_head=False)
    assert len(drifted) == 1
    assert "origin/main" in drifted[0]


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
# Leg two answers from the base as the remote holds it
#
# The leg above compares the base against its *local* remote-tracking ref, and
# until story-144 nothing refreshed that ref first: a checkout that had not
# fetched since the base moved had `main` and `origin/main` in agreement --
# both behind -- and the check passed cleanly while the branch was cut from a
# tree the shared base had left behind. Everything in this section is built
# against that state, which is why it is built by hand: a push moves the
# tracking ref and a fetch is what the refresh now does, so the only way to
# hold "the remote has moved and nothing here has heard about it" is to move
# the remote and put the tracking ref back where it was.
# --------------------------------------------------------------------------


def move_the_remote_on(run_git, branch: str = DEFAULT_BRANCH,
                       remote: str = "origin") -> str:
    """Advance the remote's `branch`, leaving the tracking ref where it was.

    `run_git` is a callable taking git's arguments, so the same construction
    serves the coordinator's repositories and `Planning`'s. What is left behind
    is a checkout whose base and whose `<remote>/<branch>` agree on a commit
    the remote is no longer standing on, which is exactly the checkout
    story-143 was planned in.

    The commit the remote moved to is returned, so a test can say what the
    refresh was supposed to bring back rather than inferring it.
    """
    tracking = f"refs/remotes/{remote}/{branch}"
    was = run_git("rev-parse", tracking).stdout.strip()
    assert was, f"{tracking} does not resolve, so there is nothing to make stale"
    standing_on = run_git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    side = f"_moved-on-the-remote-{branch}"
    # Cut from the tracking ref rather than from `branch`: where the remote
    # stands is what this helper advances, and a remote branch the checkout has
    # no local branch for -- which is the ordinary case for a base whose
    # upstream is not called what the base is called -- has no name to cut from
    # otherwise. For a branch that does have a local counterpart the two are
    # the same commit, which is the state every caller here builds.
    run_git("checkout", "-q", "-b", side, tracking)
    run_git("commit", "-q", "--allow-empty", "-m",
            f"someone else's merge, landed on {remote}/{branch}")
    moved = run_git("rev-parse", "HEAD").stdout.strip()
    run_git("push", "-q", remote, f"{side}:{branch}")
    run_git("checkout", "-q", standing_on)
    run_git("branch", "-q", "-D", side)
    # The push moved the tracking ref, which is the fact this repository is
    # built not to have heard: put it back.
    run_git("update-ref", tracking, was)
    assert run_git("rev-parse", tracking).stdout.strip() == was
    return moved


def unreachable_remote(run_git, tmp_path: Path, remote: str = "origin") -> None:
    """Point the remote's *fetch* URL at a repository that is not there.

    Its push URL is kept, so the only thing that stops working is a fetch:
    everything else an entry point does with the remote -- a plan's claim, its
    push -- goes on working, and what the test then observes is a refresh that
    could not be made rather than a repository that is broken generally.
    """
    pushes_to = run_git("remote", "get-url", remote).stdout.strip()
    assert pushes_to
    run_git("remote", "set-url", remote,
            str(tmp_path / "there-is-no-repository-here.git"))
    run_git("remote", "set-url", "--push", remote, pushes_to)


def all_refs(root: Path) -> dict[str, str]:
    """Every ref, by what a *write to it* would change.

    A symbolic ref is recorded as what it points at and an ordinary one as the
    object it names, because `%(objectname)` resolves a symbolic ref: with it
    alone, `refs/remotes/origin/HEAD` -- which `git remote set-head` makes a
    symbolic ref to `refs/remotes/origin/main` -- reports a new value whenever
    the ref it follows is written, and a reader comparing two of these
    snapshots sees two refs changed where one was written. Reading its target
    instead leaves it unchanged by a write to the ref it follows, and still
    changed by a write to it -- a retarget -- so a scope assertion built on
    this still fails if anything writes a second ref.
    """
    lines = git(root, "for-each-ref",
                "--format=%(refname) "
                "%(if)%(symref)%(then)%(symref)%(else)%(objectname)%(end)"
                ).stdout.splitlines()
    return dict(line.split(" ", 1) for line in lines)


#: The fragment a refresh that could not be made carries in its sentence. It is
#: written once here because two tests below assert its *absence* -- a refresh
#: that was made, and a run that never attempted one -- and an absence of a
#: string nothing else asserts is an absence that empties itself the moment the
#: wording moves. The presence assertions beside them are what hold it: a
#: wording change reddens those rather than quietly greening these.
REFRESH_FAILED = "could not be refreshed"


@pytest.fixture
def stale_tracking(based) -> Path:
    """`main` level with `origin/main`, and the remote a commit further on."""
    move_the_remote_on(lambda *args: git(based, *args))
    assert git(based, "rev-parse", DEFAULT_BRANCH).stdout \
        == git(based, "rev-parse", f"origin/{DEFAULT_BRANCH}").stdout, \
        "the base and its tracking ref were meant to agree"
    return based


def test_a_base_level_with_a_stale_tracking_ref_is_refused(stale_tracking):
    """The story in one test: the check cannot see a stale base until the ref
    it reads is refreshed, and once it is, it refuses.

    The first assertion is the demonstration that the second one fails without
    the implementation: asked of this repository the way it was asked before
    the refresh existed, the check finds nothing to say, because the two things
    it compares agree. Nothing about the repository changes between the two
    calls except that the tracking ref has been brought up to date.
    """
    unrefreshed = story_coordinator.base_problems(
        stale_tracking, DEFAULT_BRANCH, False)
    assert unrefreshed == [], \
        "the check answered something without a refresh, so the refusal below " \
        "would hold whether the refresh happened or not"

    assert story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH) == ""

    refused = story_coordinator.base_problems(
        stale_tracking, DEFAULT_BRANCH, False)
    assert len(refused) == 1
    assert DEFAULT_BRANCH in refused[0]
    assert f"origin/{DEFAULT_BRANCH}" in refused[0]
    assert "behind" in refused[0]


def test_that_refusal_is_the_existing_drifted_base_wording_unchanged(
    stale_tracking, make_based,
):
    """Word for word what a base behind its remote has always been refused
    with: the two repositories are different repositories at different paths,
    so equality here is equality of a message derived from the branch names
    alone -- which is what the unchanged `_refuse_base` produces."""
    fetched = make_based("already-fetched")
    base_behind(fetched)
    known = story_coordinator.base_problems(fetched, DEFAULT_BRANCH, False)
    assert len(known) == 1 and "behind" in known[0]

    story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH)
    assert story_coordinator.base_problems(
        stale_tracking, DEFAULT_BRANCH, False) == known


def test_a_run_whose_base_is_only_stale_in_the_tracking_ref_refuses(
    stale_tracking, capsys,
):
    """Driven through the entry point, where the refusal has to leave nothing
    behind. Its control is `test_a_base_identical_to_its_remote_proceeds`: the
    same repository with a remote that has not moved runs to completion."""
    capsys.readouterr()
    code, runner = run(stale_tracking)
    err = capsys.readouterr().err

    assert code == 1
    assert f"origin/{DEFAULT_BRANCH}" in err
    assert runner.calls == []
    assert not run_dir_of(stale_tracking).exists()
    assert STORY_BRANCH not in branches(stale_tracking)


def test_the_refresh_writes_the_one_tracking_ref_it_reads_and_nothing_else(
    make_based, tmp_path,
):
    """Every ref before and after, with a second stale tracking ref standing
    beside the base's so the absence is an absence and not an emptiness.

    The control is the same function asked about that second branch at the end:
    `origin/trunk` was left where it was by the refresh of `main`, and moves
    the moment the refresh is asked about `trunk`.
    """
    root = make_based("only-that-ref")
    git(root, "checkout", "-q", "-b", OTHER_DEFAULT)
    git(root, "push", "-q", "-u", "origin", OTHER_DEFAULT)
    git(root, "checkout", "-q", DEFAULT_BRANCH)
    move_the_remote_on(lambda *args: git(root, *args))
    move_the_remote_on(lambda *args: git(root, *args), branch=OTHER_DEFAULT)

    tracked = f"refs/remotes/origin/{DEFAULT_BRANCH}"
    other = f"refs/remotes/origin/{OTHER_DEFAULT}"
    before_refs = all_refs(root)
    before = snapshot(root)

    assert story_coordinator.refresh_base(root, DEFAULT_BRANCH) == ""

    after_refs = all_refs(root)
    changed = {name for name in set(before_refs) | set(after_refs)
               if before_refs.get(name) != after_refs.get(name)}
    assert changed == {tracked}, (before_refs, after_refs)
    assert after_refs[other] == before_refs[other]
    # `origin/HEAD` follows the ref that was written, so it is the case
    # `all_refs` is careful about: read as an object it would report a second
    # ref changed, and it is left in the comparison above -- rather than
    # excluded from it -- so a refresh that *retargeted* it would still fail.
    head = "refs/remotes/origin/HEAD"
    assert before_refs[head] == tracked and after_refs[head] == tracked
    assert git(root, "rev-parse", head).stdout \
        == git(root, "rev-parse", tracked).stdout
    # The working tree, the index, HEAD, the stash and the local branches are
    # what `snapshot` holds; only its branch listing may differ, because that
    # listing includes the one ref above.
    after = snapshot(root)
    assert {k: v for k, v in after.items() if k != "branches"} \
        == {k: v for k, v in before.items() if k != "branches"}

    # The control: that other ref is stale in exactly the same way, and the
    # same function moves it when it is the base being refreshed.
    assert story_coordinator.refresh_base(root, OTHER_DEFAULT) == ""
    assert all_refs(root)[other] != before_refs[other]


def test_the_fetch_runs_with_gits_terminal_prompting_disabled(
    stale_tracking, monkeypatch,
):
    """A checkout with no credentials must report a reason rather than hang a
    pre-flight on a password prompt, and `GIT_TERMINAL_PROMPT=0` is the only
    thing git reads for that.

    Two halves, because either alone would be satisfied by something that does
    not disable anything: the fetch is spawned under the prefix, and a child
    spawned under that prefix really does see the variable set.
    """
    spawned = []
    real = subprocess.run

    def watched(command, *args, **kwargs):
        spawned.append(list(command))
        return real(command, *args, **kwargs)

    monkeypatch.setattr(story_coordinator.subprocess, "run", watched)
    assert story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH) == ""

    fetches = [c for c in spawned if "fetch" in c]
    assert len(fetches) == 1, spawned
    prefix = list(story_coordinator.NO_TERMINAL_PROMPT)
    assert fetches[0][:len(prefix)] == prefix, fetches[0]

    seen = real([*prefix, "sh", "-c", "printf %s \"$GIT_TERMINAL_PROMPT\""],
                capture_output=True, text=True, check=True)
    assert seen.stdout == "0"


#: A remote-side branch name deliberately unequal to the local base's, so that
#: a refresh which guessed the base's own name and one which read git's record
#: of the upstream fetch different refs and land different objects.
REMOTE_SIDE = "mainline"


@pytest.fixture
def upstream_under_another_name(make_based) -> tuple[Path, str, str]:
    """A base whose upstream branch is not called what the base is called.

    `main` tracks `origin/mainline`, the remote's `mainline` has moved, and the
    remote's `main` has stayed where it was — so the object a refresh brings
    back says which of the two names it fetched. The tracking ref is returned
    with the commit the remote moved `mainline` to.
    """
    root = make_based("upstream-under-another-name")
    git(root, "push", "-q", "origin", f"{DEFAULT_BRANCH}:{REMOTE_SIDE}")
    git(root, "fetch", "-q", "origin")
    git(root, "config", f"branch.{DEFAULT_BRANCH}.merge",
        f"refs/heads/{REMOTE_SIDE}")
    tracking = f"refs/remotes/origin/{REMOTE_SIDE}"
    assert story_coordinator._base_tracking_ref(root, DEFAULT_BRANCH) == tracking
    moved = move_the_remote_on(lambda *args: git(root, *args),
                               branch=REMOTE_SIDE)
    assert git(root, "rev-parse", f"origin/{DEFAULT_BRANCH}").stdout.strip() \
        != moved, "the two remote branches were meant to differ"
    return root, tracking, moved


def test_the_refresh_fetches_the_branch_git_records_as_the_upstream(
    upstream_under_another_name,
):
    """`branch.<base>.merge` is the remote-side name, and the base's own name is
    only the fallback for a base that states no upstream.

    The object is what tells the two apart: the remote's `mainline` has moved
    and its `main` has not, so a refresh that fetched the base's own name would
    write the *unmoved* commit into the tracking ref and the check below would
    find nothing to say. The control beneath makes exactly that repository — the
    same one with the upstream record removed — and shows the check falling
    silent, so the assertions above are the record's doing rather than
    something that held anyway.
    """
    root, tracking, moved = upstream_under_another_name

    assert story_coordinator.refresh_base(root, DEFAULT_BRANCH) == ""

    assert git(root, "rev-parse", tracking).stdout.strip() == moved
    refused = story_coordinator.base_problems(root, DEFAULT_BRANCH, False)
    assert len(refused) == 1 and "behind" in refused[0]

    # The control: with nothing recording an upstream, the refresh has only the
    # base's own name to go on, fetches `main`, leaves `origin/mainline` where
    # it stands, and the check has nothing to say about a base level with it.
    git(root, "config", "--unset", f"branch.{DEFAULT_BRANCH}.merge")
    git(root, "update-ref", tracking,
        git(root, "rev-parse", DEFAULT_BRANCH).stdout.strip())
    assert story_coordinator.refresh_base(root, DEFAULT_BRANCH) == ""
    assert git(root, "rev-parse", tracking).stdout.strip() != moved
    assert story_coordinator.base_problems(root, DEFAULT_BRANCH, False) == []


def test_a_fetch_the_platform_will_not_spawn_is_reported_rather_than_raised(
    stale_tracking, monkeypatch,
):
    """The refresh reports every way it can fail, including the one that is not
    a failed fetch: a platform with nothing to run the prompting prefix with
    raises at the spawn, and a pre-flight may not die of that.

    The control is the same call without the monkeypatch, in the first
    assertion: this repository's refresh does succeed, so the sentence below is
    the refused spawn's and not the repository's.
    """
    assert story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH) == ""
    git(stale_tracking, "update-ref", f"refs/remotes/origin/{DEFAULT_BRANCH}",
        git(stale_tracking, "rev-parse", DEFAULT_BRANCH).stdout.strip())

    real = subprocess.run
    refused = "no such executable to run the refresh with"

    def will_not_spawn(command, *args, **kwargs):
        if list(command)[:1] == [story_coordinator.NO_TERMINAL_PROMPT[0]]:
            raise OSError(refused)
        return real(command, *args, **kwargs)

    monkeypatch.setattr(story_coordinator.subprocess, "run", will_not_spawn)

    reason = story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH)
    assert REFRESH_FAILED in reason
    assert refused in reason
    assert "\n" not in reason
    # And the decision is the one the unrefreshed ref gives, unchanged.
    assert story_coordinator.base_problems(
        stale_tracking, DEFAULT_BRANCH, False) == []


def test_a_refresh_that_could_not_be_made_leaves_the_decision_where_it_was(
    stale_tracking, tmp_path, capsys,
):
    """A network condition may not refuse a run: the check reaches exactly the
    verdict it reached before this story, from the tracking ref as it stands,
    and the reason is reported rather than raised.

    Both halves are asserted against the same repository the two tests above
    refuse, so "the decision it would have reached" is one a reader can see is
    different from the decision a reachable remote produces.
    """
    unreachable_remote(lambda *args: git(stale_tracking, *args), tmp_path)
    tracking = git(stale_tracking, "rev-parse",
                   f"origin/{DEFAULT_BRANCH}").stdout.strip()

    reason = story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH)
    assert reason != ""
    assert "\n" not in reason, "one line, printed at an entry point"
    assert DEFAULT_BRANCH in reason
    assert "origin" in reason
    assert REFRESH_FAILED in reason, \
        "the fragment the absences below are asserted against moved"
    # Nothing was refreshed, so the check answers from the ref as it stands.
    assert git(stale_tracking, "rev-parse",
               f"origin/{DEFAULT_BRANCH}").stdout.strip() == tracking
    assert story_coordinator.base_problems(
        stale_tracking, DEFAULT_BRANCH, False) == []

    capsys.readouterr()
    code, runner = run(stale_tracking)
    out = capsys.readouterr().out
    assert code == 0, "a network condition refused the run"
    assert runner.calls != []
    assert reason in out


def test_a_run_that_proceeded_on_an_unrefreshed_ref_says_so_in_its_own_record(
    stale_tracking, tmp_path,
):
    """One write, two renderings, beside the behind-the-base note.

    Its control is `test_a_refresh_that_was_made_records_nothing` below: the
    same readers over a run whose refresh succeeded find nothing.
    """
    unreachable_remote(lambda *args: git(stale_tracking, *args), tmp_path)
    reason = story_coordinator.refresh_base(stale_tracking, DEFAULT_BRANCH)
    assert reason != ""

    assert run(stale_tracking)[0] == 0
    entries = notes(stale_tracking)
    assert [entry["message"] for entry in entries] == [reason]
    assert [line for line in events(stale_tracking) if line == reason] == [reason]


def test_a_refresh_that_was_made_records_nothing(based, capsys):
    """The control for the two above: a reachable remote, the same run, and the
    refresh neither prints a line nor appends a note."""
    capsys.readouterr()
    code, runner = run(based)
    out = capsys.readouterr().out

    assert code == 0
    assert runner.calls != []
    assert notes(based) == []
    assert REFRESH_FAILED not in out


@pytest.mark.parametrize("repository", ["no-remote", "no-counterpart",
                                        "not-a-repository"])
def test_nothing_to_refresh_is_not_a_failed_refresh(
    make_based, tmp_path, repository,
):
    """A repository with no remote, a base nobody has pushed, and a root that
    is not a repository each have nothing to refresh and report nothing.

    The control is the fourth case, built in the same loop's shape: the same
    function over a repository that *does* have a counterpart and cannot reach
    its remote answers a sentence.
    """
    if repository == "no-remote":
        root = make_based("nothing-no-remote", remote=False)
        base = DEFAULT_BRANCH
    elif repository == "no-counterpart":
        root = make_based("nothing-no-counterpart")
        git(root, "checkout", "-q", "-b", OTHER_DEFAULT)
        base = OTHER_DEFAULT
        assert git(root, "rev-parse", "--verify", f"refs/remotes/origin/{base}",
                   check=False).returncode != 0
    else:
        root = tmp_path / "nothing-not-a-repository"
        root.mkdir()
        base = DEFAULT_BRANCH

    assert story_coordinator.refresh_base(root, base) == ""

    reachable = make_based("nothing-control")
    unreachable_remote(lambda *args: git(reachable, *args), tmp_path)
    assert story_coordinator.refresh_base(reachable, DEFAULT_BRANCH) != ""


def test_the_refresh_is_not_attempted_for_a_story_branch_that_already_exists(
    stale_tracking, tmp_path, capsys,
):
    """A resume does no network work, so it cannot be told anything about the
    remote -- and is not refused for a base it is not cutting anything from.

    The observable is the failed refresh's own report: with an unreachable
    remote a refresh that was attempted says so, and this run says nothing. Its
    control is the same repository with the same unreachable remote and the
    branch not yet created, where the sentence is printed and noted.
    """
    unreachable_remote(lambda *args: git(stale_tracking, *args), tmp_path)
    git(stale_tracking, "branch", STORY_BRANCH)

    capsys.readouterr()
    code, runner = run(stale_tracking)
    out = capsys.readouterr().out
    assert code == 0
    assert runner.calls != []
    assert REFRESH_FAILED not in out
    assert notes(stale_tracking) == []


def test_that_same_repository_reports_it_when_the_branch_is_not_there_yet(
    stale_tracking, tmp_path, capsys,
):
    """The control for the test above: one thing differs -- whether the story
    branch already exists -- and the refresh is attempted and reported."""
    unreachable_remote(lambda *args: git(stale_tracking, *args), tmp_path)

    capsys.readouterr()
    code, runner = run(stale_tracking)
    out = capsys.readouterr().out
    assert code == 0
    assert runner.calls != []
    assert REFRESH_FAILED in out
    assert len(notes(stale_tracking)) == 1


def test_base_problems_keeps_its_signature_and_decides_nothing_new(based):
    """The refresh changes what the second leg reads, never what the check
    decides, so the check the rest of this module asserts against is the one it
    was asserted against before: same parameters, in the same order."""
    import inspect

    assert list(inspect.signature(
        story_coordinator.base_problems).parameters) \
        == ["target_root", "base", "declared", "from_head"]
    assert story_coordinator.base_problems(based, DEFAULT_BRANCH, False) == []


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

    # Driven, where the precedence no longer arises: a run cuts from the base
    # by name and does not ask the HEAD leg at all, so the sentence it prints
    # for a repository in this state is the drift one — the leg that is asked
    # wherever a branch is cut.
    capsys.readouterr()
    code, _ = run(subject)
    err = capsys.readouterr().err
    assert code == 1
    assert ELSEWHERE not in err
    assert drift[0] in err

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
    # In the tree the run works in, which is where the branch is checked out.
    assert (conftest.run_root_for(based, STORY_ID) / "src"
            / "from_the_other_branch.py").is_file()


def test_without_the_flag_the_branch_is_not_cut_from_that_ref(based):
    """The control for the ancestry above: the same repository, same other
    branch, no flag — and the other branch's tip is not in the history."""
    tip = other_branch(based)
    assert run(based)[0] == 0
    assert git(based, "merge-base", "--is-ancestor", tip, STORY_BRANCH,
               check=False).returncode != 0
    assert not (conftest.run_root_for(based, STORY_ID) / "src"
                / "from_the_other_branch.py").exists()


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
    """After an escalation the run's tree is on the story branch, which is by
    construction not the base, so a guard that applied to a resume would refuse
    every one of them."""
    code, _ = run(based, verdicts=[FAIL_AT_ONCE])
    assert code == 2
    assert state_of(based)["status"] == "escalated"
    # The run's own tree since story-117, rather than the developer's checkout,
    # which the escalation left where it found it.
    tree = conftest.run_root_for(based, STORY_ID)
    assert head_branch(tree) == STORY_BRANCH

    # Something establishable has to have changed, or the resume is refused by
    # story-021's own guard rather than reaching the base question at all. Done
    # in that tree, because that is the tree the guard and the clean-tree
    # pre-flight both read.
    commit(tree, "the developer's own repair")
    base_ahead(based, DEFAULT_BRANCH)

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
    """Run l5-plan with a terminal for stdin and a declining reply in it.

    Since story-087 the script stamps the mandate only where stdin is a
    terminal, and an artifact carrying none is refused before it is committed.
    This module's subject is where a plan commit lands, so its sessions need to
    reach the commit at all.
    """
    with conftest.a_terminal_for_stdin() as stdin:
        return subprocess.run(
            [sys.executable, str(L5_PLAN), "--workflow", PLANNED_WORKFLOW, *argv],
            cwd=planning.root, env=planning.env(**stub),
            stdin=stdin, capture_output=True, text=True,
        )


PLANNED = ".harness/stories/story-900.yaml"


def planning_stub() -> str:
    return writes((PLANNED, artifact("story-900")))


@pytest.mark.parametrize("condition", ["base-drifted"])
def test_l5_plan_refuses_on_the_same_conditions_before_anything_is_created(
    planning: Planning, condition,
):
    """Since story-117 the refusal happens above the worktree and above the
    session, so nothing was created, nothing was invoked and nothing written.

    The HEAD-standing-on-the-base leg is no longer one of these conditions and
    is not silently dropped: l5-plan cuts its worktree from the base *by name*,
    so where the developer is standing is not a fact about the plan. The test
    below is that half, stated positively.
    """
    planning.git("commit", "-q", "--allow-empty", "-m", "local, never pushed")
    before_head = planning.head()
    before_refs = remote_refs(planning.remote)

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 1
    # The refusal, and then that it happened before anything at all.
    assert "base" in result.stderr
    assert planning.head() == before_head, "HEAD moved"
    assert remote_refs(planning.remote) == before_refs, "something was pushed"
    assert planning.status() == "", \
        "a refusal above the session was meant to write nothing"
    assert not planning.log.exists(), "the session was invoked"


def test_l5_plan_refuses_a_base_that_is_stale_only_in_its_tracking_ref(
    planning: Planning,
):
    """The other entry point over story-144's repository: a plan written in a
    checkout that has not fetched since the base moved is refused, where it
    used to be written against a tree the branch would never hold.

    The first assertion is the demonstration that the refusal is the refresh's
    doing: asked of this repository without one, the check has nothing to say.
    """
    move_the_remote_on(planning.git)
    assert story_coordinator.base_problems(planning.root, "main", False) == [], \
        "the check answered something without a refresh"

    before_head = planning.head()
    before_refs = remote_refs(planning.remote)

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 1
    assert "behind" in result.stderr
    assert planning.head() == before_head, "HEAD moved"
    assert remote_refs(planning.remote) == before_refs, "something was pushed"
    assert planning.status() == ""
    assert not planning.log.exists(), "the session was invoked"


def test_l5_plan_reports_a_refresh_it_could_not_make_and_plans_anyway(
    planning: Planning,
):
    """Reporting may not become the failure, at this entry point too.

    The fetch is made to fail by pointing the base's upstream at a branch the
    remote does not carry, which leaves the remote reachable for everything
    else the session does with it -- its claim and its push -- so what the
    assertions below see is a refresh that could not be made rather than a
    repository that stopped working. Its control is
    `test_the_same_session_on_the_base_commits_and_pushes`, where the same
    session over a base whose upstream is intact prints no such line.
    """
    planning.git("config", "branch.main.merge", "refs/heads/gone-from-the-remote")
    before_head = planning.head()

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 0, result.stderr + result.stdout
    assert REFRESH_FAILED in result.stdout
    assert planning.planned_head() != before_head, "the plan was not committed"


def test_l5_plan_no_longer_refuses_a_developer_standing_off_the_base(
    planning: Planning,
):
    """The leg story-117 dropped, asserted rather than left as an absence.

    Standing on another branch was a refusal while planning happened in the
    developer's own checkout. It cannot be one now: the worktree is cut from
    the base by name, so the plan lands on the base whatever the checkout is
    standing on — and the checkout is left standing there.
    """
    planning.git("checkout", "-q", "-b", ELSEWHERE)
    before_head = planning.head()

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 0, result.stderr + result.stdout
    assert planning.planned_head() != before_head
    assert planning.head() == before_head
    assert planning.git(
        "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == ELSEWHERE


def test_the_same_session_on_the_base_commits_and_pushes(planning: Planning):
    """The control for the refusal above: one thing differs about the
    repository, and the plan's branch moves, the remote's refs move, and the
    developer's working tree is clean of the artifact."""
    before_head = planning.head()
    before_refs = remote_refs(planning.remote)

    result = run_plan(planning, "add a thing", L5_STUB_WRITE=planning_stub())

    assert result.returncode == 0, result.stderr
    assert planning.planned_head() != before_head
    assert remote_refs(planning.remote) != before_refs
    assert PLANNED not in planning.status()
    # And a refresh that was made says nothing. Its control is
    # `test_l5_plan_reports_a_refresh_it_could_not_make_and_plans_anyway`,
    # where the same session over a base whose upstream is gone does print it.
    assert REFRESH_FAILED not in result.stdout


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
    assert planning.planned_head() != before_head, "the artifact was not committed"


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
    """Both real scripts, both over a base that has drifted from its remote,
    and their refusals compared as text: one function or the texts would not
    be equal.

    The condition is the drift rather than where HEAD is standing, because
    since story-117 both entry points cut from the base by name and neither
    asks the HEAD leg — so the leg they *do* both ask is what a comparison of
    one shared function can be made over.

    The control is built in — the two repositories are different repositories
    with different paths, so equality here is equality of a message derived
    from the branch names alone, which is what a shared derivation produces.
    """
    base_ahead(based)
    planning.git("commit", "-q", "--allow-empty", "-m", "local, never pushed")

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
