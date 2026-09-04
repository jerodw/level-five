"""Independent validation for story-103: the shared-checkout form of the
harness comparison asks where the working tree is standing.

The subject is one leg of `unchanged_since_escalation`. When the harness and
the target are one checkout, that leg used to append its evidence outright,
deferring to the branch comparison beside it. story-102 repointed the branch
comparison at `state.branch~1` so an off-branch resume could be refused, and
the deferral's premise went with it: `state.branch~1` says nothing about the
tree the developer is standing in, and the `status --porcelain` leg beside it
says that tree is clean, not that it is that branch's tree. So a developer who
checked out the base — the act the escalation exists to make safe — committed a
harness fix there and re-ran was told that nothing had establishably changed.
This story conditions the leg on the tree resolving to `state.branch`.

The fixtures are `tests/test_resume_guard.py`'s, imported rather than copied:
one checkout carrying both a target project and a harness root materialized
from a workflow that module builds, and a target repository with a harness
checkout beside it. They are extended here only by the `.gitignore` that keeps
the run directory out of the index, because every case below stands somewhere
else after the escalation, and a tracked run directory leaves the working tree
the moment it does — taking the recorded state a resume reads with it.

Every absence asserted here carries a demonstration that the same check reports
the situation it exists to catch:

  * "the shared-checkout resume from the base is not refused" sits beside a
    mutant of today's coordinator with the standing condition deleted, which
    refuses that identical resume, and beside the same fixture standing on the
    story branch, which today's coordinator still refuses;
  * "the leg establishes nothing from any other revision" is asserted over
    every standing a developer can take — the base, a third branch, a detached
    HEAD elsewhere — each paired with that same mutant, which establishes
    sameness from all of them, and with a detached HEAD *at the story branch's
    own commit*, where today's coordinator does refuse: so the emptiness is the
    condition's doing rather than an off-branch resume being unable to reach
    the leg at all;
  * "the separate-checkout form is untouched" is a refusal from that same
    off-branch standing, controlled by the shared fixture standing there, which
    is not refused, and by clearing the recorded revision, which clears it — so
    the refusal is the recorded-revision leg answering rather than any leg;
  * "the prose no longer claims the branch comparison covers every change to
    the harness source" is a scan whose control is the frozen text of this
    function that did carry the claim, committed under `tests/history-fixtures/`,
    which the same scan reports.

The `not standing` half of the condition is not asserted through the guard, and
deliberately: a tree whose HEAD git cannot resolve is a tree the `status
--porcelain` leg above has already refused on, so an assertion driven through
the guard would hold whatever that half said. What `_revision` answers for a
root it cannot read is asserted where that helper's contract is validated.

Nothing here invokes a model: every run goes through the fake agent runner
`tests/test_resume_guard.py` defines, and no baseline is resolved out of this
repository's commit graph.
"""
import ast
import json
from pathlib import Path

import conftest
from conftest import function_source, load_mutant

import story_coordinator

from test_resume_guard import (APP_AT_HEAD, COORDINATOR_PATH, DEFAULT_BRANCH,
                               PASS, QUIET_GITIGNORE, STORY_BRANCH, STORY_ID,
                               VERIFIER_STAGE, WORKFLOW_REL, Runner,
                               build_harness, build_target, escalate, git,
                               guard, state_of, write, write_json)

#: The frozen text of `unchanged_since_escalation` as it stood while the
#: shared-checkout leg deferred outright. A committed fixture rather than a
#: revision resolved out of this repository's graph, and used here for one
#: thing: as the control for the prose scan below — a text that does carry the
#: claim today's source must not.
PROSE_THAT_CARRIED_THE_BLANKET_CLAIM = (
    "story_coordinator.unchanged_since_escalation.at-story-034-endpoint.py.txt"
)

#: The claim this story removes from the docstring and from the comment: that
#: one clean tree at the escalation commit covers the harness source whatever
#: tree the developer is standing in. Written once and scanned for in both.
BLANKET_CLAIM = "covers every change to the harness source"

#: What both places must say instead, and what the leg now requires.
STANDING_CLAIM = "standing on"

#: The condition this story adds, deleted to build the control mutant.
#: `conftest.load_mutant` asserts the anchor occurs, so a leg whose text has
#: moved fails as itself rather than silently mutating nothing.
STANDING_CONDITION = """        standing = _revision(target_root)
        if not standing or standing != _revision(target_root, state.branch):
            return []
"""

#: A branch that is neither the base nor the story branch: where a developer is
#: standing when they are in the middle of something else.
THIRD_BRANCH = "another-piece-of-work"


# --------------------------------------------------------------------------
# The repositories, and the coordinator without this story's condition
# --------------------------------------------------------------------------


def shared(tmp_path: Path, name: str = "shared") -> Path:
    """One checkout serving as both roots, with its run directory ignored.

    The ignore is what makes an escalated run survive a checkout of another
    branch: tracked, the run directory exists on the story branch alone and
    leaves the working tree as soon as a developer stands anywhere else, so
    every case below would be a *fresh* run rather than a resume.
    """
    return build_target(tmp_path / name, harness_inside=True,
                        gitignore=QUIET_GITIGNORE)


def separate(tmp_path: Path, name: str = "separate") -> Path:
    """A target whose harness is a checkout beside it, ignored the same way."""
    return build_target(tmp_path / name, harness_inside=False,
                        gitignore=QUIET_GITIGNORE)


def without_the_standing_condition(tmp_path: Path):
    """Today's coordinator with this story's condition deleted.

    Not a module recovered from history: a coordinator lifted out of the graph
    runs against today's workflow, schemas and config and stops running as soon
    as any of them legitimately changes. This is the one line of difference,
    applied to the source the suite is running against.
    """
    return load_mutant(COORDINATOR_PATH, [(STANDING_CONDITION, "")],
                       name="coordinator_without_the_standing_condition",
                       tmp_path=tmp_path)


def fix_the_harness_here(root: Path, message: str = "a fix to the harness") -> None:
    """A change to the harness source, committed where `root` is standing.

    Under this fixture the harness source is the workflow definition the
    checkout carries — what `run_story` reads a harness root for — which is the
    same substitution `tests/test_resume_guard.py` makes when it edits the
    harness and nothing else.
    """
    workflow_path = root / WORKFLOW_REL
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["description"] = "a harness fixed on the base"
    write_json(workflow_path, workflow)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def revision(root: Path, name: str = "HEAD") -> str:
    return git(root, "rev-parse", name).stdout.strip()


# --------------------------------------------------------------------------
# The resume this story exists to permit
# --------------------------------------------------------------------------


def test_a_shared_checkout_resume_from_the_base_reaches_its_stage(tmp_path):
    """The first acceptance criterion, driven as the developer meets it.

    One checkout is both roots. A run escalates, the developer checks out the
    base, commits a harness fix there and re-runs. The story artifact is
    unamended and the story branch untouched, so the first two comparisons
    still establish sameness — and the resume must reach its stage anyway,
    because what the developer changed is exactly what the third comparison is
    supposed to be about.

    Two controls: the same fixture standing on the story branch immediately
    before the checkout, which is refused, and the same situation put to a
    coordinator without the standing condition, which refuses it.
    """
    root = shared(tmp_path)
    escalate(root, root)
    assert guard(root, root) != []                          # the control

    git(root, "checkout", "-q", DEFAULT_BRANCH)
    fix_the_harness_here(root)
    assert git(root, "status", "--porcelain").stdout.strip() == ""
    assert state_of(root)["status"] == "escalated"

    assert guard(root, root) == []

    # The control: the identical situation, decided by today's coordinator with
    # the standing condition deleted, which refuses it — so the emptiness above
    # is that condition rather than anything else about this repository.
    before = without_the_standing_condition(tmp_path)
    assert guard(root, root, module=before) != []

    resumed = Runner(root, PASS)
    assert story_coordinator.run_story(STORY_ID, root, root, resumed) == 0
    assert resumed.calls[0] == VERIFIER_STAGE["name"]
    assert state_of(root)["status"] == "completed"


def test_a_shared_checkout_resume_from_the_branch_is_still_refused(
    tmp_path, capsys,
):
    """The second acceptance criterion: what this story did not open up.

    Standing on the story branch with nothing changed, the resume is still
    refused, no agent is invoked, and the refusal still names three pieces of
    evidence whose third describes the shared checkout — and now the branch the
    tree is standing on — rather than a recorded revision.

    The control is the same fixture from the base, which is not refused, and
    the separate-checkout refusal below, whose third line does name a revision.
    """
    root = shared(tmp_path)
    escalate(root, root)
    capsys.readouterr()

    refused = Runner(root)
    assert story_coordinator.run_story(STORY_ID, root, root, refused) == 1
    message = capsys.readouterr().err
    assert refused.calls == []
    assert state_of(root)["status"] == "escalated"

    evidence = guard(root, root)
    assert len(evidence) == 3
    for line in evidence:
        assert line in message

    harness_line = evidence[2]
    assert "the same checkout as the target" in harness_line
    assert STORY_BRANCH in harness_line
    assert "still at revision" not in harness_line

    # The control: the same fixture, the same run, standing on the base.
    git(root, "checkout", "-q", DEFAULT_BRANCH)
    assert guard(root, root) == []


def test_the_shared_form_establishes_nothing_from_another_revision(tmp_path):
    """The third acceptance criterion, over every standing a developer takes.

    The base, a third branch and a detached HEAD elsewhere: from each of them
    the shared-checkout leg establishes nothing, with the story artifact and
    the story branch untouched throughout — so nothing but where the tree is
    standing differs between these calls.

    Each is paired with the coordinator without the condition, which returns
    its three pieces of evidence from all of them. And the last case is a
    detached HEAD *at the story branch's own commit*, where today's coordinator
    does refuse: the leg asks what revision the tree resolves to rather than
    what the branch is called, and an off-branch resume can still reach it.
    """
    root = shared(tmp_path)
    escalate(root, root)
    before = without_the_standing_condition(tmp_path)

    branch_tip = revision(root, STORY_BRANCH)
    base = revision(root, DEFAULT_BRANCH)
    git(root, "branch", THIRD_BRANCH, DEFAULT_BRANCH)

    for standing in (DEFAULT_BRANCH, THIRD_BRANCH, base):
        git(root, "checkout", "-q", standing)
        assert revision(root) != branch_tip, standing
        assert guard(root, root) == [], standing
        assert len(guard(root, root, module=before)) == 3, standing  # control

    git(root, "checkout", "-q", "--detach", branch_tip)
    assert revision(root) == branch_tip
    assert len(guard(root, root)) == 3


def test_a_separate_checkout_off_branch_resume_is_still_refused(tmp_path):
    """The fourth acceptance criterion: the deployment this story left alone.

    A target with its harness checked out beside it, resumed from the base with
    nothing changed anywhere. The recorded-revision comparison answers as it
    always did, and the refusal stands.

    Two controls. Clearing the recorded revision clears the guard, so the
    refusal is that leg answering rather than the guard refusing on anything.
    And the shared fixture in the identical standing is not refused, so this is
    a fact about the separate-checkout form rather than about off-branch
    resumes.
    """
    target = separate(tmp_path)
    harness = build_harness(tmp_path / "harness")
    escalate(target, harness)
    git(target, "checkout", "-q", DEFAULT_BRANCH)

    evidence = guard(target, harness)
    assert len(evidence) == 3
    assert "still at revision" in evidence[2]
    assert state_of(target)["harness_revision"][:12] in evidence[2]
    assert "the same checkout" not in evidence[2]

    assert guard(target, harness, changes={"harness_revision": ""}) == []

    refused = Runner(target)
    assert story_coordinator.run_story(STORY_ID, harness, target, refused) == 1
    assert refused.calls == []

    # The control: one checkout as both roots, standing where this target is
    # standing, which this story stopped refusing.
    both = shared(tmp_path, "shared-for-the-control")
    escalate(both, both)
    git(both, "checkout", "-q", DEFAULT_BRANCH)
    assert guard(both, both) == []


def test_the_branch_comparison_and_the_porcelain_leg_decide_as_they_did(
    tmp_path,
):
    """The sixth acceptance criterion, from the source and from a resume.

    The escalation-commit comparison still resolves `state.branch~1`, and the
    `status --porcelain` leg still reads the working tree — asserted as text,
    and then driven: an uncommitted edit clears the guard while standing on the
    story branch, where the new condition holds and cannot be what answered.

    The control is the same fixture immediately before the edit, which refuses.
    """
    source = function_source(COORDINATOR_PATH.read_text(encoding="utf-8"),
                             "unchanged_since_escalation")
    assert '_revision(target_root, f"{state.branch}~1")' in source
    assert '_git(target_root, "status", "--porcelain")' in source

    root = shared(tmp_path)
    escalate(root, root)
    assert revision(root) == revision(root, STORY_BRANCH)
    assert guard(root, root) != []                          # the control

    write(root / "src" / "app.py", APP_AT_HEAD + "print('uncommitted')\n")
    assert git(root, "status", "--porcelain").stdout.strip() != ""
    assert guard(root, root) == []


# --------------------------------------------------------------------------
# What the prose at the leg says
# --------------------------------------------------------------------------


def leg_prose(source: str) -> tuple[str, str]:
    """One function's docstring and its comment lines, read apart.

    Apart, because the story asks both to say what the leg now requires, and a
    joined text would let either one carry the other.
    """
    docstring = ast.get_docstring(ast.parse(source).body[0]) or ""
    comments = "\n".join(line for line in source.splitlines()
                         if line.lstrip().startswith("#"))
    return docstring, comments


def test_the_prose_at_the_leg_says_what_the_tree_must_be():
    """The fifth acceptance criterion, in both places the leg is described.

    Neither the docstring nor the comment states that the branch comparison
    covers every change to the harness source; both say what the leg requires
    of the working tree, and the docstring records what permitting an
    off-branch resume gives up.

    The control is the frozen text of this same function from when the
    deferral was unconditional, committed under `tests/history-fixtures/`: the
    same scan over it reports the claim, so an absence here is the prose having
    changed rather than the scan having stopped seeing anything.
    """
    docstring, comments = leg_prose(function_source(
        COORDINATOR_PATH.read_text(encoding="utf-8"),
        "unchanged_since_escalation"))

    assert BLANKET_CLAIM not in docstring
    assert BLANKET_CLAIM not in comments
    assert STANDING_CLAIM in docstring
    assert STANDING_CLAIM in comments
    assert "state.branch" in docstring
    assert "off-branch" in docstring

    before_docstring, before_comments = leg_prose(conftest.history_fixture(
        PROSE_THAT_CARRIED_THE_BLANKET_CLAIM))
    assert BLANKET_CLAIM in before_docstring + before_comments   # the control
    assert STANDING_CLAIM not in before_comments                # the control
