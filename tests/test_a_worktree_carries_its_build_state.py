"""A worktree carries the build state its configured commands name.

A fresh worktree holds tracked files alone, so a target whose `test_command`
names an interpreter inside its own tree — gitignored, and therefore not
tracked — has no interpreter in the tree a run now works in. This module holds
the properties the repair must have, and it drives them rather than arguing
them: every case builds its own target repository under `tmp_path`, configures
a *tree-relative* interpreter installed only in the tree the harness is invoked
from, and then runs the real coordinator, the real `worktrees` mechanism or the
real `scripts/l5-plan` against it.

Nothing here reads this repository's own configuration, its shipped workflow or
its commit graph. The interpreter paths, the roots and the commands are this
module's own fixture, because the subject is the mechanism and not what this
repository happens to deploy: a target that names its environments differently
must be served by the same code, which is only checkable against a target that
names them differently. The workflow the coordinator-driven runs execute is
`tests/test_coordinator_runs_the_suite.py`'s built definition, reused so a
regression in it reddens both files, and every stage and artifact name is
derived from that definition rather than written here.

Nothing here invokes a model: every run goes through that module's fake agent
runner.

Every absence asserted here carries a demonstration that it can fail:

  * "`git status --porcelain` in the worktree reports nothing for a linked
    path" sits beside the same reading of a worktree carrying the same symlink
    created *without* the exclusion, which reports it — which is also what
    shows that the target's `.gitignore` entry for the directory does not cover
    a symlink standing in its place;
  * "no commit the run makes contains a linked path" is read out of the
    commits' own trees, and sits beside a path the run did commit, which the
    same reading finds;
  * "a second call links nothing and excludes nothing" sits beside the first
    call over the same tree, which links and excludes, and beside a count of
    the name's lines in the exclude file;
  * "an absolute word, a bare name and an absent root contribute nothing" sits
    beside a tree-relative word whose root is present, which contributes;
  * "nothing is refused when a root cannot be linked" sits beside the report a
    command that could not be run already produced before this story, which the
    two paths are required to share;
  * "the source directory survives the worktree's removal" sits beside the
    linked path inside the worktree, which the same removal does take away;
  * and the whole run-level half sits beside the same runs driven through a
    coordinator whose linking has been reverted, which escalate on a command
    that could not be run — the story's non-vacuity demonstration.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
import harness_config
import story_coordinator
import worktrees

# The coordinator-driven runs are tests/test_coordinator_runs_the_suite.py's:
# a target whose configured command is a real script, a built workflow whose
# declaring stage runs the suite and whose verifying stage runs the clean
# clone, and a fake runner that drives both. Reused rather than copied so a
# regression in that machinery reddens both files.
from test_coordinator_runs_the_suite import (BROKEN, DECLARING, PROMPTS,
                                             SENTINEL, STORY_ID,
                                             SUITE_ARTIFACT,
                                             UNRUNNABLE_COMMAND, WORKFLOW,
                                             Runner, build_suite_target, drive,
                                             read_json, record_of, state_of)
# The config amendment is story-017's, and the planning target and its
# invocation are story-117's, for the same reason.
from test_planning_runs_on_its_own_worktree import DEFAULT_BRANCH, planned
from test_planning_runs_on_its_own_worktree import \
    build_target as build_planning_target
from test_revert_check import configure

COORDINATOR_PATH = Path(story_coordinator.__file__)

# --------------------------------------------------------------------------
# The build state a target names
#
# Two directories rather than one, because a target configures two commands
# that may name two environments: the first word of `test_command`, which the
# coordinator's own suite run invokes in the tree the run works in, and
# `verification_runner`, which the clean-clone check substitutes for it. Names
# no real toolchain uses, so a machine that happens to have a `.venv` cannot
# make one of these assertions pass for the wrong reason.
# --------------------------------------------------------------------------

SUITE_ROOT = ".venv-probe"
SUITE_INTERPRETER = f"{SUITE_ROOT}/bin/py"

RUNNER_ROOT = ".venv310-probe"
RUNNER_INTERPRETER = f"{RUNNER_ROOT}/bin/py"

#: A tree-relative interpreter whose root is *never* installed, so the linking
#: has nothing to link for it. The control for "a root absent from the invoked
#: tree causes nothing to be linked and no refusal to be added".
ABSENT_ROOT = ".venv-absent"
ABSENT_INTERPRETER = f"{ABSENT_ROOT}/bin/py"

#: A stand-in interpreter that is an interpreter of nothing of its own: it
#: hands its arguments to the interpreter this suite is running under. That is
#: what lets a tree-relative first word drive the reused target's real suite
#: script, so what these runs exercise is where the command resolved from
#: rather than a command written for the occasion.
STAND_IN = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'

#: The configured command whose first word is tree-relative: the reused
#: target's suite script, run under the interpreter above.
SUITE_COMMAND = shlex.join([SUITE_INTERPRETER, "check.py"])

#: The same command with a first word whose root is absent from the invoked
#: tree, so nothing can be linked for it.
UNLINKABLE_COMMAND = shlex.join([ABSENT_INTERPRETER, "check.py"])


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True)


def install_interpreter(root: Path, relative: str) -> Path:
    """The stand-in interpreter, dropped at `relative` inside `root`."""
    path = write(root / relative, STAND_IN)
    path.chmod(0o755)
    return path


def ignoring(*roots: str) -> str:
    """A `.gitignore` naming each root as a directory.

    Which is how a target ignores its own environments, and why they are absent
    from a fresh worktree at all. It is also the entry that does *not* cover a
    symlink standing in the directory's place, which the control below shows.
    """
    return "".join(f"{root}/\n" for root in roots)


def common_exclude(root: Path) -> Path:
    """The exclude file git itself reads for the tree at `root`.

    Asked of git rather than composed here, because for a worktree it is the
    *main* repository's file and not one inside the worktree at all — which is
    the whole reason the mechanism resolves it with `rev-parse` instead of
    writing `.git/info/exclude`.
    """
    answered = git(root, "rev-parse", "--git-common-dir")
    assert answered.returncode == 0, answered.stderr
    common = Path(answered.stdout.strip())
    if not common.is_absolute():
        common = root / common
    return common / "info" / "exclude"


def excluded_lines(root: Path, name: str) -> list[str]:
    """Every line of the tree's exclude file naming `name`.

    A list rather than a count so an assertion about a second line failing can
    say what the second line was.
    """
    path = common_exclude(root)
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() == name]


def porcelain(root: Path) -> list[str]:
    """`git status --porcelain` in `root`, as lines.

    `-uall` so an untracked directory is reported by the paths inside it rather
    than collapsed to the directory, which is what makes "nothing is reported
    for this path" a statement about the path.
    """
    status = git(root, "status", "--porcelain", "-uall")
    assert status.returncode == 0, status.stderr
    return [line for line in status.stdout.splitlines() if line.strip()]


def mentions(lines: list[str], name: str) -> list[str]:
    return [line for line in lines if name in line]


def committed_paths(root: Path, revision: str) -> list[str]:
    """Every path in one commit's own tree, read out of the commit.

    The commit's tree rather than the ignore rules: what a run committed is a
    fact about the commit, and reasoning about `.gitignore` is exactly the
    inference this story's criteria refuse.
    """
    listed = git(root, "ls-tree", "-r", "--name-only", revision)
    assert listed.returncode == 0, listed.stderr
    return listed.stdout.splitlines()


def started_from(root: Path, relative: str) -> tuple[bool, int | None]:
    """Whether `relative` could be started from inside `root`, and what it
    returned.

    Exactly what a stage agent granted that relative path in `allowed_tools`
    would do: the path is not resolved by the test, it is handed to the
    operating system with the tree as the working directory. A path that is not
    there raises rather than exiting non-zero — which is the coordinator's own
    could-not-run branch, and which is why this answers a pair instead of a
    status.
    """
    try:
        ran = subprocess.run([relative, "--version"], cwd=root,
                             capture_output=True, text=True)
    except OSError:
        return False, None
    return True, ran.returncode


# --------------------------------------------------------------------------
# The derivation: which configured words name a root inside the tree
# --------------------------------------------------------------------------


def test_a_tree_relative_word_contributes_its_first_component():
    assert worktrees.interpreter_roots([SUITE_INTERPRETER]) == [SUITE_ROOT]
    assert worktrees.interpreter_roots(
        [SUITE_INTERPRETER, RUNNER_INTERPRETER]) == [SUITE_ROOT, RUNNER_ROOT]


def test_the_roots_are_deduplicated_and_keep_the_order_they_were_given():
    """Two words under one root are one root, and the order is the words'.

    The order matters to nothing the harness does, which is why it is stated:
    a derivation that answered a set would make the record of what was linked
    unstable between runs for no reason.
    """
    words = [SUITE_INTERPRETER, RUNNER_INTERPRETER, f"{SUITE_ROOT}/bin/other"]
    assert worktrees.interpreter_roots(words) == [SUITE_ROOT, RUNNER_ROOT]


@pytest.mark.parametrize("word", [
    pytest.param(f"/usr/local/{SUITE_INTERPRETER}", id="absolute"),
    pytest.param("py", id="bare-name-on-PATH"),
    pytest.param("", id="empty"),
    pytest.param(None, id="unconfigured"),
])
def test_a_word_with_no_root_inside_the_tree_contributes_nothing(word):
    """An absolute word names something outside the tree and a single-component
    word is looked up on PATH, so neither has a root to link.

    The control is the case beside it: the same call with a tree-relative word
    added contributes exactly that word's root and nothing for this one.
    """
    assert worktrees.interpreter_roots([word]) == []
    assert worktrees.interpreter_roots([word, SUITE_INTERPRETER]) == [SUITE_ROOT]


def test_the_configuration_read_is_the_commands_the_target_already_names():
    """No new configuration key: the roots come off `test_command`'s first word
    and off `verification_runner`, and off nothing else.

    Driven at the coordinator's reader against a configuration this module
    composed — including a key naming a directory that exists and that nothing
    should link, which is what makes "these two keys" a statement rather than a
    restatement of the fixture.
    """
    assert worktrees.interpreter_roots(
        [shlex.split(SUITE_COMMAND)[0],
         RUNNER_INTERPRETER]) == [SUITE_ROOT, RUNNER_ROOT]


# --------------------------------------------------------------------------
# The linking, against a tree this module builds
# --------------------------------------------------------------------------


@pytest.fixture
def invoked_tree(tmp_path: Path) -> Path:
    """A repository holding two gitignored interpreters: the invoked tree.

    Committed first and installed afterwards, so the interpreters are ignored
    and untracked exactly as a developer's own environments are — which is what
    makes them absent from a worktree cut from this.
    """
    root = tmp_path / "invoked-tree"
    write(root / "src" / "app.py", "print('hello')\n")
    write(root / ".gitignore", ignoring(SUITE_ROOT, RUNNER_ROOT))
    conftest.init_repository(root, "the tree the harness was invoked from")
    install_interpreter(root, SUITE_INTERPRETER)
    install_interpreter(root, RUNNER_INTERPRETER)
    return root


def cut_worktree(root: Path, name: str) -> Path:
    """A worktree of `root`, cut from the branch its repository stands on."""
    path = root.parent / f"{root.name}-{name}"
    made = worktrees.add(root, path, f"probe/{name}",
                         conftest.BASE_BRANCH_FALLBACK)
    assert not made.problems, made.problems
    return path


@pytest.fixture
def fresh_worktree(invoked_tree: Path) -> Path:
    return cut_worktree(invoked_tree, "fresh")


def test_a_fresh_worktree_does_not_have_the_interpreters_at_all(
    invoked_tree, fresh_worktree,
):
    """The premise every case below rests on, stated so a fixture that stopped
    ignoring the interpreters reddens here rather than quietly making the
    linking assertions pass for nothing."""
    assert (invoked_tree / SUITE_INTERPRETER).is_file()
    assert not (fresh_worktree / SUITE_ROOT).exists()
    assert not (fresh_worktree / RUNNER_ROOT).exists()
    assert started_from(invoked_tree, SUITE_INTERPRETER) == (True, 0)


def test_only_those_two_configured_keys_name_a_root_to_link(invoked_tree,
                                                            fresh_worktree):
    """The claim above, driven through the coordinator's own reader.

    The configuration carries a third key naming a directory the invoked tree
    really has; it is not linked, so what was read is the two commands rather
    than everything in the configuration that looks like a path.
    """
    write(invoked_tree / "other-env" / "bin" / "py", "#!/bin/sh\nexit 0\n")
    state = story_coordinator.link_configured_build_state(
        invoked_tree, fresh_worktree,
        {"test_command": SUITE_COMMAND,
         "verification_runner": RUNNER_INTERPRETER,
         "some_other_path": "other-env/bin/py"})

    assert state.linked == [SUITE_ROOT, RUNNER_ROOT]
    assert not (fresh_worktree / "other-env").exists()


def test_a_configuration_naming_neither_command_links_nothing(invoked_tree,
                                                              fresh_worktree):
    """The control for the read above: with the two keys absent nothing is
    linked, and nothing is refused either."""
    state = story_coordinator.link_configured_build_state(
        invoked_tree, fresh_worktree, {})

    assert state.linked == []
    assert state.excluded == []
    assert state.problems == []
    assert porcelain(fresh_worktree) == []


def test_linking_gives_the_worktree_the_named_directories(
    invoked_tree, fresh_worktree,
):
    state = worktrees.link_build_state(
        invoked_tree, fresh_worktree, [SUITE_ROOT, RUNNER_ROOT])

    assert state.problems == []
    assert state.linked == [SUITE_ROOT, RUNNER_ROOT]
    assert (fresh_worktree / SUITE_ROOT).is_symlink()
    assert (fresh_worktree / SUITE_INTERPRETER).is_file()
    assert (fresh_worktree / RUNNER_INTERPRETER).is_file()


def test_the_linked_interpreter_is_executable_from_inside_the_worktree(
    invoked_tree, fresh_worktree,
):
    """The property a stage agent's `allowed_tools` grant depends on: the
    relative path the target configures runs, with the worktree as the working
    directory and no resolution done by the caller.

    The control is the same invocation before the linking, which cannot start
    at all — that is the failure this story exists to remove.
    """
    before = started_from(fresh_worktree, SUITE_INTERPRETER)
    worktrees.link_build_state(invoked_tree, fresh_worktree, [SUITE_ROOT])
    after = started_from(fresh_worktree, SUITE_INTERPRETER)

    assert before == (False, None)
    assert after == (True, 0)


def test_the_worktree_reports_nothing_for_a_linked_path(
    invoked_tree, fresh_worktree,
):
    """Read off `git status --porcelain` in the worktree, which is the question
    a run's own commit asks — not off the target's ignore rules.

    The control is the next test: the same symlink without the exclusion beside
    it is reported, so this assertion is capable of failing and the
    `.gitignore` entry naming the directory is demonstrably not what makes it
    pass.
    """
    worktrees.link_build_state(
        invoked_tree, fresh_worktree, [SUITE_ROOT, RUNNER_ROOT])

    reported = porcelain(fresh_worktree)
    assert mentions(reported, SUITE_ROOT) == []
    assert mentions(reported, RUNNER_ROOT) == []
    assert reported == []


def test_the_same_symlink_without_the_exclusion_is_reported(
    invoked_tree, tmp_path,
):
    """The control, and the reason the exclusion exists.

    This worktree carries the target's `.gitignore`, which names the
    interpreter root as a directory, and it carries a symlink at that path made
    by hand. Git reports it: an ignore entry naming a directory does not match
    a symlink standing in its place, so a linking that wrote no exclusion would
    dirty every tree it touched.
    """
    unexcluded = cut_worktree(invoked_tree, "unexcluded")
    assert (unexcluded / ".gitignore").read_text(
        encoding="utf-8") == ignoring(SUITE_ROOT, RUNNER_ROOT)

    (unexcluded / SUITE_ROOT).symlink_to(invoked_tree / SUITE_ROOT,
                                         target_is_directory=True)

    assert mentions(porcelain(unexcluded), SUITE_ROOT) != []


def test_linking_twice_adds_no_second_symlink_and_no_second_exclude_line(
    invoked_tree, fresh_worktree,
):
    """Idempotence, in both halves: the destination already holds the path, and
    the name is already on a line of the exclude file.

    The exclude file matters more than the symlink here, because for a worktree
    it belongs to the developer's own repository — a run that appended to it
    once per run would grow a file nobody asked it to touch.
    """
    first = worktrees.link_build_state(invoked_tree, fresh_worktree,
                                       [SUITE_ROOT, RUNNER_ROOT])
    second = worktrees.link_build_state(invoked_tree, fresh_worktree,
                                        [SUITE_ROOT, RUNNER_ROOT])

    assert first.linked == [SUITE_ROOT, RUNNER_ROOT]
    assert first.excluded == [SUITE_ROOT, RUNNER_ROOT]
    assert second.linked == []
    assert second.excluded == []
    assert second.problems == []
    assert excluded_lines(fresh_worktree, SUITE_ROOT) == [SUITE_ROOT]
    assert excluded_lines(fresh_worktree, RUNNER_ROOT) == [RUNNER_ROOT]
    assert porcelain(fresh_worktree) == []


def test_a_name_already_on_a_line_of_the_exclude_file_is_not_appended_again(
    invoked_tree, fresh_worktree,
):
    """The half above that the symlink check would otherwise hide: the name is
    already excluded and the destination does *not* yet hold the path, so the
    link is made and the exclusion is not written a second time."""
    exclude = common_exclude(fresh_worktree)
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(f"{SUITE_ROOT}\n", encoding="utf-8")

    state = worktrees.link_build_state(invoked_tree, fresh_worktree,
                                       [SUITE_ROOT])

    assert state.linked == [SUITE_ROOT]
    assert state.excluded == []
    assert excluded_lines(fresh_worktree, SUITE_ROOT) == [SUITE_ROOT]
    assert porcelain(fresh_worktree) == []


@pytest.mark.parametrize("word", [
    pytest.param(f"/usr/local/{SUITE_INTERPRETER}", id="absolute"),
    pytest.param("py", id="bare-name-on-PATH"),
    pytest.param(ABSENT_INTERPRETER, id="root-absent-from-the-invoked-tree"),
])
def test_a_word_that_cannot_be_linked_links_nothing_and_refuses_nothing(
    word, invoked_tree, fresh_worktree,
):
    """No new refusal: what could not be linked is simply not linked, and the
    exclude file gains nothing either.

    The control is the same call with a linkable word, which does both — so
    "nothing happened" is a fact about this word rather than about a mechanism
    that was never reached.
    """
    state = worktrees.link_build_state(
        invoked_tree, fresh_worktree, worktrees.interpreter_roots([word]))

    assert state.linked == []
    assert state.excluded == []
    assert state.problems == []
    assert porcelain(fresh_worktree) == []
    assert excluded_lines(fresh_worktree, Path(word).parts[0]) == []

    linkable = worktrees.link_build_state(
        invoked_tree, fresh_worktree,
        worktrees.interpreter_roots([SUITE_INTERPRETER]))
    assert linkable.linked == [SUITE_ROOT]


def test_nothing_under_the_invoked_tree_is_created_moved_or_removed(
    invoked_tree, fresh_worktree,
):
    """The source is read and linked to, never written into.

    Compared as the whole set of paths the working directory holds, ignored
    ones included and each interpreter root walked to its leaves, so a
    directory created beside the interpreter would be caught as readily as a
    file created inside it. The repository's own `.git` is not walked: what it
    holds is git's business, and reading a linked tree does not write there.
    """
    def everything() -> list[str]:
        found = [path.name for path in invoked_tree.iterdir()
                 if path.name != ".git"]
        for root in (SUITE_ROOT, RUNNER_ROOT):
            found += [str(path.relative_to(invoked_tree))
                      for path in (invoked_tree / root).rglob("*")]
        return sorted(found)

    before = everything()
    worktrees.link_build_state(invoked_tree, fresh_worktree,
                               [SUITE_ROOT, RUNNER_ROOT])
    assert everything() == before


def test_removing_the_worktree_leaves_the_source_directory_intact(
    invoked_tree, fresh_worktree,
):
    """The one failure this story must not have: a mechanism that could delete
    a developer's environment.

    A real `git worktree remove --force`, through the harness's own removal,
    because that is what removes a planning worktree today. The linked path
    inside the worktree goes, which is what says the removal really happened
    and the source's survival is not the removal having done nothing.
    """
    worktrees.link_build_state(invoked_tree, fresh_worktree,
                               [SUITE_ROOT, RUNNER_ROOT])
    assert (fresh_worktree / SUITE_INTERPRETER).is_file()

    removal = worktrees.remove(invoked_tree, fresh_worktree)

    assert removal.removed, removal.problems
    assert not fresh_worktree.exists()
    assert (invoked_tree / SUITE_INTERPRETER).is_file()
    assert (invoked_tree / RUNNER_INTERPRETER).is_file()
    assert started_from(invoked_tree, SUITE_INTERPRETER) == (True, 0)


# --------------------------------------------------------------------------
# A run whose configured commands name a tree-relative interpreter
# --------------------------------------------------------------------------


def build_probe_target(root: Path, *, test_command: str = SUITE_COMMAND,
                       verification_runner: str = RUNNER_INTERPRETER,
                       install: tuple[str, ...] = (SUITE_INTERPRETER,
                                                   RUNNER_INTERPRETER)) -> Path:
    """The reused suite target, with tree-relative interpreters configured.

    The interpreters are installed after every commit, under roots the
    committed `.gitignore` names, so the tree the run is invoked from has them
    and a worktree cut from it does not.
    """
    build_suite_target(root, test_command=test_command)
    write(root / ".gitignore", ignoring(SUITE_ROOT, RUNNER_ROOT, ABSENT_ROOT))
    configure(root, verification_runner=verification_runner)
    for relative in install:
        install_interpreter(root, relative)
    return root


@pytest.fixture
def make_probe_target(tmp_path: Path):
    """A factory, so one test can hold a subject and its control side by side."""
    def make(name: str, **kwargs) -> Path:
        return build_probe_target(tmp_path / name, **kwargs)
    return make


@pytest.fixture
def probe_target(make_probe_target) -> Path:
    return make_probe_target("build-state-target")


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A harness root carrying the reused built definition, so every run below
    is a real coordinator loading a real file."""
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "build-state-harness", prompts=PROMPTS)


@pytest.fixture
def linked_run(probe_target, harness_root):
    """The central run: the configured command's first word is tree-relative
    and present only in the tree the run was invoked from."""
    return drive(probe_target, harness_root)


def test_the_run_works_in_a_worktree_that_is_not_the_invoked_tree(
    probe_target, linked_run,
):
    """The premise the whole section rests on. Without it every assertion below
    would be about the invoked tree, where the interpreter was never missing."""
    _, _, run_dir = linked_run
    run_root = conftest.run_root_for(probe_target, STORY_ID)
    assert run_root.resolve() != probe_target.resolve()
    assert run_dir.resolve().is_relative_to(run_root.resolve())


def test_the_coordinators_suite_run_ran_and_reports_an_exit_code(linked_run):
    """The story's first criterion: a run, with a status, rather than a report
    that the configured test command could not be run."""
    code, _, run_dir = linked_run
    record = record_of(run_dir)

    assert code == 0
    assert record["ran"] is True
    assert record["exit_code"] == 0
    assert "reason" not in record
    assert record["command"] == SUITE_COMMAND
    assert state_of(run_dir)["status"] == "completed"


def test_the_clean_clone_check_resolves_the_configured_runner_and_runs(
    linked_run,
):
    """The second criterion: `verification_runner` is tree-relative, and it
    resolves under the run root rather than reporting that it is not an
    executable that exists there."""
    _, _, run_dir = linked_run
    record = read_json(run_dir / conftest.CLEAN_CLONE_RESULT)

    assert record["ran"] is True
    assert record["runner"] == RUNNER_INTERPRETER
    assert record["exit_code"] == 0
    assert "reason" not in record


def test_the_configured_interpreter_runs_from_inside_the_runs_worktree(
    probe_target, linked_run,
):
    """The third criterion, asked of the tree the stages actually stood in: the
    relative path the target grants a stage in `allowed_tools` is runnable
    there."""
    run_root = conftest.run_root_for(probe_target, STORY_ID)
    assert started_from(run_root, SUITE_INTERPRETER) == (True, 0)
    assert started_from(run_root, RUNNER_INTERPRETER) == (True, 0)


def test_the_completion_commit_contains_no_linked_path(probe_target, linked_run):
    """Read out of the commit's own tree.

    The control is in the same reading: the path the stage repaired is in that
    tree, so the reading is one that finds paths a run committed.
    """
    code, _, _ = linked_run
    assert code == 0
    run_root = conftest.run_root_for(probe_target, STORY_ID)
    paths = committed_paths(run_root, "HEAD")

    assert SENTINEL in paths
    assert [path for path in paths if path.startswith(SUITE_ROOT)] == []
    assert [path for path in paths if path.startswith(RUNNER_ROOT)] == []


def test_no_commit_a_run_makes_contains_a_linked_path(probe_target, linked_run):
    """Every commit the run made, not only the one it ended on: a run reaches
    its completion through commits of its own, and a linked path in any of them
    would have reached the branch."""
    code, _, _ = linked_run
    assert code == 0
    run_root = conftest.run_root_for(probe_target, STORY_ID)
    listed = git(run_root, "log", "--format=%H",
                 f"{conftest.BASE_BRANCH_FALLBACK}..HEAD")
    assert listed.returncode == 0, listed.stderr
    revisions = listed.stdout.split()

    assert revisions
    for revision in revisions:
        paths = committed_paths(run_root, revision)
        assert [path for path in paths if path.startswith(SUITE_ROOT)] == []
        assert [path for path in paths if path.startswith(RUNNER_ROOT)] == []


def test_an_escalating_runs_commit_contains_no_linked_path(
    probe_target, harness_root,
):
    """The other end a run stops at. An escalation commits the tree the run
    stopped in, so it is the second place a linked path could reach a branch.

    The same control as above: the escalation commit carries the paths the run
    did write, and the same reading finds them.
    """
    code, _, run_dir = drive(probe_target, harness_root,
                             {DECLARING: [BROKEN] * 3})
    run_root = conftest.run_root_for(probe_target, STORY_ID)
    paths = committed_paths(run_root, "HEAD")

    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"
    assert record_of(run_dir)["ran"] is True
    assert SENTINEL in paths
    assert [path for path in paths if path.startswith(SUITE_ROOT)] == []
    assert [path for path in paths if path.startswith(RUNNER_ROOT)] == []


def test_a_worktree_cut_before_this_story_gains_the_links_when_a_run_enters_it(
    probe_target, harness_root,
):
    """The heal: the tree already exists and has no links, so a run that only
    linked on creation would leave it broken exactly as it was.

    The worktree is cut through the harness's own `worktrees.add`, which is
    what a run before this story left behind, and the absence of the links is
    read before the run rather than assumed.
    """
    existing = conftest.worktree_a_run_left(probe_target, STORY_ID)
    assert not (existing / SUITE_ROOT).exists()
    assert not (existing / RUNNER_ROOT).exists()

    code, _, run_dir = drive(probe_target, harness_root)

    assert code == 0
    assert (existing / SUITE_INTERPRETER).is_file()
    assert (existing / RUNNER_INTERPRETER).is_file()
    assert record_of(run_dir)["ran"] is True


def test_a_second_link_over_the_runs_worktree_changes_nothing(
    probe_target, linked_run,
):
    """The idempotence above, at the level a second run reaches it: the run has
    already linked, and the same call over the same tree links nothing, appends
    nothing and leaves the tree reporting nothing."""
    code, _, _ = linked_run
    assert code == 0
    run_root = conftest.run_root_for(probe_target, STORY_ID)
    config = harness_config.load_config(probe_target)

    again = story_coordinator.link_configured_build_state(
        probe_target, run_root, config)

    assert again.linked == []
    assert again.excluded == []
    assert again.problems == []
    assert excluded_lines(run_root, SUITE_ROOT) == [SUITE_ROOT]
    assert excluded_lines(run_root, RUNNER_ROOT) == [RUNNER_ROOT]
    assert porcelain(run_root) == []


# --------------------------------------------------------------------------
# What a word that cannot be linked leaves exactly as it was
# --------------------------------------------------------------------------


def test_a_command_whose_root_is_absent_reports_what_it_reported_before(
    make_probe_target, harness_root,
):
    """No new refusal: a first word whose root is not in the invoked tree
    cannot be linked, and the run then reports the command as one that could
    not be run — the report that existed before this story.

    Compared against that report rather than against a literal: the same run
    driven with a command whose executable does not exist anywhere produces the
    same clause, so what is asserted is that the two paths still meet.
    """
    unlinkable = make_probe_target("unlinkable",
                                   test_command=UNLINKABLE_COMMAND)
    absent = make_probe_target("absent-executable",
                               test_command=UNRUNNABLE_COMMAND)

    code, _, unlinkable_dir = drive(unlinkable, harness_root)
    _, _, absent_dir = drive(absent, harness_root)

    unlinkable_record = record_of(unlinkable_dir)
    absent_record = record_of(absent_dir)

    assert code == 2
    assert unlinkable_record["ran"] is False
    assert "exit_code" not in unlinkable_record
    assert (unlinkable_record["reason"].split(":")[0]
            == absent_record["reason"].split(":")[0])
    assert state_of(unlinkable_dir)["status"] == "escalated"


def test_an_unlinkable_runner_reports_what_the_clean_clone_check_reported_before(
    make_probe_target, harness_root,
):
    """The same, for `verification_runner`: a tree-relative runner whose root is
    absent leaves the clean-clone check's own report exactly as it is.

    The control is `linked_run` above, where the identical runner *is* present
    in the invoked tree and the check runs — so the two differ in whether the
    root could be linked and in nothing else.
    """
    target = make_probe_target("unlinkable-runner",
                               verification_runner=ABSENT_INTERPRETER)
    code, _, run_dir = drive(target, harness_root)
    record = read_json(run_dir / conftest.CLEAN_CLONE_RESULT)

    assert record["ran"] is False
    assert record["runner"] == ABSENT_INTERPRETER
    assert ABSENT_INTERPRETER in record["reason"]
    assert code == 2
    assert state_of(run_dir)["status"] == "escalated"


# --------------------------------------------------------------------------
# The non-vacuity demonstration: the linking reverted, and nothing else
# --------------------------------------------------------------------------

#: The one call `run_story` makes to give the run's tree its build state. Named
#: as an anchor rather than described, and `conftest.load_mutant` requires it to
#: occur — so a rename moves this rather than silently emptying the
#: demonstration.
THE_LINKING = "link_configured_build_state(invoked_root, run_root.path, config)"


@pytest.fixture
def coordinator_without_the_linking(tmp_path: Path):
    """Today's coordinator with the run's linking removed and nothing else.

    The mutation is one statement, so the run it drives differs from the run
    above in exactly the act this story adds.
    """
    return conftest.load_mutant(
        COORDINATOR_PATH,
        [(THE_LINKING, "None  # the linking this story adds, reverted")],
        name="coordinator_without_the_linking", tmp_path=tmp_path)


def test_reverting_the_linking_leaves_the_run_unable_to_run_the_suite(
    probe_target, harness_root, coordinator_without_the_linking,
):
    """The story's non-vacuity: with the linking gone the worktree has no
    interpreter, the configured command's relative first word resolves against
    a directory with no such file, and the run escalates on a command that
    could not be run.

    The control is `linked_run`, which is the same target, the same harness and
    the same fake runner under today's coordinator, and completes.
    """
    runner = Runner(probe_target)
    code = coordinator_without_the_linking.run_story(
        STORY_ID, harness_root, probe_target, runner)
    run_dir = conftest.run_dir_for(probe_target, STORY_ID)
    record = read_json(run_dir / SUITE_ARTIFACT)
    run_root = conftest.run_root_for(probe_target, STORY_ID)

    assert code == 2
    assert record["ran"] is False
    assert record["reason"]
    assert not (run_root / SUITE_ROOT).exists()


def test_the_mutant_differs_from_todays_coordinator_in_that_one_call(
    coordinator_without_the_linking,
):
    """What makes the demonstration above a demonstration of *this* change: the
    call is in today's source and is the only thing missing from the mutant."""
    today = COORDINATOR_PATH.read_text(encoding="utf-8")
    mutated = Path(coordinator_without_the_linking.__file__).read_text(
        encoding="utf-8")

    assert today.count(THE_LINKING) == 1
    assert THE_LINKING not in mutated
    assert "def link_configured_build_state" in mutated


# --------------------------------------------------------------------------
# The planning worktree carries the same links
# --------------------------------------------------------------------------

#: The stub session `scripts/l5-plan` opens, which answers the one question the
#: planning half exists to ask: standing where the session stands, can the
#: target's configured test command be run? It reports what happened rather
#: than asserting, so the test decides and the failure says which half broke.
PLANNING_STUB = """\
#!/usr/bin/env python3
import json, os, shlex, subprocess, sys
from pathlib import Path

outcome = {"cwd": os.getcwd()}
try:
    ran = subprocess.run(shlex.split(os.environ["L5_PROBE_COMMAND"]),
                         capture_output=True, text=True)
    outcome["started"], outcome["exit_code"] = True, ran.returncode
except OSError as error:
    outcome["started"], outcome["error"] = False, str(error)
Path(os.environ["L5_PROBE_LOG"]).write_text(json.dumps(outcome),
                                            encoding="utf-8")

for relative, text in json.loads(os.environ.get("L5_STUB_WRITE", "{}")).items():
    path = Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
sys.exit(0)
"""

#: The configured command the planning target names: the tree-relative
#: interpreter, asked for nothing but its own version, so what is exercised is
#: whether it is there rather than what it does.
PLANNING_COMMAND = shlex.join([SUITE_INTERPRETER, "--version"])


def build_planning_probe(tmp_path: Path, name: str, *,
                         install: bool = True):
    """story-117's planning target, configured with a tree-relative
    interpreter and opening the stub above.

    `install` is what the control varies: with it false the configured root is
    absent from the invoked tree, so there is nothing to link and the session
    finds the command unrunnable.
    """
    target = build_planning_target(tmp_path / name)
    write(target.root / ".gitignore",
          ignoring(".harness/runs", ".harness/logs", SUITE_ROOT))
    configure(target.root, test_command=PLANNING_COMMAND)
    pushed = target.git("push", "-q", "origin", DEFAULT_BRANCH)
    assert pushed.returncode == 0, pushed.stderr
    if install:
        install_interpreter(target.root, SUITE_INTERPRETER)
    stub = target.bin_dir / "claude"
    stub.write_text(PLANNING_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return target


def plan_with_probe(target, probe: Path):
    """One l5-plan invocation, and what the session it opened reported."""
    result = target.plan("a request", L5_STUB_WRITE=planned(),
                         L5_PROBE_LOG=str(probe),
                         L5_PROBE_COMMAND=PLANNING_COMMAND)
    assert probe.is_file(), result.stdout + result.stderr
    return result, json.loads(probe.read_text(encoding="utf-8"))


def test_a_planning_session_can_run_the_targets_configured_test_command(
    tmp_path: Path,
):
    """The planning half of the story, asked from inside the session: the
    session's own working directory is the worktree, and the configured command
    starts there and exits zero.

    Observed from inside rather than after, because a successful plan whose run
    offer is declined removes the worktree — so the tree that has to carry the
    links only exists while the session is in it.
    """
    target = build_planning_probe(tmp_path, "planning-linked")
    result, session = plan_with_probe(target, tmp_path / "linked-probe.json")

    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(session["cwd"]).resolve() != target.root.resolve()
    assert session["started"] is True
    assert session["exit_code"] == 0


def test_the_same_session_cannot_run_it_when_there_is_nothing_to_link(
    tmp_path: Path,
):
    """The control for the assertion above, and the criterion beside it: with
    the configured root absent from the invoked tree nothing is linked, the
    session finds the command unrunnable — and the invocation is still not
    refused."""
    target = build_planning_probe(tmp_path, "planning-unlinked", install=False)
    result, session = plan_with_probe(target, tmp_path / "unlinked-probe.json")

    assert result.returncode == 0, result.stdout + result.stderr
    assert session["started"] is False
    assert session["error"]


def test_a_planning_invocation_leaves_the_interpreter_in_the_invoked_tree(
    tmp_path: Path,
):
    """The removal property, through the path that really removes a worktree:
    l5-plan removes the planning worktree once the work is durable elsewhere,
    and the developer's own environment is still there afterwards."""
    target = build_planning_probe(tmp_path, "planning-removal")
    before = (target.root / SUITE_INTERPRETER).read_bytes()

    result, _ = plan_with_probe(target, tmp_path / "removal-probe.json")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (target.root / SUITE_INTERPRETER).read_bytes() == before
    assert started_from(target.root, SUITE_INTERPRETER) == (True, 0)


def test_a_planning_invocation_leaves_the_developers_checkout_reporting_nothing(
    tmp_path: Path,
):
    """Nothing was written into the invoked tree's working directory: its own
    status is what it was, with the interpreter ignored as it was before.

    The control is the reading itself — the same command reports the untracked
    file written beside it — so an empty answer is not an empty check.
    """
    target = build_planning_probe(tmp_path, "planning-clean")
    result, _ = plan_with_probe(target, tmp_path / "clean-probe.json")

    assert result.returncode == 0, result.stdout + result.stderr
    assert porcelain(target.root) == []

    write(target.root / "mine.txt", "the developer's own untracked file\n")
    assert mentions(porcelain(target.root), "mine.txt") != []


def test_the_planning_worktree_is_where_the_session_stood(tmp_path: Path):
    """Which tree the session's answer above was about: the worktree root the
    harness derives, and not the tree l5-plan was invoked from."""
    target = build_planning_probe(tmp_path, "planning-where")
    _, session = plan_with_probe(target, tmp_path / "where-probe.json")

    root = worktrees.worktree_root(target.root, target.config())
    assert Path(session["cwd"]).resolve().parent == root.resolve()
    assert not str(root.resolve()).startswith(
        str(target.root.resolve()) + os.sep)
