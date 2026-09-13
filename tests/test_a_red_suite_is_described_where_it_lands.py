"""A red declared suite run is described, in every prompt, where it lands.

`tests/test_a_red_suite_reaches_the_verifier.py` holds what the coordinator
*does* with a red declared suite run: it records the outstanding failure,
appends the carry-forward line and advances, so no stage is returned to itself
over it and the verdict decides where the work goes. This module holds what the
prompts *say* about it, which is a separate thing and was wrong in three places
after that behaviour changed — a prompt is an instruction to an agent, so a
prompt that still describes the removed self-route buys a retry, an archived
attempt and a retry-history entry where it promised none of those would move.

Three rules, each derived from the shipped definitions under `workflows/`:

  * no prompt any shipped stage names says a red suite returns that stage to
    itself;
  * every prompt named by a stage that judges a run — in a workflow that
    declares a suite run at all — states what a non-zero exit in the injected
    suite record means: a failing verdict rather than a finding, a failed
    verdict, a recommended retry, and a target read off the injected routing
    table;
  * the self-route injection slots agree with one another, so the prompt that
    diverged is reported rather than read as one workflow's own wording.

The shipped prompts and definitions are this module's **subject**, not an input
to it. "What this harness deploys says the same thing to every workflow" is not
a claim any constructed definition could carry, and the defect being held is one
workflow having been told something its sibling was not — so a check written per
workflow would reproduce the failure mode it exists to prevent. The mapping is
therefore derived: which prompts are read, and which of them are held to the
judging rule, come off the definitions. No prompt filename, no stage name and no
workflow name is written into the rules, which
`test_the_rules_name_no_prompt_no_stage_and_no_workflow` asserts by reading
their own source — so a third workflow shipping a judging prompt without the
paragraph is reported with no edit here.

Every rule is asserted twice, following `tests/test_prompt_workflow_ownership.py`:
once over the shipped arrangement, where it must report nothing, and once over
an arrangement constructed under a temporary directory that breaks exactly that
rule, where it must report. Two of those controls are the superseded wordings
themselves, written out in this module rather than read out of history, so
"the corrections are in place" is held by the assertions rather than by the
edits alone.

Nothing here invokes a model, runs a subprocess, or reads a clock.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import harness_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"
WORKFLOWS = REPO_ROOT / "workflows"

#: The kinds of problem the three rules report. Each is the subject of one
#: shipped assertion and of one constructed control.
SELF_ROUTED = "self-routed"
UNSTATED = "unstated"
DIVERGED = "diverged"

#: The declaration keys the roles are read off. These are workflow *vocabulary*
#: rather than the name of any stage, prompt or workflow, which is why deriving
#: from them leaves a third workflow covered on arrival.
SUITE_RUN = "suite_run"
ROUTING = "retry_routing"

#: The placeholder the coordinator substitutes the self-route record into. A
#: template variable, not an artifact or a stage: every prompt carrying the slot
#: carries this same name, so the slots can be found without naming a file.
SLOT = "{{self_route_result}}"


# --------------------------------------------------------------------------
# Reading the arrangement
# --------------------------------------------------------------------------


def declares(stage: dict, key: str) -> bool:
    """Whether a stage declaration carries `key` anywhere within it.

    Walked rather than looked up at the top level, because a workflow is free
    to nest a declaration — the routing table hangs off the failure block — and
    where it hangs is not what any rule here is about.
    """
    if key in stage:
        return True
    for value in stage.values():
        if isinstance(value, dict) and declares(value, key):
            return True
        if isinstance(value, list) and any(
                isinstance(item, dict) and declares(item, key) for item in value):
            return True
    return False


def roles(definitions: dict[str, dict]) -> dict[str, bool]:
    """Prompt filename -> whether it is held to the judging rule.

    True for a prompt named by a stage that declares a routing table in a
    definition that declares a suite run somewhere among its stages: that is
    the stage a carried-forward failure arrives at, and the only stage whose
    verdict decides what happens to it. A prompt shared by more than one
    definition is held to the rule if any of them puts it in that position.
    """
    held: dict[str, bool] = {}
    for definition in definitions.values():
        stages = definition.get("stages", [])
        runs_a_suite = any(declares(stage, SUITE_RUN) for stage in stages)
        for stage in stages:
            if "prompt" not in stage:
                continue
            judges = runs_a_suite and declares(stage, ROUTING)
            held[stage["prompt"]] = held.get(stage["prompt"], False) or judges
    return held


def read_prompts(prompts_dir: Path, held: dict[str, bool]) -> dict[str, str]:
    return {prompt: (prompts_dir / prompt).read_text(encoding="utf-8")
            for prompt in sorted(held)}


def sentences(text: str) -> list[str]:
    """The text as sentences, each with its line wrapping collapsed.

    Sentence-wise rather than file-wise so that a red suite mentioned in one
    paragraph and a stage running again in place mentioned in another are not
    read as one claim about the two together.
    """
    flat = re.sub(r"\s+", " ", text)
    return [part.strip() for part in re.split(r"(?<=\.)\s+", flat) if part.strip()]


# --------------------------------------------------------------------------
# Rule one: no prompt says a red suite returns a stage to itself
# --------------------------------------------------------------------------

#: Affirmative ways of saying a stage runs again where it stands. Matched only
#: beside a red suite: on its own, every one of these is the true sentence about
#: a stage that failed mechanically, which several prompts carry.
IN_PLACE = (
    "back in place",
    "again in place",
    "on the same attempt",
    "re-invoked",
    "brings this stage back",
    "brings that stage back",
    "returns to this stage",
)

#: A sentence that denies the behaviour is describing it correctly. The limit is
#: deliberate and narrow: it costs a sentence that both negates and asserts,
#: which no prompt has reason to be, and it buys a rule that does not report the
#: sentence saying the thing does not happen.
DENIES = re.compile(r"\b(?:not|never|no longer|rather than)\b")


def a_red_suite_returning_a_stage_to_itself(
        prompts: dict[str, str]) -> list[str]:
    """Every sentence tying a red suite to a stage running again in place."""
    problems = []
    for prompt, text in sorted(prompts.items()):
        for sentence in sentences(text):
            if "suite" not in sentence or not re.search(r"\bred\b", sentence):
                continue
            if DENIES.search(sentence):
                continue
            said = [phrase for phrase in IN_PLACE if phrase in sentence]
            if said:
                problems.append(
                    f"{SELF_ROUTED}: {prompt} says a red suite returns the "
                    f"stage to itself — {said[0]!r} in: {sentence}")
    return problems


# --------------------------------------------------------------------------
# Rule two: a judging prompt says what a non-zero suite exit means
# --------------------------------------------------------------------------

#: The substance the paragraph must carry, one entry per thing it has to say.
#: Phrases rather than a whole paragraph, because the two prompts holding it
#: describe the run itself differently — one as made in the tree the change
#: left, one as made after the authoring stage's turn — and neither wording is
#: the rule.
FAILING_VERDICT = (
    "non-zero exit code",
    "failing verdict",
    "not a finding",
    "the verdict is failed",
    "a retry is recommended",
    "routing table",
)


def a_judging_prompt_not_stating_the_failing_verdict(
        prompts: dict[str, str], held: dict[str, bool]) -> list[str]:
    problems = []
    for prompt, judges in sorted(held.items()):
        if not judges:
            continue
        text = re.sub(r"\s+", " ", prompts[prompt])
        missing = [phrase for phrase in FAILING_VERDICT if phrase not in text]
        if missing:
            problems.append(
                f"{UNSTATED}: {prompt} judges a run whose suite may come back "
                f"red and does not say what a non-zero exit means — missing "
                f"{', '.join(repr(phrase) for phrase in missing)}")
    return problems


# --------------------------------------------------------------------------
# Rule three: the self-route injection slots agree
# --------------------------------------------------------------------------


def slot_wording(text: str) -> str | None:
    """The paragraph introducing the self-route slot, wrapping collapsed.

    None when the prompt carries no slot, which is not a fault: a stage with no
    budget to run again in place has nothing to be handed.
    """
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if SLOT in line), None)
    if index is None:
        return None
    paragraph: list[str] = []
    for line in reversed(lines[:index]):
        if not line.strip():
            break
        paragraph.append(line.strip())
    return re.sub(r"\s+", " ", " ".join(reversed(paragraph)))


def slots_that_disagree(prompts: dict[str, str]) -> list[str]:
    """Every slot wording that is not the one most of the prompts carry."""
    wordings = {prompt: slot_wording(text)
                for prompt, text in sorted(prompts.items())}
    carried = {prompt: wording for prompt, wording in wordings.items()
               if wording is not None}
    if len(carried) < 2:
        return []

    counted: dict[str, int] = {}
    for wording in carried.values():
        counted[wording] = counted.get(wording, 0) + 1
    agreed = max(counted, key=lambda wording: (counted[wording], wording))

    return [f"{DIVERGED}: {prompt} introduces the injected self-route record "
            f"in wording no other prompt carries: {wording}"
            for prompt, wording in carried.items() if wording != agreed]


def kinds(problems: list[str]) -> list[str]:
    return [problem.split(":", 1)[0] for problem in problems]


# --------------------------------------------------------------------------
# What this repository ships
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def definitions() -> dict[str, dict]:
    names = harness_config.workflow_names(REPO_ROOT)
    assert names, "this repository ships no workflow definition to read"
    return {name: json.loads(
        (WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))
        for name in names}


@pytest.fixture(scope="module")
def held(definitions) -> dict[str, bool]:
    return roles(definitions)


@pytest.fixture(scope="module")
def shipped(held) -> dict[str, str]:
    return read_prompts(PROMPTS, held)


def test_the_shipped_arrangement_is_worth_checking(definitions, held, shipped):
    """The non-vacuity guard for the three shipped assertions below.

    Each rule discriminates only where there is something to discriminate: a
    prompt held to the judging rule, a prompt not held to it, and more than one
    slot to compare. If this repository ever held none of those, the shipped
    assertions would pass by having nothing to look at.
    """
    assert len(definitions) > 1, \
        "one definition cannot show two workflows being told the same thing"
    assert [prompt for prompt, judges in held.items() if judges]
    assert [prompt for prompt, judges in held.items() if not judges]
    assert all(text.strip() for text in shipped.values())
    assert len([text for text in shipped.values()
                if slot_wording(text) is not None]) > 1
    # And every definition really does declare a suite run somewhere, which is
    # what puts its judging stage in the position rule two is about.
    assert all(any(declares(stage, SUITE_RUN) for stage in d["stages"])
               for d in definitions.values())


def test_no_shipped_prompt_says_a_red_suite_returns_that_stage_to_itself(
    shipped,
):
    assert a_red_suite_returning_a_stage_to_itself(shipped) == []


def test_every_shipped_judging_prompt_says_what_a_non_zero_suite_exit_means(
    shipped, held,
):
    assert a_judging_prompt_not_stating_the_failing_verdict(shipped, held) == []


def test_the_shipped_self_route_slots_agree_with_one_another(shipped):
    assert slots_that_disagree(shipped) == []


# --------------------------------------------------------------------------
# The controls
#
# Each builds a prompts directory and a set of definitions under a temporary
# directory and puts them to exactly the functions above. The shipped tree is
# never edited to make a control.
# --------------------------------------------------------------------------

#: A constructed pair of definitions: each declares a suite run on the stage
#: that writes, and a routing table on the stage that judges. The names are the
#: fixture's own, and the rules derive from them exactly as they derive from the
#: shipped ones.
def built_definitions() -> dict[str, dict]:
    return {
        "alpha-workflow": {
            "stages": [
                {"name": "alpha-writer", "prompt": "alpha-writer.md",
                 SUITE_RUN: {"result": "a-suite-record.json"}},
                {"name": "alpha-judge", "prompt": "alpha-judge.md",
                 "on_failure": {ROUTING: {"a-category": {
                     "stage": "alpha-writer", "when": "the work is wrong"}}}},
            ]},
        "beta-workflow": {
            "stages": [
                {"name": "beta-writer", "prompt": "beta-writer.md",
                 SUITE_RUN: {"result": "a-suite-record.json"}},
                {"name": "beta-judge", "prompt": "beta-judge.md",
                 "on_failure": {ROUTING: {"a-category": {
                     "stage": "beta-writer", "when": "the work is wrong"}}}},
            ]},
    }


#: A slot paragraph a prompt may carry, and the failing-verdict paragraph a
#: judging prompt must. Written in the fixture's own words rather than copied
#: out of a shipped prompt, so that these controls say what the rules accept
#: rather than what one file happens to contain.
BUILT_SLOT = ("Self-route result — present only when this stage is running "
              "again in place\nafter failing mechanically:\n" + SLOT + "\n")

BUILT_VERDICT = (
    "A non-zero exit code in that record is a failing verdict and not a "
    "finding,\nso the verdict is failed, a retry is recommended, and the "
    "retry target is\nthe category the injected routing table gives to the "
    "defect you judge\ncaused it.\n")


def built_prompts(tmp_path: Path, texts: dict[str, str]) -> Path:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name, text in texts.items():
        (prompts / name).write_text(text, encoding="utf-8")
    return prompts


def arrangement(tmp_path: Path, **overrides: str):
    """A whole arrangement the three rules accept, with named prompts replaced.

    Returns the prompt texts and the derived roles, read back off the built
    definitions and the built directory by exactly the code the shipped
    assertions use — so a control differs from the shipped arrangement in what
    the prompts say and in nothing else.
    """
    held = roles(built_definitions())
    texts = {}
    for prompt, judges in held.items():
        body = f"A template.\n\n{BUILT_VERDICT if judges else ''}\n{BUILT_SLOT}"
        texts[prompt] = overrides.get(prompt.removesuffix(".md").replace("-", "_"),
                                      body)
    return read_prompts(built_prompts(tmp_path, texts), held), held


def test_an_arrangement_that_says_the_right_things_reports_nothing(tmp_path):
    """The controls' own control: the constructed shape passes all three rules,
    so each violation below differs from it in one thing."""
    prompts, held = arrangement(tmp_path)

    assert a_red_suite_returning_a_stage_to_itself(prompts) == []
    assert a_judging_prompt_not_stating_the_failing_verdict(prompts, held) == []
    assert slots_that_disagree(prompts) == []


# -- rule one ---------------------------------------------------------------

#: The two superseded wordings this story removed, written out here rather than
#: read out of the commit graph: history is not the subject of this module, and
#: a sentence constructed in the test asserts the same thing with nothing under
#: it that a rebase or a squash could move.
SUPERSEDED_PARAGRAPH = (
    "A red suite brings this stage back in place, on the same attempt, with "
    "the\ncoordinator's record of the run and the path to that run's whole "
    "output\ninjected below. Read the output file rather than reaching for the "
    "suite\nyourself; repair what failed, record what you wrote, and end the "
    "turn.\n")

SUPERSEDED_SLOT = (
    "Self-route result — present only when this stage is running again in "
    "place\nafter failing mechanically, or after the suite the coordinator ran "
    "in your\ntree came back red:\n" + SLOT + "\n")


def test_a_prompt_saying_a_red_suite_hands_the_turn_back_is_reported(tmp_path):
    prompts, _ = arrangement(
        tmp_path,
        alpha_writer=f"A template.\n\n{SUPERSEDED_PARAGRAPH}\n{BUILT_SLOT}")
    problems = a_red_suite_returning_a_stage_to_itself(prompts)

    assert kinds(problems) == [SELF_ROUTED]
    assert "alpha-writer.md" in problems[0]


def test_a_slot_offered_over_a_red_suite_is_reported(tmp_path):
    """The other superseded wording: not a paragraph about the turn but a slot
    promising the record a red suite no longer produces."""
    prompts, _ = arrangement(
        tmp_path, alpha_writer=f"A template.\n\n{SUPERSEDED_SLOT}")
    problems = a_red_suite_returning_a_stage_to_itself(prompts)

    assert kinds(problems) == [SELF_ROUTED]
    assert "alpha-writer.md" in problems[0]


def test_saying_a_red_suite_does_not_hand_the_turn_back_is_not_reported(
    tmp_path,
):
    """The rule reads a claim rather than a pair of words: a prompt that denies
    the removed behaviour is describing it correctly and is left alone. Beside
    the two controls above, this is what separates the rule from a search for
    'red' and 'in place' in one sentence."""
    denial = ("A red suite does not bring this stage back in place on the same "
              "attempt.\nThe run advances and the stage that judges it decides "
              "where the work goes.\n")
    prompts, _ = arrangement(
        tmp_path, alpha_writer=f"A template.\n\n{denial}\n{BUILT_SLOT}")

    assert a_red_suite_returning_a_stage_to_itself(prompts) == []


def test_the_rule_reads_each_sentence_on_its_own(tmp_path):
    """A prompt mentioning a red suite in one paragraph and a stage running
    again in place in another says nothing about the two together, and is not
    reported for having both somewhere in it."""
    apart = ("The coordinator records the outstanding failure when the suite "
             "comes back red.\n\nThis stage may be asked to run again in place "
             "after failing mechanically.\n")
    prompts, _ = arrangement(
        tmp_path, alpha_writer=f"A template.\n\n{apart}\n{BUILT_SLOT}")

    assert a_red_suite_returning_a_stage_to_itself(prompts) == []


# -- rule two ---------------------------------------------------------------


def test_a_judging_prompt_without_the_failing_verdict_rule_is_reported(
    tmp_path,
):
    """The exposure this story closed: a stage that judges a run whose suite
    may come back red, whose prompt says nothing about what a non-zero exit
    means, so its agent is entitled to record the failures as findings on a
    passing verdict and the run stops at the end-of-run refusal."""
    prompts, held = arrangement(
        tmp_path, beta_judge=f"A template.\n\n{BUILT_SLOT}")
    problems = a_judging_prompt_not_stating_the_failing_verdict(prompts, held)

    assert kinds(problems) == [UNSTATED]
    assert "beta-judge.md" in problems[0]


def test_a_judging_prompt_stating_only_half_the_rule_is_reported(tmp_path):
    """Half is not the rule: a prompt calling a non-zero exit a failing verdict
    and saying nothing about the retry leaves the agent to invent a route."""
    half = ("A non-zero exit code in that record is a failing verdict and not "
            "a finding.\n")
    prompts, held = arrangement(
        tmp_path, beta_judge=f"A template.\n\n{half}\n{BUILT_SLOT}")
    problems = a_judging_prompt_not_stating_the_failing_verdict(prompts, held)

    assert kinds(problems) == [UNSTATED]
    assert "the verdict is failed" in problems[0]
    assert "a retry is recommended" in problems[0]


def test_a_prompt_that_judges_nothing_is_outside_the_rule(tmp_path):
    """The rule keys on the role the definition gives the stage, so the same
    text that is reported in a judging prompt is not reported in a writing one.
    Without this, the rule would be "every prompt carries the paragraph", which
    is not what any workflow needs."""
    prompts, held = arrangement(
        tmp_path, alpha_writer=f"A template.\n\n{BUILT_SLOT}")

    assert a_judging_prompt_not_stating_the_failing_verdict(prompts, held) == []


def test_a_workflow_declaring_no_suite_run_puts_its_judge_outside_the_rule():
    """Derived all the way down: a definition whose stages declare no suite run
    has no red suite to carry anywhere, so its judging stage is not held to a
    paragraph about one — and the same definition with a suite run declared is.
    """
    definitions = built_definitions()
    stage = definitions["alpha-workflow"]["stages"][0]

    assert roles(definitions)["alpha-judge.md"] is True
    without = json.loads(json.dumps(definitions))
    without["alpha-workflow"]["stages"][0].pop(SUITE_RUN)
    assert roles({"alpha-workflow": without["alpha-workflow"]})[
        "alpha-judge.md"] is False
    assert SUITE_RUN in stage  # the arrangement above really declared one


# -- rule three -------------------------------------------------------------


def test_a_slot_worded_unlike_every_other_is_reported(tmp_path):
    """The defect the third rule exists for: one prompt's slot carrying a fact
    its siblings' do not, which reads as that workflow's own wording until
    something compares them."""
    prompts, _ = arrangement(
        tmp_path,
        beta_writer=f"A template.\n\n{BUILT_VERDICT}\n{SUPERSEDED_SLOT}")
    problems = slots_that_disagree(prompts)

    assert kinds(problems) == [DIVERGED]
    assert "beta-writer.md" in problems[0]
    assert "came back red" in problems[0]


def test_a_prompt_carrying_no_slot_is_not_reported_as_disagreeing(tmp_path):
    """A stage with no budget to run again in place is handed no record, so the
    absence of a slot is not a disagreement about its wording."""
    prompts, _ = arrangement(tmp_path, beta_writer="A template.\n")

    assert slot_wording(prompts["beta-writer.md"]) is None
    assert slots_that_disagree(prompts) == []


# --------------------------------------------------------------------------
# The rules are derived, not restated
# --------------------------------------------------------------------------


def test_the_rules_name_no_prompt_no_stage_and_no_workflow(definitions, held):
    """Grep the deciding code for the names it is about.

    `declares`, `roles`, `sentences`, `slot_wording` and the three rules are
    what a third workflow's arrival would otherwise force an edit to, so none of
    them may contain a shipped prompt filename, a shipped stage name or a
    shipped workflow name. The assertions and controls above are free to name
    what they are about; these are not.

    Docstrings are stripped first: an explanation naming an example is prose
    about the rule, and a rule that could not illustrate itself would be the
    worse outcome.
    """
    import ast
    import inspect

    source = ""
    for function in (declares, roles, sentences, slot_wording,
                     a_red_suite_returning_a_stage_to_itself,
                     a_judging_prompt_not_stating_the_failing_verdict,
                     slots_that_disagree):
        tree = ast.parse(inspect.getsource(function))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
        source += ast.unparse(tree)

    for prompt in sorted(held):
        assert prompt not in source, prompt
        assert prompt.removesuffix(".md") not in source, prompt
    for workflow, definition in sorted(definitions.items()):
        assert workflow not in source, workflow
        for stage in definition["stages"]:
            assert f'"{stage["name"]}"' not in source, stage["name"]
            assert f"'{stage['name']}'" not in source, stage["name"]
