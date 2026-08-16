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
WORKFLOW = harness_config.load_workflow(REPO_ROOT, "story-workflow")


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


def _under(guarded: str) -> tuple[str, str]:
    """A file the guarded pathspec covers, and a second one it would also
    cover if the story added it."""
    if guarded.endswith("/") or not Path(guarded).suffix:
        base = guarded.rstrip("/")
        return f"{base}/kept.txt", f"{base}/brand-new.txt"
    return guarded, f"{guarded}.new"


def committed_story(tmp_path: Path, validation_rel: str, guarded: str, *,
                    violate: str | None = None, name: str = "synthetic") -> Path:
    """A repository in which one story has already run and committed.

    Commit 1 is the pre-story state and carries the guarded path. Commit 2 is
    the story's own run commit: it adds the validation file and, when
    `violate` says so, touches the guarded path in the same commit. That is
    the shape of a finished branch, and the shape under which `git diff HEAD`
    reports nothing no matter what the story did.
    """
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q")
    subject, sibling = _under(guarded)
    write(root, subject, "the pre-story content\n")
    write(root, "unrelated.txt", "something the story may touch\n")
    commit(root, "pre-story")

    write(root, validation_rel, "def test_it():\n    assert True\n")
    write(root, "unrelated.txt", "the story's own legitimate change\n")
    if violate == "modify":
        write(root, subject, "rewritten inside the story's own run commit\n")
    elif violate == "delete":
        (root / subject).unlink()
    elif violate == "add":
        write(root, sibling, "an addition\n")
    commit(root, "the story's own run commit")
    return root


def redirect(monkeypatch, module, root: Path, validation_rel: str) -> None:
    """Point one repaired module's assertions at a synthetic repository.

    The module's own helper runs unmodified — only the repository it asks
    about moves. That is what makes the failure below a property of the
    repaired code rather than of a reimplementation of it.
    """
    real = conftest.story_diff

    def patched(paths, *, validation_file=None, repo=None, origin=None,
                **kwargs):
        # `origin` is dropped deliberately. In this repository it names which
        # of a merged module's two stories a range belongs to; the synthetic
        # repository holds exactly one story, so the file's own path is the
        # only lineage there and naming an origin it never declared would
        # raise instead of resolving.
        return real(paths, validation_file=root / validation_rel, repo=root,
                    **kwargs)

    monkeypatch.setattr(module, "story_diff", patched)


# --------------------------------------------------------------------------
# The shared resolution
# --------------------------------------------------------------------------


def test_the_resolution_lives_in_one_place_and_takes_a_repository():
    for function in (story_commit_range, story_diff):
        parameters = inspect.signature(function).parameters
        assert "repo" in parameters, function.__name__
        assert parameters["repo"].default == conftest.HARNESS_ROOT
    assert "validation_file" in inspect.signature(story_diff).parameters


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
    monkeypatch, tmp_path, rel, subject,
):
    root = committed_story(tmp_path, rel, subject)
    redirect(monkeypatch, REPAIRED[rel], root, rel)
    assert _assert_unchanged(rel, subject) is None


@pytest.mark.parametrize("rel,subject", SUBJECTS)
def test_a_repaired_assertion_fails_when_its_story_violates_its_subject(
    monkeypatch, tmp_path, rel, subject,
):
    """The guarantee: not that the assertion passes, but that it can fail.

    The synthetic story's own run commit rewrites the path the assertion
    names, and the repaired code — the module's own helper, unmodified — must
    say so.
    """
    root = committed_story(tmp_path, rel, subject, violate="modify")
    redirect(monkeypatch, REPAIRED[rel], root, rel)
    with pytest.raises(AssertionError):
        _assert_unchanged(rel, subject)


#: The helper each repaired module names its own "did my story leave this
#: alone" assertion. Keyed by origin rather than by module object, because
#: story-038's merge gave story-008 and story-009 one module with one helper
#: apiece, and `module is story008` can no longer tell them apart.
UNCHANGED_HELPER = {
    "tests/test_story_008_validation.py": "_unchanged_by_story_008",
    "tests/test_story_009_validation.py": "_unchanged_by_story_009",
    "tests/test_story_010_validation.py": "_unchanged_by_this_story",
}


def _assert_unchanged(rel: str, subject) -> None:
    """Run the module's own assertion for `subject`, raising AssertionError
    when it reports a change."""
    module = REPAIRED[rel]
    if rel == "tests/test_story_007_validation.py":
        module.test_no_committed_story_artifact_was_edited()
        return None
    helper = getattr(module, UNCHANGED_HELPER[rel])
    if rel == "tests/test_story_008_validation.py" and subject == ".harness/stories":
        assert helper(subject, diff_filter="MD")
        return None
    assert helper(subject)
    return None


@pytest.mark.parametrize("rel,subject", SUBJECTS)
def test_the_same_violation_is_invisible_to_the_baseline_this_story_removed(
    tmp_path, rel, subject,
):
    """Why every one of these repairs was needed, demonstrated per subject:
    over the identical history, `git diff HEAD` is empty and the honest range
    is not."""
    root = committed_story(tmp_path, rel, subject, violate="modify")
    assert git(root, "diff", "HEAD", "--", subject).strip() == ""
    assert story_diff([subject], validation_file=root / rel,
                      repo=root).strip() != ""


@pytest.mark.parametrize("violation", ["modify", "delete"])
def test_the_narrowed_assertions_still_catch_a_rewritten_execution_record(
    monkeypatch, tmp_path, violation,
):
    """Narrowed to modifications and deletions is not weakened: a committed
    story artifact rewritten or removed inside the story's own run commit is
    still caught, by both narrowed assertions."""
    for rel, module, subject in (
        ("tests/test_story_007_validation.py", REPAIRED[
            "tests/test_story_007_validation.py"], ".harness/stories/"),
        ("tests/test_story_008_validation.py", REPAIRED[
            "tests/test_story_008_validation.py"], ".harness/stories"),
    ):
        root = committed_story(tmp_path, rel, subject, violate=violation,
                               name=f"{Path(rel).stem}-{violation}")
        redirect(monkeypatch, module, root, rel)
        with pytest.raises(AssertionError):
            _assert_unchanged(rel, subject)


def test_the_narrowing_permits_exactly_the_storys_own_new_artifact(
    monkeypatch, tmp_path,
):
    """And what it lets through: an *addition* inside the story's own run
    commit, which was never an edit. The control is the test above — the same
    assertion still fires on a modification or a deletion."""
    for rel, module, subject in (
        ("tests/test_story_007_validation.py", REPAIRED[
            "tests/test_story_007_validation.py"], ".harness/stories/"),
        ("tests/test_story_008_validation.py", REPAIRED[
            "tests/test_story_008_validation.py"], ".harness/stories"),
    ):
        root = committed_story(tmp_path, rel, subject, violate="add",
                               name=f"{Path(rel).stem}-added")
        added = story_diff([subject], validation_file=root / rel, repo=root,
                           diff_filter="A", options=("--name-only",))
        assert added.strip(), "the synthetic story was supposed to add a record"
        redirect(monkeypatch, module, root, rel)
        assert _assert_unchanged(rel, subject) is None


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


def test_the_repaired_assertions_pass_on_this_repository():
    """Run for real, unpatched, against the repository under test."""
    story007.test_no_committed_story_artifact_was_edited()
    assert story008._unchanged_by_story_008("scripts/l5-assist")
    assert story008._unchanged_by_story_008("schemas/story.schema.json")
    assert story008._unchanged_by_story_008(".harness/stories", diff_filter="MD")
    for rel in ("workflows/", "rules/", "schemas/"):
        assert story009._unchanged_by_story_009(rel)
    for rel in ("orchestration/context_assembler.py", "workflows/", "schemas/",
                "rules/", "prompts/"):
        assert story010._unchanged_by_this_story(rel)


# --------------------------------------------------------------------------
# The mechanical check
# --------------------------------------------------------------------------


def pre_repair_source(rel: str) -> str:
    """A repaired file as it stood before this story touched it.

    Resolved through the shared resolution applied to *this* file: HEAD while
    story-015 is in flight, the pre-story revision once it commits. No pinned
    SHA, so a rebase or a squash does not move the answer.
    """
    return repository_file_at(rel, validation_file=Path(__file__),
                              bound=BASELINE, repo=REPO_ROOT)


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


def test_story_011s_validation_file_is_unflagged_and_unchanged_by_this_story():
    """Two names for one module, and each is the name it has where it is read.

    The scan reads the working tree, where story-038 renamed the file to
    `test_execution_history.py`; the diff asks what story-015's own run
    commit did to it, and inside that range it is `test_story_011_validation
    .py`. Asking for either at the other's name reads nothing.
    """
    current = "tests/test_execution_history.py"
    at_story_015 = "tests/test_story_011_validation.py"
    assert check.flagged_calls((REPO_ROOT / current).read_text(encoding="utf-8"),
                               current) == []
    assert story_diff([at_story_015], validation_file=Path(__file__)).strip() == ""


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


TESTER_GUIDANCE = [
    "An assertion that claims an absence needs a negative control",
    "demonstrate that it can fail",
    "tests/conftest.py",
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
def test_this_story_changed_nothing_outside_its_scope(rel):
    assert story_diff([rel], validation_file=Path(__file__)).strip() == ""


def test_the_scope_assertion_above_can_fail(tmp_path):
    """Its control: over a synthetic history where the story does touch
    `orchestration/`, the identical call reports it."""
    rel = "tests/test_story_015_validation.py"
    root = committed_story(tmp_path, rel, "orchestration/", violate="modify")
    assert story_diff(["orchestration/"], validation_file=root / rel,
                      repo=root).strip() != ""


def test_the_archived_story_013_copy_was_not_restored_or_edited():
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
    assert story_diff([str(ARCHIVED_INSTANCE.relative_to(REPO_ROOT))],
                      validation_file=Path(__file__)).strip() == ""


def test_the_suite_still_has_the_tests_the_repaired_files_shipped_with():
    """No test was weakened, skipped or deleted to make the repairs pass."""
    for rel in REPAIRED:
        after = (REPO_ROOT / CURRENT[rel]).read_text(encoding="utf-8")
        assert expected_test_names(CURRENT[rel]) == _test_names(after), rel
        assert "@pytest.mark.skip" not in after, rel
        assert "pytest.skip(" not in after, rel
