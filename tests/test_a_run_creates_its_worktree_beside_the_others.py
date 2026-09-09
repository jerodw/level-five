"""Independent validation for story-123: where a run invoked from a linked
worktree puts the worktree it creates.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the derivation.** `worktrees.worktree_root` and `worktrees.worktree_path`
    are functions over a repository, so they are asked directly — from the
    primary working tree and from a linked worktree of the same repository,
    with the key unset and with each of its two configured forms.

  * **what a run and a planning session do with it.** Driven end to end: a
    fresh run through `story_coordinator.run_story` against a fake agent
    runner, and a real `scripts/l5-plan` invocation against a stub `claude` on
    PATH. Both are invoked *from the linked worktree*, which is the whole
    configuration this story is about, and both are observed by the directory
    they actually worked in rather than by the path a derivation predicts.

  * **the resume path**, which this story must not have touched: a branch a
    worktree already stands on is answered by `worktrees.find`, so the run
    works in that tree and the derivation never decides anything.

Nothing here reads this repository's own commit graph. Every repository is
built under a temporary directory, its "remote" is a bare repository beside it,
and no test reaches the network or invokes a model.

The fixtures are the ones `test_planning_runs_on_its_own_worktree` already
built for story-117 — the target with its bare remote and stub session, the
fake runner, and the workflow the run executes — imported rather than copied,
so a target this suite drives is built one way. What is added here is
`cut_linked_worktree`, which is the configuration none of them needed.

Every absence asserted here carries a demonstration that it can fail, and all
of them are the same demonstration: `before_this_story` is this working tree's
own `worktrees.py` with the one expression this story changed put back to what
it was, loaded as its own module. So:

  * "the answer from a linked worktree is not a directory named for that
    worktree" sits beside the same call into that module, which answers exactly
    that directory — the defect, constructed rather than described;
  * "no second directory of working trees was created" sits beside the path
    that module names, so the assertion is looking where one would have been;
  * "the configured forms are unchanged" is the same pair answering
    *identically*, which is what makes "unchanged" a reading rather than a
    claim;
  * "the answer from the primary tree is unchanged" is that pair agreeing too.

Every comparison between two worktree paths resolves both sides: a temporary
directory is reached through a symlink on this repository's own platform, and
git spells a linked worktree's repository with the symlink resolved.
"""
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import story_coordinator
import worktrees

from test_planning_runs_on_its_own_worktree import (
    DEFAULT_BRANCH, L5_PLAN, STORY_ID, WORKFLOW, Runner, Target, build_target,
    planned, planned_story)

#: The working tree's own module file, which `before_this_story` mutates. Read
#: as a path rather than as source text, because that is what `load_mutant`
#: takes and why it takes it.
WORKTREES_SOURCE = Path(worktrees.__file__).resolve()

#: The two spellings of the unset-`worktree_dir` answer: the one this working
#: tree carries, and the one it carried before this story. The substitution is
#: asserted to have found its anchor by `load_mutant` itself, so a story that
#: rewrites this expression again reddens here as itself rather than quietly
#: producing a mutant identical to the original.
TODAYS_DERIVATION = (
    "    primary = primary_root(target_root)\n"
    '    return primary.parent / f"{primary.name}{DEFAULT_SUFFIX}"'
)
THE_DERIVATION_BEFORE_THIS_STORY = (
    '    return target_root.parent / f"{target_root.name}{DEFAULT_SUFFIX}"'
)

#: The id whose story branch the linked worktree below stands on. Any branch
#: other than the one a run is asked for would do; a story branch is what a
#: developer's own tree actually stands on when they invoke the harness from
#: one, which is the situation the story describes.
LINKED_STORY_ID = "story-118"

#: The suffix the fixture repository's worktree directory is named with. Stated
#: here beside the fixture that builds the repository rather than read off the
#: module under test, so the expected directory is named by the fixture and not
#: recomputed by the implementation being asked about itself.
WORKTREE_SUFFIX = "-worktrees"

UNCONFIGURED: dict = {}


@pytest.fixture
def before_this_story(tmp_path: Path):
    """`worktrees` with the one expression this story changed put back.

    The control for every absence below. It is the working tree's module with a
    single substitution, so the difference between it and the real one is this
    story's change and nothing else — which is what makes "the real module
    answers elsewhere" a demonstration of the defect rather than a description
    of it.
    """
    return conftest.load_mutant(
        WORKTREES_SOURCE,
        [(TODAYS_DERIVATION, THE_DERIVATION_BEFORE_THIS_STORY)],
        name="worktrees_before_story_123",
        tmp_path=tmp_path,
    )


@pytest.fixture
def target(tmp_path: Path) -> Target:
    return build_target(tmp_path / "target")


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "beside-the-others-harness")


def worktree_directory(target: Target) -> Path:
    """Where this repository's working trees stand, named by the fixture.

    `target.root` is the primary working tree because the fixture created it
    and then cut everything else from it. Naming the directory from that path
    is what keeps the expectation independent of the derivation being asked
    about: nothing here asks git which tree is primary.
    """
    return target.root.parent / f"{target.root.name}{WORKTREE_SUFFIX}"


def cut_linked_worktree(target: Target, story_id: str = LINKED_STORY_ID) -> Path:
    """A second working tree of the same repository, where they all stand.

    The configuration this story is about: a developer standing in a story's
    own tree, invoking the harness from there. The tree is placed in the
    repository's worktree directory because that is where the harness put it,
    and its branch is that story's branch for the same reason.
    """
    branch = target.story_branch(story_id)
    path = worktree_directory(target) / branch.replace("/", "-")
    made = worktrees.add(target.root, path, branch, DEFAULT_BRANCH)
    assert not made.problems, made.problems
    return path


def plan_from(target: Target, cwd: Path, *argv: str,
              **stub) -> subprocess.CompletedProcess:
    """One `l5-plan` invocation, run from `cwd` rather than from the checkout.

    `Target.plan` runs the script from the target root, which is the one thing
    every test here has to vary, so the invocation is spelled again with the
    directory as an argument. Everything else — the interpreter, the terminal
    for stdin that approves the plan and declines the run offer, the stub
    environment — is the fixture's.
    """
    with conftest.a_terminal_for_stdin() as stdin:
        return subprocess.run(
            [sys.executable, str(L5_PLAN), "--workflow", "story-workflow",
             *argv],
            cwd=str(cwd), env=target.env(**stub), stdin=stdin,
            capture_output=True, text=True,
        )


# --------------------------------------------------------------------------
# The derivation, asked from each tree of one repository
# --------------------------------------------------------------------------


def test_asked_from_a_linked_worktree_the_answer_is_the_primary_trees_sibling(
        target: Target, before_this_story):
    """The story's first criterion, with the defect built beside it.

    The expected directory is the one the fixture created the repository in,
    named for the tree the fixture made primary. The control is the same
    question asked of the module as it was before this story, which answers a
    directory named for the *linked* tree — so "the answer is not that
    directory" is a reading of two different answers rather than a claim about
    one.
    """
    linked = cut_linked_worktree(target)
    expected = worktree_directory(target)

    answer = worktrees.worktree_root(linked, UNCONFIGURED)

    assert answer.resolve() == expected.resolve()
    # The tree the question was asked from decided nothing: the same question
    # asked from the primary tree answers the same directory.
    assert answer.resolve() == worktrees.worktree_root(
        target.root, UNCONFIGURED).resolve()

    # The control: before this story the same call answered a second directory
    # of working trees, named for the linked worktree it was asked from.
    nested = before_this_story.worktree_root(linked, UNCONFIGURED)
    assert nested.resolve() == (
        linked.parent / f"{linked.name}{WORKTREE_SUFFIX}").resolve()
    assert nested.resolve() != answer.resolve()


def test_asked_from_the_primary_tree_the_answer_is_what_it_always_was(
        target: Target, before_this_story):
    """The second criterion: nothing moves for a developer in their checkout.

    Read as an equality between this working tree's module and the module as it
    was before the story, both asked from the primary tree — so "unchanged" is
    the two answering the same rather than an assertion about a path that
    happens to look right.
    """
    cut_linked_worktree(target)

    answer = worktrees.worktree_root(target.root, UNCONFIGURED)

    assert answer.resolve() == worktree_directory(target).resolve()
    assert answer.resolve() == before_this_story.worktree_root(
        target.root, UNCONFIGURED).resolve()


def test_a_configured_worktree_dir_still_decides_in_both_of_its_forms(
        target: Target, before_this_story, tmp_path: Path):
    """The third criterion: the configured branch is untouched.

    Both forms, asked from both trees. The absolute value is answered verbatim
    whichever tree asks. The relative one resolves against the target root —
    the tree asked from — so the two trees answer differently, which is the
    behaviour the story leaves exactly as it is rather than an inconsistency.

    The control is the module as it was before this story answering identically
    to all four questions: unchanged is a comparison here, not a claim.
    """
    linked = cut_linked_worktree(target)
    absolute = tmp_path / "somewhere-named-outright"
    configured_absolute = {worktrees.WORKTREE_DIR_KEY: str(absolute)}
    configured_relative = {worktrees.WORKTREE_DIR_KEY: "../named-relatively"}

    for root in (target.root, linked):
        verbatim = worktrees.worktree_root(root, configured_absolute)
        assert verbatim.resolve() == absolute.resolve()
        assert verbatim.resolve() == before_this_story.worktree_root(
            root, configured_absolute).resolve()

        resolved = worktrees.worktree_root(root, configured_relative)
        assert resolved.resolve() == (root.parent / "named-relatively").resolve()
        assert resolved.resolve() == before_this_story.worktree_root(
            root, configured_relative).resolve()

    # And the two trees do answer differently for the relative form, which is
    # what makes the loop above a reading of the resolution rather than of a
    # value that could not have varied.
    assert worktrees.worktree_root(
        target.root, configured_relative).resolve() != worktrees.worktree_root(
            linked, configured_relative).resolve()


def test_the_path_for_a_branch_follows_the_root_it_is_derived_from(
        target: Target, before_this_story):
    """`worktree_path` is where every caller reaches the derivation, so the
    corrected answer has to arrive through it rather than only through
    `worktree_root`.

    Asked from the linked worktree for a branch no tree stands on, which is the
    fresh-run case, with the same control beneath it.
    """
    linked = cut_linked_worktree(target)
    branch = target.story_branch(STORY_ID)

    path = worktrees.worktree_path(linked, UNCONFIGURED, branch)

    assert path.parent.resolve() == worktree_directory(target).resolve()
    assert path.parent.resolve() == linked.parent.resolve()
    assert path.resolve() != before_this_story.worktree_path(
        linked, UNCONFIGURED, branch).resolve()


# --------------------------------------------------------------------------
# What a run invoked from a linked worktree creates
# --------------------------------------------------------------------------


def test_a_fresh_run_from_a_linked_worktree_cuts_its_tree_beside_that_one(
        target: Target, harness, before_this_story):
    """The fourth criterion, driven end to end.

    The run is invoked from the linked worktree for a branch no tree stands on.
    Its tree is observed by where the stages actually ran and where the state
    was written, not by asking the derivation again — the derivation is the
    subject, so it cannot also be the expectation.

    The control for "no second directory of working trees was created" is the
    path the module as it was before this story derives, which is where one
    would have been: the assertion is looking somewhere a failure could show.
    """
    planned_story(target)
    linked = cut_linked_worktree(target)
    runner = Runner(WORKFLOW)

    code = story_coordinator.run_story(STORY_ID, harness, linked, runner)
    assert code == 0, runner.calls

    trees = {tree.resolve() for tree in runner.trees}
    assert len(trees) == 1, runner.trees
    tree = trees.pop()
    assert tree.parent == worktree_directory(target).resolve()
    assert tree.parent == linked.resolve().parent
    assert tree != linked.resolve()
    assert (tree / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    assert worktrees.standing_branch(tree) == target.story_branch(STORY_ID)

    # The absence, with the place it is looking established as a different
    # place from the one the tree is actually in: the run did create a tree,
    # and it created it where the primary tree names rather than there.
    nested = before_this_story.worktree_root(linked, UNCONFIGURED)
    assert nested.resolve() != tree.parent
    assert not nested.exists(), f"a second directory of working trees at {nested}"


def test_a_planning_session_from_a_linked_worktree_lands_in_the_same_directory(
        target: Target, before_this_story):
    """The fifth criterion: planning reaches the same derivation, so its
    detached worktree lands where a run's does.

    Observed by where the session wrote — the stub writes its artifact relative
    to the directory it is handed — because the worktree itself is removed once
    the plan is pushed, and the directory the session ran in is the fact the
    criterion is about.
    """
    linked = cut_linked_worktree(target)

    result = plan_from(target, linked, "a request", L5_STUB_WRITE=planned())
    assert result.returncode == 0, result.stdout + result.stderr

    session_cwd = Path(target.session()["cwd"]).resolve()
    assert session_cwd.parent == worktree_directory(target).resolve()
    assert session_cwd.parent == linked.resolve().parent
    assert session_cwd != linked.resolve()

    nested = before_this_story.worktree_root(linked, UNCONFIGURED)
    assert nested.resolve() != session_cwd.parent
    assert not nested.exists(), f"a second directory of working trees at {nested}"


def test_a_run_from_a_linked_worktree_for_a_branch_with_a_tree_uses_that_tree(
        target: Target, harness):
    """The sixth criterion: the resume path is answered by `worktrees.find`,
    above the derivation, so this story cannot have moved it.

    The existing tree is deliberately placed somewhere neither derivation would
    name — not the repository's worktree directory and not a directory named
    for the invoking tree — so that working in it is `find` answering rather
    than a path either version of `worktree_root` could have produced.
    """
    planned_story(target)
    linked = cut_linked_worktree(target)
    branch = target.story_branch(STORY_ID)
    elsewhere = target.root.parent / "somewhere-else-entirely"
    made = worktrees.add(target.root, elsewhere, branch, DEFAULT_BRANCH)
    assert not made.problems, made.problems

    runner = Runner(WORKFLOW)
    code = story_coordinator.run_story(STORY_ID, harness, linked, runner)
    assert code == 0, runner.calls

    assert {tree.resolve() for tree in runner.trees} == {elsewhere.resolve()}
    assert (elsewhere / ".harness" / "runs" / STORY_ID / "state.json").is_file()
    # And nothing was created for that branch where the derivation would have
    # put it, which is the control's other half: `find` answered first.
    assert not (worktree_directory(target) / branch.replace("/", "-")).exists()
