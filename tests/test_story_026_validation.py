"""story-026: one resolution of a story's own commit range, not two.

story-015 put the resolution of a story's own commit range in
`tests/conftest.py`. `tests/test_story_011_validation.py` kept a second one —
`story_revision()`, which walked the coordinator's own history for the oldest
revision carrying the marker string "execution-history". Both resolved the
same pair. This story deletes the local one and points its single caller at
the shared one.

Nothing here reads the two edits and agrees with them. What is validated is:

1. That exactly one resolution survives under `tests/`, by a scan that is
   shown reporting a reintroduced copy placed in front of it — and by
   committed evidence, since the pre-story text of the very file this story
   edited carries the copy the scan must catch.
2. That the rewritten assertion still *can fail*. The guarantee is not that
   `test_no_prompt_template_was_changed_by_this_story` passes — it passed
   before — but that a story's own run commit touching `prompts/`,
   `workflows/` or `rules/` still turns it red. Each is driven against a
   synthetic history whose run commit violates the path it names, with the
   module's own code unmodified and only the repository it asks about moved.
3. That the edit to `tests/test_story_016_validation.py` removed exactly one
   entry, and that the assertions it left behind still fail when their
   subject is violated.
4. That the two mechanisms resolved the *same commits* before the fold, so
   this is a change of mechanism rather than of subject. The deleted helper
   is recovered from git history and run beside the shared one.

Every absence asserted below carries a control that constructs the violation
and shows the same check reporting it. The pre-story text recovered in
`pre_story_source` is the recurring one; `test_the_recovered_pre_story_sources
_are_the_pre_story_sources` is what keeps that recovery from going quietly
empty and taking every control with it.
"""
import ast
import functools
import subprocess
from pathlib import Path

import pytest

import conftest
import test_baseline_honesty as honesty
import test_story_011_validation as story011
import test_story_016_validation as story016
from conftest import story_commit_range, story_diff

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

STORY_011_REL = "tests/test_story_011_validation.py"
STORY_016_REL = "tests/test_story_016_validation.py"

#: The three paths the surviving prompt-scope assertion guards. Not trusted:
#: `test_the_rewritten_assertion_guards_the_paths_it_guarded_before` reads them
#: out of the function's own source, before and after, and holds this to them.
GUARDED = ("prompts/", "workflows/", "rules/")

#: The helper this story deletes, and the shared names its caller moved onto.
FOLDED_AWAY = "story_revision"
SHARED_NAMES = ("story_commit_range", "story_diff")

#: Byte-for-byte survivors named by the acceptance criteria.
SURVIVING_IN_STORY_011 = (
    "coordinator_source_at",
    "pre_story_revision",
    "pre_story_coordinator_source",
    "test_the_comparison_baseline_is_not_this_implementation",
    "test_the_baseline_stays_pre_story_once_this_story_is_committed",
    "test_the_baseline_resolution_fails_loudly_when_there_is_nothing_older",
)


# --------------------------------------------------------------------------
# Committed evidence: the tree this story started from
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def pre_story_source(rel: str) -> str:
    """One file as it stood before this story touched it.

    Resolved through the shared resolution applied to *this* file: HEAD while
    story-026 is in flight, the parent of this story's own run commit once it
    commits. No pinned SHA, so a rebase or a squash does not move the answer.
    """
    baseline = story_commit_range(Path(__file__)).baseline
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{baseline}:{rel}"],
        capture_output=True, text=True, check=True,
    ).stdout


def current_source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_the_recovered_pre_story_sources_are_the_pre_story_sources():
    """Every control below leans on this recovery, so it is asserted rather
    than assumed: the pre-story text differs from today's, and it carries the
    helper this story deletes."""
    before_011 = pre_story_source(STORY_011_REL)
    assert before_011 != current_source(STORY_011_REL)
    assert f"def {FOLDED_AWAY}" in before_011
    before_016 = pre_story_source(STORY_016_REL)
    assert before_016 != current_source(STORY_016_REL)
    assert f'"def {FOLDED_AWAY}"' in before_016


# --------------------------------------------------------------------------
# One resolution survives
# --------------------------------------------------------------------------


SPAWNING_CALLS = ("run", "check_output", "Popen", "call", "check_call")
REPOSITORY_ROOT_NAMES = ("REPO_ROOT", "HARNESS_ROOT")


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _git_argument_list(node: ast.Call) -> list[ast.expr] | None:
    if not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.List) or not first.elts:
        return None
    if _literal_text(first.elts[0]) != "git":
        return None
    return list(first.elts)


def _targets_the_repository_root(node: ast.Call, elements: list[ast.expr]) -> bool:
    """Whether this git call runs against the repository under test.

    Read the same way `tests/test_baseline_honesty.py` reads it: a `-C` or a
    `cwd=` naming one of the module-level names that stand for this
    repository, or no stated target at all — which inherits pytest's working
    directory and so names this repository by default.
    """
    target = None
    for index, element in enumerate(elements[:-1]):
        if _literal_text(element) == "-C":
            target = elements[index + 1]
            break
    else:
        for keyword in node.keywords:
            if keyword.arg == "cwd":
                target = keyword.value
                break
    if target is None:
        return True
    return any(isinstance(inner, ast.Name) and inner.id in REPOSITORY_ROOT_NAMES
               for inner in ast.walk(target))


def range_resolutions(source: str) -> list[int]:
    """Every line where a module bounds a diff of this repository itself.

    What this catches, and only this: a `subprocess` git invocation aimed at
    the repository under test whose subcommand is `diff`, or which passes a
    `--diff-filter`. That is the shape of a story's own commit range being
    resolved and compared locally — it is what the deleted `story_revision()`
    existed to feed, and it is what `conftest.story_diff` now does on every
    caller's behalf. A module that asks git for *text* (`show`) or walks a
    log for a revision it hands to `show` is not doing this; that is what
    `pre_story_coordinator_source` does, and this story deliberately leaves
    it alone.

    Deliberately narrow, and the narrowness is why the name-level check below
    stands beside it rather than being folded into it.
    """
    found = []
    tree = ast.parse(source)
    imported = {alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
                for alias in node.names if alias.name in SPAWNING_CALLS}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        spawns = (func.attr in SPAWNING_CALLS if isinstance(func, ast.Attribute)
                  else isinstance(func, ast.Name) and func.id in imported)
        if not spawns:
            continue
        elements = _git_argument_list(node)
        if elements is None or not _targets_the_repository_root(node, elements):
            continue
        literals = [_literal_text(element) for element in elements]
        if "diff" in literals or any(
                text is not None and text.startswith("--diff-filter")
                for text in literals):
            found.append(node.lineno)
    return found


def test_the_scan_reports_a_reintroduced_copy():
    """The control for the absence below, constructed rather than observed.

    A module carrying the copy this story removed — a revision resolved out
    of git history and handed to a diff of this repository — is reported. The
    same module with its diff aimed at a repository it built for itself is
    not, so the scan is reading the target and has not simply stopped seeing
    git calls.
    """
    reintroduced = (
        "import subprocess\n"
        "def story_revision():\n"
        "    return subprocess.run(['git', '-C', str(REPO_ROOT), 'log',\n"
        "                           '--format=%H'], capture_output=True).stdout\n"
        "def test_nothing_was_touched():\n"
        "    result = subprocess.run(['git', '-C', str(REPO_ROOT), 'diff',\n"
        "                             baseline, story_revision(), '--', 'prompts/'],\n"
        "                            capture_output=True)\n"
        "    assert result.stdout == ''\n"
    )
    assert range_resolutions(reintroduced)
    assert range_resolutions(reintroduced.replace("str(REPO_ROOT)", "str(root)")) == []


def test_the_scan_reports_the_copy_this_story_removed():
    """The second control, and this one is committed evidence rather than a
    construction: the pre-story text of the file this story edited carried
    exactly the copy the scan must catch."""
    assert range_resolutions(pre_story_source(STORY_011_REL))


def test_only_conftest_resolves_a_storys_own_commit_range():
    """The absence, over every module under tests/.

    `tests/conftest.py` is where the resolution lives; it is exempt here for
    the same reason `test_baseline_honesty.EXEMPT_MODULES` exempts it, and
    the assertion below checks it really still holds the resolution rather
    than taking the exemption on trust.
    """
    def sweep(sources: dict[str, str]) -> dict[str, list[int]]:
        return {name: lines for name, lines in
                ((name, range_resolutions(text)) for name, text in sources.items())
                if lines}

    sources = {path.name: path.read_text(encoding="utf-8")
               for path in sorted(TESTS_DIR.glob("*.py"))
               if path.name != "conftest.py"}
    assert sweep(sources) == {}, sweep(sources)
    assert len(sources) >= 15

    # The control, over the same sweep rather than over one file: put the
    # pre-story text of story-011's module back in front of it and the sweep
    # names that module. Without this, a sweep that had stopped seeing git
    # calls would report the same clean result.
    reintroduced = {**sources,
                    Path(STORY_011_REL).name: pre_story_source(STORY_011_REL)}
    assert set(sweep(reintroduced)) == {Path(STORY_011_REL).name}

    resolution = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    for name in SHARED_NAMES:
        assert f"def {name}" in resolution, name


def top_level_names(source: str) -> set[str]:
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
    return names


def test_no_module_under_tests_defines_the_folded_helper():
    """The name-level absence the scan above cannot express, with the same
    scan run over committed evidence as its control: defined, not mentioned —
    this file names `story_revision` and must not be caught by its own check.
    """
    sources = {path.name: path.read_text(encoding="utf-8")
               for path in sorted(TESTS_DIR.glob("*.py"))
               if path.name != Path(__file__).name}
    defining = {name for name, text in sources.items()
                if FOLDED_AWAY in top_level_names(text)}
    assert defining == set(), defining

    # The control, over the same sweep: the pre-story text of story-011's
    # module put back in front of it is named.
    reintroduced = {**sources,
                    Path(STORY_011_REL).name: pre_story_source(STORY_011_REL)}
    assert {name for name, text in reintroduced.items()
            if FOLDED_AWAY in top_level_names(text)} == {
                Path(STORY_011_REL).name}


def test_story_011_now_imports_the_shared_resolution():
    """The positive counterpart: the caller did not lose its bounds, it moved
    onto the shared ones."""
    tree = ast.parse(current_source(STORY_011_REL))
    imported = {alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "conftest"
                for alias in node.names}
    assert "story_diff" in imported
    assert "commit_setup" in imported, "the existing import was not preserved"


# --------------------------------------------------------------------------
# The two mechanisms resolved the same commits
# --------------------------------------------------------------------------


PRE_STORY_HELPERS = ("COORDINATOR_REPO_PATH", "coordinator_source_at",
                     "pre_story_revision", FOLDED_AWAY)


def _lift(source: str, names: tuple[str, ...]) -> str:
    """The named top-level definitions of a module, on their own.

    Lifted rather than imported: the module they come from no longer exists
    in the working tree, and the point is to run *the deleted code*, not a
    restatement of it.
    """
    tree = ast.parse(source)
    kept = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in names)
            or (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in names
                        for target in node.targets))]
    assert len(kept) == len(names), [getattr(n, "name", n) for n in kept]
    return ast.unparse(ast.Module(body=kept, type_ignores=[]))


@functools.lru_cache(maxsize=None)
def deleted_mechanism() -> dict:
    namespace = {"subprocess": subprocess, "Path": Path, "REPO_ROOT": REPO_ROOT}
    exec(_lift(pre_story_source(STORY_011_REL), PRE_STORY_HELPERS), namespace)
    return namespace


def test_the_deleted_mechanism_and_the_shared_one_resolve_the_same_pair():
    """The fold is a change of mechanism, not of subject — shown by running
    both against this repository and comparing the commits they name.

    The deleted helper is recovered from history and executed, so this is a
    comparison of two answers rather than of two descriptions.
    """
    shared = story_commit_range(REPO_ROOT / STORY_011_REL)
    deleted = deleted_mechanism()
    assert deleted[FOLDED_AWAY]() == shared.endpoint
    assert deleted["pre_story_revision"]() == shared.baseline
    # And the pair is a real range: the two ends are different commits and the
    # baseline is the endpoint's parent.
    assert shared.baseline != shared.endpoint
    parent = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", f"{shared.endpoint}^"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert parent == shared.baseline


# --------------------------------------------------------------------------
# Synthetic histories, and the surviving assertion shown failing
# --------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD").strip()


def committed_story(tmp_path: Path, validation_rel: str, guarded: str, *,
                    violate: bool = False, name: str = "synthetic") -> Path:
    """A repository in which one story has already run and committed.

    Commit 1 is the pre-story state carrying the guarded path. Commit 2 is the
    story's own run commit: it adds the validation file and, when `violate`
    says so, rewrites the guarded path in the same commit. That is the shape
    of a finished branch — the shape under which `git diff HEAD` reports
    nothing no matter what the story did, and the shape this repository cannot
    be in while these tests decide whether it commits.
    """
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q")
    subject = f"{guarded.rstrip('/')}/kept.md"
    write(root, subject, "the pre-story content\n")
    commit(root, "pre-story")

    write(root, validation_rel, "def test_it():\n    assert True\n")
    write(root, "unrelated.txt", "the story's own legitimate change\n")
    if violate:
        write(root, subject, "rewritten inside the story's own run commit\n")
    commit(root, "the story's own run commit")
    return root


def redirect(monkeypatch, module, root: Path, validation_rel: str) -> None:
    """Point one module's assertions at a synthetic repository.

    The module's own test body runs unmodified — only the repository it asks
    about moves. That is what makes the failure below a property of the
    rewritten assertion rather than of a reimplementation of it.
    """
    real = conftest.story_diff

    def patched(paths, *, validation_file=None, repo=None, **kwargs):
        return real(paths, validation_file=root / validation_rel, repo=root,
                    **kwargs)

    monkeypatch.setattr(module, "story_diff", patched)


@pytest.mark.parametrize("guarded", GUARDED)
def test_the_rewritten_assertion_passes_when_its_story_respects_its_subject(
    monkeypatch, tmp_path, guarded,
):
    """Attribution: the failure in the next test is the violation, not the
    redirect."""
    root = committed_story(tmp_path, STORY_011_REL, guarded,
                           name=f"clean-{guarded.strip('/')}")
    redirect(monkeypatch, story011, root, STORY_011_REL)
    assert story011.test_no_prompt_template_was_changed_by_this_story() is None


@pytest.mark.parametrize("guarded", GUARDED)
def test_the_rewritten_assertion_fails_when_its_story_violates_its_subject(
    monkeypatch, tmp_path, guarded,
):
    """The guarantee this story has to keep. Not that the assertion passes —
    it passed before the fold and it passes after — but that a story's own run
    commit touching one of the three paths it guards still turns it red."""
    root = committed_story(tmp_path, STORY_011_REL, guarded, violate=True,
                           name=f"violating-{guarded.strip('/')}")
    redirect(monkeypatch, story011, root, STORY_011_REL)
    with pytest.raises(AssertionError):
        story011.test_no_prompt_template_was_changed_by_this_story()


@pytest.mark.parametrize("guarded", GUARDED)
def test_the_same_violation_is_invisible_to_a_head_baseline(tmp_path, guarded):
    """Why the fold had to keep both ends bounded, per guarded path: over the
    identical history `git diff HEAD` is empty and the shared range is not."""
    root = committed_story(tmp_path, STORY_011_REL, guarded, violate=True,
                           name=f"head-{guarded.strip('/')}")
    assert git(root, "diff", "HEAD", "--", guarded).strip() == ""
    assert story_diff([guarded], validation_file=root / STORY_011_REL,
                      repo=root).strip() != ""


def guarded_paths_of(source: str, test_name: str) -> set[str]:
    """Every path literal one test function names."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == test_name:
            return {inner.value for inner in ast.walk(node)
                    if isinstance(inner, ast.Constant)
                    and isinstance(inner.value, str)
                    and inner.value.endswith("/")}
    raise AssertionError(f"{test_name} is not defined in this source")


def test_the_rewritten_assertion_guards_the_paths_it_guarded_before():
    """The subject did not move: the same three paths, read out of the
    function's own source before and after the rewrite."""
    name = "test_no_prompt_template_was_changed_by_this_story"
    before = guarded_paths_of(pre_story_source(STORY_011_REL), name)
    after = guarded_paths_of(current_source(STORY_011_REL), name)
    assert after == set(GUARDED)
    assert after == before


def docstring_of(source: str, name: str) -> str:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{name} is not defined in this source")


def test_the_docstring_names_the_shared_resolution_not_the_deleted_one():
    """An absence in prose, so its control is the prose it replaced: the
    pre-story docstring named the deleted helper, today's names the shared
    one and the file it lives in."""
    name = "test_no_prompt_template_was_changed_by_this_story"
    after = docstring_of(current_source(STORY_011_REL), name)
    assert FOLDED_AWAY not in after
    assert "story_diff" in after
    assert "conftest" in after
    assert FOLDED_AWAY in docstring_of(pre_story_source(STORY_011_REL), name)


# --------------------------------------------------------------------------
# What survived in story-011 survived byte-for-byte
# --------------------------------------------------------------------------


def test_only_the_caller_changed_and_only_the_helper_was_removed():
    """The whole edit to story-011's file, read mechanically rather than
    described: one function's code changed, one was removed, none was added.
    """
    before = story016.functions_of(pre_story_source(STORY_011_REL))
    after = story016.functions_of(current_source(STORY_011_REL))
    assert set(before) - set(after) == {FOLDED_AWAY}
    assert set(after) - set(before) == set()
    changed = {name for name in set(before) & set(after)
               if before[name] != after[name]}
    assert changed == {"test_no_prompt_template_was_changed_by_this_story"}


@pytest.mark.parametrize("name", SURVIVING_IN_STORY_011)
def test_the_named_survivors_are_unchanged(name):
    before = story016.functions_of(pre_story_source(STORY_011_REL))
    after = story016.functions_of(current_source(STORY_011_REL))
    assert name in after, name
    assert after[name] == before[name], name


def test_the_positive_guard_on_a_head_resolved_source_survives():
    """Named by the acceptance criteria on its own, because it is the one
    assertion in that file that would go quiet without saying so: the
    resolved baseline must differ from the synthetic history's own HEAD."""
    dump = story016.functions_of(current_source(STORY_011_REL))[
        "test_the_baseline_stays_pre_story_once_this_story_is_committed"]
    assert "coordinator_source_at" in dump
    assert "HEAD" in dump


def test_the_differential_module_loading_is_untouched():
    """`pre_story_coordinator_source` resolves source *text*, which is a
    different question from resolving commits, so this story leaves it and
    the loader it feeds alone."""
    before = story016.functions_of(pre_story_source(STORY_011_REL))
    after = story016.functions_of(current_source(STORY_011_REL))
    for name in ("load_variant", "synthetic_history", "pre_story_coordinator_source"):
        assert after[name] == before[name], name


def test_story_011_is_still_unflagged_by_the_baseline_check():
    """The absence, with the control beside it: the same scanner on the same
    file with one root-targeted HEAD diff appended reports it."""
    source = current_source(STORY_011_REL)
    assert honesty.flagged_calls(source, Path(STORY_011_REL).name) == []
    assert honesty.undeclared_targets(source, Path(STORY_011_REL).name) == []
    dishonest = source + (
        "\ndef probe():\n"
        "    subprocess.run(['git', '-C', str(REPO_ROOT), 'diff', 'HEAD'])\n")
    assert len(honesty.flagged_calls(dishonest, Path(STORY_011_REL).name)) == 1


# --------------------------------------------------------------------------
# story-016's intact-list, and the assertions it kept
# --------------------------------------------------------------------------


INTACT_LIST_TEST = "test_the_baseline_resolution_the_prompt_scope_assertion_needs_is_intact"


def intact_list(source: str) -> tuple[str, ...]:
    """The names story-016's intact-list asserts are present in story-011."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == INTACT_LIST_TEST:
            for inner in ast.walk(node):
                if isinstance(inner, ast.For) and isinstance(
                        inner.iter, (ast.Tuple, ast.List)):
                    return tuple(element.value for element in inner.iter.elts
                                 if isinstance(element, ast.Constant))
    raise AssertionError(f"{INTACT_LIST_TEST} has no literal name list")


def test_the_intact_list_lost_exactly_one_entry_and_kept_seven():
    before = intact_list(pre_story_source(STORY_016_REL))
    after = intact_list(current_source(STORY_016_REL))
    assert set(before) - set(after) == {f"def {FOLDED_AWAY}"}
    assert set(after) - set(before) == set()
    assert len(after) == 7


def test_the_removal_is_recorded_beside_the_list():
    """Not left to a reader to reconstruct: the file says which story folded
    the name away and where it went."""
    source = current_source(STORY_016_REL)
    body = source[source.index(f"def {INTACT_LIST_TEST}"):]
    note = body[:body.index("for name in")]
    assert "story-026" in note
    assert "conftest" in note


@pytest.mark.parametrize("dropped", intact_list(current_source(STORY_016_REL)))
def test_the_surviving_intact_list_still_fails_when_its_subject_is_violated(
    monkeypatch, tmp_path, dropped,
):
    """Seven absences reduced to seven demonstrations: strip each surviving
    name out of the file the assertion reads and it must say so. Without this
    the shortened list would be indistinguishable from a list that stopped
    looking."""
    stripped = current_source(STORY_011_REL).replace(dropped, "def removed_by_a_test")
    assert stripped != current_source(STORY_011_REL), dropped
    path = write(tmp_path, "tests/test_story_011_validation.py", stripped)
    monkeypatch.setattr(story016, "STORY_011_FILE", path)
    with pytest.raises(AssertionError):
        story016.test_the_baseline_resolution_the_prompt_scope_assertion_needs_is_intact()


def test_the_intact_list_passes_against_the_file_it_actually_reads(monkeypatch):
    """Attribution for the seven above: unpatched, over this repository, it
    passes."""
    assert story016.test_the_baseline_resolution_the_prompt_scope_assertion_needs_is_intact() is None


def test_story_016_kept_everything_else_it_asserts():
    """Only two functions in that file moved, and the sets its other
    assertions are held to did not."""
    before = story016.functions_of(pre_story_source(STORY_016_REL))
    after = story016.functions_of(current_source(STORY_016_REL))
    assert set(before) == set(after)
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {
        INTACT_LIST_TEST,
        "test_the_functions_that_did_change_are_only_those_that_took_the_baseline",
    }, changed
    assert story016.REMOVED_TESTS == _list_constant(
        pre_story_source(STORY_016_REL), "REMOVED_TESTS")
    assert story016.REMOVED_HELPERS == _list_constant(
        pre_story_source(STORY_016_REL), "REMOVED_HELPERS")


def _list_constant(source: str, name: str) -> list[str]:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            return [element.value for element in node.value.elts]
    raise AssertionError(f"{name} is not assigned in this source")


def test_the_repointed_comparison_still_fails_when_its_subject_is_violated(
    monkeypatch,
):
    """The second edited assertion, driven the same way.

    Its upper bound moved onto `story_011_at_this_storys_endpoint` so that a
    later story removing a helper story-016 has nothing to say about does not
    turn it red. The bound is all that moved: a function that did *not* depend
    on the historical coordinator disappearing between the two ends must still
    be caught.
    """
    endpoint = current_source(STORY_011_REL)
    baseline = endpoint + "\n\ndef an_unrelated_helper():\n    return 1\n"
    monkeypatch.setattr(story016, "story_011_before_this_story", lambda: baseline)
    monkeypatch.setattr(story016, "story_011_at_this_storys_endpoint",
                        lambda: endpoint)
    with pytest.raises(AssertionError, match="an_unrelated_helper"):
        story016.test_the_functions_that_did_change_are_only_those_that_took_the_baseline()


def test_the_repointed_comparison_is_bounded_at_this_storys_endpoint():
    """Read off the source rather than inferred from its behaviour: the upper
    bound is the resolved endpoint, not today's working tree."""
    source = current_source(STORY_016_REL)
    dump = story016.functions_of(source)[
        "test_the_functions_that_did_change_are_only_those_that_took_the_baseline"]
    assert "story_011_at_this_storys_endpoint" in dump
    assert "read_text" not in dump
    # And that bound resolves through the shared resolution rather than a
    # second copy of it.
    assert "story_commit_range" in story016.functions_of(source)[
        "story_011_at_this_storys_endpoint"]


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------


UNTOUCHED = [
    "orchestration/", "prompts/", "workflows/", "schemas/", "scripts/", "rules/",
    ".harness/stories/", "tests/conftest.py", "tests/test_baseline_honesty.py",
    "tests/test_story_015_validation.py", "tests/test_story_007_validation.py",
    "tests/test_story_008_validation.py", "tests/test_story_009_validation.py",
    "tests/test_story_010_validation.py",
]


@pytest.mark.parametrize("rel", UNTOUCHED)
def test_this_story_left_it_alone(rel):
    assert story_diff([rel], validation_file=Path(__file__)).strip() == ""


@pytest.mark.parametrize("rel", UNTOUCHED)
def test_the_scope_assertion_above_can_fail(tmp_path, rel):
    """The control for every entry in the list, not for one of them: over a
    synthetic history whose run commit rewrites that exact path, the identical
    call reports it."""
    root = committed_story(tmp_path, "tests/test_story_026_validation.py", rel,
                           violate=True, name=f"scope-{rel.strip('/').replace('/', '-')}")
    assert story_diff(
        [rel], validation_file=root / "tests/test_story_026_validation.py",
        repo=root).strip() != ""


def test_the_shared_resolution_kept_its_signature():
    """The constraint stated outright: this story does not touch the
    resolution itself."""
    import inspect
    assert list(inspect.signature(conftest.story_commit_range).parameters) == [
        "validation_file", "repo"]
    assert list(inspect.signature(conftest.story_diff).parameters) == [
        "paths", "validation_file", "repo", "diff_filter", "options"]
    assert issubclass(conftest.NothingToCompareAgainst, RuntimeError)
    assert [field for field in conftest.StoryRange.__dataclass_fields__] == [
        "baseline", "endpoint"]


def test_no_test_was_weakened_skipped_or_deleted():
    """The other way this change could have been made to pass."""
    for rel in (STORY_011_REL, STORY_016_REL):
        before = _test_names(pre_story_source(rel))
        after = _test_names(current_source(rel))
        assert before == after, rel
        assert "@pytest.mark.skip" not in current_source(rel), rel
        assert "pytest.skip(" not in current_source(rel), rel


def _test_names(source: str) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
