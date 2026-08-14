"""Independent validation for story-031: a mutation control mutates the
working tree, never a pinned revision.

The story's subject is a *fourth rule* in `tests/test_baseline_honesty.py` and
a scan that reports it. What has to be shown is almost entirely absence — no
module under tests/ is reported, no exemption list exists, no scan derives
another, no commit SHA is written into the new code — and an absence assertion
is exactly the kind that passes when it has stopped looking. So nothing here
is asserted by looking once:

  * every "the scan reports nothing" sits beside a source that differs from it
    in one respect only and *is* reported, so the pair shows which half of the
    pairing each case is missing rather than asserting it;
  * "no module under tests/ is reported" is run beside the same routine over a
    directory carrying a planted control, which it reports;
  * "the module names no exempt module for this rule" is read off its AST
    beside the same reading applied to a source that does declare one, which it
    finds;
  * "no commit SHA is written into the new code" is a reading of every string
    constant in the module, run beside the same reading over a rendering with a
    SHA planted in it;
  * "no scan calls another" and "each rule states its limits" are read off the
    module's source beside modified renderings of that source in which the
    violation is present, which the same readings report;
  * "the one known true positive is reported" is the only direct evidence the
    rule would have caught what it exists for, and it is recovered through the
    shared reader at a story bound rather than at a SHA — flagged before its
    repair, clean after.

The sources planted here are written for this file. They are different sources
from the ones planted beside the scan itself, so a scan narrowed to the shapes
its own author happened to write goes red here.

Nothing here invokes a model, and nothing here runs a recovered module: this
file reads the repository's history only through the shared reader in
`tests/conftest.py`, writes no recovered text to any path, and executes
nothing it recovers — which is the rule it validates, applied to itself.
"""
import ast
import re
from pathlib import Path

import pytest

from conftest import (BASELINE, ENDPOINT, function_source_at,
                      repository_file_at)
from test_baseline_honesty import Flag, mutation_controls

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
THIS_FILE = Path(__file__)

#: The module the story delivers the rule into, named here rather than read
#: off anything, so a rule that moved house fails this file.
THE_RULES_MODULE = "tests/test_baseline_honesty.py"

#: The scan this story adds, and the three it joins. Named independently of
#: the implementation's own list.
THE_NEW_SCAN = "mutation_controls"
THE_RULES_IT_JOINS = ("flagged_calls", "undeclared_targets",
                      "module_construction", "git_text_reads")

#: The file that carried the one known instance, and the validation file of
#: the story whose run commit repaired it. Named, not pinned: the pre-repair
#: text is the baseline of *that* story's own commit range, resolved through
#: the shared reader, so a rebase does not move it and no SHA is written here.
#:
#: Two spellings for one file, because story-038 renamed it: inside
#: story-028's range it is `tests/test_story_029_validation.py`, and in the
#: working tree it is `tests/test_git_history_loading_retired.py`. Each read
#: below uses the name the file has at the end of the range it is reading.
THE_REPAIRED_FILE = "tests/test_story_029_validation.py"
THE_REPAIRED_FILE_TODAY = "tests/test_git_history_loading_retired.py"
THE_STORY_THAT_REPAIRED_IT = "tests/test_retry_routing.py"


def the_rules_module() -> str:
    return (REPO_ROOT / THE_RULES_MODULE).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The sources planted here
#
# One control, written five ways, and each benign case is that same control
# with exactly one of the three required halves removed. Stating them as
# near-neighbours is what makes the benign assertions worth anything: a scan
# that reports nothing because it has stopped looking fails the positive half
# of every pair.
# --------------------------------------------------------------------------


#: The pairing, stated with a `revision=` keyword and run under pytest.
PINNED_KEYWORD_AND_SPAWN = (
    "import subprocess, sys\n"
    "def control(clone, a_revision):\n"
    "    pinned = read_file('orchestration/coordinator.py', revision=a_revision)\n"
    "    (clone / 'orchestration' / 'coordinator.py').write_text(pinned)\n"
    "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n"
)

#: The same pairing with a story bound instead of a revision, and the one
#: substitution a control actually makes carried through `.replace(...)`.
PINNED_BOUND_AND_REPLACE = (
    "import subprocess, sys\n"
    "def control(clone, rel):\n"
    "    pinned = read_file(rel, validation_file=THE_FILE, bound=THE_BOUND)\n"
    "    (clone / rel).write_text(pinned.replace(OLD, NEW, 1))\n"
    "    return subprocess.run([sys.executable, '-m', 'pytest', rel], cwd=clone)\n"
)

#: The history reached without the shared reader, spawned.
PINNED_GIT_SHOW_SPAWNED = (
    "import subprocess, sys\n"
    "def control(clone, rel, revision):\n"
    "    pinned = subprocess.check_output(\n"
    "        ['git', 'show', f'{revision}:{rel}'], text=True)\n"
    "    (clone / rel).write_text(pinned)\n"
    "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n"
)

#: The history reached through a one-line local wrapper — the form every
#: per-story module writes — and the recovered text loaded as a module.
PINNED_GIT_SHOW_WRAPPED_AND_LOADED = (
    "def control(rel, revision, tmp_path):\n"
    "    pinned = sh(REPO_ROOT, 'show', f'{revision}:{rel}')\n"
    "    target = tmp_path / 'subject.py'\n"
    "    target.write_text(pinned)\n"
    "    return bring_in(target, [], name='subject', tmp_path=tmp_path)\n"
)

#: The same, executed in this process rather than in another.
PINNED_GIT_SHOW_AND_EXEC = (
    "def control(clone, rel, revision):\n"
    "    pinned = sh(REPO_ROOT, 'show', f'{revision}:{rel}')\n"
    "    (clone / rel).write_text(pinned)\n"
    "    exec((clone / rel).read_text(), {})\n"
)

PLANTED_CONTROLS = {
    "a-revision-keyword-written-and-run-under-pytest": PINNED_KEYWORD_AND_SPAWN,
    "a-story-bound-written-through-replace-and-run": PINNED_BOUND_AND_REPLACE,
    "a-spawned-git-show-written-and-run": PINNED_GIT_SHOW_SPAWNED,
    "a-wrapped-git-show-written-and-loaded": PINNED_GIT_SHOW_WRAPPED_AND_LOADED,
    "a-wrapped-git-show-written-and-exec'd": PINNED_GIT_SHOW_AND_EXEC,
}

#: Each benign source, beside the planted control it is one edit away from.
#: The edit is named, because that is the whole content of the claim: the
#: pairing is what is reported, and removing any one half must silence it.
BENIGN_BESIDE_ITS_CONTROL = {
    "the-subject-read-from-the-working-tree-instead": (
        PINNED_KEYWORD_AND_SPAWN,
        "import subprocess, sys\n"
        "def control(clone, a_revision):\n"
        "    pinned = (REPO_ROOT / 'orchestration/coordinator.py').read_text()\n"
        "    (clone / 'orchestration' / 'coordinator.py').write_text(pinned)\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n",
    ),
    "the-pinned-read-only-compared-never-written": (
        PINNED_KEYWORD_AND_SPAWN,
        "import subprocess, sys\n"
        "def control(clone, a_revision):\n"
        "    pinned = read_file('orchestration/coordinator.py', revision=a_revision)\n"
        "    assert pinned != (REPO_ROOT / 'orchestration/coordinator.py').read_text()\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n",
    ),
    "the-pinned-read-written-but-nothing-executed": (
        PINNED_GIT_SHOW_SPAWNED,
        "import subprocess\n"
        "def control(clone, rel, revision):\n"
        "    pinned = subprocess.check_output(\n"
        "        ['git', 'show', f'{revision}:{rel}'], text=True)\n"
        "    (clone / rel).write_text(pinned)\n"
        "    assert (clone / rel).read_text() == pinned\n",
    ),
    "the-shared-loader-against-a-working-tree-path": (
        PINNED_GIT_SHOW_WRAPPED_AND_LOADED,
        "def control(tmp_path):\n"
        "    return bring_in(REPO_ROOT / 'orchestration' / 'coordinator.py',\n"
        "                    [(OLD, NEW)], name='subject', tmp_path=tmp_path)\n",
    ),
    "a-revision-named-as-None-which-bounds-nothing": (
        PINNED_KEYWORD_AND_SPAWN,
        "import subprocess, sys\n"
        "def control(clone):\n"
        "    text = read_file('orchestration/coordinator.py', revision=None)\n"
        "    (clone / 'orchestration' / 'coordinator.py').write_text(text)\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n",
    ),
}


def flags_for(source: str, module: str = "planted.py") -> list[Flag]:
    return mutation_controls(source, module)


# --------------------------------------------------------------------------
# The scan, held to what the story says it is
# --------------------------------------------------------------------------


def test_the_scan_takes_a_source_and_a_module_and_returns_flag_records():
    """The delivered shape: the same `(source, module) -> list[Flag]` the three
    scans beside it have, with the module and the line it reports read off the
    record rather than out of a message."""
    signature = ast.parse(the_rules_module())
    defined = {node.name: node for node in signature.body
               if isinstance(node, ast.FunctionDef)}
    assert THE_NEW_SCAN in defined, "the story's scan is not defined here"
    assert [argument.arg for argument in defined[THE_NEW_SCAN].args.args] \
        == ["source", "module"]

    flags = flags_for(PINNED_KEYWORD_AND_SPAWN, "some_module.py")
    assert len(flags) == 1, flags
    flag = flags[0]
    assert isinstance(flag, Flag)
    assert flag.module == "some_module.py"
    assert PINNED_KEYWORD_AND_SPAWN.splitlines()[flag.line - 1].strip() \
        .startswith("(clone /")
    assert "revision" in flag.reason


@pytest.mark.parametrize("planted", list(PLANTED_CONTROLS),
                         ids=list(PLANTED_CONTROLS))
def test_each_shape_the_rule_claims_to_cover_is_reported(planted):
    """Every shape the rule names, demonstrated rather than described: a
    keyword-bounded read and a story bound, a spawned `git show` and a wrapped
    one, run under pytest, loaded as a module, and exec'd here."""
    flags = flags_for(PLANTED_CONTROLS[planted])
    assert len(flags) == 1, flags


@pytest.mark.parametrize("case", list(BENIGN_BESIDE_ITS_CONTROL),
                         ids=list(BENIGN_BESIDE_ITS_CONTROL))
def test_each_benign_case_is_unreported_and_its_near_neighbour_is_not(case):
    """The control for every benign assertion in this file.

    Each pair differs in exactly one respect — where the subject was read, or
    whether it is written, or whether anything runs — so "the scan says
    nothing" is shown to be a statement about that difference rather than
    about a scan that has stopped looking.
    """
    reported, unreported = BENIGN_BESIDE_ITS_CONTROL[case]
    assert flags_for(reported), "the near-neighbour control was not reported"
    assert flags_for(unreported) == []


def test_the_pairing_is_the_subject_and_neither_half_is_enough():
    """Stated once as a whole, over the same three-line control: the read
    alone, the write alone and the execution alone say nothing, and only the
    three together are reported."""
    read_only = ("def control(a_revision):\n"
                 "    return read_file('orchestration/coordinator.py',\n"
                 "                     revision=a_revision)\n")
    write_only = ("import subprocess, sys\n"
                  "def control(clone, rel):\n"
                  "    (clone / rel).write_text(WORKING_TREE_TEXT)\n"
                  "    return subprocess.run([sys.executable, '-m', 'pytest'],\n"
                  "                          cwd=clone)\n")
    execute_only = ("import subprocess, sys\n"
                    "def control(clone):\n"
                    "    return subprocess.run([sys.executable, '-m', 'pytest'],\n"
                    "                          cwd=clone)\n")
    for half in (read_only, write_only, execute_only):
        assert flags_for(half) == [], half
    assert flags_for(PINNED_KEYWORD_AND_SPAWN)


def test_a_read_and_an_execution_split_across_functions_is_the_stated_limit():
    """The limit the rule states about itself, held to what it actually does:
    the same control with the pinned read moved into a helper is unreported,
    and the one-function form of it is reported. A limit nobody exercises is
    a claim about the prose, not about the scan."""
    laundered = (
        "import subprocess, sys\n"
        "def pinned_text(rel, at):\n"
        "    return read_file(rel, revision=at)\n"
        "def control(clone, rel, at):\n"
        "    (clone / rel).write_text(pinned_text(rel, at))\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n"
    )
    assert flags_for(laundered) == []
    assert flags_for(PINNED_KEYWORD_AND_SPAWN)


# --------------------------------------------------------------------------
# The two forms of a bounded read, held to the same behaviour
#
# The story says a revision bound is recognised two ways — stated in a keyword,
# or reached by asking git directly — and the acceptance criteria say the
# *pairing* is what gets reported in either form. Those two claims interact in
# one place, and it is where this file's first pass found the scan wrong: a
# spawned `git show` is itself a process spawn, so the read supplied its own
# execution half and a bounded read written to a path but never run was
# reported in the git form and not in the keyword form.
#
# So the forms are not asserted separately. The same control is written twice,
# differing only in how the bound is stated, and the two are required to agree
# case for case. A scan that recognises only one form, or that lets either form
# stand in for its own second half, disagrees somewhere in this table.
# --------------------------------------------------------------------------


#: The bound stated in a keyword — the shared reader's form.
A_KEYWORD_BOUND = "read_file(rel, revision=at)"

#: The same bound reached without the shared reader, which is the form the rule
#: exists to see: a spawned `git show` asking for a file's text at a revision.
A_SPAWNED_GIT_BOUND = (
    "subprocess.check_output(['git', 'show', f'{at}:{rel}'], text=True)")

THE_TWO_FORMS = {"a-revision-keyword": A_KEYWORD_BOUND,
                 "a-spawned-git-show": A_SPAWNED_GIT_BOUND}


def a_control(read: str, *, executes: bool) -> str:
    """One mutation control, parameterised by how its subject is bounded and by
    whether anything runs the text it writes."""
    source = ("import subprocess, sys\n"
              "def control(clone, rel, at):\n"
              f"    pinned = {read}\n"
              "    (clone / rel).write_text(pinned)\n")
    if executes:
        source += ("    return subprocess.run([sys.executable, '-m', 'pytest'],\n"
                   "                          cwd=clone)\n")
    return source


@pytest.mark.parametrize("form", list(THE_TWO_FORMS), ids=list(THE_TWO_FORMS))
def test_both_forms_of_a_bound_are_reported_when_the_written_text_is_run(form):
    """The positive half of the table, and the control for the negative half
    below: in either form, the complete pairing is reported."""
    assert len(flags_for(a_control(THE_TWO_FORMS[form], executes=True))) == 1


@pytest.mark.parametrize("form", list(THE_TWO_FORMS), ids=list(THE_TWO_FORMS))
def test_neither_form_is_reported_when_nothing_runs_what_was_written(form):
    """The defect this file reported at the first attempt, stated as the claim
    it always was: a bounded read written to a path and never executed is two
    thirds of a control and is unreported — in the git form exactly as in the
    keyword form, because the read is not its own execution."""
    read = THE_TWO_FORMS[form]
    assert flags_for(a_control(read, executes=False)) == []
    assert flags_for(a_control(read, executes=True)), \
        "the near-neighbour that does execute was not reported"


def test_the_two_forms_agree_case_for_case():
    """The table read as a whole rather than row by row. Stated this way
    because the failure it guards is a *divergence*: each form was defensible
    on its own, and only the disagreement between them showed that one of them
    had stopped reporting the pairing."""
    verdicts = {
        (form, executes): bool(flags_for(a_control(read, executes=executes)))
        for form, read in THE_TWO_FORMS.items()
        for executes in (True, False)
    }
    for executes in (True, False):
        by_form = {form: verdicts[(form, executes)] for form in THE_TWO_FORMS}
        assert len(set(by_form.values())) == 1, (executes, by_form)
    assert verdicts[("a-revision-keyword", True)] is True
    assert verdicts[("a-revision-keyword", False)] is False


def test_a_bounded_read_is_never_counted_as_the_execution_of_what_it_wrote():
    """The narrow form of the fix, held so an over-correction is visible too.

    Two spawned reads and a write, with nothing else running: still unreported,
    so the exclusion is not satisfied by there merely being one read. Add a
    spawn that is not a read and it is reported, so the exclusion has not been
    widened into "a control that spawns git is never reported".
    """
    two_reads_and_a_write = (
        "import subprocess\n"
        "def control(clone, rel, at, other):\n"
        "    pinned = subprocess.check_output(\n"
        "        ['git', 'show', f'{at}:{rel}'], text=True)\n"
        "    also = subprocess.check_output(\n"
        "        ['git', 'show', f'{at}:{other}'], text=True)\n"
        "    (clone / rel).write_text(pinned)\n"
        "    (clone / other).write_text(also)\n"
    )
    assert flags_for(two_reads_and_a_write) == []

    and_then_run = two_reads_and_a_write + (
        "    subprocess.run(['python', '-m', 'pytest'], cwd=clone)\n")
    assert len(flags_for(and_then_run)) == 2, flags_for(and_then_run)


def test_the_execution_half_still_accepts_a_spawn_that_is_not_a_read():
    """The other side of the same over-correction: a `git` spawn that is not
    asking for a file's text at a revision — checking out a clone, say — is not
    a bounded read, so it is not excluded from the execution half."""
    checkout_then_run = (
        "import subprocess, sys\n"
        "def control(clone, rel, at):\n"
        "    pinned = read_file(rel, revision=at)\n"
        "    subprocess.run(['git', '-C', str(clone), 'checkout', '-b', 'w'])\n"
        "    (clone / rel).write_text(pinned)\n"
        "    return subprocess.run([sys.executable, '-m', 'pytest'], cwd=clone)\n"
    )
    assert len(flags_for(checkout_then_run)) == 1


# --------------------------------------------------------------------------
# No exemption list, shown rather than stated
# --------------------------------------------------------------------------


def exempt_constants(source: str) -> set[str]:
    """Every module-level constant naming modules a rule excuses."""
    return {node.targets[0].id
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.endswith("EXEMPT_MODULES")}


def test_the_reading_of_exemption_constants_finds_one_when_it_is_there():
    """The control for the assertion below: the same reading, over a source
    that does declare an exemption for this rule, finds it."""
    planted = ("MUTATION_EXEMPT_MODULES = ('conftest.py',)\n"
               "def mutation_controls(source, module):\n"
               "    return []\n")
    assert "MUTATION_EXEMPT_MODULES" in exempt_constants(planted)


def test_the_new_rule_declares_no_exemption_and_the_scan_applies_none():
    """The absence is this rule's claim, so it is read three ways: no fourth
    constant exists, the scan's own body tests no module name against one, and
    a planted control is reported whatever module it is said to belong to —
    including the name the other three rules excuse."""
    source = the_rules_module()
    assert exempt_constants(source) == {"EXEMPT_MODULES",
                                        "CONSTRUCTION_EXEMPT_MODULES",
                                        "READER_EXEMPT_MODULES"}

    scan = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == THE_NEW_SCAN)
    mentioned = {inner.id for inner in ast.walk(scan)
                 if isinstance(inner, ast.Name)}
    assert not any(name.endswith("EXEMPT_MODULES") for name in mentioned)

    for module in ("conftest.py", "test_mutation_controls.py",
                   "test_baseline_honesty.py"):
        assert len(flags_for(PINNED_BOUND_AND_REPLACE, module)) == 1, module


def test_the_prose_says_why_there_is_nowhere_this_rule_would_be_wrong():
    """The story asks for the reason in prose beside the rule, not only for the
    absence: the two rules above excuse the one place their subject is correct,
    and a mutation of pinned source has no such place because the shared
    mutation loader takes a working-tree path."""
    source = the_rules_module()
    stated = source[:source.index(f"def {THE_NEW_SCAN}(")]
    stated = stated[stated.index("A fourth rule"):]
    assert "names no exempt module" in stated
    assert "working-tree path" in stated
    assert "conftest.py" in stated


# --------------------------------------------------------------------------
# The rule run over the suite, beside a directory it reports
# --------------------------------------------------------------------------


def scan_every_module(paths: list[Path]) -> list[Flag]:
    return [flag for path in sorted(paths)
            for flag in mutation_controls(path.read_text(encoding="utf-8"),
                                          path.name)]


def test_every_module_under_tests_including_conftest_is_unreported():
    """The rule's live claim. `conftest.py` is included deliberately: the other
    two rules excuse it and this one does not, so leaving it out would make
    this the assertion the story says it is not."""
    modules = list(TESTS_DIR.glob("*.py"))
    assert TESTS_DIR / "conftest.py" in modules
    assert THIS_FILE in modules
    flags = scan_every_module(modules)
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_the_same_sweep_reports_a_directory_that_carries_one(tmp_path):
    """The control for the sweep above: the identical routine over a directory
    holding one planted control reports it, so the green sweep is a statement
    about the suite rather than about a sweep that reads nothing."""
    for name, source in (("conftest.py", "def helper():\n    return 1\n"),
                         ("test_a_control.py", PINNED_KEYWORD_AND_SPAWN)):
        (tmp_path / name).write_text(source, encoding="utf-8")
    flags = scan_every_module(list(tmp_path.glob("*.py")))
    assert [flag.module for flag in flags] == ["test_a_control.py"]


# --------------------------------------------------------------------------
# The one known true positive, recovered at a story bound
# --------------------------------------------------------------------------


def before_the_repair() -> str:
    """`tests/test_story_029_validation.py` as it stood before story-028's run
    commit repaired it: the baseline of that story's own commit range, which is
    the parent of the commit that added its validation file."""
    return repository_file_at(
        THE_REPAIRED_FILE,
        validation_file=REPO_ROOT / THE_STORY_THAT_REPAIRED_IT,
        bound=BASELINE, repo=REPO_ROOT)


def test_the_recovered_text_is_the_pre_repair_text_and_not_todays():
    """Everything below leans on this recovery, so it is asserted: recovered,
    parseable, carrying the pinned read the repair removed, and different from
    the file in the working tree."""
    before = before_the_repair()
    ast.parse(before)
    assert "bound=ENDPOINT" in before
    assert before != (REPO_ROOT / THE_REPAIRED_FILE_TODAY).read_text(
        encoding="utf-8")


def test_the_scan_reports_the_one_known_instance_and_not_its_repair():
    """The only direct evidence the rule would have caught what it exists for,
    stated as the pair it has to be: reported before the repair, reported by
    nothing after it."""
    assert mutation_controls(before_the_repair(), Path(THE_REPAIRED_FILE).name)
    assert mutation_controls(
        (REPO_ROOT / THE_REPAIRED_FILE_TODAY).read_text(encoding="utf-8"),
        Path(THE_REPAIRED_FILE_TODAY).name) == []


def hex_revisions(source: str) -> set[str]:
    """Every string constant in a source that reads as a commit SHA."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(re.findall(r"\b[0-9a-f]{7,40}\b", node.value))
    return found


def test_the_reading_for_a_pinned_sha_finds_one_when_it_is_there():
    """The control for the assertion below."""
    planted = ("REVISION = 'aa4dd5b9c1f2e3'\n"
               "def before():\n"
               "    return repository_file_at(REL, revision=REVISION)\n")
    assert hex_revisions(planted) == {"aa4dd5b9c1f2e3"}


def test_no_commit_sha_is_written_into_the_rules_module():
    """The historical bound is resolved through the shared reader and the
    shared commit-range resolution, so a rebase does not move it. A SHA
    written into the source is the failure that would make the regression case
    stop being about anything."""
    assert hex_revisions(the_rules_module()) == set()

    calls = [node for node in ast.walk(ast.parse(the_rules_module()))
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "repository_file_at"]
    assert calls, "the module recovers no historical text at all"
    for call in calls:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "bound" in keywords and "validation_file" in keywords
        assert "revision" not in keywords


# --------------------------------------------------------------------------
# Four rules, stated separately, each stating its limits
# --------------------------------------------------------------------------


def scans_called_by(source: str, scans: tuple[str, ...]) -> dict[str, set[str]]:
    """For each named scan, which of the others its body calls."""
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    return {name: {inner.func.id for inner in ast.walk(functions[name])
                   if isinstance(inner, ast.Call)
                   and isinstance(inner.func, ast.Name)} & set(scans) - {name}
            for name in scans if name in functions}


def test_the_reading_of_one_scan_calling_another_finds_it_when_it_is_there():
    """The control for the assertion below: the same reading over a source in
    which the new scan draws its enforcement from a neighbour reports it."""
    planted = ("def git_text_reads(source, module):\n"
               "    return []\n"
               "def mutation_controls(source, module):\n"
               "    return git_text_reads(source, module)\n")
    assert scans_called_by(planted, ("git_text_reads", "mutation_controls")) \
        == {"git_text_reads": set(), "mutation_controls": {"git_text_reads"}}


def test_no_scan_draws_its_enforcement_from_another():
    """Four rules, four subjects. Read off the module's source rather than
    asserted about it, because "these are separate" is the kind of claim that
    stays written down after it has stopped being true."""
    scans = THE_RULES_IT_JOINS + (THE_NEW_SCAN,)
    called = scans_called_by(the_rules_module(), scans)
    assert set(called) == set(scans), "a named scan is missing from the module"
    for name, others in called.items():
        assert others == set(), (name, others)


def limits_paragraph(source: str, scan: str) -> str:
    """The `What it does not cover` paragraph stated above one scan."""
    stated = source[:source.index(f"def {scan}(")]
    return stated[stated.rindex("What it does not cover"):]


def test_the_reading_of_a_limits_paragraph_goes_red_when_it_is_stripped():
    """The control for the assertions below: over a rendering of the module
    with the new rule's limits paragraph removed, the same reading either finds
    a different rule's paragraph or none — either way it no longer states this
    rule's limits."""
    source = the_rules_module()
    paragraph = limits_paragraph(source, THE_NEW_SCAN)
    stripped = source.replace(paragraph, "\n")
    assert "obfuscation" in paragraph
    assert paragraph not in stripped
    with pytest.raises(AssertionError):
        assert_states_its_limits(stripped, THE_NEW_SCAN)


def assert_states_its_limits(source: str, scan: str) -> None:
    """The four limits the story names, required by name of the new rule."""
    limits = limits_paragraph(source, scan)
    assert "split across functions" in limits
    assert "helper" in limits and "fixture" in limits
    assert "without asking git for it" in limits
    assert "obfuscation" in limits
    assert "not tamper-proof" in source[:source.index(f"def {scan}(")]


def test_the_new_rule_states_what_it_does_not_cover():
    assert_states_its_limits(the_rules_module(), THE_NEW_SCAN)


@pytest.mark.parametrize("scan", ["module_construction", "git_text_reads",
                                  "mutation_controls"])
def test_the_delivered_meta_assertion_governs_all_three_rules(scan):
    """The story extends story-029's two meta-assertions to three rules. The
    extension is read off the delivered tests — the parametrization of the
    limits assertion, and the scans the separateness assertion names — so a
    third rule added without extending them fails here."""
    source = the_rules_module()
    tree = ast.parse(source)

    limits_test = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_each_new_rule_states_what_it_does_not_cover")
    parametrized = {constant.value for constant in ast.walk(limits_test)
                    if isinstance(constant, ast.Constant)}
    assert scan in parametrized

    separateness = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("test_the_three_rules_are_stated_separately"))
    named = {constant.value for constant in ast.walk(separateness)
             if isinstance(constant, ast.Constant)}
    assert scan in named

    # And each of the three really does state its limits, by the reading above
    # rather than by the delivered test's own.
    assert "What it does not cover" in limits_paragraph(source, scan)


# --------------------------------------------------------------------------
# The three rules already there, unchanged in reach
# --------------------------------------------------------------------------


#: What the story says it does not touch: the three existing scans and the
#: helpers their matching is built out of.
UNCHANGED = ("flagged_calls", "undeclared_targets", "module_construction",
             "git_text_reads", "_is_subprocess_call", "_git_argument_list",
             "_targets_the_repository_root", "_dishonest_baseline",
             "_asks_for_a_files_text", "_git_words")


@pytest.mark.parametrize("name", UNCHANGED)
def test_no_existing_scan_was_widened_by_this_story(name):
    """A fourth rule, not three wider ones. Each existing scan's source at this
    story's baseline is required to equal its source today, which is a stronger
    reading than "the suite still passes"."""
    before = function_source_at(THE_RULES_MODULE, name,
                                validation_file=THIS_FILE, bound=BASELINE,
                                repo=REPO_ROOT)
    after = function_source_at(THE_RULES_MODULE, name,
                               validation_file=THIS_FILE, bound=ENDPOINT,
                               repo=REPO_ROOT)
    assert before == after, name


def test_the_baseline_comparison_above_can_tell_two_sources_apart():
    """The control for it: the same comparison over a function this story did
    change reports a difference, so an equal-source assertion that could never
    differ is not what the parametrization above is doing."""
    before = repository_file_at(THE_RULES_MODULE, validation_file=THIS_FILE,
                                bound=BASELINE, repo=REPO_ROOT)
    assert THE_NEW_SCAN not in before
    assert THE_NEW_SCAN in the_rules_module()


@pytest.mark.parametrize("constant", ["EXEMPT_MODULES", "CONSTRUCTION_EXEMPT_MODULES",
                                      "READER_EXEMPT_MODULES", "SPAWNING_CALLS",
                                      "MODULE_CONSTRUCTORS", "SOURCE_EXECUTORS",
                                      "CONTENT_SUBCOMMANDS"])
def test_no_existing_rules_vocabulary_was_changed(constant):
    """The other half of "unchanged in reach": the word lists the three scans
    match on are what they were at this story's baseline."""
    before = repository_file_at(THE_RULES_MODULE, validation_file=THIS_FILE,
                                bound=BASELINE, repo=REPO_ROOT)
    assert assignment(before, constant) == assignment(the_rules_module(), constant)


def assignment(source: str, name: str) -> str:
    """One module-level constant's value, as source text."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            return ast.dump(node.value)
    raise AssertionError(f"{name} is not assigned at this module's top level")
