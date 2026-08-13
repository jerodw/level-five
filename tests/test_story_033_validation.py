"""story-033: the clone is built over git's normal transport.

`_build_clone` is the one place the harness clones the target repository,
and both checks that exist — the clean-clone check and the revert check —
reach it. It used to clone with `--no-hardlinks`, which is still a *local*
clone: git enumerates the source's `.git/objects` as a directory tree and
copies what it enumerated, and anything it cannot read, or that disappears
between the enumeration and the copy, is a hard failure. Three CI runs
failed that way; one of the files git could not copy was
`.git/objects/pack/multi-pack-index.lock`.

`--no-local` makes git negotiate a pack over a pipe instead, so no file in
the source's object store is ever opened by the clone.

Two things about how this is asserted, both deliberate:

  * The transport is read off **the argument list the harness actually hands
    to git**, captured as it runs, not off the text of
    `orchestration/story_coordinator.py`. A module can say `--no-local` in a
    docstring, in a comment, or in a branch nothing takes.
  * "carries no directory-copy flag" is an absence, so it is paired with a
    control: the same capture, over the same fixture, with the module's one
    flag mutated back — and the capture reports `--no-hardlinks` there. A
    capture pointed at nothing would pass the absence assertion just as
    happily.

The regression fixture is the same shape. A source repository holding an
entry under `.git/objects` a directory copy cannot handle stands in for the
lock file CI met; the new command clones it and the previous command fails
on it, both shown in this run, so "the clone succeeds" is a statement about
the transport rather than about a fixture that was never hostile.

Nothing here invokes a model, and every clone source is a filesystem path.
"""
import ast
import os
import subprocess
from pathlib import Path

import pytest

import story_coordinator
from conftest import commit_setup, load_mutant, story_diff

# The end-to-end fixtures come from the story that built the revert check:
# this story changes how a clone is obtained and nothing about what either
# check decides, so the assertions below are driven through the harness's own
# path rather than through a reimplementation of it.
from test_story_019_validation import (  # noqa: F401
    TEST_COMMAND,
    added_coverage,
    capture,
    fixture_and_a_test_that_needs_it,
    forced_repair,
    harness_root,
    record_of,
    run,
    run_dir_of,
    target,
    write,
)
from test_story_019_validation import TESTS_CONFTEST_AT_HEAD, TEST_APP_AT_HEAD

REPO_ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_PATH = Path(story_coordinator.__file__)
COORDINATOR_SOURCE = COORDINATOR_PATH.read_text(encoding="utf-8")

#: The flag that makes git negotiate a pack over a pipe, and the flags that
#: put it back on the directory-copy path. `-l` is git's short form of
#: `--local`; none of the three may appear.
TRANSPORT = "--no-local"
DIRECTORY_COPY_FLAGS = ("--no-hardlinks", "--local", "-l")

#: The mutation every control below uses: today's module with its one
#: transport flag put back to what it was. It is applied to the working-tree
#: source, never to source recovered at a revision.
OLD_COMMAND = (f'"{TRANSPORT}"', '"--no-hardlinks"')

#: The name of the entry planted under the source's object store. The file CI
#: could not copy, by name, so the fixture says what it stands in for.
LOCK = "multi-pack-index.lock"

#: The story's change lands in one file; every absence assertion about what
#: this story did *not* touch is controlled against the diff to this one.
CHANGED_BY_THIS_STORY = "orchestration/story_coordinator.py"

MARKER = "STORY_033_MARKER"


def git(root: Path, *args: str) -> str:
    """A git command in a repository a test built, and its output."""
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def old_command_coordinator(tmp_path: Path):
    """Today's coordinator with the previous clone command, as a module.

    The control's subject. Built through the shared mutation loader, which
    takes a working-tree path: what is being shown is that today's fixture
    fails under the old command, and a module recovered out of history would
    be a different module in more ways than the one under test.
    """
    return load_mutant(COORDINATOR_PATH, [OLD_COMMAND],
                       name="story_033_old_command", tmp_path=tmp_path)


# --------------------------------------------------------------------------
# The argument list the harness runs
# --------------------------------------------------------------------------


@pytest.fixture
def clone_argv(monkeypatch):
    """Every argument list handed to `git clone` while a test runs.

    The spawn itself is intercepted rather than the coordinator's helpers, so
    what is recorded is what the operating system was asked to do — including
    a clone built by the mutant module, which is what makes the control below
    a control on the same instrument.
    """
    seen: list[list[str]] = []
    original = subprocess.run

    def spy(args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and list(args[:2]) == ["git", "clone"]:
            seen.append([str(argument) for argument in args])
        return original(args, *rest, **kwargs)

    monkeypatch.setattr(story_coordinator.subprocess, "run", spy)
    return seen


def test_the_harness_builds_its_clone_over_the_normal_transport(
    target, tmp_path, clone_argv,
):
    """Asserted against the argument list git was given, not the module text."""
    story_coordinator._build_clone(target, tmp_path / "clone")

    assert len(clone_argv) == 1, clone_argv
    argv = clone_argv[0]
    assert TRANSPORT in argv, argv
    for flag in DIRECTORY_COPY_FLAGS:
        assert flag not in argv, argv


def test_the_same_capture_reports_the_flag_the_previous_command_carried(
    target, tmp_path, clone_argv,
):
    """The control for the absence above.

    The capture is the same fixture, the same call and the same instrument;
    only the module's one flag differs. It reports `--no-hardlinks` here, so
    the absence asserted above is a fact about the command rather than about
    a capture that sees nothing.
    """
    old = old_command_coordinator(tmp_path)

    old._build_clone(target, tmp_path / "clone")

    assert len(clone_argv) == 1, clone_argv
    argv = clone_argv[0]
    assert "--no-hardlinks" in argv, argv
    assert TRANSPORT not in argv, argv


def test_the_source_git_is_given_is_a_filesystem_path_and_not_a_url(
    target, tmp_path, clone_argv,
):
    """story-014's criterion, re-asked of the new command's argument list."""
    story_coordinator._build_clone(target, tmp_path / "clone")

    argv = clone_argv[0]
    assert str(target) in argv, argv
    for argument in argv:
        assert "://" not in argument, argv
        assert "git@" not in argument, argv


# --------------------------------------------------------------------------
# A source repository a directory copy cannot handle
# --------------------------------------------------------------------------


@pytest.fixture(params=["an unreadable file", "a dangling symlink"])
def unclonable_source(request, target: Path):
    """A target whose object store holds an entry `--no-hardlinks` cannot copy.

    Both forms stand in for the same thing CI met: a transient file under
    `.git/objects/pack` that the directory-copy path enumerates and then
    cannot read. The lock file itself cannot be reproduced deterministically —
    it exists only while something is writing a multi-pack index — so the
    fixture reproduces its *effect* on the copy, which is what fails.

    The mode is restored on the way out so the temporary directory can be
    removed whatever the test did.
    """
    entry = target / ".git" / "objects" / "pack" / LOCK
    entry.parent.mkdir(parents=True, exist_ok=True)
    if request.param == "an unreadable file":
        entry.write_text("a multi-pack index is being written\n", encoding="utf-8")
        os.chmod(entry, 0o000)
    else:
        entry.symlink_to(target / ".git" / "objects" / "pack" / "gone")
    yield target
    if entry.is_symlink():
        entry.unlink()
    elif entry.exists():
        os.chmod(entry, 0o600)


def test_a_source_a_directory_copy_cannot_read_still_clones(
    unclonable_source, tmp_path,
):
    """The new command never opens the source's object files, so the entry
    that stops a copy is not something it can trip over."""
    clone = tmp_path / "clone"

    story_coordinator._build_clone(unclonable_source, clone)

    assert (clone / "src" / "app.py").is_file()
    assert git(clone, "log", "--oneline").strip()
    assert git(clone, "status", "--porcelain").strip() == ""


def test_the_same_source_fails_under_the_previous_command(
    unclonable_source, tmp_path,
):
    """The fixture is hostile: the command this story removed fails on it.

    Without this the test above would pass against a fixture that was never
    difficult, and "the clone succeeds" would be a statement about nothing.
    """
    old = old_command_coordinator(tmp_path)

    with pytest.raises(RuntimeError) as failure:
        old._build_clone(unclonable_source, tmp_path / "clone")

    assert "Could not clone" in str(failure.value)
    assert not (tmp_path / "clone" / "src" / "app.py").exists()


def test_the_previous_command_clones_the_same_repository_without_that_entry(
    unclonable_source, tmp_path,
):
    """The second control: the mutant module is not simply broken.

    The same source, the same mutant, the planted entry removed — and the old
    command builds the clone. So what fails above is the entry under
    `.git/objects`, which is the thing this story is about.
    """
    entry = unclonable_source / ".git" / "objects" / "pack" / LOCK
    if not entry.is_symlink():
        os.chmod(entry, 0o600)
    entry.unlink()
    old = old_command_coordinator(tmp_path)

    old._build_clone(unclonable_source, tmp_path / "clone")

    assert (tmp_path / "clone" / "src" / "app.py").is_file()


# --------------------------------------------------------------------------
# One clone site, and no remote literal in the module
# --------------------------------------------------------------------------


def clone_sites(source: str) -> list[str]:
    """The top-level functions in one module that invoke `git clone`.

    Read off the argument lists rather than off the text, so a docstring
    naming the command is not a second cloner and a list literal is.
    """
    named = []
    for function in ast.parse(source).body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.List) or len(node.elts) < 2:
                continue
            words = [element.value for element in node.elts[:2]
                     if isinstance(element, ast.Constant)]
            if words == ["git", "clone"]:
                named.append(function.name)
                break
    return named


#: A second cloner, planted in the module's text for the control below. Not
#: written to disk and not executed: the question is what the scan reports.
A_SECOND_CLONER = '''

def _build_another_clone(target_root, clone):
    subprocess.run(["git", "clone", str(target_root), str(clone)])
'''


def test_exactly_one_function_in_the_coordinator_invokes_git_clone():
    assert clone_sites(COORDINATOR_SOURCE) == ["_build_clone"]


def test_the_same_scan_reports_a_second_cloner_when_there_is_one():
    """The control: the scan is looking where it claims to look."""
    assert clone_sites(COORDINATOR_SOURCE + A_SECOND_CLONER) == [
        "_build_clone", "_build_another_clone"]


def url_constants(source: str) -> list[str]:
    """Every string constant in a module that spells a remote."""
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and ("://" in node.value or "git@" in node.value)]


def test_no_string_constant_in_the_coordinator_carries_a_remote():
    """story-014's assertion, restated here because this story rewrote the
    docstring the clone lives under: prose about transports is exactly where
    a URL would arrive by accident."""
    assert url_constants(COORDINATOR_SOURCE) == []


@pytest.mark.parametrize("planted", ['\nREMOTE = "https://example.invalid/x"\n',
                                     '\nREMOTE = "git@example.invalid:x"\n'])
def test_the_same_scan_reports_a_remote_when_there_is_one(planted):
    """The control for the absence above."""
    assert url_constants(COORDINATOR_SOURCE + planted) == [planted.split('"')[1]]


# --------------------------------------------------------------------------
# What the clone carries is unchanged
# --------------------------------------------------------------------------


@pytest.fixture
def dirty_target(target: Path) -> Path:
    """A target carrying an uncommitted story, with the ignore rules the real
    repository has: a gitignored directory and the run directory."""
    (target / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\nignored/\n.harness/runs/\n", encoding="utf-8")
    commit_setup(target, "ignore rules for this test")

    write(target / "src" / "app.py", f'def greet(name):\n    return "{MARKER}"\n')
    write(target / "probe.txt", "probe\n")
    write(target / "ignored" / "secret.txt", "secret\n")
    write(target / ".harness" / "runs" / "story-001" / "state.json", "{}\n")
    return target


def test_the_clone_carries_the_working_tree_as_a_commit_and_is_clean(
    dirty_target, tmp_path,
):
    clone = tmp_path / "clone"

    story_coordinator._build_clone(dirty_target, clone)

    assert MARKER in git(clone, "show", "HEAD:src/app.py")
    assert git(clone, "show", "HEAD:probe.txt").strip() == "probe"
    assert git(clone, "status", "--porcelain").strip() == ""
    assert git(clone, "log", "--oneline").count("\n") >= 2


def test_no_gitignored_file_and_no_run_directory_reaches_the_clone(
    dirty_target, tmp_path,
):
    """An absence, with the control beside it.

    The control is `probe.txt`: an untracked file the ignore rules do *not*
    exclude, which reaches the clone by the same copy the two absent paths
    would have used. So the clone is receiving untracked files — the
    assertion is about what the ignore rules exclude and not about a clone
    that carries nothing, or a source that never held them.
    """
    clone = tmp_path / "clone"

    story_coordinator._build_clone(dirty_target, clone)

    assert (dirty_target / "ignored" / "secret.txt").is_file()
    assert (dirty_target / ".harness" / "runs" / "story-001" / "state.json").is_file()
    assert not (clone / "ignored").exists()
    assert not (clone / ".harness" / "runs").exists()
    assert (clone / "probe.txt").is_file()


def test_the_target_repository_is_only_read(dirty_target, tmp_path):
    before = (git(dirty_target, "rev-parse", "HEAD"),
              git(dirty_target, "status", "--porcelain"),
              git(dirty_target, "branch", "--format=%(refname)"),
              git(dirty_target, "stash", "list"),
              git(dirty_target, "diff", "--cached"))

    story_coordinator._build_clone(dirty_target, tmp_path / "clone")

    assert (git(dirty_target, "rev-parse", "HEAD"),
            git(dirty_target, "status", "--porcelain"),
            git(dirty_target, "branch", "--format=%(refname)"),
            git(dirty_target, "stash", "list"),
            git(dirty_target, "diff", "--cached")) == before


# --------------------------------------------------------------------------
# Both checks still reach their verdicts through the new command
# --------------------------------------------------------------------------


def test_a_forced_edit_still_reaches_the_permitted_verdict(target, harness_root):
    """End to end: a rename the pre-existing test cannot survive."""
    code, _ = run(target, harness_root, {"implementer": [forced_repair]})

    assert code == 0
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is True
    assert record["paths"] == ["tests/test_app.py"]


def test_an_unforced_edit_still_reaches_the_refused_verdict(target, harness_root):
    """The control for the verdict above: the same run, an edit nothing forced,
    and the opposite decision. A check that permitted everything would be no
    check."""
    code, _ = run(target, harness_root, {"implementer": [added_coverage]})

    assert code == 2
    record = record_of(target)
    assert record["ran"] is True
    assert record["permitted"] is False
    assert record["paths"] == ["tests/test_app.py"]


def test_a_governed_path_is_restored_from_the_baseline_inside_the_clone(
    target, tmp_path,
):
    """The restore, in a clone built the new way.

    The control is the second clone: the same builder with nothing reverted
    carries the edit, so this says the revert happened rather than that the
    clone never saw the change.
    """
    baseline = capture(target, tmp_path / "before")
    forced_repair(target, run_dir_of(target))

    story_coordinator._build_clone(target, tmp_path / "reverted",
                                   revert=["tests/test_app.py"], baseline=baseline)
    story_coordinator._build_clone(target, tmp_path / "intact")

    assert git(tmp_path / "reverted", "show",
               "HEAD:tests/test_app.py") == TEST_APP_AT_HEAD
    assert git(tmp_path / "intact", "show",
               "HEAD:tests/test_app.py") != TEST_APP_AT_HEAD
    # Everything outside the reverted path is the working tree in both.
    for clone in ("reverted", "intact"):
        assert "salute" in git(tmp_path / clone, "show", "HEAD:src/app.py")


def test_a_governed_path_the_baseline_lacks_is_deleted_inside_the_clone(
    target, tmp_path,
):
    """The delete half, in a clone built the new way.

    The control sits in the same clone: the path the baseline *does* hold is
    present and restored, so the deletion is about absence from the baseline
    and not about the revert emptying the prefix.
    """
    baseline = capture(target, tmp_path / "before")
    fixture_and_a_test_that_needs_it(target, run_dir_of(target))

    story_coordinator._build_clone(
        target, tmp_path / "reverted",
        revert=["tests/conftest.py", "tests/test_uses_fixture.py"],
        baseline=baseline)
    committed = git(tmp_path / "reverted", "ls-files").split()

    assert "tests/test_uses_fixture.py" not in committed
    assert not (tmp_path / "reverted" / "tests" / "test_uses_fixture.py").exists()
    assert "tests/conftest.py" in committed
    assert git(tmp_path / "reverted", "show",
               "HEAD:tests/conftest.py") == TESTS_CONFTEST_AT_HEAD


def test_the_clone_keeps_the_source_history_rather_than_shallowing_it(
    target, tmp_path,
):
    """The transport can shallow a clone; this one does not.

    The checks exist so a test resolving a baseline out of git history sees
    what it would see where the code ships, and a shallow clone would not.
    """
    write(target / "src" / "app.py", f'def greet(name):\n    return "{MARKER}"\n')
    commit_setup(target, "a second commit in the source")
    clone = tmp_path / "clone"

    story_coordinator._build_clone(target, clone)

    assert not (clone / ".git" / "shallow").exists()
    source_commits = git(target, "rev-list", "--count", "HEAD").strip()
    # The clone holds one more: the working tree, committed by the builder.
    assert git(clone, "rev-list", "--count", "HEAD").strip() \
        == str(int(source_commits) + 1)


# --------------------------------------------------------------------------
# What this story did not touch
# --------------------------------------------------------------------------


def story_range_diff(*paths: str) -> str:
    """This story's own diff to `paths`, bounded at both ends of its range."""
    return story_diff(list(paths), validation_file=Path(__file__))


def test_no_file_under_github_is_changed_by_this_story():
    """The CI retry and the fail-fast setting are a backstop for the next
    unknown, not this story's fix, and this story leaves them alone."""
    assert story_range_diff(".github/") == ""


def test_the_same_comparison_reports_the_file_this_story_did_change():
    """The control: the baseline resolution is bounded at this story's own
    range and is looking at this story. Without it the emptiness above would
    hold just as well for a comparison of a commit with itself."""
    assert CHANGED_BY_THIS_STORY in story_range_diff(CHANGED_BY_THIS_STORY)
    assert "--no-local" in story_range_diff(CHANGED_BY_THIS_STORY)


def test_the_three_named_ci_tests_are_not_modified_by_this_story():
    """They pass unmodified because they are unmodified: the module holding
    all three is untouched by this story's range, and the suite this stage
    ran includes it."""
    assert story_range_diff("tests/test_story_014_validation.py") == ""


def commits_saying(phrase: str) -> list[str]:
    """The subjects of commits whose message carries `phrase`.

    A search rather than a pinned revision: the point is that the record
    still says what it said, which survives a rebase, and a pinned SHA would
    not.
    """
    found = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--format=%s", f"--grep={phrase}"],
        capture_output=True, text=True, check=True).stdout
    return [line for line in found.splitlines() if line.strip()]


def test_the_commits_describing_the_cause_as_unestablished_still_say_so():
    """This story supersedes them in the record instead of rewriting them."""
    subjects = commits_saying("cause is not established")

    assert len(subjects) >= 2, subjects
    assert "CI: retry only the failed tests once, and only in CI" in subjects
    assert "CI: let every Python version finish when one fails" in subjects


def test_the_same_search_finds_nothing_when_the_phrase_is_not_there():
    """The control: the search reads the history rather than reporting
    whatever it is asked for."""
    assert commits_saying("this phrase is in no commit message") == []
