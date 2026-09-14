"""A red declared suite run is described, in every prompt, where it lands.

`tests/test_a_red_suite_reaches_the_verifier.py` holds what the coordinator
*does* with a red declared suite run: it records the outstanding failure,
appends the carry-forward line and advances, so no stage is returned to itself
over it and the verdict decides where the work goes. This module holds what the
prompts *say* about it, which is a separate thing and was wrong in three places
after that behaviour changed — a prompt is an instruction to an agent, so a
prompt that still describes the removed self-route buys a retry, an archived
attempt and a retry-history entry where it promised none of those would move.

The rules below, each derived from the shipped definitions under `workflows/`:

  * no prompt any shipped stage names says a red suite returns that stage to
    itself;
  * every prompt named by a stage that judges a run — in a workflow that
    declares a suite run at all — states what a non-zero exit in the injected
    suite record means: a failing verdict rather than a finding, a failed
    verdict, a recommended retry, and a target read off the injected routing
    table;
  * every prompt named by a stage that *declares* the suite run states what
    follows a red one: the failure recorded, the run advanced, the turn not
    handed back on this attempt, and the cost — a failed verdict, a recommended
    retry, an archived attempt and a retry-history entry;
  * the self-route injection slots agree with one another, so the prompt that
    diverged is reported rather than read as one workflow's own wording.

Those middle two rules decide over a **passage** and not over the file: a
prompt satisfies one when some single paragraph of it carries every claim, and
what a rule reports is the paragraph that came closest together with what that
paragraph does not say. Deciding over the file asks only whether each accepted
phrasing appears somewhere, which prose written about something else answers —
a sentence about an out-of-confinement edit says "the run carries on" and a
claim about a red suite is met by it, so a prompt can lose the paragraph
entirely and still be reported against by nothing. Reading a passage buys back
the thing the rules exist for: a claim met by prose about something else no
longer answers for a paragraph that is not there. What it costs is that a
prompt saying every one of these things, spread over two paragraphs, is
reported — which is asserted below rather than left to be discovered, because
the remedy for it is one paragraph and the remedy for the alternative is
nothing at all.

Rule one keeps the narrower reading it was deliberately given: it decides over
a sentence, because a red suite mentioned in one paragraph and a stage running
again in place mentioned in another are not one claim about the two together.

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
from typing import NamedTuple

import pytest

import harness_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"
WORKFLOWS = REPO_ROOT / "workflows"

#: The kinds of problem the rules report. Each is the subject of one shipped
#: assertion and of one constructed control.
SELF_ROUTED = "self-routed"
UNSTATED = "unstated"
SILENT = "silent"
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


class Role(NamedTuple):
    """The two positions a prompt can occupy around a declared suite run.

    `judges` is where a carried-forward failure arrives: a stage declaring a
    routing table, in a definition that declares a suite run somewhere among its
    stages. `authors` is where it starts: a stage that declares the suite run
    itself, and so the stage whose tree the run is made in. Both are positions
    a definition puts a prompt in, which is why neither is read off a name.
    """

    judges: bool
    authors: bool


def roles(definitions: dict[str, dict]) -> dict[str, Role]:
    """Prompt filename -> the positions the shipped definitions give it.

    A prompt shared by more than one definition holds a position if any of them
    puts it in that position.
    """
    held: dict[str, Role] = {}
    for definition in definitions.values():
        stages = definition.get("stages", [])
        runs_a_suite = any(declares(stage, SUITE_RUN) for stage in stages)
        for stage in stages:
            if "prompt" not in stage:
                continue
            judges = runs_a_suite and declares(stage, ROUTING)
            authors = declares(stage, SUITE_RUN)
            was = held.get(stage["prompt"], Role(False, False))
            held[stage["prompt"]] = Role(was.judges or judges,
                                         was.authors or authors)
    return held


def read_prompts(prompts_dir: Path, held: dict[str, Role]) -> dict[str, str]:
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


def paragraphs(text: str) -> list[str]:
    """The text as passages: each blank-line-separated block, wrapping collapsed.

    The same shape as `sentences` because it answers the same kind of question
    at a different width — a rule that asks a passage asks what one paragraph
    says, rather than what the file contains somewhere.
    """
    blocks = [re.sub(r"\s+", " ", block).strip()
              for block in re.split(r"\n\s*\n", text)]
    return [block for block in blocks if block]


#: The two halves of a passage-wise report, and what separates them. A report
#: names what the judged paragraph does not carry and then quotes the paragraph,
#: so the quote necessarily contains every claim that paragraph *does* meet.
#: "This claim is not reported against it" is therefore a statement about the
#: half before the quote, which `unmet_half` is how a control takes.
JUDGED = " — the paragraph judged was: "


def unmet_half(problem: str) -> str:
    """What a report says is missing, without the paragraph it quotes after."""
    return problem.split(JUDGED)[0]


def closest_passage(text: str, claims, met) -> tuple[str, list]:
    """The paragraph meeting the most claims, and the claims it does not meet.

    `met(passage, claim)` decides one claim against one passage, so a rule keeps
    its own idea of what satisfies a claim and shares only the search. Ties go
    to the earlier paragraph, so a report is the same on every run; a text with
    no paragraph at all answers with the empty passage, which meets nothing and
    is reported rather than passed over.
    """
    judged: str | None = None
    unmet = list(claims)
    for passage in paragraphs(text):
        missing = [claim for claim in claims if not met(passage, claim)]
        if judged is None or len(missing) < len(unmet):
            judged, unmet = passage, missing
    return judged or "", unmet


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
        prompts: dict[str, str], held: dict[str, Role]) -> list[str]:
    """Every judging prompt with no one paragraph carrying the whole rule.

    Decided over a passage: a prompt satisfies this when some single paragraph
    of it carries every phrase, so phrases scattered through prose about other
    things do not answer for the paragraph they were meant to be in.
    """
    problems = []
    for prompt, role in sorted(held.items()):
        if not role.judges:
            continue
        judged, missing = closest_passage(
            prompts[prompt], FAILING_VERDICT,
            lambda passage, phrase: phrase in passage)
        if missing:
            problems.append(
                f"{UNSTATED}: {prompt} judges a run whose suite may come back "
                f"red and no one paragraph of it says what a non-zero exit "
                f"means — missing "
                f"{', '.join(repr(phrase) for phrase in missing)}"
                f"{JUDGED}{judged}")
    return problems


# --------------------------------------------------------------------------
# Rule three: an authoring prompt says what follows a suite it leaves red
# --------------------------------------------------------------------------

#: What a prompt in the authoring position has to say about a suite it leaves
#: red, as claims: a label, and the phrasings any one of which satisfies it. The
#: label is what a failure reports, so a prompt held to this may say the thing
#: in its own vocabulary — the two prompts here describe the same carry-forward
#: from opposite ends, one as a turn that is not repeated and one as a stage
#: that is not brought back, and neither wording is the rule.
#:
#: The claims divide into where the failure goes — recorded, advanced, not
#: handed back — and what it costs, which is everything after those. A prompt
#: stating only where it goes leaves its agent believing a red suite is free,
#: which is the exposure this rule exists for.
CARRY_FORWARD = (
    ("the outstanding failure is recorded", (
        "records the outstanding failure",
        "records the failure",
        "the failure is recorded",
        "the outstanding failure is recorded",
    )),
    ("the run advances past this stage", (
        "the run advances",
        "the run carries on",
        "the run continues",
        "the run moves on",
    )),
    ("the turn is not handed back to repair it", (
        "not handed back",
        "is not repeated on this attempt",
        "does not bring this stage back",
        "does not hand the turn back",
        "is not brought back",
    )),
    ("the verdict is a failed one", (
        "a failed verdict",
        "the verdict is failed",
        "a failing verdict",
    )),
    ("a retry is recommended", (
        "a recommended retry",
        "a retry is recommended",
        "recommends a retry",
    )),
    ("the attempt is archived", (
        "an archived attempt",
        "the attempt archived",
        "the attempt is archived",
    )),
    ("the retry is recorded in the run's history", (
        "retry history",
        "retry-history",
    )),
)


def an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts: dict[str, str], held: dict[str, Role]) -> list[str]:
    """Every prompt whose stage declares the suite run and is silent about red.

    Keyed on the declaration rather than on the prompt, so a third workflow
    declaring a suite run on a stage of its own is held to this on arrival, and
    a prompt no definition puts in that position is left alone.

    Decided over a passage, for the reason the judging rule is: a claim met by a
    sentence about something else — an out-of-confinement edit the run carries
    on without — is not this prompt saying what follows a red suite.
    """
    def met(passage: str, claim) -> bool:
        lowered = passage.lower()
        return any(phrasing in lowered for phrasing in claim[1])

    problems = []
    for prompt, role in sorted(held.items()):
        if not role.authors:
            continue
        judged, missing = closest_passage(prompts[prompt], CARRY_FORWARD, met)
        if missing:
            problems.append(
                f"{SILENT}: {prompt} declares the suite run the coordinator "
                f"makes after its turn and no one paragraph of it says what "
                f"follows a red one — unmet: "
                f"{'; '.join(claim for claim, _ in missing)}"
                f"{JUDGED}{judged}")
    return problems


# --------------------------------------------------------------------------
# Rule four: the self-route injection slots agree
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
def held(definitions) -> dict[str, Role]:
    return roles(definitions)


@pytest.fixture(scope="module")
def shipped(held) -> dict[str, str]:
    return read_prompts(PROMPTS, held)


def test_the_shipped_arrangement_is_worth_checking(definitions, held, shipped):
    """The non-vacuity guard for the shipped assertions below.

    Each rule discriminates only where there is something to discriminate: a
    prompt held to the judging rule and a prompt not held to it, a prompt held
    to the carry-forward rule and a prompt not held to it, and more than one
    slot to compare. If this repository ever held none of those, the shipped
    assertions would pass by having nothing to look at.
    """
    assert len(definitions) > 1, \
        "one definition cannot show two workflows being told the same thing"
    assert [prompt for prompt, role in held.items() if role.judges]
    assert [prompt for prompt, role in held.items() if not role.judges]
    assert [prompt for prompt, role in held.items() if role.authors], \
        "no shipped stage declares the suite run, so the rule holds nothing"
    assert [prompt for prompt, role in held.items() if not role.authors], \
        "every shipped prompt authors, so the rule cannot be shown to exclude"
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


def test_every_shipped_authoring_prompt_says_what_follows_a_red_suite(
    shipped, held,
):
    assert an_authoring_prompt_not_saying_what_follows_a_red_suite(
        shipped, held) == []


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

#: The carry-forward paragraph a prompt in the authoring position must carry:
#: where a red suite goes, and what it costs. Written in the fixture's own words
#: for the same reason as the two above.
BUILT_CARRY_FORWARD = (
    "The coordinator records the outstanding failure and the run advances to "
    "the\nstages after this one; the turn is not repeated on this attempt to "
    "repair it.\nWhat follows is a failed verdict and a recommended retry, "
    "costing the run an\narchived attempt and an entry in its retry history.\n")

#: The same paragraph with its cost removed: where the failure goes, and no more.
BUILT_WITHOUT_THE_COST = (
    "The coordinator records the outstanding failure and the run advances to "
    "the\nstages after this one; the turn is not repeated on this attempt to "
    "repair it.\n")

#: And the cost on its own. The two above and this one carry between them every
#: claim the carry-forward rule holds, so putting these two in separate
#: paragraphs is a prompt that says all of it and satisfies none of it.
BUILT_ONLY_THE_COST = (
    "What follows is a failed verdict and a recommended retry, costing the run "
    "an\narchived attempt and an entry in its retry history.\n")

#: The first sentence of the failing-verdict paragraph, and the rest of it. Same
#: purpose for the judging rule as the pair above serves for the carry-forward
#: one.
BUILT_VERDICT_OPENING = (
    "A non-zero exit code in that record is a failing verdict and not a "
    "finding.\n")

BUILT_VERDICT_REMAINDER = (
    "So the verdict is failed, a retry is recommended, and the retry target "
    "is\nthe category the injected routing table gives to the defect you "
    "judge\ncaused it.\n")

#: Prose about something else that happens to carry an accepted phrasing. The
#: sentence is about an edit the revert check undoes and says nothing about a
#: suite, which is the shape of the defect this reading closes: read file-wise,
#: it answers the claim that the run advances past this stage.
BUILT_UNRELATED_CARRY_FORWARD_PHRASING = (
    "Your own version of an edit made outside the paths this stage owns is "
    "kept\nin the run directory as evidence, and the run carries on without "
    "it.\n")

#: The same shape for the judging rule: two FAILING_VERDICT phrases, in two
#: paragraphs about other things, neither of which says what a non-zero exit
#: means.
BUILT_UNRELATED_VERDICT_PHRASES = (
    "A finding can be correct and still be too small to fail a run, so "
    "recording\none is not a finding you must route on.\n"
    "\n"
    "The injected routing table is what a retry target is read off, for any "
    "defect\nyou judge caused a failure.\n")


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
    for prompt, role in held.items():
        body = (f"A template.\n\n{BUILT_VERDICT if role.judges else ''}\n"
                f"{BUILT_CARRY_FORWARD if role.authors else ''}\n{BUILT_SLOT}")
        texts[prompt] = overrides.get(prompt.removesuffix(".md").replace("-", "_"),
                                      body)
    return read_prompts(built_prompts(tmp_path, texts), held), held


def test_an_arrangement_that_says_the_right_things_reports_nothing(tmp_path):
    """The controls' own control: the constructed shape passes every rule, so
    each violation below differs from it in one thing."""
    prompts, held = arrangement(tmp_path)

    assert a_red_suite_returning_a_stage_to_itself(prompts) == []
    assert a_judging_prompt_not_stating_the_failing_verdict(prompts, held) == []
    assert an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held) == []
    assert slots_that_disagree(prompts) == []


# -- the passage reader -----------------------------------------------------


def test_a_passage_is_a_block_between_blank_lines_with_its_wrapping_collapsed():
    """What the two passage-wise rules are given to weigh.

    A blank line separates passages and a line break inside one does not, so a
    claim made across two wrapped lines is one claim and a claim made in the
    next paragraph is a different passage. Blank blocks are dropped, because a
    run of blank lines is not a passage a prompt can be said to carry.
    """
    text = "One claim\nwrapped over lines.\n\n\nA second\tpassage.\n\n"

    assert paragraphs(text) == ["One claim wrapped over lines.",
                                "A second passage."]
    assert paragraphs("\n  \n\n") == []

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


def test_a_judging_prompt_meeting_phrases_in_unrelated_prose_is_reported(
    tmp_path,
):
    """The defect the passage-wise reading closes, for this rule.

    The prompt carries no failing-verdict paragraph at all. What it carries is
    two of the rule's phrases in prose about other things — a paragraph about a
    finding too small to fail a run, and a paragraph about where a retry target
    is read off — neither of which says what a non-zero suite exit means. Read
    over the file those two phrases are found and are not reported missing;
    read over a passage the paragraph that came closest is weighed on its own,
    so `'routing table'`, which lives in a different paragraph, is reported
    against it.

    The phrase the judged paragraph *does* carry is not reported against it,
    which is the other half of the report being about one passage.
    """
    prompts, held = arrangement(
        tmp_path,
        beta_judge=(f"A template.\n\n{BUILT_UNRELATED_VERDICT_PHRASES}\n"
                    f"{BUILT_SLOT}"))
    problems = a_judging_prompt_not_stating_the_failing_verdict(prompts, held)

    assert kinds(problems) == [UNSTATED]
    assert "beta-judge.md" in problems[0]
    assert "'routing table'" in unmet_half(problems[0])
    assert "'not a finding'" not in unmet_half(problems[0])
    # And the report quotes the paragraph it weighed, so a reader sees which
    # passage was judged rather than only what is absent from it.
    assert problems[0].split(JUDGED)[1] == paragraphs(
        BUILT_UNRELATED_VERDICT_PHRASES)[0]


def test_a_judging_prompt_splitting_the_rule_across_paragraphs_is_reported(
    tmp_path,
):
    """What the passage-wise reading costs, asserted rather than discovered.

    Every phrase the rule holds is in this prompt, and no single paragraph
    carries them all — so the prompt is reported, and the remedy is to put them
    in one paragraph. The two halves meet three phrases each, so the tie is
    broken by document order and the opening paragraph is the one judged.
    """
    split = f"{BUILT_VERDICT_OPENING}\n{BUILT_VERDICT_REMAINDER}"
    prompts, held = arrangement(
        tmp_path, beta_judge=f"A template.\n\n{split}\n{BUILT_SLOT}")
    problems = a_judging_prompt_not_stating_the_failing_verdict(prompts, held)

    # Nothing is absent from the file: this is a claim about where they are.
    flat = re.sub(r"\s+", " ", prompts["beta-judge.md"])
    assert all(phrase in flat for phrase in FAILING_VERDICT)

    assert kinds(problems) == [UNSTATED]
    assert problems[0].split(JUDGED)[1] == paragraphs(BUILT_VERDICT_OPENING)[0]
    assert "'a retry is recommended'" in unmet_half(problems[0])
    assert "'routing table'" in unmet_half(problems[0])
    assert "'not a finding'" not in unmet_half(problems[0])


def test_a_judging_prompt_stating_the_rule_in_one_paragraph_is_not_reported(
    tmp_path,
):
    """The other side of the reading, for this rule: the same phrases that are
    reported when split are not reported when one passage carries them all."""
    together = re.sub(r"\s+", " ",
                      BUILT_VERDICT_OPENING + BUILT_VERDICT_REMAINDER).strip()
    prompts, held = arrangement(
        tmp_path, beta_judge=f"A template.\n\n{together}\n\n{BUILT_SLOT}")

    assert any(all(phrase in passage for phrase in FAILING_VERDICT)
               for passage in paragraphs(prompts["beta-judge.md"])), \
        "the control has to put one paragraph carrying the whole rule"
    assert a_judging_prompt_not_stating_the_failing_verdict(prompts, held) == []


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

    assert roles(definitions)["alpha-judge.md"].judges is True
    without = json.loads(json.dumps(definitions))
    without["alpha-workflow"]["stages"][0].pop(SUITE_RUN)
    assert roles({"alpha-workflow": without["alpha-workflow"]})[
        "alpha-judge.md"].judges is False
    assert SUITE_RUN in stage  # the arrangement above really declared one


# -- rule three -------------------------------------------------------------


def test_an_authoring_prompt_silent_about_a_red_suite_is_reported(tmp_path):
    """The exposure this story closed: a stage that declares the suite run the
    coordinator makes after its turn, whose prompt says nothing about a red one,
    so its agent has no reason to believe a failure it leaves costs anything."""
    prompts, held = arrangement(
        tmp_path, alpha_writer=f"A template.\n\n{BUILT_SLOT}")
    problems = an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held)

    assert kinds(problems) == [SILENT]
    assert "alpha-writer.md" in problems[0]
    for claim, _ in CARRY_FORWARD:
        assert claim in problems[0], claim


def test_an_authoring_prompt_stating_where_but_not_what_it_costs_is_reported(
    tmp_path,
):
    """Where the failure goes is not the whole rule. A prompt saying the run
    advances and the turn is not handed back, and nothing about the verdict, the
    retry or the archived attempt, is reported — and the report names the claims
    it does not meet rather than a phrase it does not contain, so the prompt is
    free to meet them in its own words."""
    prompts, held = arrangement(
        tmp_path,
        alpha_writer=f"A template.\n\n{BUILT_WITHOUT_THE_COST}\n{BUILT_SLOT}")
    problems = an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held)

    assert kinds(problems) == [SILENT]
    assert "alpha-writer.md" in problems[0]
    assert "the verdict is a failed one" in unmet_half(problems[0])
    assert "a retry is recommended" in unmet_half(problems[0])
    assert "the attempt is archived" in unmet_half(problems[0])
    # And the claims the judged paragraph does meet are not reported against
    # it. The report quotes that paragraph, so this is a statement about the
    # half of the report before the quote — the quote carries what it meets by
    # definition, which is why it is there.
    assert "the run advances past this stage" not in unmet_half(problems[0])
    assert "the outstanding failure is recorded" not in unmet_half(problems[0])
    assert problems[0].split(JUDGED)[1] == paragraphs(
        BUILT_WITHOUT_THE_COST)[0]


def test_an_authoring_prompt_meeting_a_claim_in_unrelated_prose_is_reported(
    tmp_path,
):
    """The defect the passage-wise reading closes, for this rule.

    This prompt has no paragraph saying what follows a red suite. It says what
    such a suite costs in one paragraph, and elsewhere — in prose about an edit
    made outside the paths a stage owns, which mentions no suite at all — it
    happens to use an accepted phrasing of the claim that the run advances past
    this stage.

    Read over the file, that stray sentence answers the claim and the report
    never mentions it, so deleting the paragraph a prompt is supposed to carry
    is reported one claim more quietly than it should be. Read over a passage,
    the claim is reported against the paragraph that came closest, which is the
    paragraph that does not make it.
    """
    prompts, held = arrangement(
        tmp_path,
        alpha_writer=(f"A template.\n\n{BUILT_UNRELATED_CARRY_FORWARD_PHRASING}"
                      f"\n{BUILT_ONLY_THE_COST}\n{BUILT_SLOT}"))
    problems = an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held)

    assert kinds(problems) == [SILENT]
    assert "alpha-writer.md" in problems[0]
    assert "the run advances past this stage" in unmet_half(problems[0])
    assert "the outstanding failure is recorded" in unmet_half(problems[0])
    # The cost, which the judged paragraph does make, is not reported against it.
    assert "the attempt is archived" not in unmet_half(problems[0])
    assert problems[0].split(JUDGED)[1] == paragraphs(BUILT_ONLY_THE_COST)[0]


def test_an_authoring_prompt_splitting_the_claims_across_paragraphs_is_reported(
    tmp_path,
):
    """What the passage-wise reading costs, asserted rather than discovered.

    Every claim the rule holds is somewhere in this prompt — where the failure
    goes in one paragraph, what it costs in the next — and no single paragraph
    makes them all, so it is reported. The remedy is one paragraph, which is
    what both shipped prompts in this position already carry.
    """
    split = f"{BUILT_WITHOUT_THE_COST}\n{BUILT_ONLY_THE_COST}"
    prompts, held = arrangement(
        tmp_path, alpha_writer=f"A template.\n\n{split}\n{BUILT_SLOT}")
    problems = an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held)

    # Nothing is absent from the file: this is a claim about where they are.
    flat = re.sub(r"\s+", " ", prompts["alpha-writer.md"]).lower()
    assert all(any(phrasing in flat for phrasing in phrasings)
               for _, phrasings in CARRY_FORWARD)

    assert kinds(problems) == [SILENT]
    assert "the run advances past this stage" in unmet_half(problems[0])


def test_an_authoring_prompt_making_the_claims_in_one_paragraph_is_not_reported(
    tmp_path,
):
    """The other side of the reading, for this rule: the same claims that are
    reported when split are not reported when one passage makes them all."""
    prompts, held = arrangement(tmp_path)

    def makes_every_claim(passage: str) -> bool:
        lowered = passage.lower()
        return all(any(phrasing in lowered for phrasing in phrasings)
                   for _, phrasings in CARRY_FORWARD)

    assert any(makes_every_claim(passage)
               for passage in paragraphs(prompts["alpha-writer.md"])), \
        "the control has to put one paragraph making every claim"
    assert an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held) == []


def test_a_prompt_that_declares_no_suite_run_is_outside_the_rule(tmp_path):
    """The rule keys on the position the definition gives the stage, so the same
    silence reported in a prompt whose stage declares the suite run is not
    reported in one whose stage does not. Without this the rule would be "every
    prompt carries the paragraph", which is not what any workflow needs."""
    prompts, held = arrangement(
        tmp_path, alpha_judge=f"A template.\n\n{BUILT_VERDICT}\n{BUILT_SLOT}")

    assert held["alpha-judge.md"].authors is False
    assert an_authoring_prompt_not_saying_what_follows_a_red_suite(
        prompts, held) == []


def test_a_stage_that_stops_declaring_the_suite_run_stops_being_held():
    """Derived all the way down, and the counterpart of the judging rule's own
    derivation test: the authoring position is the suite-run declaration and
    nothing else, so removing that declaration takes the prompt out of the rule
    and a workflow arriving with one puts its prompt in with no edit here."""
    definitions = built_definitions()

    assert roles(definitions)["alpha-writer.md"].authors is True
    without = json.loads(json.dumps(definitions))
    without["alpha-workflow"]["stages"][0].pop(SUITE_RUN)
    assert roles({"alpha-workflow": without["alpha-workflow"]})[
        "alpha-writer.md"].authors is False


# -- rule four --------------------------------------------------------------


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

    `declares`, `roles`, `sentences`, `paragraphs`, `closest_passage`,
    `slot_wording` and the rules themselves are what a third workflow's arrival
    would otherwise force an edit to, so none of them may contain a shipped
    prompt filename, a shipped stage name or a shipped workflow name. The
    readers are held to this alongside the rules because a reader is where a
    rule's decision is made as much as the rule is. The assertions and controls
    above are free to name what they are about; these are not.

    Docstrings are stripped first: an explanation naming an example is prose
    about the rule, and a rule that could not illustrate itself would be the
    worse outcome.
    """
    import ast
    import inspect

    source = ""
    for function in (declares, roles, sentences, paragraphs, closest_passage,
                     slot_wording,
                     a_red_suite_returning_a_stage_to_itself,
                     a_judging_prompt_not_stating_the_failing_verdict,
                     an_authoring_prompt_not_saying_what_follows_a_red_suite,
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
