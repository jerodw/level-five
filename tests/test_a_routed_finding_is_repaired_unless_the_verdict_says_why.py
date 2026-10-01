"""story-164 validation: a finding about the story's own change is repaired
unless the verdict says why it was not.

The verifier used to be told three times that declining a finding is
legitimate and never what declining costs, and story-163's verdict recorded
zero findings over two the inspection had routed to it. This story inverts the
default where the verdict is decided: a finding routed to the story is
corrected or repaired unless there is a reason not to, the reason goes in a new
optional `declined_findings` array of the verdict, and the accounting the
coordinator writes after the verdict records, for each finding the verdict
left, the reason it gave or that it gave none.

The subjects, in the order the criteria state them:

  * **both verifier prompts** — found through the shipped workflows' inspection
    declarations rather than named — state the inverted default, carry the cost
    sentence once, state the right to decline once rather than three times, no
    longer close either route on "nothing if it does not", give the repairable
    paragraph the story-artifact bound in the correctable paragraph's own
    words, and agree in the passages this story changed.

    That last claim was "character for character" until story-214, which gave
    one of the two prompts a statement that is true of its workflow alone: a
    fix needing new standing validation is no ground for declining there,
    because that workflow declares a stage whose work is validation and a
    repair pass reaches it. story-215 gave the same prompt a second such
    statement, asking for the assertion a repaired fix needs by naming the file
    it belongs in. So the parity walk names the statements it permits —
    `PERMITTED_DIVERGENCE_PHRASES`, declared here and imported one phrase at a
    time by the module whose claim is that its statement is in the prompt — and
    sets each aside a sentence at a time, keeping every other sentence of the
    same paragraph in the comparison. It gains two claims per permitted
    statement for the one it narrows: the statement is in the prompt of the
    workflow that declares a validating stage, and it is in no other. So the
    walk reports a shared sentence reworded in either prompt exactly as before,
    and reports any of the permitted statements gone missing from the prompt
    that should carry it or turning up in the one that should not.

    The widening from one blessed sentence to a list of them leaves what the
    walk holds exactly where it was, because the walk never pinned the
    paragraphs whole: it sets the blessed sentences aside and holds every
    remaining sentence of each walked passage to reading the same in both
    prompts. Those remaining sentences are shared mechanism — one file per
    finding, never a directory, never a blocked path, the bounds on reaching
    outside the story's scope, both kinds routing as a correction pass — which
    one coordinator runs for both workflows. What needed to diverge was two
    sentences, not the paragraph.

  * **the verdict schema** accepts a `declined_findings` entry carrying a slug
    and a reason, refuses one missing either, and still accepts every shape it
    accepted before the field existed.

  * **the accounting** — driven as functions and as coordinator runs against
    the fixture `test_inspection_findings_reach_the_story` builds — records
    beside each left finding the reason the verdict gave or that none was
    given, says the two counts in the events.log line, leaves a finding
    declined with a reason in the remainder and files it at completion exactly
    as before, and accounts a verdict carrying no `declined_findings` as
    declining everything silently. Nothing here stops or escalates a run.

The prompts and the schema are what this harness ships, so those cases read
the shipped artifacts as their subject. The accounting is a mechanism, so its
cases run against the sibling module's built workflow and derive every name
from it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import conftest
import harness_config
import schema_validator
import story_inspection
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    BOTH_FINDING,
    CORRECTABLE_FINDING,
    FINDINGS_ARTIFACT,
    OWN_FINDING,
    PASS,
    RETRY_CATEGORY,
    STORY_ID,
    accounting,
    accounting_events,
    build_target,
    drive,
    driven,
    failing_into,
    filed_events,
    filings,
    filings_of,
    harness,
    naming_the_slug,
    no_model,
    passing_with,
    repairing,
    routed_markers,
    target,
)

REPO_ROOT = conftest.HARNESS_ROOT

#: The verdict field this story adds, and the keys each of its entries carries.
#: Spelled here once; every assertion below derives from these.
DECLINED = "declined_findings"
SLUG = "slug"
REASON = "reason"

#: The context field the judging prompts render the routed findings under.
FINDINGS_FIELD = "inspection_findings"

VERDICT_SCHEMA = schema_validator.load_schema("verification-result")


# ==========================================================================
# The shipped verifier prompts
# ==========================================================================


def verifier_prompts() -> list[Path]:
    """The prompt of every stage declaring an inspection, in every workflow
    this repository ships — the stages whose verdicts the routed findings
    reach. Found through the declarations rather than named."""
    found = []
    for path in sorted((REPO_ROOT / "workflows").glob("*.json")):
        stages = json.loads(path.read_text(encoding="utf-8"))["stages"]
        found += [REPO_ROOT / "prompts" / stage["prompt"]
                  for stage in stages if stage.get("inspection")]
    return found


def verifier_prompts_declaring_validation() -> list[Path]:
    """Of the prompts `verifier_prompts` found, those whose workflow declares a
    stage whose work is validation — a stage confined to the tests directory
    this deployment configures.

    Derived rather than named, and resolved against this repository's own
    configuration the way a run resolves a definition, because a declaration
    read as text carries the token and not the directory. Which of the two
    shipped prompts may carry the permitted divergence is exactly what that
    divergence says about its own workflow, so the permission is read off the
    same fact the sentence states rather than off a file name.
    """
    tests_dir = conftest.repository_config()["tests_dir"]
    found = []
    for path in sorted((REPO_ROOT / "workflows").glob("*.json")):
        stages = conftest.shipped_workflow(name=path.stem)["stages"]
        if not any(tests_dir in (stage.get("may_only_change") or [])
                   for stage in stages):
            continue
        found += [REPO_ROOT / "prompts" / stage["prompt"]
                  for stage in stages if stage.get("inspection")]
    return found


def flowed(text: str) -> str:
    """`text` with its line wrapping removed, so a clause that straddles a
    newline is still found."""
    return " ".join(text.split())


def paragraphs(text: str) -> list[str]:
    return [flowed(one) for one in text.split("\n\n") if one.strip()]


def paragraph_containing(text: str, phrase: str) -> str:
    """The one paragraph of `text` carrying `phrase`, flowed. Exactly one:
    a phrase the prompt says twice is the repetition this story removes."""
    found = [one for one in paragraphs(text) if phrase in one]
    assert len(found) == 1, (phrase, found)
    return found[0]


def sentences(paragraph: str) -> list[str]:
    return [one.strip() for one in re.split(r"(?<=\.)\s+", paragraph)
            if one.strip()]


#: Each passage this story changed, keyed by the phrase that finds it. The
#: parity assertion walks these, so a passage that drifts between the two
#: prompts is named by its key.
DEFAULT_PHRASE = "is corrected or repaired unless there is a reason not to"
#: The default is stated in its own paragraph and restated where the findings
#: are rendered, so the paragraph is found by its opening rather than by the
#: default's words.
DEFAULT_OPENING = "A finding routed to this story"
CORRECTABLE_PHRASE = "Record in correctable_findings"
CORRECTION_BOUNDS_PHRASE = "A correction pass corrects words and never behaviour"
REPAIRABLE_PHRASE = "Record in repairable_findings"
PASSAGE_PHRASES = {
    "default": DEFAULT_OPENING,
    "correctable": CORRECTABLE_PHRASE,
    "correction-bounds": CORRECTION_BOUNDS_PHRASE,
    "repairable": REPAIRABLE_PHRASE,
}

#: story-214's statement: a fix needing new standing validation is no ground
#: for declining a routed finding under the workflow that declares a validating
#: stage. Kept under its own singular name because
#: `test_a_verdict_repairs_a_finding_needing_new_validation` imports that name
#: for its assertions about that sentence.
PERMITTED_DIVERGENCE_PHRASE = "needs new standing validation"

#: story-215's statement: where the fix a finding names needs validation that
#: does not exist yet, a second finding is recorded beside it naming the file
#: the assertion belongs in. Deliberately neither a substring nor a superstring
#: of the one above — that one reads "needs *new* standing validation" — so the
#: count check below holds each sentence to exactly one phrase and setting one
#: aside never takes the other's sentence with it.
#: `test_a_verdict_asks_for_the_assertion_its_repair_needs` imports this name
#: for its assertions about that sentence.
ASSERTION_REQUEST_PHRASE = "needs standing validation that does not exist yet"

#: Every statement the verifier prompt of the workflow that declares a
#: validating stage may carry and the other shipped verifier prompt may not,
#: each found by words that occur in exactly one of the two. The parity walk
#: below permits the sentence carrying any of these to differ and permits
#: nothing else, and the module whose claim is that one of these statements is
#: in the prompt imports its phrase from here — one declaration, so the guard
#: and the claims cannot disagree about which sentences may differ.
PERMITTED_DIVERGENCE_PHRASES = (PERMITTED_DIVERGENCE_PHRASE,
                                ASSERTION_REQUEST_PHRASE)

#: The sentence the correctable-findings bounds already carried and the
#: repairable paragraph lacked, by its opening words.
ARTIFACT_BOUND = "Do not record one against the approved story artifact"

#: The two closings this story removes, and the one statement of the right to
#: decline it keeps.
REMOVED_CLOSING = "nothing if it does not"
RIGHT_TO_DECLINE = "too small"


def slot_paragraph(text: str) -> str:
    """The paragraph introducing the rendered inspection findings — the last
    one before the slot, on the terms the sibling module reads it."""
    marker = f"{{{{{FINDINGS_FIELD}}}}}"
    return flowed(text[:text.index(marker)].rstrip().split("\n\n")[-1])


def test_the_default_is_carried_by_more_than_one_shipped_prompt():
    """The premise of the parity criterion: the refactor workflow carries a
    verifier prompt of its own, so "identical between the two" is a claim
    about two files rather than one file compared with itself."""
    prompts = verifier_prompts()
    assert len(set(prompts)) > 1, prompts
    for prompt in prompts:
        assert prompt.is_file(), prompt


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_prompt_states_the_inverted_default_and_where_the_reason_goes(
        prompt: Path):
    """A routed finding is corrected or repaired unless there is a reason not
    to, and the reason is recorded in declined_findings — both in one
    paragraph, so the default and its record are read together."""
    text = prompt.read_text(encoding="utf-8")
    paragraph = paragraph_containing(text, DEFAULT_OPENING)
    assert DEFAULT_PHRASE in paragraph
    assert DECLINED in paragraph
    assert "reason" in paragraph
    assert "recorded rather than silent" in paragraph
    # It also says what the array does not do, so the verifier does not read
    # an entry there as a route.
    assert "Nothing routes on that array" in paragraph
    assert "filed as a brief" in paragraph


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_prompt_states_the_cost_asymmetry_in_one_sentence(prompt: Path):
    """One sentence carries both halves of what the verifier cannot infer:
    what a declined routed finding costs and what a repair pass does not."""
    text = flowed(prompt.read_text(encoding="utf-8"))
    carrying = [one for one in sentences(text) if "planning interview" in one]
    assert len(carrying) == 1, carrying
    (sentence,) = carrying
    for cost in ("a brief", "a planning interview", "story run",
                 "a review", "a merge"):
        assert cost in sentence, (cost, sentence)
    for saving in ("spends no retry", "archives no attempt",
                   "recorded as passed"):
        assert saving in sentence, (saving, sentence)
    # Stated once: the repairable paragraph used to carry the saving clause
    # too, and a second statement is the one that goes stale. ("spends no
    # retry" is not counted — the self-route passage says it of a different
    # thing.)
    assert text.count("archives no attempt") == 1


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_neither_route_closes_on_nothing_if_it_does_not(prompt: Path):
    """The closing offer both routes used to end on is gone from the whole
    prompt. The control below shows the check reports it when present."""
    text = flowed(prompt.read_text(encoding="utf-8"))
    assert REMOVED_CLOSING not in text


def test_the_removed_closing_is_reported_when_it_is_put_back():
    """Control for the absence above: a copy of a shipped prompt with the old
    closing appended to its correctable-bounds paragraph is caught by the
    same check, so an empty result is about the prompt and not about a
    search that matches nothing."""
    text = verifier_prompts()[0].read_text(encoding="utf-8")
    bounds = paragraph_containing(text, CORRECTION_BOUNDS_PHRASE)
    assert REMOVED_CLOSING not in flowed(text)
    put_back = flowed(text).replace(
        bounds, bounds + f" That is a blocking issue if it matters and "
                         f"{REMOVED_CLOSING}.")
    assert put_back != flowed(text)
    assert REMOVED_CLOSING in put_back


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_right_to_decline_is_stated_once(prompt: Path):
    """"Too small" is said exactly once per prompt — in the paragraph that
    states the default — rather than in three passages as the fallback. The
    red-suite paragraph, which used to repeat the phrase while saying it does
    not apply there, no longer does."""
    text = flowed(prompt.read_text(encoding="utf-8"))
    assert text.count(RIGHT_TO_DECLINE) == 1, text.count(RIGHT_TO_DECLINE)
    assert RIGHT_TO_DECLINE in paragraph_containing(
        prompt.read_text(encoding="utf-8"), DEFAULT_OPENING)


def test_a_second_statement_of_the_right_is_counted():
    """Control for the count above: appending the old opening of the
    correctable paragraph to a copy makes the count two, so a count of one
    is a fact about the prompt and not about a phrase the count never
    matched."""
    text = flowed(verifier_prompts()[0].read_text(encoding="utf-8"))
    assert text.count(RIGHT_TO_DECLINE) == 1
    doubled = text + (" A finding can be correct and still be too small to "
                      "fail a run.")
    assert doubled.count(RIGHT_TO_DECLINE) == 2


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_repairable_paragraph_carries_the_story_artifact_bound(
        prompt: Path):
    """The bound the correctable-findings bounds paragraph already carries,
    in the same words: the sentence opening on the same clause is found in
    both paragraphs and is the same sentence."""
    text = prompt.read_text(encoding="utf-8")
    correctable = paragraph_containing(text, CORRECTION_BOUNDS_PHRASE)
    repairable = paragraph_containing(text, REPAIRABLE_PHRASE)

    def bound_in(paragraph: str) -> str:
        found = [one for one in sentences(paragraph)
                 if one.startswith(ARTIFACT_BOUND)]
        assert len(found) == 1, (paragraph, found)
        return found[0]

    assert bound_in(correctable) == bound_in(repairable)
    assert "nothing rewrites that mid-run" in bound_in(repairable)
    assert "leave it alone" in bound_in(repairable)


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_correctable_and_repairable_paragraphs_keep_their_bounds(
        prompt: Path):
    """Inverting the default did not loosen what belongs in each route: the
    correctable paragraph still bounds itself to the words alone, the
    repairable one still to one stage in one file, not to prose alone, routed
    after the clean-clone check, and not a licence to revisit the work."""
    text = prompt.read_text(encoding="utf-8")
    correctable = paragraph_containing(text, CORRECTABLE_PHRASE)
    assert "words alone" in correctable
    assert "spelled exactly as" in correctable
    assert "`path`" in correctable
    repairable = paragraph_containing(text, REPAIRABLE_PHRASE)
    assert "one stage" in repairable
    assert "not to prose alone" in repairable
    assert "spelled exactly as" in repairable
    assert "clean-clone check" in repairable
    assert "not a licence to revisit the work" in repairable
    bounds = paragraph_containing(text, CORRECTION_BOUNDS_PHRASE)
    assert "never behaviour" in bounds
    assert "recording a finding does not fail the run" in bounds


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_inspection_slot_points_at_the_passages_and_the_field(
        prompt: Path):
    """The slot no longer restates the too-small rule; it says these are the
    findings routed to this story, that the passages above are the terms,
    that the reason goes in declined_findings, names a repairable finding
    among the entries that may carry a slug, and says what the accounting
    records for a finding left without an entry."""
    slot = slot_paragraph(prompt.read_text(encoding="utf-8"))
    assert "routed to this story" in slot
    assert DEFAULT_PHRASE.split(" unless")[0] in slot
    assert DECLINED in slot
    assert "repairable finding" in slot
    assert "`slug`" in slot
    assert "`location`" in slot
    assert "filed as a brief" in slot
    assert "recorded as silent" in slot
    assert RIGHT_TO_DECLINE not in slot


def without_the_permitted_divergence(passage: str) -> str:
    """`passage` with every sentence carrying one of
    `PERMITTED_DIVERGENCE_PHRASES` taken out of it, and every other sentence
    left exactly as it is.

    Sentence at a time rather than paragraph at a time: dropping the whole
    paragraph from the comparison to make room for a statement would leave a
    guard that catches nothing, and every other sentence of that paragraph is
    still held to reading the same in both prompts.

    A list of phrases since story-215 rather than the single one story-214
    declared, and the list is what makes the widening a widening: a second
    story-only sentence in a walked passage is a divergence the walk must
    permit by name, not a reason to stop comparing the passage it sits in.
    """
    return " ".join(one for one in sentences(passage)
                    if not any(phrase in one
                               for phrase in PERMITTED_DIVERGENCE_PHRASES))


def parity_problems(texts: dict[str, str], permitted: set[str]) -> list[str]:
    """Every divergence the parity walk finds across the given prompt texts,
    keyed by prompt name. Empty means they agree.

    Two kinds. Each permitted statement must be in each prompt named in
    `permitted` and in no other, so none of them goes missing from the prompt
    whose workflow the statement is true of and none spreads to the one it is
    false of — counted per phrase, so a prompt carrying one of them and not the
    other is reported rather than passing on a total. And each walked passage,
    once those sentences are set aside, must read the same in every prompt — so
    a sentence the prompts share, reworded in any of them, is reported exactly
    as it was before the walk was narrowed.

    A function rather than a test body so the controls can put mutated copies
    of the shipped prompts through the same walk the shipped assertion makes.
    """
    problems = []
    for name, text in sorted(texts.items()):
        whole = flowed(text)
        expected = 1 if name in permitted else 0
        for phrase in PERMITTED_DIVERGENCE_PHRASES:
            carried = whole.count(phrase)
            if carried != expected:
                problems.append(f"{name}: carries the permitted statement "
                                f"{phrase!r} {carried} times, expected "
                                f"{expected}")
    for key, phrase in PASSAGE_PHRASES.items():
        found = {}
        for name, text in texts.items():
            passage = paragraph_containing(text, phrase)
            assert phrase in passage and len(passage) > len(phrase), (key, name)
            found[name] = without_the_permitted_divergence(passage)
            assert phrase in found[name], (key, name)
        if len(set(found.values())) != 1:
            problems.append(f"{key}: {found}")
    slots = {name: slot_paragraph(text) for name, text in texts.items()}
    for name, slot in slots.items():
        assert DECLINED in slot, (name, slot)
    if len(set(slots.values())) != 1:
        problems.append(f"slot: {slots}")
    return problems


def shipped_prompt_texts() -> dict[str, str]:
    """Each shipped verifier prompt's whole text, by file name."""
    return {prompt.name: prompt.read_text(encoding="utf-8")
            for prompt in verifier_prompts()}


def prompts_that_may_carry_the_divergence() -> set[str]:
    """The names of the prompts the walk permits the statement in."""
    return {prompt.name for prompt in verifier_prompts_declaring_validation()}


def test_exactly_one_shipped_prompt_may_carry_the_permitted_divergence():
    """The premise the narrowing rests on: the permission is derived from a
    fact that separates the two shipped workflows rather than granted to
    everything the walk looks at. One of them declares a stage confined to the
    tests directory and the other declares none, so one prompt may carry the
    statement and the other may not."""
    permitted = set(verifier_prompts_declaring_validation())
    assert len(permitted) == 1, permitted
    assert permitted < set(verifier_prompts()), permitted


def test_each_permitted_phrase_names_a_different_sentence():
    """The premise the widened walk rests on: the declared phrases are a list of
    distinct statements rather than several spellings of one.

    In the prompt permitted to carry them, each phrase is carried by exactly one
    sentence, no two phrases are carried by the same sentence, and neither is
    found inside another. Without that, setting one phrase's sentence aside
    could take another's with it and the per-phrase count could be satisfied by
    a single sentence — either of which would make the widening a hole rather
    than a list.
    """
    (carrier,) = prompts_that_may_carry_the_divergence()
    whole = sentences(flowed(shipped_prompt_texts()[carrier]))
    carried = {phrase: [one for one in whole if phrase in one]
               for phrase in PERMITTED_DIVERGENCE_PHRASES}
    for phrase, found in carried.items():
        assert len(found) == 1, (phrase, found)
    assert len({found[0] for found in carried.values()}) == len(carried)
    for phrase in PERMITTED_DIVERGENCE_PHRASES:
        others = [one for one in PERMITTED_DIVERGENCE_PHRASES if one != phrase]
        assert not any(phrase in one or one in phrase for one in others), phrase


def test_the_passages_are_identical_between_the_shipped_prompts():
    """The refactor workflow carries the same default: every passage this
    story changed, and the inspection slot, reads the same in each shipped
    verifier prompt, apart from the statements `PERMITTED_DIVERGENCE_PHRASES`
    declares for the prompt of the workflow that declares a validating stage.
    Each passage is checked non-empty and to carry its own phrase first, so
    equality is between two found passages and not between two failures to find
    one."""
    texts = shipped_prompt_texts()
    assert len(texts) > 1, texts
    assert parity_problems(texts, prompts_that_may_carry_the_divergence()) == []


def test_the_parity_check_tells_two_passages_apart():
    """Control for the equality above: the same extraction over a copy whose
    repairable paragraph has one word changed finds two different passages."""
    text = verifier_prompts()[0].read_text(encoding="utf-8")
    original = paragraph_containing(text, REPAIRABLE_PHRASE)
    altered = text.replace("not a licence to revisit",
                           "not a permit to revisit", 1)
    assert altered != text
    assert paragraph_containing(altered, REPAIRABLE_PHRASE) != original


#: A clause both shipped prompts carry in the one paragraph the walk now
#: permits a divergence in, and one word of it. Rewording it is the drift the
#: narrowing must not have stopped reporting.
SHARED_IN_THE_NARROWED_PASSAGE = "wrong on the evidence"
REWORDED = "wrong on the record"


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_narrowed_walk_still_reports_a_shared_sentence_reworded(
        prompt: Path):
    """Control for the narrowing, in the paragraph the narrowing is about: a
    sentence both prompts share, reworded in a copy of either one of them,
    is still reported. Run for each prompt in turn, because a walk that
    happened to compare one prompt against itself would be silent for the
    other. The whole shipped text is mutated and put back through the same
    walk the shipped assertion makes, so what reports the drift is the guard
    rather than a search over a string this test just edited."""
    texts = shipped_prompt_texts()
    permitted = prompts_that_may_carry_the_divergence()
    assert parity_problems(texts, permitted) == []

    drifted = texts[prompt.name].replace(SHARED_IN_THE_NARROWED_PASSAGE,
                                         REWORDED, 1)
    assert drifted != texts[prompt.name]
    reported = parity_problems({**texts, prompt.name: drifted}, permitted)
    assert any(problem.startswith("default:") for problem in reported), reported


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_red_suite_paragraph_still_says_a_red_suite_is_a_verdict(
        prompt: Path):
    """Rewording the cross-reference to the right to decline did not move
    what the red-suite paragraph decides: a non-zero exit code is a failing
    verdict and not a finding, and nothing above applies there."""
    text = flowed(prompt.read_text(encoding="utf-8"))
    assert "is a failing verdict and not a finding" in text
    assert "neither applies here" in text
    assert "the verdict is failed" in text


# ==========================================================================
# The verdict schema
# ==========================================================================


def declining(*entries, base: dict = PASS) -> dict:
    """A verdict carrying the entries exactly as given, so an entry of the
    wrong shape reaches the validator or the reader under test rather than
    being refused here."""
    return {**base, DECLINED: list(entries)}


DECLINED_ENTRY = {SLUG: OWN_FINDING["slug"],
                  REASON: "MARKER-REASON too small to be worth a pass"}


def test_the_schema_declares_declined_findings_optional_with_slug_and_reason():
    """Presence is the signal, like its siblings: not required, an array of
    objects each requiring exactly slug and reason, both strings."""
    assert DECLINED in VERDICT_SCHEMA["properties"]
    assert DECLINED not in VERDICT_SCHEMA.get("required", [])
    field = VERDICT_SCHEMA["properties"][DECLINED]
    assert field["type"] == "array"
    item = field["items"]
    assert set(item["required"]) == {SLUG, REASON}
    assert item["properties"][SLUG]["type"] == "string"
    assert item["properties"][REASON]["type"] == "string"


def test_the_schemas_description_says_what_the_field_is_and_is_not():
    """Presence-as-signal on its siblings' terms, nothing routes on it, and
    an unnamed slug is still filed as a brief whether or not declined here."""
    description = VERDICT_SCHEMA["properties"][DECLINED]["description"]
    assert "presence-as-signal" in description
    assert "Nothing routes on it" in description
    assert "brief" in description or "files" in description
    assert "whether or not" in description
    assert "without a reason" in description


def test_a_verdict_declining_with_slug_and_reason_validates():
    assert schema_validator.validate(declining(DECLINED_ENTRY),
                                     VERDICT_SCHEMA) == []
    two = declining(DECLINED_ENTRY,
                    {SLUG: BOTH_FINDING["slug"], REASON: "wrong on the evidence"})
    assert schema_validator.validate(two, VERDICT_SCHEMA) == []
    assert schema_validator.validate(declining(), VERDICT_SCHEMA) == []


@pytest.mark.parametrize("missing", [REASON, SLUG])
def test_an_entry_missing_slug_or_reason_is_refused_naming_the_field(missing):
    """Control for the acceptance above, and the criterion: an entry missing
    reason is refused, and the refusal names what is missing."""
    broken = declining({key: value for key, value in DECLINED_ENTRY.items()
                        if key != missing})
    errors = schema_validator.validate(broken, VERDICT_SCHEMA)
    assert errors
    assert any(missing in error for error in errors), errors


@pytest.mark.parametrize("wrong", [
    {SLUG: 3, REASON: "a reason"},
    {SLUG: OWN_FINDING["slug"], REASON: ["a reason"]},
    "not an entry",
])
def test_an_entry_of_the_wrong_shape_is_refused(wrong):
    assert schema_validator.validate(declining(wrong), VERDICT_SCHEMA)


def every_shape_accepted_before() -> list[dict]:
    """The verdict shapes the schema accepted before this story, built from
    the sibling module's own verdicts rather than listed here."""
    correctable = passing_with(CORRECTABLE_FINDING)
    return [
        PASS,
        correctable,
        repairing(naming_the_slug(OWN_FINDING, {**CORRECTABLE_FINDING})),
        {**correctable, "repairable_findings": correctable["correctable_findings"]},
        failing_into(RETRY_CATEGORY),
        {**failing_into(RETRY_CATEGORY), "retry_recommended": False,
         "unfinishable_by_retry": "the work does not close in the budget"},
    ]


@pytest.mark.parametrize("verdict", every_shape_accepted_before(),
                         ids=lambda v: v["status"] + ":" + ",".join(
                             sorted(k for k in v if k not in PASS)))
def test_every_shape_accepted_before_still_validates(verdict):
    """Adding an optional field refuses nothing that validated before."""
    assert schema_validator.validate(verdict, VERDICT_SCHEMA) == [], verdict
    # And each still validates with the new field beside its others.
    assert schema_validator.validate(declining(DECLINED_ENTRY, base=verdict),
                                     VERDICT_SCHEMA) == []


# ==========================================================================
# The accounting, driven as functions
# ==========================================================================


def test_declined_reasons_maps_each_slug_to_the_reason_the_verdict_gave():
    reasons = story_inspection.declined_reasons(declining(
        DECLINED_ENTRY, {SLUG: BOTH_FINDING["slug"], REASON: "second"}))
    assert reasons == {OWN_FINDING["slug"]: DECLINED_ENTRY[REASON],
                       BOTH_FINDING["slug"]: "second"}


def test_declined_reasons_is_empty_for_a_verdict_carrying_none():
    """A verdict with no field, an empty array, or no verdict at all: nothing
    to map, and nothing raised."""
    assert story_inspection.declined_reasons(PASS) == {}
    assert story_inspection.declined_reasons(declining()) == {}
    assert story_inspection.declined_reasons({**PASS, DECLINED: None}) == {}
    assert story_inspection.declined_reasons("not a verdict") == {}
    assert story_inspection.declined_reasons(None) == {}


def test_declined_reasons_reads_defensively_and_keeps_the_first_entry():
    """An entry the schema would have refused contributes nothing; the first
    entry naming a slug wins over a later one."""
    reasons = story_inspection.declined_reasons(declining(
        "not an entry",
        {SLUG: "", REASON: "no slug"},
        {SLUG: OWN_FINDING["slug"], REASON: "   "},
        {SLUG: OWN_FINDING["slug"]},
        {SLUG: OWN_FINDING["slug"], REASON: "first"},
        {SLUG: OWN_FINDING["slug"], REASON: "second"},
        {SLUG: 7, REASON: "not a string"},
    ))
    assert reasons == {OWN_FINDING["slug"]: "first"}


def test_declined_accounting_marks_a_reason_given_or_not_per_left_finding():
    """The marker that no reason was given is the boolean: an entry without
    a reason carries no reason key at all, rather than an empty string a
    reader could take for a blank reason."""
    reasons = {OWN_FINDING["slug"]: DECLINED_ENTRY[REASON]}
    declined = story_inspection.declined_accounting(
        [OWN_FINDING, BOTH_FINDING], reasons)
    # `dropped` is story-202's addition beside `reason_given`, and it is false
    # here for the reason it is written at all: a caller that names no dropped
    # slug — which is every caller this test is about — records every left
    # finding as one the run files rather than drops. `abandoned` is
    # story-213's, on exactly those terms: a caller that names no abandoned
    # slug records every left finding as one a pass allowance did not refuse.
    # `refused_files` and `decision_rejected` are story-221's, written on
    # every entry on the same terms: a caller that names no refusal and no
    # rejection records that no part of the fix is owed and no decision was
    # rejected.
    assert declined == [
        {SLUG: OWN_FINDING["slug"], "reason_given": True,
         REASON: DECLINED_ENTRY[REASON], "dropped": False, "abandoned": False,
         "refused_files": [], "decision_rejected": False},
        {SLUG: BOTH_FINDING["slug"], "reason_given": False, "dropped": False,
         "abandoned": False, "refused_files": [], "decision_rejected": False},
    ]
    assert story_inspection.declined_accounting([], reasons) == []


def test_a_left_finding_with_no_slug_is_declined_without_a_reason():
    """Nothing can name it, so nothing can have declined it by name."""
    unnamed = {key: value for key, value in OWN_FINDING.items()
               if key != SLUG}
    (entry,) = story_inspection.declined_accounting(
        [unnamed], {OWN_FINDING["slug"]: "a reason"})
    assert entry["reason_given"] is False
    assert REASON not in entry


def test_write_accounting_without_declined_records_every_left_as_silent(
        tmp_path):
    """A caller that passes no `declined` — what every caller was before this
    story — records every left finding as declined without a reason, and
    `remainder_slugs` is what it was."""
    run_dir = tmp_path / "run"
    story_inspection.write_accounting(
        run_dir, FINDINGS_ARTIFACT, 1, STORY_ID,
        routed=[OWN_FINDING, BOTH_FINDING], acted_on=[],
        remainder=[OWN_FINDING, BOTH_FINDING])
    recorded = json.loads((run_dir / story_inspection.accounting_artifact_file(
        FINDINGS_ARTIFACT, 1)).read_text(encoding="utf-8"))
    assert recorded["remainder_slugs"] == [OWN_FINDING["slug"],
                                           BOTH_FINDING["slug"]]
    # `dropped` and `abandoned` false on every entry, for the reason the test
    # beside this one records: a caller that names no dropped slug and no
    # abandoned one records every left finding as one the run files. story-221's
    # `refused_files` and `decision_rejected` are empty and false on the same
    # terms.
    assert recorded["declined"] == [
        {SLUG: OWN_FINDING["slug"], "reason_given": False, "dropped": False,
         "abandoned": False, "refused_files": [], "decision_rejected": False},
        {SLUG: BOTH_FINDING["slug"], "reason_given": False, "dropped": False,
         "abandoned": False, "refused_files": [], "decision_rejected": False},
    ]
    assert recorded["declined_with_reason"] == 0
    assert recorded["declined_without_reason"] == 2


def test_write_accounting_counts_agree_with_the_entries_it_writes(tmp_path):
    run_dir = tmp_path / "run"
    declined = story_inspection.declined_accounting(
        [OWN_FINDING, BOTH_FINDING], {BOTH_FINDING["slug"]: "a reason"})
    story_inspection.write_accounting(
        run_dir, FINDINGS_ARTIFACT, 2, STORY_ID,
        routed=[OWN_FINDING, BOTH_FINDING], acted_on=[],
        remainder=[OWN_FINDING, BOTH_FINDING], declined=declined)
    recorded = json.loads((run_dir / story_inspection.accounting_artifact_file(
        FINDINGS_ARTIFACT, 2)).read_text(encoding="utf-8"))
    assert recorded["declined"] == declined
    assert recorded["declined_with_reason"] == sum(
        1 for one in declined if one["reason_given"])
    assert recorded["declined_without_reason"] == sum(
        1 for one in declined if not one["reason_given"])
    assert recorded["declined_with_reason"] + \
        recorded["declined_without_reason"] == recorded["remainder"]


# ==========================================================================
# The accounting, driven as coordinator runs
# ==========================================================================


def declined_by_slug(recorded: dict) -> dict:
    return {one[SLUG]: one for one in recorded["declined"]}


def the_accounting_line(run) -> str:
    events = accounting_events(run)
    assert len(events) == 1, events
    return events[0]["message"]


def test_a_verdict_declining_with_a_reason_is_accounted_with_that_reason(
        driven, filings):
    """The criterion driven end to end: two findings routed, a passing
    verdict declining one by slug with a reason and saying nothing of the
    other. The artifact carries the reason beside the one and the marker
    beside the other, the events.log line says one of each, both are still
    the remainder, both are filed at completion exactly as before, and the
    run completes."""
    run = driven(findings=[OWN_FINDING, BOTH_FINDING],
                 verdicts=[declining(DECLINED_ENTRY)])

    assert run.code == 0
    assert run.state["status"] == "completed"

    recorded = accounting(run)
    assert recorded["routed"] == 2
    assert recorded["acted_on"] == 0
    assert sorted(recorded["remainder_slugs"]) == sorted(
        [OWN_FINDING["slug"], BOTH_FINDING["slug"]])
    by_slug = declined_by_slug(recorded)
    assert set(by_slug) == set(recorded["remainder_slugs"])
    assert by_slug[OWN_FINDING["slug"]]["reason_given"] is True
    assert by_slug[OWN_FINDING["slug"]][REASON] == DECLINED_ENTRY[REASON]
    assert by_slug[BOTH_FINDING["slug"]]["reason_given"] is False
    assert REASON not in by_slug[BOTH_FINDING["slug"]]
    assert recorded["declined_with_reason"] == 1
    assert recorded["declined_without_reason"] == 1

    line = the_accounting_line(run)
    assert "2 left by it for the run's completion" in line
    assert "1 declined with a reason and 1 declined without one" in line
    assert line in run.messages

    # Still the remainder, still filed once at completion, as before.
    assert routed_markers(run) == sorted(
        [OWN_FINDING["body"], BOTH_FINDING["body"]])
    assert filings_of(filings, OWN_FINDING) == 1
    assert filings_of(filings, BOTH_FINDING) == 1
    (filed,) = filed_events(run)
    assert filed["filed"] == 2


def test_a_verdict_carrying_no_declined_findings_is_accounted_as_before(
        driven, filings):
    """The negative control and the compatibility criterion together: the
    same run with the verdict the sibling module has always driven records
    every left finding as declined without a reason, the line says so, and
    nothing else about the run moves."""
    run = driven(findings=[OWN_FINDING, BOTH_FINDING])

    assert run.code == 0
    assert run.state["status"] == "completed"
    recorded = accounting(run)
    assert sorted(recorded["remainder_slugs"]) == sorted(
        [OWN_FINDING["slug"], BOTH_FINDING["slug"]])
    assert all(one["reason_given"] is False for one in recorded["declined"])
    assert all(REASON not in one for one in recorded["declined"])
    assert recorded["declined_with_reason"] == 0
    assert recorded["declined_without_reason"] == 2

    line = the_accounting_line(run)
    assert "0 declined with a reason and 2 declined without one" in line
    assert routed_markers(run) == sorted(
        [OWN_FINDING["body"], BOTH_FINDING["body"]])
    assert filings_of(filings, OWN_FINDING) == 1
    assert filings_of(filings, BOTH_FINDING) == 1


def test_a_finding_declined_with_a_reason_is_filed_the_same_as_one_declined_silently(
        target, harness, tmp_path, filings):
    """The reason changes the record and nothing about the filing: one run
    declining the finding with a reason and one saying nothing of it leave
    the same queue entry and each make one filing call carrying it. The two
    runs are driven against two targets, so neither reads the other's tree."""
    with_reason = drive(target, harness, tmp_path,
                        findings=[OWN_FINDING],
                        verdicts=[declining(DECLINED_ENTRY)], name="reasoned")
    reasoned_queue = what_was_filed(with_reason.queue)
    assert filings_of(filings, OWN_FINDING) == 1
    assert accounting(with_reason)["declined_with_reason"] == 1

    silent = drive(build_target(tmp_path / "silent-target"), harness, tmp_path,
                   findings=[OWN_FINDING], name="silent")
    silent_queue = what_was_filed(silent.queue)
    assert accounting(silent)["declined_without_reason"] == 1
    assert filings_of(filings, OWN_FINDING) == 2

    assert reasoned_queue == silent_queue
    assert len(reasoned_queue) == 1
    assert OWN_FINDING["body"] in reasoned_queue[0]
    # The reason is the accounting's and not the brief's: it reaches no entry.
    assert DECLINED_ENTRY[REASON] not in reasoned_queue[0]


def what_was_filed(queue: list[dict]) -> list[str]:
    """Each queue entry's key, identity and payload — what a filing says —
    without the timestamps the outbox stamps on the entry when it lands."""
    return sorted(json.dumps({field: entry[field]
                              for field in ("key", "identity", "payload")},
                             sort_keys=True) for entry in queue)


def test_a_declined_entry_naming_a_finding_the_verdict_acted_on_is_unused(
        driven, filings):
    """Acted-on wins: a verdict that names a slug in a correctable finding
    and also declines it has acted on it. The finding is not in the
    remainder, appears in no declined entry, and both counts are zero."""
    verdict = declining(DECLINED_ENTRY,
                        base=passing_with(naming_the_slug(OWN_FINDING)))
    run = driven(findings=[OWN_FINDING], verdicts=[verdict, PASS])

    assert run.code == 0
    recorded = accounting(run)
    assert recorded["acted_on_slugs"] == [OWN_FINDING["slug"]]
    assert recorded["remainder_slugs"] == []
    assert recorded["declined"] == []
    assert recorded["declined_with_reason"] == 0
    assert recorded["declined_without_reason"] == 0
    assert "0 declined with a reason and 0 declined without one" in \
        the_accounting_line(run)
    assert filings_of(filings, OWN_FINDING) == 0


def test_a_declined_entry_naming_no_routed_finding_is_recorded_nowhere(
        driven):
    """The declined list is about the remainder: an entry for a slug nothing
    routed contributes no entry, and the one left finding is silent."""
    stray = {SLUG: "zzz-nothing-routed-here", REASON: "a reason for nothing"}
    run = driven(findings=[OWN_FINDING], verdicts=[declining(stray)])

    assert run.code == 0
    recorded = accounting(run)
    assert [one[SLUG] for one in recorded["declined"]] == [OWN_FINDING["slug"]]
    assert recorded["declined_with_reason"] == 0
    assert recorded["declined_without_reason"] == 1
    assert stray[REASON] not in json.dumps(recorded)


def test_the_accounting_event_keeps_its_structured_fields(driven):
    """The counts live in the line; the structured record carries what it
    carried, so nothing reading the history by field meets a new key."""
    run = driven(findings=[OWN_FINDING, BOTH_FINDING],
                 verdicts=[declining(DECLINED_ENTRY)])
    (event,) = accounting_events(run)
    assert event["routed"] == 2
    assert event["acted_on"] == 0
    assert event["filed"] == 0
    assert event["dropped"] == 0
    assert "held" not in event["message"]


def test_the_reason_reaches_the_artifact_through_the_entry_point_alone(
        target, harness, tmp_path, no_model):
    """The accounting called directly, with the guard against any model
    invocation still in place: the reason is read off the verdict handed in
    and lands in the artifact and the events.log line, and nothing is
    filed."""
    run_dir = tmp_path / "accounted-run"
    run_dir.mkdir()
    story_inspection.write_findings(run_dir, FINDINGS_ARTIFACT, 1, STORY_ID,
                                    ran=True, findings=[OWN_FINDING, BOTH_FINDING])
    config = harness_config.load_config(target)

    answered = story_inspection.account_after_verdict(
        run_dir, target, config, harness, STORY_ID,
        artifact=FINDINGS_ARTIFACT, attempt=1, verdict=declining(DECLINED_ENTRY))

    assert answered is None
    assert no_model.calls == 0
    recorded = json.loads((run_dir / story_inspection.accounting_artifact_file(
        FINDINGS_ARTIFACT, 1)).read_text(encoding="utf-8"))
    by_slug = declined_by_slug(recorded)
    assert by_slug[OWN_FINDING["slug"]][REASON] == DECLINED_ENTRY[REASON]
    assert by_slug[BOTH_FINDING["slug"]]["reason_given"] is False
    assert recorded["filed"] == 0
    events = (run_dir / "events.log").read_text(encoding="utf-8")
    assert "1 declined with a reason and 1 declined without one" in events
