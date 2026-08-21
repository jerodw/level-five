"""Independent validation for story-056: a story's range ends where the story
ended, not where it escalated.

`conftest.story_commit_range` finds where a story *begins* by the oldest commit
that added the file identifying it. For a story whose validation file was
already in the tree when it escalated, that oldest add is the escalation
commit — so the range used to end there and exclude everything the story did
after it resumed, silently, because comparing less is never an error.

The subject is split in two, and the two halves are kept apart deliberately:

  * **the rule**, which is a property of the resolver and is asserted against
    repositories these tests build. Every shape the rule is about — escalate
    and resume, escalate and never resume, revert and restore, a hotfix that
    modifies rather than adds — is constructed under `tmp_path` from the commit
    subjects and bodies the coordinator itself writes, so no assertion here
    moves when this repository is committed to, rebased or squashed.

  * **the live case**, which genuinely is a claim about *this* repository's
    history: that `tests/test_validation_module_naming.py` was added by
    story-038's escalation commit and now resolves to the commit that finished
    story-038. That claim cannot be made against a constructed repository,
    because it is about which commits this repository carries. It is made by
    subject and marker throughout — no assertion here names a sha, which is
    itself asserted below, with a control.

Every absence asserted here carries a demonstration that it can fail:

  * "the live endpoint is not an escalation" is paired with the same predicate
    applied to the commit the resolution used to return, which it *does*
    report — so a predicate that recognised nothing could not produce both;
  * "the resumed story's range reaches the work the completion did" is paired
    with the same repository resolved through `pre_story_range`, a mutant of
    today's `tests/conftest.py` with the endpoint advance taken back out, whose
    range is bounded at the escalation and whose diff over that work is empty;
  * "an unresumed story's endpoint is None" is paired with the resumed build of
    the same shape, whose endpoint is a commit;
  * "the revert-and-restore hazard is still defended" is paired with the newest
    add commit the restore introduced, which is a commit the resolution could
    have returned and did not;
  * "the hotfix commit is not what the resolution returns" is paired with the
    evidence that it touched the validation file at all;
  * "no marker text is spelled a second time in `tests/conftest.py`" is paired
    with a copy of that source with the marker spelled into it, which the same
    scan reports;
  * "this module names no sha of this repository" is paired with the same scan
    over a text carrying a sha this repository really does carry.

Nothing here invokes a model, and every repository built is a local directory
under `tmp_path`.
"""
import re
import subprocess
from pathlib import Path

import pytest

import conftest
from conftest import (CONSTRUCTED_STORY_ID, CONSTRUCTED_STORY_TITLE,
                      CONSTRUCTED_VALIDATION_REL, ENDPOINT, constructed_story,
                      constructed_story_diff, constructed_story_range,
                      declared_origins, deleted_and_restored, escalating_story,
                      load_mutant, modified_later, repository_file_at,
                      story_commit_range)

import story_coordinator
from test_baseline_honesty import DECLARED_HISTORY_READERS

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
CONFTEST_REL = "tests/conftest.py"
COORDINATOR_REL = "orchestration/story_coordinator.py"

#: The live case this story names: the one module under `tests/` whose own path
#: was added by an escalation commit rather than by the commit that finished the
#: story. Named as a module, not as a sha.
LIVE_ESCALATED_MODULE = "test_validation_module_naming.py"

#: The story that module validates, and the story whose completion commit is
#: now the endpoint. Recovered from the escalation commit's own subject below
#: and compared against this, so the assertion is about *which* story the
#: history says escalated rather than about a number written here.
LIVE_ESCALATED_STORY = "story-038"

#: The control module. It escalated at the implementer on its first attempt,
#: before any tester had written its file, so the escalation commit did not add
#: the file and the oldest add is the story's own completion commit. Nothing
#: about its resolution changes, which is what shows the rule is about whether
#: the file existed when the story escalated and not about whether the story
#: resumed.
LIVE_UNAFFECTED_MODULE = "test_retry_routing.py"

#: The path a constructed story's completion commit rewrites, and the path a
#: range bounded at the escalation cannot see. A directory, so `_sample_under`
#: puts a file beneath it.
WORK_AFTER_RESUMING = "src/"


# --------------------------------------------------------------------------
# Reading a commit the way the coordinator writes it
#
# Every question below is asked of a commit's subject and body, never of its
# position in a log and never of its sha. The recognition itself is
# `story_coordinator`'s, imported rather than restated, so a change to what the
# coordinator writes moves what these read.
# --------------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


def subject_of(repo: Path, commit: str) -> str:
    return git(repo, "log", "-1", "--format=%s", commit).strip()


def body_of(repo: Path, commit: str) -> str:
    return git(repo, "log", "-1", "--format=%b", commit)


def escalated_story_at(repo: Path, commit: str) -> str | None:
    """The story an escalation commit names, or None if it is not one."""
    return story_coordinator.escalated_story(subject_of(repo, commit))


def completes(repo: Path, commit: str, story_id: str) -> bool:
    """Whether `commit` is the commit that finished `story_id`.

    The two pieces of evidence `completion_commits` requires together: a
    subject of the completion shape for *this* story, and a body carrying the
    completion marker. Asked of one commit rather than of a branch, because
    what is under test here is which commit a resolution returned.
    """
    prefix = story_coordinator.completion_commit_subject(story_id, "")
    subject = subject_of(repo, commit)
    return (subject.startswith(prefix) and subject != prefix
            and story_coordinator.COMPLETION_COMMIT_MARKER
            in body_of(repo, commit))


def add_commits(repo: Path, relative: str) -> list[str]:
    """Every commit that added `relative`, newest first."""
    return git(repo, "log", "--diff-filter=A", "--format=%H", "--",
               relative).split()


def parent_of(repo: Path, commit: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{commit}^").strip()


def origin_of(module: str) -> str:
    """The path whose add-commit identifies `module`'s story.

    The declaration when the module makes one, and the module's own path
    otherwise — the same answer the resolution reaches, asked through the
    public reader rather than by reaching into the resolver.
    """
    declared = declared_origins(TESTS_DIR / module)
    return declared[0] if declared else f"tests/{module}"


def commit(repo: Path, message: str) -> str:
    """One more commit on a repository a test built."""
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def completion_message(story_id: str, title: str) -> str:
    """What the coordinator writes when a run of `story_id` finishes.

    Composed through the coordinator's own writer, so a commit a test makes
    carries the subject and body a real completion carries.
    """
    return story_coordinator.completion_commit_message(
        story_coordinator.RunState(story_id=story_id,
                                   branch=f"story/{story_id}"), title)


def escalation_commits(repo: Path) -> list[str]:
    """Every escalation commit in `repo`, oldest first, by what it says it is."""
    return [commit for commit in reversed(git(repo, "log", "--format=%H").split())
            if escalated_story_at(repo, commit) is not None]


# --------------------------------------------------------------------------
# The resolver with this story's change taken back out
#
# The endpoint advance is what these tests are about, and widening a range is
# exactly the change that can make an assertion pass for a reason that has
# nothing to do with it. So each constructed assertion about the widened range
# is run again through the resolver as it was, which is today's
# `tests/conftest.py` with the advance removed and nothing else touched.
# --------------------------------------------------------------------------


#: The advance, and what stood there before it: the endpoint was the oldest add
#: commit whatever that commit turned out to be.
ADVANCE = """    subject = _git(repo, "log", "-1", "--format=%s", run_commit).stdout.strip()
    escalated = story_coordinator.escalated_story(subject)
    if escalated is None:
        return StoryRange(baseline=baseline, endpoint=run_commit)
    return StoryRange(baseline=baseline,
                      endpoint=_completion_commit_after(repo, run_commit,
                                                        escalated))"""

WITHOUT_THE_ADVANCE = "    return StoryRange(baseline=baseline, endpoint=run_commit)"


@pytest.fixture(scope="module")
def pre_story_conftest(tmp_path_factory):
    """`tests/conftest.py` as it resolved a range before this story."""
    return load_mutant(REPO_ROOT / CONFTEST_REL,
                       [(ADVANCE, WITHOUT_THE_ADVANCE)],
                       name="pre_story_range_resolution",
                       tmp_path=tmp_path_factory.mktemp("pre-story"))


def test_the_pre_story_resolver_is_todays_without_the_advance(
        pre_story_conftest, tmp_path):
    """The control is the resolver as it was, and it really is a resolver.

    An anchor that no longer occurs makes `load_mutant` fail as itself, so the
    substitution is known to have applied — asserted here by the advance being
    present in the working tree's source. What this adds is that the mutant
    still resolves an ordinary range: a mutant that had merely broken would
    make every comparison below pass for the wrong reason.
    """
    assert ADVANCE in (REPO_ROOT / CONFTEST_REL).read_text(encoding="utf-8")

    root = constructed_story(tmp_path, name="unescalated")
    was = pre_story_conftest.story_commit_range(
        root / CONSTRUCTED_VALIDATION_REL, root)
    assert was.endpoint == add_commits(root, CONSTRUCTED_VALIDATION_REL)[-1]


# --------------------------------------------------------------------------
# 1. The rule, against repositories these tests build
# --------------------------------------------------------------------------


def test_a_story_that_escalated_with_its_file_written_ends_at_its_completion(
        tmp_path, pre_story_conftest):
    """The shape this story exists for, and the range it used to get.

    The validation file is added by the escalation commit, and the work the
    story exists for is made by the commit that completed it. So the resolved
    endpoint must be the completion — asserted by subject and marker — and the
    baseline must still be the parent of the escalation, because where a story
    begins is unchanged.

    The control is the same repository through the pre-story resolver, which
    ends the range at the escalation and whose diff over the work the
    completion did is empty. That is the silence this story removed.
    """
    root = escalating_story(tmp_path, violated=(WORK_AFTER_RESUMING,))
    escalation = escalation_commits(root)[0]

    resolved = constructed_story_range(root)
    assert completes(root, resolved.endpoint, CONSTRUCTED_STORY_ID)
    assert escalated_story_at(root, resolved.endpoint) is None
    assert resolved.baseline == parent_of(root, escalation)
    assert constructed_story_diff(root, [WORK_AFTER_RESUMING]) != ""

    was = pre_story_conftest.story_commit_range(
        root / CONSTRUCTED_VALIDATION_REL, root)
    assert was.endpoint == escalation
    assert was.baseline == resolved.baseline, (
        "the baseline is what it was; only the endpoint moved")
    assert pre_story_conftest.story_diff(
        [WORK_AFTER_RESUMING], validation_file=root / CONSTRUCTED_VALIDATION_REL,
        repo=root) == "", (
        "bounded at the escalation, the story's own work is invisible")


def test_the_endpoint_advances_past_every_escalation_the_story_made(tmp_path):
    """Two escalations before the resume, and still the completion.

    The advance is not "one commit forward": it is the commit that recorded
    itself as having finished this story. A story that escalated twice has two
    commits between its first add and its completion, and neither is an
    endpoint. The control is that both escalations really are in the history
    and really do name this story.
    """
    root = escalating_story(tmp_path,
                            escalated_at=("implementer", "verifier"),
                            violated=(WORK_AFTER_RESUMING,))
    escalations = escalation_commits(root)
    assert [escalated_story_at(root, commit) for commit in escalations] \
        == [CONSTRUCTED_STORY_ID, CONSTRUCTED_STORY_ID]

    resolved = constructed_story_range(root)
    assert completes(root, resolved.endpoint, CONSTRUCTED_STORY_ID)
    assert resolved.endpoint not in escalations
    assert resolved.baseline == parent_of(root, escalations[0])
    assert constructed_story_diff(root, [WORK_AFTER_RESUMING]) != ""


def test_a_story_that_escalated_and_has_not_resumed_has_no_endpoint(tmp_path):
    """In flight, so the end of the range is the working tree.

    There is no completion commit to advance to, and the answer is None rather
    than the escalation: the case an uncommitted story already resolves to. The
    control is the same builder with `resumed`, whose endpoint is a commit — so
    None is a statement about this history rather than about the builder.
    """
    in_flight = escalating_story(tmp_path, resumed=False, name="in-flight")
    assert constructed_story_range(in_flight).endpoint is None
    assert not constructed_story_range(in_flight).committed

    resumed = escalating_story(tmp_path, resumed=True, name="resumed")
    assert constructed_story_range(resumed).endpoint is not None


def test_a_caller_reading_at_an_in_flight_endpoint_reads_the_working_tree(
        tmp_path):
    """None is a bound callers already handle, not a new failure.

    `repository_file_at` at the endpoint of a story with no commit yet reads
    the working tree, which is the whole reason the endpoint is None rather
    than a raise. The control is the file's committed text differing from what
    the working tree holds, so reading the working tree is observably what
    happened rather than a coincidence.
    """
    root = escalating_story(tmp_path, resumed=False)
    uncommitted = CONSTRUCTED_VALIDATION_REL
    (root / uncommitted).write_text("# only in the working tree\n",
                                    encoding="utf-8")

    assert repository_file_at(uncommitted,
                              validation_file=root / CONSTRUCTED_VALIDATION_REL,
                              bound=ENDPOINT, repo=root) \
        == "# only in the working tree\n"
    assert "# only in the working tree" not in git(root, "show",
                                                   f"HEAD:{uncommitted}")


def test_a_story_committed_without_escalating_is_untouched_by_the_advance(
        tmp_path, pre_story_conftest):
    """The ordinary shape: one run commit, which is both add and completion.

    Its oldest add is not an escalation, so the advance never runs and the
    range is what it always was — asserted against the pre-story resolver
    rather than against a recollection of it.
    """
    root = constructed_story(tmp_path, violated=(WORK_AFTER_RESUMING,))
    resolved = constructed_story_range(root)
    was = pre_story_conftest.story_commit_range(
        root / CONSTRUCTED_VALIDATION_REL, root)

    assert escalated_story_at(root, resolved.endpoint) is None
    assert (resolved.baseline, resolved.endpoint) == (was.baseline, was.endpoint)


# --------------------------------------------------------------------------
# 2. The two hazards the oldest-add rule was written against, still defended
# --------------------------------------------------------------------------


def test_a_revert_and_restore_after_the_story_does_not_move_the_range(tmp_path):
    """The first hazard, unchanged by this story.

    The restoring commit adds the validation file a second time, so a
    resolution that took the *newest* add would bound the range at it and see
    nothing the story did. The control is that second add commit: it exists,
    the resolution could have returned it, and it did not.
    """
    root = constructed_story(tmp_path, violated=(WORK_AFTER_RESUMING,))
    before = constructed_story_range(root)
    deleted_and_restored(root)
    after = constructed_story_range(root)

    adds = add_commits(root, CONSTRUCTED_VALIDATION_REL)
    assert len(adds) == 2, "the restore is a second add, or this proves nothing"
    assert adds[0] != after.endpoint
    assert (after.baseline, after.endpoint) == (before.baseline, before.endpoint)
    assert constructed_story_diff(root, [WORK_AFTER_RESUMING]) != ""


def test_a_hotfix_that_modifies_the_file_is_not_what_the_resolution_returns(
        tmp_path):
    """The second hazard, unchanged by this story.

    A planning or hotfix commit touches the validation file without adding it,
    so it is a candidate for neither end. The control is that it did touch the
    file — a commit the resolution never saw would make the absence below
    true for the wrong reason.
    """
    root = constructed_story(tmp_path, violated=(WORK_AFTER_RESUMING,))
    before = constructed_story_range(root)
    modified_later(root)
    hotfix = git(root, "rev-parse", "HEAD").strip()
    after = constructed_story_range(root)

    touched = git(root, "log", "--format=%H", "--",
                  CONSTRUCTED_VALIDATION_REL).split()
    assert hotfix in touched, "the hotfix touched the file, or this proves nothing"
    assert hotfix not in add_commits(root, CONSTRUCTED_VALIDATION_REL)
    assert hotfix not in (after.baseline, after.endpoint)
    assert (after.baseline, after.endpoint) == (before.baseline, before.endpoint)


def test_a_hotfix_after_an_escalated_story_does_not_become_its_endpoint(
        tmp_path):
    """The two hazards together with the shape this story added.

    The advance searches forward from the escalation for a commit that recorded
    itself as finishing the story. A later commit that merely touches the file
    is not one, so it cannot be reached by the advance any more than by the
    oldest-add rule.
    """
    root = escalating_story(tmp_path, violated=(WORK_AFTER_RESUMING,))
    resolved = constructed_story_range(root)
    modified_later(root)
    hotfix = git(root, "rev-parse", "HEAD").strip()
    after = constructed_story_range(root)

    assert completes(root, after.endpoint, CONSTRUCTED_STORY_ID)
    assert after.endpoint != hotfix
    assert (after.baseline, after.endpoint) == (resolved.baseline,
                                                resolved.endpoint)


def test_a_later_storys_completion_is_not_this_storys_endpoint(tmp_path):
    """The advance stops at *this* story's completion, not at any completion.

    A second story completing afterwards carries the completion marker too, and
    a search that matched on the marker alone would run past the first story's
    end into it. The control is that the later completion is recognisable as a
    completion — of the other story — so the reason it is rejected is the story
    it names.
    """
    root = escalating_story(tmp_path, violated=(WORK_AFTER_RESUMING,))
    later = "story-901"
    (root / "unrelated.txt").write_text("the next story's change\n",
                                        encoding="utf-8")
    commit(root, completion_message(later, "A later story"))
    latest = git(root, "rev-parse", "HEAD").strip()

    assert completes(root, latest, later)
    resolved = constructed_story_range(root)
    assert resolved.endpoint != latest
    assert completes(root, resolved.endpoint, CONSTRUCTED_STORY_ID)


# --------------------------------------------------------------------------
# 3. The live case: what this repository's own history says
# --------------------------------------------------------------------------


def live_range(module: str) -> conftest.StoryRange:
    return story_commit_range(TESTS_DIR / module)


def test_the_live_escalated_module_ends_at_the_commit_that_finished_its_story():
    """`tests/test_validation_module_naming.py`, the case this story names.

    Its own path was added by story-038's escalation commit, which is read off
    that commit's subject rather than assumed, and the story it names is
    compared against the story this repository records it as. The endpoint is
    then the commit that finished *that* story, by subject and by marker.
    """
    relative = origin_of(LIVE_ESCALATED_MODULE)
    oldest_add = add_commits(REPO_ROOT, relative)[-1]
    story = escalated_story_at(REPO_ROOT, oldest_add)
    assert story == LIVE_ESCALATED_STORY, (
        "the module's own path is added by that story's escalation commit")

    resolved = live_range(LIVE_ESCALATED_MODULE)
    assert completes(REPO_ROOT, resolved.endpoint, story)
    assert resolved.baseline == parent_of(REPO_ROOT, oldest_add)


def test_the_live_escalated_modules_endpoint_is_no_longer_an_escalation():
    """The absence, with the commit that used to be returned beside it.

    `escalated_story` reporting None of the endpoint says nothing on its own —
    it says the same thing about a subject it cannot parse and about a commit
    that does not exist. Applied to the oldest add, which is the commit the
    resolution returned before this story, it reports an escalation. One
    predicate, two commits, two answers.
    """
    relative = origin_of(LIVE_ESCALATED_MODULE)
    oldest_add = add_commits(REPO_ROOT, relative)[-1]
    endpoint = live_range(LIVE_ESCALATED_MODULE).endpoint

    assert escalated_story_at(REPO_ROOT, oldest_add) is not None
    assert escalated_story_at(REPO_ROOT, endpoint) is None
    assert endpoint != oldest_add


def test_the_live_control_module_resolves_exactly_as_it_did():
    """`tests/test_retry_routing.py`: escalated, resumed, unchanged here.

    Its story escalated at the implementer before any tester had written its
    validation file, so the escalation commit did not add the file and the
    oldest add is the story's own completion commit. The advance therefore
    never runs, and the endpoint is the oldest add — which is what shows the
    rule is about whether the file existed when the story escalated rather
    than about whether the story resumed.
    """
    relative = origin_of(LIVE_UNAFFECTED_MODULE)
    oldest_add = add_commits(REPO_ROOT, relative)[-1]
    assert escalated_story_at(REPO_ROOT, oldest_add) is None

    resolved = live_range(LIVE_UNAFFECTED_MODULE)
    assert resolved.endpoint == oldest_add
    assert resolved.baseline == parent_of(REPO_ROOT, oldest_add)


def committed_history_readers() -> list[str]:
    """Every declared history reader whose own story has been committed.

    A module whose story is still in flight has no add-commit and so no
    endpoint — the case the resolution answers with None and the caller reads
    the working tree for. While *this* story runs, this module is that case,
    and once it commits it joins the rest. Any *other* module answering None
    would be a resolution that had stopped resolving, so it is refused here
    rather than quietly skipped, which is what stops the loops below emptying
    themselves into green.
    """
    committed = []
    for module in DECLARED_HISTORY_READERS:
        if live_range(module).committed:
            committed.append(module)
        else:
            assert module == Path(__file__).name, (
                f"{module} resolves to no endpoint, and it is not the module "
                f"whose own story is running")
    return committed


def test_no_declared_history_reader_ends_at_an_escalation_commit():
    """Every module that resolves this repository's history, checked at once.

    The list is `test_baseline_honesty.DECLARED_HISTORY_READERS`, imported
    rather than restated, so a module that joins it is checked here without
    this module being edited. The control is the live escalated module's own
    oldest add, on which the same predicate reports an escalation.
    """
    modules = committed_history_readers()
    assert modules, "an empty list would assert nothing about any endpoint"
    for module in modules:
        endpoint = live_range(module).endpoint
        assert escalated_story_at(REPO_ROOT, endpoint) is None, module

    assert escalated_story_at(
        REPO_ROOT,
        add_commits(REPO_ROOT, origin_of(LIVE_ESCALATED_MODULE))[-1]) is not None


def test_every_live_baseline_is_still_the_parent_of_the_oldest_add():
    """Where a story begins did not move, which is the constraint this story took.

    Asserted for every module whose endpoint this story could have affected —
    the declared history readers and the control — against the rule the
    baseline has always followed, recomputed here rather than recalled.
    """
    for module in (*committed_history_readers(), LIVE_UNAFFECTED_MODULE):
        oldest_add = add_commits(REPO_ROOT, origin_of(module))[-1]
        assert live_range(module).baseline == parent_of(REPO_ROOT,
                                                        oldest_add), module


# --------------------------------------------------------------------------
# 4. One spelling of the subject, and one spelling of the markers
# --------------------------------------------------------------------------


def test_the_escalation_subject_is_written_and_read_through_one_template():
    """The reader is derived from the composition, not written beside it.

    A round trip through both halves for several stories and stages, and the
    message the coordinator writes leading with exactly the subject the
    composition returns — so what `escalated_story` reads is what an escalation
    commit carries rather than a pattern that happens to agree with it today.
    """
    for story_id, stage in (("story-038", "verifier"),
                            ("story-056", "implementer"),
                            (CONSTRUCTED_STORY_ID, "documenter")):
        subject = story_coordinator.escalation_commit_subject(story_id, stage)
        assert story_coordinator.escalated_story(subject) == story_id

        state = story_coordinator.RunState(story_id=story_id,
                                           branch=f"story/{story_id}",
                                           current_stage=stage)
        message = story_coordinator.escalation_commit_message(state, "why")
        assert message.splitlines()[0] == subject


def test_a_subject_that_is_not_an_escalation_is_not_read_as_one():
    """The reader's negative side, which is what the resolution branches on.

    A completion subject, a planning commit's subject and ordinary prose are
    each not an escalation. Without this the advance would fire on commits it
    was never about, and every endpoint would move.
    """
    completion = story_coordinator.completion_commit_subject(
        CONSTRUCTED_STORY_ID, CONSTRUCTED_STORY_TITLE)
    for subject in (completion,
                    f"Plan {CONSTRUCTED_STORY_ID}: something",
                    "restore the module",
                    ""):
        assert story_coordinator.escalated_story(subject) is None


def test_changing_the_subjects_shape_moves_the_writer_and_the_reader_together(
        tmp_path):
    """The pairing, demonstrated rather than asserted of the source.

    A mutant coordinator whose subject template says something else still
    round-trips: the reader recognises what that coordinator writes, and stops
    recognising what today's writes. Two spellings of the shape could not do
    both.
    """
    mutant = load_mutant(
        REPO_ROOT / COORDINATOR_REL,
        [("{story_id}} stopped at {{stage}}",
          "{story_id}} halted within {{stage}}")],
        name="restyled_escalation_subject", tmp_path=tmp_path)

    restyled = mutant.escalation_commit_subject(CONSTRUCTED_STORY_ID, "verifier")
    assert restyled != story_coordinator.escalation_commit_subject(
        CONSTRUCTED_STORY_ID, "verifier")
    assert mutant.escalated_story(restyled) == CONSTRUCTED_STORY_ID
    assert mutant.escalated_story(
        story_coordinator.escalation_commit_subject(
            CONSTRUCTED_STORY_ID, "verifier")) is None


def restated_markers(source: str) -> list[str]:
    """Every marker text spelled literally in `source`.

    A scan rather than an `in` per marker, so the control below can be run
    through the identical code path on a source that does restate one.
    """
    return [marker for marker in (story_coordinator.ESCALATION_COMMIT_MARKER,
                                  story_coordinator.COMPLETION_COMMIT_MARKER)
            if marker in source]


def test_no_marker_text_is_spelled_a_second_time_in_the_shared_helpers():
    """`tests/conftest.py` imports the recognition rather than restating it.

    The control is the same scan over that source with a marker written into
    it, which reports it — so the empty result above is about the file rather
    than about a scan that cannot see anything. Beside it, the evidence that
    conftest reaches the coordinator's recognition at all: a file that restated
    nothing because it recognised nothing would also pass the scan.
    """
    source = (REPO_ROOT / CONFTEST_REL).read_text(encoding="utf-8")
    assert restated_markers(source) == []
    assert restated_markers(
        source + f"\nMARKER = {story_coordinator.COMPLETION_COMMIT_MARKER!r}\n") \
        == [story_coordinator.COMPLETION_COMMIT_MARKER]

    assert "story_coordinator.completion_commits(" in source
    assert "story_coordinator.escalated_story(" in source


def test_no_completion_subject_shape_is_spelled_a_second_time_in_the_helpers(
        tmp_path):
    """The subject's shape is the coordinator's too, and is not rebuilt here.

    `completion_commit_subject` is what composes "<story-id>: <title>". The
    helpers compose their commit messages by calling the coordinator's writers,
    which is asserted by reading the commits a constructed history carries: its
    escalation leads with the subject the coordinator composes and its
    completion leads with the completion subject and carries the marker.
    """
    root = escalating_story(tmp_path)
    escalation = escalation_commits(root)[0]
    assert subject_of(root, escalation) == \
        story_coordinator.escalation_commit_subject(CONSTRUCTED_STORY_ID,
                                                    "verifier")

    endpoint = constructed_story_range(root).endpoint
    prefix = story_coordinator.completion_commit_subject(CONSTRUCTED_STORY_ID,
                                                         "")
    assert subject_of(root, endpoint).startswith(prefix)
    assert CONSTRUCTED_STORY_TITLE in subject_of(root, endpoint)
    assert story_coordinator.COMPLETION_COMMIT_MARKER in body_of(root, endpoint)


# --------------------------------------------------------------------------
# 5. Nothing here is pinned to a sha
# --------------------------------------------------------------------------


#: Anything that could be an abbreviated or full object name. Whether one
#: *is* a commit of this repository is then asked of git rather than decided by
#: the pattern, so an ordinary word made of these letters is not a false report.
HEX_TOKEN = re.compile(r"\b[0-9a-f]{7,40}\b")


def pinned_shas(source: str, repo: Path = REPO_ROOT) -> list[str]:
    """Every token in `source` that names a commit of `repo`."""
    found = []
    for token in dict.fromkeys(HEX_TOKEN.findall(source)):
        named = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
             f"{token}^{{commit}}"], capture_output=True, text=True)
        if named.returncode == 0:
            found.append(token)
    return found


def test_this_module_names_no_commit_of_this_repositorys_history():
    """A rebase or a squash merge must not falsify anything asserted here.

    The control is the same scan over a text carrying a sha this repository
    really does hold — resolved here and never written down — which the scan
    reports. So the empty result above is about this module's text rather than
    about a scan that finds nothing anywhere.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert pinned_shas(source) == []

    real = git(REPO_ROOT, "rev-parse", "HEAD").strip()
    assert pinned_shas(f"the commit is {real}") == [real]
