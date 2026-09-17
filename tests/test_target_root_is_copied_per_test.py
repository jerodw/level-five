"""Independent validation for story-158: the shared sample target is built
once per workflow name and copied per test.

Written from the story's acceptance criteria rather than from the
implementation. What function scope was buying is isolation — a test that
mutates its target cannot be seen by any other — and this story keeps the
scope while changing how the target is produced, so the questions asked here
are about the copy each test receives and about the template it is copied
from:

  * **the copy.** It lives at `tmp_path / "sample-target"`, holds every path
    the fixture wrote before this story with the same contents, stands on one
    commit, is clean under `git status`, and its `.git` is a directory rather
    than a `gitdir:` pointer.
  * **the isolation.** One test copies, mutates and commits, then inspects the
    template and a second copy taken afterwards. Mutation and check happen in
    the same test so the assertion holds under `-n auto` whichever worker runs
    it and in whatever order.
  * **the process.** Once a template exists, producing a target spawns
    nothing. `subprocess.Popen.__init__` is replaced with a raiser and the
    fixture is resolved through `request.getfixturevalue`, so the assertion is
    on the fixture as pytest runs it rather than on a re-implementation.
  * **the key.** A class overriding `configured_workflow` receives a target
    whose config names that workflow, and every template the session holds
    names the workflow it is keyed under.

Every absence asserted here carries a demonstration that it can fail:

  * "the copy is clean" sits beside the same check on the copy after a file
    is written into it;
  * "the template holds no such file and its HEAD did not move" sits beside
    the same checks on the mutated copy, where both report the mutation;
  * "no process is spawned" sits beside `build_sample_target` under the same
    raiser, which raises on its first git call;
  * "`.git` is a directory" sits beside a worktree added from the copy, whose
    `.git` is the pointer file the assertion exists to rule out.

No test here reads a clock: the saving is a property of the fixture's
structure, and the structure is what is asserted.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import CONFIG, STORY, build_sample_target


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def _commit_count(root: Path) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD").strip())


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _tree_files(root: Path) -> dict[str, bytes]:
    """Every regular file under `root`, keyed by its relative path."""
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _spawn_refused(*args, **kwargs):
    raise AssertionError("a process was spawned on the per-test path")


# The paths `target_root` wrote before this story, with the contents the
# fixture writes for the workflow it is configured for.
def _expected_files(workflow: str) -> dict[str, str]:
    return {
        ".harness/config.yaml": CONFIG.format(workflow=workflow),
        ".harness/stories/story-001.yaml": STORY,
        ".harness/standards/coding.md": "# Coding Standards\n- keep it simple\n",
        ".harness/standards/testing.md": "# Testing Standards\n- test everything\n",
        ".harness/docs/ARCHITECTURE.md": "# Sample Architecture\n",
        "src/app.py": "print('hello')\n",
    }


EXPECTED_DIRECTORIES = (".harness/standards", ".harness/stories", ".harness/runs",
                        ".harness/logs", ".harness/docs", "src")


def test_the_target_is_at_the_path_it_always_had(target_root, tmp_path):
    assert target_root == tmp_path / "sample-target"


def test_the_target_holds_the_tree_the_fixture_wrote(target_root, configured_workflow):
    for relative in EXPECTED_DIRECTORIES:
        assert (target_root / relative).is_dir(), relative
    for relative, contents in _expected_files(configured_workflow).items():
        assert (target_root / relative).read_text(encoding="utf-8") == contents, relative


def test_the_target_stands_on_exactly_one_commit(target_root):
    assert _commit_count(target_root) == 1
    assert _git(target_root, "log", "--format=%s").strip() == "initial"


def test_the_target_is_a_copy_of_the_template_byte_for_byte(target_root, target_template):
    """Including `.git`: the commit is what the clean-tree pre-flight reads."""
    assert _tree_files(target_root) == _tree_files(target_template)
    assert _head(target_root) == _head(target_template)


def test_a_fresh_target_is_clean_under_git_status(target_root):
    assert _git(target_root, "status", "--porcelain") == ""
    # Control: the same check reports a file written into the copy, so an
    # empty porcelain listing above means the copy is clean rather than that
    # the listing is looking at nothing.
    (target_root / "src" / "dirty.py").write_text("pass\n", encoding="utf-8")
    assert "src/dirty.py" in _git(target_root, "status", "--porcelain")


def test_the_targets_git_is_a_directory_not_a_pointer(target_root, tmp_path):
    dot_git = target_root / ".git"
    assert dot_git.is_dir()
    assert not dot_git.is_symlink()
    # Control: a worktree cut from the copy carries the `gitdir:` pointer file
    # the assertion above rules out, and the same predicates say so.
    worktree = tmp_path / "pointer-worktree"
    _git(target_root, "worktree", "add", "-q", str(worktree), "-b", "pointer-probe")
    assert not (worktree / ".git").is_dir()
    assert (worktree / ".git").read_text(encoding="utf-8").startswith("gitdir:")


def test_the_template_is_never_handed_to_a_test(target_root, target_template, tmp_path):
    assert target_root != target_template
    assert target_root.resolve() != target_template.resolve()
    assert tmp_path in target_root.parents
    assert tmp_path not in target_template.parents


def test_target_root_stays_function_scoped(request):
    (definition,) = request._fixturemanager.getfixturedefs("target_root", request.node)
    assert definition.scope == "function"


def test_the_per_test_path_spawns_no_process(request, target_template, monkeypatch, tmp_path,
                                             configured_workflow):
    """Once the template for this workflow exists, producing a target is a copy.

    `target_template` is resolved first so the one-time build has happened;
    the raiser then stands in for every route a process is started by, and
    `target_root` is resolved through pytest itself.
    """
    monkeypatch.setattr(subprocess.Popen, "__init__", _spawn_refused)
    root = request.getfixturevalue("target_root")
    assert root == tmp_path / "sample-target"
    assert (root / ".git").is_dir()
    # Control: the build path does spawn, and the same raiser catches it — so
    # the copy above passed because nothing was spawned, not because the
    # raiser was installed somewhere git does not go.
    with pytest.raises(AssertionError, match="a process was spawned"):
        build_sample_target(tmp_path / "built-under-the-raiser", configured_workflow)


def test_a_mutated_target_leaves_the_template_and_a_later_copy_unchanged(
        target_root, target_template, tmp_path):
    """Mutation and inspection in one test, so no ordering across workers is relied on."""
    template_head_before = _head(target_template)
    template_files_before = _tree_files(target_template)

    (target_root / "src" / "added.py").write_text("print('added')\n", encoding="utf-8")
    _git(target_root, "add", "-A")
    _git(target_root, "commit", "-q", "-m", "mutate the copy")

    # Control: the checks below, applied to the mutated copy, report the
    # mutation — two commits, a moved HEAD, the file present.
    assert _commit_count(target_root) == 2
    assert _head(target_root) != template_head_before
    assert (target_root / "src" / "added.py").exists()
    assert "src/added.py" in _tree_files(target_root)

    # The template is as it was built.
    assert _commit_count(target_template) == 1
    assert _head(target_template) == template_head_before
    assert not (target_template / "src" / "added.py").exists()
    assert _tree_files(target_template) == template_files_before

    # A copy taken afterwards, the way the fixture takes one, is the template.
    later = tmp_path / "later-copy"
    shutil.copytree(target_template, later, symlinks=True)
    assert _commit_count(later) == 1
    assert _head(later) == template_head_before
    assert not (later / "src" / "added.py").exists()
    assert _git(later, "status", "--porcelain") == ""


def test_every_template_in_the_session_names_the_workflow_it_is_keyed_under(
        target_template, configured_workflow, sample_target_templates):
    assert sample_target_templates[configured_workflow] == target_template
    for workflow, template in sample_target_templates.items():
        assert (template / ".harness" / "config.yaml").read_text(encoding="utf-8") == \
            CONFIG.format(workflow=workflow), workflow
    paths = list(sample_target_templates.values())
    assert len(set(paths)) == len(paths)


class TestUnderAnotherWorkflowName:
    """A module that overrides `configured_workflow` gets a target built for it.

    The name is never run: nothing here invokes the coordinator, so no
    definition of that name needs to exist. What is asserted is that the
    target's config names it and that the template it came from is keyed
    under it, separately from the default name's template.
    """

    @pytest.fixture
    def configured_workflow(self) -> str:
        return "template-isolation-probe"

    def test_the_target_is_configured_for_the_overriding_workflow(
            self, target_root, target_template, configured_workflow, sample_target_templates):
        config = (target_root / ".harness" / "config.yaml").read_text(encoding="utf-8")
        assert config == CONFIG.format(workflow=configured_workflow)
        assert config.splitlines()[0] == f"workflow: {configured_workflow}"
        assert sample_target_templates[configured_workflow] == target_template
        assert _commit_count(target_root) == 1
        assert _git(target_root, "status", "--porcelain") == ""
        # Two names never share a template: every template the session holds
        # names the workflow it is keyed under, so the default name's template
        # cannot be the one handed out here and this one cannot be handed out
        # under the default name.
        for workflow, template in sample_target_templates.items():
            assert (template / ".harness" / "config.yaml").read_text(encoding="utf-8") == \
                CONFIG.format(workflow=workflow), workflow
