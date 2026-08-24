"""A prompt's filename says which workflow owns it.

The convention, recorded in `.harness/docs/ARCHITECTURE.md` since story-070:
a prompt prefixed with a workflow's name belongs to that workflow, and an
unprefixed prompt is shared by every workflow that names it. Being unprefixed
is a claim of shared ownership rather than an absence of one, which is why
`documenter.md` carries no prefix and `story-implementer.md` does.

It was prose alone until this module. This repository has twice recorded an
injected written rule failing to hold — the `git diff HEAD` baseline rule and
the git-history loader each shipped several more times after being written
down — so what keeps a convention true here is a check rather than a
paragraph.

The subject is what this repository *ships*: the files under `prompts/` and
the definitions under `workflows/`. That is the exception the fixture rule
states rather than a departure from it. An assertion about a mechanism builds
the workflow it needs; an assertion about what this harness deploys has to
read what it deploys, and "the shipped prompts are named for the shipped
workflows that name them" is not a claim any built definition could carry.

The mapping is **derived** from the definitions rather than restated here: no
prompt filename, no stage name and no workflow prefix is written into the
rules below, so a third workflow arriving with prompts of its own is checked
by this module with no edit to it.

Every rule is asserted twice — once over the shipped arrangement, where it
must report nothing, and once over an arrangement constructed in a temporary
directory that breaks exactly that rule, where it must report. Without the
second half, "the shipped prompts are named correctly" would pass just as
happily against a check that had stopped looking.

Nothing here invokes a model, and nothing here reads this repository's commit
graph.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"
WORKFLOWS = REPO_ROOT / "workflows"


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------

#: The four kinds of problem the convention can be broken in. Each is the
#: subject of one assertion below and of one constructed control.
ABSENT = "absent"
UNPREFIXED = "unprefixed"
MISPREFIXED = "misprefixed"
SHARED = "shared"


def workflow_prefix(workflow: str) -> str:
    """The filename prefix a workflow's own prompts carry.

    Derived from the definition's own name: `story-workflow` owns
    `story-implementer.md` and `refactor-workflow` owns
    `refactor-verifier.md`. Written this way rather than as a mapping so that
    a definition this repository does not yet ship is covered on arrival.
    """
    return workflow.removesuffix("-workflow")


def prompts_named_by(definition: dict) -> set[str]:
    """Every prompt filename a loaded workflow definition's stages name."""
    return {stage["prompt"] for stage in definition.get("stages", [])
            if "prompt" in stage}


def owners(named: dict[str, set[str]]) -> dict[str, set[str]]:
    """Prompt filename -> the workflows naming it, inverted from `named`."""
    inverted: dict[str, set[str]] = {}
    for workflow, prompts in named.items():
        for prompt in prompts:
            inverted.setdefault(prompt, set()).add(workflow)
    return inverted


def ownership_problems(prompts_dir: Path,
                       named: dict[str, set[str]]) -> list[str]:
    """Every way the arrangement breaks the convention, one problem per line.

    `named` maps a workflow's name to the prompt filenames its stages name;
    `prompts_dir` is the directory those filenames are resolved in. Both are
    parameters so the shipped arrangement and a constructed one go through
    exactly this code, and a control is a statement about the rule rather
    than about the tree it happens to be run over.
    """
    prefixes = sorted({workflow_prefix(workflow) for workflow in named})
    problems = []

    for prompt, workflows in sorted(owners(named).items()):
        naming = ", ".join(sorted(workflows))
        if not (prompts_dir / prompt).is_file():
            problems.append(
                f"{ABSENT}: {prompt} is named by {naming} and is not a file "
                f"under {prompts_dir.name}/")
            continue

        carried = next((prefix for prefix in prefixes
                        if prompt.startswith(f"{prefix}-")), None)

        if len(workflows) == 1:
            wanted = workflow_prefix(next(iter(workflows)))
            if carried is None:
                problems.append(
                    f"{UNPREFIXED}: {prompt} is named by {naming} alone, so "
                    f"its filename must begin '{wanted}-'")
            elif carried != wanted:
                problems.append(
                    f"{MISPREFIXED}: {prompt} is named by {naming} alone, so "
                    f"its filename must begin '{wanted}-' and it begins "
                    f"'{carried}-'")
        elif carried is not None:
            problems.append(
                f"{SHARED}: {prompt} is named by {naming}, so its filename "
                f"must carry no workflow prefix, and it begins '{carried}-'")

    return problems


def kinds(problems: list[str]) -> list[str]:
    return [problem.split(":", 1)[0] for problem in problems]


# --------------------------------------------------------------------------
# What this repository ships
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shipped() -> dict[str, set[str]]:
    """Each shipped definition's name, mapped to the prompts its stages name.

    Read as JSON rather than through `load_workflow` because the only values
    wanted are the `prompt` strings, which carry no configuration reference,
    so nothing here needs a target's config to resolve.
    """
    names = harness_config.workflow_names(REPO_ROOT)
    assert names, "this repository ships no workflow definition to read"
    return {
        name: prompts_named_by(
            json.loads((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8")))
        for name in names
    }


def test_the_shipped_arrangement_is_worth_checking(shipped):
    """The non-vacuity guard for every assertion below.

    The rules discriminate only where there is something to discriminate: a
    prompt exactly one workflow names, and a prompt more than one names. If
    this repository ever held neither, three of the four checks would pass by
    having no case to look at, and the shipped assertions would say nothing.
    """
    assert len(shipped) > 1, "one workflow cannot demonstrate shared ownership"
    counted = owners(shipped)
    assert [p for p, w in counted.items() if len(w) == 1], \
        "no prompt is named by exactly one workflow"
    assert [p for p, w in counted.items() if len(w) > 1], \
        "no prompt is named by more than one workflow"


def test_every_shipped_prompt_a_workflow_names_is_named_as_the_convention_says(
    shipped,
):
    assert ownership_problems(PROMPTS, shipped) == []


def test_the_prompts_no_workflow_names_are_outside_the_rule(shipped):
    """The convention keys on being *named* by a workflow, so a prompt no
    workflow names is neither owned nor shared and carries no prefix.

    `planner.md` is the case: the planner is not a workflow stage at all. The
    shared partials are the others. Asserted so that a later reading of the
    rule as "every file under prompts/ must be prefixed" is contradicted by a
    test rather than only by a paragraph.
    """
    named = set(owners(shipped))
    unnamed = {path.name for path in PROMPTS.glob("*.md")} - named
    assert unnamed, "every shipped prompt is named by a workflow"

    prefixes = tuple(f"{workflow_prefix(w)}-" for w in shipped)
    assert [name for name in sorted(unnamed) if name.startswith(prefixes)] == []


# --------------------------------------------------------------------------
# The controls
#
# Each builds a `prompts/` directory and a set of workflow definitions in a
# temporary directory and puts them to the same `ownership_problems`. The
# shipped tree is never edited to make a control.
# --------------------------------------------------------------------------


def arrangement(tmp_path: Path, named: dict[str, set[str]],
                present: set[str] | None = None) -> Path:
    """A prompts directory holding every named prompt, or a chosen subset."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for prompt in sorted(present if present is not None else set(owners(named))):
        (prompts / prompt).write_text("a template\n", encoding="utf-8")
    return prompts


def test_an_arrangement_that_respects_the_convention_reports_nothing(tmp_path):
    """The control's own control: the constructed shape is one the rule
    accepts, so each violation below differs from it in one thing."""
    named = {"alpha-workflow": {"alpha-writer.md", "shared.md"},
             "beta-workflow": {"beta-writer.md", "shared.md"}}
    assert ownership_problems(arrangement(tmp_path, named), named) == []


def test_a_single_workflow_prompt_with_no_prefix_is_reported(tmp_path):
    named = {"alpha-workflow": {"writer.md", "shared.md"},
             "beta-workflow": {"beta-writer.md", "shared.md"}}
    problems = ownership_problems(arrangement(tmp_path, named), named)

    assert kinds(problems) == [UNPREFIXED]
    assert "writer.md" in problems[0]
    assert "'alpha-'" in problems[0]


def test_a_prefixed_prompt_named_by_another_workflow_is_reported(tmp_path):
    """`beta-writer.md` named by `alpha-workflow` alone: it carries a prefix,
    and the prefix is another workflow's, which is the case a bare
    "does it carry any prefix" test would let through."""
    named = {"alpha-workflow": {"beta-writer.md", "shared.md"},
             "beta-workflow": {"beta-other.md", "shared.md"}}
    problems = ownership_problems(arrangement(tmp_path, named), named)

    assert kinds(problems) == [MISPREFIXED]
    assert "beta-writer.md" in problems[0]
    assert "'alpha-'" in problems[0]
    assert "'beta-'" in problems[0]


def test_a_shared_prompt_carrying_a_prefix_is_reported(tmp_path):
    named = {"alpha-workflow": {"alpha-writer.md", "alpha-shared.md"},
             "beta-workflow": {"beta-writer.md", "alpha-shared.md"}}
    problems = ownership_problems(arrangement(tmp_path, named), named)

    assert kinds(problems) == [SHARED]
    assert "alpha-shared.md" in problems[0]
    assert "alpha-workflow, beta-workflow" in problems[0]


def test_a_workflow_naming_a_prompt_that_is_absent_is_reported(tmp_path):
    named = {"alpha-workflow": {"alpha-writer.md", "shared.md"},
             "beta-workflow": {"beta-writer.md", "shared.md"}}
    prompts = arrangement(tmp_path, named,
                          present={"alpha-writer.md", "shared.md"})
    problems = ownership_problems(prompts, named)

    assert kinds(problems) == [ABSENT]
    assert "beta-writer.md" in problems[0]


def test_an_absent_prompt_is_reported_once_and_not_also_as_a_naming_fault(
    tmp_path,
):
    """A file that is not there cannot be judged on its prefix, so the absence
    is the whole finding. Without this, a badly named missing prompt would be
    reported twice and a reader would repair the name and still be refused."""
    named = {"alpha-workflow": {"writer.md"},
             "beta-workflow": {"beta-writer.md"}}
    prompts = arrangement(tmp_path, named, present={"beta-writer.md"})
    problems = ownership_problems(prompts, named)

    assert kinds(problems) == [ABSENT]


# --------------------------------------------------------------------------
# The mapping is derived, not restated
# --------------------------------------------------------------------------


def test_the_rules_name_no_prompt_no_stage_and_no_workflow(shipped):
    """Grep the rule's own source for the names it is about.

    `ownership_problems`, `workflow_prefix`, `prompts_named_by` and `owners`
    are what a third workflow's arrival would otherwise force an edit to, so
    they must contain no shipped prompt filename, no shipped stage name and
    no shipped workflow name. The assertions and controls above are free to
    name whatever they are about; these four functions are not.

    Docstrings are stripped before the search: an explanation naming an
    example is prose about the rule, and a rule that could not illustrate
    itself would be the worse outcome. What must not name a shipped file is
    the code that decides.
    """
    import ast
    import inspect

    source = ""
    for function in (workflow_prefix, prompts_named_by, owners,
                     ownership_problems):
        tree = ast.parse(inspect.getsource(function))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
        source += ast.unparse(tree)

    for prompt in sorted(owners(shipped)):
        assert prompt not in source, prompt
    for workflow in sorted(shipped):
        assert workflow not in source, workflow
        assert f"{workflow_prefix(workflow)}-" not in source, workflow

    definitions = [json.loads((WORKFLOWS / f"{name}.json").read_text(
        encoding="utf-8")) for name in sorted(shipped)]
    for definition in definitions:
        for stage in definition["stages"]:
            assert f'"{stage["name"]}"' not in source, stage["name"]
