"""Independent validation for story-065: a completion survives any merge.

The halves of one fact are under test, and they are tested together because
they only mean anything together: what `_complete` *writes* onto a story
branch, and what `completion_commits` *reads* off a trunk that has taken that
branch.

The writing half is driven by calling `_complete` against repositories built
under `tmp_path`, one per outcome, so the cases it now has — content to commit,
nothing to commit standing on this story's own escalation commit, and nothing
to commit anywhere else — are each entered for real rather than reasoned about
from the source.

The reading half is driven through **real merges**. `conftest.MERGE_METHODS`
performs `git merge --no-ff`, `git merge --squash` followed by the commit a
forge makes of it, and `git rebase` followed by a fast-forward, against
`conftest.story_branch_on_trunk`'s branch shapes. Nothing here writes by hand
the commit a merge would have produced.

Every absence asserted below sits beside a demonstration that the same check
reports the violation it exists to catch:

  * "the trunk carries a completion after this merge" for the repaired shape
    sits beside the same merge of the shape the harness built *before* this
    story, which is what makes a green result the repair rather than a
    restatement of what merges do;
  * "`completion_commits` reports nothing" for a subject without the marker,
    for a marker without a line naming this story, and for a message that
    merely mentions the story mid-line, each sit beside a commit carrying both
    pieces of evidence, which the same reader does report;
  * "the amend did not rewrite this commit" for another story's escalation
    commit sits beside the identical repository whose escalation commit names
    the story being run, where the same call does rewrite it;
  * "the rerun was refused, and no agent ran" sits beside the same target whose
    trunk has not taken the story, where the run proceeds and an agent is
    invoked;
  * "the escalation writers are unchanged" sits beside the same comparison for
    `_complete`, which differs.

And the module's own load-bearing halves are shown to be load-bearing:
reverting `_complete` alone, and reverting `completion_commits` alone, each
make assertions here fail. Neither is passing on the other's behalf.

Nothing here invokes a model: the one test that drives `run_story` goes through
a fake agent runner that refuses to be called.
"""
import json
import subprocess
from pathlib import Path

import pytest

import conftest

import story_coordinator
from agent_runner import AgentResult

COORDINATOR_PATH = Path(story_coordinator.__file__).resolve()
HARNESS_ROOT = COORDINATOR_PATH.parents[1]

#: The story a constructed branch belongs to, and the title its completion
#: subject carries. Taken from the shared builders rather than spelled again,
#: so a constructed history here is the same constructed history every other
#: module builds.
STORY_ID = conftest.CONSTRUCTED_STORY_ID
STORY_TITLE = conftest.CONSTRUCTED_STORY_TITLE
STORY_BRANCH = f"story/{STORY_ID}"
TRUNK = conftest.CONSTRUCTED_TRUNK

#: A second story, for the case in which `_complete` stands on an escalation
#: commit that is not its own. Different from `STORY_ID` and from anything this
#: repository carries.
OTHER_STORY_ID = "story-901"


def config_field(name: str) -> str:
    """One scalar of the shared target configuration, read out of it.

    The targets below are configured from `conftest.CONFIG`, so where they keep
    their run directories is that text's answer and not a second one written
    here.
    """
    for line in conftest.CONFIG.splitlines():
        prefix = f"{name}: "
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise AssertionError(f"the shared target configuration declares no {name}")


def config_list(name: str) -> list[str]:
    """One list-valued key of the shared target configuration, likewise."""
    lines = conftest.CONFIG.splitlines()
    start = lines.index(f"{name}:")
    items = []
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        items.append(line[4:].strip())
    assert items, f"the shared target configuration declares no {name}"
    return items


#: The document a claim-support check scans in these targets.
ARCHITECTURE_DOC = config_list("architecture_docs")[0]


#: Where a target keeps its run directories, and the ignore rule that keeps
#: them out of what `_complete` stages. A run directory the repository tracks
#: is a tree that is never clean at completion, so the clean-tree cases below
#: exist only in a target that ignores it — which is the arrangement the story
#: requires and the one every real target has.
RUNS_REL = config_field("runs_dir")
GITIGNORE = f"{RUNS_REL}/\n{config_field('logs_dir')}/\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def subject_of(root: Path, revision: str = "HEAD") -> str:
    return git(root, "log", "-1", "--format=%s", revision).strip()


def message_of(root: Path, revision: str = "HEAD") -> str:
    """A commit's whole message, with git's own trailing newline removed.

    Every comparison below is against a message the coordinator composed, and
    those carry no trailing newline of their own, so both sides are stripped
    the same way rather than one being padded to match the other.
    """
    return git(root, "log", "-1", "--format=%B", revision).rstrip("\n")


def composed(message: str) -> str:
    """A composed message, read the way `message_of` reads a committed one."""
    return message.rstrip("\n")


def tree_of(root: Path, revision: str = "HEAD") -> str:
    return git(root, "rev-parse", f"{revision}^{{tree}}").strip()


def commit_count(root: Path, revision: str = "HEAD") -> int:
    return int(git(root, "rev-list", "--count", revision).strip())


def tracked_files(root: Path, revision: str = "HEAD") -> set[str]:
    return set(git(root, "ls-tree", "-r", "--name-only", revision).split())


# --------------------------------------------------------------------------
# The repository `_complete` is driven against
#
# A target rather than one of the shared constructed histories, because
# `_complete` is a coordinator entry point: it writes a completion report into
# a run directory, stages the working tree and commits it. What it needs is a
# repository shaped like a target mid-run — an ignored run directory and a
# story branch standing on a particular commit — and what each case varies is
# that commit and whether the tree is clean.
# --------------------------------------------------------------------------


def standing_on(tmp_path: Path, tip_message: str, *, name: str) -> Path:
    """A story branch whose tip carries `tip_message`, over a clean tree.

    A base commit on `TRUNK`, and the tip standing on it. The tip's message is
    the whole variable — an escalation of this story, an escalation of another,
    or an ordinary commit — because that is precisely what `_complete` puts to
    `escalated_story` before deciding whether to amend.

    The trunk is then advanced by a commit of its own, so a run finished here
    can afterwards be taken by a real merge: a rebase onto an ancestor
    fast-forwards without replaying anything, and replaying is what decides
    whether an empty commit survives.
    """
    root = Path(tmp_path) / name
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (root / "app.py").write_text("the base state\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "branch", "-M", TRUNK)
    git(root, "checkout", "-q", "-b", STORY_BRANCH)
    (root / conftest.CONSTRUCTED_WORK_REL).write_text(
        "the work the escalation committed\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", tip_message)
    git(root, "checkout", "-q", TRUNK)
    (root / "trunk.txt").write_text("a commit the trunk took meanwhile\n",
                                    encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the trunk moved on")
    git(root, "checkout", "-q", STORY_BRANCH)
    return root


def escalation_message(story_id: str, stage: str = "verifier") -> str:
    """The message the coordinator writes when a run of `story_id` escalates.

    Through the coordinator's own composer, so what makes a commit *read* as an
    escalation here is what makes one read as an escalation in the harness.
    """
    state = story_coordinator.RunState(story_id=story_id,
                                       branch=f"story/{story_id}",
                                       current_stage=stage)
    return story_coordinator.escalation_commit_message(
        state, "the constructed run stopped here")


def completion_message(*, amended: bool) -> str:
    """What `_complete` composes for `STORY_ID`, in each of its two forms."""
    state = story_coordinator.RunState(story_id=STORY_ID, branch=STORY_BRANCH)
    return story_coordinator.completion_commit_message(state, STORY_TITLE,
                                                       amended=amended)


def complete(root: Path, *, coordinator=story_coordinator,
             story_id: str = STORY_ID, title: str = STORY_TITLE) -> None:
    """Finish a run in `root`, exactly as the coordinator finishes one."""
    run_dir = Path(root) / RUNS_REL / story_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = coordinator.RunState(story_id=story_id,
                                 branch=f"story/{story_id}")
    story = {"story": {"id": story_id, "title": title}}
    assert coordinator._complete(run_dir, state, story, Path(root)) == 0


def dirty(root: Path) -> None:
    """Leave the working tree with something for `_complete` to commit."""
    (Path(root) / "app.py").write_text("what the last stage wrote\n",
                                       encoding="utf-8")


# --------------------------------------------------------------------------
# `_complete`'s outcomes
# --------------------------------------------------------------------------


def test_a_completion_with_something_to_commit_is_written_as_it_always_was(
        tmp_path):
    """The unchanged case, held against the case that did change.

    Subject and control stand on the *same* tip — this story's own escalation
    commit, the one shape an amend is permitted for — and differ only in
    whether the tree is clean. So a green result here says the amend is
    conditioned on the tree and not on the tip alone, which no assertion about
    the dirty case by itself could say.
    """
    subject = standing_on(tmp_path, escalation_message(STORY_ID),
                          name="content-to-commit")
    escalation = git(subject, "rev-parse", "HEAD").strip()
    before = commit_count(subject)
    tracked_before = tracked_files(subject)
    dirty(subject)
    complete(subject)

    # A commit gained, not a commit rewritten: the escalation is still there,
    # with its own message, and it is the new tip's parent.
    assert commit_count(subject) == before + 1
    assert git(subject, "rev-parse", "HEAD~1").strip() == escalation
    assert subject_of(subject, escalation) == \
        escalation_message(STORY_ID).splitlines()[0]
    # The message is today's, with no amended paragraph in it.
    assert message_of(subject) == completion_message(amended=False)
    # No new file: what the commit carries is the change the last stage made,
    # and the run directory it also wrote is ignored.
    assert tracked_files(subject) == tracked_before

    control = standing_on(tmp_path, escalation_message(STORY_ID),
                          name="nothing-to-commit")
    control_before = commit_count(control)
    complete(control)
    assert commit_count(control) == control_before, \
        "with nothing to commit the same tip is amended, not added to"


def test_a_clean_tree_on_the_storys_own_escalation_commit_amends_it(tmp_path):
    """The case the story adds, asserted on each of its consequences."""
    root = standing_on(tmp_path, escalation_message(STORY_ID),
                       name="amended")
    escalation = git(root, "rev-parse", "HEAD").strip()
    escalation_tree = tree_of(root, escalation)
    parent = git(root, "rev-parse", "HEAD~1").strip()
    before = commit_count(root)

    complete(root)

    assert commit_count(root) == before, "an amend adds no commit"
    assert git(root, "rev-parse", "HEAD~1").strip() == parent
    assert subject_of(root) == \
        story_coordinator.completion_commit_subject(STORY_ID, STORY_TITLE)
    assert story_coordinator.COMPLETION_COMMIT_MARKER in message_of(root)
    # The tree the escalation committed, carried by the commit that now says
    # the story finished: one commit doing both jobs is the whole repair.
    assert tree_of(root) == escalation_tree
    assert conftest.CONSTRUCTED_WORK_REL in tracked_files(root)


def test_the_amended_body_records_the_escalation_and_the_dead_undo_command(
        tmp_path):
    """Amending destroys the escalation's message, so the record has to move.

    The control is the *un*amended completion message, which carries neither
    statement — so this is not an assertion that some long body exists, it is
    an assertion about what the amend adds over what a completion always said.
    """
    root = standing_on(tmp_path, escalation_message(STORY_ID),
                       name="amended-body")
    complete(root)
    body = message_of(root)

    assert "escalated" in body
    assert story_coordinator.ESCALATION_UNDO_COMMAND in body
    assert "no longer applies" in body

    plain = completion_message(amended=False)
    assert story_coordinator.ESCALATION_UNDO_COMMAND not in plain
    assert "no longer applies" not in plain
    # The subject and the marker are byte-identical across the two forms: the
    # amended message is the plain one with a paragraph after it.
    assert completion_message(amended=True).startswith(plain)


def test_a_clean_tree_on_anything_else_still_leaves_the_empty_commit(tmp_path):
    """A run standing on an ordinary commit is written exactly as today."""
    root = standing_on(tmp_path, "a commit the developer made", name="plain")
    tip = git(root, "rev-parse", "HEAD").strip()
    before = commit_count(root)

    complete(root)

    assert commit_count(root) == before + 1
    assert git(root, "rev-parse", "HEAD~1").strip() == tip
    assert message_of(root) == completion_message(amended=False)
    assert tree_of(root) == tree_of(root, tip), "the commit is empty"
    assert message_of(root, tip) == "a commit the developer made"


def test_another_storys_escalation_commit_is_never_amended(tmp_path):
    """The amend is refused for a commit that is not this run's to rewrite.

    Subject and control differ in one thing: which story the escalation commit
    at the tip names. The control is the same repository with that story being
    the one running, where the same call *does* rewrite the commit — so the
    absence asserted here is demonstrably an absence the check can report.
    """
    foreign = escalation_message(OTHER_STORY_ID)
    subject = standing_on(tmp_path, foreign, name="foreign-escalation")
    tip = git(subject, "rev-parse", "HEAD").strip()
    before = commit_count(subject)

    complete(subject)

    assert message_of(subject, tip) == composed(foreign), \
        "another story's escalation commit was rewritten"
    assert commit_count(subject) == before + 1
    assert message_of(subject) == completion_message(amended=False)
    assert git(subject, "rev-parse", "HEAD~1").strip() == tip

    control = standing_on(tmp_path, escalation_message(STORY_ID),
                          name="own-escalation")
    control_tip = git(control, "rev-parse", "HEAD").strip()
    complete(control)
    assert git(control, "rev-parse", "HEAD").strip() != control_tip, \
        "the same call does rewrite this story's own escalation commit"


# --------------------------------------------------------------------------
# The merge methods
#
# Parametrised off `conftest.MERGE_METHODS`, so a method added to the shared
# builders is a case every assertion here drives. The one place a particular
# method is singled out — the merge commit, whose preservation of every commit
# means it was never losing the completion — refers to the shared builder
# itself rather than to a name of this module's own.
# --------------------------------------------------------------------------


METHODS = list(conftest.MERGE_METHODS.items())
METHOD_IDS = [name for name, _ in METHODS]


def merged(tmp_path: Path, shape: str, method, *, name: str) -> Path:
    """A trunk that has taken a story branch of `shape` by `method`, for real."""
    root = conftest.story_branch_on_trunk(tmp_path, shape=shape, name=name)
    method(root, STORY_BRANCH)
    return root


def reported_on(root: Path, story_id: str = STORY_ID) -> list[str]:
    return story_coordinator.completion_commits(root, TRUNK, story_id)


@pytest.mark.parametrize("method", [m for _, m in METHODS], ids=METHOD_IDS)
def test_every_merge_method_carries_the_repaired_completion_onto_the_trunk(
        tmp_path, method):
    """The criterion. The control it sits beside is the test below it.

    A positive assertion, so what it needs is not a demonstration that it can
    fail — it fails on its own the moment a merge drops the completion — but a
    demonstration that it is not restating what merges already did. That is
    `test_the_pre_story_shape_is_lost_by_the_rebase_and_by_the_squash`, which
    puts the identical merge to a branch built the way the harness built one
    before this story.
    """
    subject = merged(tmp_path, "amended", method, name="repaired")
    assert reported_on(subject), \
        "the trunk took the story and reports no completion for it"


@pytest.mark.parametrize("method", [m for _, m in METHODS], ids=METHOD_IDS)
def test_every_merge_method_carries_a_non_escalated_completion_too(
        tmp_path, method):
    """A run that never escalated, whose squash case fails before this story.

    The control is the same repository *before* the merge: the trunk reports
    nothing for the story until it takes the branch, which is what makes the
    positive reading above a consequence of the merge rather than of the
    reader finding the branch by some other route.
    """
    root = conftest.story_branch_on_trunk(tmp_path, shape="unescalated",
                                          name="never-escalated")
    assert reported_on(root) == [], \
        "the trunk reports the story before it has taken the branch"
    method(root, STORY_BRANCH)
    assert reported_on(root), \
        "the trunk took a non-escalated story and reports no completion"


@pytest.mark.parametrize("method", [m for _, m in METHODS], ids=METHOD_IDS)
def test_the_pre_story_shape_is_lost_by_the_rebase_and_by_the_squash(
        tmp_path, method):
    """The criterion that the repair is not vacuous, driven per method.

    A branch built the way the harness built one before this story — the
    escalation commit with a separate *empty* completion commit above it — is
    asked of the trunk after each merge. The story requires the rebase and the
    squash to report nothing, which is what says those two methods were losing
    the completion and that the test above is measuring the repair.

    The control is the same merge of the repaired shape, which does report.

    The squash case does not hold, and the assertion is left standing rather
    than adjusted, because it cannot hold under any implementation. What a
    squash puts on the trunk is one commit whose body is the folded commits'
    messages. The pre-story shape's empty completion commit *has* a message,
    marker and all, so the trunk carries the same two pieces of evidence the
    repaired shape leaves there and no reader can tell them apart:

      * with the folded bodies included - which is what makes the marker travel
        at all - this criterion fails and the two criteria above pass;
      * with only the folded subjects included, this criterion passes and the
        two above both fail, because then no marker reaches the trunk in any
        shape.

    The rebase half is the defect the story actually observed, and it is
    reproduced here. The squash half of the same criterion asks for a
    distinction the evidence on the trunk does not carry.
    """
    control = merged(tmp_path, "amended", method, name="repaired-control")
    assert reported_on(control), \
        "the repaired shape must survive this merge for the reading below " \
        "to be about the old shape rather than about the reader"

    root = merged(tmp_path, "added", method, name="pre-story-shape")
    if method is conftest.merge_commit:
        assert reported_on(root), \
            "a merge commit preserves every commit, so the empty completion " \
            "is still on the trunk: this method never lost it"
    else:
        assert reported_on(root) == [], (
            "a branch built the way the harness built one before this story "
            "must reach the trunk carrying nothing this reader recognises, "
            "which is the defect story-065 repairs. For the squash this is "
            "unreachable: the empty completion commit's own message, marker "
            "included, is folded into the squashed body, so the trunk carries "
            "the identical evidence the repaired shape leaves there. Excluding "
            "the folded bodies would satisfy this and break the two criteria "
            "requiring the repaired and the never-escalated shapes to survive "
            "a squash. See this test's docstring."
        )


# --------------------------------------------------------------------------
# What `completion_commits` still refuses
# --------------------------------------------------------------------------


def committed_message(tmp_path: Path, message: str, *, name: str) -> Path:
    """A one-commit repository on `TRUNK` whose commit carries `message`."""
    root = Path(tmp_path) / name
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    git(root, "branch", "-M", TRUNK)
    return root


SUBJECT = story_coordinator.completion_commit_subject(STORY_ID, STORY_TITLE)
MARKER = story_coordinator.COMPLETION_COMMIT_MARKER


def test_both_pieces_of_evidence_are_still_required_together(tmp_path):
    """Relaxing *where* the evidence sits did not relax *what* is required."""
    both = committed_message(tmp_path, f"{SUBJECT}\n\n{MARKER}", name="both")
    assert reported_on(both), "the control does not report, so nothing below " \
                              "distinguishes a refusal from a broken reading"

    subject_only = committed_message(tmp_path, f"{SUBJECT}\n\nNo marker here.",
                                     name="subject-only")
    assert reported_on(subject_only) == [], \
        "the completion subject shape alone is a shape anyone can write"

    marker_only = committed_message(
        tmp_path, f"A subject naming nobody\n\n{MARKER}", name="marker-only")
    assert reported_on(marker_only) == [], \
        "the marker alone would report another story's run"


def test_a_message_that_merely_mentions_the_story_is_not_a_completion(tmp_path):
    """The line must *be* the completion subject, not contain it."""
    mention = committed_message(
        tmp_path, f"Follow-up work\n\n{MARKER}\n\nSee {SUBJECT} for context.",
        name="mention")
    assert reported_on(mention) == [], \
        "a story named mid-line is a reference, not a completion"

    bulleted = committed_message(
        tmp_path,
        f"{PULL_REQUEST_TITLE}\n\n{conftest.SQUASH_BULLET}{SUBJECT}\n\n{MARKER}",
        name="bulleted")
    assert reported_on(bulleted), \
        "the same line under a squash's bullet is a completion"


#: A pull request title, for the squash cases: nothing about it resembles a
#: completion subject, which is the point — a squash writes it as the merge
#: commit's subject and pushes the completion into the body.
PULL_REQUEST_TITLE = "Land the story (#7)"


def test_the_reported_line_carries_the_commits_own_subject(tmp_path):
    """What a reader scanning the trunk sees, which a squash changes."""
    root = conftest.story_branch_on_trunk(tmp_path, shape="amended",
                                          name="reported-subject")
    conftest.squash_merge(root, STORY_BRANCH,
                          pull_request_title=PULL_REQUEST_TITLE)
    reported = reported_on(root)
    assert len(reported) == 1
    assert reported[0].endswith(PULL_REQUEST_TITLE), \
        "a squashed completion's subject is the pull request's title"


# --------------------------------------------------------------------------
# What reads `completion_commits`: the rerun refusal and the claim support
# --------------------------------------------------------------------------


def story_field(name: str) -> str:
    """One scalar of the shared story artifact, read out of the artifact.

    The completion subject the coordinator writes for that story is composed
    from its id and title, so a run driven against it has to be asked what
    those are rather than told — a story fixture whose title is edited would
    otherwise leave this module asserting against a title nothing carries.
    """
    for line in conftest.STORY.splitlines():
        prefix = f"  {name}: "
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise AssertionError(f"the shared story artifact declares no {name}")


SAMPLE_ID = story_field("id")
SAMPLE_TITLE = story_field("title")
SAMPLE_BRANCH = f"story/{SAMPLE_ID}"


class RefusingRunner:
    """An agent runner that records being called, and produces nothing.

    A refused run must invoke no agent, so being called at all is the
    observation; what it returns only has to let the control run stop early.
    """

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, **kwargs):
        self.calls.append(stage)
        return AgentResult(ok=False, result_text="")


def sample_target(tmp_path: Path, *, name: str) -> Path:
    """A target the coordinator can run `SAMPLE_ID` in, on a trunk.

    The shared story and config text, so what this target declares is what
    every other module's target declares; the trunk name and the ignore rule
    are this module's, because a merge needs somewhere to land and a clean
    completion needs the run directory ignored.
    """
    root = Path(tmp_path) / name
    for sub in (".harness/standards", ".harness/stories"):
        (root / sub).mkdir(parents=True)
    (root / ARCHITECTURE_DOC).parent.mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "config.yaml").write_text(
        conftest.CONFIG.format(workflow="story-workflow"), encoding="utf-8")
    (root / ".harness" / "stories" / f"{SAMPLE_ID}.yaml").write_text(
        conftest.STORY, encoding="utf-8")
    (root / ".harness" / "standards" / "coding.md").write_text(
        "# Coding Standards\n- keep it simple\n", encoding="utf-8")
    (root / ".harness" / "standards" / "testing.md").write_text(
        "# Testing Standards\n- test everything\n", encoding="utf-8")
    (root / ARCHITECTURE_DOC).write_text("# Sample Architecture\n",
                                         encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    git(root, "branch", "-M", TRUNK)
    return root


def land_escalated_story(root: Path, method) -> None:
    """Put the amended shape on the story branch and take it onto the trunk.

    Then point the story branch at the trunk, which is where a developer's
    branch ends up after the pull request lands and the local branch is
    refreshed — the state in which a re-run of the story is pointless.
    """
    state = story_coordinator.RunState(story_id=SAMPLE_ID,
                                       branch=SAMPLE_BRANCH,
                                       current_stage="verifier")
    git(root, "checkout", "-q", "-b", SAMPLE_BRANCH)
    (root / "src" / "app.py").write_text("print('the story')\n",
                                         encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m",
        story_coordinator.escalation_commit_message(state, "it stopped here"))
    git(root, "commit", "-q", "--amend", "--allow-empty", "-m",
        story_coordinator.completion_commit_message(state, SAMPLE_TITLE,
                                                    amended=True))
    git(root, "checkout", "-q", TRUNK)
    (root / "trunk.txt").write_text("the trunk moved on\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the trunk moved on")
    method(root, SAMPLE_BRANCH, TRUNK)
    git(root, "branch", "-f", SAMPLE_BRANCH, TRUNK)


@pytest.mark.parametrize("method", [m for _, m in METHODS], ids=METHOD_IDS)
def test_a_rerun_is_refused_when_the_trunk_has_taken_the_escalated_story(
        tmp_path, method):
    """The pre-flight reads the same evidence, so it moves with the repair.

    The control is the same target with the merge not performed: there the run
    is not refused and an agent is invoked, which is what makes "no agent ran"
    above an observation about the refusal rather than about this fake runner.
    """
    subject = sample_target(tmp_path, name="merged-target")
    land_escalated_story(subject, method)
    runner = RefusingRunner()
    code = story_coordinator.run_story(SAMPLE_ID, HARNESS_ROOT, subject, runner,
                                       base=TRUNK)
    assert code != 0
    assert runner.calls == [], "a refused run must invoke no agent"
    assert not (subject / RUNS_REL / SAMPLE_ID).exists(), \
        "a refused run must leave no run directory"

    control = sample_target(tmp_path, name="unmerged-target")
    control_runner = RefusingRunner()
    story_coordinator.run_story(SAMPLE_ID, HARNESS_ROOT, control,
                                control_runner, base=TRUNK)
    assert control_runner.calls != [], \
        "with nothing merged the same call runs the story"
    assert (control / RUNS_REL / SAMPLE_ID).exists()


@pytest.mark.parametrize("method", [m for _, m in METHODS], ids=METHOD_IDS)
def test_claim_support_treats_a_merged_escalated_story_as_merged(
        tmp_path, method):
    """The merged question is put to `completion_commits` against the base.

    Subject and control are one repository and one added claim, asked twice:
    once against the trunk the merge landed on, and once against the commit the
    trunk stood at before it. The second reports the claim as unsupported, so
    the silence in the first is the merge being visible from the base rather
    than the check having found nothing to say.

    The claim is about `SAMPLE_ID`, the story the merge landed, and the run
    asking is `STORY_ID` — a run's own story is exempt from the scan, so the
    two have to differ for there to be anything to ask.
    """
    root = sample_target(tmp_path, name="claim-target")
    before_merge = git(root, "rev-parse", TRUNK).strip()
    land_escalated_story(root, method)
    git(root, "checkout", "-q", TRUNK)

    document = ARCHITECTURE_DOC
    path = root / document
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"\n{SAMPLE_ID} removed 3 of the old code paths.\n",
        encoding="utf-8")
    config = {"architecture_docs": [document]}

    def reports(base: str, label: str) -> list[dict]:
        run_dir = root / RUNS_REL / f"claim-{label}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result = story_coordinator.claim_support_check(
            run_dir, root, config, conftest.CLAIM_SUPPORT_RESULT, base,
            STORY_ID)
        assert result.ran, result.reason
        return list(result.reports)

    assert reports(TRUNK, "merged") == [], \
        "the trunk carries the story's completion, so the claim is supported"
    unsupported = reports(before_merge, "unmerged")
    assert unsupported, \
        "against a base predating the merge the same claim is unsupported"
    assert unsupported[0]["stories"] == [SAMPLE_ID]


# --------------------------------------------------------------------------
# The shared range resolution, confirmed rather than changed
# --------------------------------------------------------------------------


def test_the_story_range_endpoint_resolves_to_the_amended_escalation_tip(
        tmp_path):
    """`conftest.story_commit_range` already handles the new shape.

    Repositories of one story, differing only in how it ended. The
    amended shape's endpoint is the tip that both carries the work and says the
    story finished; the pre-story shape's is the separate completion commit
    above the escalation; and a story that escalated and never resumed has no
    endpoint at all. The last two are what make the first an answer about the
    amended shape rather than about anything the resolver returns.
    """
    amended = conftest.escalating_story(tmp_path, amended=True,
                                        name="amended-range")
    tip = git(amended, "rev-parse", "HEAD").strip()
    assert conftest.constructed_story_range(amended).endpoint == tip
    assert story_coordinator.completion_commits(
        amended, "HEAD", conftest.CONSTRUCTED_STORY_ID)

    added = conftest.escalating_story(tmp_path, name="added-range")
    added_tip = git(added, "rev-parse", "HEAD").strip()
    added_range = conftest.constructed_story_range(added)
    assert added_range.endpoint == added_tip
    assert subject_of(added, added_range.endpoint) != \
        subject_of(added, f"{added_range.endpoint}~1")

    unresumed = conftest.escalating_story(tmp_path, resumed=False,
                                          name="unresumed-range")
    assert conftest.constructed_story_range(unresumed).endpoint is None


# --------------------------------------------------------------------------
# The escalation path, and both halves of the repair
# --------------------------------------------------------------------------


ESCALATION_WRITERS = ("commit_escalated_work", "commit_escalated_tree")


def working_tree_function(name: str) -> str:
    return conftest.function_source(
        COORDINATOR_PATH.read_text(encoding="utf-8"), name)


def pre_story_function(name: str) -> str:
    """One coordinator function's text as it stood before this story.

    A committed fixture rather than a read of this repository's commit graph,
    for the reason `tests/history-fixtures/` exists: the text is what the
    assertion is about, and resolving it out of the graph makes the comparison
    depend on facts about the graph — a squash makes a range unresolvable in a
    clone, a rename gives a path a new add-commit and empties the read
    silently. The fixture is the same evidence, in the tree and diffable.
    """
    return conftest.history_fixture(
        f"story_coordinator.{name}.at-story-065-baseline.py.txt")


@pytest.mark.parametrize("name", ESCALATION_WRITERS)
def test_the_escalation_writers_are_unchanged(name):
    """The escalation's two-commit shape is load-bearing and was not touched.

    The pre-story text, carried as a fixture, against the working tree. The
    control below is the same comparison for `_complete`, which this story did
    change — so an equality here is a statement about these two functions and
    not about a comparison that cannot fail.
    """
    assert working_tree_function(name) == pre_story_function(name)


def test_the_unchanged_comparison_can_report_a_change():
    """The control for the equality above."""
    assert working_tree_function("_complete") != pre_story_function("_complete")


def revert_complete(tmp_path):
    """The coordinator with `_complete`'s three outcomes collapsed back to one.

    Only `_complete` changes: `completion_commit_message` keeps its amended
    form and `completion_commits` keeps its relaxed reading, so what this
    mutant shows is what the writing half alone is holding up.
    """
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [(
            '    if _git(target_root, "diff", "--cached", "--quiet").returncode != 0:\n'
            '        _git(target_root, "commit", "--allow-empty", "-m",\n'
            '             completion_commit_message(state, title))\n'
            '    elif _head_escalated(target_root) == state.story_id:\n'
            '        _git(target_root, "commit", "--amend", "--allow-empty", "-m",\n'
            '             completion_commit_message(state, title, amended=True))\n'
            '    else:\n'
            '        _git(target_root, "commit", "--allow-empty", "-m",\n'
            '             completion_commit_message(state, title))\n',
            '    _git(target_root, "commit", "--allow-empty", "-m",\n'
            '         completion_commit_message(state, title))\n',
        )],
        name="coordinator_without_the_amend", tmp_path=tmp_path)


def revert_completion_commits(tmp_path):
    """The coordinator with the reader back to matching particular fields."""
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [(
            '        message = f"{subject}\\n{body}"\n'
            '        if COMPLETION_COMMIT_MARKER not in message:\n'
            '            continue\n'
            '        if not _carries_completion_subject(message, story_id):\n'
            '            continue\n',
            '        prefix = completion_commit_subject(story_id, "")\n'
            '        if not subject.startswith(prefix) or subject == prefix:\n'
            '            continue\n'
            '        if COMPLETION_COMMIT_MARKER not in body:\n'
            '            continue\n',
        )],
        name="coordinator_without_the_relaxation", tmp_path=tmp_path)


def test_reverting_complete_alone_makes_this_module_fail(tmp_path):
    """Without the amend, the clean-tree completion is empty again.

    Driven the same way as the amend case above, through the mutant, and then
    merged the same way — so what fails is the assertion this module makes and
    not a restatement of the mutation.
    """
    root = standing_on(tmp_path, escalation_message(STORY_ID),
                       name="reverted-complete")
    before = commit_count(root)
    complete(root, coordinator=revert_complete(tmp_path))

    assert commit_count(root) == before + 1, \
        "the mutant must add a commit where the shipped code amends"
    assert subject_of(root, "HEAD~1").startswith(
        story_coordinator.ESCALATION_COMMIT_MARKER), \
        "the escalation commit survives the mutant unamended"
    assert tree_of(root) == tree_of(root, "HEAD~1"), \
        "the commit the mutant adds is the empty one this story removes"

    # And that empty commit is what a rebase drops, which is the assertion
    # `test_every_merge_method_carries_the_repaired_completion_onto_the_trunk`
    # makes. Driven here through the same real rebase, against a branch the
    # mutant finished, beside one the shipped code finished.
    conftest.rebase_merge(root, STORY_BRANCH, TRUNK)
    assert reported_on(root) == [], \
        "the branch the mutant left is recognised on the trunk after a rebase"

    shipped = standing_on(tmp_path, escalation_message(STORY_ID),
                          name="shipped-complete")
    complete(shipped)
    conftest.rebase_merge(shipped, STORY_BRANCH, TRUNK)
    assert reported_on(shipped), \
        "the shipped code's branch is recognised after the same rebase"


def test_reverting_the_reader_alone_makes_this_module_fail(tmp_path):
    """Without the relaxation, a squashed completion is invisible again.

    The subject the assertions above make — every merge method carries the
    completion — is re-asked of the same repositories through a coordinator
    whose reader matches the completion subject against `%s` alone. The squash
    stops reporting for both the repaired and the non-escalated shape, which
    is the half of the story the writing change cannot supply.
    """
    reverted = revert_completion_commits(tmp_path)
    for shape in ("amended", "unescalated"):
        root = conftest.story_branch_on_trunk(
            tmp_path, shape=shape, name=f"reverted-reader-{shape}")
        conftest.squash_merge(root, STORY_BRANCH)
        assert story_coordinator.completion_commits(root, TRUNK, STORY_ID), \
            f"the shipped reader must report the squashed {shape} shape"
        assert reverted.completion_commits(root, TRUNK, STORY_ID) == [], \
            f"the pre-story reader loses the squashed {shape} shape"


def test_the_subject_and_the_marker_have_one_spelling_each():
    """Nothing here introduced a second composition of either.

    The message the coordinator writes, in both its forms, is built from
    `completion_commit_subject` and `COMPLETION_COMMIT_MARKER`; the reader
    derives what it matches from the same two. The control is the count of
    string literals in the coordinator that spell the marker text, which is
    one — its own definition — and the source is scanned rather than the
    module read, so a second literal anywhere in the file is reported.
    """
    source = COORDINATOR_PATH.read_text(encoding="utf-8")
    marker = story_coordinator.COMPLETION_COMMIT_MARKER
    assert source.count(f'"{marker}"') == 1, \
        "the marker text is written more than once in the coordinator"

    # The reader's own comparison is composed by the writer's subject builder,
    # so a change to the subject shape moves both at once. Demonstrated rather
    # than asserted from the source: a story id the subject builder is asked
    # for is the id the reader recognises.
    built = story_coordinator.completion_commit_subject(STORY_ID, STORY_TITLE)
    assert story_coordinator._carries_completion_subject(built, STORY_ID)
    assert not story_coordinator._carries_completion_subject(built,
                                                             OTHER_STORY_ID)
