"""Independent validation for the two refusals a run makes when the story
artifact is in neither the invoked tree nor the story branch.

A run whose artifact is absent here resolves it through its branch, fetching
that branch when this clone has never seen it. When the fetch could not ask --
no remote configured, a spawn the platform refused, a fetch killed at the
bound, a fetch that exited non-zero -- the run used to refuse with a sentence
saying the artifact was "none on branch <branch> here or on the remote" and
directing the developer to run `l5-plan`, neither of which the fetch had
established. Since planning happens in a worktree that is removed when its run
offer is declined, "the artifact is only on the remote" is the ordinary case,
so that sentence was ordinarily false rather than exotically so.

Two subjects, kept apart:

  * **which refusal is printed**, which turns on whether the fetch reported a
    reason. Driven through `run_story` against target repositories built under
    `tmp_path`, with a `git` these tests write standing in for the remote's
    behaviour, so the two sentences are compared side by side on repositories
    differing in exactly what the fetch did.
  * **what neither refusal creates**, which is asserted with the same readers a
    successful run is asserted with, so an emptiness here is an emptiness
    rather than a reader looking in the wrong place.

Every absence asserted carries a demonstration that it can fail:

  * "the could-not-ask refusal does not direct the developer to l5-plan and
    does not state what the remote holds" sits beside the ordinary refusal,
    printed by the same function for the same story on a repository differing
    only in what its fetch did, where that direction and that claim are exactly
    what is printed -- and the clause asserted absent is *taken from* the
    ordinary sentence rather than spelled here, so a rewording of either moves
    both;
  * "neither refusal left a run directory, a state.json, a log or a branch, and
    neither invoked an agent" sits beside the same readers over a run of the
    same story on a repository whose artifact is present, where every one of
    them is created and the agent is invoked;
  * "the run did not retry the fetch and did not proceed without the artifact"
    is asserted by counting the fetches of the story branch a refusing run
    spawned -- recorded as they are spawned rather than read out of the source
    -- beside the assertion that the count is not zero, which a run that never
    fetched at all would satisfy.

The reasons themselves -- that each way of not being able to ask produces its
own sentence, and that the two fetches this module's subject spawns word an
outcome the same way -- are `tests/test_branch_base.py`'s subject and are not
restated here. What is asserted here is what the caller does with a reason.

The repositories, the fake runner and the readers are imported from
`test_foreign_work_refusal`, and the `git` stubs from `test_branch_base`,
rather than copied: one home for one fact is why a regression in either shows
up here.

No model is invoked anywhere in this file, and nothing here resolves a baseline
out of this repository's commit graph.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from test_foreign_work_refusal import (
    REPO_ROOT,
    STORY_BRANCH,
    STORY_ID,
    Runner,
    branches,
    build_target,
    git,
    log_of,
    run_dir_of,
)
from test_branch_base import (
    FETCH_EXIT_CODE,
    add_remote,
    answers_at_once,
    fails_saying,
    fails_silently,
    git_that,
    on_path,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

import story_coordinator  # noqa: E402

#: What the remote says when it answers the story branch's fetch with an error.
#: Deliberately unlike anything the coordinator would compose, so finding it in
#: a refusal says the reason reached the sentence.
REMOTE_DECLINED = "fatal-this-remote-will-not-serve-that-ref"

#: The direction the ordinary refusal gives and the could-not-ask refusal may
#: not: a story that *is* planned, whose artifact *is* on the remote, must not
#: be planned again because one fetch went unanswered.
PLAN_AGAIN = "l5-plan"


def without_its_story(root: Path) -> Path:
    """A target repository whose story artifact is not in the invoked tree.

    Which is what a clone that did not plan the story looks like: the artifact
    was written in a worktree of the planning run's own, pushed on the story
    branch, and the worktree removed when the run offer was declined.
    """
    build_target(root)
    artifact = root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    artifact.unlink()
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the story was planned somewhere else")
    assert not artifact.is_file()
    return root


def drive_run(target_root: Path, capsys) -> tuple[int, str, Runner]:
    """Drive `run_story` and give back its status, what it printed, and the
    runner it was handed -- whose recorded calls are how "no agent was invoked"
    is an observable emptiness rather than an argument."""
    capsys.readouterr()
    runner = Runner(target_root)
    code = story_coordinator.run_story(
        STORY_ID, REPO_ROOT, target_root, runner)
    return code, capsys.readouterr().err, runner


def created_nothing(target_root: Path, runner: Runner) -> list[str]:
    """Everything a run creates that a refusal above the run directory must not.

    Reported as a list of what *was* found rather than asserted here, so one
    reader serves both the refusals and the control beneath them: over a run
    that was allowed to proceed the same call reports every one of these.
    """
    found = []
    if run_dir_of(target_root).exists():
        found.append("run directory")
    if (run_dir_of(target_root) / "state.json").exists():
        found.append("state.json")
    if log_of(target_root).exists():
        found.append("log")
    if STORY_BRANCH in branches(target_root):
        found.append("branch")
    if runner.calls:
        found.append("agent invocation")
    return found


@pytest.fixture
def could_not_ask(tmp_path, monkeypatch, capsys):
    """A run whose fetch reported a reason, refused.

    The remote is real and the fetch is the stub's: it answers with an error and
    says why, which is one of the four ways this clone cannot establish what the
    remote holds. Which of the four it is does not matter here -- the caller
    branches on there being a reason, not on which one.
    """
    root = without_its_story(tmp_path / "could-not-ask")
    add_remote(tmp_path, root)
    with monkeypatch.context() as failing:
        on_path(failing, git_that(tmp_path / "declining-remote",
                                  fails_saying(REMOTE_DECLINED)))
        return drive_run(root, capsys) + (root,)


@pytest.fixture
def the_remote_answered(tmp_path, monkeypatch, capsys):
    """A run whose fetch established what the remote holds, refused.

    The stub's fetch exits zero and produces no branch, which is a remote that
    answered and does not have it -- the one case in which the sentence this
    run has always printed is true.
    """
    root = without_its_story(tmp_path / "the-remote-answered")
    add_remote(tmp_path, root)
    with monkeypatch.context() as answering:
        on_path(answering, git_that(tmp_path / "answering-remote",
                                    answers_at_once()))
        return drive_run(root, capsys) + (root,)


def test_a_fetch_that_answered_still_prints_the_sentence_it_printed_before(
    the_remote_answered,
):
    """The remote was asked and does not have the branch, so nothing about the
    old sentence was wrong and nothing about it changes: it names the path, the
    branch, the remote, and tells the developer to plan the story.

    This is the control for every absence the test below asserts.
    """
    code, printed, _, root = the_remote_answered

    assert code == 1
    story_path = root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    assert str(story_path) in printed
    assert STORY_BRANCH in printed
    assert "on the remote" in printed, printed
    assert PLAN_AGAIN in printed, printed


def the_claim_about_the_remote(ordinary: str) -> str:
    """The clause of the ordinary refusal that states what the remote holds.

    Taken from the ordinary sentence rather than spelled here, so the absence
    asserted against the could-not-ask refusal is an absence of *that* sentence
    rather than of a fragment written down once and left to go stale when
    either refusal is reworded.
    """
    assert ", and " in ordinary, ordinary
    return ordinary.split(", and ", 1)[1].strip()


def test_a_fetch_that_could_not_ask_says_so_instead_of_speaking_for_the_remote(
    could_not_ask, the_remote_answered,
):
    """The refusal a run makes when nobody reached the remote: it says the
    artifact is not in this tree, that this clone could not reach the remote to
    find out, and why -- and it says nothing about what the remote holds and
    does not send the developer back to plan a story that may already be
    planned.

    The two absences are controlled by the ordinary refusal beside them, printed
    by the same function for the same story id: the direction and the claim are
    both found there, so finding neither here is the reason's doing rather than
    a reader that cannot see either sentence.
    """
    code, printed, _, root = could_not_ask
    _, ordinary, _, _ = the_remote_answered

    assert code == 1
    story_path = root / ".harness" / "stories" / f"{STORY_ID}.yaml"
    assert str(story_path) in printed
    assert STORY_BRANCH in printed
    assert "could not reach the remote" in printed, printed
    assert REMOTE_DECLINED in printed, printed

    # The control for both absences: the same fragments in the ordinary sentence.
    claim = the_claim_about_the_remote(ordinary)
    assert PLAN_AGAIN in ordinary and claim in ordinary

    assert PLAN_AGAIN not in printed, printed
    assert claim not in printed, printed


def test_both_refusals_return_the_same_status_and_create_nothing(
    could_not_ask, the_remote_answered,
):
    """One status for one situation, and neither refusal reaches anything a run
    creates: the artifact is read above the run directory, the state file, the
    log, the branch and every agent invocation.

    The emptiness is controlled by `test_the_same_run_creates_every_one_of_those`
    below, which runs the same story through the same reader on a repository
    whose artifact is present.
    """
    refused = [could_not_ask, the_remote_answered]
    assert [code for code, _, _, _ in refused] == [1, 1]

    for code, _, runner, root in refused:
        assert created_nothing(root, runner) == [], root.name


def test_the_same_run_creates_every_one_of_those(tmp_path, capsys):
    """The control for the emptiness above: the same story, the same reader, on
    a repository that has its artifact -- where the run directory, the state
    file, the log and the branch are all created and the agent is invoked."""
    root = build_target(tmp_path / "artifact-present")
    code, _, runner = drive_run(root, capsys)

    assert code == 0
    assert sorted(created_nothing(root, runner)) == sorted(
        ["run directory", "state.json", "log", "branch", "agent invocation"])


def fetches_spawned(patch) -> list[list[str]]:
    """Every fetch the coordinator spawns, recorded rather than read out of the
    source, so what is counted is what ran."""
    seen: list[list[str]] = []
    real = subprocess.Popen

    def watched(command, *args, **kwargs):
        argv = list(command) if isinstance(command, (list, tuple)) else []
        if "fetch" in argv:
            seen.append(argv)
        return real(command, *args, **kwargs)

    patch.setattr(story_coordinator.subprocess, "Popen", watched)
    return seen


def test_a_refusing_run_asks_once_and_then_proceeds_on_nothing(
    tmp_path, monkeypatch, capsys,
):
    """Nothing this story added retries the fetch or lets the run go on without
    the artifact: it is asked for once, the answer is a reason, and the run
    refuses.

    Asserted by counting the fetches of the story branch the run spawned,
    recorded as they are spawned rather than read out of the source. A retry
    would show as a second one, and a run that fetched nothing at all would
    make an equality against zero vacuous -- which is why the count asserted is
    one rather than an absence.

    That the one fetch is bounded, and by the module's single constant, is
    `test_a_stalled_fetch_of_a_story_branch_leaves_it_a_branch_this_clone_lacks`
    in `tests/test_branch_base.py` and is not restated here.
    """
    root = without_its_story(tmp_path / "asks-once")
    add_remote(tmp_path, root)

    with monkeypatch.context() as failing:
        spawned = fetches_spawned(failing)
        on_path(failing, git_that(tmp_path / "declining-once",
                                  fails_saying(REMOTE_DECLINED)))
        code, printed, runner = drive_run(root, capsys)

    assert code == 1
    assert REMOTE_DECLINED in printed
    of_the_story_branch = [argv for argv in spawned
                           if f"{STORY_BRANCH}:{STORY_BRANCH}" in argv]
    assert len(of_the_story_branch) == 1, spawned
    assert created_nothing(root, runner) == []


def test_a_clone_with_no_remote_is_not_told_the_branch_is_absent_from_one(
    tmp_path, capsys,
):
    """The fourth way of not being able to ask, which is not a failed fetch:
    there was no remote to fetch from, and the run used to answer that with the
    same claim about a remote that does not exist.

    Its control is the ordinary refusal in
    `test_a_fetch_that_answered_still_prints_the_sentence_it_printed_before`:
    the same function, the same story, a repository with a remote that answers,
    where the claim and the direction are printed.
    """
    root = without_its_story(tmp_path / "no-remote-at-all")
    assert git(root, "remote").stdout.split() == []

    code, printed, runner = drive_run(root, capsys)

    assert code == 1
    assert "could not reach the remote" in printed, printed
    assert "no remote" in printed, printed
    assert PLAN_AGAIN not in printed, printed
    assert created_nothing(root, runner) == []


def test_a_fetch_that_said_nothing_still_refuses_with_a_reason(
    tmp_path, monkeypatch, capsys,
):
    """A remote that answers with an error and no words leaves the run with the
    exit status to report, and it is reported rather than swallowed back into
    the sentence about what the remote holds.

    Beside `test_a_fetch_that_could_not_ask_says_so_instead_of_speaking_for_the_
    remote`, whose reason carries the remote's own words: between them the
    refusal is shown to carry a reason whether or not git wrote one.
    """
    root = without_its_story(tmp_path / "silent-failure")
    add_remote(tmp_path, root)

    with monkeypatch.context() as silent:
        on_path(silent, git_that(tmp_path / "silent-remote",
                                 fails_silently()))
        code, printed, runner = drive_run(root, capsys)

    assert code == 1
    assert f"exited {FETCH_EXIT_CODE}" in printed, printed
    assert PLAN_AGAIN not in printed, printed
    assert created_nothing(root, runner) == []


