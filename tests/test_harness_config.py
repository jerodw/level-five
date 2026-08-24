"""What `harness_config` reads: a target's configuration, and the workflow
definitions a harness root holds.

The definitions used below are built by the fixture in `tests/conftest.py` and
written into roots these tests own. The subject is the reader — which names it
reports, and what it says about a name it cannot answer — and a definition is
its input, so reading `workflows/story-workflow.json` here would make what this
repository deploys into something the suite enforces, and would leave the
central case unexercised: a root that holds more than one definition, which
this repository is not.
"""
import inspect
from pathlib import Path

import pytest

import conftest

import harness_config

#: Two definitions differing only in name. Nothing here loads a stage or runs
#: one, so the shape a workflow needs to be *runnable* is beside the point;
#: what matters is that the root holds two of them and that their names sort in
#: a known order.
FIRST = conftest.build_workflow(conftest.workflow_stage(),
                                name="alpha-workflow")
SECOND = conftest.build_workflow(conftest.workflow_stage(),
                                 name="beta-workflow")

#: A name neither definition carries.
UNDEFINED = "gamma-workflow"


def test_the_two_definitions_this_module_builds_differ():
    assert FIRST["name"] != SECOND["name"]
    assert UNDEFINED not in (FIRST["name"], SECOND["name"])


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    """A harness root holding both definitions."""
    root = tmp_path / "harness"
    for workflow in (FIRST, SECOND):
        conftest.materialize_workflow(workflow, root)
    return root


def test_quoted_values_are_unquoted(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "config.yaml").write_text(
        'test_command: "echo ok"\n'
        'allowed_tools:\n'
        '  - "Bash(.venv/bin/python:*)"\n'
        "  - 'Bash(chmod:*)'\n"
        '  - Bash(ls:*)\n',
        encoding="utf-8",
    )
    config = harness_config.load_config(tmp_path)
    assert config["test_command"] == "echo ok"
    assert config["allowed_tools"] == [
        "Bash(.venv/bin/python:*)",
        "Bash(chmod:*)",
        "Bash(ls:*)",
    ]


# --------------------------------------------------------------------------
# The names a harness root holds
# --------------------------------------------------------------------------


def test_workflow_names_reports_every_definition_under_a_root_sorted(harness):
    assert harness_config.workflow_names(harness) == tuple(
        sorted((FIRST["name"], SECOND["name"])))


def test_a_root_with_no_workflows_directory_holds_no_definitions(tmp_path):
    """Its control is the same root once a definition is written into it, so
    the empty answer is a fact about the root rather than a reader that reports
    nothing whatever it is given."""
    bare = tmp_path / "bare"
    bare.mkdir()
    assert harness_config.workflow_names(bare) == ()

    conftest.materialize_workflow(FIRST, bare)
    assert harness_config.workflow_names(bare) == (FIRST["name"],)


def test_the_names_reported_are_the_names_load_workflow_takes(harness):
    """Otherwise a refusal could list names that cannot be asked for."""
    for name in harness_config.workflow_names(harness):
        assert harness_config.load_workflow(harness, name, {})["name"] == name


# --------------------------------------------------------------------------
# The refusal for a name no definition answers
# --------------------------------------------------------------------------


def test_loading_a_name_with_no_definition_raises_naming_both_halves(harness):
    """The name asked for and the names the harness holds, so the refusal is
    actionable without listing the directory."""
    with pytest.raises(harness_config.UnknownWorkflow) as excinfo:
        harness_config.load_workflow(harness, UNDEFINED, {})

    unknown = excinfo.value
    assert unknown.workflow == UNDEFINED
    assert unknown.problems
    reported = " ".join(unknown.problems)
    assert UNDEFINED in reported
    for name in harness_config.workflow_names(harness):
        assert name in reported, name


def test_a_defined_name_raises_nothing(harness):
    """The control for the refusal above: the same root and the same call,
    differing only in the name asked for."""
    assert harness_config.load_workflow(
        harness, FIRST["name"], {})["name"] == FIRST["name"]


def test_the_refusal_from_a_root_holding_nothing_says_so(tmp_path):
    empty = tmp_path / "empty"
    (empty / "workflows").mkdir(parents=True)

    with pytest.raises(harness_config.UnknownWorkflow) as excinfo:
        harness_config.load_workflow(empty, UNDEFINED, {})

    assert "no workflow definitions" in " ".join(excinfo.value.problems)


def test_load_workflow_keeps_the_signature_every_caller_uses():
    """The refusal is raised where the definition cannot be read, so no caller
    had to change to gain it — including the historical callers recovered under
    `tests/history-fixtures/`."""
    assert list(inspect.signature(harness_config.load_workflow).parameters) == [
        "harness_root", "name", "config"]
