"""A test that cannot fail must not count as validation.

Five stories shipped an "X is unchanged" assertion that resolved its
baseline as `git diff HEAD` — the working tree against the last commit.
The coordinator commits the working tree in `_complete`, so on the finished
branch that diff is empty for every path in the repository and the
assertion holds no matter what the story did. story-009's
`test_the_definitions_this_story_injects_are_unchanged` asserted `schemas/`
was unchanged and passed on a branch that added `schemas/manifest.json`.

Prose guidance was tried here and failed: `.harness/docs/ARCHITECTURE.md`
recorded the rule, `.harness/config.yaml` injects that document into every
stage, and `git diff HEAD` was written four more times anyway. So this
module is a mechanical check rather than a paragraph.

What it covers, and only this: a `subprocess` git invocation that targets
*this* repository's root and carries a HEAD-derived revision or a
working-tree status query. It is deliberately narrow. It catches the one
idiom above; it does not catch the general class of vacuous assertions, and
nothing here should be read as claiming it does. An assertion can still be
empty on both sides of an honest baseline, tautological, or aimed at the
wrong subject, and no AST scan will say so — that is what the negative
control now required of absence assertions in `prompts/tester.md` is for.

The regression set is committed evidence rather than a constructed fixture:
the four merged instances are recovered from git history at the revision
preceding this story's own commit, and story-013's is read from the
archived pre-reset copy under `.harness/runs-archive/`.

The second half of this module demonstrates the repairs. An honest baseline
that is always empty for a different reason would be no improvement, so
each repaired subject is violated against a synthetic history — a
repository in which the story *is* committed, which is the state the
repository under test cannot be in while these tests decide whether it
commits — and the shared resolution must report the violation.
"""
import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import (NothingToCompareAgainst, story_commit_range, story_diff)

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: The exemption, stated here rather than inferred from anything. Exactly one
#: module is exempt: the one holding the shared baseline resolution, which is
#: the single place in the suite where comparing the working tree against HEAD
#: is the *correct* answer — it is the right baseline while a story is still
#: in flight, and the resolution exists so no other module has to write it.
#: Every per-story validation file is subject to the check, including the four
#: this story repaired.
EXEMPT_MODULES = ("conftest.py",)

#: Module-level names that stand for the repository under test. A git call
#: pointed at one of these is asking about this repository; a call pointed at
#: a path a test built for itself under tmp_path is not, and is not the check's
#: business.
REPOSITORY_ROOT_NAMES = ("REPO_ROOT", "HARNESS_ROOT")

#: The four files this story repaired, plus the archived fifth instance.
REPAIRED_FILES = (
    "tests/test_story_007_validation.py",
    "tests/test_story_008_validation.py",
    "tests/test_story_009_validation.py",
    "tests/test_story_010_validation.py",
)
ARCHIVED_INSTANCE = (
    ".harness/runs-archive/story-013-vacuous-tests/"
    "pre-reset-test_story_013_validation.py"
)


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    module: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line}: {self.reason}"


def _literal_text(node: ast.AST) -> str | None:
    """The leading literal text of an argument, or None if it has none.

    A plain string yields itself. An f-string yields its literal prefix, so
    `f"HEAD~{n}"` is read as a HEAD-derived revision while `f"{revision}:{path}"`
    — which resolves a revision the test computed — is not.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _names_the_repository_root(node: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Name) and inner.id in REPOSITORY_ROOT_NAMES
        for inner in ast.walk(node)
    )


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Attribute)
            and func.attr in ("run", "check_output")
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess")


def _git_argument_list(node: ast.Call) -> list[ast.expr] | None:
    """The argument list of a git invocation, or None if this is not one."""
    if not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.List) or not first.elts:
        return None
    if _literal_text(first.elts[0]) != "git":
        return None
    return list(first.elts)


def _declared_target(node: ast.Call, elements: list[ast.expr]) -> ast.expr | None:
    """The expression naming where this git call runs, or None if it names none.

    `-C <path>` in the argument list, or the `cwd=` keyword. Which of the two
    is used does not matter; that a target was stated at all does.
    """
    for index, element in enumerate(elements[:-1]):
        if _literal_text(element) == "-C":
            return elements[index + 1]
    for keyword in node.keywords:
        if keyword.arg == "cwd":
            return keyword.value
    return None


def _targets_the_repository_root(node: ast.Call, elements: list[ast.expr]) -> bool:
    """Whether this git call runs against the repository under test.

    Three cases, and the third is why this is not simply "does it say
    REPO_ROOT". A call that states no target inherits the parent process's
    working directory — that is what `subprocess` does, not a guess about
    what the author meant — and pytest runs this suite from the repository
    root. So saying nothing is not neutral: it names this repository by
    default, and the check must read it that way or the dishonest baseline
    the whole module exists to catch simply moves one keyword away.

    The scan never evaluates an expression or reasons about what a variable
    holds. It asks only whether a target was stated, and if so whether it is
    written as one of the two names that stand for this repository. A stated
    target that is anything else is somebody's throwaway repository and is
    not this check's business.
    """
    target = _declared_target(node, elements)
    if target is None:
        return True
    return _names_the_repository_root(target)


def _head_derived(text: str) -> bool:
    return text == "HEAD" or text.startswith(("HEAD:", "HEAD~", "HEAD^"))


def _dishonest_baseline(elements: list[ast.expr]) -> str | None:
    """Why this git invocation resolves a baseline that cannot fail."""
    literals = [_literal_text(element) for element in elements]
    for text in literals:
        if text is not None and _head_derived(text):
            return (f"resolves a baseline as {text!r} against the repository "
                    f"root; the story's own commit becomes HEAD when the "
                    f"coordinator commits the working tree")
    if "status" in literals and "--porcelain" in literals:
        return ("queries the working tree with `status --porcelain` against "
                "the repository root; the answer is empty once the "
                "coordinator commits")
    return None


def flagged_calls(source: str, module: str) -> list[Flag]:
    """Every dishonest git baseline in one module's source.

    Exemptions are not applied here: this is the scan, and a caller that
    means to exempt a module does not scan it. That keeps the exemption a
    stated policy at one call site rather than a condition buried in the
    detector, and it is what lets the regression set below be fed to the
    same function the live suite is held to.
    """
    flags = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        elements = _git_argument_list(node)
        if elements is None or not _targets_the_repository_root(node, elements):
            continue
        reason = _dishonest_baseline(elements)
        if reason is not None:
            flags.append(Flag(module=module, line=node.lineno, reason=reason))
    return flags


def undeclared_targets(source: str, module: str) -> list[Flag]:
    """Every git invocation in one module that does not say where it runs.

    A second, independent rule, and a stricter one: it does not care what the
    call asks for. An implicit target is the ambiguity that let the baseline
    check above be evaded by deleting a keyword, and the same ambiguity would
    return through any other subcommand. Requiring the target to be stated
    removes the question rather than answering it each time.

    No module is exempt. The baseline exemption exists because comparing the
    working tree against HEAD is *correct* in exactly one place; there is
    nowhere that leaving the target unsaid is correct.
    """
    flags = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        elements = _git_argument_list(node)
        if elements is None or _declared_target(node, elements) is not None:
            continue
        flags.append(Flag(
            module=module, line=node.lineno,
            reason=("runs git without saying where: no `-C` and no `cwd=`, so "
                    "it inherits the process working directory, which is this "
                    "repository"),
        ))
    return flags


def scanned_modules() -> list[Path]:
    """Discovered by globbing, never by naming, so a new module is covered
    the moment it lands."""
    return [path for path in sorted(TESTS_DIR.glob("*.py"))
            if path.name not in EXEMPT_MODULES]


def all_modules() -> list[Path]:
    """Every module, including the one the baseline check exempts."""
    return sorted(TESTS_DIR.glob("*.py"))


# --------------------------------------------------------------------------
# The live suite
# --------------------------------------------------------------------------


def test_the_scan_discovers_modules_and_finds_some():
    """The companion assertion the glob needs: a check over zero files
    passes for the wrong reason."""
    modules = scanned_modules()
    assert len(modules) >= 15
    assert all(path.name.endswith(".py") for path in modules)
    assert {path.name for path in modules} >= {
        Path(rel).name for rel in REPAIRED_FILES}


def test_exactly_one_module_is_exempt_and_it_holds_the_shared_resolution():
    assert EXEMPT_MODULES == ("conftest.py",)
    resolution = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "def story_commit_range" in resolution
    assert "def story_diff" in resolution


def test_no_module_in_the_suite_resolves_a_dishonest_baseline():
    flags = [
        flag
        for path in scanned_modules()
        for flag in flagged_calls(path.read_text(encoding="utf-8"), path.name)
    ]
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_no_module_runs_git_without_saying_where():
    """The stricter companion rule, over every module including the exempt one.

    This is what closes the evasion the baseline check had: a call stating no
    target inherits the process working directory, so `git diff HEAD` without
    a `cwd=` asked about this repository while reading as though it asked
    about nothing.
    """
    flags = [
        flag
        for path in all_modules()
        for flag in undeclared_targets(path.read_text(encoding="utf-8"), path.name)
    ]
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_an_undeclared_target_is_flagged_whatever_the_call_asks_for():
    """The rule is about the missing target, not about the subcommand.

    Both sources below are dishonest in the same way and neither names a
    revision, so the baseline check has nothing to say about them; this one
    does.
    """
    benign = "import subprocess\nsubprocess.run(['git', 'status'])\n"
    assert len(undeclared_targets(benign, "probe.py")) == 1
    assert flagged_calls(benign, "probe.py") == []

    declared = "import subprocess\nsubprocess.run(['git', 'status'], cwd=tmp)\n"
    assert undeclared_targets(declared, "probe.py") == []


def test_an_undeclared_target_carrying_head_is_caught_by_both_rules():
    """The hole this closes, stated as a test.

    Before the target became three-valued, dropping `cwd=REPO_ROOT` from a
    `git diff HEAD` call made it invisible to the baseline check while
    changing nothing about what it did.
    """
    evasion = "import subprocess\nsubprocess.run(['git', 'diff', 'HEAD'])\n"
    assert len(flagged_calls(evasion, "probe.py")) == 1
    assert len(undeclared_targets(evasion, "probe.py")) == 1

    elsewhere = ("import subprocess\n"
                 "subprocess.run(['git', 'diff', 'HEAD'], cwd=tmp_path)\n")
    assert flagged_calls(elsewhere, "probe.py") == []


@pytest.mark.parametrize("name", [
    "test_story_005_validation.py",
    "test_story_006_single_reader.py",
    "test_story_007_validation.py",
    "test_story_coordinator.py",
    "test_story_011_validation.py",
])
def test_a_module_that_builds_its_own_repository_is_unflagged(name):
    """Throwaway repositories under tmp_path are not this check's business,
    and story-011's HEAD reference is a positive guard passed to a local
    helper rather than a literal in a git argument list."""
    path = TESTS_DIR / name
    assert flagged_calls(path.read_text(encoding="utf-8"), name) == []


def test_a_throwaway_repository_call_is_unflagged_even_written_with_head():
    """The distinction stated as a control rather than as an absence: the
    same command is flagged against the repository root and ignored against
    a repository the test built."""
    against_a_temp_repo = (
        "import subprocess\n"
        "def probe(root):\n"
        "    subprocess.run(['git', '-C', str(root), 'diff', 'HEAD'])\n"
        "    subprocess.run(['git', 'status', '--porcelain'], cwd=root)\n"
    )
    assert flagged_calls(against_a_temp_repo, "probe.py") == []

    against_this_repo = against_a_temp_repo.replace("root)", "REPO_ROOT)")
    assert len(flagged_calls(against_this_repo, "probe.py")) == 2


def test_the_exemption_is_by_name_and_covers_nothing_else():
    """The exempt module is excluded from the scan; an identical call in any
    other module is not."""
    source = (
        "import subprocess\n"
        "subprocess.run(['git', '-C', str(REPO_ROOT), 'diff', 'HEAD'])\n"
    )
    assert len(flagged_calls(source, "conftest.py")) == 1  # the scan itself is blind
    scanned = {path.name for path in scanned_modules()}
    assert scanned.isdisjoint(EXEMPT_MODULES)
    assert len(scanned) + len(EXEMPT_MODULES) == len(list(TESTS_DIR.glob("*.py")))


# --------------------------------------------------------------------------
# The regression set: five instances, all committed evidence
# --------------------------------------------------------------------------


def _blob(revision: str, rel: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{revision}:{rel}"],
        capture_output=True, text=True, check=True,
    ).stdout


def pre_repair_source(rel: str) -> str:
    """One repaired file as it stood before this story touched it.

    Resolved through the same shared baseline the repairs use: the parent of
    the commit that added *this* module. While this story is in flight that
    is HEAD, and once it commits it is the revision before it — the pre-repair
    text either way, without a pinned SHA that a rebase would invalidate.
    """
    return _blob(story_commit_range(Path(__file__)).baseline, rel)


@pytest.mark.parametrize("rel", REPAIRED_FILES)
def test_the_check_flags_the_pre_repair_version_of_each_merged_instance(rel):
    flags = flagged_calls(pre_repair_source(rel), Path(rel).name)
    assert flags, f"{rel} was expected to carry the idiom before its repair"
    assert all("HEAD" in flag.reason for flag in flags), flags


def test_the_check_flags_story_013s_archived_instance():
    """Read from the archive, which is read-only evidence: story-013's run
    was reset and only its story artifact is on main, awaiting a re-run. Its
    instance is not repaired here — the check catches it when story-013 runs
    again."""
    path = REPO_ROOT / ARCHIVED_INSTANCE
    assert path.is_file()
    flags = flagged_calls(path.read_text(encoding="utf-8"), path.name)
    assert len(flags) >= 4
    reasons = " ".join(flag.reason for flag in flags)
    assert "HEAD" in reasons
    assert "status --porcelain" in reasons


def test_all_five_known_instances_are_caught():
    """The regression set stated as one assertion, so a repair that also
    quietly narrowed the check would show up here."""
    sources = {rel: pre_repair_source(rel) for rel in REPAIRED_FILES}
    sources[ARCHIVED_INSTANCE] = (
        REPO_ROOT / ARCHIVED_INSTANCE).read_text(encoding="utf-8")
    caught = {rel for rel, source in sources.items()
              if flagged_calls(source, Path(rel).name)}
    assert caught == set(sources)


def test_the_repaired_files_no_longer_carry_what_they_carried_before():
    """The other half of the same evidence: flagged before, clean after."""
    for rel in REPAIRED_FILES:
        after = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert flagged_calls(after, Path(rel).name) == [], rel


# --------------------------------------------------------------------------
# The repairs, shown failing when their subject is violated
# --------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
        "-m", message)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def synthetic_story(tmp_path: Path, validation_rel: str, guarded: list[str], *,
                    violate: str | None = None,
                    also_add: str | None = None) -> tuple[Path, Path]:
    """A repository in which a story is already committed.

    Two commits: a pre-story state carrying the guarded paths, then the
    story's own run commit, which adds the validation file and — when
    `violate` says so — modifies, deletes, or adds a guarded path in the
    same commit. This is the state the repository under test cannot be in
    while these tests decide whether it commits, which is exactly why the
    resolution takes a repository parameter.
    """
    root = tmp_path / "synthetic"
    root.mkdir()
    git(root, "init", "-q")
    for rel in guarded:
        write(root, rel, "the pre-story content\n")
    commit(root, "pre-story")

    write(root, validation_rel, "def test_it():\n    assert True\n")
    if violate == "modify":
        write(root, guarded[0], "a later edit\n")
    elif violate == "delete":
        (root / guarded[0]).unlink()
    elif violate == "add":
        write(root, also_add or f"{guarded[0]}.new", "an addition\n")
    commit(root, "the story's own run commit")
    return root, root / validation_rel


#: Every subject the four repaired files assert their story left alone.
REPAIRED_SUBJECTS = [
    ("tests/test_story_007_validation.py", ".harness/stories/story-007.yaml"),
    ("tests/test_story_008_validation.py", "scripts/l5-assist"),
    ("tests/test_story_008_validation.py", "schemas/story.schema.json"),
    ("tests/test_story_009_validation.py", "workflows/story-workflow.json"),
    ("tests/test_story_009_validation.py", "rules/execution-rules.json"),
    ("tests/test_story_009_validation.py", "schemas/story.schema.json"),
    ("tests/test_story_010_validation.py", "orchestration/context_assembler.py"),
    ("tests/test_story_010_validation.py", "prompts/tester.md"),
]


@pytest.mark.parametrize("validation_rel,guarded", REPAIRED_SUBJECTS)
def test_a_repaired_assertion_passes_when_its_subject_is_respected(
    tmp_path, validation_rel, guarded,
):
    root, validation_file = synthetic_story(tmp_path, validation_rel, [guarded])
    assert story_diff([guarded], validation_file=validation_file,
                      repo=root).strip() == ""


@pytest.mark.parametrize("validation_rel,guarded", REPAIRED_SUBJECTS)
def test_a_repaired_assertion_fails_when_its_subject_is_violated(
    tmp_path, validation_rel, guarded,
):
    """The guarantee is not that the assertion passes but that it can fail.
    The story's own run commit edits the path it claims to have left alone,
    and the comparison must say so."""
    root, validation_file = synthetic_story(tmp_path, validation_rel, [guarded],
                                            violate="modify")
    assert story_diff([guarded], validation_file=validation_file,
                      repo=root).strip() != ""


def test_the_same_violation_goes_green_under_the_baseline_this_story_removed(
    tmp_path,
):
    """Why the repairs were worth doing, shown rather than argued: over the
    same history, `git diff HEAD` is empty and the honest range is not."""
    root, validation_file = synthetic_story(
        tmp_path, "tests/test_story_009_validation.py", ["schemas/manifest.json"],
        violate="modify")
    assert git(root, "diff", "HEAD", "--", "schemas/").strip() == ""
    assert story_diff(["schemas/"], validation_file=validation_file,
                      repo=root).strip() != ""


@pytest.mark.parametrize("violation", ["modify", "delete"])
def test_the_narrowed_assertions_still_catch_an_edited_story_artifact(
    tmp_path, violation,
):
    """The two `test_no_committed_story_artifact_was_edited` assertions are
    narrowed to modifications and deletions. Narrowed is not weakened: an
    execution record rewritten or removed in the story's own commit is still
    caught."""
    root, validation_file = synthetic_story(
        tmp_path, "tests/test_story_007_validation.py",
        [".harness/stories/story-001.yaml"], violate=violation)
    assert story_diff([".harness/stories/"], validation_file=validation_file,
                      repo=root, diff_filter="MD",
                      options=("--name-only",)).strip() != ""


def test_the_narrowing_is_exactly_the_storys_own_new_artifact():
    """What the narrowing lets through and nothing more: on this repository,
    story-007's own commit added `.harness/stories/story-007.yaml` and edited
    no other record."""
    validation_file = REPO_ROOT / "tests" / "test_story_007_validation.py"
    added = story_diff([".harness/stories/"], validation_file=validation_file,
                       diff_filter="A", options=("--name-only",)).split()
    assert added == [".harness/stories/story-007.yaml"]
    assert story_diff([".harness/stories/"], validation_file=validation_file,
                      diff_filter="MD", options=("--name-only",)).strip() == ""


# --------------------------------------------------------------------------
# The resolution's edges
# --------------------------------------------------------------------------


def test_the_resolution_returns_the_run_commit_and_its_parent(tmp_path):
    root, validation_file = synthetic_story(
        tmp_path, "tests/test_story_009_validation.py", ["schemas/story.schema.json"])
    resolved = story_commit_range(validation_file, root)
    assert resolved.committed
    assert resolved.endpoint == git(root, "rev-parse", "HEAD").strip()
    assert resolved.baseline == git(root, "rev-parse", "HEAD^").strip()


def test_the_run_commit_is_not_an_earlier_commit_on_the_same_story(tmp_path):
    """A planning or hotfix commit touching the file *modifies* it; only the
    story's own run commit *adds* it, and only additions are considered."""
    root, validation_file = synthetic_story(
        tmp_path, "tests/test_story_009_validation.py", ["schemas/story.schema.json"])
    run_commit = git(root, "rev-parse", "HEAD").strip()
    validation_file.write_text("def test_it():\n    assert 1\n", encoding="utf-8")
    commit(root, "a follow-up hotfix on the same story")
    assert story_commit_range(validation_file, root).endpoint == run_commit


def test_an_uncommitted_validation_file_falls_back_to_the_working_tree(tmp_path):
    """While a story is in flight, the working tree against HEAD *is* the
    correct pre-story baseline."""
    root = tmp_path / "in-flight"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "schemas/story.schema.json", "{}\n")
    commit(root, "pre-story")
    validation_file = write(root, "tests/test_story_099_validation.py", "pass\n")

    resolved = story_commit_range(validation_file, root)
    assert not resolved.committed
    assert resolved.baseline == "HEAD"
    assert story_diff(["schemas/"], validation_file=validation_file,
                      repo=root).strip() == ""

    write(root, "schemas/story.schema.json", "{\"edited\": true}\n")
    assert story_diff(["schemas/"], validation_file=validation_file,
                      repo=root).strip() != ""


def test_the_resolution_raises_when_the_history_does_not_reach_far_enough(tmp_path):
    """A shallow clone has the validation file in HEAD but not the commit
    that added it. Degrading to the working tree there would hand back a
    baseline that makes every caller vacuous, so it raises instead."""
    root, _ = synthetic_story(
        tmp_path, "tests/test_story_009_validation.py", ["schemas/story.schema.json"])
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth", "1", "-q", root.as_uri(), str(shallow)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    validation_file = shallow / "tests" / "test_story_009_validation.py"
    assert validation_file.is_file()

    with pytest.raises(NothingToCompareAgainst) as raised:
        story_commit_range(validation_file, shallow)
    assert "nothing to compare against" in str(raised.value)


def test_the_resolution_raises_when_the_run_commit_has_no_parent(tmp_path):
    """The other way history can fall short: the adding commit is the root
    commit, so there is no pre-story state to compare against."""
    root = tmp_path / "root-commit"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "schemas/story.schema.json", "{}\n")
    validation_file = write(root, "tests/test_story_099_validation.py", "pass\n")
    commit(root, "everything at once")

    with pytest.raises(NothingToCompareAgainst) as raised:
        story_commit_range(validation_file, root)
    assert "nothing to compare against" in str(raised.value)
