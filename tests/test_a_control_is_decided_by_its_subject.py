"""Independent validation for story-206: a negative control that cannot fail
is reported by a check rather than left to a rule.

A test that asserts something is absent passes for two very different reasons:
the thing really is absent, or the test has stopped looking. The story adds a
mechanical rule for one narrow version of the second — a test that plants a
phrase into a value and then asserts that same phrase against that same value,
without ever handing the result to the check it claims to be demonstrating —
and repairs the sites in this suite that the rule reports.

Almost everything this story claims is an absence: the scan reports nothing
over the suite, it leaves the sound control of the same written shape alone, it
names no register of known violations, and the repaired controls are reported
by nothing. An absence assertion is exactly what this story exists to distrust,
so nothing here is asserted by looking once:

  * every "the scan says nothing" sits beside a source differing from it in one
    respect only, which the same scan *does* report — so the silence is that
    one difference deciding rather than a scan that has stopped seeing;
  * the clause that separates a sound control from a vacuous one is shown
    against a module of this repository's rather than only against sources
    composed here: `tests/test_mutation_controls.py` writes exactly the
    reported shape and is left alone, and the same text with its call to the
    real check removed is reported;
  * the only evidence that the rule catches the defect it was written for,
    rather than the shapes its own author happened to compose, is recovered
    from this repository's history — the controls story-203 repaired, read at
    the revision before it repaired them, reported there and reported by
    nothing in the working tree;
  * the live sweep over the suite is run beside the identical routine over a
    directory holding a planted control, which it reports;
  * each repaired control is held to being decided by the reading it vouches
    for, read off its own source beside the same reading over a source in which
    the control is decided by its own plant, which that reading reports.

The sources planted here are written for this file and are spelled differently
from the ones planted beside the scan itself, so a scan narrowed to the shapes
its author wrote goes red here.

Every claim about what the scan reports is made by calling the scan — asserted
below over this file's own call graph, because "a property of the standard
library stood in for the check" is the defect the story exists to catch and
this file would be the last place it should appear. This repository's history is
reached only through the shared readers in `tests/conftest.py`, at a revision
found by searching for text; no commit sha is written here, and nothing
recovered is written to a path or executed.
"""
import ast
import re
from pathlib import Path

import pytest

import conftest
from conftest import BASELINE, repository_file_at, revision_carrying
from test_baseline_honesty import Flag, planted_assertions

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
THIS_FILE = Path(__file__)

#: The module the story delivers the rule into, and the rule's name, written
#: here rather than read off anything — a rule that moved house fails this file.
THE_RULES_MODULE = "tests/test_baseline_honesty.py"
THE_NEW_SCAN = "planted_assertions"

#: The line the rule's own comment block is headed by. Written once here
#: because three readings below find the block by it: a banner naming the
#: rule's subject rather than its position in a list, so that adding a rule
#: above it moves nothing.
THE_RULES_BANNER = "A control that plants a phrase and then finds it"

#: The sentence in the rules module's docstring that states what its rules
#: cover. Named here for the same reason, and phrased without a count of the
#: rules: a count in prose and the rules the file defines drift apart, and this
#: module asserts against the words the docstring actually carries.
THE_COVERAGE_SENTENCE = "one mechanical question about one shape"

#: The rules it joins, named independently of the implementation's own list.
THE_RULES_IT_JOINS = ("flagged_calls", "module_construction", "git_text_reads",
                      "mutation_controls")

#: The sound control of exactly the written shape the rule reports: it strips a
#: paragraph out of a copy of a module's source, asserts the paragraph is gone,
#: and then puts the stripped source back through the real check. That last
#: call is the whole of what makes it sound, so it is the module the
#: distinguishing clause is demonstrated against.
THE_SOUND_CONTROL = "tests/test_mutation_controls.py"

#: The controls story-203 repaired, and a line one of them carried before that
#: repair. The line is what the revision is *searched for*: no sha is written
#: here, because a sha survives neither a rebase nor a squash merge and a
#: rebased-away sha is unreachable in a clean clone even while it still resolves
#: in the working repository.
THE_REPAIRED_CONTROLS = (
    "tests/test_a_merge_closes_the_brief.py",
    "tests/test_a_question_to_the_developer_is_about_consequences.py",
    "tests/test_the_assist_agent_files_a_brief.py",
)
A_LINE_ONE_OF_THEM_CARRIED = "planted = SCRIPT_SOURCE +"


def the_rules_module() -> str:
    return (REPO_ROOT / THE_RULES_MODULE).read_text(encoding="utf-8")


def the_rules_module_at_this_storys_baseline() -> str:
    """The rules module as it stood before this story, through the shared
    resolution rather than through a second one written here."""
    return repository_file_at(THE_RULES_MODULE, validation_file=THIS_FILE,
                              bound=BASELINE)


def the_comment_block_above_the_rule() -> str:
    """The prose stated above the new rule, flowed into one line.

    Read from the banner opening this rule rather than from the top of the
    module, so what it finds is this rule's own block and not a sentence some
    rule above it happens to carry — and flowed, because a phrase a comment
    wraps across two lines is one phrase to a reader and two to `in`.
    """
    source = the_rules_module()
    stated = source[:source.index(f"def {THE_NEW_SCAN}(")]
    block = stated[stated.rindex(THE_RULES_BANNER):]
    return " ".join(line.lstrip("# ").strip() for line in block.splitlines())


def flags_for(source: str, module: str = "test_a_probe.py") -> list[Flag]:
    """The scan, called. Every claim in this file about what it reports goes
    through here or through the scan directly."""
    return planted_assertions(source, module)


def scan_modules(paths: list[Path]) -> list[Flag]:
    """One routine for every sweep, so the green sweep over the suite and the
    red sweep over a planted directory are the same reading."""
    return [flag for path in sorted(paths)
            for flag in planted_assertions(path.read_text(encoding="utf-8"),
                                           path.name)]


# --------------------------------------------------------------------------
# The sources planted here
#
# One control, written several ways, and each benign source below is that same
# control with exactly one of its required parts changed. Stating them as near
# neighbours is what makes the benign assertions worth anything: a scan
# reporting nothing because it has stopped looking fails the positive half of
# every pair.
# --------------------------------------------------------------------------


#: A phrase added to a value, and that phrase found in it.
ADDED_AND_FOUND = (
    "def a_control(shipped):\n"
    "    doctored = shipped + THE_RETIRED_LINE\n"
    "    assert THE_RETIRED_LINE in doctored\n"
)

#: The needle interpolated into the added part rather than concatenated whole,
#: which is how a control that plants a key and a value usually reads.
INTERPOLATED_AND_FOUND = (
    "def a_control(key, value):\n"
    "    doctored = TEMPLATE + f'  {key}: {value}\\n'\n"
    "    assert key in doctored\n"
)

#: The needle reached through a subscript on both sides, which is how a
#: parametrized control over a table of files usually reads.
SUBSCRIPTED_AND_FOUND = (
    "def a_control(relative):\n"
    "    doctored = SOURCES[relative] + RETIRED[relative]\n"
    "    assert RETIRED[relative] in doctored\n"
)

#: A phrase taken out of a value, and that phrase not found in it.
CUT_AND_NOT_FOUND = (
    "def a_control(prose):\n"
    "    cut = prose.replace(THE_RULE, '')\n"
    "    assert THE_RULE not in cut\n"
)

#: The same removal bounded to one occurrence, which is the spelling a control
#: over a document stating a rule once reaches for.
CUT_ONCE_AND_NOT_FOUND = (
    "def a_control(prose):\n"
    "    cut = prose.replace(THE_RULE, '', 1)\n"
    "    assert THE_RULE not in cut\n"
)

PLANTED_CONTROLS = {
    "a-phrase-added-and-found": ADDED_AND_FOUND,
    "a-needle-interpolated-into-the-added-part": INTERPOLATED_AND_FOUND,
    "a-needle-reached-through-a-subscript": SUBSCRIPTED_AND_FOUND,
    "a-phrase-cut-and-not-found": CUT_AND_NOT_FOUND,
    "a-phrase-cut-once-and-not-found": CUT_ONCE_AND_NOT_FOUND,
}

#: Each benign source beside the planted control it is one change away from.
#: The change is named in the key, because that is the whole content of the
#: claim: the pairing is what gets reported, and altering any part of it must
#: silence the scan.
BENIGN_BESIDE_ITS_CONTROL = {
    "the-operator-the-plant-did-not-decide": (
        ADDED_AND_FOUND,
        "def a_control(shipped):\n"
        "    doctored = shipped + THE_RETIRED_LINE\n"
        "    assert THE_RETIRED_LINE not in shipped\n",
    ),
    "a-phrase-the-plant-never-carried": (
        ADDED_AND_FOUND,
        "def a_control(shipped):\n"
        "    doctored = shipped + THE_RETIRED_LINE\n"
        "    assert SOMETHING_ELSE in doctored\n",
    ),
    "a-removal-asked-what-survived-it": (
        CUT_AND_NOT_FOUND,
        "def a_control(prose):\n"
        "    cut = prose.replace(THE_RULE, '')\n"
        "    assert THE_SURVIVOR in cut\n",
    ),
    "a-comparison-that-is-not-a-membership": (
        CUT_AND_NOT_FOUND,
        "def a_control(prose):\n"
        "    cut = prose.replace(THE_RULE, '')\n"
        "    assert len(cut) < len(prose)\n",
    ),
    "a-plant-bound-to-no-name-at-all": (
        ADDED_AND_FOUND,
        "def a_control(shipped):\n"
        "    assert THE_RETIRED_LINE in shipped + THE_RETIRED_LINE\n",
    ),
    "a-plant-and-an-assertion-in-different-functions": (
        CUT_AND_NOT_FOUND,
        "def cut_it(prose):\n"
        "    return prose.replace(THE_RULE, '')\n"
        "def a_control(prose):\n"
        "    assert THE_RULE not in cut_it(prose)\n",
    ),
    "an-assertion-about-the-interpreter-over-a-file-the-test-wrote": (
        CUT_AND_NOT_FOUND,
        "def a_control(tmp_path):\n"
        "    written = tmp_path / 'empty.txt'\n"
        "    written.write_text('')\n"
        "    with pytest.raises(ValueError):\n"
        "        int(written.read_text())\n",
    ),
}


# --------------------------------------------------------------------------
# The scan, held to the shape the story says it delivers
# --------------------------------------------------------------------------


def test_the_scan_takes_a_source_and_a_module_and_returns_flag_records():
    """The delivered shape: the same `(source, module) -> list[Flag]` the scans
    beside it have, with the module and the line read off the record rather
    than out of a message."""
    defined = {node.name: node for node in ast.parse(the_rules_module()).body
               if isinstance(node, ast.FunctionDef)}
    assert THE_NEW_SCAN in defined, "the story's scan is not defined here"
    assert [argument.arg for argument in defined[THE_NEW_SCAN].args.args] \
        == ["source", "module"]
    for joined in THE_RULES_IT_JOINS:
        assert [argument.arg for argument in defined[joined].args.args][:2] \
            == ["source", "module"], joined

    flags = flags_for(CUT_AND_NOT_FOUND, "some_module.py")
    assert len(flags) == 1, flags
    flag = flags[0]
    assert isinstance(flag, Flag)
    assert flag.module == "some_module.py"
    assert CUT_AND_NOT_FOUND.splitlines()[flag.line - 1].strip() \
        .startswith("assert THE_RULE not in cut")
    assert "a_control" in flag.reason and flag.reason.strip()


@pytest.mark.parametrize("planted", list(PLANTED_CONTROLS),
                         ids=list(PLANTED_CONTROLS))
def test_each_form_of_the_plant_is_reported(planted):
    """Both forms the story names, each written more than one way: a phrase
    added and found, and a phrase cut and not found. Demonstrated by calling
    the scan, since a scan with no reported source is indistinguishable from
    one that has stopped looking."""
    flags = flags_for(PLANTED_CONTROLS[planted])
    assert len(flags) == 1, flags


@pytest.mark.parametrize("case", list(BENIGN_BESIDE_ITS_CONTROL),
                         ids=list(BENIGN_BESIDE_ITS_CONTROL))
def test_each_benign_source_is_unreported_and_its_near_neighbour_is_not(case):
    """The control for every benign assertion in this section.

    Each pair differs in one respect — the operator, the needle, whether the
    plant is bound to a name, whether the pairing is inside one function — so
    "the scan says nothing" is shown to be a statement about that difference
    rather than about a scan that reads nothing.
    """
    reported, unreported = BENIGN_BESIDE_ITS_CONTROL[case]
    assert flags_for(reported), "the near-neighbour control was not reported"
    assert flags_for(unreported) == []


def test_the_interpreter_shape_is_outside_the_rule_and_the_prose_says_so():
    """The limit the story states about itself, held to what the scan does
    rather than only to what the comment block claims.

    An assertion about the standard library over a file the test itself wrote
    is the same defect by a different route, and is deliberately outside this
    rule. A limit nobody exercises is a claim about the prose; a limit stated
    nowhere is indistinguishable from a limit nobody noticed, so both halves
    are asserted here.
    """
    reported, outside = BENIGN_BESIDE_ITS_CONTROL[
        "an-assertion-about-the-interpreter-over-a-file-the-test-wrote"]
    assert flags_for(outside) == []
    assert flags_for(reported), "the plant-shaped sibling was not reported"

    stated = the_comment_block_above_the_rule()
    limits = stated[stated.rindex("What it does not cover"):]
    assert "the interpreter or the standard library" in limits
    assert "does not come out cleanly" in limits


# --------------------------------------------------------------------------
# The clause that separates a sound control from a vacuous one
#
# It is the only part of the rule that decides anything about a *control* as
# opposed to about string arithmetic, so it is not asserted by one source. It
# is shown as a pair over sources composed here, shown to turn on whether the
# call is to something this repository wrote rather than to something the
# standard library provides, and then shown against a module of this
# repository's own in the section below.
# --------------------------------------------------------------------------


#: The sound shape: the planted value put back through the reading the control
#: vouches for, which is a function the module defines.
A_READING_THIS_MODULE_DEFINES = (
    "def the_reading(text):\n"
    "    return [line for line in text.splitlines() if THE_RULE in line]\n"
    "def a_control(prose):\n"
    "    cut = prose.replace(THE_RULE, '')\n"
    "    assert THE_RULE not in cut\n"
    "    assert the_reading(cut) == []\n"
)


def test_a_plant_that_goes_back_through_the_reading_it_vouches_for_is_left():
    """The clause, as the pair it has to be: the same plant and the same
    assertion, differing only in whether the planted value is put back through
    the reading the control vouches for. With the call there the scan reports
    nothing; with it removed the scan reports — so the silence is that clause
    deciding rather than the scan overlooking a shape it was written for."""
    assert flags_for(A_READING_THIS_MODULE_DEFINES) == []

    without_the_reading = A_READING_THIS_MODULE_DEFINES.replace(
        "    assert the_reading(cut) == []\n", "")
    assert len(flags_for(without_the_reading)) == 1


def test_the_planted_value_reaches_the_reading_however_it_is_handed_over():
    """The same clause however the planted value is passed, because a control
    written with a keyword argument, or with the needle first, is the same
    control. Each is unreported, and each is shown beside the source with the
    call removed, which is reported."""
    for handed in ("the_reading(text=cut)", "the_reading(THE_RULE, cut)"):
        sound = (
            "def the_reading(text):\n"
            "    return [line for line in text.splitlines() if THE_RULE in line]\n"
            "def a_control(prose):\n"
            "    cut = prose.replace(THE_RULE, '')\n"
            "    assert THE_RULE not in cut\n"
            f"    assert {handed} == []\n"
        )
        assert flags_for(sound) == [], handed
        assert flags_for(sound.replace(f"    assert {handed} == []\n", "")), \
            handed


def test_the_clause_turns_on_a_call_this_repository_wrote_not_on_any_call():
    """Where the boundary of "a real check" is, stated as a pair that differs
    only in which module the call is reached through.

    A call into the standard library is not the check a control vouches for —
    a search re-run over the cut text says what the removal said — so the pair
    is still reported. A call into something this repository imports is, so it
    is not. Without both halves, a clause satisfied by any call at all and a
    clause satisfied by none would look the same from here.
    """
    into_the_standard_library = (
        "import re\n"
        "def a_control(prose):\n"
        "    cut = prose.replace(THE_RULE, '')\n"
        "    assert THE_RULE not in cut\n"
        "    assert re.search(THE_RULE, cut) is None\n"
    )
    assert len(flags_for(into_the_standard_library)) == 1

    into_this_repository = into_the_standard_library.replace(
        "import re\n", "import story_coordinator\n").replace(
        "    assert re.search(THE_RULE, cut) is None\n",
        "    assert story_coordinator.load_state(cut) is None\n")
    assert flags_for(into_this_repository) == []


# --------------------------------------------------------------------------
# The same clause, shown against a module of this repository's
#
# A clause demonstrated only against sources its own validator composed is a
# clause shown to work on the shapes that validator thought of. The suite
# already holds one control of exactly the reported written shape that is
# sound, and it is the sharpest available case: if the rule reported it, the
# rule would be wrong about the suite it ships into.
# --------------------------------------------------------------------------


def the_sound_control() -> str:
    return (REPO_ROOT / THE_SOUND_CONTROL).read_text(encoding="utf-8")


def without_the_call_to_the_real_check(source: str) -> str:
    """The same module with the call that puts the stripped source back through
    the real check taken out, and nothing else about it changed.

    Derived from the source rather than quoted from it, so this reading does
    not go quietly stale when that control is reworded: the `with` statement
    whose body hands the stripped value to the check is removed whole.
    """
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        handed = {inner.id for call in ast.walk(node)
                  if isinstance(call, ast.Call)
                  for inner in call.args if isinstance(inner, ast.Name)}
        if "stripped" in handed:
            return "".join(lines[:node.lineno - 1] + lines[node.end_lineno:])
    raise AssertionError(
        f"{THE_SOUND_CONTROL} no longer hands a stripped source to a check "
        f"inside a with statement; this reading has gone stale")


def test_the_sound_control_of_the_same_written_shape_is_left_alone():
    """Unreported as it stands, and reported once the call that puts the
    stripped source back through the real check is taken out — so the silence
    is that call and not the scan failing to see this module at all."""
    text = the_sound_control()
    name = Path(THE_SOUND_CONTROL).name
    assert planted_assertions(text, name) == []

    cut = without_the_call_to_the_real_check(text)
    ast.parse(cut)
    assert cut != text
    flags = planted_assertions(cut, name)
    assert len(flags) == 1, flags


def test_the_removal_above_takes_out_the_call_and_nothing_else():
    """What makes the pair above a pair: the two texts differ in the removed
    statement only, so the report is that statement rather than some other
    edit this reading happened to make."""
    text = the_sound_control()
    cut = without_the_call_to_the_real_check(text)
    removed = [line for line in text.splitlines()
               if line not in cut.splitlines()]
    assert removed, "nothing was removed"
    assert any("assert_states_its_limits" in line for line in removed)
    assert not any(line.strip().startswith("def ") for line in removed)


# --------------------------------------------------------------------------
# The evidence the rule catches the defect it was written for
#
# Everything above is a shape someone chose. The controls story-203 repaired
# are the defect as it actually shipped, written by different authors for
# different subjects, and they are the only evidence that this rule would have
# caught what it exists for. They are recovered at the revision before that
# repair, found by searching the history for a line one of them carried.
# --------------------------------------------------------------------------


def before_story_203_repaired_them() -> str:
    """The newest revision at which one of the repaired controls still carried
    its plant, which is the revision before the repair.

    A search rather than a sha: a sha survives neither a rebase nor a squash
    merge, and a rebased-away sha is unreachable in a clean clone even while it
    still resolves in the working repository.
    """
    return revision_carrying(THE_REPAIRED_CONTROLS[0], A_LINE_ONE_OF_THEM_CARRIED)


@pytest.mark.parametrize("relative", THE_REPAIRED_CONTROLS)
def test_the_recovered_text_really_is_the_text_before_the_repair(relative):
    """Everything below leans on this recovery, so it is asserted rather than
    assumed: recovered, parseable, and different from the file in the working
    tree. A recovery that had silently resolved to today's text would make the
    reports below statements about nothing."""
    recovered = repository_file_at(relative,
                                  revision=before_story_203_repaired_them())
    ast.parse(recovered)
    assert recovered != (REPO_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", THE_REPAIRED_CONTROLS)
def test_the_scan_reports_each_control_before_its_repair_and_not_after(relative):
    """The pair it has to be: reported before the repair, reported by nothing
    in the working tree. Both halves by calling the scan, over the same module
    name, so the difference is the text and nothing else."""
    name = Path(relative).name
    recovered = repository_file_at(relative,
                                  revision=before_story_203_repaired_them())
    assert planted_assertions(recovered, name), name
    assert planted_assertions(
        (REPO_ROOT / relative).read_text(encoding="utf-8"), name) == []


def test_the_revision_is_the_one_before_the_repair_for_every_one_of_them():
    """One revision answers for all of them, which is what makes the search
    sound: each carried its plant there, and each is repaired today. Asserted
    as a whole rather than module by module, because a revision that happened
    to predate only the first would still pass every row above."""
    revision = before_story_203_repaired_them()
    reported = {
        Path(relative).name: bool(planted_assertions(
            repository_file_at(relative, revision=revision),
            Path(relative).name))
        for relative in THE_REPAIRED_CONTROLS
    }
    assert set(reported.values()) == {True}, reported


def hex_revisions(source: str) -> set[str]:
    """Every string constant in a source that reads as a commit sha."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(re.findall(r"\b[0-9a-f]{7,40}\b", node.value))
    return found


#: A sha-shaped string, written here for one purpose: to be found by the
#: reading below, so that the reading is shown capable of finding one. Nothing
#: resolves it, which is asserted rather than stated.
A_SHA_NOTHING_HERE_RESOLVES = "0bbdbac85c7b9"


def test_the_reading_for_a_pinned_sha_finds_one_when_it_is_there():
    """The control for the assertions below."""
    planted = (f"REVISION = '{A_SHA_NOTHING_HERE_RESOLVES}'\n"
               f"def before():\n"
               f"    return repository_file_at(REL, revision=REVISION)\n")
    assert hex_revisions(planted) == {A_SHA_NOTHING_HERE_RESOLVES}


def test_no_commit_sha_is_written_into_the_module_holding_the_rule():
    """The constraint, read off the source the story delivers: the revision is
    searched for, so neither a rebase nor a squash merge moves it."""
    assert hex_revisions(the_rules_module()) == set()


def test_the_only_sha_here_is_the_one_the_reading_above_is_shown_finding():
    """The same constraint for this file, which cannot assert the empty set: a
    control demonstrating that the reading finds a sha has to contain one. So
    the claim is narrowed to what it actually is — that sha is the only one
    here, and no call in this file resolves a revision from a literal."""
    source = THIS_FILE.read_text(encoding="utf-8")
    assert hex_revisions(source) == {A_SHA_NOTHING_HERE_RESOLVES}

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "revision":
                assert not isinstance(keyword.value, ast.Constant), \
                    ast.unparse(node)


def calls_reaching_git(source: str) -> list[str]:
    """Every call in a source that asks git for something without going through
    the shared readers: a spawn, or a subprocess helper given a git argv."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for argument in ast.walk(node):
            if isinstance(argument, ast.Constant) and argument.value == "git":
                found.append(ast.unparse(node.func))
    return found


def test_the_reading_for_a_git_spawn_finds_one_when_it_is_there():
    """The control for the assertion below: the same reading over a source that
    does ask git directly reports it."""
    planted = ("import subprocess\n"
               "def before(rel, at):\n"
               "    return subprocess.check_output(['git', 'show', at + rel])\n")
    assert calls_reaching_git(planted) == ["subprocess.check_output"]


def test_this_module_reaches_the_history_only_through_the_shared_readers():
    """The constraint the story states, read off this file's own source: no git
    spawn here, and every historical read is one of the shared resolvers."""
    source = THIS_FILE.read_text(encoding="utf-8")
    assert calls_reaching_git(source) == []

    reached = {ast.unparse(node.func) for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Call)
               and ast.unparse(node.func).split(".")[-1]
               in ("repository_file_at", "revision_carrying",
                   "story_commit_range", "story_diff", "function_source_at")}
    assert reached == {"repository_file_at", "revision_carrying"}
    for name in reached:
        assert getattr(conftest, name), name


# --------------------------------------------------------------------------
# The rule run over the suite, beside a directory it reports
# --------------------------------------------------------------------------


def test_every_module_under_tests_is_unreported():
    """The rule's live claim, run here independently of the module that defines
    it, over modules the glob finds rather than any this file names."""
    modules = list(TESTS_DIR.glob("*.py"))
    assert THIS_FILE in modules
    flags = scan_modules(modules)
    assert flags == [], "\n".join(str(flag) for flag in flags)


def test_the_same_sweep_reports_a_directory_that_carries_a_planted_control(
    tmp_path,
):
    """The control for the sweep above: the identical routine over a directory
    holding one planted control reports it and leaves the benign module beside
    it alone, so the green sweep is a statement about the suite rather than
    about a sweep that reads nothing."""
    for name, source in (("conftest.py", "def helper():\n    return 1\n"),
                         ("test_a_sound_one.py", A_READING_THIS_MODULE_DEFINES),
                         ("test_a_vacuous_one.py", ADDED_AND_FOUND)):
        (tmp_path / name).write_text(source, encoding="utf-8")
    flags = scan_modules(list(tmp_path.glob("*.py")))
    assert [flag.module for flag in flags] == ["test_a_vacuous_one.py"]


def test_the_sweep_over_an_empty_directory_reports_nothing_either(tmp_path):
    """Why the sweep above needs its companion below: over a directory with no
    modules in it, the identical routine is green. Emptiness and compliance are
    the same answer from the sweep alone."""
    assert scan_modules(list(tmp_path.glob("*.py"))) == []


def delivered_tests(source: str) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")}


def calls_named(function: ast.AST) -> set[str]:
    return {node.func.id for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def test_the_delivered_live_assertion_globs_and_compares_against_nothing():
    """The delivered assertion read off its own source, because how it finds
    its modules is the part that cannot be seen from its result: a list of
    names would pass over a module that had just landed."""
    source = the_rules_module()
    tests = delivered_tests(source)

    live = [name for name, node in tests.items()
            if THE_NEW_SCAN in calls_named(node)
            and calls_named(node) & {"all_modules", "scanned_modules"}]
    assert len(live) == 1, live
    node = tests[live[0]]

    discovery = calls_named(node) & {"all_modules", "scanned_modules"}
    glob = conftest.function_source(source, discovery.pop())
    assert ".glob(" in glob and ".py" in glob

    empty = [compare for compare in ast.walk(node)
             if isinstance(compare, ast.Compare)
             and isinstance(compare.ops[0], ast.Eq)
             and isinstance(compare.comparators[0], ast.List)
             and not compare.comparators[0].elts]
    assert empty, "the live assertion does not compare against an empty list"

    named = {constant.value for constant in ast.walk(node)
             if isinstance(constant, ast.Constant)
             and isinstance(constant.value, str)}
    assert not [value for value in named if value.endswith(".py")], named


def test_the_delivered_companion_establishes_the_glob_found_modules():
    """The companion the glob needs, read off the delivered source: a sweep
    over zero files reports nothing, which is what the live assertion above
    asserts of a compliant suite — as this file's own empty-directory sweep
    shows."""
    source = the_rules_module()
    companions = [
        name for name, node in delivered_tests(source).items()
        if THE_NEW_SCAN not in calls_named(node)
        and "all_modules" in calls_named(node)
        and any(isinstance(compare.ops[0], (ast.Gt, ast.GtE))
                and "len" in calls_named(compare)
                for compare in ast.walk(node)
                if isinstance(compare, ast.Compare))
    ]
    assert companions, "no companion establishes that the glob found modules"


def register_constants(source: str) -> set[str]:
    """Every module-level constant naming modules a rule excuses or tolerates."""
    return {node.targets[0].id
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (node.targets[0].id.endswith("EXEMPT_MODULES")
                 or "KNOWN" in node.targets[0].id)}


def test_the_reading_of_a_register_finds_one_when_it_is_there():
    """The control for the assertion below: the same reading over a source that
    does declare a register for this rule finds it."""
    planted = (f"KNOWN_PLANTED_ASSERTIONS = ('conftest.py',)\n"
               f"def {THE_NEW_SCAN}(source, module):\n"
               f"    return []\n")
    assert "KNOWN_PLANTED_ASSERTIONS" in register_constants(planted)


def test_the_new_rule_names_no_register_of_known_violations():
    """The absence, read three ways: no register exists for this rule, the
    scan's body tests no module name against one, and a planted control is
    reported whatever module it is said to belong to — including the names the
    rules above excuse."""
    source = the_rules_module()
    assert not [name for name in register_constants(source)
                if "PLANT" in name or "CONTROL" in name]

    scan = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef)
                and node.name == THE_NEW_SCAN)
    mentioned = {inner.id for inner in ast.walk(scan) if isinstance(inner, ast.Name)}
    assert not [name for name in mentioned if name.endswith("EXEMPT_MODULES")
                or "KNOWN" in name]

    for module in ("conftest.py", Path(THE_SOUND_CONTROL).name,
                   Path(THE_RULES_MODULE).name, THIS_FILE.name):
        assert len(flags_for(ADDED_AND_FOUND, module)) == 1, module


# --------------------------------------------------------------------------
# The three repaired controls, each decided by the check it vouches for
#
# "The scan reports no site here" is the weak half of what the story claims
# about these: a control deleted outright would satisfy it. The claim is that
# each still demonstrates what it was written to demonstrate and is now decided
# by the reading it protects, so each one's own source is read for that.
# --------------------------------------------------------------------------


#: Each repaired module, with the control the story repaired in it and the
#: readings that control must now be decided by. The readings are the same
#: functions the assertions they vouch for are decided by, which is the whole
#: point of the repair: one reading, two callers.
THE_REPAIRS = {
    "tests/test_opportunistic_sweep.py": (
        "test_the_search_for_the_retired_sentence_can_find_it",
        ("shipped_text",),
    ),
    "tests/test_a_run_stands_on_its_branch_first.py": (
        "test_the_comparison_names_the_branch_and_no_longer_names_head",
        ("guard_source",),
    ),
    "tests/test_turn_ending_instructions.py": (
        "test_the_placement_check_reports_a_rendering_without_the_sentence",
        ("carries_the_sentence", "states_it_after_the_run_directory",
         "states_it_exactly_once"),
    ),
}


def _is_a_reading_call(node: ast.AST, readings: tuple[str, ...]) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in readings)


def _decided_by(test: ast.AST, readings: tuple[str, ...],
                read_names: set[str]) -> bool:
    """Whether one assertion's answer comes from a named reading.

    Either the assertion *is* the reading's answer — the call itself, negated,
    or one side of a comparison — or it compares a name this function bound to
    the reading's answer. A reading buried inside a larger expression does not
    count: `planted = the_reading(x) + NEEDLE` is the plant shape, and the name
    it binds holds what the test built rather than what the reading said.
    """
    if _is_a_reading_call(test, readings):
        return True
    if isinstance(test, ast.UnaryOp):
        return _decided_by(test.operand, readings, read_names)
    if isinstance(test, ast.BoolOp):
        return any(_decided_by(value, readings, read_names)
                   for value in test.values)
    if isinstance(test, ast.Compare):
        if _is_a_reading_call(test.left, readings) \
                or any(_is_a_reading_call(comparator, readings)
                       for comparator in test.comparators):
            return True
    return bool({name.id for name in ast.walk(test)
                 if isinstance(name, ast.Name)} & read_names)


def asserts_not_reaching(source: str, function: str,
                         readings: tuple[str, ...]) -> list[int]:
    """The lines of every assertion in one function that no named reading
    decides.

    Anything this reports is an assertion decided by whatever the test itself
    did to the text rather than by the check it claims to be demonstrating,
    which is the defect this story exists to catch.
    """
    tree = ast.parse(source)
    node = next(inner for inner in ast.walk(tree)
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                and inner.name == function)

    read_names = {inner.targets[0].id for inner in ast.walk(node)
                  if isinstance(inner, ast.Assign) and len(inner.targets) == 1
                  and isinstance(inner.targets[0], ast.Name)
                  and _is_a_reading_call(inner.value, readings)}

    return [inner.lineno for inner in ast.walk(node)
            if isinstance(inner, ast.Assert)
            and not _decided_by(inner.test, readings, read_names)]


def test_the_reading_above_finds_an_assertion_decided_by_its_own_plant():
    """The control for the three assertions below: the same reading, over a
    control decided by the text it concatenated rather than by the reading it
    protects, reports that assertion."""
    decided_by_the_plant = (
        "def shipped_text(path):\n"
        "    return path.read_text()\n"
        "def a_control(relative):\n"
        "    planted = shipped_text(relative) + RETIRED\n"
        "    assert RETIRED in planted\n"
    )
    assert asserts_not_reaching(decided_by_the_plant, "a_control",
                                ("shipped_text",)) == [5]

    decided_by_the_reading = decided_by_the_plant.replace(
        "    assert RETIRED in planted\n",
        "    assert RETIRED in shipped_text(planted)\n")
    assert asserts_not_reaching(decided_by_the_reading, "a_control",
                                ("shipped_text",)) == []


@pytest.mark.parametrize("relative", list(THE_REPAIRS), ids=list(THE_REPAIRS))
def test_each_repaired_control_is_decided_by_the_reading_it_protects(relative):
    """Every assertion in each repaired control reaches the reading the
    assertion it vouches for is decided by, so what the control demonstrates is
    that reading reporting rather than the plant it performed."""
    control, readings = THE_REPAIRS[relative]
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    defined = {node.name for node in ast.parse(source).body
               if isinstance(node, ast.FunctionDef)}
    for reading in readings:
        assert reading in defined, (relative, reading)
    assert asserts_not_reaching(source, control, readings) == [], relative


@pytest.mark.parametrize("relative", list(THE_REPAIRS), ids=list(THE_REPAIRS))
def test_each_repaired_control_still_asserts_what_a_check_decides(relative):
    """The claim the absence above cannot make: the control still asserts
    something, and what it asserts is the reading's answer over the broken
    subject — so the repair did not turn a control into a no-op."""
    control, readings = THE_REPAIRS[relative]
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    text = conftest.function_source(source, control)
    node = ast.parse(text.lstrip()) if not text.startswith("@") else ast.parse(
        "".join(line for line in text.splitlines(keepends=True)
                if not line.startswith("@")))
    body = next(inner for inner in ast.walk(node)
                if isinstance(inner, ast.FunctionDef) and inner.name == control)
    assertions = [inner for inner in ast.walk(body)
                  if isinstance(inner, ast.Assert)]
    assert assertions, relative
    for reading in readings:
        assert reading in text, (relative, reading)


@pytest.mark.parametrize("relative", list(THE_REPAIRS), ids=list(THE_REPAIRS))
def test_the_scan_reports_no_site_in_the_repaired_modules(relative):
    """The story's own claim about these files, by calling the scan. Its
    control is every report above: the same scan reports the planted sources,
    the sound control with its check removed, and each of these files' cousins
    at the revision before story-203 repaired them."""
    name = Path(relative).name
    source = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert planted_assertions(source, name) == [], name


# --------------------------------------------------------------------------
# What the module that holds the rule says about it
# --------------------------------------------------------------------------


def test_the_comment_block_above_the_rule_states_why_it_is_its_own_rule():
    """The story asks for the reason in prose beside the rule rather than only
    for a separate function: what it is about, and why it is not an extension
    of one above it."""
    stated = the_comment_block_above_the_rule()
    assert "control" in stated
    assert "`assert` compares" in stated
    assert "not tamper-proof" in stated


def test_the_reading_of_that_block_is_confined_to_this_rules_own_block():
    """The control for the reading above, which is the part that could quietly
    stop meaning anything: the same flowed reading over the module up to the
    *first* rule's scan finds a different block, so the slice is answering about
    the rule it was pointed at rather than about the whole file."""
    source = the_rules_module()
    first = source[:source.index("def flagged_calls(")]
    assert THE_RULES_BANNER not in first
    assert THE_RULES_BANNER in the_comment_block_above_the_rule()


def test_no_scan_in_that_module_draws_its_enforcement_from_another():
    """The constraint: its own rule. Read off the module's source rather than
    asserted about it, because "these are separate" is the kind of claim that
    stays written down after it has stopped being true."""
    source = the_rules_module()
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)}
    scans = THE_RULES_IT_JOINS + (THE_NEW_SCAN,)
    for name in scans:
        assert name in functions, name
        assert calls_named(functions[name]) & set(scans) - {name} == set(), name


def test_the_module_docstring_accounts_for_the_new_rule_and_keeps_disclaiming():
    """The docstring is what a reader meets first, so it says what the rules
    between them cover and goes on saying what no scan here decides."""
    docstring = ast.get_docstring(ast.parse(the_rules_module()))
    assert "control" in docstring
    assert "general class of vacuous assertions" in docstring
    assert "prompts/story-tester.md" in docstring
    assert THE_COVERAGE_SENTENCE in docstring


def test_the_reading_of_the_docstring_goes_red_when_the_paragraph_is_cut():
    """The control for the assertion above: over the same module with the
    paragraph stating its coverage removed, the same reading no longer finds
    what it found, so the assertion is about that paragraph rather than about
    words the docstring carries anywhere."""
    source = the_rules_module()
    docstring = ast.get_docstring(ast.parse(source))
    paragraph = next(part for part in docstring.split("\n\n")
                     if THE_COVERAGE_SENTENCE in part)
    cut = source.replace(paragraph, "")
    assert THE_COVERAGE_SENTENCE not in ast.get_docstring(ast.parse(cut))


# --------------------------------------------------------------------------
# The rules it joined, unchanged in reach
# --------------------------------------------------------------------------


#: What the story says it does not touch: the scans it joins and the helpers
#: their matching is built out of.
UNCHANGED = THE_RULES_IT_JOINS + (
    "undeclared_targets", "history_reads", "live_artifact_reads",
    "_is_subprocess_call", "_git_argument_list", "_dishonest_baseline",
    "_asks_for_a_files_text", "_git_words", "_pinned_names",
)


@pytest.mark.parametrize("name", UNCHANGED)
def test_no_existing_scan_was_widened_by_this_story(name):
    """A new rule, not wider old ones. Each existing scan's source before
    this story is required to equal its source today, which is a stronger
    reading than "the suite still passes"."""
    before = conftest.function_source(
        the_rules_module_at_this_storys_baseline(), name)
    after = conftest.function_source(the_rules_module(), name)
    assert before == after, name


def test_the_comparison_above_can_tell_two_sources_apart():
    """Its control: the same comparison over what this story did add reports a
    difference, so an equal-source assertion that could never differ is not
    what the parametrization above is doing."""
    before = the_rules_module_at_this_storys_baseline()
    assert f"def {THE_NEW_SCAN}(" not in before
    assert f"def {THE_NEW_SCAN}(" in the_rules_module()
    with pytest.raises(AssertionError):
        conftest.function_source(before, THE_NEW_SCAN)


@pytest.mark.parametrize("constant", ["EXEMPT_MODULES", "CONSTRUCTION_EXEMPT_MODULES",
                                      "READER_EXEMPT_MODULES", "SPAWNING_CALLS",
                                      "MODULE_CONSTRUCTORS", "SOURCE_EXECUTORS",
                                      "CONTENT_SUBCOMMANDS", "HISTORY_RESOLVERS"])
def test_no_existing_rules_vocabulary_was_changed(constant):
    """The other half of "unchanged in reach": the word lists the scans beside
    this one match on are what they were before this story."""
    assert assignment(the_rules_module_at_this_storys_baseline(), constant) \
        == assignment(the_rules_module(), constant)


def assignment(source: str, name: str) -> str:
    """One module-level constant's value, as a dumped tree."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            return ast.dump(node.value)
    raise AssertionError(f"{name} is not assigned at this module's top level")


# --------------------------------------------------------------------------
# This file, held to the rule it validates
#
# The defect the story exists to catch is an assertion decided by a property of
# the standard library rather than by the check it claims to be about. A
# validation module for that story asserting it in a way that could not fail
# would be the joke telling itself, so the claim is read off this file's own
# call graph rather than trusted.
# --------------------------------------------------------------------------


#: The words a test's name here uses when it claims something about what the
#: scan does with a source. Every such test has to call the scan; the tests
#: named for a *reading* defined in this file say `finds` instead, so the two
#: classes are told apart by their names rather than by a list of exceptions.
CLAIMS_ABOUT_THE_SCAN = ("report", "unreported", "is_left", "left_alone")


def functions_claiming_a_report(source: str) -> dict[str, ast.FunctionDef]:
    """Every test here whose name claims something about what the scan does."""
    return {name: node for name, node in delivered_tests(source).items()
            if any(word in name for word in CLAIMS_ABOUT_THE_SCAN)}


def reaches(source: str, name: str, target: str) -> bool:
    """Whether one function reaches a target through this module's own calls."""
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    seen, frontier = set(), [name]
    while frontier:
        current = frontier.pop()
        if current in seen or current not in functions:
            continue
        seen.add(current)
        called = calls_named(functions[current])
        if target in called:
            return True
        frontier.extend(called)
    return False


def test_the_call_graph_reading_finds_a_test_that_never_calls_the_scan():
    """The control for the assertion below: over a source whose reporting test
    decides its claim with a string operation instead, the same reading says
    so."""
    planted = (f"def {THE_NEW_SCAN}(source, module):\n"
               f"    return []\n"
               f"def test_the_scan_reports_a_plant():\n"
               f"    planted = SOURCE + NEEDLE\n"
               f"    assert NEEDLE in planted\n")
    assert not reaches(planted, "test_the_scan_reports_a_plant", THE_NEW_SCAN)

    repaired = planted.replace("    assert NEEDLE in planted\n",
                               f"    assert {THE_NEW_SCAN}(planted, 'm.py')\n")
    assert reaches(repaired, "test_the_scan_reports_a_plant", THE_NEW_SCAN)


def test_every_claim_here_about_the_scan_is_made_by_calling_the_scan():
    """No assertion in this file holding this story is decided by a property of
    the standard library, which is the defect the story exists to catch."""
    source = THIS_FILE.read_text(encoding="utf-8")
    claiming = functions_claiming_a_report(source)
    assert claiming, "this reading found no test claiming a report"
    for name in claiming:
        assert reaches(source, name, THE_NEW_SCAN), name
