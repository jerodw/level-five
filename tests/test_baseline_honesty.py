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
control now required of absence assertions in `prompts/story-tester.md` is for.

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
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import context_assembler
from conftest import (BASELINE, NothingToCompareAgainst, function_source,
                      repository_file_at, story_commit_range, story_diff)

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

#: Where each of the files named at a past revision above lives *now*.
#: story-038 renamed every per-story validation module for the behaviour it
#: validates, and merged two pairs of them. A path is asked for at a revision
#: under the name it has there, so the constants above keep their historical
#: spelling and every read of the working tree goes through this.
TODAY = {
    "tests/test_story_007_validation.py": "tests/test_stage_output_ownership.py",
    "tests/test_story_008_validation.py": "tests/test_planner_injection.py",
    "tests/test_story_009_validation.py": "tests/test_planner_injection.py",
    "tests/test_story_010_validation.py": "tests/test_attempt_archiving.py",
    "tests/test_story_020_validation.py": "tests/test_escalation_resume.py",
    "tests/test_story_021_validation.py": "tests/test_foreign_work_refusal.py",
    "tests/test_story_024_validation.py": "tests/test_escalation_summary.py",
    "tests/test_story_027_validation.py": "tests/test_rerun_refusal.py",
    "tests/test_story_029_validation.py":
        "tests/test_git_history_loading_retired.py",
}


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


def _imported_names(tree: ast.Module, module: str,
                    wanted: object) -> dict[str, str]:
    """What this module bound from `module`, as local name to original name.

    Read off the import statements, which is a fact stated in the source, not
    a value anything has to resolve. `from subprocess import run` binds `run`
    to `run`; `from subprocess import run as sh` binds `sh` to `run`.

    The alias handling lives here once. The reader rule below reaches it
    through `_imported_spawners`, and the history rule at the foot of this
    module reaches it directly for the helpers it names — a second copy of
    "which local name stands for which import" is exactly the kind of thing
    that gets fixed in one place and left wrong in the other.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            bound.update({alias.asname or alias.name: alias.name
                          for alias in node.names if alias.name in wanted})
    return bound


def _imported_spawners(tree: ast.Module) -> frozenset[str]:
    """Names this module bound directly from `subprocess`."""
    return frozenset(_imported_names(tree, "subprocess", SPAWNING_CALLS))


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
        Path(TODAY[rel]).name for rel in REPAIRED_FILES}


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
    "test_schema_directed_parsing.py",
    "test_single_story_reader.py",
    "test_stage_output_ownership.py",
    "test_story_coordinator.py",
    "test_execution_history.py",
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


# --------------------------------------------------------------------------
# A fourth rule: a mutation control mutates the working tree, never a pinned
# revision
#
# Its own rule, a fourth time, and it draws nothing from the three above. The
# first is about a *comparison that cannot fail*. The second is about an
# instrument built out of history. The third is about where a file's historical
# text is read. This one is about what a control is allowed to *mutate*.
#
# A control that mutates code to demonstrate an assertion can fail must mutate
# the code as it stands. The property being demonstrated — this assertion goes
# red when what it names is violated — is a property of the assertion and of the
# code it is about, and both are present in the working tree. Pinning the
# subject at a revision adds a second variable, and that variable can only ever
# make the control fail for reasons unrelated to what it shows.
#
# Observed, not predicted. story-029's own validation file pinned the
# coordinator at that story's endpoint, wrote it into a copy of the repository
# and ran a test against it under pytest. One story later two workflow keys were
# reshaped, the pinned coordinator could no longer run against the workflow
# definition it was paired with, and every case failed its control rather than
# its subject. It survived both of story-029's scans legitimately: it writes a
# file into a copy and shells out, which is a shape neither of them recognises.
#
# It is narrower than "never pin a revision", and it says so. Reading a pinned
# revision to establish what a story changed is correct, and is what
# `story_diff`, `repository_file_at` and `function_source_at` exist for. This
# rule is about mutation controls, which is why the pairing rather than either
# half is what it reports.
#
# **This rule names no exempt module, and the absence is deliberate.** The two
# rules above exempt `tests/conftest.py` because it is the one place the thing
# they forbid is the correct answer — the shared baseline resolution has to
# compare against HEAD, and the shared reader has to ask git for a file's text.
# A mutation of pinned source has no such place. The shared mutation loader
# takes a working-tree path and the substitutions to make in it, precisely so
# that pinned text is not a value it accepts, so there is nowhere under tests/
# where this rule would be wrong.
#
# It borrows rule two's construct list for what counts as *running* source
# rather than writing a second copy of it, which would be one fact in two
# places. Borrowing a word list is not drawing enforcement from another rule:
# neither scan calls the other, each reports its own subject, and each states
# its own limits.
#
# What it does not cover, stated here because this is where a reader meets it:
#
#   * **a read and an execution split across functions.** The pairing is
#     followed inside one function. A pinned read in one function whose value is
#     written or run in another — passed through a fixture, stashed on a class,
#     or laundered through a helper that returns it — is not seen. The execution
#     half alone is followed one step into a helper defined at this module's top
#     level, because that is stated in the source; nothing else is chased.
#   * **source text obtained without asking git for it.** A caller that reads a
#     path historical text was already written into, or a worktree or clone
#     built elsewhere and read with `read_text`, is pinning by a route this scan
#     cannot see: it reads what the source states and does not evaluate it or
#     track values.
#   * **deliberate obfuscation.** A revision spec assembled at runtime, a read
#     reached through a rebound local name, or a write performed by a subprocess
#     rather than in this process, is not seen.
#
# And the same standing limit as everything mechanical here: it is not
# tamper-proof. An edit deleting this check alongside a genuinely forced repair
# is not caught, at any granularity, because deleting the check that fails you
# satisfies the revert rule's own definition of a forced edit.
# --------------------------------------------------------------------------


#: Keyword arguments that bound a read at a revision. `revision=` names one
#: directly; `bound=` names one end of a story's own commit range. Both are
#: stated in the call, which is what this scan reads. A `revision=None` bounds
#: nothing and is not one — the shared reader takes it as "I named a bound
#: instead".
REVISION_KEYWORDS = ("revision", "bound")

#: Writing text to a path. Matched on the method alone, whatever the path
#: expression is, for the same reason `_is_subprocess_call` matches a spawn on
#: its attribute: how the path was spelled is not what the rule is about.
PATH_WRITES = ("write_text", "write_bytes")

#: The shared mutation loader's *interface*, not its name. It takes the
#: substitutions to make in a working-tree path and is called with both of
#: these keywords; matching the interface rather than the name is what keeps a
#: rename from changing anything, which is the property the superseded
#: name-matching scan lacked.
LOADER_INTERFACE = ("name", "tmp_path")


def _executes_source(node: ast.Call, imported: frozenset[str]) -> bool:
    """Whether one call runs source in this process or in another.

    Three shapes, and they are the three a mutation control has available: a
    process spawn, a call carrying the shared mutation loader's interface, and
    a construct that builds or runs a module — the last read off rule two's
    vocabulary rather than a second copy of it.
    """
    if _is_subprocess_call(node, imported):
        return True
    if set(LOADER_INTERFACE) <= {keyword.arg for keyword in node.keywords}:
        return True
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in MODULE_CONSTRUCTORS
    return isinstance(func, ast.Name) and func.id in (
        MODULE_CONSTRUCTORS + SOURCE_EXECUTORS)


def _executing_functions(tree: ast.Module,
                         imported: frozenset[str]) -> frozenset[str]:
    """The top-level functions that execute source, directly or one step on.

    A control almost never spawns inline: story-029's wrote a one-line
    `run_one_test` helper beside itself. Following a call to a function defined
    at this module's top level is reading what the source states, the same
    thing `_imported_spawners` does with an import; nothing beyond this module
    is chased, and the limit is stated above.

    A revision-bounded read is never itself the execution, and excluding it is
    what keeps the pairing a pairing. The git form of such a read *is* a
    process spawn, so counting it would make every bounded read its own second
    half: a `git show` written to a path and never run would be reported, and
    the same control written through the shared reader would not. Some call
    other than the read has to run the text.
    """
    defined = {node.name: node for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    direct, calls = set(), {}
    for name, node in defined.items():
        inner_calls = [inner for inner in ast.walk(node)
                       if isinstance(inner, ast.Call)]
        if any(_executes_source(inner, imported) for inner in inner_calls
               if not _is_revision_bounded_read(inner, imported)):
            direct.add(name)
        calls[name] = {inner.func.id for inner in inner_calls
                       if isinstance(inner.func, ast.Name)} & set(defined)
    executing = set(direct)
    growing = True
    while growing:
        growing = False
        for name in defined:
            if name not in executing and calls[name] & executing:
                executing.add(name)
                growing = True
    return frozenset(executing)


def _is_revision_bounded_read(node: ast.AST, imported: frozenset[str]) -> bool:
    """Whether one call resolves something at a revision.

    Two forms, so that reaching the history without the shared reader is still
    seen: a call stating a revision or a story bound in a keyword, and a git
    invocation asking for a file's text at a revision — the second read by the
    same shape the reader rule above reads, rather than by a second copy of it.
    """
    if not isinstance(node, ast.Call):
        return False
    for keyword in node.keywords:
        if keyword.arg not in REVISION_KEYWORDS:
            continue
        if isinstance(keyword.value, ast.Constant) and keyword.value.value is None:
            continue
        return True
    elements = _git_words(node)
    if elements is None:
        return False
    if bool(node.args) and isinstance(node.args[0], ast.List) \
            and not _is_subprocess_call(node, imported):
        return False
    return _asks_for_a_files_text(elements)


def _carries_a_revision_bounded_read(value: ast.AST,
                                     imported: frozenset[str]) -> bool:
    """Whether an expression has such a read anywhere inside it.

    The read is rarely the whole expression: a spawned `git show` is written
    `subprocess.run(...).stdout`, and a shared-reader call is as often
    `...strip()` as bare. Reading the expression whole is what keeps the shape
    from turning on a trailing attribute.
    """
    return any(_is_revision_bounded_read(inner, imported)
               for inner in ast.walk(value))


def _pinned_names(function: ast.AST, imported: frozenset[str]) -> set[str]:
    """The local names this function binds to a revision-bounded read."""
    names = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and _carries_a_revision_bounded_read(
                node.value, imported):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and isinstance(node.target, ast.Name) \
                and _carries_a_revision_bounded_read(node.value, imported):
            names.add(node.target.id)
    return names


def _flows_from_a_pinned_read(value: ast.AST, pinned: set[str],
                              imported: frozenset[str]) -> bool:
    """Whether a written value came from a revision-bounded read.

    Directly, as the read itself or as a name bound to one, and through a
    `.replace(...)` of such a value — which is how a control makes the one
    change it means to demonstrate, and is therefore the one transformation
    worth following.
    """
    if isinstance(value, ast.Name):
        return value.id in pinned
    if _carries_a_revision_bounded_read(value, imported):
        return True
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
            and value.func.attr == "replace":
        return _flows_from_a_pinned_read(value.func.value, pinned, imported)
    return False


def mutation_controls(source: str, module: str) -> list[Flag]:
    """Every mutation control in one module that mutates a pinned revision.

    A per-function pairing: a value from a revision-bounded read that flows
    into a write to a path, inside a function that also executes source. All
    three are required, because the point is the pairing — a bounded read that
    is only compared is the correct way to establish what a story changed, and
    a mutation of working-tree source is the correct way to show an assertion
    can fail.

    No exemption is applied, here or by any caller: this rule names no exempt
    module, for the reason stated above.
    """
    flags = []
    tree = ast.parse(source)
    imported = _imported_spawners(tree)
    executing = _executing_functions(tree, imported)
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name not in executing:
            continue
        pinned = _pinned_names(function, imported)
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Attribute) \
                    or node.func.attr not in PATH_WRITES:
                continue
            if not _flows_from_a_pinned_read(node.args[0], pinned, imported):
                continue
            flags.append(Flag(
                module=module, line=node.lineno,
                reason=(f"{function.name} writes source resolved at a revision "
                        f"to a path and runs it; a mutation control mutates the "
                        f"working tree, never a pinned revision"),
            ))
    return flags


def test_no_module_under_tests_mutates_a_pinned_revision():
    """The rule, run rather than inspected, over every module including the one
    the other three exempt."""
    flags = [
        flag
        for path in all_modules()
        for flag in mutation_controls(path.read_text(encoding="utf-8"), path.name)
    ]
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_this_rule_names_no_exempt_module_and_says_why():
    """The absence is the claim, so it is asserted rather than left implied.

    No `*_EXEMPT_MODULES` constant belongs to this rule, the scan is run over
    every module the glob finds, and the prose beside the rule says why there
    is nowhere a mutation of pinned source would be correct.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    exemptions = {node.targets[0].id
                  for node in ast.parse(source).body
                  if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id.endswith("EXEMPT_MODULES")}
    assert exemptions == {"EXEMPT_MODULES", "CONSTRUCTION_EXEMPT_MODULES",
                          "READER_EXEMPT_MODULES"}

    runner = source[source.index(
        "def test_no_module_under_tests_mutates_a_pinned_revision"):]
    assert "all_modules()" in runner.split("def test_", 2)[1]

    stated = source[:source.index("def _executes_source(")]
    assert "This rule names no exempt module" in stated
    assert "takes a working-tree path" in stated

    # The whole suite, conftest.py included, is what the rule is run over.
    assert {path.name for path in all_modules()} \
        == {path.name for path in TESTS_DIR.glob("*.py")}


@pytest.mark.parametrize("planted", [
    pytest.param(
        "import subprocess, sys\n"
        "def probe(repo, tmp_path):\n"
        "    pristine = repository_file_at(REL, validation_file=THIS_FILE,\n"
        "                                  bound=ENDPOINT)\n"
        "    (repo / REL).write_text(pristine.replace(OLD, NEW, 1))\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=repo)\n",
        id="pinned-read-run-under-pytest-in-a-subprocess"),
    pytest.param(
        "def probe(tmp_path):\n"
        "    text = repository_file_at(REL, revision=BASELINE_OF_A_STORY)\n"
        "    target = tmp_path / 'subject.py'\n"
        "    target.write_text(text)\n"
        "    return load_mutant(target, [(OLD, NEW)], name='subject',\n"
        "                       tmp_path=tmp_path)\n",
        id="pinned-read-loaded-through-the-shared-mutation-loader"),
    pytest.param(
        "import subprocess\n"
        "def probe(repo, revision, rel):\n"
        "    recovered = subprocess.run(\n"
        "        ['git', '-C', str(REPO_ROOT), 'show', f'{revision}:{rel}'],\n"
        "        capture_output=True, text=True).stdout\n"
        "    (repo / rel).write_text(recovered)\n"
        "    subprocess.run(['python', '-m', 'pytest'], cwd=repo)\n",
        id="pinned-git-show-written-and-executed"),
])
def test_the_mutation_scan_reports_a_planted_control(planted):
    """Its reach demonstrated rather than asserted, on the same terms as the
    two scans above: a scan with no planted violation is indistinguishable from
    one that has stopped looking."""
    flags = mutation_controls(planted, "probe.py")
    assert len(flags) == 1, flags


def test_the_mutation_scan_follows_the_execution_through_a_helper_beside_it():
    """The shape the one known instance actually wrote: the spawn is one line
    of a helper defined beside the control rather than inline in it."""
    planted = (
        "import subprocess, sys\n"
        "def run_one_test(repo, rel, test):\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest', rel],\n"
        "                          cwd=repo, capture_output=True)\n"
        "def probe(repo):\n"
        "    pristine = repository_file_at(REL, validation_file=THIS_FILE,\n"
        "                                  bound=ENDPOINT)\n"
        "    (repo / REL).write_text(pristine)\n"
        "    return run_one_test(repo, REL, 'test_it')\n"
    )
    assert len(mutation_controls(planted, "probe.py")) == 1


@pytest.mark.parametrize("benign", [
    pytest.param(
        "def probe(tmp_path):\n"
        "    return load_mutant(REPO_ROOT / REL, [(OLD, NEW)], name='m',\n"
        "                       tmp_path=tmp_path)\n",
        id="the-shared-loader-against-a-working-tree-path"),
    pytest.param(
        "import subprocess, sys\n"
        "def probe(repo):\n"
        "    before = repository_file_at(REL, validation_file=THIS_FILE,\n"
        "                                bound=BASELINE)\n"
        "    subprocess.run([sys.executable, '-m', 'pytest'], cwd=repo)\n"
        "    assert before != (REPO_ROOT / REL).read_text()\n",
        id="a-bounded-read-that-is-only-compared"),
    pytest.param(
        "def probe(tmp_path):\n"
        "    before = repository_file_at(REL, validation_file=THIS_FILE,\n"
        "                                bound=BASELINE)\n"
        "    (tmp_path / 'evidence.py').write_text(before)\n",
        id="a-bounded-read-written-but-never-executed"),
    pytest.param(
        "import subprocess\n"
        "def probe(repo, revision, rel):\n"
        "    recovered = subprocess.run(\n"
        "        ['git', '-C', str(REPO_ROOT), 'show', f'{revision}:{rel}'],\n"
        "        capture_output=True, text=True).stdout\n"
        "    (repo / rel).write_text(recovered)\n",
        id="a-bounded-git-show-written-but-never-executed"),
    pytest.param(
        "import subprocess, sys\n"
        "def probe(repo):\n"
        "    pristine = (REPO_ROOT / REL).read_text(encoding='utf-8')\n"
        "    (repo / REL).write_text(pristine.replace(OLD, NEW, 1))\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=repo)\n",
        id="the-repaired-working-tree-shape"),
])
def test_the_mutation_scan_leaves_these_alone(benign):
    """What it must not report. The pairing is the subject, so each half on its
    own is unreported — and the last case is the shape the repair merged, which
    has to stay unreported or the rule would forbid the fix it argues for."""
    assert mutation_controls(benign, "probe.py") == []


# --------------------------------------------------------------------------
# The fourth rule's regression set: the one known true positive, recovered at
# the bound of the story that repaired it
# --------------------------------------------------------------------------


#: The file that carried the one known instance, and the validation file of the
#: story whose run commit repaired it. The pre-repair text is that story's
#: baseline — the parent of its run commit — resolved through the shared reader
#: rather than written here as a sha, so a rebase does not move it.
THE_REPAIRED_MUTATION_CONTROL = "tests/test_story_029_validation.py"
THE_STORY_THAT_REPAIRED_IT = "tests/test_retry_routing.py"


def before_the_repair() -> str:
    """The one known true positive, as it stood before it was repaired."""
    return repository_file_at(
        THE_REPAIRED_MUTATION_CONTROL,
        validation_file=REPO_ROOT / THE_STORY_THAT_REPAIRED_IT,
        bound=BASELINE, repo=REPO_ROOT)


def test_the_recovered_text_really_is_the_pre_repair_text():
    """The regression case leans on this recovery, so it is asserted rather
    than assumed: recovered, different from today's, and carrying the pinned
    read the repair removed."""
    before = before_the_repair()
    assert "def test_" in before
    assert before != (REPO_ROOT / TODAY[THE_REPAIRED_MUTATION_CONTROL]).read_text(
        encoding="utf-8")
    assert "bound=ENDPOINT" in before


def test_the_mutation_scan_reports_the_one_known_instance():
    """The only direct evidence this rule would have caught what it is for.

    Everything else a new scan can say about itself — planted controls, benign
    probes, a clean suite — is about its reach in the abstract.
    """
    flags = mutation_controls(before_the_repair(),
                              Path(THE_REPAIRED_MUTATION_CONTROL).name)
    assert flags, "the pre-repair mutation control was expected to be reported"


def test_the_same_file_in_the_working_tree_is_reported_by_nothing():
    """The other half of the same evidence: flagged before, clean after, and
    clean under all four rules rather than only this one."""
    after = (REPO_ROOT / TODAY[THE_REPAIRED_MUTATION_CONTROL]).read_text(
        encoding="utf-8")
    name = Path(TODAY[THE_REPAIRED_MUTATION_CONTROL]).name
    assert mutation_controls(after, name) == []
    assert flagged_calls(after, name) == []
    assert module_construction(after, name) == []
    assert git_text_reads(after, name) == []


def test_the_three_rules_are_stated_separately_and_none_derives_another():
    """Three rules, three purposes. None is described as getting its
    enforcement from another, and no scan calls another.

    Read off the source rather than asserted about it, because "these are
    separate" is exactly the kind of claim that stays written down after it
    has stopped being true.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    scans = ("module_construction", "git_text_reads", "mutation_controls")
    for name in scans:
        called = {inner.func.id for inner in ast.walk(functions[name])
                  if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)}
        assert called.isdisjoint(set(scans) - {name}), name

    # Three vocabularies, pairwise disjoint: no rule's subject is expressible
    # in another's terms, which is what "separate rules" means here.
    vocabularies = (
        set(MODULE_CONSTRUCTORS + SOURCE_EXECUTORS),
        set(CONTENT_SUBCOMMANDS),
        set(REVISION_KEYWORDS + PATH_WRITES + LOADER_INTERFACE),
    )
    for index, vocabulary in enumerate(vocabularies):
        for other in vocabularies[index + 1:]:
            assert vocabulary.isdisjoint(other), vocabulary & other

    for rule in ("no module under tests/ builds a module at runtime",
                 "only the shared reader asks git for a repository file's text",
                 "a mutation control mutates the working tree, never a pinned"):
        assert rule in source, rule


@pytest.mark.parametrize("scan", ["module_construction", "git_text_reads",
                                  "mutation_controls", "history_reads"])
def test_each_new_rule_states_what_it_does_not_cover(scan):
    """The narrowness this module already states about itself, required of
    each new rule as well — and the three limits named by this story's
    acceptance criteria are checked by name, so a rewritten paragraph that
    quietly drops one goes red.

    `history_reads` is held to the same shape by the same test rather than by a
    second one beside it: a rule whose limits are stated in a paragraph nothing
    reads is a rule whose limits go stale.
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
THIS_STORYS_VALIDATION_FILE = "tests/test_git_history_loading_retired.py"

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
        assert before != (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8"), rel
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
        after = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
        assert module_construction(after, Path(TODAY[rel]).name) == [], rel
        assert git_text_reads(after, Path(TODAY[rel]).name) == [], rel


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
        after = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
        assert flagged_calls(after, Path(TODAY[rel]).name) == [], rel


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
#:
#: These paths are the ones those stories' scopes declared, and they resolve
#: against the synthetic repository `synthetic_story` builds rather than
#: against this one — so `prompts/tester.md` keeps the name it carried when
#: story-010 named it, and does not follow story-071's rename of the shipped
#: template to `prompts/story-tester.md`.
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
    # Named at the path it has now. story-038 renamed it, and the range it
    # resolves to is unchanged: the module declares its origin in
    # `conftest.STORY_ORIGINS`, so the resolution still reaches story-007's
    # own run commit rather than the rename's.
    validation_file = REPO_ROOT / "tests" / "test_stage_output_ownership.py"
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


# --------------------------------------------------------------------------
# A fifth rule: a module under tests/ is named for what it checks
#
# Its own rule again, and it shares nothing with the four above but the file it
# lives in. Those are about what an assertion may do; this one is about what a
# module may be called. It is here because this is where the standing rules
# over the suite live, and because the stage that owns validation is the stage
# that names validation modules.
#
# The defect it prevents is not a vacuous assertion but an unfindable one. A
# module named `test_story_017_validation.py` says that somebody once worked on
# a numbered thing. The number is meaningful while the story is in flight and
# meaningless the moment it merges, and a reader looking for the revert check
# can then find it only by grep. story-038 renamed thirty-four such modules;
# the convention that keeps them renamed is held by three mechanisms — a
# plan-time refusal in `orchestration/plan_validation.py`, this scan, and one
# sentence in `prompts/story-tester.md`. The prompt is the layer that failed at this
# twice before and is not the one relied on.
#
# It matches on the *digits*, which is the whole of what makes such a name a
# story number: `test_story_parser.py` and `test_story_coordinator.py` are
# named for their subjects — the story parser and the story coordinator — and
# a rule keyed on the `test_story_` prefix alone would report both. The control
# for that is written below rather than argued for here.
#
# What it does not cover, stated because this is where a reader meets it:
#
#   * **any other uninformative name.** `test_misc.py`, `test_stuff.py` and
#     `test_the_thing.py` are as unfindable as a story number and are not
#     reported. This rule knows one pattern, which is the one a stage
#     mechanically produced thirty-four times; the general question of whether
#     a name describes its subject is not decidable by a regular expression and
#     is not claimed here.
#   * **a story number written anywhere but the module's own name.** A module
#     named for its behaviour whose tests, constants and prose are all keyed to
#     a story number is unreported, and legitimately so — a module states which
#     story it validates in `conftest.STORY_ORIGINS`, and the historical path
#     it declares there *is* a story-numbered name.
#   * **a directory.** The scan reads the modules directly under tests/, which
#     is where every module in this suite lives, and a subdirectory this suite
#     does not have is not searched.
#
# And the same standing limit as everything mechanical here: it is not
# tamper-proof. An edit deleting this check alongside a genuinely forced repair
# is not caught, at any granularity, because deleting the check that fails you
# satisfies the revert rule's own definition of a forced edit.
# --------------------------------------------------------------------------


#: A module named for the story number that produced it. Anchored at the start
#: and requiring at least one digit after the prefix: the digits are what make
#: the name a story number rather than a subject, and without them
#: `test_story_parser.py` and `test_story_coordinator.py` — named for the story
#: parser and the story coordinator, which are subjects — would be reported.
STORY_NUMBERED_MODULE = re.compile(r"^test_story_\d+")

#: The two modules whose names begin with the prefix and are not story numbers.
#: Named here so the over-match control below is about *these* files rather
#: than about strings resembling them.
NAMED_FOR_A_STORY_SUBJECT = ("test_story_parser.py", "test_story_coordinator.py")


def story_numbered_modules(directory: Path) -> list[str]:
    """Every module under `directory` named for a story number.

    A search over the directory rather than a comparison against a listing: a
    listing is a second copy of the answer, and it goes stale silently the
    moment a module lands that nobody remembered to add to it. The directory
    is a parameter so the same search the live suite is held to can be run
    over a directory with a violation planted in it.
    """
    return [path.name for path in sorted(Path(directory).glob("*.py"))
            if STORY_NUMBERED_MODULE.match(path.name)]


def test_no_module_under_tests_is_named_for_a_story_number():
    """The rule, run rather than inspected."""
    found = story_numbered_modules(TESTS_DIR)
    assert found == [], (
        "name a validation module for the behaviour it validates: "
        + ", ".join(found))
    # The companion assertion the search needs: a search over zero files
    # reports nothing for the wrong reason.
    assert len(all_modules()) >= 15


def test_the_naming_scan_reports_a_planted_violation(tmp_path):
    """Its reach demonstrated rather than asserted, on the same terms as the
    three scans above: a scan with no planted violation is indistinguishable
    from one that has stopped looking at the directory it was pointed at."""
    for name in ("test_revert_check.py", "test_story_017_validation.py",
                 "test_story_006_single_reader.py", "conftest.py"):
        (tmp_path / name).write_text("def test_it():\n    pass\n",
                                     encoding="utf-8")

    assert story_numbered_modules(tmp_path) == [
        "test_story_006_single_reader.py", "test_story_017_validation.py"]


def test_the_scan_leaves_the_two_modules_named_for_a_story_subject_alone():
    """The over-match control, over the real files rather than over strings
    resembling them: both exist under those names now, and neither is
    reported."""
    for name in NAMED_FOR_A_STORY_SUBJECT:
        assert (TESTS_DIR / name).is_file(), name
        assert STORY_NUMBERED_MODULE.match(name) is None, name
    assert set(NAMED_FOR_A_STORY_SUBJECT) <= {p.name for p in all_modules()}

    # And the control for that control: the same prefix followed by digits is
    # reported, so "not reported" above is a property of these names.
    assert STORY_NUMBERED_MODULE.match("test_story_038_validation.py")


# --------------------------------------------------------------------------
# A sixth rule: a test reads a live harness artifact only when that artifact is
# what it is about
#
# Its own rule again, and it shares nothing with the five above. Those are about
# a comparison that cannot fail, an instrument built out of history, where a
# file's historical text is read, what a control may mutate, and what a module
# may be called. This one is about where an assertion's *inputs* come from.
#
# The shipped workflow, `rules/execution-rules.json`, this repository's own
# `.harness/config.yaml`, the prompt templates and the schemas are live harness
# artifacts. They are legitimate *subjects*: an assertion about what this
# harness ships has to read what it ships. They are usually the wrong *input*.
# An assertion about how the coordinator routes needs *a* workflow, not the
# shipped one, and reading the live one there turns a deployment fact into
# something the suite enforces.
#
# Observed, not predicted. story-047 granted one stage a `max_self_routes`
# budget — a correct one-line change — and reddened four assertions in a module
# with nothing to say about whether that grant was right. Adding a stage or
# renaming an artifact does the same thing to a different set.
#
# **This rule reports rather than forbids, and the declared list is why.**
# The list records every module the scan reports, each with a stated reason
# saying why the shipped artifact is that module's *subject* rather than an
# input to it. It is asserted *equal* to what the scan reports, in both
# directions: a module that joins the set fails because it is not on the list,
# and a module converted off the set fails because the list still names it. A
# subset assertion either way would let one of those pass silently.
#
# It reads two routes to the same artifact.
#
# The first matches on the **shape of the path**, never on a helper name — the
# same reasoning the second and third rules use. A path built by joining onto
# one of the module-level names that stand for this repository, reaching one of
# the five artifact families. Both join idioms the suite writes are read, the
# `/` operator and `.joinpath(...)`, because story-004 forces the second one in
# places and covering only the first would leave an unreported route to the
# same read. Renaming the local that holds the result changes nothing.
#
# The second, added by story-048, matches the **workflow resolvers**:
# `conftest.shipped_workflow` and `harness_config.load_workflow`, handed one of
# those same repository-root names or handed nothing at all. Those two join the
# path *inside* the helper, in a module the path-shape route is not reading
# while it reads this one — so before the widening they were invisible, and the
# idiom the suite actually writes was the invisible one. Widening it added
# eleven modules to the report in a single commit.
#
# What it does not cover, stated here because this is where a reader meets it:
#
#   * **an equivalent read of some other artifact reached through a
#     helper in another module.** `harness_config.load_rules(REPO_ROOT)` and
#     `context_assembler.load_template(REPO_ROOT, "story-implementer.md")` resolve
#     live artifacts and are not reported: the path is joined inside the helper,
#     in a module this scan is not looking at while it reads this one, and only
#     the two *workflow* resolvers are named above. The scan reads what a
#     module's own source states and follows nothing across a module boundary —
#     including a helper one module borrows from another, which is why
#     converting a module whose helpers another borrows puts the borrower in
#     scope without the scan ever saying so.
#   * **the difference between a subject and an input.** This is the rule's
#     central limit and it is not a small one. The scan cannot tell an assertion
#     *about* the shipped workflow — which must read it — from an assertion that
#     merely needed *a* workflow and reached for the shipped one. Every report is
#     a place to ask the question, not a verdict that the read is wrong, and the
#     reason recorded beside each listed module is a human answer to it rather
#     than something the scan derived.
#   * **a read resolved against a name this scan does not recognise.** A path
#     built from a fixture parameter, an `os.environ` lookup, or a string
#     concatenation rather than a `/` join is not seen; nor is a resolver handed
#     a root it computed, such as `Path(module.__file__).parents[1]`. The scan
#     does not evaluate expressions or track values.
#
# And the same standing limit as everything mechanical here: it is not
# tamper-proof. An edit deleting this check alongside a genuinely forced repair
# is not caught, at any granularity, because deleting the check that fails you
# satisfies the revert rule's own definition of a forced edit.
# --------------------------------------------------------------------------


#: The five families of live harness artifact, as the path segment each is
#: reached through from the repository root. Naming them here is not the
#: hard-coding the rule warns about: this scan's *subject* is what this
#: repository ships, so the families are the thing being asserted rather than an
#: input smuggled into an assertion about something else.
LIVE_ARTIFACT_SEGMENTS = (
    "workflows",     # the shipped workflow definition
    "rules",         # rules/execution-rules.json
    "prompts",       # the prompt templates
    "schemas",       # the artifact schemas
    "config.yaml",   # this repository's own .harness/config.yaml
)


def _path_fragments(node: ast.AST) -> list[str]:
    """Every literal path segment inside an expression.

    Fragments are split on "/" so a segment written as part of a longer literal
    — `REPO_ROOT / ".harness/config.yaml"` — reads the same as one written on
    its own. A computed field of an f-string contributes nothing, which is what
    keeps a resolved name from being mistaken for a literal segment.
    """
    fragments: list[str] = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            fragments.extend(inner.value.split("/"))
        elif isinstance(inner, ast.JoinedStr):
            for value in inner.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    fragments.extend(value.value.split("/"))
    return fragments


def _is_path_join(node: ast.AST) -> bool:
    """Whether an expression joins segments onto a path.

    Both idioms the suite writes: the `/` operator, and `.joinpath(...)`, which
    story-004 forces in the places where a `/` join would name a path under
    this repository's own run directory. Covering only the operator would leave
    the second idiom an unreported route to the same read.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "joinpath")


#: The two helpers that resolve a workflow definition out of a harness root.
#: `conftest.shipped_workflow` wraps `harness_config.load_workflow`, and both
#: reach `workflows/<name>.json` under the root they are handed. The join
#: happens *inside* the helper, in a module the path-shape route above is not
#: reading while it reads this one, which is why that route reports neither.
#: story-048 widened the scan to them because they are the idiom the suite
#: actually writes: the path-shape route saw seven modules and the helper route
#: sees twenty more resolving the very same artifact.
WORKFLOW_RESOLVERS = ("shipped_workflow", "load_workflow")


def _callee_name(node: ast.Call) -> str | None:
    """The called name, however it is qualified.

    `conftest.shipped_workflow`, a bare `shipped_workflow` under a
    `from conftest import ...`, and `harness_config.load_workflow` are the same
    call. Matched on the name alone for the reason `_is_subprocess_call` gives:
    requiring the module to be spelled a particular way makes the check
    evadable by an import statement.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_a_repository_root_name(node: ast.AST) -> bool:
    """Whether this argument *is* one of the names standing for this repository.

    Narrower than `_names_the_repository_root`, deliberately, and not a second
    copy of it: that one asks whether such a name occurs anywhere inside an
    expression, which is the right question for a path join and the wrong one
    for an argument — `Path(REPO_ROOT).parent / "elsewhere"` names the root and
    does not resolve to it. Here the argument must *be* the name, written bare
    or qualified by the module that holds it (`conftest.HARNESS_ROOT`).
    """
    if isinstance(node, ast.Name):
        return node.id in REPOSITORY_ROOT_NAMES
    return isinstance(node, ast.Attribute) and node.attr in REPOSITORY_ROOT_NAMES


def _resolves_a_workflow_through_a_helper(node: ast.AST) -> bool:
    """Whether this call loads a workflow out of *this* repository.

    Two forms, and both are the same read:

      * a root argument that is one of the module-level names standing for this
        repository — `shipped_workflow(REPO_ROOT, ...)`,
        `load_workflow(HARNESS_ROOT, name, config)`;
      * no root argument at all — `conftest.shipped_workflow()`, whose default
        root *is* this repository. A defaulted read reaches the same file as a
        spelled one, so leaving it out would have made the widening evadable by
        deleting an argument.

    A root the test built for itself — a `tmp_path` harness, a fixture
    parameter, a local — is not this rule's business, exactly as for the path
    shape above.
    """
    if not isinstance(node, ast.Call):
        return False
    if _callee_name(node) not in WORKFLOW_RESOLVERS:
        return False
    root = next((keyword.value for keyword in node.keywords
                 if keyword.arg == "root"), None)
    if root is None and node.args:
        root = node.args[0]
    if root is None:
        return True
    return _is_a_repository_root_name(root)


def live_artifact_reads(source: str, module: str) -> list[Flag]:
    """Every path in one module that resolves a live harness artifact.

    A join rooted at one of the module-level names that stand for this
    repository — `_names_the_repository_root`, the same recognition the first
    and third rules use rather than a second copy of it — whose literal
    segments reach one of the five families. A path a test built for itself,
    under `tmp_path` or a local it assembled, names none of those roots and is
    not this rule's business.

    Only the outermost join of a chain is reported, so
    `REPO_ROOT / "prompts" / "story-tester.md"` is one read rather than two.

    No exemption is applied, here or by any caller. The grandfathered list is
    not an exemption: it is asserted equal to what this returns, so it records
    the set rather than hiding it.
    """
    tree = ast.parse(source)
    nested = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) \
                and _is_path_join(node.left):
            nested.add(id(node.left))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "joinpath" and _is_path_join(node.func.value):
            nested.add(id(node.func.value))

    flags = []
    for node in ast.walk(tree):
        if _resolves_a_workflow_through_a_helper(node):
            flags.append(Flag(
                module=module, line=node.lineno,
                reason=(f"resolves the live harness artifact under "
                        f"{LIVE_ARTIFACT_SEGMENTS[0]!r} through "
                        f"{_callee_name(node)}; that is right when the shipped "
                        f"artifact is what the assertion is about, and wrong "
                        f"when the assertion merely needed one — build a "
                        f"fixture for the second case"),
            ))
            continue
        if not _is_path_join(node) or id(node) in nested:
            continue
        if not _names_the_repository_root(node):
            continue
        reached = [segment for segment in _path_fragments(node)
                   if segment in LIVE_ARTIFACT_SEGMENTS]
        if not reached:
            continue
        flags.append(Flag(
            module=module, line=node.lineno,
            reason=(f"resolves the live harness artifact under {reached[0]!r} "
                    f"against this repository's root; that is right when the "
                    f"shipped artifact is what the assertion is about, and "
                    f"wrong when the assertion merely needed one — build a "
                    f"fixture for the second case"),
        ))
    return flags


def live_artifact_reading_modules() -> list[str]:
    """Every module under tests/ the scan reports, by name.

    Discovered by globbing, never by naming, so a module that starts reading a
    live artifact joins this set the moment it lands — which is what makes the
    equality below bite.
    """
    return sorted(
        path.name for path in all_modules()
        if live_artifact_reads(path.read_text(encoding="utf-8"), path.name)
    )


#: Every module permitted to resolve a live harness artifact, and why. The
#: reason is the answer to the question the scan cannot ask: is the shipped
#: artifact the *subject* of this module's assertions, or an input to them? A
#: module whose answer is "an input" does not belong here — it belongs on a
#: workflow it builds for itself, which is what story-048 converted a dozen
#: modules to.
#:
#: A mapping rather than a tuple, so a name cannot sit here without a reason.
#: It is asserted equal to what the scan reports in both directions, so it
#: cannot grow silently and cannot keep a name whose module was converted.
DECLARED_LIVE_ARTIFACT_READERS = {
    "test_clean_clone_check.py":
        "its workflow reads were converted by story-048; what remains is the "
        "schema this repository ships for the clean-clone record and the retry "
        "ceiling it declares in rules/execution-rules.json, both of which are "
        "shipped declarations rather than inputs",
    "test_config_keys_are_obeyed.py":
        "its subject is this repository's own configuration — whether every "
        "key .harness/config.yaml declares is obeyed by the harness that reads "
        "it",
    "test_configured_test_location.py":
        "its subject is the configured test location and the token the shipped "
        "workflow carries for it, which only the shipped declaration states",
    "test_contract_assertions_bite.py":
        "its subject is whether this repository's own contract assertions fail "
        "when violated, which is a claim about the artifacts it ships",
    "test_coordinator_runs_the_suite.py":
        "every run it drives goes through a workflow it builds, where the "
        "suite-run declaration, the stage names and the artifact names are "
        "inputs. What remains has what is shipped as its subject: the schemas "
        "this repository ships for the suite-run record, the test results and "
        "the two existing check records, together with the inventory the new "
        "one is registered in; the shipped workflow, because "
        "'the coordinator writes this record so no stage is asked to satisfy "
        "it' is a claim about what this repository deploys, and because which "
        "shipped schemas describe a coordinator suite record is answered by "
        "the artifacts this repository's own declarations name; and "
        "prompts/story-verifier.md and prompts/story-tester.md, which are where a stage "
        "reads who runs the suite and what it is given afterwards, so the "
        "words those templates carry are the criterion rather than an input "
        "to one",
    "test_correction_pass.py":
        "its runs are driven against a workflow it builds, where the "
        "declaration, the categories and the artifact name are inputs. What "
        "remains has what is shipped as its subject: the schemas this "
        "repository ships for the correction-pass record and the verification "
        "result, the inventory the first is registered in, the retry ceiling "
        "in rules/execution-rules.json, and prompts/story-verifier.md, which is "
        "where a verifier reads what may go in the field and so is the only "
        "place the words-and-never-behaviour constraint can be asserted",
    "test_documented_claim_support.py":
        "its runs are driven by a workflow it builds, which is where the "
        "declaration story-051 adds is an input. What remains has what is "
        "shipped as its subject: the schema this repository ships for the "
        "claim-support record and the inventory it is listed in; and "
        "prompts/story-verifier.md and prompts/documenter.md, whose new criteria "
        "are asserted on the rendered prompt because what reaches the agent "
        "is the claim — a rendering that needs the shipped workflow to "
        "resolve the routing table the verifier's own template prints, and "
        "which reads this deployment's documentation retry category for the "
        "same reason story-045's module reads its routing table",
    "test_documenter_before_verification.py":
        "its runs were converted by story-048 to a workflow it builds; what "
        "remains are four assertions whose subject is what is deployed. "
        "test_the_verifier_declares_exactly_the_three_categories, "
        "test_the_documentation_when_tells_the_document_from_the_code_it_"
        "describes and test_the_two_existing_routes_are_preserved read this "
        "deployment's own routing table, which is story-045's acceptance "
        "criterion and which a built table would assert the builder's "
        "arguments back to itself. test_the_template_restates_no_category_"
        "destination_or_when, test_the_template_declares_both_placeholders "
        "and test_the_role_layer_says_the_documenters_output_is_part_of_the_"
        "subject read prompts/story-verifier.md, the template this repository ships",
    "test_escalation_summary.py":
        "its workflow reads were converted by story-048; what remains is the "
        "retry ceiling this repository declares in rules/execution-rules.json, "
        "which a summary written *at* the ceiling is a summary about",
    "test_forced_adaptation_declaration.py":
        "its plan-time half runs against a fixture workflow and fixture roots "
        "it builds, where the restricted stage and its prefix are inputs. What "
        "remains has what is shipped as its subject: the story schema this "
        "repository ships, which is where the declaration's contract is "
        "stated; orchestration/plan_validation.py's own prose and "
        "prompts/planner.md, whose corrected passages are the story's "
        "criterion rather than an input to one; this repository's committed "
        "stories, because the two runs the rule was reasoned from are "
        "reconstructed rather than invented; the shipped workflow, because "
        "those reconstructions were planned against this deployment's "
        "restriction and against any other they would be reconstructions of a "
        "different story; and the scan over workflows/, rules/, schemas/ and "
        "prompts/, whose subject is which of the files this repository ships "
        "name the field at all",
    "test_harness_layer_extraction.py":
        "its subject is which prompts this repository ships carry a shared "
        "partial and which do not, so the templates, the two partials and the "
        "prompts a real run of the shipped workflow renders are the artifacts "
        "under test rather than inputs to it; the planner half drives the real "
        "scripts/l5-plan, which resolves this repository as its own harness "
        "root and so can only be compared against what is deployed",
    "test_plan_assignment_refusal.py":
        "the refusal is decided against this repository's own declarations: "
        "which stage may create what, here, in this deployment",
    "test_plan_commit.py":
        "its subject is what a plan commit of *this* repository contains",
    "test_plan_run_offer.py":
        "everything it drives runs against a harness root it builds under "
        "tmp_path, whose l5-run is a stub; what remains is the planner prompt "
        "as this deployment renders it, which is where the story's criterion "
        "about what the planner is and is no longer told lives — a rendering "
        "only the shipped template, workflow and rules can produce",
    "test_plan_time_validation.py":
        "it reads a real story against the real workflow, which is the pairing "
        "the plan-time check exists to validate",
    "test_planner_injection.py":
        "its workflow reads were converted by story-048; what remains is the "
        "planner template this repository ships, the story schema it injects "
        "and the schema directory it is drawn from — the artifacts under test "
        "rather than inputs to the test — together with the one end-to-end "
        "case that drives l5-plan, which resolves this repository as its own "
        "harness root and so can only be compared against what is deployed",
    "test_prompt_workflow_ownership.py":
        "the shipped prompts and the shipped workflow definitions are the "
        "whole of its subject: it asks whether the file this repository holds "
        "under prompts/ is named for the workflow this repository's own "
        "definitions say owns it. A built definition could not carry that "
        "claim — it would assert the builder's own arguments back to itself — "
        "and every rule the module states is asserted a second time over an "
        "arrangement constructed under tmp_path, which is where the inputs "
        "are built rather than read",
    "test_refactor_workflow.py":
        "the two shipped definitions are the whole of its subject: it asks "
        "whether this repository ships a refactor workflow that drops the "
        "ownership and revert declarations, and whether the same coordinator "
        "enforces differently under it and under story-workflow. That "
        "comparison is between the definitions this repository deploys, and a "
        "built workflow could not make it. The prompts, the schemas and the "
        "census it reads are shipped artifacts on the same terms",
    "test_retry_history.py":
        "its workflow reads were converted by story-048; what remains is the "
        "retry ceiling this repository declares in rules/execution-rules.json "
        "and the schema it ships for the artifact under test, both of which "
        "are shipped declarations rather than inputs",
    "test_retry_routing.py":
        "three of its assertions have the shipped definition as their subject: "
        "the retry-ceiling search over this repository, the check that no "
        "shipped stage declares a ceiling of its own, and the check that no "
        "name this deployment's routing table chooses is written into the "
        "orchestration source. Its mechanism half was converted to a workflow "
        "it builds",
    "test_revert_baseline.py":
        "its workflow reads were converted by story-048; what remains is the "
        "schema this repository ships for the record the check writes and the "
        "inventory it is listed in, including the assertion that the baseline "
        "directory carries no schema of its own",
    "test_revert_check.py":
        "its workflow reads were converted by story-048; what remains is the "
        "schema this repository ships for the record the check writes, the "
        "schema inventory it is listed in, and this repository's own stories",
    "test_schema_inventory_location.py":
        "its subject is the schema inventory this repository ships, against "
        "schemas/manifest.json",
    "test_self_routing_retry.py":
        "its workflow reads were converted by story-048; what remains is the "
        "schema this repository ships for the self-route record, the schema "
        "inventory it is listed in, and the retry ceiling declared in "
        "rules/execution-rules.json",
    "test_shipped_workflow_is_valid.py":
        "the shipped workflow is the whole of its subject: whether this "
        "deployment's definition is well-formed and says what this project "
        "intends of it",
    "test_stage_baseline.py":
        "its workflow reads were converted by story-048; what remains is this "
        "repository's schema inventory and the assertion that the baseline "
        "directory it names carries no schema of its own, both of which are "
        "claims about what is shipped",
    "test_stage_tool_grants.py":
        "its subject is whether this deployment grants its stages the tools "
        "they need, which is a fact about what it ships",
    "test_suite_census.py":
        "the workflow it runs is built and the census those runs configure is "
        "written by the fixture, because the comparison is a mechanism. What "
        "it resolves live is this repository's own census at .harness/census.py "
        "and the schema shipped for the record the check writes, both of which "
        "are the subject: what this repository counts, and what a reader of "
        "the artifact is told the count does not mean",
    "test_suite_parallelism.py":
        "what this deployment ships is the whole of its subject: that the "
        "command .harness/config.yaml configures runs the suite in parallel "
        "without pinning a core count, and that the CI workflow this "
        "repository ships installs the tracked dependency declaration that "
        "command needs rather than a hand-listed set. The scan reaches it "
        "through .github/workflows/tests.yml, whose path carries a 'workflows' "
        "segment; a fixture workflow could not answer either question, because "
        "both are about the files this repository deploys and about nothing "
        "else",
    "test_suite_run_denial.py":
        "every run it drives goes through a workflow it builds, against a "
        "target it constructs, where the may_not_run_suite declaration, the "
        "stage names and the configured test command are inputs. What it "
        "resolves live has what is shipped as its subject: both shipped "
        "workflow definitions, because 'this deployment restricts the tester, "
        "the documenter and the verifier and deliberately not the implementer' "
        "is a claim about what it deploys and about nothing else; and "
        "prompts/story-tester.md, because the criterion is that this "
        "repository's own tester prompt shortened and names the guard, which "
        "only the shipped template can answer",
    "test_undeclared_config_keys.py":
        "its subject is this repository's own configuration keys",
    "test_validation_module_naming.py":
        "its subject is this repository's own module names",
    "test_workflow_proposal.py":
        "the workflows it plans under are built and written into roots it "
        "owns; what it resolves live are the shipped declarations that are its "
        "subject — that every definition under workflows/ says when it "
        "applies, that prompts/workflow-selector.md enumerates no workflow of "
        "its own and carries no stage facts, that the selection schema is in "
        "this repository's manifest, and that scripts/l5-plan reads the "
        "configured workflow key on no path. The selector prompt is also "
        "copied into the harness root its sessions run out of, because 'a "
        "third definition is selectable with no edit to it' is a claim about "
        "the file this repository ships",
    "test_workflow_selection.py":
        "the workflows it runs are built and written into roots it owns; what "
        "it resolves live are the shipped declarations that are its subject — "
        "the story schema's optional workflow field, the planner template that "
        "must carry the selected name and ask for it to be recorded, and this "
        "repository's own committed story artifacts, which must still parse "
        "unchanged. The planner template is also copied into the harness root "
        "the l5-plan cases run out of, because the template a session renders "
        "is the one this repository ships",
}


def test_the_modules_reading_a_live_artifact_are_exactly_the_declared_ones():
    """The rule, run rather than inspected, and asserted in both directions.

    Set equality rather than either subset. A module that begins reading a live
    artifact is absent from the list and fails; a module converted to a fixture
    stops being reported and its stale entry fails. Written as two explicit
    differences so the failure says which of the two happened.
    """
    reported = set(live_artifact_reading_modules())
    listed = set(DECLARED_LIVE_ARTIFACT_READERS)

    joined = sorted(reported - listed)
    assert not joined, (
        "these modules resolve a live harness artifact and are not declared "
        "permitted to. Ask the question the scan cannot: is what this "
        "repository ships the subject of the assertion, or an input to it? If "
        "it is an input, build the workflow the test needs with "
        "conftest.build_workflow; if it is the subject, declare it here with "
        "the reason: " + ", ".join(joined))

    left = sorted(listed - reported)
    assert not left, (
        "these modules no longer resolve a live harness artifact and must be "
        "removed from the declared list: " + ", ".join(left))

    assert reported == listed
    # The companion assertion the glob needs: a scan over zero files agrees
    # with an empty list for the wrong reason.
    assert len(all_modules()) >= 15
    assert reported


def test_every_declared_reader_states_why_the_shipped_artifact_is_its_subject():
    """A name with no reason is a name nobody asked the question about."""
    for name, reason in DECLARED_LIVE_ARTIFACT_READERS.items():
        assert (TESTS_DIR / name).is_file(), name
        assert reason.strip(), name
        assert len(reason.split()) >= 8, (name, reason)
    assert DECLARED_LIVE_ARTIFACT_READERS


@pytest.mark.parametrize("planted,segment", [
    pytest.param("P = REPO_ROOT / 'workflows' / 'story-workflow.json'\n",
                 "workflows", id="the-shipped-workflow"),
    pytest.param("P = REPO_ROOT / 'rules' / 'execution-rules.json'\n",
                 "rules", id="the-execution-rules"),
    pytest.param("T = (HARNESS_ROOT / 'prompts' / 'story-tester.md').read_text()\n",
                 "prompts", id="a-prompt-template"),
    pytest.param("S = REPO_ROOT / 'schemas' / 'story.schema.json'\n",
                 "schemas", id="a-schema"),
    pytest.param("C = REPO_ROOT / '.harness' / 'config.yaml'\n",
                 "config.yaml", id="the-target-configuration"),
    pytest.param("C = REPO_ROOT / '.harness/config.yaml'\n",
                 "config.yaml", id="the-target-configuration-in-one-literal"),
    pytest.param("def probe(name):\n"
                 "    return (REPO_ROOT / 'prompts' / f'{name}.md').read_text()\n",
                 "prompts", id="a-computed-leaf-under-a-live-family"),
    pytest.param("P = REPO_ROOT.joinpath('workflows', 'story-workflow.json')\n",
                 "workflows", id="the-joinpath-idiom-story-004-forces"),
])
def test_the_live_artifact_scan_reports_a_planted_violation(planted, segment):
    """Its reach demonstrated rather than asserted, on the same terms as the
    scans above: a scan with no planted violation is indistinguishable from one
    that has stopped looking. Each of the five families is planted, because a
    scan that had quietly lost one would still pass a control over the others.
    """
    flags = live_artifact_reads(planted, "probe.py")
    assert len(flags) == 1, flags
    assert segment in flags[0].reason


def test_the_live_artifact_scan_leaves_a_module_reading_its_own_fixture_alone():
    """The distinction stated as a control rather than as an absence.

    Two sources asking for the same five artifacts. The first builds a harness
    of its own under a temporary directory and reads that; the second reaches
    for what this repository ships. Only the second is reported, so "not
    reported" above is a property of where the path is rooted rather than of
    the scan having stopped looking.
    """
    against_a_fixture = (
        "def probe(tmp_path):\n"
        "    harness = tmp_path / 'harness'\n"
        "    workflow = harness / 'workflows' / 'story-workflow.json'\n"
        "    rules = harness / 'rules' / 'execution-rules.json'\n"
        "    prompt = harness / 'prompts' / 'tester.md'\n"
        "    schema = harness / 'schemas' / 'story.schema.json'\n"
        "    config = harness / '.harness' / 'config.yaml'\n"
        "    return workflow, rules, prompt, schema, config\n"
    )
    assert live_artifact_reads(against_a_fixture, "probe.py") == []

    against_this_repository = against_a_fixture.replace(
        "harness = tmp_path / 'harness'", "harness = REPO_ROOT")
    assert len(live_artifact_reads(against_this_repository, "probe.py")) == 0, (
        "a local rebound to the repository root is a stated limit: the scan "
        "does not track values")

    rooted = against_a_fixture.replace("harness /", "REPO_ROOT /")
    assert len(live_artifact_reads(rooted, "probe.py")) == 5


@pytest.mark.parametrize("planted", [
    pytest.param("W = conftest.shipped_workflow(REPO_ROOT, 'story-workflow')\n",
                 id="the-conftest-helper-with-a-root-argument"),
    pytest.param("W = conftest.shipped_workflow(conftest.HARNESS_ROOT)\n",
                 id="the-same-helper-with-a-qualified-root-name"),
    pytest.param("from conftest import shipped_workflow\n"
                 "W = shipped_workflow(HARNESS_ROOT, 'story-workflow')\n",
                 id="the-same-helper-imported-bare"),
    pytest.param("W = conftest.shipped_workflow()\n",
                 id="the-same-helper-with-the-root-defaulted"),
    pytest.param("W = harness_config.load_workflow(REPO_ROOT, NAME, config)\n",
                 id="the-loader-the-helper-wraps"),
])
def test_the_live_artifact_scan_reports_a_planted_helper_route_read(planted):
    """The widening story-048 made, demonstrated rather than asserted.

    Before it, every one of these resolved `workflows/<name>.json` under this
    repository and none was reported: the join happens inside the helper. A
    scan that had quietly lost the helper route would still pass the
    path-shape controls above, so each spelling the suite writes is planted
    here — qualified, bare, and defaulted.
    """
    flags = live_artifact_reads(planted, "probe.py")
    assert len(flags) == 1, flags
    assert "workflows" in flags[0].reason


def test_the_live_artifact_scan_leaves_a_module_that_builds_its_workflow_alone():
    """The other half of that control, and the one that makes the widening
    usable: a module whose only workflow comes from the builder is not
    reported, so "not reported" is a property of where the definition came
    from rather than of the scan having stopped looking at helpers.

    The two sources ask for the same thing and differ in exactly that.
    """
    built = (
        "W = conftest.build_workflow(\n"
        "    conftest.workflow_stage(outputs=('a.json',)),\n"
        "    conftest.workflow_stage(name=conftest.VERIFYING_STAGE))\n"
        "def harness(tmp_path):\n"
        "    return conftest.materialize_workflow(W, tmp_path / 'harness')\n"
        "def loaded(harness_root):\n"
        "    return harness_config.load_workflow(harness_root, W['name'], {})\n"
    )
    assert live_artifact_reads(built, "probe.py") == []

    reached_for = built.replace(
        "harness_config.load_workflow(harness_root, W['name'], {})",
        "harness_config.load_workflow(REPO_ROOT, W['name'], {})")
    assert len(live_artifact_reads(reached_for, "probe.py")) == 1


@pytest.mark.parametrize("benign", [
    pytest.param("P = REPO_ROOT / 'tests' / 'test_thing.py'\n",
                 id="a-path-outside-every-live-family"),
    pytest.param("P = REPO_ROOT / '.harness' / 'stories' / 'story-001.yaml'\n",
                 id="a-story-artifact-which-is-not-configuration"),
    pytest.param("P = tmp_path / 'workflows' / 'story-workflow.json'\n",
                 id="a-fixture-workflow-under-tmp_path"),
    pytest.param("def probe(harness_root):\n"
                 "    return harness_root / 'prompts' / 'tester.md'\n",
                 id="a-fixture-parameter-which-is-a-stated-limit"),
    pytest.param("W = conftest.shipped_workflow(harness_root, NAME)\n",
                 id="a-resolver-handed-a-root-the-test-owns"),
    pytest.param("R = harness_config.load_rules(REPO_ROOT)\n",
                 id="a-helper-mediated-read-of-another-artifact-a-stated-limit"),
])
def test_the_live_artifact_scan_leaves_these_alone(benign):
    """What it must not report, including two of its own stated limits: a read
    routed through a fixture parameter and a read whose path is joined inside a
    helper in another module are outside this rule by construction, and the
    prose above says so rather than leaving the silence to be discovered."""
    assert live_artifact_reads(benign, "probe.py") == []


def test_the_live_artifact_rules_stated_limits_are_in_the_module_and_are_true():
    """The limits are load-bearing — the grandfathered list is only honest if
    the reader knows what the scan cannot see — so they are asserted present
    and each is demonstrated by the controls above rather than only claimed.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    stated = source[:source.index("def live_artifact_reads(")]
    limits = stated[stated.rindex("What it does not cover"):]

    assert "helper in another module" in limits
    assert "subject and an input" in limits
    assert "evaluate expressions or track values" in limits
    assert "tamper-proof" in stated

    # And the claims are true of the scan, not just written above it. The
    # helper-boundary limit is now about the artifacts the two *workflow*
    # resolvers do not reach, which is what the prose above says.
    assert live_artifact_reads(
        "R = harness_config.load_rules(REPO_ROOT)\n", "probe.py") == []
    assert live_artifact_reads(
        "def probe(harness_root):\n"
        "    return harness_root / 'workflows' / 'story-workflow.json'\n",
        "probe.py") == []
    # The subject/input limit: the same expression, one legitimate and one not,
    # and the scan says exactly the same thing about both.
    subject = "W = (REPO_ROOT / 'workflows' / 'story-workflow.json').read_text()\n"
    an_input = "F = (REPO_ROOT / 'workflows' / 'story-workflow.json').read_text()\n"
    assert [flag.reason for flag in live_artifact_reads(subject, "a.py")] \
        == [flag.reason for flag in live_artifact_reads(an_input, "b.py")]


def test_this_rule_draws_nothing_from_the_five_above():
    """Six rules, six purposes. No scan calls another, and this one's
    vocabulary is expressible in none of theirs."""
    source = Path(__file__).read_text(encoding="utf-8")
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    others = ("flagged_calls", "undeclared_targets", "module_construction",
              "git_text_reads", "mutation_controls", "story_numbered_modules")
    called = {inner.func.id
              for inner in ast.walk(functions["live_artifact_reads"])
              if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)}
    assert called.isdisjoint(others)

    for vocabulary in (set(MODULE_CONSTRUCTORS + SOURCE_EXECUTORS),
                       set(CONTENT_SUBCOMMANDS),
                       set(REVISION_KEYWORDS + PATH_WRITES + LOADER_INTERFACE)):
        assert vocabulary.isdisjoint(LIVE_ARTIFACT_SEGMENTS), vocabulary

    assert ("a test reads a live harness artifact only when that artifact is"
            in source)


def test_the_declared_list_is_a_list_and_not_a_derivation():
    """It is written out, and it must be: a list derived from the scan would
    equal it by construction and the equality above would assert nothing.

    Read off this module's own source rather than asserted about the value, so
    a later edit that replaces the mapping with a comprehension goes red here.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assignment = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and isinstance(getattr(node, "target", None) or node.targets[0], ast.Name)
        and (getattr(node, "target", None) or node.targets[0]).id
        == "DECLARED_LIVE_ARTIFACT_READERS")
    assert isinstance(assignment.value, ast.Dict)
    assert all(isinstance(key, ast.Constant) for key in assignment.value.keys)
    assert len(assignment.value.keys) == len(DECLARED_LIVE_ARTIFACT_READERS)
    assert list(DECLARED_LIVE_ARTIFACT_READERS) \
        == sorted(DECLARED_LIVE_ARTIFACT_READERS)
    for name in DECLARED_LIVE_ARTIFACT_READERS:
        assert (TESTS_DIR / name).is_file(), name


# --------------------------------------------------------------------------
# A seventh rule: a test resolves this repository's own history only when that
# history is what it is about
#
# Its own rule again, and it shares nothing with the six above. Those are about
# a comparison that cannot fail, an instrument built out of history, where a
# file's historical text is read, what a control may mutate, what a module may
# be called, and which of the files this repository *ships* an assertion may
# reach for. This one is about the commit graph those files sit in.
#
# The five shared helpers in `tests/conftest.py` — `story_commit_range`,
# `story_diff`, `repository_file_at`, `function_source_at` and
# `revision_carrying` — resolve commits out of the history the harness itself
# lives in, and they are the *sanctioned* route to it. The third rule above
# exists so that no module writes a second one, and nothing here reverses that.
# What they are not is a source of ordinary inputs. A module whose subject
# genuinely is this repository — that a value is defined exactly once across
# the tree, that a committed archive holds a particular patch, that a
# declaration moved in a named commit — reads the history as its subject and
# goes on reading it. A module that wants a sentence, a prior version of a
# function, or the set of paths some change touched is using the history as an
# *instrument*, and its answers then move when something is committed, renamed,
# squashed or rebased, none of which is a property of the code under test.
#
# Observed, not predicted. story-051 wrote one of these into a module created
# that day, for a single sentence, and spent that run's entire retry budget on
# it: a pinned revision rebased away by a squash merge, then a content search
# that collided with the story's own documentation quoting the very figure it
# searched for. The attempt that passes does so because no line of the prose
# happens to carry two words together, which is a property nobody is holding.
# The test immediately above it asserts the same behaviour against a sentence
# constructed in the test, with no git at all, and asserts more besides.
#
# Nothing caught it, and that is what this rule is for. The sixth rule covers
# five families of file that ship, and the commit graph is in none of them and
# structurally cannot be: that scan matches a path shape joined onto a
# repository-root name, and a history read has no path shape to match. It is a
# call, to a named helper, whose repository argument is defaulted or spelled.
# So this one is a call-site scan, and the two share neither a predicate nor a
# vocabulary.
#
# **This rule reports rather than forbids, and the declared list is why.** The
# list records every module the scan reports, and each entry is *classified*: a
# subject reader carries the reason this repository's history is what that
# module's assertions are about, and a pending entry says only that the module
# has not been decided yet. Pending is not a verdict that a read is wrong; it
# is the work list for a conversion that is a separate story. A module leaves
# that class by being converted, never by being reclassified. The list is
# asserted *equal* to what the scan reports, in both directions: a module that
# joins the set fails because it is not on the list, and a module converted off
# the set fails because the list still names it.
#
# And a ceiling, because a list that only ever grows records a practice instead
# of stopping one. `PENDING_CEILING` is the number of pending entries at this
# story's completion: a converting story lowers it, and nothing raises it. It
# is a literal integer compared against a length, and resolves nothing out of
# this repository's history itself — a rule against reading the commit graph
# that read the commit graph to enforce itself would be the third instance of
# the practice it exists to stop.
#
# What it does not cover, stated here because this is where a reader meets it:
#
#   * **git reached any other way.** A `subprocess` invocation of git spelled
#     out by hand is the first and third rules' business rather than this
#     one's, and a module's own helper wrapping one of the five is not followed
#     across the assignment that bound it. This scan reads the call the source
#     states and tracks no values, exactly as `_is_subprocess_call` does not.
#   * **a path a test has already written history into.** Reading a file the
#     test wrote, or a working-tree path, is not a history read and is not
#     reported — which is the point rather than a gap, since that is the shape
#     a converted module takes.
#   * **deliberate obfuscation.** A helper fetched with `getattr`, a resolver
#     reached through a module object bound at runtime, or a repository
#     argument assembled out of pieces is not seen.
#   * **the difference between a subject and an instrument.** The scan cannot
#     tell them apart, and this is the rule's central limit. Every report is a
#     place to ask the question, not a verdict that the read is wrong, and the
#     classification recorded beside each listed module is a human answer to it
#     rather than something the scan derived.
#
# And the same standing limit as everything mechanical here: it is not
# tamper-proof. An edit deleting this check alongside a genuinely forced repair
# is not caught, at any granularity, because deleting the check that fails you
# satisfies the revert rule's own definition of a forced edit.
# --------------------------------------------------------------------------


#: The module the shared helpers are imported from, named once. It is the
#: module the third rule above exempts, for the same reason: that is where
#: reaching this repository's history is the correct thing to do.
HISTORY_HELPER_MODULE = "conftest"

#: The five helpers that resolve a commit out of the history this harness lives
#: in, each mapped to the position its `repo` argument may be written at.
#: `story_commit_range(validation_file, repo, origin)` takes one positionally;
#: the other four take it keyword-only, and `None` records that. Reading the
#: interface rather than guessing at it is what lets a call against a
#: repository the test built for itself go unreported.
HISTORY_RESOLVERS = {
    "story_commit_range": 1,
    "story_diff": None,
    "repository_file_at": None,
    "function_source_at": None,
    "revision_carrying": None,
}


def _resolver_called(node: ast.Call, bound: dict[str, str],
                     resolvers: dict[str, int | None]) -> str | None:
    """Which of the five this call reaches, however the helper reached here.

    Three spellings, all of them the same call: imported by name
    (`story_diff(...)`), reached as an attribute of the module that holds it
    (`conftest.story_diff(...)`), and bound to a local alias by the import
    (`from conftest import story_diff as diff_of_this_story`). The alias case
    is read off `_imported_names`, which is the one place in this module that
    maps a local name back to what it imports; the attribute case matches on
    the name alone, whatever qualifies it, for the reason
    `_is_subprocess_call` gives — requiring the module to be spelled a
    particular way makes the check evadable by an import statement.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr if func.attr in resolvers else None
    if isinstance(func, ast.Name):
        if func.id in bound:
            return bound[func.id]
        return func.id if func.id in resolvers else None
    return None


def _asks_this_repositorys_history(node: ast.Call, resolver: str,
                                   resolvers: dict[str, int | None]) -> bool:
    """Whether this call asks *this* repository's history rather than another.

    Every one of the five defaults its repository to the root the harness lives
    in, so a call that states no repository states this one. A defaulted call
    is therefore reported rather than skipped: leaving it out would make the
    rule evadable by deleting an argument, which is the same reasoning the
    sixth rule applies to a defaulted workflow root.

    A stated repository is read the way the first and third rules read one,
    through `_names_the_repository_root`: a repository-root name means this
    repository, and anything else — a root under `tmp_path`, a fixture
    parameter, a local a test assembled — is somebody else's history and is not
    this rule's business.
    """
    stated = next((keyword.value for keyword in node.keywords
                   if keyword.arg == "repo"), None)
    index = resolvers[resolver]
    if stated is None and index is not None and len(node.args) > index:
        stated = node.args[index]
    return stated is None or _names_the_repository_root(stated)


def history_reads(source: str, module: str,
                  resolvers: dict[str, int | None] | None = None) -> list[Flag]:
    """Every call in one module that resolves this repository's own history.

    `resolvers` is a parameter rather than a constant read straight out of the
    module body, for the reason `story_numbered_modules` takes a directory: the
    same scan the live suite is held to can then be run over a vocabulary with
    one helper taken out of it, which is how each of the five is shown below to
    be doing work.

    No exemption is applied, here or by any caller. The module holding the
    helpers states a repository for every call it makes to them, so it is
    unreported by the rule rather than excused from it.
    """
    resolvers = HISTORY_RESOLVERS if resolvers is None else resolvers
    tree = ast.parse(source)
    bound = _imported_names(tree, HISTORY_HELPER_MODULE, resolvers)
    flags = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolver = _resolver_called(node, bound, resolvers)
        if resolver is None:
            continue
        if not _asks_this_repositorys_history(node, resolver, resolvers):
            continue
        flags.append(Flag(
            module=module, line=node.lineno,
            reason=(f"resolves this repository's own history through "
                    f"{resolver}; that is right when this repository's history "
                    f"is what the assertion is about, and wrong when the "
                    f"assertion merely wanted a sentence, an earlier version "
                    f"or a set of paths — build the history the test needs, or "
                    f"construct the value in the test"),
        ))
    return flags


def history_reading_modules() -> list[str]:
    """Every module under tests/ the history scan reports, by name.

    Discovered by globbing, never by naming, so a module that starts resolving
    this repository's history joins this set the moment it lands — which is
    what makes the equality below bite.
    """
    return sorted(
        path.name for path in all_modules()
        if history_reads(path.read_text(encoding="utf-8"), path.name)
    )


#: What a listed module's entry says when the module has not been decided yet.
#: It is a classification rather than a verdict: the read may well be wrong,
#: and it may well be right, and this story does not say which. Every pending
#: entry is an item on the conversion story's work list.
PENDING = "pending conversion"


#: Every module the scan above reports, classified. A subject reader carries
#: the reason this repository's *history* is what its assertions are about; a
#: pending entry carries `PENDING` and nothing else, because a reason there
#: would be a decision this story did not make.
#:
#: A mapping rather than a tuple, so a name cannot sit here unclassified. It is
#: asserted equal to what the scan reports in both directions, so it cannot
#: grow silently and cannot keep a name whose module was converted.
#:
#: The reasons live here rather than in the modules' docstrings: story-052
#: landed the mechanism and converted nothing, and writing a paragraph into a
#: module it was not converting would have been an edit with no assertion
#: behind it. story-053 then converted every pending entry, so what is left is
#: four subject readers and their reasons — and `PENDING` stays, because the
#: class it names is where the next module to take a history dependency lands.
#: An empty pending class is a state this list can be in, not a state it has
#: retired into.
DECLARED_HISTORY_READERS = {
    "test_baseline_honesty.py":
        "its regression set is committed evidence rather than a constructed "
        "fixture: the five known vacuous assertions are recovered from this "
        "repository's history at the bound of the story that carried each, and "
        "the archived pre-reset copy is read from the tree this repository "
        "commits. What this repository's own stories did is the subject",
    "test_baseline_resolution_is_single.py":
        "its subject is that the baseline resolution is written exactly once "
        "across this repository's tree, and that a named story's commit really "
        "did carry the marker into the coordinator — a claim about where a "
        "declaration lives here and when it moved, which no constructed "
        "repository can answer",
    "test_git_history_loading_retired.py":
        "its subject is that a practice this repository retired is gone from "
        "it and stayed gone: the marker is asserted present at the endpoint of "
        "the story that removed it and absent at that story's baseline, and "
        "the archived copy is asserted still to hold what it held. That is a "
        "claim about this repository's own history of the defect",
    "test_refactor_workflow.py":
        "its subject includes what story-070 itself changed and left alone: "
        "that no prompt which existed before it was touched, that "
        "workflows/story-workflow.json declares exactly what it declared, that "
        "no story artifact was edited, and that the two stage.get lookups read "
        "the same line before and after. Each is a claim about this "
        "repository's own change, which no constructed repository can answer; "
        "the control that the resolution still reports a violation is built "
        "under tmp_path rather than looked for here",
    "test_story_range_endpoint.py":
        "its subject is where this repository's own stories end: that the "
        "module validating story-038 had its path added by that story's "
        "escalation commit and now resolves past it to the commit that "
        "finished the story, and that no module resolving this history ends at "
        "an escalation. Which commits this repository carries is the claim, "
        "and no constructed repository can make it. Every other shape the "
        "resolver is about — escalate and never resume, revert and restore, a "
        "hotfix that modifies rather than adds — is built under tmp_path there "
        "rather than looked for here",
    "test_validation_module_naming.py":
        "its subject is this repository's own module names and the origins "
        "declared for them: that every declared origin resolves to a commit in "
        "this history, that the endpoints they resolve to are distinct, and "
        "that the rename a named story made is visible across it. The names "
        "this repository carries are the thing under test",
}


#: The number of pending entries at this story's completion. A converting story
#: lowers it, and nothing raises it: a module that begins resolving this
#: repository's history has to be converted or argued to be a subject reader,
#: and neither of those adds to this count. It is a literal integer compared
#: against a length — it resolves nothing out of this repository's history,
#: which is asserted of the code that performs the comparison rather than
#: claimed here.
#:
#: story-053 took it from twenty-six to zero: every module the scan reported as
#: pending was converted, none of them by being reclassified a subject reader.
#: Zero is a real ceiling and not a retirement — the class, the classification
#: vocabulary and this constant all survive, so the next module to take a
#: history dependency is reported by the scan, absent from the declared list,
#: and red here.
PENDING_CEILING = 0


def pending_entries() -> list[str]:
    """Every listed module the classification leaves undecided."""
    return sorted(name for name, entry in DECLARED_HISTORY_READERS.items()
                  if entry == PENDING)


def subject_entries() -> dict[str, str]:
    """Every listed module whose assertions are about this repository, and why."""
    return {name: entry for name, entry in DECLARED_HISTORY_READERS.items()
            if entry != PENDING}


def within_the_ceiling(pending: list[str]) -> bool:
    """Whether a pending list fits under the declared ceiling.

    A length against a literal integer, and nothing else. The whole point of
    the constant is that enforcing a rule against reading the commit graph must
    not read the commit graph, and that is a property of this function's body
    rather than of the sentence above it — so the body is asserted below with
    the same scans this module holds the suite to.
    """
    return len(pending) <= PENDING_CEILING


def disagreements(reported: set[str], listed: set[str]) -> tuple[list[str],
                                                                 list[str]]:
    """The two directions of the agreement, kept apart.

    A single set comparison says only that the two differ. These say which
    module joined the reported set without being declared, and which declared
    module the scan no longer reports — and they are a function rather than two
    inline expressions so each direction can be shown to fail below.
    """
    return sorted(reported - listed), sorted(listed - reported)


def test_the_modules_resolving_this_repositorys_history_are_exactly_declared():
    """The rule, run rather than inspected, and asserted in both directions.

    Set equality rather than either subset. A module that begins resolving this
    repository's history is absent from the list and fails; a module converted
    to a history it builds stops being reported and its stale entry fails.
    """
    reported = set(history_reading_modules())
    listed = set(DECLARED_HISTORY_READERS)
    joined, left = disagreements(reported, listed)

    assert not joined, (
        "these modules resolve this repository's own history and are not "
        "declared. Ask the question the scan cannot: is this repository's "
        "history what the assertion is about, or an instrument it reached for? "
        "If it is an instrument, build the history the test needs — the "
        "target_root fixture is a repository under a temporary directory — or "
        "construct the value in the test; if it is the subject, declare it "
        "here with the reason: " + ", ".join(joined))
    assert not left, (
        "these modules no longer resolve this repository's history and must be "
        "removed from the declared list: " + ", ".join(left))

    assert reported == listed
    # The companion assertion the glob needs: a scan over zero files agrees
    # with an empty list for the wrong reason.
    assert len(all_modules()) >= 15
    assert reported


def test_each_direction_of_the_agreement_fails_when_it_should():
    """The control for the equality above, one direction at a time.

    An equality that has stopped seeing either side passes exactly as happily
    as one that holds, so each direction is shown reporting: a name added to
    the reported set is a module that joined without being declared, and a name
    added to the list is a module the scan no longer reports.
    """
    reported = set(history_reading_modules())
    listed = set(DECLARED_HISTORY_READERS)
    assert disagreements(reported, listed) == ([], [])

    joined, left = disagreements(reported | {"test_probe.py"}, listed)
    assert joined == ["test_probe.py"] and left == []

    joined, left = disagreements(reported, listed | {"test_probe.py"})
    assert joined == [] and left == ["test_probe.py"]


def test_every_entry_is_classified_and_every_subject_states_its_reason():
    """No entry is unclassified, and a subject entry with no reason is a name
    nobody asked the question about."""
    assert set(pending_entries()) | set(subject_entries()) \
        == set(DECLARED_HISTORY_READERS)
    assert set(pending_entries()).isdisjoint(subject_entries())

    for name, reason in subject_entries().items():
        assert (TESTS_DIR / name).is_file(), name
        assert reason != PENDING and reason.strip(), name
        assert len(reason.split()) >= 8, (name, reason)
    for name in pending_entries():
        assert (TESTS_DIR / name).is_file(), name
        assert DECLARED_HISTORY_READERS[name] is PENDING, name

    # Every listed module is a subject reader as of story-053, which converted
    # all twenty-six pending entries. An empty pending class is the success
    # this rule was built to reach, so it is asserted as such rather than as a
    # non-emptiness that would now be false — while the subject class is still
    # required to be non-empty, because a scan reporting nothing at all would
    # satisfy the equality above for the wrong reason.
    assert subject_entries()
    assert pending_entries() == []


def test_the_read_story_051_spent_its_retry_budget_on_is_listed_as_pending():
    """The case this rule exists to have caught, named — and now converted.

    story-051 wrote a history read into a module created that day, for one
    sentence, and burned its whole retry budget on it while nothing in the
    suite had anything to say. It was listed here as pending, which said the
    read was the conversion story's work rather than a decided subject read.

    story-053 did that work, so the statement inverts into the stronger one:
    the module is not reported by the scan at all, and is therefore not on the
    list in either class. Its assertion is unchanged — the same sentence is
    still reported when a run adds it — and what it reads is a constant written
    in the test rather than a revision searched for in this repository, which
    is what a whole retry budget bought the knowledge of.
    """
    module = "test_documented_claim_support.py"
    assert module not in history_reading_modules()
    assert module not in DECLARED_HISTORY_READERS
    assert (TESTS_DIR / module).is_file()

    # The control: the scan has not stopped reporting. Applied to the same
    # module's source with one history read planted in it, it reports.
    planted = (TESTS_DIR / module).read_text(encoding="utf-8") + (
        "\n\ndef _probe():\n"
        "    return conftest.revision_carrying('docs/x.md', 'a', 'b')\n")
    assert history_reads(planted, module)


def test_the_pending_list_is_within_the_declared_ceiling():
    """The ceiling, run. It is an equality at this story's completion: the
    constant *is* today's count, so a module joining the pending class fails
    here rather than being discovered two stories later."""
    pending = pending_entries()
    assert within_the_ceiling(pending)
    assert len(pending) == PENDING_CEILING


def test_the_ceiling_rejects_a_pending_list_one_entry_longer():
    """Its control, by construction rather than by the assertion above
    continuing to pass: a list one entry longer than today's is rejected by the
    same predicate that accepts today's."""
    pending = pending_entries()
    assert within_the_ceiling(pending)
    assert not within_the_ceiling(pending + ["test_probe.py"])
    # And a converted module leaves room rather than taking it, which is the
    # direction the constant is allowed to move in.
    assert within_the_ceiling(pending[:-1])


def test_the_ceilings_own_evaluation_reads_nothing_out_of_this_history():
    """Asserted of the code that performs it, not of the story.

    A rule against resolving this repository's commit graph that resolved the
    commit graph to enforce itself would be the third instance of the practice
    it exists to stop. So the constant, the predicate and the test that runs it
    are read as source and put through this module's own scans.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    performing = "".join(
        function_source(source, name) for name in
        ("pending_entries", "within_the_ceiling",
         "test_the_pending_list_is_within_the_declared_ceiling",
         "test_the_ceiling_rejects_a_pending_list_one_entry_longer"))

    assert history_reads(performing, "probe.py") == []
    assert flagged_calls(performing, "probe.py") == []
    assert git_text_reads(performing, "probe.py") == []

    # The control: the same reading over the same code with one history read
    # planted in it reports, so the emptiness above is a property of the code
    # rather than of the scans having stopped looking at it.
    planted = performing.replace(
        "return len(pending) <= PENDING_CEILING",
        "return len(pending) <= len(story_diff([], "
        "validation_file=Path(__file__)))")
    assert len(history_reads(planted, "probe.py")) == 1

    # And the constant really is a literal integer rather than something
    # computed, which is what makes the reading above worth doing.
    declared = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "PENDING_CEILING")
    assert isinstance(declared.value, ast.Constant)
    assert isinstance(declared.value.value, int)


@pytest.mark.parametrize("planted,resolver", [
    pytest.param("R = story_commit_range(Path(__file__))\n",
                 "story_commit_range", id="the-range-imported-by-name"),
    pytest.param("D = story_diff(['orchestration/'], "
                 "validation_file=Path(__file__))\n",
                 "story_diff", id="the-diff-imported-by-name"),
    pytest.param("T = conftest.repository_file_at('x.py', bound=BASELINE, "
                 "validation_file=Path(__file__))\n",
                 "repository_file_at", id="a-files-text-as-a-module-attribute"),
    pytest.param("F = conftest.function_source_at('a.py', 'f', "
                 "revision=REVISION, repo=REPO_ROOT)\n",
                 "function_source_at", id="a-functions-source-at-a-revision"),
    pytest.param("from conftest import revision_carrying as newest\n"
                 "V = newest('docs/ARCHITECTURE.md', 'a phrase')\n",
                 "revision_carrying", id="a-content-search-under-a-local-alias"),
])
def test_the_history_scan_reports_a_planted_violation(planted, resolver):
    """Its reach demonstrated rather than asserted, on the same terms as the
    scans above, and one planting per helper: a scan that had quietly lost one
    of the five would still pass a control over the other four.

    The three spellings are spread across the five — imported by name, reached
    as an attribute of the module that holds it, and bound to a local alias —
    so no route to a helper is left undemonstrated.
    """
    flags = history_reads(planted, "probe.py")
    assert len(flags) == 1, flags
    assert resolver in flags[0].reason

    # The other half of the control: with that one helper dropped from the
    # vocabulary the same source is unreported, so each name is carrying the
    # report rather than some other name catching it.
    without = {name: index for name, index in HISTORY_RESOLVERS.items()
               if name != resolver}
    assert history_reads(planted, "probe.py", without) == []


@pytest.mark.parametrize("spelling", [
    pytest.param("S = story_diff(['x'], validation_file=Path(__file__))\n",
                 id="imported-by-name"),
    pytest.param("S = conftest.story_diff(['x'], "
                 "validation_file=Path(__file__))\n",
                 id="an-attribute-of-the-module-that-holds-it"),
    pytest.param("from conftest import story_diff as what_this_story_touched\n"
                 "S = what_this_story_touched(['x'], "
                 "validation_file=Path(__file__))\n",
                 id="bound-to-a-local-alias-by-the-import"),
])
def test_the_history_scan_reads_the_call_however_the_helper_arrived(spelling):
    """One helper, three spellings, one report each: a rule matching only the
    bare name would be evadable by an import statement, which is the failure
    `_is_subprocess_call` records having had."""
    flags = history_reads(spelling, "probe.py")
    assert len(flags) == 1, flags
    assert "story_diff" in flags[0].reason


def test_the_history_scan_leaves_a_module_that_builds_its_own_repository_alone():
    """The distinction stated as a control rather than as an absence.

    Two sources asking the same questions. The first builds a repository under
    a temporary directory, runs git inside it and resolves the helpers against
    *that*; the second reaches for the history the harness is running in. Only
    the second is reported, so "not reported" above is a property of which
    repository is named rather than of the scan having stopped looking.
    """
    against_a_fixture = (
        "import subprocess\n"
        "def probe(tmp_path):\n"
        "    root = tmp_path / 'repo'\n"
        "    subprocess.run(['git', '-C', str(root), 'init'])\n"
        "    subprocess.run(['git', '-C', str(root), 'commit', '-m', 'x'])\n"
        "    validation = root / 'tests' / 'test_thing.py'\n"
        "    span = story_commit_range(validation, root)\n"
        "    text = repository_file_at('a.py', revision=span.baseline, "
        "repo=root)\n"
        "    diff = story_diff(['a.py'], validation_file=validation, "
        "repo=root)\n"
        "    return span, text, diff\n"
    )
    assert history_reads(against_a_fixture, "probe.py") == []

    reached_for = (against_a_fixture.replace("repo=root", "repo=REPO_ROOT")
                   .replace("story_commit_range(validation, root)",
                            "story_commit_range(validation, REPO_ROOT)"))
    assert len(history_reads(reached_for, "probe.py")) == 3


@pytest.mark.parametrize("benign", [
    pytest.param("def probe(tmp_path):\n"
                 "    return (tmp_path / 'written.md').read_text()\n",
                 id="a-fixture-the-test-wrote-and-read-back"),
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(root), 'show',\n"
                 "                f'HEAD:{path}'], cwd=root)\n",
                 id="the-throwaway-repository-idiom-the-other-rules-allow"),
    pytest.param("S = story_diff(['x'], validation_file=validation, "
                 "repo=target_root)\n",
                 id="a-helper-resolved-against-a-repository-the-test-owns"),
    pytest.param("S = story_commit_range(validation, tmp_path / 'repo')\n",
                 id="the-same-repository-stated-positionally"),
    pytest.param("S = conftest.function_source(source, 'story_branch')\n",
                 id="the-text-helper-that-resolves-no-commit"),
    pytest.param("S = repository_file_at\n",
                 id="a-reference-to-a-helper-that-calls-nothing"),
])
def test_the_history_scan_leaves_these_alone(benign):
    """What it must not report: another repository's history is not this
    rule's business, a file the test wrote is not history at all, and the
    throwaway-repository idiom the first and third rules leave alone stays
    unflagged here for the same reason it does there."""
    assert history_reads(benign, "probe.py") == []


def test_this_rule_draws_nothing_from_the_six_above():
    """Seven rules, seven purposes. No scan calls another, and this one draws
    neither a predicate nor a vocabulary from the live-artifact rule — asserted
    the way that rule asserts its own independence from the five before it.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    reachable = set()
    frontier = ["history_reads"]
    while frontier:
        name = frontier.pop()
        for inner in ast.walk(functions[name]):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                    and inner.func.id not in reachable:
                reachable.add(inner.func.id)
                if inner.func.id in functions:
                    frontier.append(inner.func.id)

    others = ("flagged_calls", "undeclared_targets", "module_construction",
              "git_text_reads", "mutation_controls", "story_numbered_modules",
              "live_artifact_reads")
    assert reachable.isdisjoint(others)

    # And nothing the live-artifact rule introduced, transitively: its scan,
    # its predicates and its helpers. `_names_the_repository_root` is not on
    # that list and is deliberately shared — it predates the live-artifact rule
    # and the first and third rules recognise a repository through it too.
    live_artifact_predicates = ("_path_fragments", "_is_path_join",
                                "_callee_name", "_is_a_repository_root_name",
                                "_resolves_a_workflow_through_a_helper")
    assert reachable.isdisjoint(live_artifact_predicates)

    for vocabulary in (set(LIVE_ARTIFACT_SEGMENTS), set(WORKFLOW_RESOLVERS),
                       set(MODULE_CONSTRUCTORS + SOURCE_EXECUTORS),
                       set(CONTENT_SUBCOMMANDS),
                       set(REVISION_KEYWORDS + PATH_WRITES + LOADER_INTERFACE)):
        assert vocabulary.isdisjoint(set(HISTORY_RESOLVERS)), vocabulary

    assert ("a test resolves this repository's own history only when that"
            in source)


def test_the_declared_history_list_is_a_list_and_not_a_derivation():
    """It is written out, and it must be: a list derived from the scan would
    equal it by construction and the equality above would assert nothing.

    Read off this module's own source rather than asserted about the value, so
    a later edit that replaces the mapping with a comprehension goes red here.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assignment = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "DECLARED_HISTORY_READERS")
    assert isinstance(assignment.value, ast.Dict)
    assert all(isinstance(key, ast.Constant) for key in assignment.value.keys)
    assert len(assignment.value.keys) == len(DECLARED_HISTORY_READERS)
    assert list(DECLARED_HISTORY_READERS) == sorted(DECLARED_HISTORY_READERS)

    # Each entry is classified in the literal itself: a pending one names the
    # sentinel and a subject one states its reason as text.
    for value in assignment.value.values:
        assert isinstance(value, ast.Name | ast.Constant)
        if isinstance(value, ast.Name):
            assert value.id == "PENDING"


# --------------------------------------------------------------------------
# The guidance the rendered tester prompt carries
#
# The shipped template is the *subject* here, not an input: the acceptance
# criterion is that the guidance reaches the agent, and what reaches the agent
# is the rendering rather than the file. It is read the way the harness reads
# it — through the loader, against the root the fixture hands over — so no path
# in this module is joined onto a repository-root name and the sixth rule's
# declared list does not grow. That is one of that rule's stated limits rather
# than a route around it.
#
# Every check here is a positive assertion over a rendering, which passes just
# as happily when it has stopped reading anything, so each is run again over
# the same rendering with the guidance cut out, where it must report.
# --------------------------------------------------------------------------


#: The tester's template, named because it is the subject of these assertions.
#: No stage name is written here: the template is loaded directly, so the
#: workflow's own naming of the stage that carries it is not something this
#: section has to restate.
TESTER_TEMPLATE = "story-tester.md"

#: Where the commit-graph half of the guidance begins, and where the role layer
#: ends. The cut for the control below is made at these rather than at a column.
COMMIT_GRAPH_GUIDANCE_START = "Ask the same question of this repository's own"
COMMIT_GRAPH_GUIDANCE_END = "When you finish, write these files"

#: The phrases the guidance has to carry. Phrases rather than sentences, so
#: rewording the prose around them does not redden this while dropping one
#: does. Each is unique to the commit-graph paragraphs — the fixture guidance
#: story-047 wrote sits immediately above them and shares its wording with
#: nothing here.
THE_COMMIT_GRAPH_QUESTION = "same question of this repository's own commit graph"
THE_SANCTIONED_ROUTE = "sanctioned route"
THE_INSTRUMENT = "using the history as an instrument"
THE_MOVEMENT = "committed, renamed, squashed or rebased"
THE_INSTRUCTION_TO_BUILD = "When the history is an input, build one"
THE_CONSTRUCTED_REPOSITORY = "constructs its own repository and commits into it"

#: The idiom the guidance has to name rather than describe, as the identifiers
#: the suite actually provides.
THE_HISTORY_IDIOMS = ("target_root", "git init", "commit_setup")


def flattened(text: str) -> str:
    """One rendering with its line wrapping taken out.

    The template is wrapped to a column, so a phrase of the guidance is broken
    across lines at a position nothing about the guidance decides. A phrase
    searched for in the raw text would then be absent for a reason that has
    nothing to do with whether the guidance is there.
    """
    return " ".join(text.split())


@pytest.fixture
def rendered_tester_prompt(harness_root) -> str:
    """The tester's prompt as the harness renders it, not as the file holds it."""
    template = context_assembler.load_template(harness_root, TESTER_TEMPLATE)
    rendered = context_assembler.render(template, {})
    # The rendering really is a rendering: the template carries placeholders
    # here and the result carries none, so a phrase found below was found in
    # what a stage is handed rather than in the file it came from.
    assert "{{" in template
    assert "{{" not in rendered
    return rendered


def without_the_commit_graph_guidance(rendered: str) -> str:
    """The same rendering with the commit-graph paragraphs cut out of it."""
    return (rendered[:rendered.index(COMMIT_GRAPH_GUIDANCE_START)]
            + rendered[rendered.index(COMMIT_GRAPH_GUIDANCE_END):])


def test_the_rendered_tester_prompt_asks_the_question_of_the_commit_graph(
    rendered_tester_prompt,
):
    """The question story-047 put to the shipped artifacts, put to the history
    the harness itself lives in — and the five helpers named, because guidance
    that names four leaves the fifth reading as though it were exempt."""
    prose = flattened(rendered_tester_prompt)

    assert THE_COMMIT_GRAPH_QUESTION in prose
    for resolver in HISTORY_RESOLVERS:
        assert resolver in prose, resolver
    assert THE_SANCTIONED_ROUTE in prose
    assert THE_INSTRUMENT in prose
    assert THE_MOVEMENT in prose


def test_the_rendered_tester_prompt_says_a_test_that_needs_a_history_builds_one(
    rendered_tester_prompt,
):
    """The instruction, and the existing idiom named rather than a principle
    described: a tester is pointed at the fixture that already builds a
    repository under a temporary directory, and at the helper that commits what
    a test adds afterwards."""
    prose = flattened(rendered_tester_prompt)

    assert THE_INSTRUCTION_TO_BUILD in prose
    assert THE_CONSTRUCTED_REPOSITORY in prose
    for idiom in THE_HISTORY_IDIOMS:
        assert idiom in prose, idiom


def test_the_control_removes_the_commit_graph_guidance_and_nothing_else(
    rendered_tester_prompt,
):
    """The control the cases below lean on, asserted rather than assumed:
    shorter, and still carrying the rest of the prompt at both ends — including
    the fixture guidance story-047 wrote, which sits immediately above the cut
    and must survive it."""
    stripped = flattened(without_the_commit_graph_guidance(rendered_tester_prompt))
    whole = flattened(rendered_tester_prompt)

    assert len(stripped) < len(whole)
    assert stripped.startswith(whole[:200])
    assert stripped.endswith(whole[-200:])
    assert "is the shipped artifact the subject of this assertion" in stripped


@pytest.mark.parametrize("phrase", [
    THE_COMMIT_GRAPH_QUESTION, THE_SANCTIONED_ROUTE, THE_INSTRUMENT,
    THE_MOVEMENT, THE_INSTRUCTION_TO_BUILD, THE_CONSTRUCTED_REPOSITORY,
    *THE_HISTORY_IDIOMS, *HISTORY_RESOLVERS,
])
def test_every_phrase_this_section_looks_for_is_absent_once_it_is_removed(
    rendered_tester_prompt, phrase,
):
    """The control for every positive check above, stated once.

    Each phrase is found in the rendering and not found in the same rendering
    with the commit-graph guidance cut out. Without this, a check that had
    drifted to a phrase the prompt happens to carry elsewhere would pass while
    asserting nothing about the guidance.
    """
    assert phrase in flattened(rendered_tester_prompt)
    assert phrase not in flattened(
        without_the_commit_graph_guidance(rendered_tester_prompt))
