"""Independent validation for the rule this story leaves standing: the values
a target configures in its tracker script are read by the tests that check
them, never written into them.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart deliberately:

  * **the shape, not the values.** What is asserted is *how* the suite comes to
    know this target's board — every board-naming value is bound by reading the
    installed script, and none is bound to a string literal — rather than
    whether any particular string appears anywhere. A search of the suite for a
    configured value would break the very rule it is meant to hold: this
    target's project is a short string that occurs innocently all over the
    suite, and the name of its Status field is an ordinary English word that
    occurs as a heading in another module's prose. Whether an unrelated test
    happens to contain the string a target chose is not a property of the
    harness, so a value search could not promise that changing a value keeps
    the suite green — which is the promise this module exists to make.

  * **the modules it covers, discovered rather than listed.** `tests/` is
    parsed, and a module is covered when it imports one of the derived board
    values from the module that derives them. A listing written here would go
    stale the moment a module lands that nobody added to it. What this way of
    looking cannot see is stated rather than implied: a module that pins a
    board value *without* importing the derivation is outside the set this
    module discovers, and nothing here reports it. That is the cost of refusing
    to search test sources for values, and it is the cheaper of the two costs.

  * **the installed copy, as a live harness artifact.** `.harness/scripts/
    github.sh` is what this target has configured, so it is the subject of the
    assertions that name it: each board-naming constant it declares must be
    non-empty, because a target that has configured no board would otherwise
    pass every board assertion in the suite vacuously — which is what happened
    when the item branch carried no project for weeks and reported a failure
    for every status move it was asked to make.

  * **the drive, on two files.** The installed copy is run with nothing set in
    its environment and must file onto the board it declares. A copy of it
    whose board constants have been rewritten to invented values is run the
    same way and must file onto *those* — so what is asserted is that a copy
    follows its own declarations, rather than that it agrees with a number
    written down beside it.

Every absence asserted here carries a demonstration that it can fail:

  * "no covered module binds a board value to a string literal" sits beside the
    same scan over each of those modules' own source with such a binding
    planted in it, and beside a planted axis whose field name is a literal,
    both of which the same scan reports;
  * "each board value in the deriving module is bound by a call to the
    derivation" sits beside the same scan over that source with the calls
    rewritten to literals, which reports none of them derived;
  * "every constant the installed copy declares is non-empty" sits beside the
    same derivation over a copy declaring an empty one, which refuses it;
  * "the board a copy files onto is the board it declares" sits beside a copy
    declaring invented values, driven identically, which files onto the
    invented board — and the same derivation over that copy reports the
    invented values rather than this target's.

Nothing here reaches a network: the copies are run against the stub `gh` that
`test_filed_query` wrote, first on PATH, and the invented values are this
module's own inventions rather than any board that exists.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import test_filed_query
from test_filed_query import (  # noqa: F401 - shared idioms and fixtures
    BOARD_CONSTANTS,
    CLASSIFICATION,
    CONSTANT_ASSIGNMENT,
    INSTALLED_CONSTANTS,
    INSTALLED_SCRIPT,
    PROJECT_CONSTANT,
    PROJECT_OWNER_CONSTANT,
    REPO_ROOT,
    STATUS_FIELD_CONSTANT,
    STATUS_OPTION_CONSTANT,
    a_filed_brief,
    fixture_file,
    ledger_state,
    needs_jq,
    run_the_sync,
    stub_tracker,
    sync_constants,
    this_targets,
)

TESTS_DIR = REPO_ROOT / "tests"

#: The module that reads the installed script, and the function in it that does
#: the reading — taken off the module object rather than spelled here, so a
#: rename that was not carried through is an import that fails rather than a
#: scan that quietly matches nothing.
DERIVATION_MODULE = test_filed_query.__name__
DERIVATION_FUNCTION = test_filed_query.this_targets.__name__
DERIVING_SOURCE = Path(test_filed_query.__file__)

#: How the suite names a value belonging to the board *this* target files
#: against. A Python binding convention rather than a configured value, which
#: is why it can be written here: what may not be written down is what the
#: values are, and this says only what they are called.
BOARD_VALUE_PREFIX = "THIS_TARGETS_"
BOARD_VALUE_NAMES = tuple(sorted(
    name for name in vars(test_filed_query) if name.startswith(BOARD_VALUE_PREFIX)))

#: The classification table's rows, and where in one of them the board's own
#: name for a column sits. Read off the tuple type rather than counted here, so
#: an axis that gained a part is a position resolved afresh.
AXIS_TYPE = test_filed_query.Axis.__name__
FIELD_NAME_POSITION = test_filed_query.Axis._fields.index("field_name")


# --------------------------------------------------------------------------
# 1. Which modules this holds, discovered by reading imports
# --------------------------------------------------------------------------


def imports_the_derived_values(source: str) -> bool:
    """Whether one module reads its board values from the derivation."""
    return any(
        isinstance(node, ast.ImportFrom) and node.module == DERIVATION_MODULE
        and any(alias.name in BOARD_VALUE_NAMES for alias in node.names)
        for node in ast.walk(ast.parse(source)))


def tracker_facing_modules(directory: Path) -> list[Path]:
    """Every module under `directory` this rule covers, and the deriving one.

    Discovery rather than a listing, and taking the directory as a parameter so
    the same discovery the live suite is held to is the one that can be run
    over a directory built to contain something.
    """
    found = [path for path in sorted(Path(directory).glob("test_*.py"))
             if imports_the_derived_values(path.read_text(encoding="utf-8"))]
    deriving = Path(directory) / DERIVING_SOURCE.name
    return ([deriving] if deriving.is_file() else []) + found


COVERED = tracker_facing_modules(TESTS_DIR)


def test_the_discovery_finds_the_deriving_module_and_the_modules_importing_it():
    """The sweep below is over something.

    A scan that covered no module would report no violation for a reason that
    has nothing to do with what any module contains, so what it covers is
    asserted first: the module that derives the values, and more than one
    module that imports them.
    """
    assert DERIVING_SOURCE in COVERED
    assert len(COVERED) >= 3, [path.name for path in COVERED]
    assert BOARD_VALUE_NAMES, sorted(vars(test_filed_query))
    for name in BOARD_VALUE_NAMES:
        value = getattr(test_filed_query, name)
        assert isinstance(value, str) and value, name


def test_the_discovery_leaves_a_module_that_imports_nothing_of_the_kind_alone(
        tmp_path):
    """The control for the discovery: it decides by what a module imports.

    A module importing something else from the same module is not covered, and
    one importing a board value is — so "covered" is a property of the import
    rather than of the file being under `tests/`.
    """
    (tmp_path / "test_unrelated.py").write_text(
        f"from {DERIVATION_MODULE} import needs_jq\n", encoding="utf-8")
    assert tracker_facing_modules(tmp_path) == []

    (tmp_path / "test_covered.py").write_text(
        f"from {DERIVATION_MODULE} import {BOARD_VALUE_NAMES[0]}\n",
        encoding="utf-8")
    assert [path.name for path in tracker_facing_modules(tmp_path)] == [
        "test_covered.py"]


# --------------------------------------------------------------------------
# 2. In each of them, every board value is read and none is written
# --------------------------------------------------------------------------


def _carries_a_string(node: ast.AST) -> bool:
    return any(isinstance(inner, ast.Constant) and isinstance(inner.value, str)
               for inner in ast.walk(node))


def _axis_field_name(node: ast.Call) -> ast.expr | None:
    """What one construction of the classification table names its column."""
    for keyword in node.keywords:
        if keyword.arg == test_filed_query.Axis._fields[FIELD_NAME_POSITION]:
            return keyword.value
    if len(node.args) > FIELD_NAME_POSITION:
        return node.args[FIELD_NAME_POSITION]
    return None


def board_values_written_down(source: str) -> list[tuple[str, int]]:
    """Every place in one module where a board value is a string rather than a
    reading: the name and the line.

    Two shapes, because a board value reaches the suite two ways. One is a
    binding of one of the derived names; the other is a row of the
    classification table, whose column name is the board's and whose other
    parts — what the payload calls the field, and what the script calls the
    constant — are not.
    """
    written = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            names = [one.id for one in targets if isinstance(one, ast.Name)]
            for name in names:
                if name in BOARD_VALUE_NAMES and node.value is not None \
                        and _carries_a_string(node.value):
                    written.append((name, node.lineno))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == AXIS_TYPE:
            named = _axis_field_name(node)
            if named is not None and isinstance(named, ast.Constant) \
                    and isinstance(named.value, str):
                written.append((AXIS_TYPE, node.lineno))
    return sorted(written)


@pytest.mark.parametrize("module", COVERED, ids=lambda path: path.name)
def test_no_covered_module_writes_a_board_value_down(module: Path):
    """The rule itself, module by module."""
    found = board_values_written_down(module.read_text(encoding="utf-8"))
    assert found == [], (module.name, found)


#: A binding of a board value to a string, and a row of the classification
#: table naming a column with one. Both are what this module exists to catch,
#: written here — they are invented values rather than this target's — and
#: appended to each covered module's own source, so what is scanned is still
#: that module.
PLANTED_BINDING = '\n{name} = "an invented board"\n'
PLANTED_AXIS = (
    '\nA_PROBE = ' + AXIS_TYPE + '("category", "CATEGORY_FIELD",\n'
    '                  "An Invented Column", ())\n')


@pytest.mark.parametrize("module", COVERED, ids=lambda path: path.name)
def test_the_same_scan_reports_a_binding_planted_in_that_module(module: Path):
    """The negative control every one of the absences above needs: the scan is
    shown reporting what it is looking for, in the real source of the module it
    has just been silent about."""
    source = module.read_text(encoding="utf-8")
    name = BOARD_VALUE_NAMES[0]

    planted = source + PLANTED_BINDING.format(name=name)
    assert [one for one, _ in board_values_written_down(planted)] == [name]

    with_an_axis = source + PLANTED_AXIS
    assert [one for one, _ in board_values_written_down(with_an_axis)] == \
        [AXIS_TYPE]


def test_the_scan_leaves_the_parts_of_an_axis_that_are_not_the_boards_alone():
    """The other control: what an axis legitimately writes down is untouched.

    A row names three strings, and only one of them is the board's. A scan that
    reported the payload's field name or the script's constant name would make
    this rule unsatisfiable rather than strict.
    """
    derived = ('\nA_PROBE = ' + AXIS_TYPE + '("category", "CATEGORY_FIELD",\n'
               '                  ' + DERIVATION_FUNCTION +
               '("CATEGORY_FIELD"), ())\n')
    assert board_values_written_down(derived) == []
    # And the same row with the column written down, so the difference between
    # the two is the one position this scan is about.
    assert board_values_written_down(PLANTED_AXIS) == [(AXIS_TYPE, 2)]


# --------------------------------------------------------------------------
# 3. The deriving module binds each of them by reading the script
# --------------------------------------------------------------------------


def values_read_from_the_script(source: str) -> set[str]:
    """Every board value one module binds by calling the derivation."""
    read = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or \
                    target.id not in BOARD_VALUE_NAMES:
                continue
            if any(isinstance(inner, ast.Call)
                   and isinstance(inner.func, ast.Name)
                   and inner.func.id == DERIVATION_FUNCTION
                   for inner in ast.walk(node.value)):
                read.add(target.id)
    return read


def test_every_board_value_is_bound_by_a_call_to_the_derivation():
    """Not written down is half the claim; read from the script is the other.

    A name bound to something that is neither a literal nor a reading — a
    second parse written beside this one, say — would satisfy the scan above
    and still be a second answer to what this target's board is.
    """
    source = DERIVING_SOURCE.read_text(encoding="utf-8")
    assert values_read_from_the_script(source) == set(BOARD_VALUE_NAMES)


def test_the_same_scan_reports_none_read_when_the_calls_become_literals():
    """The negative control for that: the same source with each derivation
    rewritten to the literal it replaced, where nothing is read."""
    source = DERIVING_SOURCE.read_text(encoding="utf-8")
    rewritten = "\n".join(
        f'{line.split(" = ")[0]} = "an invented board"'
        if line.split(" = ")[0] in BOARD_VALUE_NAMES else line
        for line in source.splitlines())
    assert values_read_from_the_script(rewritten) == set()
    assert [one for one, _ in board_values_written_down(rewritten)] == \
        sorted(BOARD_VALUE_NAMES)


@pytest.mark.parametrize(
    "module", [path for path in COVERED if path != DERIVING_SOURCE],
    ids=lambda path: path.name)
def test_an_importing_module_binds_no_board_value_of_its_own(module: Path):
    """One reading, so the modules cannot disagree about the board.

    An importing module may use the values and may not bind them: a second
    binding, however it was written, is a second answer available to that
    module alone.
    """
    bound = [node.lineno
             for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
             if isinstance(node, ast.Assign)
             for target in node.targets
             if isinstance(target, ast.Name) and target.id in BOARD_VALUE_NAMES]
    assert bound == [], (module.name, bound)


# --------------------------------------------------------------------------
# 4. The installed copy declares a board, and a copy declaring another one
#    follows its own declarations
# --------------------------------------------------------------------------


def test_every_board_constant_the_installed_copy_declares_is_non_empty():
    """What the writing-down used to be for.

    Every other board test in the suite sets the tracker's variables, so an
    installed copy whose constants were empty would pass all of them while this
    target filed against no board at all. This is the claim that stands against
    that, and it names no value.
    """
    for constant in BOARD_CONSTANTS:
        assert this_targets(constant), constant


def test_the_same_derivation_refuses_a_copy_declaring_an_empty_constant():
    """The negative control the assertion above needs: the derivation is shown
    refusing what it is looking for, over a copy of the installed script with
    one constant emptied."""
    for constant in BOARD_CONSTANTS:
        emptied = sync_constants(a_script_declaring({constant: ""}))
        assert emptied[constant][1] == "", constant
        with pytest.raises(AssertionError):
            this_targets(constant, constants=emptied)
        # The rest of that copy is untouched, so the refusal is about the one
        # constant rather than about a rewriting that broke the parse.
        for other in BOARD_CONSTANTS:
            if other != constant:
                assert this_targets(other, constants=emptied), (constant, other)


#: A board this target does not have. Every board-naming constant gets one, so
#: a copy declaring these has nothing whatever in common with the installed
#: copy's own board — which is what makes "the values that took effect are the
#: ones it declares" a statement about the file rather than about a coincidence.
INVENTED = {
    PROJECT_CONSTANT: "4219",
    PROJECT_OWNER_CONSTANT: "@an-owner-this-target-is-not",
    STATUS_FIELD_CONSTANT: "Stage",
    STATUS_OPTION_CONSTANT: "Intake",
    **{one.constant: f"An Invented {one.payload_field.title()}"
       for one in CLASSIFICATION},
}


def a_script_declaring(values: dict[str, str]) -> str:
    """The installed copy with its board constants rewritten to `values`.

    A rendering of the shipped file rather than a script written here, so the
    copy that is driven is the one this target runs in every respect except the
    board it names.
    """
    def rewrite(found) -> str:
        replacement = values.get(found.group("name"))
        if replacement is None:
            return found.group(0)
        return (f'{found.group("name")}="${{{found.group("variable")}'
                f':-{replacement}}}"')

    return CONSTANT_ASSIGNMENT.sub(
        rewrite, INSTALLED_SCRIPT.read_text(encoding="utf-8"))


def test_the_invented_board_shares_nothing_with_the_one_this_target_declares():
    """What the control below rests on, asserted rather than assumed."""
    assert set(INVENTED) == set(BOARD_CONSTANTS)
    for constant, invented in INVENTED.items():
        assert invented, constant
        assert invented != this_targets(constant), constant


def test_the_same_derivation_over_a_copy_reports_what_that_copy_declares():
    """The suite's knowledge of the board follows the file.

    The derivation that reads this target's values is applied to a copy
    declaring other ones, and reports those — so what the assertions elsewhere
    rest on is a reading rather than a constant that happens to agree with one.
    """
    declared = sync_constants(a_script_declaring(INVENTED))
    for constant, invented in INVENTED.items():
        assert this_targets(constant, constants=declared) == invented, constant
    # The mechanics are untouched: only the values moved, so the copy is the
    # shipped file in every other respect.
    assert set(declared) == set(INSTALLED_CONSTANTS)


def a_board_named_by(ledger: Path, values: dict[str, str]) -> str:
    """Re-name the stub's board to the one a copy declaring `values` files onto.

    The project, its owner, the Status field and every column a classification
    reaches are renamed in place, so what the stub offers is the board the copy
    declares and nothing else. Returned as the key it now answers to.
    """
    state = ledger_state(ledger)
    renamed = {this_targets(constant): value
               for constant, value in values.items()}
    board = state["projects"].pop(
        f"{this_targets(PROJECT_OWNER_CONSTANT)}/"
        f"{this_targets(PROJECT_CONSTANT)}")
    for field in board["fields"]:
        field["name"] = renamed.get(field["name"], field["name"])
        for option in field.get("options", []):
            option["name"] = renamed.get(option["name"], option["name"])
    key = f"{values[PROJECT_OWNER_CONSTANT]}/{values[PROJECT_CONSTANT]}"
    state["projects"][key] = board
    ledger.write_text(json.dumps(state), encoding="utf-8")
    return key


def column(name: str) -> str:
    """How the board keys an item's value for the column called `name`."""
    return name.replace(" ", "").lower()


def the_item_filed_onto(ledger: Path, key: str) -> dict:
    items = ledger_state(ledger)["projects"][key]["items"]
    assert len(items) == 1, items
    return items[0]


def drive_with_nothing_set(script: Path, tmp_path: Path, environment: dict,
                           brief: dict):
    """One filing by a copy run with nothing set in its environment.

    `extra` is empty, which is the whole claim: the board reached is the one
    the file declares rather than one this module handed it.
    """
    return run_the_sync(script, tmp_path, environment, key="k-declared",
                        payload=brief, extra={})


@needs_jq
def test_the_installed_copy_files_onto_the_board_it_declares(tmp_path):
    """The property this target already had, kept: driven with nothing set, the
    installed copy files against its own project and lands in its own column,
    with each classification written into the column it names."""
    environment, ledger = stub_tracker(tmp_path)
    brief = a_filed_brief()

    result = drive_with_nothing_set(INSTALLED_SCRIPT, tmp_path, environment,
                                    brief)

    assert result.returncode == 0, result.stderr
    filed = the_item_filed_onto(
        ledger, f"{this_targets(PROJECT_OWNER_CONSTANT)}/"
                f"{this_targets(PROJECT_CONSTANT)}")
    assert filed[column(this_targets(STATUS_FIELD_CONSTANT))] == \
        this_targets(STATUS_OPTION_CONSTANT)
    for one in CLASSIFICATION:
        assert filed.get(column(this_targets(one.constant))) == \
            str(brief[one.payload_field]), one.constant


@needs_jq
def test_a_copy_declaring_an_invented_board_files_onto_that_one(tmp_path):
    """The mocked configuration, and the control the test above needs.

    Identical drive, identical stub, a copy of the installed script differing
    only in the values its board constants declare — and every value that takes
    effect is the invented one. Green above therefore says the copy follows its
    own declarations, rather than that this module and that file happen to
    agree about a number.
    """
    environment, ledger = stub_tracker(tmp_path)
    key = a_board_named_by(ledger, INVENTED)
    copied = fixture_file(tmp_path / "an-invented-board",
                          INSTALLED_SCRIPT.name, a_script_declaring(INVENTED))
    brief = a_filed_brief()

    result = drive_with_nothing_set(copied, tmp_path, environment, brief)

    assert result.returncode == 0, result.stderr
    filed = the_item_filed_onto(ledger, key)
    assert filed[column(INVENTED[STATUS_FIELD_CONSTANT])] == \
        INVENTED[STATUS_OPTION_CONSTANT]
    for one in CLASSIFICATION:
        assert filed.get(column(INVENTED[one.constant])) == \
            str(brief[one.payload_field]), one.constant
