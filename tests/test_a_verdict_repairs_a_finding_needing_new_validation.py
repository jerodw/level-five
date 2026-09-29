"""story-214 validation: a verdict repairs a finding whose fix needs new
standing validation rather than declining it.

A run that reviews its own work can fix what it finds in place or write it up
and send it to the backlog, and fixing costs a few minutes where filing costs a
planning conversation, a whole second run, a review and a merge. Twice in one
run the reviewing stage filed rather than fixed, and the reason it gave was
that fixing would mean writing a new test to hold the fix and that a run cannot
write a test alongside a fix. That reason is wrong under this workflow: a fix
sent back into the run re-enters at the stage that owns the file and runs
forward through the stages after it, and one of those stages does nothing but
write tests. This story closes that reason off in the text the reviewing stage
is actually given, and states beside it the fact that makes it wrong.

The subjects, in the order the criteria state them:

  * **the verifier prompt of the workflow that declares a stage whose work is
    validation** — found through that declaration rather than named — states
    in the paragraph enumerating the grounds for declining a routed finding
    that a fix needing new standing validation is not among them, and states
    where the retry routes are rendered that a repair pass reaches the
    validating stage, so the assertion a fix needs is written by a stage other
    than the one whose change it validates. Every ground that paragraph named
    before is still named, and the rule that size is a reason to fix is
    untouched.

  * **the other shipped verifier prompt** states neither, and still carries
    the sentence this story's new one is written against: that its workflow
    has no stage that authors validation, so it declares no category for a
    defect in the tests as such.

  * **the parity guard** over the two shipped prompts, which held the grounds
    paragraph byte-identical between them and now permits exactly this
    statement: it reports the statement gone missing from the prompt that
    should carry it, and reports it present in the prompt that should not.
    That the narrowing did not stop the guard reporting a sentence the two
    share, reworded in either of them, is held where the guard is declared —
    tests/test_a_routed_finding_is_repaired_unless_the_verdict_says_why.py
    ::test_the_narrowed_walk_still_reports_a_shared_sentence_reworded.

Every absence asserted here is demonstrated to be able to fail, and every
demonstration mutates a copy of the *whole* shipped prompt and puts it back
through the same reading the shipped assertion makes — mutating an extracted
string would prove only that `str.replace` works:

  * "the grounds paragraph closes this ground" sits beside two copies of the
    whole prompt, one with the statement cut out of it and one with the ground
    offered again in its place, both of which the same paragraph-and-sentence
    reading reports;
  * "the other prompt states neither thing" sits beside a copy of that whole
    prompt with each statement planted where the first prompt carries it,
    which the same readings do find;
  * "the parity guard reports nothing today" sits beside copies with the
    statement removed and with it planted in the other prompt, each of which
    the same guard reports.

Nothing here is written down twice. The prompts, the phrase naming the
permitted divergence, the parity guard and the paragraph and sentence readings
all come from the module that declares them; the grounds this story leaves
alone come from the module whose story named them. Nothing here reads this
repository's history or drives a run: the claims are about the text two shipped
prompts carry, so the shipped prompts are the subject and are read as they
ship.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    PERMITTED_DIVERGENCE_PHRASE,
    flowed,
    parity_problems,
    prompts_that_may_carry_the_divergence,
    sentences,
    shipped_prompt_texts,
    verifier_prompts,
    verifier_prompts_declaring_validation,
)
from test_a_small_finding_is_repaired_or_dropped_never_filed import (
    GROUNDS,
    GROUNDS_PHRASE,
    SIZE_PHRASE,
    sentence_carrying,
    triage_paragraph,
)

#: The context field each verifier prompt renders its retry-routing table
#: under. The paragraph this story's second statement belongs in is the one
#: after that slot, found through the slot rather than by a phrase, so the
#: reading does not depend on words either prompt happens to use there.
ROUTES_FIELD = "retry_routes"

#: What the statement closing the ground must say. Each of these is a claim
#: the statement makes rather than a way of spelling it: that the ground is
#: closed, that the route exists, where it leads, and that it is this run's
#: work. A rewording that keeps all four is the same statement; one that drops
#: any of them is the ground reopening.
CLOSING_WORDS = (
    "is not a reason either",
    "the repair pass reaches",
    "the stage whose work is validation",
    "written within this run",
)

#: What the retry-routes paragraph of the validating workflow's prompt must
#: say: that the workflow has such a stage, that a repair pass runs forward
#: through the stages after the one it enters at, and what that buys — an
#: assertion written by a stage other than the one whose change it validates,
#: which is the sentence the whole story exists to put in front of a verifier.
ROUTE_WORDS = (
    "declares a stage whose work is validation",
    "runs forward from there through every stage after it",
    "a stage other than the one whose change it validates",
)

#: The sentence the other workflow's prompt already carried and this story
#: leaves exactly as it is — the mirror the new statement is written against.
#: Pinned whole rather than by a phrase, because "unchanged" is the claim.
THE_MIRROR = ("This workflow has no stage that authors validation, so it "
              "declares no category for a defect in the tests as such")


# ==========================================================================
# Reading the two prompts
# ==========================================================================


def routes_paragraph(text: str) -> str:
    """The paragraph immediately after the rendered retry routes — where each
    prompt tells its verifier what the category list is and is not."""
    marker = f"{{{{{ROUTES_FIELD}}}}}"
    after = text[text.index(marker) + len(marker):].lstrip()
    return flowed(after.split("\n\n")[0])


def the_carrying_prompt() -> str:
    """The name of the prompt whose workflow declares a validating stage."""
    (name,) = prompts_that_may_carry_the_divergence()
    return name


def the_other_prompt() -> str:
    """The name of the shipped verifier prompt that declares none."""
    (name,) = set(shipped_prompt_texts()) - prompts_that_may_carry_the_divergence()
    return name


def closed_ground_problems(text: str) -> list[str]:
    """Everything wrong with the way `text` closes the ground: empty when the
    grounds paragraph carries exactly one statement about a fix needing new
    standing validation and that statement makes every claim
    `CLOSING_WORDS` names.

    A function rather than a test body so the controls can put mutated copies
    of the whole prompt through the same paragraph and sentence readings the
    shipped assertion makes. It reports rather than asserts, because one of
    the ways the statement can go wrong is to vanish, and a reading that
    raised on that could not be shown to report it.
    """
    carrying = [one for one in sentences(triage_paragraph(text))
                if PERMITTED_DIVERGENCE_PHRASE in one]
    if len(carrying) != 1:
        return [f"the grounds paragraph carries {len(carrying)} statements "
                f"about a fix that {PERMITTED_DIVERGENCE_PHRASE}"]
    (statement,) = carrying
    return [f"the statement does not say {words!r}"
            for words in CLOSING_WORDS if words not in statement]


def routes_problems(text: str) -> list[str]:
    """Everything the retry-routes paragraph of `text` does not say about the
    route a repair pass takes. Empty for the prompt of a workflow that
    declares a validating stage; every entry for one that does not."""
    paragraph = routes_paragraph(text)
    return [words for words in ROUTE_WORDS if words not in paragraph]


# ==========================================================================
# Mutating a copy of a whole shipped prompt
# ==========================================================================


def with_the_statement_removed(text: str) -> str:
    """A copy of the whole prompt with the statement closing the ground cut
    out of it.

    The span is computed from the text rather than written here: the statement
    is found by its own words in the flowed paragraph, and matched back
    against the file with each gap between words allowed to be the line break
    the file wraps it at. So the cut follows the prompt when it is rewrapped
    instead of going quietly stale, and everything the file carries either
    side of the statement is byte for byte what it was.
    """
    (statement,) = [one for one in sentences(triage_paragraph(text))
                    if PERMITTED_DIVERGENCE_PHRASE in one]
    wrapped = re.compile(r"\s+".join(re.escape(word)
                                     for word in statement.split()) + r"\s*")
    cut, replaced = wrapped.subn("", text, count=1)
    assert replaced == 1 and cut != text, statement
    return cut


def with_the_statement_planted(text: str, statement: str) -> str:
    """A copy of the whole prompt with `statement` planted into its grounds
    paragraph, immediately before the sentence saying a declined finding is
    recorded — which is where the prompt that carries the statement carries
    it."""
    anchor = "A declined routed finding"
    assert text.count(anchor) == 1, anchor
    planted = text.replace(anchor, f"{statement} {anchor}", 1)
    assert planted != text
    return planted


def the_statement(text: str) -> str:
    """The statement closing the ground, as the prompt that carries it words
    it — so a control planting it elsewhere plants the real sentence rather
    than an imitation of it."""
    (statement,) = [one for one in sentences(triage_paragraph(text))
                    if PERMITTED_DIVERGENCE_PHRASE in one]
    return statement


#: The ground reopened, as the control puts it back: the clause the statement
#: opens on, replaced by one that offers what the statement closes.
OFFERED_AGAIN = "is a further ground for declining, and no repair pass reaches"
CLOSED_CLAUSE = "is not a reason either: the repair pass reaches"


def with_the_ground_offered_again(text: str) -> str:
    """A copy of the whole prompt in which the statement offers the ground it
    closes. Planted on the one line the clause occupies whole, so nothing
    straddles a line break."""
    assert text.count(CLOSED_CLAUSE) == 1, CLOSED_CLAUSE
    reopened = text.replace(CLOSED_CLAUSE, OFFERED_AGAIN, 1)
    assert reopened != text
    return reopened


# ==========================================================================
# The prompt of the workflow that declares a validating stage
# ==========================================================================


def test_the_two_prompts_are_told_apart_by_a_declaration_and_not_by_a_name():
    """The premise everything below rests on: which prompt carries the new
    statements is read off the workflows, where one declares a stage confined
    to the tests directory and the other declares none. Both prompts exist and
    are different files, so "one of them and not the other" is a claim about
    two texts rather than about one text compared with itself."""
    carrying = verifier_prompts_declaring_validation()
    assert len(carrying) == 1, carrying
    assert set(carrying) < set(verifier_prompts()), carrying
    for prompt in verifier_prompts():
        assert prompt.is_file(), prompt
    assert the_carrying_prompt() != the_other_prompt()


def test_the_grounds_paragraph_closes_the_ground_of_needing_new_validation():
    """The first criterion: the paragraph enumerating the grounds a verdict may
    decline a routed finding on says that a fix needing new standing validation
    is not among them, and says why — the repair pass reaches the stage whose
    work is validation, so the assertion is written inside this run."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    assert closed_ground_problems(text) == []


def test_the_reading_reports_the_statement_cut_out_of_the_whole_prompt():
    """Control for the assertion above. The statement is cut out of a copy of
    the whole shipped prompt and the same paragraph-and-sentence reading is run
    again over that copy, which reports it — so a clean result is a fact about
    the prompt rather than about a reading that has stopped finding the
    paragraph or a search that matches nothing."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    assert closed_ground_problems(text) == []
    cut = with_the_statement_removed(text)
    assert PERMITTED_DIVERGENCE_PHRASE not in flowed(cut)
    reported = closed_ground_problems(cut)
    assert reported, cut
    # And the cut took the statement and nothing else: the grounds the
    # paragraph names are still there to be read.
    for ground in GROUNDS:
        assert ground in triage_paragraph(cut), ground


def test_the_reading_reports_the_ground_offered_again():
    """Control for the same assertion from the other direction, and the shape
    the defect would really take: the statement is still there and still about
    a fix needing new standing validation, but it offers the ground instead of
    closing it. The same reading reports every claim it stopped making."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    reopened = with_the_ground_offered_again(text)
    reported = closed_ground_problems(reopened)
    assert any("is not a reason either" in problem for problem in reported), \
        reported
    assert any("the repair pass reaches" in problem for problem in reported), \
        reported


def test_the_routes_paragraph_says_the_assertion_is_written_by_another_stage():
    """The second criterion: the paragraph rendering the retry routes says this
    workflow declares a stage whose work is validation, that a repair pass runs
    forward from the stage it enters at through every stage after it, and that
    the assertion the fix needs is therefore written by a stage other than the
    one whose change it validates."""
    text = shipped_prompt_texts()[the_carrying_prompt()]
    assert routes_problems(text) == []


# ==========================================================================
# The grounds this story leaves exactly as they were
# ==========================================================================


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_every_ground_the_paragraph_already_named_is_still_named(prompt: Path):
    """The third criterion, and the constraint under it: closing one ground
    left the others alone. The sentence naming what may still be declined still
    names the design decision, the protected file and the finding that is
    wrong, and still says those grounds are not size. Asserted over both
    shipped prompts, because the story closes the ground in one of them and
    promises to have disturbed neither."""
    grounds = sentence_carrying(triage_paragraph(prompt.read_text(
        encoding="utf-8")), GROUNDS_PHRASE)
    assert "not size" in grounds, grounds
    for ground in GROUNDS:
        assert ground in grounds, (ground, grounds)


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_size_is_still_a_reason_to_fix_rather_than_a_reason_to_decline(
        prompt: Path):
    """The rest of that constraint: the rule story-202 put in this paragraph is
    untouched in both prompts, and the sentence mentioning size is still the
    sentence naming the two cheap routes."""
    sized = sentence_carrying(triage_paragraph(prompt.read_text(
        encoding="utf-8")), SIZE_PHRASE)
    assert "reason to fix" in sized, sized
    assert "correctable" in sized, sized
    assert "repairable" in sized, sized


# ==========================================================================
# The other shipped verifier prompt
# ==========================================================================


def test_the_other_prompt_states_neither_of_the_two_statements():
    """The fourth criterion: the workflow that declares no validating stage is
    told none of this. Its grounds paragraph carries no statement about a fix
    needing new standing validation, and its retry-routes paragraph says none
    of what the other one's now says. The controls below show both readings
    report each statement when it is planted there."""
    texts = shipped_prompt_texts()
    other = texts[the_other_prompt()]
    assert PERMITTED_DIVERGENCE_PHRASE not in flowed(other)
    assert sorted(routes_problems(other)) == sorted(ROUTE_WORDS), other


def test_the_closed_ground_is_found_when_it_is_planted_in_the_other_prompt():
    """Control for the first absence above: the statement the one prompt
    carries, planted into a copy of the *whole* other prompt where that one
    carries it, is found there by the same reading — so its absence is a fact
    about the shipped prompt rather than about a search matching nothing."""
    texts = shipped_prompt_texts()
    statement = the_statement(texts[the_carrying_prompt()])
    planted = with_the_statement_planted(texts[the_other_prompt()], statement)
    assert closed_ground_problems(planted) == [], planted


def test_the_route_statement_is_found_when_it_is_planted_in_the_other_prompt():
    """Control for the second absence: the routes paragraph of the one prompt,
    appended to the routes paragraph of a copy of the whole other prompt, is
    read back by the same reading, which then reports nothing missing."""
    texts = shipped_prompt_texts()
    carried = routes_paragraph(texts[the_carrying_prompt()])
    other = texts[the_other_prompt()]
    assert routes_problems(other) == list(ROUTE_WORDS)
    marker = f"{{{{{ROUTES_FIELD}}}}}"
    head, _, tail = other.partition(marker)
    paragraph, separator, rest = tail.lstrip("\n").partition("\n\n")
    planted = f"{head}{marker}\n\n{paragraph} {carried}{separator}{rest}"
    assert planted != other
    assert routes_problems(planted) == [], routes_problems(planted)


def test_the_other_prompt_still_says_its_workflow_authors_no_validation():
    """The rest of the fourth criterion: the sentence this story's new one is
    written against is left exactly as it is, in the same paragraph, and the
    prompt that gained the new statement does not acquire it."""
    texts = shipped_prompt_texts()
    assert THE_MIRROR in routes_paragraph(texts[the_other_prompt()])
    assert THE_MIRROR not in flowed(texts[the_carrying_prompt()])


# ==========================================================================
# What the narrowed parity guard reports
# ==========================================================================


def test_the_parity_guard_reports_the_statement_missing_where_it_belongs():
    """The sixth criterion, first half. The guard permits the statement in one
    prompt; it does not stop caring whether it is there. Cut out of a copy of
    the whole prompt, the statement's absence is reported — which is what makes
    the permission a narrowing rather than a hole."""
    texts = shipped_prompt_texts()
    permitted = prompts_that_may_carry_the_divergence()
    carrier = the_carrying_prompt()
    assert parity_problems(texts, permitted) == []

    cut = {**texts, carrier: with_the_statement_removed(texts[carrier])}
    reported = parity_problems(cut, permitted)
    assert any(problem.startswith(f"{carrier}:") for problem in reported), \
        reported


def test_the_parity_guard_reports_the_statement_in_the_prompt_it_is_false_of():
    """The sixth criterion, second half, and the one the passage comparison
    alone cannot make: planted into the other prompt the statement makes the
    two paragraphs agree again, so only a guard that knows which prompt may
    carry it reports the spread. This is what stops a statement true of one
    workflow reaching the verifier of a workflow it is false of."""
    texts = shipped_prompt_texts()
    permitted = prompts_that_may_carry_the_divergence()
    other = the_other_prompt()
    statement = the_statement(texts[the_carrying_prompt()])

    spread = {**texts, other: with_the_statement_planted(texts[other],
                                                         statement)}
    reported = parity_problems(spread, permitted)
    assert any(problem.startswith(f"{other}:") for problem in reported), \
        reported
