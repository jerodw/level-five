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

from conftest import (BASELINE, NothingToCompareAgainst, repository_file_at,
                      story_commit_range, story_diff)

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


#: The call names that spawn a process. Matched on the name alone, never on
#: what it is qualified by: `subprocess.run`, `sp.run` under an aliased
#: import, and a bare `run` under `from subprocess import run` are the same
#: call, and requiring the module to be spelled a particular way made the
#: check evadable by an import statement. What identifies these calls is the
#: literal "git" at the head of their argument list, which _git_argument_list
#: already insists on, so matching the tail loses nothing.
SPAWNING_CALLS = ("run", "check_output", "Popen", "call", "check_call")


def _imported_spawners(tree: ast.Module) -> frozenset[str]:
    """Names this module bound directly from `subprocess`.

    Read off the import statements, which is a fact stated in the source, not
    a value anything has to resolve. `from subprocess import run` binds `run`;
    `from subprocess import run as sh` binds `sh`.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name in SPAWNING_CALLS)
    return frozenset(names)


def _is_subprocess_call(node: ast.Call, imported: frozenset[str]) -> bool:
    """Whether this call spawns a process, however subprocess was imported.

    Two forms, and the asymmetry between them is deliberate.

    A qualified call matches on the attribute alone, whatever qualifies it:
    `subprocess.run` and `sp.run` under an aliased import are the same call,
    and requiring the module to be spelled one way made the check evadable by
    an import statement.

    A bare call matches only when the module imported that name from
    `subprocess`. Matching every bare `run(...)` would flag the legitimate
    `run = functools.partial(subprocess.run, cwd=root)` idiom, where the
    target *is* declared — one line above the call. Chasing that binding
    means following assignments, and this check does not evaluate or track
    values; it reads what the source states. Imports are stated. A local
    rebinding is not, and is left uncovered rather than guessed at.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in SPAWNING_CALLS
    return isinstance(func, ast.Name) and func.id in imported


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
    tree = ast.parse(source)
    imported = _imported_spawners(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node, imported):
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
    tree = ast.parse(source)
    imported = _imported_spawners(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node, imported):
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


@pytest.mark.parametrize("source", [
    pytest.param("import subprocess\nsubprocess.run(['git', 'diff', 'HEAD'])\n",
                 id="plain-import"),
    pytest.param("import subprocess as sp\nsp.run(['git', 'diff', 'HEAD'])\n",
                 id="aliased-module"),
    pytest.param("from subprocess import run\nrun(['git', 'diff', 'HEAD'])\n",
                 id="imported-name"),
    pytest.param("from subprocess import run as sh\nsh(['git', 'diff', 'HEAD'])\n",
                 id="imported-name-aliased"),
    pytest.param("import subprocess\nsubprocess.check_output(['git', 'diff', 'HEAD'])\n",
                 id="check_output"),
    pytest.param("import subprocess\nsubprocess.Popen(['git', 'diff', 'HEAD'])\n",
                 id="popen"),
])
def test_the_spawn_is_recognized_however_subprocess_was_imported(source):
    """An import statement must not be able to hide a call from the check.

    Matching only `subprocess.run` meant renaming the import was enough to
    disappear; every form below spawns the same process.
    """
    assert len(flagged_calls(source, "probe.py")) == 1
    assert len(undeclared_targets(source, "probe.py")) == 1


def test_a_partial_bound_runner_is_not_flagged():
    """The target is declared on the partial, one line above the call.

    Reading it would mean following an assignment, and this check does not
    track values. `tests/test_story_016_validation.py` uses this idiom, so the
    case is real rather than hypothetical — and treating a bare `run(...)` as
    a spawn regardless of imports would flag it wrongly.
    """
    source = ("import functools, subprocess\n"
              "run = functools.partial(subprocess.run, cwd=root)\n"
              "run(['git', 'diff', 'HEAD'])\n")
    assert undeclared_targets(source, "probe.py") == []
    assert flagged_calls(source, "probe.py") == []

    # The same module *also* importing run from subprocess binds the name, and
    # then the call is a spawn by the module's own declaration.
    declared = ("from subprocess import run\n"
                "run(['git', 'diff', 'HEAD'])\n")
    assert len(undeclared_targets(declared, "probe.py")) == 1


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
# A second rule: no module under tests/ builds a module at runtime
#
# Its own rule, with its own purpose, and it draws nothing from the baseline
# rule above. That one is about a *comparison that cannot fail*. This one is
# about an *instrument with a shelf life*: a module built at runtime out of
# source recovered from git history runs against today's workflow, schemas and
# config, so a legitimate change to any of those breaks it for reasons
# unrelated to what it tested. story-028 reshaped two workflow keys and ten
# tests went red, every one of them decay.
#
# It matches on **language constructs**, never on helper names. The scan it
# replaced (`tests/test_story_016_validation.py`, deleted by story-029) named
# two history readers and five loaders; four modules written after it defined
# their own helpers under different names and went unreported for three
# stories. A rule that names no helper cannot be evaded by renaming one.
#
# What it does not cover, stated here because this is where a reader meets it:
#
#   * **source run in a subprocess rather than in-process.** A module that
#     writes recovered source to a file and runs `sys.executable` over it is
#     not building a module in this process and is not seen. This is a real
#     idiom in the suite — `tests/test_story_016_validation.py` copies
#     `orchestration/` into a throwaway repository and runs pytest there — and
#     it must stay unflagged, so the boundary is drawn at this process.
#   * **historical text written to a file and passed in as a path.** The
#     shared loader takes a path, and the scan does not evaluate expressions or
#     track values, so it cannot tell a working-tree path from a path a caller
#     wrote recovered text into. That is why the shared mutation-loader takes a
#     working-tree path and its replacements rather than arbitrary source text:
#     the shape of the helper is what makes recovered source an unnatural
#     argument, and the scan is not what stops it.
#   * **deliberate obfuscation of a banned construct.** `getattr(builtins,
#     "ex" + "ec")`, an alias assigned at runtime, or a construct reached
#     through a local rebinding is not seen. The scan reads what the source
#     states; it does not evaluate it.
#
# And the limit it shares with everything mechanical in this repository: it is
# not tamper-proof. An edit that deletes a check alongside a genuinely forced
# repair is not caught, at any granularity, because deleting the check that
# fails you satisfies the revert rule's own definition of a forced edit.
# --------------------------------------------------------------------------


#: Constructs that build or execute a module, reached through a module or
#: bound directly. Matched on the name alone, whatever qualifies it — the same
#: reasoning `_is_subprocess_call` uses for a spawn: requiring `importlib.util`
#: to be spelled a particular way would make the check evadable by an import
#: statement.
MODULE_CONSTRUCTORS = (
    "spec_from_file_location", "spec_from_loader", "module_from_spec",
    "exec_module", "SourceFileLoader", "SourcelessFileLoader",
    "ExtensionFileLoader", "ModuleType", "import_module",
    "run_path", "run_module",
)

#: Builtins that run source in this process. Matched **only** as bare calls,
#: because a builtin is never qualified: `re.compile` compiles a regular
#: expression and `ast.literal_eval` evaluates a literal, and neither runs
#: source. Reaching a builtin through an attribute is the obfuscation case the
#: docstring above puts outside this scan.
SOURCE_EXECUTORS = ("exec", "eval", "compile", "__import__")

#: The one module allowed to do it, stated here rather than inferred. It holds
#: the shared loaders — the mutation-loader every non-vacuity check goes
#: through, and the loader for the extensionless entry points under
#: `scripts/`, which have no suffix and so cannot be imported.
CONSTRUCTION_EXEMPT_MODULES = ("conftest.py",)


def _construction_reason(name: str) -> str:
    return (f"builds or runs a module at runtime with `{name}`; the shared "
            f"loaders in tests/conftest.py are the one place under tests/ "
            f"that may")


def module_construction(source: str, module: str) -> list[Flag]:
    """Every construct in one module's source that builds or runs a module.

    Exemptions are not applied here, exactly as `flagged_calls` does not apply
    its own: this is the scan, and a caller that means to exempt a module does
    not scan it. That is what lets the regression set below be fed to the same
    function the live suite is held to.
    """
    flags = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in MODULE_CONSTRUCTORS:
            flags.append(Flag(module=module, line=node.lineno,
                              reason=_construction_reason(func.attr)))
        elif isinstance(func, ast.Name) and func.id in (
                MODULE_CONSTRUCTORS + SOURCE_EXECUTORS):
            flags.append(Flag(module=module, line=node.lineno,
                              reason=_construction_reason(func.id)))
    return flags


def constructing_modules() -> list[Path]:
    """Every module this rule covers. Discovered by globbing, never by naming."""
    return [path for path in sorted(TESTS_DIR.glob("*.py"))
            if path.name not in CONSTRUCTION_EXEMPT_MODULES]


def test_no_module_under_tests_builds_a_module_at_runtime():
    """The rule, run rather than inspected."""
    flags = [
        flag
        for path in constructing_modules()
        for flag in module_construction(path.read_text(encoding="utf-8"), path.name)
    ]
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_exactly_one_module_may_build_one_and_it_holds_the_shared_loaders():
    """The exemption is by name and covers nothing else."""
    assert CONSTRUCTION_EXEMPT_MODULES == ("conftest.py",)
    shared = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "def load_mutant" in shared
    assert "def load_script" in shared
    assert module_construction(shared, "conftest.py"), \
        "the exempt module is exempt because it is the one that does this"

    covered = {path.name for path in constructing_modules()}
    assert covered.isdisjoint(CONSTRUCTION_EXEMPT_MODULES)
    assert len(covered) + len(CONSTRUCTION_EXEMPT_MODULES) \
        == len(list(TESTS_DIR.glob("*.py")))


@pytest.mark.parametrize("planted,expected", [
    pytest.param(
        "import importlib.util\n"
        "def probe(path):\n"
        "    spec = importlib.util.spec_from_file_location('x', path)\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n",
        3, id="spec-from-file-location"),
    pytest.param(
        "import importlib.machinery\n"
        "def probe(path):\n"
        "    loader = importlib.machinery.SourceFileLoader('x', path)\n"
        "    loader.exec_module(object())\n",
        2, id="source-file-loader"),
    pytest.param(
        "def probe(source):\n"
        "    namespace = {}\n"
        "    exec(source, namespace)\n"
        "    return namespace\n",
        1, id="exec-into-a-namespace"),
    pytest.param(
        "import types\n"
        "def probe(source):\n"
        "    module = types.ModuleType('x')\n"
        "    exec(compile(source, 'x', 'exec'), module.__dict__)\n",
        3, id="module-type-and-compile"),
])
def test_the_construction_scan_reports_a_planted_violation(planted, expected):
    """Its reach demonstrated rather than asserted. A scan with no planted
    violation is indistinguishable from one that has stopped looking, which
    is the failure mode this whole module exists about."""
    flags = module_construction(planted, "probe.py")
    assert len(flags) == expected, flags


def test_the_construction_scan_reports_a_module_that_renamed_its_helpers():
    """Renaming is exactly how the scan this replaces was evaded, so it is the
    case that must be shown rather than argued.

    Two sources, identical in what they do and sharing not one helper name.
    Both are reported, because the rule names constructs and no helper.
    """
    one = ("import importlib.machinery, importlib.util\n"
           "def load_variant(name, path):\n"
           "    loader = importlib.machinery.SourceFileLoader(name, str(path))\n"
           "    spec = importlib.util.spec_from_loader(loader.name, loader)\n"
           "    module = importlib.util.module_from_spec(spec)\n"
           "    loader.exec_module(module)\n"
           "    return module\n")
    renamed = (one.replace("load_variant", "_summon_the_old_one")
               .replace("(name, path)", "(label, where)")
               .replace("name, str(path)", "label, str(where)"))

    assert "load_variant" not in renamed
    assert "_summon_the_old_one" in renamed
    assert len(module_construction(one, "one.py")) == 4
    assert len(module_construction(renamed, "renamed.py")) == 4


@pytest.mark.parametrize("benign", [
    pytest.param("import re\nPATTERN = re.compile('x')\n", id="re-compile"),
    pytest.param("import ast\nV = ast.literal_eval('1')\n", id="literal-eval"),
    pytest.param("import ast\nT = ast.parse('x = 1')\n", id="ast-parse"),
    pytest.param("import subprocess, sys\n"
                 "subprocess.run([sys.executable, '-m', 'pytest'], cwd='x')\n",
                 id="a-subprocess-which-is-a-stated-limit"),
])
def test_the_construction_scan_leaves_these_alone(benign):
    """What it must not report, including one of its own stated limits: a
    suite run in a subprocess is outside this rule by construction, and
    `tests/test_story_016_validation.py` depends on that staying true."""
    assert module_construction(benign, "probe.py") == []


# --------------------------------------------------------------------------
# A third rule: only the shared reader asks git for a repository file's text
#
# Its own rule again, and it is not the construction rule's enforcement. That
# one says a module may not be built here; this one says a file's historical
# text is read in one place. Either could hold without the other: a module
# could be built from text read some other way, and text can be read for a
# hundred honest reasons that never build anything.
#
# The purpose is the one `story_commit_range` was written for and that six
# repairs have re-derived: a per-story assertion has to be bounded at *both*
# ends of its own story's commit range, and every private copy of the reader
# was a place that bounding could be got wrong again. Eleven copies existed
# when story-029 folded them.
#
# What it does not cover, stated here rather than implied:
#
#   * **git run in a subprocess this scan cannot attribute** — a call assembled
#     through `functools.partial` or a local helper is not seen, for the same
#     reason `_is_subprocess_call` does not see it: this check reads what the
#     source states and does not track values. The throwaway-repository tests
#     are written that way and must stay unflagged.
#   * **a file's text obtained without asking git for it** — reading a path a
#     caller has already written historical text into, or a `git worktree` or
#     clone built elsewhere and read with `read_text`, is outside this rule.
#   * **deliberate obfuscation** — a subcommand assembled from pieces, or a
#     revision spec built by concatenation at runtime, is not seen.
#
# And the same tamper limit: an edit that deletes this check alongside a
# genuinely forced repair is not caught, and no granularity closes that.
# --------------------------------------------------------------------------


#: The subcommands that hand back a file's *content*. `git show <rev>` and
#: `git show --name-only` name commits and paths, which is a different
#: question and not this rule's business.
CONTENT_SUBCOMMANDS = ("show", "cat-file")

#: The module holding the shared reader, stated by name. The same file the
#: other two rules exempt, and for a third reason: it is where reading a
#: repository file's text at a bound is the *correct* thing to do.
READER_EXEMPT_MODULES = ("conftest.py",)


def _joined_literal(node: ast.AST) -> str:
    """Every literal fragment of an argument, joined.

    An f-string's computed fields contribute nothing, so `f"{revision}:{path}"`
    reads as `":"` — which is the revision-and-path shape this looks for, and
    is what a computed revision cannot hide.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(value.value for value in node.values
                       if isinstance(value, ast.Constant)
                       and isinstance(value.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _joined_literal(node.left) + _joined_literal(node.right)
    return ""


def _asks_for_a_files_text(elements: list[ast.expr]) -> bool:
    """Whether these arguments ask git to hand back a file's content.

    A content subcommand, plus either a `<revision>:<path>` argument — read
    through its literal fragments, so a computed revision cannot hide the
    shape — or the object type `cat-file` prints.
    """
    literals = [_literal_text(element) for element in elements]
    if not any(text in CONTENT_SUBCOMMANDS for text in literals if text):
        return False
    for element in elements:
        joined = _joined_literal(element)
        if ":" in joined and not joined.startswith("--"):
            return True
    return any(text in ("blob", "-p") for text in literals if text)


def _git_words(node: ast.Call) -> list[ast.expr] | None:
    """The arguments of a git invocation, in either of the two forms.

    The spawned form, `subprocess.run(["git", ...])`, is the one the baseline
    rule above reads. The *wrapped* form, `git(REPO_ROOT, "show", ...)`, is
    the one every per-story module actually writes: a one-line local helper
    around `subprocess.run`, which the baseline rule deliberately does not
    chase because it does not track values.

    This rule cannot afford to skip it — all four modules that carried the
    retired practice wrote it that way — so it reads the wrapped form by its
    *shape* rather than by the helper's name: a call naming one of the
    repository-root names among its arguments, with a content subcommand and
    a revision-and-path argument beside it. No helper name appears here, so
    renaming one changes nothing.
    """
    if node.args:
        first = node.args[0]
        if isinstance(first, ast.List) and first.elts \
                and _literal_text(first.elts[0]) == "git":
            return list(first.elts)
    if any(_names_the_repository_root(argument) for argument in node.args):
        return list(node.args)
    return None


def git_text_reads(source: str, module: str) -> list[Flag]:
    """Every git invocation in one module that asks this repository for a
    file's text.

    Which repository is read the way the baseline rule above reads it — a
    `-C` or `cwd=` naming one of the names that stand for this repository, or
    no stated target at all — with the wrapped form recognized by a
    repository-root name among its arguments. A throwaway repository a test
    built for itself is somebody else's history and is not this rule's
    business.
    """
    flags = []
    tree = ast.parse(source)
    imported = _imported_spawners(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        elements = _git_words(node)
        if elements is None:
            continue
        spawned = bool(node.args) and isinstance(node.args[0], ast.List)
        if spawned:
            if not _is_subprocess_call(node, imported):
                continue
            if not _targets_the_repository_root(node, elements):
                continue
        if _asks_for_a_files_text(elements):
            flags.append(Flag(
                module=module, line=node.lineno,
                reason=("asks git for a repository file's text; "
                        "tests/conftest.py holds the one reader, which bounds "
                        "it at a story's own commit range"),
            ))
    return flags


def reading_modules() -> list[Path]:
    return [path for path in sorted(TESTS_DIR.glob("*.py"))
            if path.name not in READER_EXEMPT_MODULES]


def test_no_module_under_tests_reads_a_repository_file_out_of_git():
    """The rule, run rather than inspected."""
    flags = [
        flag
        for path in reading_modules()
        for flag in git_text_reads(path.read_text(encoding="utf-8"), path.name)
    ]
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_exactly_one_module_may_read_one_and_it_holds_the_shared_reader():
    """The exemption is by name and covers nothing else.

    That the exempt module really is the one doing the reading is asserted on
    its source rather than by scanning it: `conftest.py` reaches git through
    its own one-line wrapper with the repository passed in as a parameter, so
    the shape this rule matches on is not written there — which is one of the
    limits stated above, met by the exemption being a name rather than a
    derivation.
    """
    assert READER_EXEMPT_MODULES == ("conftest.py",)
    shared = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "def repository_file_at" in shared
    assert "def function_source_at" in shared
    assert '"show", f"{resolved}:{relative}"' in shared

    covered = {path.name for path in reading_modules()}
    assert covered.isdisjoint(READER_EXEMPT_MODULES)
    assert len(covered) + len(READER_EXEMPT_MODULES) \
        == len(list(TESTS_DIR.glob("*.py")))


@pytest.mark.parametrize("planted,expected", [
    pytest.param(
        "import subprocess\n"
        "def probe(revision, path):\n"
        "    return subprocess.run(\n"
        "        ['git', '-C', str(REPO_ROOT), 'show', f'{revision}:{path}'],\n"
        "        capture_output=True, text=True).stdout\n",
        1, id="show-a-blob"),
    pytest.param(
        "import subprocess\n"
        "def probe(path):\n"
        "    subprocess.check_output(\n"
        "        ['git', '-C', str(HARNESS_ROOT), 'show', 'HEAD~3:' + path])\n",
        1, id="show-a-blob-at-a-fixed-revision"),
    pytest.param(
        "import subprocess\n"
        "def probe(sha):\n"
        "    subprocess.run(['git', 'cat-file', 'blob', sha], cwd=REPO_ROOT)\n",
        1, id="cat-file-blob"),
    pytest.param(
        "import subprocess\n"
        "def probe(revision, path):\n"
        "    subprocess.run(['git', 'show', f'{revision}:{path}'])\n",
        1, id="no-stated-target-inherits-this-repository"),
])
def test_the_reader_scan_reports_a_planted_violation(planted, expected):
    """Its reach demonstrated rather than asserted, on the same terms as the
    construction scan's planted violations."""
    flags = git_text_reads(planted, "probe.py")
    assert len(flags) == expected, flags


def test_the_reader_scan_reports_a_module_that_renamed_its_helpers():
    """Renaming is how the superseded scan was evaded, so this rule is shown
    against it too: two readers with no name in common, both reported."""
    one = ("import subprocess\n"
           "def pre_story(path):\n"
           "    revision = baseline()\n"
           "    return subprocess.run(\n"
           "        ['git', '-C', str(REPO_ROOT), 'show', f'{revision}:{path}'],\n"
           "        capture_output=True, text=True).stdout\n")
    renamed = (one.replace("pre_story", "recover_the_old_text")
               .replace("revision", "moment").replace("baseline", "way_back"))

    assert "pre_story" not in renamed
    assert len(git_text_reads(one, "one.py")) == 1
    assert len(git_text_reads(renamed, "renamed.py")) == 1


@pytest.mark.parametrize("benign", [
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(REPO_ROOT), 'log',\n"
                 "                '--format=%H', '--', 'x'])\n",
                 id="a-commit-list"),
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(REPO_ROOT), 'show',\n"
                 "                '--name-only', '--format=', revision])\n",
                 id="a-commits-file-list"),
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(root), 'show',\n"
                 "                f'HEAD:{path}'], cwd=root)\n",
                 id="a-throwaway-repository"),
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(REPO_ROOT), 'diff',\n"
                 "                '--pretty=format:%H'])\n",
                 id="a-colon-inside-an-option"),
])
def test_the_reader_scan_leaves_these_alone(benign):
    """What it must not report: naming commits and paths is a different
    question from reading a file's content, and another repository's history
    is not this rule's business."""
    assert git_text_reads(benign, "probe.py") == []


def test_the_two_new_rules_are_stated_separately_and_neither_derives_the_other():
    """Two rules, two purposes, two exemption lists. Neither is described as
    getting its enforcement from the other, and neither scan calls the other.

    Read off the source rather than asserted about it, because "these are
    separate" is exactly the kind of claim that stays written down after it
    has stopped being true.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    for name, other in (("module_construction", "git_text_reads"),
                        ("git_text_reads", "module_construction")):
        called = {inner.func.id for inner in ast.walk(functions[name])
                  if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)}
        assert other not in called, name

    # Two vocabularies, disjoint: neither rule's subject is expressible in the
    # other's terms, which is what "separate rules" means here.
    assert set(MODULE_CONSTRUCTORS + SOURCE_EXECUTORS).isdisjoint(
        CONTENT_SUBCOMMANDS)
    for rule in ("no module under tests/ builds a module at runtime",
                 "only the shared reader asks git for a repository file's text"):
        assert rule in source, rule


@pytest.mark.parametrize("scan", ["module_construction", "git_text_reads"])
def test_each_new_rule_states_what_it_does_not_cover(scan):
    """The narrowness this module already states about itself, required of
    each new rule as well — and the three limits named by this story's
    acceptance criteria are checked by name, so a rewritten paragraph that
    quietly drops one goes red.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    where = source.index(f"def {scan}(")
    stated = source[:where]
    heading = stated.rindex("What it does not cover")
    limits = stated[heading:]

    assert "subprocess" in limits
    assert "written" in limits and "path" in limits
    assert "obfuscation" in limits
    assert "tamper-proof" in stated or "not tamper-proof" in stated


# --------------------------------------------------------------------------
# The regression set for the two new rules: the four modules that carried the
# practice, recovered at this story's own baseline
# --------------------------------------------------------------------------


#: This story's own validation file. It does not exist while the implementer
#: is running and is written by the tester, so the shared resolution reports
#: the story as in flight and the baseline as HEAD — which is this story's
#: baseline — and reports the run commit and its parent once the story
#: commits. Named rather than pinned, so a rebase does not move it.
THIS_STORYS_VALIDATION_FILE = "tests/test_story_029_validation.py"

#: The four modules that carried the retired practice at this story's
#: baseline. Every one of them postdates the name-matching scan that was
#: supposed to prevent it, and every one of them evaded it by defining its own
#: helpers under different names. This is the only direct evidence the
#: replacement would have caught what it is for.
CARRIED_THE_PRACTICE = (
    "tests/test_story_020_validation.py",
    "tests/test_story_021_validation.py",
    "tests/test_story_024_validation.py",
    "tests/test_story_027_validation.py",
)


def at_this_storys_baseline(rel: str) -> str:
    """One file as it stood before this story touched it."""
    return repository_file_at(
        rel, validation_file=REPO_ROOT / THIS_STORYS_VALIDATION_FILE,
        bound=BASELINE, repo=REPO_ROOT)


def test_the_recovered_baseline_sources_are_the_baseline_sources():
    """The regression set below leans on this recovery, so it is asserted
    rather than assumed: each recovered text differs from today's and carries
    the practice's own shape."""
    for rel in CARRIED_THE_PRACTICE:
        before = at_this_storys_baseline(rel)
        assert before != (REPO_ROOT / rel).read_text(encoding="utf-8"), rel
        assert "def test_" in before, rel


@pytest.mark.parametrize("rel", CARRIED_THE_PRACTICE)
def test_the_construction_scan_reports_each_module_at_this_storys_baseline(rel):
    flags = module_construction(at_this_storys_baseline(rel), Path(rel).name)
    assert flags, rel


@pytest.mark.parametrize("rel", CARRIED_THE_PRACTICE)
def test_the_reader_scan_reports_each_module_at_this_storys_baseline(rel):
    flags = git_text_reads(at_this_storys_baseline(rel), Path(rel).name)
    assert flags, rel


def test_all_four_are_caught_by_both_rules_and_none_survives_in_the_suite():
    """The regression set stated as one assertion each way, so a rule that
    was quietly narrowed while its modules were repaired shows up here."""
    recovered = {rel: at_this_storys_baseline(rel) for rel in CARRIED_THE_PRACTICE}
    for scan in (module_construction, git_text_reads):
        caught = {rel for rel, source in recovered.items()
                  if scan(source, Path(rel).name)}
        assert caught == set(recovered), scan.__name__

    for rel in CARRIED_THE_PRACTICE:
        after = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert module_construction(after, Path(rel).name) == [], rel
        assert git_text_reads(after, Path(rel).name) == [], rel


# --------------------------------------------------------------------------
# The regression set: five instances, all committed evidence
# --------------------------------------------------------------------------


def pre_repair_source(rel: str) -> str:
    """One repaired file as it stood before this story touched it.

    Resolved through the same shared baseline the repairs use: the parent of
    the commit that added *this* module. While this story is in flight that
    is HEAD, and once it commits it is the revision before it — the pre-repair
    text either way, without a pinned SHA that a rebase would invalidate.

    Read through `conftest.repository_file_at` since story-029, which folded
    this module's own `git show` into the shared reader along with the ten
    others. This module states the rule that no module but that one may read
    a repository file's text out of git, so carrying a private copy of the
    call would have been the first thing the rule reports.
    """
    return repository_file_at(rel, validation_file=Path(__file__),
                              bound=BASELINE, repo=REPO_ROOT)


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
