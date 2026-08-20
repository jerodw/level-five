"""story-015: a test that cannot fail must not count as validation.

The story ships three things and this file validates each of them
independently of how they were implemented:

1. One shared baseline resolution in `tests/conftest.py` — a story's own run
   commit resolved as the commit that *added* that story's validation file,
   and the baseline as that commit's parent. Exercised here against synthetic
   histories in which the story is already committed, which is the state this
   repository cannot be in while these tests decide whether it commits.
2. Four repaired assertions. The guarantee under test is not that they pass —
   they passed before, vacuously — but that they *can fail*. Each repaired
   helper is called for real, with its repository redirected at a synthetic
   history whose run commit violates the very path the assertion names.
3. `tests/test_baseline_honesty.py`, the mechanical check. Its regression set
   is recovered here independently, from git history and from the archive,
   and fed back through its own scanner.

Every absence asserted below carries a control that constructs the violation
and shows the same check reporting it. The five instances this story exists
because of were absences that could not fail.
"""
import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import test_baseline_honesty as check
import test_stage_output_ownership as story007
import test_planner_injection as story008
import test_attempt_archiving as story010

#: story-038 merged story-008's and story-009's validation into one module
#: named for the subject they share, so the two origins now resolve to the
#: same module. The alias is kept so every assertion below still names the
#: story it is about.
story009 = story008
from conftest import (BASELINE, NothingToCompareAgainst, repository_file_at,
                      story_commit_range, story_diff)

import context_assembler
import harness_config
import schema_validator
import story_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: Keyed by the path each repaired file had *at story-015's baseline*, which
#: is where `pre_repair_source` reads it from and the only name it has there.
#: story-038 renamed all four; `CURRENT` says where each lives now, so a read
#: of the working tree and a read of history each use the name the file has
#: at that end of the range.
REPAIRED = {
    "tests/test_story_007_validation.py": story007,
    "tests/test_story_008_validation.py": story008,
    "tests/test_story_009_validation.py": story009,
    "tests/test_story_010_validation.py": story010,
}

CURRENT = {
    "tests/test_story_007_validation.py": "tests/test_stage_output_ownership.py",
    "tests/test_story_008_validation.py": "tests/test_planner_injection.py",
    "tests/test_story_009_validation.py": "tests/test_planner_injection.py",
    "tests/test_story_010_validation.py": "tests/test_attempt_archiving.py",
}

#: The test names a later story had to change, mapped from the spelling the
#: origin shipped to the one the module carries now. Two are story-038's
#: merge, because story-008 and story-009 each shipped a test of that name and
#: the merged module can hold only one of each. Two are story-045's, which
#: moved the documenter ahead of the verifier: an attempt-1 archive now holds
#: eight artifacts rather than six, and the documenter's report is no longer
#: the artifact a failed attempt did not write, so each of those two cases was
#: renamed for what it now checks rather than left describing what it used to.
#: Every one of them survives under its new spelling, and the name-set
#: comparisons below map through this rather than dropping either side.
MERGE_RENAMES = {
    "tests/test_story_009_validation.py": {
        "test_the_rendered_prompt_has_no_leftover_placeholder":
            "test_the_rendered_prompt_with_workflow_facts_has_no_leftover_placeholder",
        "test_the_coverage_comes_from_the_injection_and_not_from_leftover_prose":
            "test_the_workflow_fact_coverage_comes_from_the_injection_not_leftover_prose",
    },
    "tests/test_story_010_validation.py": {
        "test_attempt_1_archive_holds_the_six_artifacts_under_canonical_names":
            "test_attempt_1_archive_holds_every_stage_artifact_under_canonical_names",
        "test_an_artifact_the_attempt_did_not_write_is_skipped":
            "test_the_documenters_artifacts_are_archived_with_the_attempt",
    },
}


def origins_of(current_rel: str) -> list[str]:
    """Every baseline-era file the module at `current_rel` was built from."""
    return [rel for rel, current in CURRENT.items() if current == current_rel]


def expected_test_names(current_rel: str) -> set[str]:
    """The test names a repaired module must still carry.

    The union across its origins, each mapped through the merge renames, so
    a merged module is held to *both* origins' full sets rather than to
    either one — which a subset comparison would let through.
    """
    names: set[str] = set()
    for rel in origins_of(current_rel):
        renames = MERGE_RENAMES.get(rel, {})
        names |= {renames.get(name, name)
                  for name in _test_names(pre_repair_source(rel))}
    return names

ARCHIVED_INSTANCE = (REPO_ROOT / ".harness" / "runs-archive"
                     / "story-013-vacuous-tests"
                     / "pre-reset-test_story_013_validation.py")

#: Every subject a repaired assertion names, with the module that asserts it.
#: Read off the repaired files rather than assumed; `test_every_repaired_subject_
#: is_covered_here` holds this list to what the modules actually assert.
SUBJECTS = [
    ("tests/test_story_007_validation.py", ".harness/stories/"),
    ("tests/test_story_008_validation.py", "scripts/l5-assist"),
    ("tests/test_story_008_validation.py", "schemas/story.schema.json"),
    ("tests/test_story_008_validation.py", ".harness/stories"),
    ("tests/test_story_009_validation.py", "workflows/"),
    ("tests/test_story_009_validation.py", "rules/"),
    ("tests/test_story_009_validation.py", "schemas/"),
    ("tests/test_story_010_validation.py", "orchestration/context_assembler.py"),
    ("tests/test_story_010_validation.py", "workflows/"),
    ("tests/test_story_010_validation.py", "schemas/"),
    ("tests/test_story_010_validation.py", "rules/"),
    ("tests/test_story_010_validation.py", "prompts/"),
]


# --------------------------------------------------------------------------
# Synthetic histories
# --------------------------------------------------------------------------


#: The loaded workflow build_context has taken as a required argument
#: since story-028, which injects the workflow's own facts — its stages,
#: its create restrictions, its retry routes — into every stage prompt.
#:
#: story-048 built it here rather than resolving what this repository deploys.
#: The assertions below are about the *guidance text* the shipped tester and
#: verifier templates carry, and the workflow is an input the assembler
#: requires in order to render one of them at all — any workflow declaring a
#: writing stage and a judging one renders the same guidance, so deriving it
#: from the deployed definition made the number of stages this repository
#: happens to ship into something these assertions would move on.
WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES,),
        changed_files=conftest.CHANGED_FILES),
    conftest.workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        retry_routing={"implementation-defect": {
            "stage": conftest.StageRef(0),
            "when": "the behaviour the story asked for is missing"}}),
    name="shared-baseline-resolution-workflow",
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t",
        "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD").strip()


def committed_story(tmp_path: Path, validation_rel: str, guarded: str, *,
                    violate: str | None = None, name: str = "synthetic") -> Path:
    """A repository in which one story has already run and committed.

    Commit 1 is the pre-story state and carries the guarded path. Commit 2 is
    the story's own run commit: it adds the validation file and, when
    `violate` says so, touches the guarded path in the same commit. That is
    the shape of a finished branch, and the shape under which `git diff HEAD`
    reports nothing no matter what the story did.

    The building itself moved to `conftest.constructed_story` when story-053
    converted every history-reading module in the suite: the shape this module
    established is now what a dozen of them build, and one home for it is the
    same argument the shared resolution below is built on. This wrapper keeps
    the calling convention every assertion here already uses.
    """
    return conftest.constructed_story(
        tmp_path,
        respected=() if violate else (guarded,),
        violated=(guarded,) if violate else (),
        violation=violate or "modify",
        validation_rel=validation_rel, name=name)


def repaired_story(tmp_path: Path, guarded: str, *, violate: str | None = None,
                   name: str = "synthetic") -> Path:
    """A synthetic story built at the validation path a repaired helper expects.

    story-053 converted the four repaired modules to take the repository they
    ask about as an argument, so pointing one at a synthetic history is now the
    helper's own parameter rather than a monkeypatch over the name it imported.
    The helper still runs unmodified — which is what makes the failures below a
    property of the repaired code rather than of a reimplementation of it — and
    the repository it resolves is the constructed one.
    """
    return committed_story(tmp_path, conftest.CONSTRUCTED_VALIDATION_REL,
                           guarded, violate=violate, name=name)


# --------------------------------------------------------------------------
# The shared resolution
# --------------------------------------------------------------------------


def test_the_resolution_lives_in_one_place_and_takes_a_repository():
    for function in (story_commit_range, story_diff):
        parameters = inspect.signature(function).parameters
        assert "repo" in parameters, function.__name__
        assert parameters["repo"].default == conftest.HARNESS_ROOT
    assert "validation_file" in inspect.signature(story_diff).parameters


#: The five resolvers, each with the full signature it carries, spelled out
#: rather than derived. story-053 converted twenty-six modules off them and was
#: required to change neither their behaviour nor their interface, and that is a
#: claim about *these five signatures* -- so they are written here and compared,
#: rather than asserted one keyword at a time in a way that could not notice a
#: parameter appearing, disappearing, changing default, or changing kind.
RESOLVER_SIGNATURES = {
    "story_commit_range":
        "(validation_file: pathlib.Path, repo: pathlib.Path = HARNESS_ROOT, "
        "origin: str | None = None) -> conftest.StoryRange",
    "story_diff":
        "(paths: list[str], *, validation_file: pathlib.Path, "
        "repo: pathlib.Path = HARNESS_ROOT, diff_filter: str | None = None, "
        "options: tuple[str, ...] = (), origin: str | None = None) -> str",
    "repository_file_at":
        "(relative: str, *, revision: str | None = None, "
        "validation_file: pathlib.Path | None = None, bound: str | None = None, "
        "repo: pathlib.Path = HARNESS_ROOT, origin: str | None = None) -> str",
    "function_source_at":
        "(relative: str, name: str, *, revision: str | None = None, "
        "validation_file: pathlib.Path | None = None, bound: str | None = None, "
        "repo: pathlib.Path = HARNESS_ROOT, origin: str | None = None) -> str",
    "revision_carrying":
        "(relative: str, *needles: str, repo: pathlib.Path = HARNESS_ROOT) -> str",
}


def test_the_five_resolvers_keep_their_signatures():
    """Asserted of the signatures, not claimed of them.

    `inspect.signature` renders parameter names, kinds, defaults and
    annotations, so a resolver that gained a keyword, lost one, changed a
    default or moved a parameter between positional and keyword-only reports a
    different string here. The `HARNESS_ROOT` default renders as the repository
    path, which would make this a machine-specific literal, so it is folded
    back to the name it is written under.
    """
    for name, expected in RESOLVER_SIGNATURES.items():
        rendered = str(inspect.signature(getattr(conftest, name)))
        rendered = rendered.replace(repr(conftest.HARNESS_ROOT), "HARNESS_ROOT")
        assert rendered == expected, name

    # The control: the comparison can differ. A resolver's signature read
    # without the fold, and one read from a function with a parameter added,
    # are both reported as different by the same comparison.
    def with_one_more(relative: str, *needles: str, repo=conftest.HARNESS_ROOT,
                      shallow: bool = False) -> str:
        return ""

    widened = str(inspect.signature(with_one_more)).replace(
        repr(conftest.HARNESS_ROOT), "HARNESS_ROOT")
    assert widened != RESOLVER_SIGNATURES["revision_carrying"]
    assert str(inspect.signature(conftest.revision_carrying)) \
        != RESOLVER_SIGNATURES["revision_carrying"]


def test_no_repaired_file_carries_its_own_copy_of_the_resolution():
    """The absence: no repaired module resolves a revision itself any more.

    The control is the pre-repair text of the same four files, which does —
    so a scan that had stopped seeing git calls would fail here rather than
    report four clean modules.
    """
    def resolving_calls(source: str) -> list[str]:
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"):
                continue
            literals = [element.value for element in ast.walk(node)
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)]
            if "git" in literals and {"diff", "log", "show"} & set(literals):
                found.append(f"line {node.lineno}")
        return found

    for rel in REPAIRED:
        current = (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8")
        assert resolving_calls(current) == [], rel
        assert "from conftest import" in current, rel
        assert resolving_calls(pre_repair_source(rel)), (
            f"{rel} was expected to resolve its own baseline before the repair")


def test_a_committed_story_resolves_to_its_run_commit_and_that_commits_parent(
    tmp_path,
):
    root = committed_story(tmp_path, "tests/test_story_042_validation.py",
                           "schemas/")
    resolved = story_commit_range(root / "tests/test_story_042_validation.py",
                                  root)
    assert resolved.committed
    assert resolved.endpoint == git(root, "rev-parse", "HEAD").strip()
    assert resolved.baseline == git(root, "rev-parse", "HEAD^").strip()
    # The baseline really predates the story: the validation file is not in it.
    listing = git(root, "ls-tree", "-r", "--name-only", resolved.baseline).split()
    assert "tests/test_story_042_validation.py" not in listing
    assert "tests/test_story_042_validation.py" in git(
        root, "ls-tree", "-r", "--name-only", resolved.endpoint).split()


def test_the_run_commit_is_not_a_later_planning_or_hotfix_commit(tmp_path):
    root = committed_story(tmp_path, "tests/test_story_042_validation.py",
                           "schemas/")
    run_commit = git(root, "rev-parse", "HEAD").strip()
    write(root, "tests/test_story_042_validation.py", "def test_it():\n    pass\n")
    commit(root, "a hotfix on the same story")
    write(root, "schemas/kept.txt", "a later story legitimately edits this\n")
    later = commit(root, "a later story")

    resolved = story_commit_range(root / "tests/test_story_042_validation.py",
                                  root)
    assert resolved.endpoint == run_commit
    assert resolved.endpoint != later
    # And the later story's edit is not attributed to this one.
    assert story_diff(
        ["schemas/"],
        validation_file=root / "tests/test_story_042_validation.py",
        repo=root).strip() == ""


def test_a_re_added_validation_file_still_resolves_to_the_oldest_addition(
    tmp_path,
):
    root = committed_story(tmp_path, "tests/test_story_042_validation.py",
                           "schemas/")
    run_commit = git(root, "rev-parse", "HEAD").strip()
    (root / "tests/test_story_042_validation.py").unlink()
    commit(root, "removed by accident")
    write(root, "tests/test_story_042_validation.py", "def test_it():\n    pass\n")
    commit(root, "restored")
    assert story_commit_range(
        root / "tests/test_story_042_validation.py", root).endpoint == run_commit


def test_an_uncommitted_validation_file_compares_the_working_tree_to_head(
    tmp_path,
):
    """The in-flight case, with the control beside it: clean while nothing has
    been touched, non-empty the moment the working tree touches the subject."""
    root = tmp_path / "in-flight"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "schemas/story.schema.json", "{}\n")
    commit(root, "pre-story")
    validation_file = write(root, "tests/test_story_042_validation.py", "pass\n")

    resolved = story_commit_range(validation_file, root)
    assert not resolved.committed
    assert resolved.baseline == "HEAD"
    assert resolved.endpoint is None
    assert story_diff(["schemas/"], validation_file=validation_file,
                      repo=root).strip() == ""

    write(root, "schemas/story.schema.json", '{"edited": true}\n')
    assert story_diff(["schemas/"], validation_file=validation_file,
                      repo=root).strip() != ""


def test_the_resolution_raises_rather_than_degrading_on_a_truncated_history(
    tmp_path,
):
    """A shallow clone carries the validation file in HEAD but not the commit
    that added it. Falling back to the working tree there would hand every
    caller a baseline that cannot fail, so it must raise.

    The control is the same clone unshallowed, where the resolution succeeds —
    so the raise is a property of the truncation and not of the clone.
    """
    origin = committed_story(tmp_path, "tests/test_story_042_validation.py",
                             "schemas/")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", "-q", origin.as_uri(),
                    str(shallow)], cwd=tmp_path, capture_output=True,
                   text=True, check=True)
    validation_file = shallow / "tests/test_story_042_validation.py"
    assert validation_file.is_file()

    with pytest.raises(NothingToCompareAgainst) as raised:
        story_commit_range(validation_file, shallow)
    assert "nothing to compare against" in str(raised.value)

    git(shallow, "fetch", "-q", "--unshallow")
    assert story_commit_range(validation_file, shallow).committed


def test_the_resolution_raises_when_the_adding_commit_is_the_root_commit(
    tmp_path,
):
    root = tmp_path / "root-commit"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "schemas/story.schema.json", "{}\n")
    validation_file = write(root, "tests/test_story_042_validation.py", "pass\n")
    commit(root, "everything at once")

    with pytest.raises(NothingToCompareAgainst) as raised:
        story_commit_range(validation_file, root)
    assert "nothing to compare against" in str(raised.value)


def test_the_raise_is_not_swallowed_by_the_diff_helper(tmp_path):
    """`story_diff` must propagate the raise rather than returning empty
    output, which a caller would read as 'unchanged'."""
    origin = committed_story(tmp_path, "tests/test_story_042_validation.py",
                             "schemas/")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", "-q", origin.as_uri(),
                    str(shallow)], cwd=tmp_path, capture_output=True,
                   text=True, check=True)
    with pytest.raises(NothingToCompareAgainst):
        story_diff(["schemas/"],
                   validation_file=shallow / "tests/test_story_042_validation.py",
                   repo=shallow)


# --------------------------------------------------------------------------
# The three less-proven resolvers, over the shapes this repository has hit
#
# `story_commit_range` has been exercised above since story-015.
# `repository_file_at`, `function_source_at` and `revision_carrying` arrived
# later and were proven only by the callers that happened to use them -- which
# is no proof at all, because every one of those callers asked *this*
# repository's history, where the shape under test is whatever the history
# happens to be on the day. So they are proven here, against histories this
# module builds, over the four shapes this repository has actually hit: a
# rename, a revert-and-restore, a squash, and a path with no history.
#
# Each case carries its own break rather than its own reimplementation. The
# helper under test is loaded from `tests/conftest.py` in the working tree with
# one substitution applied -- the mutation idiom the suite already uses, and
# the one that keeps the subject the shipped code rather than a copy of it --
# and the same case is shown answering differently under the break. A case that
# no break can redden is not proving the helper; it is watching it.
# --------------------------------------------------------------------------


#: One substitution per resolver, each the defect that resolver exists to
#: prevent. Applied to `tests/conftest.py` in the working tree, so what is
#: broken is the shipped helper and not a stand-in for it.
BREAKS = {
    #: The bound dropped: every read answers HEAD, which is the trap the
    #: reader's own docstring records under the HEAD-baseline bullets.
    "repository_file_at": ('result = _git(repo, "show", f"{resolved}:{relative}")',
                           'result = _git(repo, "show", f"HEAD:{relative}")'),
    #: Decorators dropped, which `function_source` includes deliberately --
    #: `_escalate` carries one that decides when its work is committed.
    "function_source": ("first = min([node.lineno] + "
                        "[d.lineno for d in node.decorator_list])",
                        "first = node.lineno"),
    #: The needles no longer required together on one line, so a revision
    #: carrying the same words in different sentences answers instead. That is
    #: the exact collision story-051 spent its retry budget on.
    "revision_carrying": ("for line in text.splitlines()",
                          "for line in [text]"),
}


def broken(resolver: str, tmp_path: Path):
    """`tests/conftest.py` with one resolver's defining line broken."""
    module = conftest.load_mutant(
        Path(conftest.__file__), [BREAKS[resolver]],
        name=f"conftest_with_{resolver}_broken", tmp_path=tmp_path)
    return module


DECORATED = '''\
import functools


@functools.cache
def marked() -> str:
    return "{answer}"
'''


def test_repository_file_at_reads_a_renamed_paths_text_at_its_own_name(tmp_path):
    """A rename is the shape that silently empties a history-bound read.

    Before the rename the text lives at the old path and the new path does not
    exist; after it, the reverse. Each end is read at the name it has there,
    which is what the helper is for -- and a read that had drifted to HEAD
    would find nothing at the old name at all.
    """
    root = tmp_path / "renamed"
    before, after = conftest.build_history(root, [
        {"write": {"orchestration/old_name.py": DECORATED.format(answer="first")},
         "message": "the original"},
        {"rename": {"orchestration/old_name.py": "orchestration/new_name.py"},
         "write": {"orchestration/new_name.py": DECORATED.format(answer="second")},
         "message": "renamed and rewritten"},
    ])

    assert 'return "first"' in conftest.repository_file_at(
        "orchestration/old_name.py", revision=before, repo=root)
    assert 'return "second"' in conftest.repository_file_at(
        "orchestration/new_name.py", revision=after, repo=root)
    with pytest.raises(NothingToCompareAgainst):
        conftest.repository_file_at("orchestration/new_name.py", revision=before,
                                    repo=root)

    # The break: the same two reads with the bound dropped answer HEAD, where
    # the old name is gone and the new one carries the later text.
    hobbled = broken("repository_file_at", tmp_path)
    with pytest.raises(hobbled.NothingToCompareAgainst):
        hobbled.repository_file_at("orchestration/old_name.py", revision=before,
                                   repo=root)
    assert 'return "second"' in hobbled.repository_file_at(
        "orchestration/new_name.py", revision=before, repo=root)


def test_function_source_at_recovers_a_decorated_function_across_a_rename(
    tmp_path,
):
    """The text half, over the same rename, decorators included."""
    root = tmp_path / "renamed-function"
    before, after = conftest.build_history(root, [
        {"write": {"orchestration/old_name.py": DECORATED.format(answer="first")},
         "message": "the original"},
        {"rename": {"orchestration/old_name.py": "orchestration/new_name.py"},
         "write": {"orchestration/new_name.py": DECORATED.format(answer="second")},
         "message": "renamed and rewritten"},
    ])

    recovered = conftest.function_source_at("orchestration/old_name.py", "marked",
                                            revision=before, repo=root)
    assert recovered.startswith("@functools.cache")
    assert 'return "first"' in recovered
    assert conftest.function_source_at("orchestration/new_name.py", "marked",
                                       revision=after,
                                       repo=root).startswith("@functools.cache")

    # The break: without the decorator the recovered source is a different
    # text, so a comparison of two revisions' sources would miss a decorator
    # added or removed between them.
    hobbled = broken("function_source", tmp_path)
    without = hobbled.function_source_at("orchestration/old_name.py", "marked",
                                         revision=before, repo=root)
    assert not without.startswith("@functools.cache")
    assert without != recovered


def test_revision_carrying_skips_the_commit_that_removed_the_path(tmp_path):
    """A revert-and-restore: the path is written, deleted, and written again.

    `git log -- <path>` reports the deleting commit too, and that revision
    holds no blob at all. The helper has to answer no rather than raise there,
    and go on to the revision that does carry the text.
    """
    root = tmp_path / "revert-and-restore"
    written, removed, restored = conftest.build_history(root, [
        {"write": {"docs/note.md": "the original sentence, exactly as written\n"},
         "message": "written"},
        {"delete": ["docs/note.md"], "message": "reverted"},
        {"write": {"docs/note.md": "a replacement sentence\n"}, "message": "restored"},
    ])

    assert conftest.revision_carrying("docs/note.md", "original", "sentence",
                                      repo=root) == written
    assert conftest.revision_carrying("docs/note.md", "replacement",
                                      repo=root) == restored
    # The deleting commit really is in the path's log, so the skip is doing
    # work rather than never being reached.
    log = git(root, "log", "--format=%H", "--", "docs/note.md").split()
    assert removed in log
    with pytest.raises(NothingToCompareAgainst):
        conftest.repository_file_at("docs/note.md", revision=removed, repo=root)


def test_revision_carrying_requires_the_needles_on_one_line(tmp_path):
    """The property story-051's retry budget paid for.

    A later revision carrying the same two words in *different* sentences must
    not answer a search for the sentence that held them together.
    """
    root = tmp_path / "one-line"
    sentence, scattered = conftest.build_history(root, [
        {"write": {"docs/note.md": "the check reports twenty-six modules\n"},
         "message": "the sentence"},
        {"write": {"docs/note.md": "the check was rewritten.\n"
                                   "it now covers twenty-six of something else.\n"
                                   "it reports nothing about them.\n"},
         "message": "the same words, different sentences"},
    ])

    assert conftest.revision_carrying("docs/note.md", "reports", "twenty-six",
                                      repo=root) == sentence

    # The break: dropping the one-line requirement answers with the later
    # revision, which carries both words and says the opposite.
    hobbled = broken("revision_carrying", tmp_path)
    assert hobbled.revision_carrying("docs/note.md", "reports", "twenty-six",
                                     repo=root) == scattered


def test_the_resolvers_survive_a_squash_that_makes_a_pinned_revision_unreachable(
    tmp_path,
):
    """The squash-merge shape, end to end.

    Several commits are replayed as one, and the repository is cloned so the
    replaced commits are genuinely absent rather than merely unreferenced. A
    pinned revision is then unreadable — which is how a suite that passed
    locally failed in the clean clone — while the search finds the text in the
    commit that now carries it.
    """
    root = tmp_path / "before-squash"
    base, _, pinned = conftest.build_history(root, [
        {"write": {"docs/note.md": "nothing yet\n"}, "message": "base"},
        {"write": {"docs/note.md": "an intermediate sentence\n"},
         "message": "step one"},
        {"write": {"docs/note.md": "the sentence that survives the squash\n"},
         "message": "step two"},
    ])
    squashed = conftest.squash_onto(root, base, "the squash merge")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", root.as_uri(), str(clone)],
                   cwd=tmp_path, capture_output=True, text=True, check=True)
    assert git(clone, "rev-parse", "HEAD").strip() == squashed
    assert pinned not in git(clone, "log", "--format=%H")

    # The pinned revision cannot be read in the clone at all.
    with pytest.raises(NothingToCompareAgainst):
        conftest.repository_file_at("docs/note.md", revision=pinned, repo=clone)
    # The search, which is what the helper is for, still finds it.
    assert conftest.revision_carrying("docs/note.md", "survives", "squash",
                                      repo=clone) == squashed
    assert "survives the squash" in conftest.repository_file_at(
        "docs/note.md", revision=squashed, repo=clone)

    # The break: with the bound dropped, the unreachable pinned revision reads
    # *successfully* — it silently answers HEAD — so the unreachability the
    # clean clone would have reported disappears into a green assertion. That
    # is the shape of the failure this whole resolution exists to prevent, and
    # the raise above is what prevents it.
    hobbled = broken("repository_file_at", tmp_path)
    assert "survives the squash" in hobbled.repository_file_at(
        "docs/note.md", revision=pinned, repo=clone)


def test_a_path_with_no_history_raises_rather_than_answering(tmp_path):
    """A file in the working tree that no commit has ever carried.

    Every one of the three has to refuse it. Degrading to the working tree
    there is the failure mode the whole resolution exists to prevent: it hands
    the caller a value that looks like an answer and makes the assertion built
    on it unfalsifiable.
    """
    root = tmp_path / "no-history"
    conftest.build_history(root, [
        {"write": {"docs/note.md": "committed\n"}, "message": "only this"},
    ])
    write(root, "docs/never-committed.md", "written but never committed\n")

    with pytest.raises(NothingToCompareAgainst):
        conftest.repository_file_at("docs/never-committed.md", revision="HEAD",
                                    repo=root)
    with pytest.raises(NothingToCompareAgainst):
        conftest.function_source_at("docs/never-committed.md", "marked",
                                    revision="HEAD", repo=root)
    with pytest.raises(NothingToCompareAgainst) as raised:
        conftest.revision_carrying("docs/never-committed.md", "written",
                                   repo=root)
    assert "no revision" in str(raised.value)

    # The break: a read that answers the working tree instead returns the text
    # happily, which is the shape of an answer nobody can falsify.
    hobbled = broken("repository_file_at", tmp_path)
    with pytest.raises(hobbled.NothingToCompareAgainst):
        hobbled.repository_file_at("docs/never-committed.md", revision="HEAD",
                                   repo=root)
    # And the working-tree file really is there, so the refusal is about the
    # history rather than about the path being absent.
    assert (root / "docs/never-committed.md").is_file()


# --------------------------------------------------------------------------
# The four repairs, shown failing
# --------------------------------------------------------------------------


def test_every_repaired_subject_is_covered_here():
    """The companion assertion the table above needs: the subjects exercised
    below are the subjects the repaired modules actually assert, read out of
    their source rather than trusted."""
    for rel, module in REPAIRED.items():
        source = (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8")
        literals = {node.value for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)}
        covered = {subject for module_rel, subject in SUBJECTS
                   if module_rel == rel}
        assert covered, rel
        assert covered <= literals, (rel, covered - literals)


@pytest.mark.parametrize("rel,subject", SUBJECTS)
def test_a_repaired_assertion_passes_when_its_story_respects_its_subject(
    tmp_path, rel, subject,
):
    root = repaired_story(tmp_path, subject)
    assert _assert_unchanged(rel, subject, root) is None


@pytest.mark.parametrize("rel,subject", SUBJECTS)
def test_a_repaired_assertion_fails_when_its_story_violates_its_subject(
    tmp_path, rel, subject,
):
    """The guarantee: not that the assertion passes, but that it can fail.

    The synthetic story's own run commit rewrites the path the assertion
    names, and the repaired code — the module's own helper, unmodified — must
    say so.
    """
    root = repaired_story(tmp_path, subject, violate="modify")
    with pytest.raises(AssertionError):
        _assert_unchanged(rel, subject, root)


#: The helper each repaired module names its own "did my story leave this
#: alone" assertion. Keyed by origin rather than by module object, because
#: story-038's merge gave story-008 and story-009 one module with one helper
#: apiece, and `module is story008` can no longer tell them apart.
#:
#: story-007's entry arrived with story-053: converting that assertion to take
#: the repository it asks about turned its body into a helper beside it, and
#: naming the helper here is what lets this table drive all four the same way.
UNCHANGED_HELPER = {
    "tests/test_story_007_validation.py": "_no_committed_story_artifact_edited",
    "tests/test_story_008_validation.py": "_unchanged_by_story_008",
    "tests/test_story_009_validation.py": "_unchanged_by_story_009",
    "tests/test_story_010_validation.py": "_unchanged_by_this_story",
}


def _assert_unchanged(rel: str, subject, root: Path) -> None:
    """Run the module's own assertion for `subject` against `root`, raising
    AssertionError when it reports a change."""
    module = REPAIRED[rel]
    helper = getattr(module, UNCHANGED_HELPER[rel])
    if rel == "tests/test_story_007_validation.py":
        assert helper(root)
        return None
    if rel == "tests/test_story_008_validation.py" and subject == ".harness/stories":
        assert helper(subject, root, diff_filter="MD")
        return None
    assert helper(subject, root)
    return None


@pytest.mark.parametrize("rel,subject", SUBJECTS)
def test_the_same_violation_is_invisible_to_the_baseline_this_story_removed(
    tmp_path, rel, subject,
):
    """Why every one of these repairs was needed, demonstrated per subject:
    over the identical history, `git diff HEAD` is empty and the honest range
    is not."""
    root = repaired_story(tmp_path, subject, violate="modify")
    assert git(root, "diff", "HEAD", "--", subject).strip() == ""
    assert conftest.constructed_story_diff(root, [subject]).strip() != ""


@pytest.mark.parametrize("violation", ["modify", "delete"])
def test_the_narrowed_assertions_still_catch_a_rewritten_execution_record(
    tmp_path, violation,
):
    """Narrowed to modifications and deletions is not weakened: a committed
    story artifact rewritten or removed inside the story's own run commit is
    still caught, by both narrowed assertions."""
    for rel, subject in (
        ("tests/test_story_007_validation.py", ".harness/stories/"),
        ("tests/test_story_008_validation.py", ".harness/stories"),
    ):
        root = repaired_story(tmp_path, subject, violate=violation,
                              name=f"{Path(rel).stem}-{violation}")
        with pytest.raises(AssertionError):
            _assert_unchanged(rel, subject, root)


def test_the_narrowing_permits_exactly_the_storys_own_new_artifact(tmp_path):
    """And what it lets through: an *addition* inside the story's own run
    commit, which was never an edit. The control is the test above — the same
    assertion still fires on a modification or a deletion."""
    for rel, subject in (
        ("tests/test_story_007_validation.py", ".harness/stories/"),
        ("tests/test_story_008_validation.py", ".harness/stories"),
    ):
        root = repaired_story(tmp_path, subject, violate="add",
                              name=f"{Path(rel).stem}-added")
        added = conftest.constructed_story_diff(
            root, [subject], diff_filter="A", options=("--name-only",))
        assert added.strip(), "the synthetic story was supposed to add a record"
        assert _assert_unchanged(rel, subject, root) is None


def test_no_repaired_assertion_changed_its_subject_or_its_strictness():
    """The diff of each repaired file against its pre-repair version, read
    mechanically: the same tests over the same subjects, with the single
    authorized narrowing and nothing else."""
    #: Keyed by the file the filters are *read out of*, not by the origin.
    #: story-038 merged story-008's module with story-009's, and story-008's
    #: authorized narrowing is in the merged text — so asking story-009's
    #: origin about a file it now shares would report story-008's filter as
    #: an unauthorized one.
    narrowed = {
        "tests/test_stage_output_ownership.py",
        "tests/test_planner_injection.py",
    }
    for rel in REPAIRED:
        before = pre_repair_source(rel)
        after = (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8")
        assert expected_test_names(CURRENT[rel]) == _test_names(after), rel
        assert _guarded_paths(before) <= _guarded_paths(after), rel

        filters = _diff_filters(after)
        assert filters == ({"MD"} if CURRENT[rel] in narrowed else set()), (
            rel, filters)


def _diff_filters(source: str) -> set[str]:
    """Every value passed as `diff_filter=`, which is the only place this
    story is authorized to change an assertion's strictness."""
    return {node.value.value
            for call in ast.walk(ast.parse(source))
            if isinstance(call, ast.Call)
            for node in call.keywords
            if node.arg == "diff_filter" and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)}


def _test_names(source: str) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def _guarded_paths(source: str) -> set[str]:
    """Every path a *test* in this module names, in its body or in its
    parametrize decorator.

    Scoped to test functions on purpose: the pre-repair sources carried
    module-level constants belonging to the resolution machinery the repair
    removed (story-009's `STORY_MARKER_PATH`, for one), and those were never
    subjects of an assertion. What a test names is what it guards.
    """
    paths = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef)
                and node.name.startswith("test_")):
            continue
        for inner in [*ast.walk(node)]:
            if (isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                    and "/" in inner.value
                    and len(inner.value.splitlines()) == 1
                    and not inner.value.startswith(("http", " "))):
                paths.add(inner.value)
    return paths


def test_the_repaired_assertions_pass_on_this_repository(tmp_path):
    """Run for real and unpatched: the repaired modules' own test functions,
    as this repository ships them, executed here end to end.

    Their subjects, their controls and their strictness are the modules' own —
    nothing here reaches inside them — so what this asserts is that the four
    repaired assertions, as they stand in this repository, pass.
    """
    story007.test_no_committed_story_artifact_was_edited(tmp_path / "story-007")
    story008.test_l5_assist_is_unchanged(tmp_path / "assist")
    story008.test_the_story_schema_is_unchanged(tmp_path / "story-schema")
    story008.test_no_committed_story_artifact_was_edited(tmp_path / "story-008")
    for index, rel in enumerate(("workflows/", "rules/", "schemas/")):
        story009.test_the_definitions_this_story_injects_are_unchanged(
            rel, tmp_path / f"story-009-{index}")
    story010.test_context_assembler_is_unchanged(tmp_path / "assembler")
    for index, rel in enumerate(("workflows/", "schemas/", "rules/", "prompts/")):
        story010.test_the_definitions_this_story_reads_are_unchanged(
            rel, tmp_path / f"story-010-{index}")


# --------------------------------------------------------------------------
# The mechanical check
# --------------------------------------------------------------------------


def pre_repair_source(rel: str) -> str:
    """A repaired file as it stood before this story touched it.

    Carried as a committed fixture under `tests/history-fixtures/` rather than
    resolved out of this repository's commit graph. The text is the same text
    -- it was lifted from the baseline of story-015's own commit range, which
    is where this used to read it from -- but it is now evidence the repository
    *holds* rather than an answer git recomputes. A rebase, a squash or a
    rename moved the old answer and moves nothing here, and the fixture is
    diffable, so a story that changes the regression set changes it visibly.

    What the fixture claims about itself is asserted rather than trusted, by
    `test_the_recovered_pre_repair_sources_are_the_pre_repair_sources` below
    and by every scan that is fed it.
    """
    return conftest.history_fixture(f"{Path(rel).name}.txt")


def test_the_recovered_pre_repair_sources_are_the_pre_repair_sources():
    """The regression set is only evidence if it is what it claims to be."""
    for rel in REPAIRED:
        before = pre_repair_source(rel)
        assert before != (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8"), rel
        assert "def test_" in before, rel


@pytest.mark.parametrize("rel", sorted(REPAIRED))
def test_the_check_flags_each_merged_instance_recovered_from_history(rel):
    flags = check.flagged_calls(pre_repair_source(rel), Path(rel).name)
    assert flags, rel


def test_the_check_flags_story_013s_archived_instance():
    assert ARCHIVED_INSTANCE.is_file()
    flags = check.flagged_calls(
        ARCHIVED_INSTANCE.read_text(encoding="utf-8"), ARCHIVED_INSTANCE.name)
    assert flags
    reasons = " ".join(flag.reason for flag in flags)
    assert "HEAD" in reasons and "porcelain" in reasons


def test_all_five_known_instances_are_caught_and_the_repairs_are_clean():
    caught = {rel for rel in REPAIRED
              if check.flagged_calls(pre_repair_source(rel), rel)}
    assert caught == set(REPAIRED)
    assert check.flagged_calls(
        ARCHIVED_INSTANCE.read_text(encoding="utf-8"), ARCHIVED_INSTANCE.name)
    for rel in REPAIRED:
        assert check.flagged_calls(
            (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8"),
            CURRENT[rel]) == [], rel


def test_the_live_suite_carries_no_dishonest_baseline():
    """The absence. Its control is the parametrized regression set above: the
    same scanner, on the same modules before their repair, flags all five."""
    flags = [flag
             for path in check.scanned_modules()
             for flag in check.flagged_calls(path.read_text(encoding="utf-8"),
                                             path.name)]
    assert flags == [], "\n".join(str(flag) for flag in flags)


@pytest.mark.parametrize("revision", ["HEAD", "HEAD~1", "HEAD^", "HEAD:prompts/x"])
def test_every_head_derived_revision_against_the_repository_root_is_flagged(
    revision,
):
    for form in (
        f"subprocess.run(['git', '-C', str(REPO_ROOT), 'diff', '{revision}'])",
        f"subprocess.run(['git', 'diff', '{revision}'], cwd=REPO_ROOT)",
        f"subprocess.check_output(['git', '-C', str(HARNESS_ROOT), 'show', "
        f"'{revision}'])",
    ):
        assert check.flagged_calls(f"import subprocess\n{form}\n", "probe.py"), form


def test_a_working_tree_status_query_against_the_repository_root_is_flagged():
    source = ("import subprocess\n"
              "subprocess.run(['git', 'status', '--porcelain'], cwd=REPO_ROOT)\n")
    assert check.flagged_calls(source, "probe.py")


def test_an_interpolated_head_revision_is_flagged_and_a_resolved_one_is_not():
    """`f\"HEAD~{n}\"` is the idiom spelled with an offset; `f\"{revision}:{path}\"`
    is a revision the test resolved, which is the honest form."""
    dishonest = ("import subprocess\n"
                 "subprocess.run(['git', '-C', str(REPO_ROOT), 'diff', "
                 "f'HEAD~{n}'])\n")
    honest = ("import subprocess\n"
              "subprocess.run(['git', '-C', str(REPO_ROOT), 'show', "
              "f'{revision}:{path}'])\n")
    assert check.flagged_calls(dishonest, "probe.py")
    assert check.flagged_calls(honest, "probe.py") == []


def test_a_throwaway_repository_is_not_the_checks_business():
    """The distinction the check is built on, stated as a control: the same
    two commands, flagged against the repository root and ignored against a
    repository the test built for itself."""
    def probe(target: str) -> str:
        return ("import subprocess\n"
                "def probe(tmp_path):\n"
                "    root = tmp_path / 'repo'\n"
                f"    subprocess.run(['git', '-C', str({target}), 'diff', 'HEAD'])\n"
                f"    subprocess.run(['git', 'status', '--porcelain'], "
                f"cwd={target})\n")

    assert check.flagged_calls(probe("root"), "probe.py") == []
    assert len(check.flagged_calls(probe("REPO_ROOT"), "probe.py")) == 2


@pytest.mark.parametrize("name", [
    "conftest.py",
    "test_schema_directed_parsing.py",
    "test_single_story_reader.py",
    "test_stage_output_ownership.py",
    "test_story_coordinator.py",
    "test_execution_history.py",
])
def test_the_throwaway_repository_tests_are_unflagged_by_the_scanner(name):
    """These modules build their own repository under tmp_path, so their git
    calls are not this check's business. `conftest.py` is scanned here
    directly even though it is exempt: its own HEAD usage goes through a
    local helper rather than a literal argument list, so the exemption is a
    stated policy rather than the thing keeping the suite green.

    The control for this absence is `test_a_throwaway_repository_is_not_the_
    checks_business`, which shows the identical commands flagged once their
    target is the repository root.
    """
    path = TESTS_DIR / name
    assert check.flagged_calls(path.read_text(encoding="utf-8"), name) == []


def test_story_011s_validation_file_is_unflagged_and_unchanged_by_this_story(
    tmp_path,
):
    """Two names for one module, and each is the name it has where it is read.

    The scan reads the working tree, where story-038 renamed the file to
    `test_execution_history.py`. The diff asks whether a story left that
    module's baseline-era path alone, and it asks it of a repository this test
    builds: a story that touches nothing there is reported clean, and the same
    call over a story that rewrites it reports the change. Reading this
    repository's own commit graph for it made the answer move on a rename, a
    squash or a rebase, none of which is a property of the module being
    unflagged.
    """
    current = "tests/test_execution_history.py"
    at_story_015 = "tests/test_story_011_validation.py"
    assert check.flagged_calls((REPO_ROOT / current).read_text(encoding="utf-8"),
                               current) == []

    respecting = committed_story(tmp_path, "tests/test_story_015_validation.py",
                                 at_story_015, name="story-011-left-alone")
    assert story_diff([at_story_015],
                      validation_file=respecting / "tests/test_story_015_validation.py",
                      repo=respecting).strip() == ""
    # The control: the identical call over a story whose own run commit did
    # rewrite that path reports it, so the emptiness above is a statement about
    # the story rather than about a comparison that cannot differ.
    violating = committed_story(tmp_path, "tests/test_story_015_validation.py",
                                at_story_015, violate="modify",
                                name="story-011-rewritten")
    assert story_diff([at_story_015],
                      validation_file=violating / "tests/test_story_015_validation.py",
                      repo=violating).strip() != ""


def test_exactly_one_module_is_exempt_and_the_exemption_is_stated():
    assert check.EXEMPT_MODULES == ("conftest.py",)
    source = (TESTS_DIR / "test_baseline_honesty.py").read_text(encoding="utf-8")
    assert "EXEMPT_MODULES = " in source
    conftest_source = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "def story_commit_range" in conftest_source
    scanned = {path.name for path in check.scanned_modules()}
    assert scanned.isdisjoint(check.EXEMPT_MODULES)
    assert scanned | set(check.EXEMPT_MODULES) == {
        path.name for path in TESTS_DIR.glob("*.py")}


def test_no_per_story_validation_file_is_exempt():
    """The check must not ship with exemptions for the cases that motivated
    it.

    The set used to be found by globbing `test_story_*.py`. story-038 renamed
    every per-story module for its subject, so that glob now finds the parser
    and coordinator modules and none of the validation files it was written
    about. The set comes off `conftest.STORY_ORIGINS` instead, which is the
    declaration of which modules validate a story and cannot be emptied by a
    later rename.
    """
    scanned = {path.name for path in check.scanned_modules()}
    validation_files = set(conftest.STORY_ORIGINS)
    assert validation_files
    assert validation_files <= {path.name for path in TESTS_DIR.glob("*.py")}
    assert validation_files <= scanned


def test_the_scanned_set_is_discovered_by_globbing_and_cannot_be_empty():
    modules = check.scanned_modules()
    assert modules
    assert {path.name for path in modules} == {
        path.name for path in TESTS_DIR.glob("*.py")
        if path.name not in check.EXEMPT_MODULES}
    assert Path(__file__).name in {path.name for path in modules}


def test_the_checks_module_states_the_narrow_class_it_covers():
    source = (TESTS_DIR / "test_baseline_honesty.py").read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    assert "narrow" in docstring.lower()
    assert "does not" in docstring.lower()


def test_a_well_written_absence_assertion_survives_the_check():
    """The mechanism distinguishes the two rather than flagging every absence
    assertion: an absence resolved against a repository the test built, with
    its negative control beside it, is clean — while the same file with one
    root-targeted HEAD call is not."""
    honest = (
        "import subprocess\n"
        "def test_the_story_left_schemas_alone(tmp_path):\n"
        "    root = build_repo(tmp_path)\n"
        "    diff = subprocess.run(['git', '-C', str(root), 'diff',\n"
        "                           base, tip, '--', 'schemas/'])\n"
        "    assert diff.stdout == ''\n"
        "def test_the_same_check_reports_a_violation(tmp_path):\n"
        "    root = build_repo(tmp_path, violate=True)\n"
        "    diff = subprocess.run(['git', '-C', str(root), 'diff',\n"
        "                           base, tip, '--', 'schemas/'])\n"
        "    assert diff.stdout != ''\n"
    )
    assert check.flagged_calls(honest, "probe.py") == []
    assert check.flagged_calls(honest + (
        "subprocess.run(['git', '-C', str(REPO_ROOT), 'diff', 'HEAD'])\n"),
        "probe.py")


# --------------------------------------------------------------------------
# The guidance, in the rendered prompts
# --------------------------------------------------------------------------


#: The third phrase used to be the path this repository keeps the shared
#: resolution at. The prompt ships to any target, so it now names the thing
#: rather than the file, and this reads the sentence that survived: the
#: instruction is still to use the shared resolution rather than to write a
#: second one, which is the whole of what the guidance was for.
TESTER_GUIDANCE = [
    "An assertion that claims an absence needs a negative control",
    "demonstrate that it can fail",
    "shared baseline resolution",
]
VERIFIER_GUIDANCE = [
    "absence",
    "is a finding",
]


def rendered(prompt_file: str, target_root: Path, harness_root: Path) -> str:
    story_text = (target_root / ".harness" / "stories"
                  / "story-001.yaml").read_text(encoding="utf-8")
    run_dir = target_root / ".harness" / "runs" / "story-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    context = context_assembler.build_context(
        story_text=story_text,
        story=story_parser.parse(story_text,
                                 schema_validator.load_schema("story")),
        run_dir=run_dir,
        target_root=target_root,
        harness_root=harness_root,
        config=harness_config.load_config(target_root),
        rules=harness_config.load_rules(harness_root),
        workflow=WORKFLOW,
        retry_count=0,
    )
    return context_assembler.render(
        context_assembler.load_template(harness_root, prompt_file), context)


def test_the_negative_control_guidance_reaches_the_rendered_tester_prompt(
    target_root, harness_root,
):
    prompt = rendered("tester.md", target_root, harness_root)
    for phrase in TESTER_GUIDANCE:
        assert phrase in prompt, phrase
    assert "positive" in prompt.lower()
    assert "{{" not in prompt


def test_the_corresponding_requirement_reaches_the_rendered_verifier_prompt(
    target_root, harness_root,
):
    prompt = rendered("verifier.md", target_root, harness_root)
    for phrase in VERIFIER_GUIDANCE:
        assert phrase in prompt, phrase
    assert "{{" not in prompt


def test_the_guidance_is_read_from_the_render_and_not_from_the_template(
    target_root, harness_root, monkeypatch,
):
    """The control for the two assertions above: they pass because the
    renderer produced the text, so a template that stopped being loaded shows
    up as a failure rather than as a silent pass."""
    monkeypatch.setattr(context_assembler, "load_template",
                        lambda root, name: "a template with no guidance\n")
    prompt = rendered("tester.md", target_root, harness_root)
    assert TESTER_GUIDANCE[0] not in prompt


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ["orchestration/", "workflows/", "schemas/",
                                 "scripts/", ".harness/runs-archive/",
                                 "prompts/implementer.md", "prompts/planner.md",
                                 "prompts/documenter.md",
                                 "prompts/harness-layer.md", "prompts/assist.md"])
def test_this_story_changed_nothing_outside_its_scope(rel, tmp_path):
    """Each scoped path, over a repository in which the story respected it.

    Restated rather than recalled. Asked of this repository's own commit graph
    the assertion re-stated a frozen past fact, and its answer moved whenever
    something was committed, renamed, squashed or rebased — a rename gives a
    path a new add-commit and silently empties every assertion bounded by that
    path's range. Here the story is built, the predicate is the same predicate,
    and the control beside it shows the same call reporting the violation.
    """
    validation_rel = "tests/test_story_015_validation.py"
    respecting = committed_story(tmp_path, validation_rel, rel,
                                 name="in-scope")
    assert story_diff([rel], validation_file=respecting / validation_rel,
                      repo=respecting).strip() == ""


def test_the_scope_assertion_above_can_fail(tmp_path):
    """Its control: over a synthetic history where the story does touch
    `orchestration/`, the identical call reports it."""
    rel = "tests/test_story_015_validation.py"
    root = committed_story(tmp_path, rel, "orchestration/", violate="modify")
    assert story_diff(["orchestration/"], validation_file=root / rel,
                      repo=root).strip() != ""


def test_the_archived_story_013_copy_was_not_restored_or_edited(tmp_path):
    """Held by content rather than by the path's absence.

    This was written as `not (TESTS_DIR / "test_story_013_validation.py")
    .exists()` while story-013 was reset and awaiting a re-run. That re-run
    has since landed and legitimately writes that path, so the absence form
    asserted something this story never meant: the property is that the
    *archived vacuous copy* was not put back, not that story-013 may never
    have validation again. Comparing content says the same thing and keeps
    saying it after the re-run.
    """
    archived = ARCHIVED_INSTANCE.read_text(encoding="utf-8")
    for path in sorted(TESTS_DIR.glob("*.py")):
        assert path.read_text(encoding="utf-8") != archived, path.name
    # The comparison is against a genuinely vacuous subject: this story's own
    # check still flags it.
    assert check.flagged_calls(archived, ARCHIVED_INSTANCE.name)
    assert ARCHIVED_INSTANCE.is_file()
    # And the archive is not a path a story edits in passing. Asked of this
    # repository's history that was a frozen fact whose evidence moved under
    # it; asked of a story this test builds it is checked, with the control
    # beside it showing the same call reporting an edit to the archive.
    archive = str(ARCHIVED_INSTANCE.parent.relative_to(REPO_ROOT))
    validation_rel = "tests/test_story_015_validation.py"
    respecting = committed_story(tmp_path, validation_rel, archive,
                                 name="archive-left-alone")
    assert story_diff([archive],
                      validation_file=respecting / validation_rel,
                      repo=respecting).strip() == ""
    violating = committed_story(tmp_path, validation_rel, archive,
                                violate="modify", name="archive-edited")
    assert story_diff([archive], validation_file=violating / validation_rel,
                      repo=violating).strip() != ""


def test_the_suite_still_has_the_tests_the_repaired_files_shipped_with():
    """No test was weakened, skipped or deleted to make the repairs pass."""
    for rel in REPAIRED:
        after = (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8")
        assert expected_test_names(CURRENT[rel]) == _test_names(after), rel
        assert "@pytest.mark.skip" not in after, rel
        assert "pytest.skip(" not in after, rel
