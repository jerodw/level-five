"""story-215 validation: a verdict asks for the assertion its repair needs.

The reviewing stage was recently told that a fix needing a new test is no
ground for sending a finding to the backlog, because the run re-enters at the
stage that owns the fix and then carries on through a stage whose only job is
writing tests. Nothing told it to ask that stage for anything. On such a pass
the test stage arrived holding nothing that named it and was told — by the
harness in its own voice — that it had nothing to repair, so the promise was
made and no text kept it, invisibly: a problem routed for repair is recorded as
dealt with, so nothing is filed for it afterwards, and no line anywhere said the
assertion was never written.

This story has the reviewer ask for the assertion by name. Where the fix it is
sending back needs standing validation that does not exist yet, it records a
second finding beside the first, naming the file the assertion belongs in and
the category that owns that file, tied to the first by the same location. The
run then enters at the stage that owns the code, carries forward to the stage
that writes tests, and that stage arrives holding a finding that names its file.
No machinery moved: the harness already routes one finding per file under one
location and already hands each stage the findings that name it.

The subjects, in the order the criteria state them:

  * **the verifier prompt of the workflow that declares a stage whose work is
    validation** — found through that declaration rather than named — carries
    the instruction in its `repairable_findings` paragraph, beside the sentence
    about a fix spanning files that the instruction extends. It says what the
    second finding names, what ties it to the first, and the bound that keeps it
    honest: ask for it only where the fix is owned by a stage that runs before
    the one whose work is validation, because the pair moves the entry to the
    earliest of the two and an assertion asked for beside a later fix would be
    written before the fix it validates landed. It names no stage and no
    category.

  * **the other shipped verifier prompt** carries no such instruction, because
    its workflow declares no stage that could be sent to write the assertion.

  * **every rule the repairable paragraph already stated** is still stated in
    both prompts, compared sentence by sentence between the paragraph as it
    stood at this story's own baseline and the paragraph at this story's own
    endpoint — both ends at this module's range, so the claim is about what this
    story did and stays that after the story commits.

  * **the routing this repository ships**: in the workflow declaring a stage
    confined to the tests directory, a category resolves to that stage and the
    category that owns a defect in the code resolves to a stage earlier in
    workflow order, so a pass entering at the first reaches the second. The
    stage that owns the code may not itself create a file under the tests
    directory, which is why the instruction is needed at all.

  * **a driven run** whose passing verdict carries the pair under one location
    takes one pass, enters at the stage that owns the fix, invokes the confined
    stage within that pass, hands it the finding naming its file in the text the
    coordinator gives it, records that file as its to change for that pass, and
    leaves the file it wrote in the tree for the returning verification.

  * **the control** is the same run with the second finding dropped: the
    confined stage still runs, is granted nothing, and is told it has nothing to
    repair.

One thing that control cannot discriminate, recorded here so a later reader does
not take it for more than it is. The file the assertion finding names is inside
the confined stage's own confinement, so no restriction governs it and it
survives whether or not it was granted. Survival is therefore asserted as the
end state the promise is about and never as evidence of the grant; what
discriminates is the grant the harness recorded and the text the stage was
handed, and both are asserted in the pair run and denied in the control.

Where the subjects sit, and why:

  * The two prompts and the shipped routing are what this harness ships, so
    they are read as they ship. Neither is resolved here: the prompts come
    through the helpers
    `tests/test_a_routed_finding_is_repaired_unless_the_verdict_says_why.py`
    declares and the definitions through the one
    `tests/test_repair_pass.py` declares, for the reason story-214's module
    reaches for the first — those modules are where that read is declared and
    justified, and a second declaration of the same read would be a second
    place to keep true.

  * The run is a mechanism, so the workflow it executes is an input and is
    built by `tests/conftest.py`'s builder. Every stage name, category and
    artifact name below comes off that definition rather than being spelled at
    an assertion.

Every absence asserted here is demonstrated to be able to fail, and every
demonstration mutates a copy of the *whole* shipped prompt and puts it back
through the same reading the shipped assertion makes — mutating an extracted
string would prove only that `str.replace` works:

  * "the paragraph carries the instruction" sits beside a copy with the
    statement cut out of it and a copy whose statement drops the bound, both of
    which the same paragraph-and-sentence reading reports;
  * "the other prompt carries no such instruction" sits beside a copy of that
    whole prompt with the statement planted where the first prompt carries it,
    which the same reading does find;
  * "the instruction names no stage and no category" sits beside a copy whose
    instruction names one, which the same reading reports;
  * "every rule already stated is still stated" sits beside a copy of the
    shipped prompt with one of those sentences cut out, which the comparison
    against the baseline reports;
  * "the parity walk reports nothing today" sits beside copies with the
    statement removed and with it planted in the other prompt, each of which the
    same walk reports;
  * every run-level claim sits beside the same run with the second finding
    dropped from the verdict.

Nothing here reads a clock and nothing here invokes a model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import conftest
import story_coordinator
from agent_runner import AgentResult
from conftest import (
    BASELINE,
    ENDPOINT,
    StageRef,
    repository_file_at,
    workflow_stage,
)
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    ASSERTION_REQUEST_PHRASE,
    REPAIRABLE_PHRASE,
    REPO_ROOT,
    flowed,
    paragraph_containing,
    parity_problems,
    prompts_that_may_carry_the_divergence,
    sentences,
    shipped_prompt_texts,
    verifier_prompts,
)
from test_a_verdict_repairs_a_finding_needing_new_validation import (
    the_carrying_prompt,
    the_other_prompt,
)
from test_repair_pass import (
    GRANT_EVENT,
    PASS,
    ROUTED,
    events_of,
    grants_per_invocation,
    read_json,
    read_state,
    rendered_prompt,
    repairing,
    shipped_definitions,
    write,
    write_json,
)

THIS_MODULE = Path(__file__)


# ==========================================================================
# Reading the instruction out of a prompt
# ==========================================================================


#: What the instruction must say. Each is a claim it makes rather than a way of
#: spelling it: that a second finding is recorded beside the first, what that
#: finding names, and what ties it to the first. A rewording that keeps all of
#: them is the same instruction; one that drops any of them stops asking for the
#: assertion the repair needs.
ASKS_FOR = (
    "record a second finding beside it",
    "naming the file the assertion belongs in",
    "the category that owns that file",
    "tied to the first by the same slug in `location`",
)

#: The bound on the ask, stated apart from the ask because it is its own
#: criterion: a pass moves the entry to the earliest stage the findings resolve
#: to, so an assertion asked for beside a fix owned by a *later* stage would be
#: written before the fix it validates landed. The clause and the reason for it
#: are both required, because a bound with no reason beside it is the first
#: thing a rewrite drops.
BOUNDED_BY = (
    "ask for it only where the fix is owned by a stage that runs before",
    "written before the fix it validates landed",
)


def instruction_problems(text: str) -> list[str]:
    """Everything wrong with the way `text` asks for the assertion its repair
    needs: empty when the repairable paragraph carries exactly one statement
    about a fix needing standing validation that does not exist yet and that
    statement makes every claim `ASKS_FOR` and `BOUNDED_BY` name.

    A function rather than a test body so the controls can put mutated copies of
    the whole prompt through the same paragraph-and-sentence reading the shipped
    assertion makes. It reports rather than asserts, because one of the ways the
    instruction can go wrong is to vanish, and a reading that raised on that
    could not be shown to report it.
    """
    carrying = [one
                for one in sentences(paragraph_containing(text,
                                                          REPAIRABLE_PHRASE))
                if ASSERTION_REQUEST_PHRASE in one]
    if len(carrying) != 1:
        return [f"the repairable paragraph carries {len(carrying)} statements "
                f"about a fix that {ASSERTION_REQUEST_PHRASE}"]
    (statement,) = carrying
    return [f"the instruction does not say {words!r}"
            for words in ASKS_FOR + BOUNDED_BY if words not in statement]


def the_instruction(text: str) -> str:
    """The instruction as `text` words it — so a control planting it elsewhere
    plants the real sentence rather than an imitation of it."""
    (statement,) = [
        one for one in sentences(paragraph_containing(text, REPAIRABLE_PHRASE))
        if ASSERTION_REQUEST_PHRASE in one]
    return statement


# ==========================================================================
# Mutating a copy of a whole shipped prompt
# ==========================================================================


def _as_the_file_wraps_it(words: str, *,
                          and_the_gap_after: bool = False) -> re.Pattern:
    """`words` as a hard-wrapped file carries them: every gap between words may
    be any whitespace, so a clause that straddles a line break is still matched
    and the mutation follows the prompt when it is rewrapped instead of going
    quietly stale. `and_the_gap_after` takes the whitespace that follows too,
    which is what a caller cutting a whole sentence out of a paragraph wants."""
    pattern = r"\s+".join(re.escape(word) for word in words.split())
    return re.compile(pattern + (r"\s*" if and_the_gap_after else ""))


def with_the_instruction_removed(text: str) -> str:
    """A copy of the whole prompt with the instruction cut out of it.

    The span is computed from the text rather than written here: the sentence is
    found by its own words in the flowed paragraph and matched back against the
    file, so everything the file carries either side of it is byte for byte what
    it was. The sentence and the gap after it go, and one space takes their
    place, so the paragraph reads as it did before the instruction was added
    rather than closing up over a sentence boundary.
    """
    wrapped = _as_the_file_wraps_it(the_instruction(text),
                                    and_the_gap_after=True)
    cut, replaced = wrapped.subn(" ", text, count=1)
    assert replaced == 1 and cut != text, the_instruction(text)
    return cut


def with_the_bound_dropped(text: str) -> str:
    """A copy of the whole prompt whose instruction asks for the assertion
    whatever stage owns the fix — the shape the defect would really take, since
    the ask survives and only the bound on it goes."""
    dropped, replaced = _as_the_file_wraps_it(BOUNDED_BY[0]).subn(
        "ask for it wherever a fix is owned by a stage", text, count=1)
    assert replaced == 1 and dropped != text
    return dropped


#: Where the instruction belongs in the paragraph both prompts share: the
#: sentence about a fix spanning files is the mechanism it extends, and the
#: prompt that carries the instruction carries it immediately after that
#: sentence. Matched on that sentence's closing words, which both prompts carry
#: on one line, so the control plants the statement where the real one sits.
ANCHOR = "running forward through the rest."


def with_the_instruction_planted(text: str, statement: str) -> str:
    """A copy of the whole prompt with `statement` planted into its repairable
    paragraph, immediately after the sentence the instruction extends."""
    assert text.count(ANCHOR) == 1, ANCHOR
    planted = text.replace(ANCHOR, f"{ANCHOR} {statement}", 1)
    assert planted != text
    return planted


# ==========================================================================
# The prompt of the workflow that declares a validating stage
# ==========================================================================


def test_the_two_prompts_are_told_apart_by_a_declaration_and_not_by_a_name():
    """The premise everything below rests on: which prompt carries the
    instruction is read off the workflows, where one declares a stage confined
    to the tests directory and the other declares none. Both prompts exist and
    are different files, so "one of them and not the other" is a claim about two
    texts rather than about one text compared with itself."""
    assert len(prompts_that_may_carry_the_divergence()) == 1
    assert prompts_that_may_carry_the_divergence() < set(shipped_prompt_texts())
    assert the_carrying_prompt() != the_other_prompt()
    for prompt in verifier_prompts():
        assert prompt.is_file(), prompt


def test_the_repairable_paragraph_asks_for_the_assertion_by_its_file():
    """The first two criteria: the paragraph where a repairable finding is
    composed instructs that where a fix needs standing validation that does not
    exist yet a second finding is recorded beside the first, naming the file the
    assertion belongs in and the category that owns that file, tied to the first
    by the same location — and bounds the ask to a fix owned by a stage that runs
    before the one whose work is validation."""
    assert instruction_problems(
        shipped_prompt_texts()[the_carrying_prompt()]) == []


def test_the_instruction_sits_beside_the_sentence_it_extends():
    """Where it sits is part of the ask reaching the verifier: the instruction
    extends the rule that a fix spanning files is one finding per file sharing
    one location, and it follows that sentence immediately rather than sitting a
    hundred lines above the field it is about."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    paragraph = sentences(paragraph_containing(text, REPAIRABLE_PHRASE))
    spanning = [index for index, one in enumerate(paragraph)
                if ANCHOR.rstrip(".") in one]
    instruction = paragraph.index(the_instruction(text))
    assert spanning == [instruction - 1], (spanning, instruction)


def test_the_reading_reports_the_instruction_cut_out_of_the_whole_prompt():
    """Control for the assertion above. The instruction is cut out of a copy of
    the whole shipped prompt and the same paragraph-and-sentence reading is run
    again over that copy, which reports it — so a clean result is a fact about
    the prompt rather than about a reading that has stopped finding the
    paragraph or a search that matches nothing."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    assert instruction_problems(text) == []
    cut = with_the_instruction_removed(text)
    assert ASSERTION_REQUEST_PHRASE not in flowed(cut)
    assert instruction_problems(cut), cut
    # And the cut took the instruction and nothing else: the sentence it
    # extends, and the one after it, are still there to be read.
    paragraph = paragraph_containing(cut, REPAIRABLE_PHRASE)
    assert ANCHOR in paragraph
    assert "Name the category that owns the fix" in paragraph


def test_the_reading_reports_the_bound_dropped_from_the_instruction():
    """Control from the other direction, and the shape the defect would really
    take: the instruction is still there and still about a fix needing
    validation that does not exist yet, but it asks for the assertion whatever
    stage owns the fix. The same reading reports the claims it stopped making,
    and reports nothing about the ask itself, which is untouched."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    dropped = with_the_bound_dropped(text)
    reported = instruction_problems(dropped)
    assert any(BOUNDED_BY[0] in problem for problem in reported), reported
    for words in ASKS_FOR:
        assert not any(words in problem for problem in reported), reported


#: The phrases the instruction borrows from the prompt to say what it means
#: without naming a route, each of which carries a word the routing table also
#: uses. They are set aside by the reading below, and what makes setting them
#: aside legitimate rather than a judgement written here is pinned beneath: each
#: is a phrase this prompt already uses outside the instruction.
BORROWED = ("the stage whose work is validation", "standing validation")


def routing_vocabulary() -> set[str]:
    """Every stage name and every retry category the shipped definitions
    declare — the words an instruction must not use if the routing vocabulary is
    to reach the verifier prompt only through the rendered routes."""
    words = set()
    for definition in shipped_definitions().values():
        for stage in definition["stages"]:
            words.add(stage["name"])
            words.update((stage.get("on_failure") or {}).get("retry_routing")
                         or {})
    return words


def routes_named_in(statement: str) -> list[str]:
    """Which words of the routing vocabulary `statement` uses, once the borrowed
    periphrases are set aside. Whole words only, so `validates` is not read as
    the category `validation`."""
    remaining = statement
    for phrase in BORROWED:
        remaining = remaining.replace(phrase, " ")
    return sorted(word for word in routing_vocabulary()
                  if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", remaining))


def test_the_borrowed_periphrases_are_the_prompts_own_vocabulary():
    """What makes setting them aside legitimate: each phrase is one this prompt
    already uses outside the instruction, so the instruction says what it means
    in the words the prompt had already settled on for saying it without naming
    a route, rather than in a second way of saying the same thing that a reader
    would have to reconcile with the first."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    elsewhere = flowed(text).replace(the_instruction(text), " ")
    assert elsewhere != flowed(text)
    for phrase in BORROWED:
        assert phrase in elsewhere, phrase


def test_the_instruction_names_no_stage_and_no_category():
    """The third criterion: the routing vocabulary still reaches this prompt
    only through the rendered routes. Every stage name and every category the
    shipped definitions declare is looked for in the instruction, with the two
    borrowed periphrases set aside, and none is found."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    assert routing_vocabulary()
    assert routes_named_in(the_instruction(text)) == []


@pytest.mark.parametrize("word", sorted(routing_vocabulary()))
def test_the_reading_reports_a_route_named_in_the_instruction(word: str):
    """Control for the absence above, once per word it is asserted of: the same
    word planted into the instruction of a copy of the whole shipped prompt is
    reported by the same reading. Without this, a vocabulary that had stopped
    resolving anything would read as an instruction that names nothing."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    planted = text.replace("record a second finding",
                           f"record a second {word} finding", 1)
    assert planted != text
    assert routes_named_in(the_instruction(planted)) == [word]


# ==========================================================================
# The other shipped verifier prompt
# ==========================================================================


def test_the_other_prompt_carries_no_such_instruction():
    """The fourth criterion: the workflow that declares no stage whose work is
    validation is not told to ask for an assertion, because it has no stage that
    could be sent to write one. The control below shows the same reading finds
    the instruction when it is planted there."""
    other = shipped_prompt_texts()[the_other_prompt()]
    assert ASSERTION_REQUEST_PHRASE not in flowed(other)
    assert instruction_problems(other)


def test_the_instruction_is_found_when_it_is_planted_in_the_other_prompt():
    """Control for the absence above: the instruction the one prompt carries,
    planted into a copy of the *whole* other prompt where that one carries it,
    is found there by the same reading — so its absence is a fact about the
    shipped prompt rather than about a search matching nothing."""
    texts = shipped_prompt_texts()
    planted = with_the_instruction_planted(
        texts[the_other_prompt()],
        the_instruction(texts[the_carrying_prompt()]))
    assert instruction_problems(planted) == [], planted


# ==========================================================================
# Every rule the paragraph already stated
# ==========================================================================


def shipped_prompt(name: str) -> Path:
    """The shipped verifier prompt of that file name, through the sibling's own
    derivation rather than by joining a path here."""
    (found,) = [prompt for prompt in verifier_prompts() if prompt.name == name]
    return found


def relative_to_repo(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def rules_lost(before: str, after: str) -> list[str]:
    """Every sentence the repairable paragraph carried in `before` and no longer
    carries in `after`, compared flowed — so rewrapping a line, which is what
    inserting a sentence does to the lines around it, is invisible here and only
    a rule that stopped being stated is reported."""
    kept = set(sentences(paragraph_containing(after, REPAIRABLE_PHRASE)))
    return [one for one in sentences(paragraph_containing(before,
                                                          REPAIRABLE_PHRASE))
            if one not in kept]


@pytest.mark.parametrize("name", sorted(shipped_prompt_texts()))
def test_every_rule_the_repairable_paragraph_already_stated_is_still_stated(
        name: str):
    """The fifth criterion, over both shipped prompts: adding the instruction
    displaced nothing. Every sentence the paragraph carried at this story's own
    baseline is still a sentence of it.

    Both ends are bounded at this module's own story range, and neither is the
    working tree read against the repository root. The earlier end is BASELINE
    rather than HEAD because the coordinator commits the tree at the end of a
    successful run, so a HEAD baseline would compare the paragraph with itself
    and go vacuously green. The later end is ENDPOINT rather than the working
    tree for the mirror-image reason: a story after this one may legitimately
    reword a sentence of that paragraph — brief key 344 asks for exactly that,
    over the sentence stating what happens on `speaks_to_another_concern` — and
    an after side read from the working tree would turn this assertion into a
    standing prohibition on whoever edits the prompt next, reddening here and
    blaming this story for a rule it was never asserting anything about. That is
    the trap the architecture document records under its HEAD-baseline bullets
    and the shared reader declares against in its own words."""
    before = repository_file_at(relative_to_repo(shipped_prompt(name)),
                                validation_file=THIS_MODULE, bound=BASELINE)
    after = repository_file_at(relative_to_repo(shipped_prompt(name)),
                               validation_file=THIS_MODULE, bound=ENDPOINT)
    assert rules_lost(before, after) == []


#: A rule the paragraph states in both prompts, in a sentence neither is
#: permitted to diverge on. Cutting it is the loss the comparison above must
#: report.
A_RULE = "A category this workflow does not define escalates the run."


def test_the_comparison_reports_a_rule_that_stopped_being_stated():
    """Control for the comparison above: the same baseline against a copy of the
    prompt at this story's endpoint with one sentence of that paragraph cut out
    reports it. Green above therefore says the paragraph kept its rules rather
    than that the comparison cannot go red.

    Both ends go through the same bounds the comparison above uses, and the cut
    sentence is planted into the endpoint text rather than into the working tree,
    so the control stays a control after this story commits for the reason
    recorded there."""
    name = the_carrying_prompt()
    before = repository_file_at(relative_to_repo(shipped_prompt(name)),
                                validation_file=THIS_MODULE, bound=BASELINE)
    after = repository_file_at(relative_to_repo(shipped_prompt(name)),
                               validation_file=THIS_MODULE, bound=ENDPOINT)
    assert rules_lost(before, after) == []
    dropped, replaced = _as_the_file_wraps_it(A_RULE).subn("", after, count=1)
    assert replaced == 1 and dropped != after
    assert rules_lost(before, dropped) == [A_RULE]


# ==========================================================================
# What the widened parity walk reports about this statement
# ==========================================================================


def test_the_parity_walk_reports_the_instruction_missing_where_it_belongs():
    """The sixth criterion for this statement, first half. The walk permits the
    instruction in one prompt; it does not stop caring whether it is there. Cut
    out of a copy of the whole prompt, its absence is reported — which is what
    makes the permission a widening rather than a hole."""
    texts = shipped_prompt_texts()
    permitted = prompts_that_may_carry_the_divergence()
    carrier = the_carrying_prompt()
    assert parity_problems(texts, permitted) == []

    cut = {**texts, carrier: with_the_instruction_removed(texts[carrier])}
    reported = parity_problems(cut, permitted)
    assert any(problem.startswith(f"{carrier}:") for problem in reported), \
        reported
    assert any(ASSERTION_REQUEST_PHRASE in problem for problem in reported), \
        reported


def test_the_parity_walk_reports_the_instruction_in_the_prompt_it_is_false_of():
    """The second half, and the one the passage comparison alone cannot make:
    planted into the other prompt the instruction makes the two paragraphs agree
    again, so only a walk that knows which prompt may carry it reports the
    spread. This is what stops an instruction true of one workflow reaching the
    verifier of a workflow it is false of."""
    texts = shipped_prompt_texts()
    permitted = prompts_that_may_carry_the_divergence()
    other = the_other_prompt()
    spread = {**texts,
              other: with_the_instruction_planted(
                  texts[other], the_instruction(texts[the_carrying_prompt()]))}
    reported = parity_problems(spread, permitted)
    assert any(problem.startswith(f"{other}:") for problem in reported), \
        reported
    assert any(ASSERTION_REQUEST_PHRASE in problem for problem in reported), \
        reported


# ==========================================================================
# The routing this repository ships
# ==========================================================================


def the_shipped_workflow_declaring_a_confined_validating_stage() -> dict:
    """The one shipped definition declaring a stage confined to the tests
    directory this deployment configures.

    Resolved through the helper `tests/test_repair_pass.py` declares, against
    this repository's own configuration the way a run resolves a definition,
    because a declaration read as text carries the token and not the directory.
    """
    tests_dir = conftest.repository_config()["tests_dir"]
    found = [definition for definition in shipped_definitions().values()
             if any(tests_dir in (stage.get("may_only_change") or [])
                    for stage in definition["stages"])]
    (one,) = found
    return one


def confined_stage_of(definition: dict) -> str:
    tests_dir = conftest.repository_config()["tests_dir"]
    (stage,) = [stage for stage in definition["stages"]
                if tests_dir in (stage.get("may_only_change") or [])]
    return stage["name"]


def shipped_routes(definition: dict) -> dict:
    (stage,) = [stage for stage in definition["stages"]
                if (stage.get("on_failure") or {}).get("retry_routing")]
    return stage["on_failure"]["retry_routing"]


def test_the_definition_declaring_that_stage_is_the_one_whose_prompt_carries_it():
    """What ties the routing claims below to the text claims above: the
    definition read here is the one whose verifier prompt carries the
    instruction. Two derivations of "the workflow that declares a stage whose
    work is validation" — one off `may_only_change`, one off the prompt the
    parity walk permits the statement in — and they answer the same."""
    definition = the_shipped_workflow_declaring_a_confined_validating_stage()
    (judging,) = [stage for stage in definition["stages"]
                  if stage.get("inspection")]
    assert judging["prompt"] == the_carrying_prompt()


def the_code_owning_route(order: list[str], confined: str,
                          routes: dict) -> tuple[str, str]:
    """The category that owns a defect in the code, and the stage it resolves
    to, derived rather than named: it is the one category resolving to a stage
    that runs before the stage confined to the tests directory. Exactly one,
    which is asserted here so a table that grew a second such route is reported
    rather than silently picked from."""
    owning = [(category, route["stage"]) for category, route in routes.items()
              if order.index(route["stage"]) < order.index(confined)]
    assert len(owning) == 1, owning
    return owning[0]


def test_the_stage_that_owns_the_code_may_not_write_the_assertion_itself():
    """Why the instruction exists at all, read off the shipped definition: the
    stage the code-owning category resolves to is forbidden to create a file
    under the tests directory, so the assertion a fix needs cannot be written by
    the stage making the fix. That is the fact the second finding is for."""
    definition = the_shipped_workflow_declaring_a_confined_validating_stage()
    tests_dir = conftest.repository_config()["tests_dir"]
    order = [stage["name"] for stage in definition["stages"]]
    _, code_stage = the_code_owning_route(order, confined_stage_of(definition),
                                          shipped_routes(definition))
    (owning,) = [stage for stage in definition["stages"]
                 if stage["name"] == code_stage]
    assert tests_dir in (owning.get("may_not_create") or []), owning


def test_the_shipped_routing_reaches_the_confined_stage_from_the_earlier_one():
    """The ninth criterion. In the workflow this repository ships that declares a
    stage confined to its tests directory, a category resolves to that stage and
    the category that owns a defect in the code resolves to a stage earlier in
    workflow order — so a pass entering at the first reaches the second by
    running forward, which is the whole mechanism the instruction rests on.

    Decided by the coordinator's own routing function over the shipped table
    rather than by comparing indices here, and driven in both orders, because
    the entry must come off workflow order and not off the order the verdict
    happened to list the findings in.
    """
    definition = the_shipped_workflow_declaring_a_confined_validating_stage()
    order = [stage["name"] for stage in definition["stages"]]
    confined = confined_stage_of(definition)
    routes = shipped_routes(definition)
    code_category, code_stage = the_code_owning_route(order, confined, routes)

    to_confined = [category for category, route in routes.items()
                   if route["stage"] == confined]
    assert to_confined, routes
    assert order.index(code_stage) < order.index(confined)

    pair = [{"path": "a-file", "location": "one place",
             "category": code_category},
            {"path": "another-file", "location": "one place",
             "category": to_confined[0]}]
    for findings in (pair, list(reversed(pair))):
        routing = story_coordinator.repair_destination(findings, routes, order)
        assert routing.unknown == []
        assert routing.stage == code_stage
        assert confined in order[order.index(routing.stage):]


# ==========================================================================
# The workflow the runs below execute
# ==========================================================================


#: Where the validating stage this workflow declares is confined. Stated once
#: here, in the fixture, and read from here by the declaration and by every path
#: below it: the workflow is built to confine that stage to this directory and
#: the findings name files under it, so everything downstream derives the place
#: from the fixture rather than from what this repository happens to configure.
CONFINED_TO = "tests/"

#: The file the validating stage writes whenever it runs, and the file it writes
#: only when a finding names it. Both under the confinement, which is the point:
#: the assertion belongs where that stage may write and the stage that owns the
#: code may not.
ORDINARY_VALIDATION = f"{CONFINED_TO}test_the_sample_behaviour.py"
THE_ASSERTION = f"{CONFINED_TO}test_the_repaired_fix_holds.py"

#: The file the stage that owns the code writes, and the fix the first finding
#: names.
THE_CODE = "src/app.py"

#: The artifact the fixture's declaration names, deliberately not the name this
#: repository deploys: a record reaching the run directory under this name is
#: what says the coordinator reads the name off the declaration.
REPAIR_ARTIFACT = "assertion-request-probe.json"

#: The context field the coordinator injects the pass record under.
#: `conftest.BUILT_PROMPT_FIELDS` predates it, and the fixture says a module
#: needing a field it does not list passes its own template, which is what the
#: templates below do. Spelled once, here.
REPAIR_FIELD = "repair_pass_result"

STORY_ID = conftest.SAMPLE_STORY_ID

#: Three stages, because the claim is about a pass that enters at the stage
#: owning a fix and runs forward into a stage confined to the tests directory:
#: two writing stages so there is an order to enter in, and the judging stage
#: that routes. No clean-clone declaration, because nothing here is about that
#: ordering and a workflow declaring none routes the pass just the same.
WORKFLOW = conftest.build_workflow(
    workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"},
        may_not_create=(CONFINED_TO,)),
    workflow_stage(
        outputs=(conftest.TEST_RESULTS, conftest.TESTER_CHANGED_FILES),
        changed_files=conftest.TESTER_CHANGED_FILES,
        schemas={conftest.TEST_RESULTS: "test-results",
                 conftest.TESTER_CHANGED_FILES: "changed-files"},
        may_only_change=(CONFINED_TO,),
        revert_check={"result": "validation-revert-probe.json",
                      "baseline": "stage-baseline",
                      "discarded": "discarded"}),
    workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result",
                 conftest.RETRY_GUIDANCE: "retry-guidance"},
        repair_pass={"result": REPAIR_ARTIFACT, "budget": 2},
        retry_routing={
            "the-code": {
                "stage": StageRef(0),
                "when": "the behaviour the story asked for is missing"},
            "the-assertions": {
                "stage": StageRef(1),
                "when": "the validation does not hold the behaviour"},
        }),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="assertion-request-workflow",
)

STAGE_NAMES = [stage["name"] for stage in WORKFLOW["stages"]]
WRITING, VALIDATING, VERIFYING = STAGE_NAMES

VERIFIER_STAGE = next(stage for stage in WORKFLOW["stages"]
                      if "repair_pass" in stage)
ROUTES = VERIFIER_STAGE["on_failure"]["retry_routing"]
CODE_CATEGORY, ASSERTION_CATEGORY = list(ROUTES)

PROMPTS = {name: (conftest.built_stage_prompt(name)
                  + f"{REPAIR_FIELD}:\n{{{{{REPAIR_FIELD}}}}}\n")
           for name in STAGE_NAMES}

#: The one location both findings share, which is what makes them one problem
#: rather than two. Distinctive text throughout, so a search of a rendered
#: prompt is looking for these findings rather than for any sentence about
#: repairs.
LOCATION = f"{THE_CODE} - the sample behaviour, and the assertion it needs"

THE_FIX = {
    "path": THE_CODE,
    "location": LOCATION,
    "finding": "MARKER-FIX the sample behaviour refuses an empty name",
    "correction": "MARKER-FIX-DONE accept it",
    "category": CODE_CATEGORY,
}
THE_ASSERTION_ASKED_FOR = {
    "path": THE_ASSERTION,
    "location": LOCATION,
    "finding": "MARKER-ASSERTION nothing asserts the empty name is accepted",
    "correction": "MARKER-ASSERTION-DONE write the assertion beside the others",
    "category": ASSERTION_CATEGORY,
}


# ==========================================================================
# Fixture plumbing
# ==========================================================================


def the_record_in(prompt: str) -> dict | None:
    """The pass record the coordinator rendered into `prompt`, parsed.

    None where the prompt carries none, which is every stage of a run that has
    taken no pass. Read out of the prompt rather than off the run directory
    because what these claims are about is the text the stage was handed: the
    record is the last thing each template renders, so the tail after its label
    is the record and nothing else.
    """
    _, label, rendered = prompt.rpartition(f"{REPAIR_FIELD}:\n")
    assert label, prompt
    rendered = rendered.strip()
    return None if rendered == "None" else json.loads(rendered)


def findings_for(record: dict | None, stage: str) -> list[dict]:
    """The findings in `record` that name `stage` — what that stage is asked to
    repair."""
    return [finding for finding in (record or {}).get("findings", [])
            if finding.get("stage") == stage]


#: The clause the coordinator's own statement uses to tell a stage that no
#: finding in the record names it, and the condition it is conditional on.
#: Pinned against the function that writes it below, so a rewording reddens
#: there rather than leaving this reading quietly matching nothing.
NOTHING_TO_REPAIR = "you have nothing to repair"
CONDITIONAL = "if none names yours"


def is_told_it_has_nothing_to_repair(record: dict | None, stage: str) -> bool:
    """Whether the text the coordinator handed `stage` tells it there is nothing
    for it to do.

    The statement is one paragraph every stage a pass reaches is handed, and its
    clause about having nothing to repair is conditional on no finding in the
    record naming that stage. So the condition is what is read: the words are
    identical in both runs below and would discriminate nothing, while whether
    any finding resolved to this stage is exactly what the conditional turns on.
    The clause is asserted present first, so a statement that stopped carrying it
    cannot read as a stage being told something it is not told.
    """
    assert NOTHING_TO_REPAIR in (record or {}).get("statement", "")
    assert CONDITIONAL in record["statement"]
    return findings_for(record, stage) == []


def test_the_conditional_this_module_reads_is_the_coordinators_own():
    """The clause above is the coordinator's, not this module's: the statement
    the coordinator composes for a pass carries it, and carries it as a
    condition on no finding naming the stage. A rewording fails here rather than
    leaving the reading above matching nothing."""
    statement = story_coordinator.repair_pass_statement(
        WRITING, clean_clone_checked=False)
    assert NOTHING_TO_REPAIR in statement
    assert CONDITIONAL in statement
    (sentence,) = [one for one in sentences(flowed(statement))
                   if NOTHING_TO_REPAIR in one]
    assert CONDITIONAL in sentence, sentence


class Runner:
    """A fake agent runner whose writing stages act on the text they are handed.

    Each stage writes its declared artifacts, and the two writing stages also
    make the correction of every finding the pass record in their own prompt
    names for them — which is what a real stage does with what it is given, and
    what makes "the stage was asked for the assertion" observable as a file in
    the tree rather than only as a string in a prompt.

    Every invocation's changed-files record names the file the stage always
    writes beside whatever a pass asked for. The completeness check's tree
    signature is first-seen-wins within an attempt and a repair pass makes no
    new attempt, so a re-entered invocation recording only the pass's edit would
    be reported for the file the tree still holds from its first run, and the
    run would stop before any grant was decided.
    """

    def __init__(self, target_root: Path, verdicts: list[dict]):
        self.target_root = target_root
        self.run_dir = conftest.run_dir_for(target_root, STORY_ID)
        self.verdicts = list(verdicts)
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        #: What the file the assertion finding names held in the tree at each
        #: invocation of the judging stage, None where it was not there.
        #: Recorded at that moment rather than read back afterwards, because
        #: "in the tree when the returning verification runs" is a question
        #: about an instant, and the tree a run works in is not the directory
        #: the fixture handed it.
        self.assertion_when_judged: list[str | None] = []

    def __call__(self, prompt, *, stage, cwd, log_path=None,
                 permission_mode=None, model=None, allowed_tools=None,
                 max_budget_usd=None, run_dir=None, **declared):
        tree = Path(cwd)
        self.calls.append(stage)
        self.prompts.setdefault(stage, []).append(prompt)
        asked_for = findings_for(the_record_in(prompt), stage)
        if stage == WRITING:
            write(tree / THE_CODE, "print('hello')\n# the story's change\n")
            for finding in asked_for:
                write(tree / finding["path"],
                      f"print('hello')\n# {finding['correction']}\n")
            write_json(self.run_dir / conftest.CHANGED_FILES,
                       {"modified": [THE_CODE], "created": [], "deleted": []})
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VALIDATING:
            created = [ORDINARY_VALIDATION]
            write(tree / ORDINARY_VALIDATION,
                  "# the validation this stage always writes\n")
            for finding in asked_for:
                write(tree / finding["path"], f"# {finding['correction']}\n")
                if finding["path"] not in created:
                    created.append(finding["path"])
            write_json(self.run_dir / conftest.TESTER_CHANGED_FILES,
                       {"modified": [], "created": created, "deleted": []})
            write_json(self.run_dir / conftest.TEST_RESULTS,
                       {"tests_written": len(created)})
        elif stage == VERIFYING:
            asked = tree / THE_ASSERTION
            self.assertion_when_judged.append(
                asked.read_text(encoding="utf-8") if asked.is_file() else None)
            write_json(self.run_dir / conftest.VERIFICATION_RESULT,
                       self.verdicts.pop(0))
        return AgentResult(ok=True, result_text=f"{stage} done")


class Run:
    """One driven run: its exit code, its runner and its run directory."""

    def __init__(self, code: int, runner: Runner, run_dir: Path):
        self.code = code
        self.runner = runner
        self.run_dir = run_dir


def drive(target_root: Path, harness: Path, verdicts: list[dict]) -> Run:
    runner = Runner(target_root, verdicts)
    code = story_coordinator.run_story(STORY_ID, harness, target_root, runner)
    return Run(code, runner, conftest.run_dir_for(target_root, STORY_ID))


def record_of(run: Run, number: int = 1) -> dict:
    return read_json(run.run_dir / story_coordinator.repair_pass_result_file(
        REPAIR_ARTIFACT, number))


def entry_shape(entry: str) -> list[str]:
    """The stages a run entering at `entry` invokes: all of them, then that one
    and everything after it."""
    return [*STAGE_NAMES, *STAGE_NAMES[STAGE_NAMES.index(entry):]]


@pytest.fixture
def configured_workflow() -> str:
    """Point the shared target fixture at the definition built above."""
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    return conftest.materialize_workflow(
        WORKFLOW, tmp_path / "assertion-request-harness", prompts=PROMPTS)


@pytest.fixture
def the_pair(target_root, harness_root) -> Run:
    """The central run: a passing verdict carrying both findings under one
    location — the fix, and the assertion that fix needs."""
    return drive(target_root, harness_root,
                 [repairing(THE_FIX, THE_ASSERTION_ASKED_FOR), PASS])


@pytest.fixture
def the_fix_alone(target_root, harness_root) -> Run:
    """The control every run-level claim below is read against: the same run,
    the same verdict, with the second finding dropped."""
    return drive(target_root, harness_root, [repairing(THE_FIX), PASS])


# ==========================================================================
# The fixture is what it claims to be
# ==========================================================================


def test_the_built_workflow_is_the_arrangement_these_claims_need():
    """Every premise the runs rest on, asserted rather than assumed: one stage
    confined to the tests directory, a stage before it that may not create
    there, a category resolving to each of the two, and the files the findings
    name on the right side of that confinement."""
    stages = WORKFLOW["stages"]
    (confinement,) = story_coordinator.restrictions_on(stages[1])
    assert confinement.sense == story_coordinator.CONFINEMENT
    assert confinement.governs(THE_CODE)
    assert not confinement.governs(THE_ASSERTION)
    assert not confinement.governs(ORDINARY_VALIDATION)
    assert CONFINED_TO in stages[0]["may_not_create"]

    assert ROUTES[CODE_CATEGORY]["stage"] == WRITING
    assert ROUTES[ASSERTION_CATEGORY]["stage"] == VALIDATING
    assert STAGE_NAMES.index(WRITING) < STAGE_NAMES.index(VALIDATING)
    assert THE_FIX["location"] == THE_ASSERTION_ASKED_FOR["location"]
    assert THE_FIX["path"] != THE_ASSERTION_ASKED_FOR["path"]


# ==========================================================================
# The driven run: the pair delivers the promise
# ==========================================================================


def test_the_pair_takes_one_pass_entering_at_the_stage_that_owns_the_fix(
        the_pair):
    """The tenth criterion: two repairable findings under one location, one
    resolving to the stage that owns the fix and one to the stage confined to the
    tests directory, cost one pass that enters at the first of those stages and
    runs forward through the second."""
    run = the_pair
    assert run.code == 0
    assert read_state(run.run_dir)["status"] == "completed"
    assert read_state(run.run_dir)["repair_pass_count"] == 1
    assert events_of(run.run_dir).count(ROUTED) == 1

    record = record_of(run)
    assert record["stage"] == WRITING
    assert record["findings"] == [
        {**THE_FIX, "stage": WRITING},
        {**THE_ASSERTION_ASKED_FOR, "stage": VALIDATING},
    ]
    assert run.runner.calls == entry_shape(WRITING)


def test_the_confined_stage_is_handed_the_finding_that_names_its_file(the_pair):
    """The eleventh criterion: the stage confined to the tests directory is
    invoked within the pass, the text the coordinator gives it carries the
    finding naming its file, and it is not told it has nothing to repair. Its
    first invocation is the control inside the same run: the same stage, the same
    template, and none of this in it."""
    run = the_pair
    assert run.runner.calls.count(VALIDATING) == 2
    first, second = run.runner.prompts[VALIDATING]

    assert findings_for(the_record_in(first), VALIDATING) == []
    assert findings_for(the_record_in(second), VALIDATING) == [
        {**THE_ASSERTION_ASKED_FOR, "stage": VALIDATING}]
    for words in (THE_ASSERTION_ASKED_FOR["location"],
                  THE_ASSERTION_ASKED_FOR["finding"],
                  THE_ASSERTION_ASKED_FOR["correction"], THE_ASSERTION):
        assert words not in first, words
        assert words in second, words
    assert "{{" not in second

    assert not is_told_it_has_nothing_to_repair(the_record_in(second),
                                                VALIDATING)
    # And the prompt the coordinator rendered is the one the stage was handed,
    # so what is asserted above is the text of record rather than a string this
    # process happened to hold.
    assert rendered_prompt(run.run_dir, VALIDATING) == second


def test_the_harness_records_that_file_as_that_stages_to_change_for_the_pass(
        the_pair):
    """The twelfth criterion, from both sides: the event stream records the grant
    at that stage's invocation inside the pass and at no other, and the reader
    the ownership and revert checks call answers the same file."""
    run = the_pair
    assert grants_per_invocation(run.run_dir, VALIDATING, GRANT_EVENT) == \
        [[], [THE_ASSERTION]]
    assert grants_per_invocation(run.run_dir, WRITING, GRANT_EVENT) == \
        [[], [THE_CODE]]
    assert story_coordinator.repair_pass_grants(
        run.run_dir, WORKFLOW["stages"],
        story_coordinator.load_state(run.run_dir), VALIDATING, 1) == \
        [THE_ASSERTION]


def test_the_file_that_stage_wrote_is_in_the_tree_at_the_returning_verification(
        the_pair):
    """The thirteenth criterion, and the end state the whole instruction is for:
    the assertion exists when the verification the pass returns to runs.

    Read at that instant rather than after the run, and recorded here as the end
    state rather than as evidence of the grant: the file is inside that stage's
    own confinement, so nothing would have undone it whether or not it was
    granted. What says the grant happened is the test above.
    """
    judged = the_pair.runner.assertion_when_judged
    assert len(judged) == 2, judged
    # Absent when the verdict that asked for it was written, there when the
    # verification the pass returns to runs -- so it is this pass's doing.
    assert judged[0] is None
    assert THE_ASSERTION_ASKED_FOR["correction"] in judged[1]


# ==========================================================================
# The control: the same run carrying only the first finding
# ==========================================================================


def test_the_same_run_carrying_only_the_fix_grants_that_stage_nothing(
        the_fix_alone):
    """The fourteenth criterion, and the control for every claim above. The same
    workflow, the same target, the same verdict with the second finding dropped:
    the pass still routes and still runs forward through the stage confined to
    the tests directory, and that stage is granted nothing and is told it has
    nothing to repair."""
    run = the_fix_alone
    assert run.code == 0
    assert read_state(run.run_dir)["repair_pass_count"] == 1
    record = record_of(run)
    assert record["stage"] == WRITING
    assert record["findings"] == [{**THE_FIX, "stage": WRITING}]

    # It runs: what differs is what it is granted and what it is told, not
    # whether it was reached.
    assert run.runner.calls == entry_shape(WRITING)
    assert grants_per_invocation(run.run_dir, VALIDATING, GRANT_EVENT) == \
        [[], []]
    assert story_coordinator.repair_pass_grants(
        run.run_dir, WORKFLOW["stages"],
        story_coordinator.load_state(run.run_dir), VALIDATING, 1) == []

    second = run.runner.prompts[VALIDATING][1]
    assert is_told_it_has_nothing_to_repair(the_record_in(second), VALIDATING)
    assert THE_ASSERTION_ASKED_FOR["finding"] not in second
    assert THE_ASSERTION not in second
    # Nothing wrote it, because nothing asked for it: the file is absent at
    # both verifications, where the pair run has it at the second.
    assert run.runner.assertion_when_judged == [None, None]
