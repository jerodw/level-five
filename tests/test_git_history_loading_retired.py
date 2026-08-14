"""Independent validation for story-029: loading code out of git history is
retired, and the rule is enforced mechanically.

The story's subject is a *capability that must be gone* and *two scans that
must report it coming back*. Both halves are absences, so nothing here is
asserted by looking once:

  * "no module under tests/ builds a module at runtime" and "no module under
    tests/ other than the shared one asks git for a file's text" are scanned
    here by an implementation written for this file rather than by calling the
    implementer's — two independent readings of the same rule — and each
    absence sits beside the four modules recovered at this story's baseline,
    which the same reading reports;
  * "the scans have reach" is shown against violations planted here, written
    to be different sources from the ones planted beside the scans themselves,
    including a renamed copy of the shared loader — renaming is how the scan
    being replaced was evaded;
  * "the retired helper names are gone" is an AST reading of every definition,
    call, import and attribute under tests/, beside a planted source in which
    the same reading finds all four;
  * "each restated comparison fails when its subject is violated" is not
    argued: for each one, this file copies the repository, mutates the
    coordinator so the named subject is violated, runs that one test in the
    copy, and requires it to go red — and runs the same test in the unmutated
    copy first, so a copy that could not run anything would fail as itself
    rather than pass as eleven demonstrations;
  * "the text-only assertions kept their subject, strictness and control" is
    read as the comparison operators and the coordinator functions each names,
    at this story's baseline and today, and required to be equal.

Nothing here invokes a model. The coordinator runs that do happen are driven
by the modules under test, in a copy of this repository, through their own
fake runners.

This module is itself governed by the two scans it validates: it constructs no
module and asks git for no file's text, reaching the repository's history only
through the shared reader in `tests/conftest.py`. The one construct it uses
that the construction scan does not cover is a `subprocess` pytest run, which
is a limit that scan states about itself.
"""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (BASELINE, ENDPOINT, function_source,
                      repository_file_at, story_commit_range)

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
THIS_FILE = Path(__file__)

#: The one module the story exempts from both rules, stated here rather than
#: read off the implementation, so a widened exemption fails this file too.
SHARED_MODULE = "conftest.py"

#: The four modules that carried the retired practice at this story's
#: baseline. Named independently of the implementer's list.
CARRIED_THE_PRACTICE = (
    "tests/test_story_020_validation.py",
    "tests/test_story_021_validation.py",
    "tests/test_story_024_validation.py",
    "tests/test_story_027_validation.py",
)

#: Where each file named at a past revision in this module lives *now*.
#: story-038 renamed every per-story validation module for the behaviour it
#: validates. A path is asked for at a revision under the name it has there,
#: so every constant naming a revision-era path keeps its historical spelling
#: and every read of the working tree — or node id run against it — goes
#: through this.
TODAY = {
    "tests/test_story_011_validation.py": "tests/test_execution_history.py",
    "tests/test_story_012_validation.py": "tests/test_retry_history.py",
    "tests/test_story_014_validation.py": "tests/test_clean_clone_check.py",
    "tests/test_story_016_validation.py":
        "tests/test_contract_assertions_bite.py",
    "tests/test_story_020_validation.py": "tests/test_escalation_resume.py",
    "tests/test_story_021_validation.py": "tests/test_foreign_work_refusal.py",
    "tests/test_story_024_validation.py": "tests/test_escalation_summary.py",
    "tests/test_story_025_validation.py": "tests/test_plan_time_validation.py",
    "tests/test_story_026_validation.py":
        "tests/test_baseline_resolution_is_single.py",
    "tests/test_story_027_validation.py": "tests/test_rerun_refusal.py",
}

#: The names the story says are deleted.
RETIRED_NAMES = (
    "runnable_against_current_modules",
    "pre_story_coordinator_source",
    "coordinator_source_at",
    "pre_story_revision",
)


def baseline_text(rel: str) -> str:
    """One repository file as it stood at this story's baseline.

    Through the shared reader, bounded at this file's own story range: while
    story-029 is in flight that is HEAD, and once it commits it is the parent
    of its run commit. Both are this story's baseline, and neither is a pinned
    sha a rebase could move.
    """
    return repository_file_at(rel, validation_file=THIS_FILE, bound=BASELINE,
                              repo=REPO_ROOT)


def module_sources() -> dict[str, str]:
    """Every module this suite ships, keyed by file name. Globbed, not named."""
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(TESTS_DIR.glob("*.py"))}


# --------------------------------------------------------------------------
# Rule one, read independently: nothing under tests/ builds a module
# --------------------------------------------------------------------------


#: The closed set of ways source becomes a live module in this process. Named
#: from the language rather than from any helper, which is the whole point of
#: the rule: a module that renames its loader still has to call one of these.
BUILDERS = (
    "spec_from_file_location", "spec_from_loader", "module_from_spec",
    "exec_module", "SourceFileLoader", "SourcelessFileLoader",
    "ExtensionFileLoader", "ModuleType", "import_module", "run_path",
    "run_module",
)

#: Builtins that run source here. Bare calls only — `re.compile` and
#: `ast.literal_eval` are qualified and are not these.
RUNNERS = ("exec", "eval", "compile", "__import__")


def builds_a_module(source: str) -> list[tuple[int, str]]:
    """Every construction site in one module's source: (line, construct).

    Written for this file rather than imported, so that "the suite is clean"
    is established by two implementations that were not derived from each
    other.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in BUILDERS:
            found.append((node.lineno, func.attr))
        elif isinstance(func, ast.Name) and func.id in BUILDERS + RUNNERS:
            found.append((node.lineno, func.id))
    return found


def test_no_module_under_tests_builds_a_module_except_the_shared_one():
    """The rule, read by this file's own scan.

    Its control is the four modules recovered at this story's baseline, below:
    the same reading reports every one of them, so the emptiness here is the
    practice being gone rather than the reading having stopped seeing.
    """
    offenders = {name: builds_a_module(source)
                 for name, source in module_sources().items()
                 if name != SHARED_MODULE and builds_a_module(source)}
    assert offenders == {}, offenders


def test_the_shared_module_is_the_one_that_builds_them():
    """The exemption is a name, and it is not an empty one: the shared module
    really does hold the construction the rest gave up."""
    shared = (TESTS_DIR / SHARED_MODULE).read_text(encoding="utf-8")
    assert builds_a_module(shared), "the exempt module builds nothing"
    assert "def load_mutant" in shared
    assert "def load_script" in shared


@pytest.mark.parametrize("rel", CARRIED_THE_PRACTICE)
def test_this_files_scan_reports_each_module_at_this_storys_baseline(rel):
    """The direct evidence that the rule would have caught what it is for,
    read by this file's own scan rather than by the implementation's."""
    found = builds_a_module(baseline_text(rel))
    assert found, rel


def test_this_files_scan_reports_a_loader_however_its_helpers_are_named():
    """Renaming is how the superseded name-matching scan was evaded, so it is
    shown rather than argued: two loaders that do the same thing and share not
    one identifier, both reported with the same constructs.

    The shared loader in `tests/conftest.py` is the third: it is reported too,
    which is why it is the module the rule exempts by name.
    """
    one = ("import importlib.machinery, importlib.util\n"
           "def load_variant(label, where):\n"
           "    ldr = importlib.machinery.SourceFileLoader(label, str(where))\n"
           "    sp = importlib.util.spec_from_loader(ldr.name, ldr)\n"
           "    mod = importlib.util.module_from_spec(sp)\n"
           "    ldr.exec_module(mod)\n"
           "    return mod\n")
    renamed = ("import importlib.machinery as m, importlib.util as u\n"
               "def _summon_the_old_one(tag, place):\n"
               "    fetcher = m.SourceFileLoader(tag, str(place))\n"
               "    plan = u.spec_from_loader(fetcher.name, fetcher)\n"
               "    thing = u.module_from_spec(plan)\n"
               "    fetcher.exec_module(thing)\n"
               "    return thing\n")

    assert set(renamed.split()).isdisjoint(
        {"load_variant", "ldr", "sp", "mod", "where", "label"})
    assert [construct for _, construct in builds_a_module(one)] \
        == [construct for _, construct in builds_a_module(renamed)]
    assert len(builds_a_module(renamed)) == 4
    assert builds_a_module(function_source(
        (TESTS_DIR / SHARED_MODULE).read_text(encoding="utf-8"), "_load_module"))


@pytest.mark.parametrize("planted", [
    pytest.param("import importlib.util as u\n"
                 "def probe(p):\n"
                 "    s = u.spec_from_file_location('x', p)\n"
                 "    m = u.module_from_spec(s)\n"
                 "    s.loader.exec_module(m)\n", id="aliased-importlib"),
    pytest.param("from importlib.machinery import SourceFileLoader as L\n"
                 "def probe(p):\n"
                 "    L('x', str(p)).exec_module(object())\n",
                 id="loader-imported-under-another-name"),
    pytest.param("def probe(text):\n"
                 "    ns = {}\n"
                 "    exec(text, ns)\n"
                 "    return ns['run_story']\n", id="exec-recovered-source"),
    pytest.param("from importlib import import_module\n"
                 "def probe():\n"
                 "    return import_module('story_coordinator')\n",
                 id="import-module"),
])
def test_this_files_scan_reports_a_planted_construction(planted):
    """Its reach, demonstrated on sources written here — deliberately not the
    ones planted beside the scan under test."""
    assert builds_a_module(planted), planted


@pytest.mark.parametrize("benign", [
    pytest.param("import re\nP = re.compile('x')\n", id="re-compile"),
    pytest.param("import ast\nV = ast.literal_eval('[1]')\n", id="literal-eval"),
    pytest.param("import subprocess, sys\n"
                 "subprocess.run([sys.executable, '-m', 'pytest'], cwd='x')\n",
                 id="a-subprocess-run-which-is-a-stated-limit"),
])
def test_this_files_scan_leaves_these_alone(benign):
    """What it must not report, so "clean" means something narrower than
    "nothing was looked at"."""
    assert builds_a_module(benign) == []


# --------------------------------------------------------------------------
# Rule two, read independently: only the shared module asks git for text
# --------------------------------------------------------------------------


CONTENT_SUBCOMMANDS = ("show", "cat-file")


def _literals(node: ast.AST) -> str:
    """An argument's literal fragments joined; computed pieces contribute
    nothing, so `f"{rev}:{path}"` reads as `":"`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values
                       if isinstance(part, ast.Constant)
                       and isinstance(part.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literals(node.left) + _literals(node.right)
    return ""


def _mentions_this_repository(node: ast.Call) -> bool:
    """Whether a call names one of the names that stand for this repository,
    in an argument or in `cwd=`. A throwaway repository a test built for
    itself is another history and is not this rule's subject."""
    roots = ("REPO_ROOT", "HARNESS_ROOT")
    candidates = list(node.args) + [kw.value for kw in node.keywords
                                    if kw.arg in ("cwd", "repo")]
    return any(isinstance(inner, ast.Name) and inner.id in roots
               for candidate in candidates for inner in ast.walk(candidate))


def reads_a_files_text(source: str) -> list[int]:
    """Every call in one module that asks git for a repository file's text.

    Both shapes: the spawned list, `subprocess.run(["git", ...])`, and the
    wrapped one, `git(REPO_ROOT, "show", f"{rev}:{path}")`, which is what the
    four offending modules actually wrote. Recognized by shape — a content
    subcommand beside a `<revision>:<path>` argument — so no helper name
    appears here and renaming one changes nothing.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if node.args and isinstance(node.args[0], ast.List) \
                and node.args[0].elts \
                and _literals(node.args[0].elts[0]) == "git":
            words = list(node.args[0].elts)
            targeted = _mentions_this_repository(node) or not any(
                _literals(word) == "-C" for word in words)
        elif _mentions_this_repository(node):
            words = list(node.args)
            targeted = True
        else:
            continue
        if not targeted:
            continue
        if not any(_literals(word) in CONTENT_SUBCOMMANDS for word in words):
            continue
        spec = [_literals(word) for word in words]
        if any(":" in text and not text.startswith("--") for text in spec) \
                or any(text in ("blob", "-p") for text in spec):
            found.append(node.lineno)
    return found


def test_no_module_under_tests_reads_a_files_text_out_of_git_except_the_shared_one():
    """The rule, read by this file's own scan. Its control is the same four
    baseline modules below, every one of which this reading reports."""
    offenders = {name: reads_a_files_text(source)
                 for name, source in module_sources().items()
                 if name != SHARED_MODULE and reads_a_files_text(source)}
    assert offenders == {}, offenders


def test_the_shared_module_holds_the_one_reader():
    """The exemption again: one reader, in the module named, bounded at a
    story's own commit range rather than at HEAD."""
    shared = (TESTS_DIR / SHARED_MODULE).read_text(encoding="utf-8")
    assert "def repository_file_at" in shared
    assert '"show", f"{resolved}:{relative}"' in shared
    reader = function_source(shared, "repository_file_at")
    assert "story_commit_range" in function_source(shared, "_resolved_revision")
    assert "bound" in reader


@pytest.mark.parametrize("rel", CARRIED_THE_PRACTICE)
def test_this_files_reader_scan_reports_each_module_at_this_storys_baseline(rel):
    assert reads_a_files_text(baseline_text(rel)), rel


@pytest.mark.parametrize("planted", [
    pytest.param("import subprocess\n"
                 "def probe(rev, rel):\n"
                 "    return subprocess.run(['git', '-C', str(REPO_ROOT),\n"
                 "        'show', f'{rev}:{rel}'], capture_output=True).stdout\n",
                 id="spawned-show"),
    pytest.param("def probe(rev, rel):\n"
                 "    return git(HARNESS_ROOT, 'show', rev + ':' + rel)\n",
                 id="wrapped-show-with-a-concatenated-spec"),
    pytest.param("import subprocess\n"
                 "def probe(sha):\n"
                 "    subprocess.check_output(['git', 'cat-file', 'blob', sha],\n"
                 "                            cwd=REPO_ROOT)\n",
                 id="cat-file-blob"),
])
def test_this_files_reader_scan_reports_a_planted_read(planted):
    assert reads_a_files_text(planted), planted


def test_this_files_reader_scan_reports_a_renamed_reader():
    """The same renaming demonstration as for rule one: two readers with no
    identifier in common, both reported."""
    one = ("def old_text(rel):\n"
           "    revision = pre_story()\n"
           "    return git(REPO_ROOT, 'show', f'{revision}:{rel}')\n")
    renamed = (one.replace("old_text", "way_back_when")
               .replace("revision", "moment").replace("pre_story", "before"))
    assert "old_text" not in renamed
    assert reads_a_files_text(one) and reads_a_files_text(renamed)


@pytest.mark.parametrize("benign", [
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(REPO_ROOT), 'log',\n"
                 "                '--format=%H', '--', rel])\n", id="a-log"),
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(root), 'show',\n"
                 "                f'HEAD:{rel}'], cwd=root)\n",
                 id="a-throwaway-repository"),
    pytest.param("import subprocess\n"
                 "subprocess.run(['git', '-C', str(REPO_ROOT), 'show',\n"
                 "                '--name-only', '--format=', rev])\n",
                 id="a-commits-file-list"),
])
def test_this_files_reader_scan_leaves_these_alone(benign):
    assert reads_a_files_text(benign) == []


# --------------------------------------------------------------------------
# The scans as the story requires them to be stated
# --------------------------------------------------------------------------


HONESTY = TESTS_DIR / "test_baseline_honesty.py"


def honesty_source() -> str:
    return HONESTY.read_text(encoding="utf-8")


def scan_function(name: str) -> ast.FunctionDef:
    for node in ast.parse(honesty_source()).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in {HONESTY.name}")


def test_the_two_scans_exist_and_are_run_rather_than_inspected():
    """The rule has to be executed by the suite, not described in it: each
    scan is a function, and a test calls it over every module it covers."""
    source = honesty_source()
    for scan, runner in (
        ("module_construction",
         "test_no_module_under_tests_builds_a_module_at_runtime"),
        ("git_text_reads",
         "test_no_module_under_tests_reads_a_repository_file_out_of_git"),
    ):
        scan_function(scan)
        body = function_source(source, runner)
        assert f"{scan}(" in body, runner
        assert "glob" not in body and "modules()" in body, runner


@pytest.mark.parametrize("scan,constant", [
    ("module_construction", "CONSTRUCTION_EXEMPT_MODULES"),
    ("git_text_reads", "READER_EXEMPT_MODULES"),
])
def test_each_scan_matches_on_constructs_and_names_no_helper(scan, constant):
    """The property the superseded scan lacked, checked on the scan's own
    source: neither the function nor the vocabulary it matches on mentions any
    helper name — not one of the retired names, and not one of the private
    names the four modules used."""
    source = honesty_source()
    body = function_source(source, scan)
    private = RETIRED_NAMES + ("pre_story", "at_story_endpoint", "_blob",
                               "load_variant", "_loaded_coordinator", "_lift")
    for name in private:
        assert name not in body, (scan, name)
    assert constant in source


def test_the_construction_scan_reports_violations_written_here():
    """Its reach, against sources this file wrote, including the renamed
    loader: the implementation under test is held to what this file's own scan
    reports, so a scan narrowed to its own planted examples fails here."""
    from test_baseline_honesty import module_construction

    for planted in (
        "import importlib.util as u\n"
        "def probe(p):\n"
        "    return u.module_from_spec(u.spec_from_file_location('x', p))\n",
        "def probe(text):\n    ns = {}\n    exec(text, ns)\n",
        "from importlib import import_module\n"
        "def probe():\n    return import_module('story_coordinator')\n",
    ):
        assert module_construction(planted, "probe.py"), planted


def test_the_reader_scan_reports_violations_written_here():
    from test_baseline_honesty import git_text_reads

    for planted in (
        "def probe(rel):\n"
        "    return git(REPO_ROOT, 'show', f'{pre_story()}:{rel}')\n",
        "import subprocess\n"
        "def probe(rev, rel):\n"
        "    subprocess.run(['git', '-C', str(HARNESS_ROOT), 'show',\n"
        "                    rev + ':' + rel])\n",
    ):
        assert git_text_reads(planted, "probe.py"), planted


@pytest.mark.parametrize("rel", CARRIED_THE_PRACTICE)
def test_both_shipped_scans_report_each_module_at_this_storys_baseline(rel):
    """The verification requirement stated per module: this is the only direct
    evidence either scan would have caught the practice it retires."""
    from test_baseline_honesty import git_text_reads, module_construction

    before = baseline_text(rel)
    assert module_construction(before, Path(rel).name), rel
    assert git_text_reads(before, Path(rel).name), rel
    assert before != (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8"), rel


@pytest.mark.parametrize("scan", ["module_construction", "git_text_reads"])
def test_each_scan_states_the_limits_it_does_not_cover(scan):
    """Each rule states its own narrowness, in the paragraph above its own
    definition, and the three limits the story names are found by their
    subject rather than by a shared phrase.

    The control is the same reading of a copy with the limits paragraph
    removed, which finds none of them — otherwise a search that had stopped
    matching would look the same as a paragraph that had been deleted.
    """
    source = honesty_source()
    preamble = source[:source.index(f"def {scan}(")]
    stated = preamble[preamble.rindex("What it does not cover"):]

    assert "subprocess" in stated, "source run in a subprocess"
    assert "path" in stated and ("written" in stated or "worktree" in stated), \
        "historical text written to a file and passed in as a path"
    assert "obfuscation" in stated, "deliberate obfuscation"
    assert "tamper-proof" in preamble

    # The control: the same reading of the same paragraph with the obfuscation
    # limit taken out reports it missing, so the three findings above are the
    # paragraph saying these things rather than the reading matching anything.
    without = "\n".join(line for line in stated.splitlines()
                        if "obfuscation" not in line)
    assert "obfuscation" not in without
    assert "subprocess" in without


def test_the_two_rules_are_stated_separately_and_neither_derives_the_other():
    """Two rules, two purposes, two exemption lists, and neither described as
    getting its enforcement from the other.

    Read off the source: each scan's own body calls the other nowhere, the two
    vocabularies are disjoint, and each rule is introduced in its own words.
    """
    source = honesty_source()
    for name, other in (("module_construction", "git_text_reads"),
                        ("git_text_reads", "module_construction")):
        called = {node.func.id for node in ast.walk(scan_function(name))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert other not in called, name

    from test_baseline_honesty import (CONSTRUCTION_EXEMPT_MODULES,
                                       CONTENT_SUBCOMMANDS as SHIPPED_CONTENT,
                                       MODULE_CONSTRUCTORS, READER_EXEMPT_MODULES,
                                       SOURCE_EXECUTORS)
    assert set(MODULE_CONSTRUCTORS + SOURCE_EXECUTORS).isdisjoint(SHIPPED_CONTENT)
    assert CONSTRUCTION_EXEMPT_MODULES == (SHARED_MODULE,)
    assert READER_EXEMPT_MODULES == (SHARED_MODULE,)

    for phrase in ("no module under tests/ builds a module at runtime",
                   "only the shared reader asks git for a repository file's text"):
        assert phrase in source, phrase
    for claim in ("enforced by the rule above", "derives its enforcement",
                  "because the other rule"):
        assert claim not in source, claim


def test_the_superseded_name_matching_scan_is_gone():
    """The scan that could be evaded by renaming, and its two name lists,
    deleted from story-016's module — and present at this story's baseline, so
    the absence is a deletion rather than a search for something that was
    never there."""
    rel = "tests/test_story_016_validation.py"
    before = baseline_text(rel)
    after = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
    for name in ("def test_no_module_under_tests_loads_a_coordinator_out_of_git_history",
                 "HISTORY_SOURCE_READERS", "MODULE_LOADERS"):
        assert name in before, name
        assert name not in after, name


def test_no_deleted_recovery_helper_name_appears_in_the_scans_module():
    """The acceptance criterion stated as it is worded: the names of the
    deleted recovery helpers do not appear anywhere in the module that holds
    the new scans.

    Its control is the modules that did carry them at this story's baseline,
    read the same way, which the same search reports. Otherwise "the name is
    not here" would look the same as a search that had stopped matching.

    The control names the three helpers that existed at this story's baseline
    and the modules that held them. `runnable_against_current_modules` is
    deliberately not among them: it was added by story-028, which is not an
    ancestor of this branch, so it is a name that never existed here rather
    than one this story removed. It stays in RETIRED_NAMES for the absence
    half above, which holds either way.
    """
    source = honesty_source()
    for name in RETIRED_NAMES:
        assert name not in source, name

    carried_at_baseline = ("pre_story_coordinator_source",
                           "coordinator_source_at",
                           "pre_story_revision")
    for rel in ("tests/test_story_011_validation.py",
                "tests/test_story_016_validation.py",
                "tests/test_story_026_validation.py"):
        assert any(name in baseline_text(rel) for name in carried_at_baseline), rel


# --------------------------------------------------------------------------
# The capability itself: no definition, call, import or attribute survives
# --------------------------------------------------------------------------


def live_references(source: str, names: tuple[str, ...]) -> set[str]:
    """Every *live* use of one of `names` in a module: a definition, a call, a
    plain name reference, an attribute or an import.

    A string literal is deliberately not one. That distinction is the whole
    question below, so it is drawn by the parser rather than by a grep.
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name in names:
            found.add(node.name)
        elif isinstance(node, ast.Name) and node.id in names:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name in names or alias.asname in names:
                    found.add(alias.asname or alias.name)
    return found


def test_no_module_under_tests_still_defines_calls_or_imports_the_retired_helpers():
    """The capability is gone as code: not one of the four names is defined,
    called, referenced or imported anywhere under tests/.

    The control is the planted module below and the baseline modules beside
    it: the same reading finds every name when it is there.
    """
    live = {name: sorted(live_references(source, RETIRED_NAMES))
            for name, source in module_sources().items()
            if live_references(source, RETIRED_NAMES)}
    assert live == {}, live


def test_the_reading_above_finds_them_when_they_are_there():
    """Its control, in both forms the four modules wrote them: a definition
    with its call, and an import."""
    planted = ("from conftest import runnable_against_current_modules\n"
               "def coordinator_source_at(rev):\n"
               "    return rev\n"
               "def probe():\n"
               "    return runnable_against_current_modules(\n"
               "        coordinator_source_at(pre_story_revision()))\n")
    assert live_references(planted, RETIRED_NAMES) == {
        "runnable_against_current_modules", "coordinator_source_at",
        "pre_story_revision"}
    assert live_references(baseline_text(
        "tests/test_story_011_validation.py"), RETIRED_NAMES)


def test_where_the_retired_names_still_occur_and_that_it_is_only_as_text():
    """The one place the story's wording and its own tasks pull apart, stated
    here rather than left for a reader to discover.

    The acceptance criterion asks that a grep for these four names over tests/
    return nothing. It does not: three of them survive as **string literals**
    inside two assertions — story-016's intact-list and story-026's survivor
    list — which the same story's task list says to *repoint, keeping subject
    and strictness*, not to delete. Both are bounded at their own story's
    finished commit range, so they are statements about what those stories did
    and not about what tests/ holds today.

    So the honest statement is made in two halves: no live reference survives
    anywhere (the test above), and every remaining textual occurrence is a
    string literal or a comment, in exactly these two modules. A fourth module
    growing one of these literals fails here and has to be looked at.
    """
    occurrences = {name: {rel for rel, source in module_sources().items()
                          if name in source}
                   for name in RETIRED_NAMES}
    assert occurrences["runnable_against_current_modules"] == {THIS_FILE.name}
    for name in ("coordinator_source_at", "pre_story_revision",
                 "pre_story_coordinator_source"):
        assert occurrences[name] == {
            THIS_FILE.name,
            Path(TODAY["tests/test_story_016_validation.py"]).name,
            Path(TODAY["tests/test_story_026_validation.py"]).name}, name

    for rel in ("tests/test_story_016_validation.py",
                "tests/test_story_026_validation.py"):
        source = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
        assert live_references(source, RETIRED_NAMES) == set(), rel


def test_the_shared_module_carries_the_replacement_and_no_shim():
    """The shared module holds the reader that replaced the practice, and none
    of the retired names.

    This asserted a *removal* while story-028 was this branch's baseline:
    `runnable_against_current_modules` was story-028's shim over the loaders,
    and the claim was that this story deleted rather than adapted it. story-028
    is no longer an ancestor, so that shim never existed here and no removal
    can be shown. What survives the change of baseline is the half that was
    always this story's own: `repository_file_at` is absent at the baseline and
    present now, and no retired name is in the shared module at either end.
    """
    before = baseline_text("tests/conftest.py")
    after = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    for name in RETIRED_NAMES:
        assert name not in after, name
    assert "def repository_file_at" not in before      # what replaced the practice
    assert "def repository_file_at" in after


# --------------------------------------------------------------------------
# The shared helpers the eleven private copies stand in for
# --------------------------------------------------------------------------


def test_the_shared_mutation_loader_takes_a_path_and_its_replacements(tmp_path):
    """Not arbitrary source text, which is the shape that keeps recovered
    source from being a value it naturally accepts.

    Checked as the signature and then as behaviour: a working-tree path is
    mutated and loaded, and a blob of source text handed in where the path
    goes is rejected by the filesystem rather than executed.
    """
    from conftest import load_mutant

    signature = function_source(
        (TESTS_DIR / "conftest.py").read_text(encoding="utf-8"), "load_mutant")
    assert "source_path" in signature and "replacements" in signature
    for rejected in ("source:", "text:", "def load_mutant(source,"):
        assert rejected not in signature, rejected

    target = tmp_path / "subject.py"
    target.write_text("VALUE = 'original'\n", encoding="utf-8")
    mutant = load_mutant(target, [("'original'", "'mutated'")],
                         name="story_029_mutant", tmp_path=tmp_path)
    assert mutant.VALUE == "mutated"

    # An anchor that is not there fails as itself rather than loading a module
    # that quietly changed nothing.
    with pytest.raises(AssertionError):
        load_mutant(target, [("absent", "x")], name="story_029_absent",
                    tmp_path=tmp_path)

    # Source text where the path goes is not a value it takes.
    with pytest.raises((OSError, ValueError)):
        load_mutant(Path("VALUE = 'recovered'\n"), [], name="story_029_text",
                    tmp_path=tmp_path)


def test_the_shared_script_loader_takes_a_script_name():
    """The extensionless entry points under scripts/ have no suffix and cannot
    be imported, so one named loader is the only way to reach them — one,
    rather than one per module, which is what the story folded."""
    from conftest import load_script

    module = load_script("l5-status")
    assert hasattr(module, "main")
    with pytest.raises(FileNotFoundError):
        load_script("no-such-script")


def test_no_module_carries_a_private_copy_of_either_loader():
    """The folding, stated over the suite: the two loader idioms exist in the
    shared module and nowhere else. Falls out of the construction scan above,
    and is stated separately because it is a separate claim — the callers were
    repointed rather than merely made to stop constructing."""
    for name, source in module_sources().items():
        if name == SHARED_MODULE:
            continue
        defined = {node.name for node in ast.parse(source).body
                   if isinstance(node, ast.FunctionDef)}
        assert not defined & {"load_mutant", "load_script"}, name

    # The control: each fold's callers did define their own before this story,
    # which is `test_each_folded_caller_carried_its_own_copy_at_the_baseline`
    # below, and the shared module defines both now.
    shared = (TESTS_DIR / SHARED_MODULE).read_text(encoding="utf-8")
    assert "def load_mutant" in shared and "def load_script" in shared


@pytest.mark.parametrize("rel", [
    "tests/test_story_012_validation.py",
    "tests/test_story_014_validation.py",
    "tests/test_story_020_validation.py",
    "tests/test_story_025_validation.py",
])
def test_each_folded_caller_carried_its_own_copy_at_the_baseline(rel):
    """The control for the fold: each of these modules did define its own
    loader before this story, so "one shared helper" is a consolidation rather
    than a name that was always alone."""
    before = baseline_text(rel)
    assert builds_a_module(before), rel


# --------------------------------------------------------------------------
# The restated comparisons, each shown red when its subject is violated
# --------------------------------------------------------------------------


COORDINATOR_REL = "orchestration/story_coordinator.py"

#: One entry per restated comparison: the test that replaced it, and an edit
#: to the coordinator that violates the subject that test names. The edit is
#: the demonstration — a replacement assertion that cannot go red is a
#: description of today's output rather than a check on it.
RESTATED = [
    pytest.param(
        "tests/test_story_021_validation.py",
        "test_a_clean_fresh_run_is_what_it_was_before_the_check_existed",
        'f"{name} stage completed"', 'f"{name} stage finished"',
        id="021-clean-run-events"),
    pytest.param(
        "tests/test_story_021_validation.py",
        "test_a_story_artifact_no_longer_reaches_a_story_commit",
        "            return _refuse_dirty_tree(target_root, dirty)",
        "            pass",
        id="021-artifact-absorbed"),
    pytest.param(
        "tests/test_story_027_validation.py",
        "test_a_first_run_of_a_story_whose_branch_does_not_exist_is_unaffected",
        'COMPLETION_COMMIT_MARKER = "Implemented by the l5 harness story workflow."',
        'COMPLETION_COMMIT_MARKER = "Made by something else entirely."',
        id="027-first-run-commit-message"),
    pytest.param(
        "tests/test_story_027_validation.py",
        "test_a_completed_state_still_meets_the_already_ended_refusal",
        "already ended with status", "has already ended with status",
        id="027-already-ended-message"),
    pytest.param(
        "tests/test_story_027_validation.py",
        "test_the_defect_this_closes_is_recorded_and_the_same_move_is_refused",
        "            return _refuse_finished_branch(branch, run_dir, finished)",
        "            pass",
        id="027-rerun-onto-finished-branch"),
    pytest.param(
        "tests/test_story_024_validation.py",
        "test_the_four_pre_existing_sections_carry_the_text_they_carried",
        'f"## Status\\nEscalated"', 'f"## Status\\nStopped"',
        id="024-status-section-body"),
    pytest.param(
        "tests/test_story_024_validation.py",
        "test_escalation_reason_returns_the_string_it_returned_before",
        '"verification failed and retries are exhausted"',
        '"verification failed, and that is all we will say"',
        id="024-escalation-reason-string"),
    pytest.param(
        "tests/test_story_024_validation.py",
        "test_an_escalation_writes_no_new_file_to_the_run_directory",
        '    (run_dir / "escalation-summary.md").write_text(summary, encoding="utf-8")',
        '    (run_dir / "escalation-summary.md").write_text(summary, encoding="utf-8")\n'
        '    (run_dir / "a-stray-artifact.json").write_text("{}\\n", encoding="utf-8")',
        id="024-escalation-run-directory"),
    pytest.param(
        "tests/test_story_024_validation.py",
        "test_reason_is_still_the_section_immediately_after_status",
        '        f"## Reason\\n{reason}",',
        '        "## Interloper\\nwedged in",\n        f"## Reason\\n{reason}",',
        id="024-reason-immediately-after-status"),
    pytest.param(
        "tests/test_story_020_validation.py",
        "test_the_refusal_this_story_narrowed_is_named_at_both_ends_of_its_range",
        'if state and state.status == "completed":',
        'if state and state.status != "running":',
        id="020-narrowed-refusal"),
    pytest.param(
        "tests/test_story_020_validation.py",
        "test_a_state_file_written_before_this_story_still_loads",
        "    artifacts: list[str] = field(default_factory=list)",
        "    artifacts: list[str] = field(default_factory=list)\n"
        '    invented_later: str = ""',
        id="020-pre-story-state-shape"),
]


@pytest.fixture(scope="module")
def repo_copy(tmp_path_factory) -> Path:
    """A copy of this repository, history included, that a test may mutate.

    Copied rather than mutated in place because the subject of every case
    below is a coordinator edit, and the repository this suite is running from
    is the one being validated. The history comes along because the assertions
    under test bound themselves at their own story's commit range.
    """
    destination = tmp_path_factory.mktemp("repo") / "level-five"
    shutil.copytree(REPO_ROOT, destination,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__",
                                                  ".pytest_cache"))
    return destination


def run_one_test(repo: Path, rel: str, test: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", f"{rel}::{test}", "-q",
         "-p", "no:cacheprovider"],
        cwd=repo, capture_output=True, text=True,
    )


@pytest.mark.parametrize("rel,test,old,new", RESTATED)
def test_each_restated_assertion_exists_and_names_its_subject(rel, test, old, new):
    """Half of the criterion, read off the source: the replacement exists, and
    what it is about is named in it rather than derived from another run.

    "Names its subject" is checked as the subject appearing in the test or in
    the module-level constant it asserts against — the artifact list, the event
    list, the section body, the message, the state field set — and as the test
    referring to no module recovered out of history.
    """
    source = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
    body = function_source(source, test)

    assert builds_a_module(body) == [], test
    assert reads_a_files_text(body) == [], test
    for retired in RETIRED_NAMES:
        assert retired not in body, (test, retired)

    named = [node for node in ast.walk(ast.parse(body))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    referenced = {node.id for node in ast.walk(ast.parse(body))
                  if isinstance(node, ast.Name)}
    assert len(named) > 1 or referenced & {
        "CLEAN_RUN_EVENTS", "CLEAN_RUN_ARTIFACTS", "CLEAN_RUN_STAGES",
        "FIRST_RUN_EVENTS", "FIRST_RUN_ARTIFACTS", "FIRST_RUN_STAGES",
        "UNCHANGED_SECTIONS", "ESCALATION_RUN_DIRECTORY", "NEW_FIELDS",
        "PRE_EXTRACTION_MESSAGE"}, test


@pytest.mark.parametrize("rel,test,old,new", RESTATED)
def test_each_restated_assertion_fails_when_its_subject_is_violated(
    repo_copy, rel, test, old, new,
):
    """The other half, and it is the one that cannot be read off source: the
    replacement is run against a coordinator edited so the subject it names is
    violated, and it must go red.

    The unmutated run is the control, and it is what makes the red mean
    something: a copy that could not import, collect or run would fail
    identically under every mutation. So each case asserts green first, then
    red, on the same copy.
    """
    coordinator = repo_copy / COORDINATOR_REL
    # The control is the coordinator as it stands, not as it stood at this
    # story's endpoint. Pinning it there paired a frozen coordinator with a
    # workflow definition that keeps moving — story-028 reshaped the verifier's
    # on_failure and clean_clone keys, and the pinned coordinator could no
    # longer run against them, so every case failed its *control* rather than
    # its subject. The subject here is that a restated assertion goes red when
    # what it names is violated, which is a property of the assertion and the
    # code it is about; each `old` below is still matched exactly once, and the
    # green-then-red pair is unchanged.
    pristine = (REPO_ROOT / COORDINATOR_REL).read_text(encoding="utf-8")
    coordinator.write_text(pristine, encoding="utf-8")

    before = run_one_test(repo_copy, TODAY[rel], test)
    assert before.returncode == 0, before.stdout[-3000:] + before.stderr[-2000:]
    assert "1 passed" in before.stdout, before.stdout[-2000:]

    assert old in pristine, (test, old)
    coordinator.write_text(pristine.replace(old, new, 1), encoding="utf-8")
    try:
        after = run_one_test(repo_copy, TODAY[rel], test)
    finally:
        coordinator.write_text(pristine, encoding="utf-8")

    assert after.returncode != 0, (
        f"{test} passed with its subject violated\n" + after.stdout[-3000:])
    assert test in after.stdout, after.stdout[-2000:]


# --------------------------------------------------------------------------
# The assertions retargeted to read source as text
# --------------------------------------------------------------------------


#: Every assertion that recovered a coordinator only to compare its text, and
#: the coordinator functions each one is about. The names are the subject: a
#: retarget that quietly dropped `_escalate` would have dropped the control.
TEXT_ONLY = [
    ("tests/test_story_020_validation.py",
     "test_the_completion_commit_is_byte_for_byte_the_code_it_was",
     {"_complete", "_escalate"}),
    ("tests/test_story_020_validation.py",
     "test_the_escalation_summary_is_the_text_it_was", {"_escalate"}),
    ("tests/test_story_021_validation.py",
     "test_neither_terminal_commit_was_changed_to_achieve_any_of_this",
     {"_complete", "_escalate", "run_story"}),
    ("tests/test_story_027_validation.py",
     "test_the_composed_message_is_byte_for_byte_the_pre_extraction_string",
     {"_complete"}),
    ("tests/test_story_027_validation.py",
     "test_the_extraction_is_the_only_edit_the_story_made_to_complete",
     {"_complete", "run_story"}),
    ("tests/test_story_027_validation.py",
     "test_reverting_the_repointed_story_020_assertion_re_breaks_it",
     {"_complete", "_escalate"}),
]

COORDINATOR_FUNCTIONS = {"_complete", "_escalate", "run_story",
                         "escalation_summary", "escalation_reason"}


def subjects_of(body: str) -> set[str]:
    """The coordinator functions one assertion is about, however it names them.

    Before this story they were attributes of a recovered module; now they are
    strings handed to a reader. Both are collected, so "the same subject" is a
    question the two forms can be compared on at all.
    """
    tree = ast.parse(body)
    named = {node.attr for node in ast.walk(tree)
             if isinstance(node, ast.Attribute)}
    named |= {node.value for node in ast.walk(tree)
              if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    return named & COORDINATOR_FUNCTIONS


def comparisons_in(node: ast.AST) -> list[str]:
    """The comparison operators under one node, sorted. This is an assertion's
    strictness: an `==` relaxed to an `in`, or a control's `!=` dropped, shows
    up here and nowhere else."""
    return sorted(type(op).__name__ for inner in ast.walk(node)
                  if isinstance(inner, ast.Compare) for op in inner.ops)


def comparisons_of(body: str) -> list[str]:
    return comparisons_in(ast.parse(body))


def without_its_last_assertion(body: str) -> list[str]:
    """The same profile with the assertion's last `assert` dropped.

    The control for the comparison above, and it is taken on the parsed tree
    rather than by deleting a line, because these assertions wrap.
    """
    definition = ast.parse(body).body[0]
    statements = [node for node in definition.body
                  if isinstance(node, ast.Assert)]
    dropped = statements[-1]
    return sorted(operator for node in definition.body if node is not dropped
                  for operator in comparisons_in(node))


@pytest.mark.parametrize("rel,test,subjects", TEXT_ONLY)
def test_each_text_only_assertion_kept_its_subject_strictness_and_control(
    rel, test, subjects,
):
    """Read at this story's two bounds and required to be equal on both counts.

    The subject is the set of coordinator functions the assertion is about; the
    strictness and the control are the comparison operators it uses, as a
    multiset. Every one of these assertions carries its control as a second
    comparison — `_escalate` or `run_story` compared the same way and shown to
    differ, or the old text searched for — so a control dropped in the retarget
    is a missing operator here, and a strictness relaxed from `==` to `in` is a
    changed one.

    Its own control is the weakened copy below: the same reading of the same
    assertion with one comparison changed reports a different profile, so
    equality here is a comparison that can differ.
    """
    before = function_source(baseline_text(rel), test)
    after = function_source(
        (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8"), test)

    assert subjects_of(after) == subjects, test
    assert subjects_of(after) == subjects_of(before), test
    assert comparisons_of(after) == comparisons_of(before), test
    assert len(comparisons_of(after)) >= 2, f"{test} has no control comparison"

    assert without_its_last_assertion(after) != comparisons_of(before), test


@pytest.mark.parametrize("rel,test,subjects", TEXT_ONLY)
def test_each_text_only_assertion_constructs_no_module(rel, test, subjects):
    """The retarget itself: text is read, nothing is loaded. Stated per
    assertion because the module-wide scan would also be satisfied by an
    assertion that had simply been deleted."""
    after = function_source(
        (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8"), test)
    assert builds_a_module(after) == [], test
    before = function_source(baseline_text(rel), test)
    assert "coordinator" in before.lower(), test


def test_the_control_that_accompanied_only_a_comparison_is_gone():
    """The one deletion with no replacement, because its subject was the
    comparison it accompanied: it asserted that the module that comparison
    loaded was the one without the check.

    Present at this story's baseline, absent now — a deletion rather than a
    name that never existed.
    """
    rel = "tests/test_story_021_validation.py"
    name = "test_the_module_that_comparison_used_really_is_the_one_without_the_check"
    assert f"def {name}" in baseline_text(rel)
    assert name not in (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The two defect reproductions, as frozen evidence
# --------------------------------------------------------------------------


ARCHIVE = REPO_ROOT / ".harness" / "runs-archive"

FROZEN = [
    ("story-021-artifact-absorbed",
     "tests/test_story_021_validation.py",
     "test_a_story_artifact_no_longer_reaches_a_story_commit"),
    ("story-027-rerun-onto-finished-branch",
     "tests/test_story_027_validation.py",
     "test_the_defect_this_closes_is_recorded_and_the_same_move_is_refused"),
]


@pytest.mark.parametrize("directory,rel,reader", FROZEN)
def test_each_deleted_reproduction_left_a_record_a_reader_can_find(
    directory, rel, reader,
):
    """A defect whose only account was a deleted test has lost its account, so
    each record is required to be findable, to say what the defect was, to name
    the reproduction it replaces, and to be machine-readable.

    The control is the reproduction itself, which really is gone from the
    module at today's revision and really was there at this story's baseline.
    """
    record = ARCHIVE / directory
    assert (record / "README.md").is_file(), directory
    evidence = json.loads((record / "evidence.json").read_text(encoding="utf-8"))

    assert evidence["defect"], directory
    assert evidence["frozen_by"] == "story-029"
    assert evidence["reproduction"]["module"] == rel
    demonstrated = evidence["reproduction"]["demonstrated"]
    assert demonstrated, directory

    deleted = evidence["reproduction"]["test"]
    assert f"def {deleted}" in baseline_text(rel), deleted
    if deleted != reader:
        assert deleted not in (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")

    prose = (record / "README.md").read_text(encoding="utf-8")
    assert "story-029" in prose and evidence["story"] in prose


@pytest.mark.parametrize("directory,rel,reader", FROZEN)
def test_an_assertion_reads_that_evidence(directory, rel, reader):
    """The record is only evidence if the suite reads it: the surviving
    assertion names the file and asserts on what it says, rather than
    describing it in prose."""
    source = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
    body = function_source(source, reader)

    assert directory in source, directory
    assert "evidence" in body, reader
    assert "demonstrated" in body, reader
    assert body.count("assert") >= 4, reader


def test_the_story_021_record_agrees_with_the_committed_archive_it_points_at():
    """The record's claims about the one instance that really happened are
    checked against that instance's own committed files, independently of the
    assertion in story-021's module that does the same.

    Its control is the archive's own record of what the run reported changed,
    which does name other paths — so "the artifact is not named" is a reading
    that can see names.
    """
    evidence = json.loads(
        (ARCHIVE / "story-021-artifact-absorbed" / "evidence.json")
        .read_text(encoding="utf-8"))
    observed = evidence["observed_instance"]
    archived = REPO_ROOT / observed["archive"]

    patch = (archived / observed["patch"]).read_text(encoding="utf-8")
    record = json.loads(
        (archived / observed["record"]).read_text(encoding="utf-8"))
    named = set(record["modified"]) | set(record["created"]) | set(record["deleted"])

    assert f"diff --git a/{observed['artifact']}" in patch
    assert observed["commit_subject_prefix"] in patch
    assert observed["artifact"] not in named
    assert observed["artifact_named_by_the_runs_own_record"] is False
    assert named, "the run's record names nothing at all"


def test_neither_record_claims_to_have_been_produced_by_rerunning_the_code():
    """Both records say why they are records: the reproduction needed a
    coordinator loaded out of git history, and this story retired that
    capability. A record that omitted the reason would read as a run that was
    simply not repeated.

    The reason was originally written as a TypeError the recovered coordinator
    raised against an object-valued `clean_clone` declaration. That was true of
    the workflow story-028 left on the branch this story was first built on,
    and stopped being true when that ancestry was dropped — a reason with the
    shelf life of the thing it described. The durable reason is the retirement
    itself, which is this story's own subject and cannot expire while the scans
    stand.
    """
    for directory, _, _ in FROZEN:
        evidence = json.loads(
            (ARCHIVE / directory / "evidence.json").read_text(encoding="utf-8"))
        why = evidence["reproduction"]["why_it_cannot_be_rerun"]
        assert "git history" in why, directory
        assert "test_baseline_honesty.py" in why, directory


# --------------------------------------------------------------------------
# The fixture split, and the commit pair story-026 restated
# --------------------------------------------------------------------------


def test_the_split_fixture_builds_one_escalation_from_todays_coordinator_only():
    """`test_reason_is_still_the_section_immediately_after_status` stands on
    today's output alone.

    Read as the fixture it now takes — one that builds a single run — where at
    this story's baseline it took the two-escalation fixture whose second half
    was a recovered module. That it still holds is the suite's own business;
    that it can still fail is the mutation case above.
    """
    rel = "tests/test_story_024_validation.py"
    name = "test_reason_is_still_the_section_immediately_after_status"
    source = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")

    definition = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef) and node.name == name)
    parameters = [argument.arg for argument in definition.args.args]
    assert parameters == ["exhausted_escalation"], parameters

    before = function_source(baseline_text(rel), name)
    assert "the_same_escalation_before_and_after" in before
    assert "the_same_escalation_before_and_after" not in source

    fixture = function_source(source, "exhausted_escalation")
    assert builds_a_module(fixture) == []
    assert reads_a_files_text(fixture) == []
    assert fixture.count("run_story") == 1, "one escalation, not two"


def test_the_restated_story_026_comparison_names_the_same_commit_pair():
    """story-026's lift-and-execute comparison established a commit pair. The
    same pair is established here independently, out of git, and both ends are
    checked — so the restatement is held to the pair rather than to its own
    account of it.

    The endpoint is the commit that added story-011's validation file and the
    oldest whose coordinator knows the artifact story-011 introduced; the
    baseline is that commit's parent and the newest whose coordinator does not.
    That the endpoint is the *adding* commit is established from both ends
    too — the file reads at the endpoint and cannot be read at the baseline.
    The control is the marker itself: it is absent on one side and present on
    the other, so the pair is not two readings of one revision.

    The commit range is resolved through `tests/conftest.py`, and the two
    direct git calls here are a `rev-parse` and nothing else, because
    story-026's own rule — one resolver of a story's own commit range — is not
    one this story is allowed to spend.
    """
    from conftest import NothingToCompareAgainst

    story_011 = "tests/test_story_011_validation.py"
    marker = "execution-history"

    span = story_commit_range(REPO_ROOT / story_011)
    assert span.committed

    assert "def test_" in repository_file_at(story_011, revision=span.endpoint,
                                             repo=REPO_ROOT)
    with pytest.raises(NothingToCompareAgainst):
        repository_file_at(story_011, revision=span.baseline, repo=REPO_ROOT)

    parent = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", f"{span.endpoint}^"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert parent == span.baseline

    assert marker in repository_file_at(COORDINATOR_REL, revision=span.endpoint,
                                        repo=REPO_ROOT)
    assert marker not in repository_file_at(COORDINATOR_REL,
                                            revision=span.baseline,
                                            repo=REPO_ROOT)

    # And the restatement in story-026 is the one making that statement, with
    # no recovered code executed to make it.
    restated = function_source(
        (REPO_ROOT / TODAY["tests/test_story_026_validation.py"])
        .read_text(encoding="utf-8"),
        "test_the_shared_resolution_names_the_pair_the_deleted_mechanism_named")
    assert builds_a_module(restated) == []
    assert "story_commit_range" in restated
    assert "rev-parse" in restated and "--diff-filter=A" in restated
    assert marker in restated or "STORY_011_MARKER" in restated


# --------------------------------------------------------------------------
# The ten tests that fail at this story's baseline
# --------------------------------------------------------------------------


#: What `.venv/bin/python -m pytest tests/ -q` reports at this story's
#: baseline, measured here against a copy of the baseline tree rather than
#: transcribed: six failures and four collection errors, ten in a suite of
#: 1253, every one of them decay in an instrument that recovered a coordinator
#: out of history and none of them a defect in the work that broke them.
#:
#: Four of the six survive as tests and had to be replaced; two were deleted
#: with their subject accounted for elsewhere, which is a different claim and
#: is stated separately below.
BASELINE_FAILURES_REPLACED = [
    ("tests/test_story_021_validation.py",
     "test_a_clean_fresh_run_is_what_it_was_before_the_check_existed"),
    ("tests/test_story_021_validation.py",
     "test_a_story_artifact_no_longer_reaches_a_story_commit"),
    ("tests/test_story_027_validation.py",
     "test_a_first_run_of_a_story_whose_branch_does_not_exist_is_unaffected"),
    ("tests/test_story_027_validation.py",
     "test_a_completed_state_still_meets_the_already_ended_refusal"),
]

#: The two that were deleted, and where each one's subject went: a control
#: whose only subject was the comparison it accompanied, and a defect
#: reproduction whose account is now frozen evidence.
BASELINE_FAILURES_DELETED = [
    ("tests/test_story_021_validation.py",
     "test_the_module_that_comparison_used_really_is_the_one_without_the_check",
     None),
    ("tests/test_story_027_validation.py",
     "test_the_earlier_coordinator_reruns_the_finished_story_and_reports_success",
     "story-027-rerun-onto-finished-branch"),
]

#: The module whose collection failed at the baseline — four errors — because
#: the fixture every one of those tests shared could no longer build.
BASELINE_COLLECTION_ERRORS = "tests/test_story_024_validation.py"


@pytest.mark.parametrize("rel,test", BASELINE_FAILURES_REPLACED)
def test_each_test_failing_at_the_baseline_now_exists_and_is_not_skipped(rel, test):
    """These are green because they were replaced, so each is required to
    still be a test with a body and with its subject named — a skipped or
    hollowed-out test would satisfy "the suite passes" and nothing else.

    Each also appears in the mutation table above, where it is run against a
    coordinator that violates the subject it names and required to go red.
    """
    source = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
    body = function_source(source, test)
    assert "assert" in body, test
    assert "skip" not in body, test
    assert builds_a_module(body) == [], test
    assert (rel, test) in [(param.values[0], param.values[1])
                           for param in RESTATED], test


@pytest.mark.parametrize("rel,test,record", BASELINE_FAILURES_DELETED)
def test_each_deleted_baseline_failure_has_its_subject_accounted_for(
    rel, test, record,
):
    """The two that were deleted rather than replaced. A test that fails and
    is then deleted is the one move this story could make that would turn the
    suite green while losing coverage, so each is required to be gone *and* to
    have somewhere its subject went.

    The control with no independent subject is checked to have been exactly
    that — it names the comparison it accompanied — and the reproduction is
    checked to have left a record an assertion reads.
    """
    before = baseline_text(rel)
    after = (REPO_ROOT / TODAY[rel]).read_text(encoding="utf-8")
    assert f"def {test}" in before, test
    assert test not in after, test

    if record is None:
        deleted = function_source(before, test)
        # It had no subject of its own: every assertion in it was about the
        # recovered module the comparison beside it used, which it took as a
        # fixture and passed straight back into the coordinator run.
        assert "pre_story_coordinator" in deleted
        assert "coordinator=pre_story_coordinator" in deleted
        assert "pre_story_coordinator" not in after
    else:
        evidence = json.loads((ARCHIVE / record / "evidence.json")
                              .read_text(encoding="utf-8"))
        assert evidence["reproduction"]["test"] == test
        assert evidence["reproduction"]["module"] == rel


def test_the_module_that_could_not_collect_at_the_baseline_collects_now():
    """Its four tests failed as collection errors because the fixture they
    shared built a recovered coordinator. The fixture was split; the module is
    required to collect and to still hold those tests."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", TODAY[BASELINE_COLLECTION_ERRORS],
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-3000:]
    assert "error" not in result.stdout.lower(), result.stdout[-2000:]
    assert "test_reason_is_still_the_section_immediately_after_status" \
        in result.stdout


def test_the_suite_passes_without_any_recovered_module_being_made_runnable():
    """The story's own constraint: the ten are green because the assertions
    were replaced, not because a shim was added or widened.

    Stated as three absences with the baseline beside each: no compatibility
    shim survives in the shared module, no module under tests/ freezes a copy
    of the workflow, schemas or config beside frozen code, and nothing under
    orchestration/, workflows/, schemas/, prompts/ or scripts/ changed in this
    story's own commit range.
    """
    from conftest import story_diff

    shared = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "runnable_against_current_modules" not in shared
    # The workflow key, in the form a frozen copy of the workflow would carry
    # it. Written bare until story-038, which named a renamed validation
    # module `test_clean_clone_check.py` and put that name in the shared
    # module's origin map — a module *name* containing the key's spelling,
    # which is not a frozen declaration of it. Quoting narrows the match to
    # what the assertion is about; a conftest that did freeze the key still
    # fails here.
    assert '"clean_clone"' not in shared

    for name, source in module_sources().items():
        if name == THIS_FILE.name:
            continue
        assert "workflows/story-workflow.yaml\"" not in source or "frozen" not in source

    untouched = ["orchestration/", "workflows/", "schemas/", "prompts/",
                 "scripts/", ".harness/config.yaml", ".harness/stories/"]
    assert story_diff(untouched, validation_file=THIS_FILE) == ""
    # The control: the paths this story did change, read the same way.
    assert story_diff(["tests/"], validation_file=THIS_FILE) != ""
