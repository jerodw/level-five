"""Independent validation for story-163: a fixture that drives a whole
coordinator run is built once for the tests that read it.

Written from the story's acceptance criteria rather than from the
implementation, in the shape story-158 gave the same claim for the sample
target. What function scope was buying is isolation — a test that writes into
its run directory cannot be seen by any other — and this story keeps the
scope on the fixture a test names while changing how the run behind it is
produced, so the questions asked here are about the copy each test receives,
the shared run it is copied from, and how many times the coordinator is
driven to make them:

  * **the count.** `tests/test_coordinator_contract.py` is run alone, in a
    subprocess, under a plugin that wraps `story_coordinator.run_story` and
    tallies each invocation; the tally is one per run shape. No duration is
    read: the saving is a property of the fixture's structure, and the
    structure is what is asserted.
  * **the copy.** Each shape's fixture returns a path under the requesting
    test's `tmp_path` that is not the shared run's path, holding the same
    files with the same contents.
  * **the isolation.** One test writes into its copy, appends an event to it
    and deletes a required artifact from it, then reads the shared run and
    takes a fresh copy. Mutation and check happen in the same test so the
    assertion holds under `-n auto` whichever worker runs it and in whatever
    order.
  * **the process.** Once a shape's shared run exists, handing a test a copy
    drives the coordinator no further: `run_story` is replaced with a raiser
    and the fixture resolved through `request.getfixturevalue`, so the
    assertion is on the fixture as pytest runs it.
  * **the sibling savings.** The two nested runs in
    `tests/test_contract_assertions_bite.py` pass a `-k` selection, and the
    harness copy in `tests/test_config_keys_are_obeyed.py` is a per-test copy
    of a tree built once per session.

Every absence asserted here carries a demonstration that it can fail:

  * "the shared run holds no such file, its log gained no line and its
    artifact is still there" sits beside the same checks on the mutated copy,
    where all three report the mutation;
  * "no run is driven on the per-test path" sits beside the shared run's own
    builder under the same raiser, which raises when it reaches the
    coordinator;
  * "the harness template holds the read the mutation replaced" sits beside
    the same check on the mutated copy, where the read is gone.

The fixtures under test are imported from the modules that define them, so
what is resolved here is the fixture those modules run rather than a
re-implementation of it.
"""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import story_coordinator
import test_config_keys_are_obeyed as config_keys
import test_contract_assertions_bite as bite
import test_coordinator_contract as contract
import test_no_test_bounds_the_machine as clocks
from test_config_keys_are_obeyed import (  # noqa: F401 - fixtures under test
    harness_copy, harness_copy_template)
from test_coordinator_contract import (  # noqa: F401 - fixtures under test
    completed_run, completed_target_root, escalated_run, retried_run,
    shared_completed_run, shared_escalated_run, shared_retried_run)

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
CONTRACT_FILE = Path(contract.__file__)

#: The run shapes the contract module drives, and the fixtures that hand a
#: test each one. Read off the module rather than written here, so a shape
#: added there is counted and copied here without this file changing.
SHAPE_NAMES = tuple(contract.RUN_SHAPES)


def shared_fixture_for(shape: str) -> str:
    return f"shared_{shape}"


def tree_files(root: Path) -> dict[str, bytes]:
    """Every regular file under `root`, keyed by its relative path."""
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _run_refused(*args, **kwargs):
    raise AssertionError("the coordinator was driven on the per-test path")


# --------------------------------------------------------------------------
# The count: the contract file alone drives the coordinator once per shape
# --------------------------------------------------------------------------

#: A plugin the nested run loads with `-p`. It wraps the coordinator's entry
#: point at configure time — after the suite's conftest has put
#: `orchestration/` on the path — and appends one line to the tally file for
#: every invocation, so the count is of calls the contract file's fixtures
#: made and nothing else.
TALLY_ENV = "L5_RUN_STORY_TALLY"
COUNTING_PLUGIN = '''\
import functools
import os


def pytest_configure(config):
    # Imported here rather than at the top: a `-p` plugin is loaded before
    # the suite's conftest has put `orchestration/` on the path, and
    # configure time is after.
    import story_coordinator
    original = story_coordinator.run_story
    tally = os.environ["{env}"]

    @functools.wraps(original)
    def counted(*args, **kwargs):
        with open(tally, "a", encoding="utf-8") as handle:
            handle.write("run_story\\n")
        return original(*args, **kwargs)

    story_coordinator.run_story = counted
'''.format(env=TALLY_ENV)


def run_contract_file_counting_runs(tmp_path: Path) -> tuple[subprocess.CompletedProcess, int]:
    """Run the contract file alone, in one process, and count the runs it drove."""
    plugin_dir = tmp_path / "counting-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "count_run_story.py").write_text(COUNTING_PLUGIN, encoding="utf-8")
    tally = tmp_path / "run-story-tally.txt"
    tally.write_text("", encoding="utf-8")
    env = {**os.environ, TALLY_ENV: str(tally),
           "PYTHONPATH": os.pathsep.join(
               [str(plugin_dir)] + [p for p in [os.environ.get("PYTHONPATH")] if p])}
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         f"tests/{CONTRACT_FILE.name}", "-q", "-p", "no:cacheprovider",
         "-p", "count_run_story"],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    lines = tally.read_text(encoding="utf-8").splitlines()
    return result, len(lines)


def test_the_contract_file_alone_drives_the_coordinator_once_per_shape(tmp_path):
    """The saving the story exists for, counted rather than timed.

    Run in one process, so "per worker" is one worker: every test in the
    contract file runs, and the coordinator is invoked exactly as many times
    as there are shapes. Before this story the count was the number of tests
    that read a run, and any regression to function scope on one shape
    raises it above the number of shapes again.
    """
    result, runs = run_contract_file_counting_runs(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no tests ran" not in result.stdout
    assert runs == len(SHAPE_NAMES), (
        f"the contract file drove the coordinator {runs} time(s) for "
        f"{len(SHAPE_NAMES)} shape(s):\n{result.stdout}")


def test_every_shape_is_read_by_more_than_one_test_so_the_count_is_a_saving():
    """The count above means something only if each shape has several
    readers: with one reader per shape, once per shape and once per reader
    are the same number. Read off the contract file's own source: the tests
    naming each shape's fixture, plus the parametrized readers naming it as a
    string."""
    tree = ast.parse(CONTRACT_FILE.read_text(encoding="utf-8"))
    tests = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    for shape in SHAPE_NAMES:
        readers = [
            node.name for node in tests
            if shape in {argument.arg for argument in node.args.args}
            or any(isinstance(inner, ast.Constant) and inner.value == shape
                   for inner in ast.walk(node))
        ]
        assert len(readers) > 1, (shape, readers)


# --------------------------------------------------------------------------
# The scopes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", SHAPE_NAMES)
def test_the_shared_run_is_module_scoped_and_the_copy_is_function_scoped(
        shape, request):
    (shared,) = request._fixturemanager.getfixturedefs(
        shared_fixture_for(shape), request.node)
    (copy,) = request._fixturemanager.getfixturedefs(shape, request.node)
    assert shared.scope == "module"
    assert copy.scope == "function"
    assert shared_fixture_for(shape) in copy.argnames


def test_the_shapes_are_the_ones_the_contract_module_hands_out():
    """Each shape in the table has a fixture of its name and a shared one
    behind it, and the table drives every shape the module has fixtures for."""
    for shape in SHAPE_NAMES:
        assert hasattr(contract, shape), shape
        assert hasattr(contract, shared_fixture_for(shape)), shape
    fixtures_handing_out_a_shape = {
        name[len("shared_"):] for name in dir(contract)
        if name.startswith("shared_") and name.endswith("_run")}
    assert fixtures_handing_out_a_shape == set(SHAPE_NAMES)


# --------------------------------------------------------------------------
# The copy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", SHAPE_NAMES)
def test_each_shape_returns_a_copy_under_tmp_path_holding_the_shared_runs_files(
        shape, request, tmp_path):
    copy = request.getfixturevalue(shape)
    shared = request.getfixturevalue(shared_fixture_for(shape))
    assert tmp_path in copy.parents
    assert copy != shared.run_dir
    assert copy.resolve() != shared.run_dir.resolve()
    assert tmp_path not in shared.run_dir.parents
    assert tree_files(copy) == tree_files(shared.run_dir)
    assert tree_files(copy), "the run wrote nothing to copy"
    # What a reader asks of the copy is what it asked of the run before:
    # every artifact the contract requires of a completed run is there, or —
    # for an escalated one — its summary is.
    present = {path.name for path in copy.iterdir()}
    _, exit_code = contract.RUN_SHAPES[shape]
    if exit_code == 0:
        assert contract.required_artifacts() <= present, contract.required_artifacts() - present
    else:
        assert "escalation-summary.md" in present


def test_two_copies_of_one_shape_in_one_test_are_two_directories(
        completed_run, shared_completed_run, tmp_path):
    later = contract.copy_of_run(shared_completed_run, tmp_path, "a-later-copy")
    assert later != completed_run
    assert tree_files(later) == tree_files(completed_run)


def test_the_completion_target_is_the_repository_the_shared_run_committed_into(
        completed_target_root, shared_completed_run):
    assert completed_target_root == shared_completed_run.target_root
    assert (completed_target_root / ".git").is_dir()
    message = contract.commit_message(completed_target_root)
    assert contract.completion_commit_problems(
        message, story_id="story-001", title=contract.STORY_TITLE) == []


# --------------------------------------------------------------------------
# The process: a copy drives no run
# --------------------------------------------------------------------------


def test_the_per_test_path_drives_no_run(request, shared_completed_run, monkeypatch,
                                         tmp_path, tmp_path_factory):
    """Once the shared run exists, handing a test a copy is a copy.

    `shared_completed_run` is resolved first so the one-time run has
    happened; the raiser then stands in for the coordinator's entry point,
    and `completed_run` is resolved through pytest itself.
    """
    monkeypatch.setattr(story_coordinator, "run_story", _run_refused)
    copy = request.getfixturevalue("completed_run")
    assert tmp_path in copy.parents
    assert (copy / "state.json").is_file()
    # Control: the shared run's own builder does drive the coordinator, and
    # the same raiser catches it — so the copy above passed because no run
    # was driven, not because the raiser was installed where the fixture
    # does not go.
    with pytest.raises(AssertionError, match="the coordinator was driven"):
        contract.drive_shared_run(tmp_path_factory, "completed_run")


# --------------------------------------------------------------------------
# The isolation
# --------------------------------------------------------------------------


def test_a_mutated_copy_leaves_the_shared_run_and_a_later_copy_unchanged(
        completed_run, shared_completed_run, tmp_path):
    """Mutation and inspection in one test, so no ordering across workers is
    relied on. The three mutations are the three the contract module's own
    readers make: one writes an artifact, one appends an event, one deletes
    a required artifact."""
    shared = shared_completed_run.run_dir
    shared_files_before = tree_files(shared)
    shared_log_before = contract.log_lines(shared)
    # A required artifact other than the two the other mutations read back.
    artifact = next(name for name in sorted(contract.required_artifacts())
                    if name not in ("events.log", "state.json"))
    assert (shared / artifact).is_file(), artifact

    (completed_run / "an-artifact-this-test-adds.json").write_text(
        "{}\n", encoding="utf-8")
    story_coordinator.append_event(
        completed_run, "an event this test appends", kind="note")
    (completed_run / artifact).unlink()

    # Control: the checks below, applied to the mutated copy, report all
    # three mutations — the file present, the log one line longer and ending
    # in the appended event, the artifact gone.
    assert (completed_run / "an-artifact-this-test-adds.json").exists()
    assert contract.log_lines(completed_run)[-1].endswith("an event this test appends")
    assert len(contract.log_lines(completed_run)) == len(shared_log_before) + 1
    assert not (completed_run / artifact).exists()
    assert tree_files(completed_run) != shared_files_before

    # The shared run is as it was driven.
    assert not (shared / "an-artifact-this-test-adds.json").exists()
    assert contract.log_lines(shared) == shared_log_before
    assert (shared / artifact).is_file()
    assert tree_files(shared) == shared_files_before

    # A copy taken afterwards, the way the fixture takes one, is the run.
    later = contract.copy_of_run(shared_completed_run, tmp_path, "later-copy")
    assert not (later / "an-artifact-this-test-adds.json").exists()
    assert contract.log_lines(later) == shared_log_before
    assert (later / artifact).is_file()
    assert tree_files(later) == shared_files_before


# --------------------------------------------------------------------------
# The narrowed nested runs
# --------------------------------------------------------------------------

NARROWED_NESTED_RUNS = (
    "test_the_contract_file_passes_against_an_unmutated_coordinator",
    "test_the_contract_holds_when_a_run_gains_an_artifact_and_an_event",
)


def _run_contract_calls(function: ast.FunctionDef) -> list[ast.Call]:
    return [node for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == bite.run_contract.__name__]


@pytest.mark.parametrize("name", NARROWED_NESTED_RUNS)
def test_the_nested_run_passes_a_selection_and_requires_it_to_select_something(name):
    """Read off the source: each of the two runs hands `run_contract` a
    second argument — the names it narrows to — and asserts the output does
    not say nothing ran. The mutation cases beside them already did both."""
    tree = ast.parse(Path(bite.__file__).read_text(encoding="utf-8"))
    (function,) = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == name]
    calls = _run_contract_calls(function)
    assert calls, name
    for call in calls:
        assert len(call.args) == 2, ast.unparse(call)
    assert any(isinstance(node, ast.Constant) and node.value == "no tests ran"
               for node in ast.walk(function)), name


def test_the_control_selects_the_union_of_the_violations_names():
    expected = {name for _, names in bite.VIOLATIONS.values() for name in names}
    assert set(bite.CONTRACT_TESTS_THE_VIOLATIONS_NAME) == expected
    assert expected, "no violation names a contract test"


def test_the_artifact_and_event_case_selects_the_run_directory_events_and_state_tests():
    """The three contract tests about what a run directory holds, what its
    log looks like and what its state carries — matched on their subjects
    rather than on a copy of their names."""
    selected = bite.RUN_DIRECTORY_EVENTS_AND_STATE_TESTS
    assert any("artifacts" in name for name in selected)
    assert any("events_log" in name for name in selected)
    assert any("state_json" in name for name in selected)


def test_a_selection_matching_nothing_passes_nothing_and_exits_non_zero(tmp_path):
    """The control for "the selection ran something": a `-k` naming no test
    the contract file carries deselects everything, passes nothing and exits
    non-zero, so a narrowed run that exits zero with something passed did
    select its tests."""
    root = bite.mutant_repo(tmp_path, [])
    code, output = bite.run_contract(root, ("test_a_name_no_contract_test_carries",))
    assert code != 0
    assert " passed" not in output
    assert "deselected" in output


# --------------------------------------------------------------------------
# The harness copy in the config-keys module
# --------------------------------------------------------------------------


def test_the_harness_copy_is_a_per_test_copy_of_a_session_template(
        harness_copy, harness_copy_template, tmp_path, request):
    assert harness_copy == tmp_path / "throwaway-harness"
    assert harness_copy != harness_copy_template
    assert tmp_path not in harness_copy_template.parents
    assert tree_files(harness_copy) == tree_files(harness_copy_template)
    for directory in config_keys.COPIED_TREES:
        assert (harness_copy / directory).is_dir(), directory
    for name in config_keys.COPIED_TESTS:
        assert (harness_copy / "tests" / name).is_file(), name
    (template_def,) = request._fixturemanager.getfixturedefs(
        "harness_copy_template", request.node)
    (copy_def,) = request._fixturemanager.getfixturedefs("harness_copy", request.node)
    assert template_def.scope == "session"
    assert copy_def.scope == "function"


def test_the_copied_trees_are_real_directories_not_links_back_to_this_repository(
        harness_copy):
    """The copy exists so a mutation lands in it rather than here: every
    copied tree and test file resolves under the copy."""
    for directory in config_keys.COPIED_TREES:
        path = harness_copy / directory
        assert not path.is_symlink(), directory
        assert harness_copy.resolve() in path.resolve().parents, directory
    for name in config_keys.COPIED_TESTS:
        path = harness_copy / "tests" / name
        assert not path.is_symlink(), name
        assert harness_copy.resolve() in path.resolve().parents, name


def test_a_mutated_harness_copy_leaves_the_template_and_a_later_copy_unchanged(
        harness_copy, harness_copy_template, tmp_path):
    """Mutation and inspection in one test, as above. The mutation is one
    the module's own cases apply, taken off its table."""
    key = sorted(config_keys.MUTATIONS)[0]
    template_files_before = tree_files(harness_copy_template)
    config_keys.apply_mutation(harness_copy, key)
    # Control: the same reads report the mutation in the copy — the anchor
    # each replacement removes is gone there.
    for relative, old, new in config_keys.MUTATIONS[key]:
        mutated = (harness_copy / relative).read_text(encoding="utf-8")
        assert old not in mutated, (key, relative)
        assert new in mutated, (key, relative)
    assert tree_files(harness_copy) != template_files_before

    # The template still carries every read the mutation replaced.
    for relative, old, _ in config_keys.MUTATIONS[key]:
        assert old in (harness_copy_template / relative).read_text(encoding="utf-8")
    assert tree_files(harness_copy_template) == template_files_before

    # A copy taken afterwards, the way the fixture takes one, is the template.
    later = tmp_path / "later-copy"
    shutil.copytree(harness_copy_template, later, symlinks=True)
    for relative, old, _ in config_keys.MUTATIONS[key]:
        assert old in (later / relative).read_text(encoding="utf-8")
    assert tree_files(later) == template_files_before


def test_the_mutation_cases_and_the_pristine_control_take_the_copy_fixture():
    """Read off the source: both tests are handed `harness_copy` by pytest
    rather than building one, so each runs in a copy of its own."""
    tree = ast.parse(Path(config_keys.__file__).read_text(encoding="utf-8"))
    for name in ("test_the_pristine_copy_runs_every_proof_green",
                 "test_the_proof_for_each_key_goes_red_when_that_key_stops_being_read"):
        (function,) = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == name]
        assert "harness_copy" in {a.arg for a in function.args.args}, name


# --------------------------------------------------------------------------
# No wall-clock bound
# --------------------------------------------------------------------------

#: The modules this story wrote or rewrote fixtures in, other than the
#: config-keys module — which reads a clock in the timeout proofs this story
#: left alone, and is classified for it in the standing rule.
MODULES_THIS_STORY_LEFT_CLOCKLESS = (
    Path(__file__).name,
    CONTRACT_FILE.name,
    Path(bite.__file__).name,
)

#: The functions this story added or changed in the config-keys module, held
#: to the same standard one function at a time since the module as a whole
#: cannot be.
CONFIG_KEYS_FUNCTIONS_THIS_STORY_TOUCHED = (
    "build_harness_copy", "harness_copy_template", "harness_copy",
    "test_the_pristine_copy_runs_every_proof_green",
    "test_the_proof_for_each_key_goes_red_when_that_key_stops_being_read",
)


def test_no_module_this_story_changed_reads_a_clock(tmp_path):
    """The saving is asserted by structure, never by duration: none of the
    modules this story rewrote fixtures in sleeps or reads a clock, by the
    standing rule's own search."""
    reading = clocks.modules_reading_a_clock(REPO_ROOT / "tests")
    for name in MODULES_THIS_STORY_LEFT_CLOCKLESS:
        assert name not in reading, name
    # Control: the same search reports a module planted with the first token
    # it looks for. The token is composed rather than written out, so this
    # module does not itself become one the search reports.
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "test_planted.py").write_text(
        "import time\n\n\ndef test_it():\n    " + clocks.CLOCK_CALLS[0] + "0.01)\n",
        encoding="utf-8")
    (planted / "test_clockless.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8")
    assert clocks.modules_reading_a_clock(planted) == ["test_planted.py"]


def test_the_functions_this_story_touched_in_the_config_keys_module_read_no_clock():
    tree = ast.parse(Path(config_keys.__file__).read_text(encoding="utf-8"))
    found = {node.name: node for node in tree.body
             if isinstance(node, ast.FunctionDef)
             and node.name in CONFIG_KEYS_FUNCTIONS_THIS_STORY_TOUCHED}
    assert set(found) == set(CONFIG_KEYS_FUNCTIONS_THIS_STORY_TOUCHED)
    for name, node in found.items():
        text = ast.unparse(node)
        for token in clocks.CLOCK_CALLS:
            assert token not in text, (name, token)
    # Control: the module as a whole does carry those tokens, in the timeout
    # proofs this story left alone, so the per-function scan above is
    # looking at source that could have contained one.
    whole = Path(config_keys.__file__).read_text(encoding="utf-8")
    assert any(token in whole for token in clocks.CLOCK_CALLS)
