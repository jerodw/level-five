"""Independent validation for story-195: the resume guard asks about the tree
the harness is read from, and a harness fix on the base reaches the resumed
stage.

The subject is the third comparison of `unchanged_since_escalation` in the
shape a run has here since story-117: the harness and the target are one
repository, the run works in a worktree of its own, and the harness is read
from the tree the developer invoked it in — the base — for the whole run. That
worktree and the invoked tree are two working trees of one repository, so
`same_repository` answers False for them and the guard compares the recorded
revision of the harness root, exactly as it does for a harness checked out
beside its target. The escalation commits land in the worktree and move that
revision not at all, which is what makes the comparison sound: a developer who
fixes the harness on the base and re-runs has moved it and is admitted, and
one who re-runs with nothing changed has not and is refused, naming the
recorded revision.

Before this story the shared-tree leg carried a condition story-103 added —
that the run's tree resolve to `state.branch` — which no run could fail once
story-117 had `resolve_run_root` guarantee it, and prose saying the recorded
revision "cannot be reached for" under a shared root and that an off-branch
shared resume proceeds unrefused. Both are false under a worktree. The module
that validated that condition drove the guard by hand against a tree
`run_story` would never resolve; this one drives `run_story` and lets it
resolve the tree, so every question below is one a run can actually reach.

The fixtures are `tests/test_resume_guard.py`'s, imported rather than copied:
one repository carrying both a target project and a harness root materialized
from a workflow that module builds, with its run directory gitignored so the
developer's checkout stays clean while the run works elsewhere. The prompt
file the verifying stage is rendered from is the harness source a fix is made
to, and the fake runner records the prompt it was handed, so "the fix reached
the stage" is read off what the stage received rather than inferred.

Every absence asserted here carries a demonstration that the same check reports
the situation it exists to catch:

  * "the fixed prompt reached the resumed stage" sits beside the prompt the
    escalating run's verifier received, which does not carry the fix, and
    beside the same resume driven before the fix, which is refused;
  * "the unchanged resume is refused naming the recorded revision, not the
    shared checkout" sits beside one checkout standing on the story branch and
    handed to `run_story` as both roots, whose refusal does name the shared
    checkout — so the phrase is the leg's doing rather than a phrase the
    refusal never carries;
  * "the uncommitted fix is refused" sits beside the same fix committed, which
    is admitted;
  * "the recorded revision is what the worktree case compares" is shown by
    clearing the field in memory, which clears the guard here and does not
    clear it under one checkout standing on the branch;
  * "no function named `_checkout_story_branch` exists" is a scan whose
    control is the same source with the definition put back, which the same
    scan reports;
  * "the prose no longer carries the two false claims" is a scan whose control
    is today's text with each claim written back into it, which the same scan
    reports.

Nothing here invokes a model: every run goes through the fake agent runner
`tests/test_resume_guard.py` defines, and no baseline is resolved out of this
repository's commit graph.
"""
import ast
import json
from pathlib import Path

import conftest
from conftest import function_source

import story_coordinator

from test_resume_guard import (COORDINATOR_PATH, DEFAULT_BRANCH, FAIL, PASS,
                               QUIET_GITIGNORE, STORY_BRANCH, STORY_ID,
                               VERIFIER_STAGE, Runner, build_target, escalate,
                               git, guard, run_dir_in, write)

#: What a harness fix on the base says, appended to the verifying stage's
#: prompt template and looked for in the prompt the resumed stage receives.
FIXED_TEXT = "The harness was fixed on the base before this resume."

#: The evidence phrase each form of the third comparison carries. The
#: recorded-revision form names a revision; the shared-checkout form names
#: the checkout. Which one a refusal carries is how the two are told apart.
RECORDED_REVISION_PHRASE = "still at revision"
SHARED_CHECKOUT_PHRASE = "the same checkout as the target"

#: The helper story-117 left without a caller and story-195 deleted.
DEAD_HELPER = "_checkout_story_branch"

#: The two claims the guard's prose carried about a shared root and no longer
#: may: that the recorded revision could not serve as the comparison, and that
#: an off-branch resume of a shared checkout is let through unrefused. Each is
#: scanned for in the docstring and in the comments separately.
FALSE_CLAIMS = ("cannot be reached for", "the resume proceeds",
                "is no longer refused")

#: What both places must say instead: where the harness is read from, and
#: that a worktree of the same repository takes the recorded-revision form.
TRUE_CLAIMS = ("harness_root", "worktree", "recorded revision")


# --------------------------------------------------------------------------
# The repository, the run in its worktree, and the fix on the base
# --------------------------------------------------------------------------


class PromptRecordingRunner(Runner):
    """The fake runner, also keeping the prompt each stage was handed.

    Keyed by stage in the order the stages were invoked, so the prompt the
    escalating run's verifier received and the prompt the resumed verifier
    received are both readable afterwards and can be compared.
    """

    def __init__(self, target_root: Path, verdict: dict = FAIL, *,
                 edit: bool = True):
        super().__init__(target_root, verdict, edit=edit)
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, prompt, *, stage, **kwargs):
        self.prompts.append((stage, prompt))
        return super().__call__(prompt, stage=stage, **kwargs)

    def prompt_for(self, stage: str) -> str:
        """The last prompt the named stage received."""
        return [text for name, text in self.prompts if name == stage][-1]


def shared(tmp_path: Path, name: str = "shared") -> Path:
    """One repository that is both roots, standing on the base.

    Not on the story branch: `resolve_run_root` then cuts a worktree for the
    run, which is the shape this module is about. The run directory is
    ignored so the checkout the developer stands in is clean throughout.
    """
    return build_target(tmp_path / name, harness_inside=True,
                        gitignore=QUIET_GITIGNORE)


def one_checkout_on_the_branch(tmp_path: Path,
                               name: str = "one-checkout") -> Path:
    """The other shape, for the controls: one checkout already standing on the
    story branch, which `resolve_run_root` returns as the run's own tree."""
    return build_target(tmp_path / name, harness_inside=True,
                        on_the_story_branch=True)


def escalate_in_a_worktree(root: Path) -> PromptRecordingRunner:
    """Drive the shared repository to an escalation, run in a worktree.

    Asserts the premise every test below rests on: the run worked in a tree
    that is not the invoked one, the two are two working trees of one
    repository, the invoked tree still stands on the base, and the revision
    the escalation recorded for the harness is that tree's HEAD.
    """
    runner = PromptRecordingRunner(root, FAIL)
    assert story_coordinator.run_story(STORY_ID, root, root, runner) == 2
    tree = conftest.run_root_for(root, STORY_ID)
    assert tree != root
    assert story_coordinator.same_repository(tree, root) is False
    assert common_dir(tree) == common_dir(root)
    assert current_branch(root) == DEFAULT_BRANCH
    assert current_branch(tree) == STORY_BRANCH
    assert state_in(tree)["status"] == "escalated"
    assert state_in(tree)["harness_revision"] == revision(root)
    return runner


def resume(root: Path, verdict: dict = PASS) -> tuple[int, PromptRecordingRunner]:
    """The developer's next act: `l5-run` again, from the base."""
    runner = PromptRecordingRunner(root, verdict)
    code = story_coordinator.run_story(STORY_ID, root, root, runner)
    return code, runner


def prompt_path(root: Path) -> Path:
    """The verifying stage's template, which is the harness source fixed here.

    Named off the built workflow's own declaration rather than spelled out,
    so the file edited is the one the resumed stage is rendered from.
    """
    return root / "prompts" / VERIFIER_STAGE["prompt"]


def fix_the_prompt_on_the_base(root: Path, *, commit: bool = True) -> None:
    """Append the fix to the template in the invoked tree, standing on the
    base, and commit it there unless asked not to."""
    path = prompt_path(root)
    write(path, path.read_text(encoding="utf-8") + "\n" + FIXED_TEXT + "\n")
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "a fix to the harness, on the base")


def state_in(tree: Path) -> dict:
    return json.loads((run_dir_in(tree) / "state.json").read_text())


def state_bytes_in(tree: Path) -> str:
    return (run_dir_in(tree) / "state.json").read_text()


def revision(root: Path, name: str = "HEAD") -> str:
    return git(root, "rev-parse", name).stdout.strip()


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def common_dir(root: Path) -> Path:
    """The repository a working tree belongs to, as git names it."""
    return (root / git(root, "rev-parse", "--git-common-dir").stdout.strip()
            ).resolve()


# --------------------------------------------------------------------------
# The premise: the run works in a worktree of the shared repository
# --------------------------------------------------------------------------


def test_the_run_works_in_a_worktree_of_the_repository_the_harness_is_read_from(
    tmp_path,
):
    """The fixture's own shape, demonstrated before anything is asked of it.

    One repository, invoked from the base as both roots: the run stands on the
    story branch in a worktree, the invoked tree stays on the base, and the
    two are one repository and two working trees — which is the pair
    `same_repository` decides False for and the recorded-revision form is put
    to. The escalation's two commits are on the worktree's branch, and the
    revision it recorded for the harness is the base's HEAD, untouched.

    The control is one checkout already standing on the story branch, where
    the run's tree *is* the invoked tree, `same_repository` answers True, and
    the escalation commits do move past the recorded revision — the reason
    that shape has a leg of its own.
    """
    root = shared(tmp_path)
    base_before = revision(root)
    escalate_in_a_worktree(root)
    tree = conftest.run_root_for(root, STORY_ID)
    state = state_in(tree)

    assert revision(root) == base_before
    assert state["harness_revision"] == base_before
    assert state["escalation_commit"] == revision(tree, f"{STORY_BRANCH}~1")
    assert revision(tree) != base_before
    assert git(root, "status", "--porcelain").stdout.strip() == ""

    one = one_checkout_on_the_branch(tmp_path)
    escalate(one, one)
    assert conftest.run_root_for(one, STORY_ID) == one
    assert story_coordinator.same_repository(one, one) is True
    assert state_in(one)["harness_revision"] != revision(one)      # control


# --------------------------------------------------------------------------
# The resume this story exists to prove: a harness fix on the base gets through
# --------------------------------------------------------------------------


def test_a_prompt_fixed_on_the_base_reaches_the_resumed_stage(tmp_path):
    """The first acceptance criterion, driven as the developer meets it.

    A run escalates. The developer, standing on the base, fixes a prompt
    template and commits it there, then re-runs. The story artifact and the
    story branch are untouched, so the first two comparisons still establish
    sameness — and the resume reaches its stage anyway, because the harness
    root's revision has moved, which is exactly what the third comparison is
    about. The stage it reaches is the one the escalation recorded, and the
    prompt that stage receives carries the fix.

    Two controls: the same resume driven before the fix, which is refused and
    invokes nothing; and the prompt the escalating run's verifier received,
    which does not carry the fix — so its presence afterwards is the fix
    reaching the stage rather than the template always saying it.
    """
    root = shared(tmp_path)
    escalated = escalate_in_a_worktree(root)
    tree = conftest.run_root_for(root, STORY_ID)
    recorded_stage = state_in(tree)["current_stage"]
    assert recorded_stage == VERIFIER_STAGE["name"]
    assert FIXED_TEXT not in escalated.prompt_for(recorded_stage)   # control

    code, refused = resume(root)                                    # control
    assert code == 1
    assert refused.calls == []
    assert guard(root, root) != []

    fix_the_prompt_on_the_base(root)
    assert git(root, "status", "--porcelain").stdout.strip() == ""
    assert revision(root) != state_in(tree)["harness_revision"]
    assert guard(root, root) == []

    code, resumed = resume(root)
    assert code == 0
    assert resumed.calls[0] == recorded_stage
    assert FIXED_TEXT in resumed.prompt_for(recorded_stage)
    assert state_in(tree)["status"] == "completed"


def test_the_fix_commit_moved_only_the_harness_root(tmp_path):
    """What the fix changed and what it did not, so the admission above is
    the third comparison's doing and not the first or second's.

    The fix commit touches the template alone, on the base; the story branch
    still stands exactly one commit past the escalation commit and its tree is
    clean; the story artifact is byte for byte what the run read. The control
    is the story digest and escalation commit compared before and after the
    fix, which agree — and the harness revision, which does not.
    """
    root = shared(tmp_path)
    escalate_in_a_worktree(root)
    tree = conftest.run_root_for(root, STORY_ID)
    before = state_in(tree)
    tip_before = revision(tree, STORY_BRANCH)

    fix_the_prompt_on_the_base(root)

    changed = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                  "HEAD").stdout.split()
    assert changed == [prompt_path(root).relative_to(root).as_posix()]
    assert revision(tree, STORY_BRANCH) == tip_before
    assert revision(tree, f"{STORY_BRANCH}~1") == before["escalation_commit"]
    assert git(tree, "status", "--porcelain").stdout.strip() == ""
    assert story_coordinator.story_digest(
        (root / ".harness" / "stories" / f"{STORY_ID}.yaml").read_text()
    ) == before["story_digest"]
    assert revision(root) != before["harness_revision"]             # control


# --------------------------------------------------------------------------
# The resume this story keeps refusing: nothing changed
# --------------------------------------------------------------------------


def test_a_resume_with_nothing_changed_is_refused_naming_the_recorded_revision(
    tmp_path, capsys,
):
    """The second acceptance criterion: what the worktree case refuses, and
    how the refusal reads.

    Nothing touched anywhere, re-run from the base: exit 1, no agent invoked,
    the recorded state byte for byte what the escalation left, and three
    pieces of evidence in the message whose third names the recorded harness
    revision — the recorded-revision form — rather than the shared checkout.

    The control is one checkout standing on the story branch and handed to
    `run_story` as both roots, whose third line does name the shared checkout
    and the branch it stands on, so the phrase's absence here is the worktree
    case taking the other form rather than a phrase no refusal carries.
    """
    root = shared(tmp_path)
    escalate_in_a_worktree(root)
    tree = conftest.run_root_for(root, STORY_ID)
    state_before = state_bytes_in(tree)
    recorded = state_in(tree)["harness_revision"]
    capsys.readouterr()

    code, refused = resume(root)
    message = capsys.readouterr().err

    assert code == 1
    assert refused.calls == []
    assert refused.prompts == []
    assert state_bytes_in(tree) == state_before
    assert state_in(tree)["status"] == "escalated"

    evidence = guard(root, root)
    assert len(evidence) == 3
    for line in evidence:
        assert line in message
    harness_line = evidence[2]
    assert RECORDED_REVISION_PHRASE in harness_line
    assert recorded[:12] in harness_line
    assert SHARED_CHECKOUT_PHRASE not in harness_line

    one = one_checkout_on_the_branch(tmp_path)
    escalate(one, one)
    one_line = guard(one, one)[2]
    assert SHARED_CHECKOUT_PHRASE in one_line                       # control
    assert STORY_BRANCH in one_line
    assert RECORDED_REVISION_PHRASE not in one_line


def test_the_shared_checkout_on_its_branch_is_refused_as_before(
    tmp_path, capsys,
):
    """The constraint: a shared repository resumed from its own story-branch
    tree is refused exactly as it was, and the leg that refuses it now takes
    no condition on where the tree stands — `resolve_run_root` has already
    decided that.

    Driven through `run_story` rather than the guard: exit 1, no agent, the
    third line naming the shared checkout and the branch. The control is the
    same checkout with a commit on the branch, which is admitted — the branch
    comparison the shared leg defers to, doing the deferring.
    """
    one = one_checkout_on_the_branch(tmp_path)
    escalate(one, one)
    capsys.readouterr()

    code, refused = resume(one)
    message = capsys.readouterr().err

    assert code == 1
    assert refused.calls == []
    assert state_in(one)["status"] == "escalated"
    evidence = guard(one, one)
    assert len(evidence) == 3
    assert evidence[2] in message
    assert SHARED_CHECKOUT_PHRASE in evidence[2]
    assert STORY_BRANCH in evidence[2]

    write(one / "src" / "app.py", "print('by hand')\n")
    git(one, "add", "-A")
    git(one, "commit", "-q", "-m", "a developer's fix on the branch")
    assert guard(one, one) == []                                    # control
    code, admitted = resume(one)
    assert code == 0
    assert admitted.calls[0] == VERIFIER_STAGE["name"]


def test_the_fix_left_uncommitted_on_the_base_is_still_refused(tmp_path):
    """The third acceptance criterion, recording the guard's bias as it
    stands.

    The same fix, made on the base and not committed. The harness root's HEAD
    has not moved, so the recorded-revision comparison establishes sameness
    and the resume is refused — the dirty-tree pre-flight reads the run's own
    worktree, which is clean, so it is the guard refusing and not that
    pre-flight. What the resumed stage would have read is the fixed file; the
    guard reads commits, and this test records that rather than endorsing it.

    The control is the same fix committed, which is admitted.
    """
    root = shared(tmp_path)
    escalate_in_a_worktree(root)
    tree = conftest.run_root_for(root, STORY_ID)
    recorded = state_in(tree)["harness_revision"]

    fix_the_prompt_on_the_base(root, commit=False)
    assert git(root, "status", "--porcelain").stdout.strip() != ""
    assert git(tree, "status", "--porcelain").stdout.strip() == ""
    assert revision(root) == recorded
    assert FIXED_TEXT in prompt_path(root).read_text(encoding="utf-8")

    code, refused = resume(root)
    assert code == 1
    assert refused.calls == []
    assert state_in(tree)["status"] == "escalated"
    evidence = guard(root, root)
    assert len(evidence) == 3
    assert RECORDED_REVISION_PHRASE in evidence[2]

    git(root, "add", "-A")                                          # control
    git(root, "commit", "-q", "-m", "the same fix, committed")
    assert guard(root, root) == []
    code, admitted = resume(root)
    assert code == 0
    assert FIXED_TEXT in admitted.prompt_for(VERIFIER_STAGE["name"])


def test_the_recorded_revision_is_what_the_worktree_case_compares(tmp_path):
    """Which field the worktree case reads, one at a time.

    Clearing or changing the recorded harness revision in memory clears the
    guard here, because that field is the comparison; clearing the digest or
    the escalation commit clears it too, as in every form. The control is one
    checkout standing on the branch, where the same field is not read at all
    and changing it clears nothing — the difference between the two forms.
    """
    root = shared(tmp_path)
    escalate_in_a_worktree(root)
    assert len(guard(root, root)) == 3

    for field in ("story_digest", "escalation_commit", "harness_revision"):
        assert guard(root, root, changes={field: ""}) == [], field
    assert guard(root, root, changes={"harness_revision": "0" * 40}) == []
    assert guard(root, root) != []

    one = one_checkout_on_the_branch(tmp_path)
    escalate(one, one)
    assert guard(one, one, changes={"harness_revision": ""}) != []      # control
    assert guard(one, one, changes={"harness_revision": "0" * 40}) != []


# --------------------------------------------------------------------------
# What the story removed, and what the prose says now
# --------------------------------------------------------------------------


def top_level_functions(source: str) -> set[str]:
    return {node.name for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_dead_checkout_helper_is_gone():
    """No function of that name is defined in the coordinator.

    The control is the same source with the definition written back, which
    the same scan reports — so the absence is the deletion rather than a scan
    that has stopped seeing definitions.
    """
    source = COORDINATOR_PATH.read_text(encoding="utf-8")
    defined = top_level_functions(source)

    assert DEAD_HELPER not in defined
    assert "unchanged_since_escalation" in defined

    put_back = source + f"\n\ndef {DEAD_HELPER}(target_root, branch):\n    return []\n"
    assert DEAD_HELPER in top_level_functions(put_back)              # control


def leg_prose(source: str) -> tuple[str, str]:
    """One function's docstring and its comment lines, read apart, because
    the story asks both to say the same thing and a joined text would let
    either carry the other.

    Each is flattened to single spaces, so a phrase wrapped across two lines
    of prose is found as the phrase and not missed at the line break.
    """
    docstring = ast.get_docstring(ast.parse(source).body[0]) or ""
    comments = " ".join(line.lstrip().lstrip("#") for line in source.splitlines()
                        if line.lstrip().startswith("#"))
    return " ".join(docstring.split()), " ".join(comments.split())


def test_the_prose_at_the_leg_says_what_it_decides_now():
    """The fourth acceptance criterion, in both places the leg is described.

    Neither the docstring nor the comments says the recorded revision cannot
    be reached for, or that an off-branch shared resume proceeds; both say the
    harness is read from `harness_root` and that a worktree of the same
    repository falls to the recorded-revision comparison; the docstring still
    records the rejected tree hash and the words "which directories".

    The control is today's text with each false claim written back into the
    docstring and into a comment, which the same scan reports — so an absence
    here is the prose having changed rather than the scan seeing nothing.
    """
    source = function_source(COORDINATOR_PATH.read_text(encoding="utf-8"),
                             "unchanged_since_escalation")
    docstring, comments = leg_prose(source)

    for claim in FALSE_CLAIMS:
        assert claim not in docstring, claim
        assert claim not in comments, claim
    for claim in TRUE_CLAIMS:
        assert claim in docstring, claim
        assert claim in comments, claim
    assert "tree hash" in docstring
    assert "which directories" in docstring

    for claim in FALSE_CLAIMS:
        with_claim = source.replace(
            '    """Evidence that resuming',
            f'    """Evidence that resuming ({claim})', 1,
        ).replace(
            "    # The harness is read from",
            f"    # The harness is read from ({claim})\n    # The harness is read from",
            1,
        )
        assert with_claim != source
        put_back_docstring, put_back_comments = leg_prose(with_claim)
        assert claim in put_back_docstring                          # control
        assert claim in put_back_comments                           # control


def test_same_repository_says_it_decides_one_working_tree():
    """The helper's docstring says what it decides — one working tree, by
    toplevel — and that a linked worktree and its primary are not one tree,
    which the fixture above demonstrates in git.

    The control is the same scan for a phrase the docstring does not carry.
    """
    docstring = ast.get_docstring(ast.parse(function_source(
        COORDINATOR_PATH.read_text(encoding="utf-8"), "same_repository"
    )).body[0]) or ""

    assert "working tree" in docstring
    assert "toplevel" in docstring
    assert "worktree" in docstring
    assert "recorded-revision" in docstring
    assert "a phrase this docstring does not carry" not in docstring   # control


def test_no_module_keys_a_mutant_on_the_deleted_condition():
    """The condition story-103 added is gone from the source, and no module
    under `tests/` holds its text as a mutant anchor, so nothing reconstructs
    a guard that requires the run's tree to resolve to `state.branch`.

    The control is this module's own text, which carries the condition as
    the literal the scan looks for, and which the same scan reports — so the
    scan is reading the files it walks rather than matching nothing anywhere.
    """
    condition = "standing != _revision(target_root, state.branch)"
    retired = "test_the_harness_leg_checks_the_tree_it_will_run.py"
    tests_dir = Path(__file__).resolve().parent

    assert condition not in COORDINATOR_PATH.read_text(encoding="utf-8")
    assert not (tests_dir / retired).exists()

    carrying = [path.name for path in sorted(tests_dir.glob("test_*.py"))
                if condition in path.read_text(encoding="utf-8")]
    assert carrying == [Path(__file__).name]                        # control
