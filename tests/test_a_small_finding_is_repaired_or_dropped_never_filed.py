"""story-202 validation: a finding judged correct and too small to fail the
run is repaired or dropped, and never filed.

A reviewer at the end of a run could agree a defect was real, judge it not
worth stopping the run for, and by saying so send it to the backlog — where
the smallest thing anyone found became a planning conversation, a whole run,
a review and a merge. This story makes size a reason to fix and never a
reason to file: a correct finding a verifier judges small is corrected or
repaired, the grounds left for declining are not about size, and a correct
finding too small to be worth even those cheap routes is said so in a field
of its own and dropped — filed nowhere, recorded in the run's accounting and
named on the line the run writes when it completes.

The subjects, in the order the criteria state them:

  * **both shipped verifier prompts** — found through the shipped workflows'
    inspection declarations rather than named — state that size is a reason
    to fix, name the grounds for declining that are not size, name the new
    field for a correct finding too small to be worth any route, say in the
    inspection slot that a finding dropped through it is not filed, agree
    with each other in both passages, and leave the repairable-findings
    passage exactly as it stood before this story. That agreement sets aside
    the one sentence story-214 gives one workflow's prompt alone, named by
    that story's own declaration of which sentence may differ; every sentence
    carrying anything this story claims about the paragraph is still in the
    comparison.

  * **the verdict schema** declares the new field optional beside a
    `declined_findings` entry whose required set is still slug and reason,
    accepts a verdict carrying it, refuses one whose value is not a string,
    and no longer offers size as a ground in the reason's own description.

  * **the drop reason** is a constant of its own beside the severity floor's,
    distinct from every reason the filing already names.

  * **the accounting**, driven as functions and as coordinator runs against
    the fixture `test_inspection_findings_reach_the_story` builds: the new
    field is read by slug, each declined entry says whether it was dropped
    and carries the verdict's own words, the artifact names the dropped slugs
    and reads them back through the accessor the acted-on slugs are read
    through, and the events line says a drop happened only when one did.

  * **the filing at completion**: a dropped finding reaches no queue and no
    filing call while a finding declined for a reason that is not size
    reaches both exactly as before, the completion event names the drop
    reason with its count, a run whose whole remainder was dropped still
    writes that event, and a finding an entry's verdict dropped is not
    recovered and filed from that entry after a resume.

  * **the compatibility criterion**: a verdict carrying the field nowhere is
    accounted, filed and reported as it was, and every field the accounting
    artifact carried before this story is still there with what it had.

Every absence asserted here is demonstrated to be able to fail:

  * "no verifier prompt offers smallness as a ground for declining" sits
    beside a copy of the whole prompt with the old clause planted back into
    it, read again through the same paragraph and sentence extractions, which
    report it;
  * "the schema's reason description no longer offers being too small to be
    worth a pass" sits beside the same search over that description with the
    clause restored;
  * "the repairable passage is unchanged" sits beside the same comparison
    against a mutated copy of the baseline text, which goes red;
  * "a dropped finding is in no queue entry and no filing call" sits beside a
    second routed finding of the same run, which the same readings do find
    there, and beside the accounting, which shows the dropped one was found,
    routed and left by the verdict before it went unfiled;
  * "a verdict dropping nothing says nothing about dropping" sits beside the
    dropping run, whose line the same search does match;
  * "the finding an earlier entry dropped is not recovered" sits beside that
    entry's own held finding, which the same resume does recover.

Nothing here reaches a model: the sibling module's guard on
`agent_runner.run_agent` is installed for every test here too. The prompts and
the schema are what this harness ships, so the cases about them read the
shipped artifacts as their subject; the accounting and the filing are
mechanisms, so their cases run against the sibling modules' built workflow and
derive every stage, artifact and category name from it. The one comparison
against this repository's history is resolved through the shared reader at
this module's own story bound, never against HEAD or the working tree.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import conftest
import inspection
import schema_validator
import story_inspection
from conftest import BASELINE, ENDPOINT, function_source_at, repository_file_at

import test_one_defect_found_on_several_attempts_is_filed_once as filed_once
from test_a_routed_finding_is_repaired_unless_the_verdict_says_why import (
    DECLINED,
    DEFAULT_OPENING,
    REASON,
    REPAIRABLE_PHRASE,
    SLUG,
    VERDICT_SCHEMA,
    paragraph_containing,
    sentences,
    slot_paragraph,
    verifier_prompts,
    without_the_permitted_divergence,
)
from test_inspection_findings_reach_the_story import (  # noqa: F401 - fixtures used by name
    BOTH_FINDING,
    CHANGED_FILE,
    FINDINGS_ARTIFACT,
    OWN_FINDING,
    PASS,
    STORY_ID,
    WORKFLOW,
    accounting,
    accounting_events,
    build_target,
    driven,
    filed_events,
    filings,
    filings_of,
    harness,
    materialize,
    no_model,
    routed_markers,
    target,
)

#: The verdict field this story adds, and the key the accounting records the
#: drop under. Spelled here once; every assertion below derives from these.
NOT_WORTH_FILING = "not_worth_filing"
DROPPED = "dropped"

#: The key the accounting artifact names the dropped slugs under, read from
#: the accessor that reads them back rather than written twice.
DROPPED_SLUGS = "dropped_slugs"

#: What a *later* story added to the accounting artifact, and to each declined
#: entry, beside what this story added. Named here rather than the two
#: equalities below being loosened into containment checks, which is the shape
#: `tests/test_a_brief_is_not_only_a_defect.py` uses for the same problem — and
#: held shut from both sides, so a field the writers gain without an entry here
#: fails on the equality and an entry naming a field they no longer write fails
#: on the presence check beside it.
#:
#: story-213's three are the abandonment a spent pass allowance leaves: two
#: lists on the artifact and a marker on every declined entry. They are not
#: about dropping and this story's claim is only about what *it* added, so
#: excluding them by name is what keeps that claim an equality. The claim they
#: carry is held by
#: `tests/test_a_completed_run_says_what_became_of_its_findings.py`.
FIELDS_ADDED_SINCE = ("abandoned_slugs", "abandoned_by_spent_allowance")
ENTRY_FIELDS_ADDED_SINCE = ("abandoned",)

#: The module whose text this repository's history is asked about, and the
#: relative path of each prompt, composed from what `verifier_prompts` found
#: so nothing here names a prompt file.
THIS_MODULE = Path(__file__)

#: What a verdict says when it drops a finding. Distinctive, so a search of an
#: artifact or a queue entry is looking for *these* words.
DROP_WORDS = "MARKER-DROPPED a one-word typo nobody should plan a story for"
DECLINE_REASON = "MARKER-REASON the fix is a design decision the plan did not make"


def relative_to_repo(path: Path) -> str:
    return str(Path(path).relative_to(conftest.HARNESS_ROOT))


def dropping_entry(found: dict, *, words: str = DROP_WORDS,
                   reason: str = DECLINE_REASON) -> dict:
    """A `declined_findings` entry that drops `found`: a slug, a reason and
    the verdict's judgement that it is not worth filing."""
    return {SLUG: found["slug"], REASON: reason, NOT_WORTH_FILING: words}


def declining_entry(found: dict, *, reason: str = DECLINE_REASON) -> dict:
    """The same entry without the new field — a decline for a ground that is
    not size, which is what goes on being filed."""
    return {SLUG: found["slug"], REASON: reason}


def verdict_with(*entries: dict, base: dict = PASS) -> dict:
    return {**base, DECLINED: list(entries)}


# ==========================================================================
# The shipped verifier prompts
# ==========================================================================


#: The paragraph this story rewrites is found by the opening the sibling
#: module already reads it by, so the two cannot disagree about which
#: paragraph "the triage paragraph" is.
GROUNDS_PHRASE = "the grounds that remain"
SIZE_PHRASE = "too small"
SMALLER_PHRASE = "smaller"

#: The grounds that are not size, in the words the story names them in.
#:
#: story-219 split the first of them. A choice the plan does not make is a
#: ground only where no existing source settles it — one a source settles is
#: repaired under a recorded decision instead — and a fix the plan's own words
#: contradict or bar is a ground of its own, because it wants the plan changed
#: rather than a decision made. Both are pinned whole, so the narrowing cannot
#: be lost while the older words survive inside the newer ones.
GROUNDS = (
    "a design decision the plan did not make and no existing source settles",
    "contradicts or is barred by something the plan does say",
    "do_not_modify entry protects the file",
    "the finding is wrong on the evidence",
)


def triage_paragraph(text: str) -> str:
    return paragraph_containing(text, DEFAULT_OPENING)


def sentence_carrying(paragraph: str, phrase: str) -> str:
    """The one sentence of `paragraph` carrying `phrase`. Exactly one: a rule
    stated twice is the drift the sibling module already refuses."""
    found = [one for one in sentences(paragraph) if phrase in one]
    assert len(found) == 1, (phrase, found)
    return found[0]


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_triage_paragraph_makes_size_a_reason_to_fix(prompt: Path):
    """The criterion: a correct finding judged small is corrected or repaired
    where a route can take it. The sentence that mentions size is the
    sentence that names the two routes, so size arrives as a reason to fix."""
    paragraph = triage_paragraph(prompt.read_text(encoding="utf-8"))
    sized = sentence_carrying(paragraph, SIZE_PHRASE)
    assert "reason to fix" in sized, sized
    assert "correctable" in sized, sized
    assert "repairable" in sized, sized


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_triage_paragraph_names_the_grounds_that_are_not_size(prompt: Path):
    """The right to decline is kept, and what is left of it is named: the
    design decision no existing source settles, the fix the plan's own words
    bar, the protected file, and the finding that is wrong."""
    paragraph = triage_paragraph(prompt.read_text(encoding="utf-8"))
    grounds = sentence_carrying(paragraph, GROUNDS_PHRASE)
    assert "decline" in grounds, grounds
    for ground in GROUNDS:
        assert ground in grounds, (ground, grounds)


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_grounds_for_declining_no_longer_offer_smallness(prompt: Path):
    """The absence the story is about: the sentence naming what may still be
    declined names size as what is *not* among them, and neither phrase by
    which the old paragraph offered smallness is in it. The control below
    shows the same reading reports the clause when it is put back."""
    paragraph = triage_paragraph(prompt.read_text(encoding="utf-8"))
    grounds = sentence_carrying(paragraph, GROUNDS_PHRASE)
    assert "not size" in grounds, grounds
    assert SIZE_PHRASE not in grounds, grounds
    assert SMALLER_PHRASE not in grounds, grounds


#: The clause the old paragraph declined on, as the control plants it back.
REINSTATED_GROUND = (
    f"the grounds that remain include that a finding can be correct and still "
    f"be {SIZE_PHRASE} to fail the run"
)


def test_the_old_ground_is_reported_when_it_is_put_back():
    """Control for the absence above: the clause the old paragraph declined on
    is planted into a copy of the *whole* shipped prompt, and the same two
    readings — the paragraph extraction and the sentence extraction — are run
    again over that copy, which report it. So a clean sentence is a fact about
    the prompt rather than about an extraction that stopped finding the
    paragraph or a search that matches nothing. Mutating the extracted string
    would prove only that str.replace works."""
    text = verifier_prompts()[0].read_text(encoding="utf-8")
    grounds = sentence_carrying(triage_paragraph(text), GROUNDS_PHRASE)
    assert SIZE_PHRASE not in grounds
    # Planted in the unflowed text, on the one line the clause occupies whole,
    # so nothing straddles a line break.
    mutated = text.replace(f"{GROUNDS_PHRASE} are not size", REINSTATED_GROUND,
                           1)
    assert mutated != text
    reported = sentence_carrying(triage_paragraph(mutated), GROUNDS_PHRASE)
    assert SIZE_PHRASE in reported, reported
    assert "not size" not in reported, reported


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_triage_paragraph_names_the_field_that_drops_a_finding(
        prompt: Path):
    """A correct finding too small to be worth any route is dropped, and the
    paragraph says through which field and what the harness then does with
    it: nothing is filed, and the drop is recorded and reported."""
    paragraph = triage_paragraph(prompt.read_text(encoding="utf-8"))
    dropping = sentence_carrying(paragraph, NOT_WORTH_FILING)
    assert "correct" in dropping, dropping
    assert "drops it" in dropping, dropping
    assert "nothing is filed for it" in dropping, dropping
    assert "recorded in the accounting" in dropping, dropping


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_inspection_slot_says_a_dropped_finding_is_not_filed(prompt: Path):
    """The paragraph introducing the routed findings used to promise that
    every finding left is filed at the run's completion. It now names the one
    that is not."""
    slot = slot_paragraph(prompt.read_text(encoding="utf-8"))
    assert NOT_WORTH_FILING in slot, slot
    assert "not filed" in slot, slot
    # And it still says what happens to every other left finding, so the
    # exception is read against the rule rather than replacing it.
    assert "filed as a brief" in slot, slot


def test_the_two_rewritten_passages_are_identical_between_the_prompts():
    """The criterion that the rule reads the same in both shipped verifier
    prompts. Each passage is found by its own phrase first and asserted
    non-empty, so equality is between two found passages rather than between
    two failures to find one.

    The triage paragraph is compared with the one sentence story-214 gives one
    workflow's prompt alone set aside, on that story's terms and through its
    declaration of which sentence that is — the same narrowing the sibling
    module's own walk over this paragraph makes. This story's rule is not that
    sentence's subject: what it claims about this paragraph is that size is a
    reason to fix, which grounds remain, and which field drops a finding, and
    every sentence saying any of that is still held to reading the same in both
    prompts. The control below rewords one of them and must be reported.
    """
    texts = {prompt.name: prompt.read_text(encoding="utf-8")
             for prompt in verifier_prompts()}
    assert len(texts) > 1, texts
    triage = {name: without_the_permitted_divergence(triage_paragraph(text))
              for name, text in texts.items()}
    slots = {name: slot_paragraph(text) for name, text in texts.items()}
    for found in (triage, slots):
        for name, passage in found.items():
            assert NOT_WORTH_FILING in passage, (name, passage)
        assert len(set(found.values())) == 1, found


def test_the_parity_reading_tells_two_triage_paragraphs_apart():
    """Control for the equality above: the same extraction over a copy whose
    triage paragraph has one word changed finds two different passages, and
    the word changed is in a sentence the narrowing above does not set aside —
    so what the comparison still reports is a sentence the two prompts share
    and not the one story-214 permits them to differ in."""
    text = verifier_prompts()[0].read_text(encoding="utf-8")
    original = without_the_permitted_divergence(triage_paragraph(text))
    # Mutated in the *unflowed* text, where the paragraph is line-wrapped, so
    # the word replaced is one that does not straddle a line break.
    altered = text.replace("therefore", "accordingly", 1)
    assert altered != text
    assert without_the_permitted_divergence(triage_paragraph(altered)) != original


@pytest.mark.parametrize("prompt", verifier_prompts(), ids=lambda p: p.name)
def test_the_repairable_passage_is_what_it_was_before_this_story(prompt: Path):
    """The story changes which findings reach a route and not what the routes
    are, so the paragraph describing a repairable finding is compared with
    itself across this module's own story range and must not have moved.

    Both ends are bounded at that range: the later end is this story's ENDPOINT
    rather than the working tree, because a story after this one may legitimately
    add to that paragraph — story-208 makes the do_not_modify bound conditional
    on a judgement and says so there — and reading the working tree as the after
    side would make this assertion about whoever edits the prompt next rather
    than about the story it belongs to. That is the trap the architecture
    document records under its HEAD-baseline bullets."""
    before = repository_file_at(relative_to_repo(prompt),
                                validation_file=THIS_MODULE, bound=BASELINE)
    after = repository_file_at(relative_to_repo(prompt),
                               validation_file=THIS_MODULE, bound=ENDPOINT)
    assert paragraph_containing(before, REPAIRABLE_PHRASE) == \
        paragraph_containing(after, REPAIRABLE_PHRASE)


def test_the_unchanged_comparison_reports_a_passage_that_moved():
    """Control for the comparison above: the same baseline text with one word
    of that paragraph changed no longer matches the one at this story's
    endpoint, so green there says the paragraph is untouched rather than that
    the comparison cannot go red."""
    prompt = verifier_prompts()[0]
    before = repository_file_at(relative_to_repo(prompt),
                                validation_file=THIS_MODULE, bound=BASELINE)
    after = repository_file_at(relative_to_repo(prompt),
                               validation_file=THIS_MODULE, bound=ENDPOINT)
    moved = before.replace("not a licence to revisit", "not a permit to revisit",
                           1)
    assert moved != before
    assert paragraph_containing(moved, REPAIRABLE_PHRASE) != \
        paragraph_containing(after, REPAIRABLE_PHRASE)


# ==========================================================================
# The verdict schema
# ==========================================================================


DECLINED_ITEM = VERDICT_SCHEMA["properties"][DECLINED]["items"]


def test_the_schema_declares_the_new_field_optional_beside_slug_and_reason():
    """The criterion: an optional string, and the required set untouched."""
    assert set(DECLINED_ITEM["required"]) == {SLUG, REASON}
    assert NOT_WORTH_FILING in DECLINED_ITEM["properties"]
    assert DECLINED_ITEM["properties"][NOT_WORTH_FILING]["type"] == "string"


def test_the_new_fields_description_says_what_it_signals_and_what_it_is_not():
    """Presence-as-signal on its siblings' terms, the drop it causes, and the
    two bounds the story puts on it: nothing routes on it, and the drop is
    never read out of the prose of a reason."""
    description = DECLINED_ITEM["properties"][NOT_WORTH_FILING][
        "description"].lower()
    assert "optional" in description
    assert "presence-as-signal" in description
    assert "nothing routes on it" in description
    assert "nothing is filed" in description


def test_a_verdict_carrying_the_new_field_validates():
    assert schema_validator.validate(
        verdict_with(dropping_entry(OWN_FINDING)), VERDICT_SCHEMA) == []
    # And beside a sibling entry that carries only a reason.
    assert schema_validator.validate(
        verdict_with(dropping_entry(OWN_FINDING),
                     declining_entry(BOTH_FINDING)), VERDICT_SCHEMA) == []


@pytest.mark.parametrize("wrong", [7, ["words"], None, {"text": "words"}])
def test_an_entry_whose_new_field_is_not_a_string_is_refused(wrong):
    """Control for the acceptance above: the field is typed, so the accepting
    case says the shape was checked rather than that nothing was."""
    entry = {**dropping_entry(OWN_FINDING), NOT_WORTH_FILING: wrong}
    errors = schema_validator.validate(verdict_with(entry), VERDICT_SCHEMA)
    assert errors, entry


def test_an_entry_carrying_only_the_new_field_is_still_refused():
    """The required set is unchanged, so a drop is a decline that also says
    it is not worth filing rather than a shape of its own."""
    assert schema_validator.validate(
        verdict_with({SLUG: OWN_FINDING["slug"], NOT_WORTH_FILING: DROP_WORDS}),
        VERDICT_SCHEMA)


REASON_DESCRIPTION = DECLINED_ITEM["properties"][REASON]["description"]

#: The clause the reason's description used to open its list of grounds with.
OLD_GROUND = "too small to be worth a pass"


def test_the_reason_description_no_longer_offers_size_as_a_ground():
    """The criterion: size is gone from the grounds and named as what is not
    one, and what remains is the grounds that are not about size. The control
    below shows the same search reports the clause when it is restored."""
    assert OLD_GROUND not in REASON_DESCRIPTION
    assert "Size is not" in REASON_DESCRIPTION
    for ground in GROUNDS:
        assert ground in REASON_DESCRIPTION, ground


def test_the_restored_ground_is_reported_by_the_same_search():
    """Control for the absence above."""
    restored = REASON_DESCRIPTION.replace(
        "Size is not among the reasons",
        f"Why the finding was left: {OLD_GROUND}", 1)
    assert restored != REASON_DESCRIPTION
    assert OLD_GROUND in restored


# ==========================================================================
# The drop reason
# ==========================================================================


def test_the_drop_reason_is_a_reason_of_its_own():
    """One constant beside the severity floor's, and not equal to any reason
    the filing already names — a reader is owed which bound decided."""
    reason = inspection.NOT_WORTH_FILING
    assert isinstance(reason, str) and reason
    others = [inspection.BENEATH_THE_FLOOR, inspection.PAST_THE_CAP,
              inspection.LOST_BY_THE_QUEUE]
    assert reason not in others, reason


# ==========================================================================
# The accounting, driven as functions
# ==========================================================================


def test_the_new_field_is_read_by_slug():
    texts = story_inspection.not_worth_filing_texts(verdict_with(
        dropping_entry(OWN_FINDING),
        dropping_entry(BOTH_FINDING, words="MARKER-SECOND-DROP"),
    ))
    assert texts == {OWN_FINDING["slug"]: DROP_WORDS,
                     BOTH_FINDING["slug"]: "MARKER-SECOND-DROP"}


def test_a_verdict_carrying_none_of_the_field_drops_nothing():
    """The compatibility case, and the demonstration that the mapping can be
    empty for a reason other than a reader that never matches: the reading
    above finds two, and each of these finds none."""
    assert story_inspection.not_worth_filing_texts(PASS) == {}
    assert story_inspection.not_worth_filing_texts(verdict_with()) == {}
    assert story_inspection.not_worth_filing_texts(
        verdict_with(declining_entry(OWN_FINDING))) == {}
    assert story_inspection.not_worth_filing_texts({**PASS, DECLINED: None}) == {}
    assert story_inspection.not_worth_filing_texts("not a verdict") == {}
    assert story_inspection.not_worth_filing_texts(None) == {}


def test_the_new_field_is_read_defensively_and_keeps_the_first_entry():
    """An entry the schema would have refused contributes nothing, a blank
    text is no judgement at all, and the first entry naming a slug wins —
    the terms the reason beside it is already read on."""
    texts = story_inspection.not_worth_filing_texts(verdict_with(
        "not an entry",
        {SLUG: "", REASON: "r", NOT_WORTH_FILING: "no slug"},
        {SLUG: OWN_FINDING["slug"], REASON: "r", NOT_WORTH_FILING: "   "},
        {SLUG: OWN_FINDING["slug"], REASON: "r", NOT_WORTH_FILING: 7},
        {SLUG: OWN_FINDING["slug"], REASON: "r", NOT_WORTH_FILING: "first"},
        {SLUG: OWN_FINDING["slug"], REASON: "r", NOT_WORTH_FILING: "second"},
    ))
    assert texts == {OWN_FINDING["slug"]: "first"}


def test_each_declined_entry_says_whether_it_was_dropped_and_in_whose_words():
    """The accounting records the drop per finding beside the reason it
    already records, and a left finding no drop names is recorded as one the
    run files rather than as one it says nothing about."""
    declined = story_inspection.declined_accounting(
        [OWN_FINDING, BOTH_FINDING],
        {OWN_FINDING["slug"]: DECLINE_REASON},
        {OWN_FINDING["slug"]: DROP_WORDS})
    by_slug = {one[SLUG]: one for one in declined}
    assert by_slug[OWN_FINDING["slug"]][DROPPED] is True
    assert by_slug[OWN_FINDING["slug"]][NOT_WORTH_FILING] == DROP_WORDS
    assert by_slug[OWN_FINDING["slug"]][REASON] == DECLINE_REASON
    assert by_slug[BOTH_FINDING["slug"]][DROPPED] is False
    assert NOT_WORTH_FILING not in by_slug[BOTH_FINDING["slug"]]


def test_a_caller_naming_no_drops_records_every_left_finding_as_filed():
    """What every caller was before this story, and what a verdict carrying
    the field nowhere means."""
    declined = story_inspection.declined_accounting(
        [OWN_FINDING, BOTH_FINDING], {})
    assert [one[DROPPED] for one in declined] == [False, False]
    assert all(NOT_WORTH_FILING not in one for one in declined)


def test_the_artifact_names_the_dropped_slugs_and_reads_them_back(tmp_path):
    """The slugs are recorded beside the declined entries and read back
    through the accessor the acted-on slugs are read through, which is what
    the filing at completion consults."""
    run_dir = tmp_path / "run"
    declined = story_inspection.declined_accounting(
        [OWN_FINDING, BOTH_FINDING], {OWN_FINDING["slug"]: DECLINE_REASON},
        {OWN_FINDING["slug"]: DROP_WORDS})
    story_inspection.write_accounting(
        run_dir, FINDINGS_ARTIFACT, 1, STORY_ID,
        routed=[OWN_FINDING, BOTH_FINDING], acted_on=[],
        remainder=[OWN_FINDING, BOTH_FINDING], declined=declined)
    recorded = json.loads((run_dir / story_inspection.accounting_artifact_file(
        FINDINGS_ARTIFACT, 1)).read_text(encoding="utf-8"))
    assert recorded[DROPPED_SLUGS] == [OWN_FINDING["slug"]]
    assert story_inspection.dropped_slugs(run_dir, FINDINGS_ARTIFACT, 1) == \
        (OWN_FINDING["slug"],)


def test_an_attempt_that_dropped_nothing_names_no_slug(tmp_path):
    """The negative control for the accessor above: the same writer over a
    verdict that dropped nothing records an empty list and the accessor
    answers empty, and so does an attempt that was never accounted — so the
    reading above is about what was recorded and not about an accessor that
    answers nothing whatever it is asked."""
    run_dir = tmp_path / "run"
    story_inspection.write_accounting(
        run_dir, FINDINGS_ARTIFACT, 1, STORY_ID,
        routed=[OWN_FINDING], acted_on=[], remainder=[OWN_FINDING])
    recorded = json.loads((run_dir / story_inspection.accounting_artifact_file(
        FINDINGS_ARTIFACT, 1)).read_text(encoding="utf-8"))
    assert recorded[DROPPED_SLUGS] == []
    assert story_inspection.dropped_slugs(run_dir, FINDINGS_ARTIFACT, 1) == ()
    assert story_inspection.dropped_slugs(run_dir, FINDINGS_ARTIFACT, 2) == ()


# ==========================================================================
# The accounting and the filing, driven as coordinator runs
# ==========================================================================


def the_accounting_line(run) -> str:
    events = accounting_events(run)
    assert len(events) == 1, events
    return events[0]["message"]


def declined_by_slug(recorded: dict) -> dict:
    return {one[SLUG]: one for one in recorded["declined"]}


def queue_text(run) -> list[str]:
    return [json.dumps(entry) for entry in run.queue]


def the_clause_the_completion_line_opens_with(recorded: dict) -> str:
    """What a run that dropped something must open its completion line with.

    The counts are what the *verdict* left, so they are read off the
    accounting artifact, which records the remainder before anything is
    dropped from it — the same number the accounting event of the same
    attempt reports, so the two lines of one run agree about one quantity.
    Nothing is held in these runs: every finding they route names a file the
    story changed, so the inspection holds none of them back, which is the
    same reading the sibling module's held finding makes on the other side.

    Asserting this is what stops the two counts being taken *after* the drop
    filter: taken there they would report what survived the drop rather than
    what the verdict left, and the run whose whole remainder was dropped —
    the run this story made write a line rather than return silently — would
    open by saying nothing was left and go on to say one was dropped.
    """
    return f"0 held and {recorded['remainder']} left by the verdict"


def test_a_dropped_finding_is_filed_nowhere_and_recorded_as_dropped(
        driven, filings):
    """The criterion end to end. Two findings are routed; the verdict passes
    and declines one of them as not worth filing. Nothing is filed for that
    one — no queue entry, no filing call — while the other is filed exactly
    as it always was, which is what says the absence is the drop's doing and
    not a run that filed nothing. The accounting records the drop beside the
    words the verdict gave, and the line says a drop happened.
    """
    run = driven(findings=[OWN_FINDING, BOTH_FINDING],
                 verdicts=[verdict_with(dropping_entry(OWN_FINDING))])

    assert run.code == 0
    assert run.state["status"] == "completed"

    recorded = accounting(run)
    assert sorted(recorded["remainder_slugs"]) == sorted(
        [OWN_FINDING["slug"], BOTH_FINDING["slug"]])
    assert recorded[DROPPED_SLUGS] == [OWN_FINDING["slug"]]
    by_slug = declined_by_slug(recorded)
    assert by_slug[OWN_FINDING["slug"]][DROPPED] is True
    assert by_slug[OWN_FINDING["slug"]][NOT_WORTH_FILING] == DROP_WORDS
    assert by_slug[BOTH_FINDING["slug"]][DROPPED] is False

    line = the_accounting_line(run)
    assert "not worth filing" in line, line
    assert "1 of those" in line, line

    # Filed nowhere: no call carried it and no entry holds its words, while
    # the finding beside it was filed by the same run.
    assert filings_of(filings, OWN_FINDING) == 0
    assert filings_of(filings, BOTH_FINDING) == 1
    assert routed_markers(run) == [BOTH_FINDING["body"]]
    assert not any(OWN_FINDING["body"] in entry for entry in queue_text(run))
    # The verdict's words are the accounting's and reach no brief.
    assert not any(DROP_WORDS in entry for entry in queue_text(run))

    (filed,) = filed_events(run)
    assert filed["filed"] == 1
    # Counted once. A drop counted beside a filing it was also part of, or
    # gathered twice from the same verdict, would say two here while one
    # finding went unfiled, and the line would be an account of nothing.
    assert filed[DROPPED] == 1
    assert f"{inspection.NOT_WORTH_FILING}: 1" in filed["message"], filed
    # And the line opens on what the verdict left — two — rather than on the
    # one that survived the drop, so its opening and the drop clause beneath
    # it account for each other instead of giving two numbers for one thing.
    assert the_clause_the_completion_line_opens_with(recorded) in (
        filed["message"]), filed


def test_a_finding_declined_for_a_ground_that_is_not_size_is_filed_as_before(
        driven, filings):
    """The distinction rather than a suppression: the same run whose verdict
    declines the same finding with a reason and no judgement about size files
    it exactly as it is filed today, and nothing says anything was dropped."""
    run = driven(findings=[OWN_FINDING, BOTH_FINDING],
                 verdicts=[verdict_with(declining_entry(OWN_FINDING))])

    assert run.code == 0
    recorded = accounting(run)
    assert recorded[DROPPED_SLUGS] == []
    by_slug = declined_by_slug(recorded)
    assert by_slug[OWN_FINDING["slug"]][DROPPED] is False
    assert by_slug[OWN_FINDING["slug"]][REASON] == DECLINE_REASON

    assert "not worth filing" not in the_accounting_line(run)
    assert filings_of(filings, OWN_FINDING) == 1
    assert filings_of(filings, BOTH_FINDING) == 1
    assert routed_markers(run) == sorted(
        [OWN_FINDING["body"], BOTH_FINDING["body"]])
    (filed,) = filed_events(run)
    assert filed["filed"] == 2
    assert inspection.NOT_WORTH_FILING not in filed["message"], filed


def test_a_run_whose_whole_remainder_was_dropped_still_writes_its_line(
        driven, filings):
    """The criterion about the event: a run with nothing left to file and
    something to report writes the completion event anyway, naming the drop
    reason and counting it, rather than returning silently and leaving a
    reader with a routed finding that went nowhere and no line about it.

    It is also where the line's opening clause matters most, and the
    assertion on it is what stops the two counts being taken after the drop
    filter rather than before it: this is the run where the filtered lists
    are empty, so a line composed from them would open by reporting that the
    verdict left nothing and close by reporting that one of the nothing was
    dropped."""
    run = driven(findings=[OWN_FINDING],
                 verdicts=[verdict_with(dropping_entry(OWN_FINDING))])

    assert run.code == 0
    assert run.state["status"] == "completed"
    assert filings_of(filings, OWN_FINDING) == 0
    assert routed_markers(run) == []
    assert not any(DROP_WORDS in entry for entry in queue_text(run))

    recorded = accounting(run)
    assert recorded["remainder"] == 1
    (filed,) = filed_events(run)
    assert filed["filed"] == 0
    assert filed[DROPPED] == 1
    assert f"{inspection.NOT_WORTH_FILING}: 1" in filed["message"], filed
    assert the_clause_the_completion_line_opens_with(recorded) in (
        filed["message"]), filed


def test_the_same_run_declining_without_the_field_files_its_one_finding(
        driven, filings):
    """The control for the run above: one routed finding, one passing verdict
    declining it for a ground that is not size, and the same readings find a
    filing call, a queue entry and a completion event counting one filed. So
    the dropped run's empty queue is the drop's doing."""
    run = driven(findings=[OWN_FINDING],
                 verdicts=[verdict_with(declining_entry(OWN_FINDING))])

    assert run.code == 0
    assert filings_of(filings, OWN_FINDING) == 1
    assert routed_markers(run) == [OWN_FINDING["body"]]
    (filed,) = filed_events(run)
    assert filed["filed"] == 1
    assert filed[DROPPED] == 0


# ==========================================================================
# What a verdict that drops nothing writes
# ==========================================================================


def keys_written_at_baseline(name: str) -> set:
    """The keys the named writer's dict literals carried before this story,
    read out of its own source at this module's story baseline rather than
    listed here — a list would be a second copy of the answer and would go
    stale the moment a field is added for any other reason."""
    source = function_source_at("orchestration/story_inspection.py", name,
                                validation_file=THIS_MODULE, bound=BASELINE)
    return {key.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Dict)
            for key in node.keys if isinstance(key, ast.Constant)}


def test_a_verdict_dropping_nothing_writes_the_artifact_it_wrote_before(
        driven, filings):
    """The compatibility criterion. The verdict the sibling module has always
    driven — carrying no declined findings at all — leaves every field the
    accounting artifact carried before this story, with the values it had,
    and everything this story added is about dropping and says nothing
    happened. The line carries no drop clause and the filing is what it was.

    What a later story added to the same two writers is named in
    `FIELDS_ADDED_SINCE` and `ENTRY_FIELDS_ADDED_SINCE` and excluded here, so
    both comparisons stay equalities rather than becoming containment checks:
    the claim is about what *this* story added, and a field somebody else added
    for another reason is neither evidence for it nor against it.
    """
    run = driven(findings=[OWN_FINDING, BOTH_FINDING])

    assert run.code == 0
    recorded = accounting(run)
    before = keys_written_at_baseline("write_accounting")
    assert before <= set(recorded), before - set(recorded)
    # Held shut from the other side: every field declared as added since is one
    # the writer still writes, so a name left there after its field went away
    # fails here rather than quietly excusing nothing.
    assert set(FIELDS_ADDED_SINCE) <= set(recorded)
    added = set(recorded) - before - set(FIELDS_ADDED_SINCE)
    assert added, "this story added a field to the accounting artifact"
    for name in added:
        assert "drop" in name, name
        assert not recorded[name], (name, recorded[name])

    assert recorded["remainder"] == 2
    assert recorded["declined_with_reason"] == 0
    assert recorded["declined_without_reason"] == 2
    assert recorded["filed"] == 0

    entry_before = keys_written_at_baseline("declined_accounting")
    for entry in recorded["declined"]:
        assert set(ENTRY_FIELDS_ADDED_SINCE) <= set(entry), entry
        assert set(entry) - entry_before - set(ENTRY_FIELDS_ADDED_SINCE) \
            == {DROPPED}, entry
        assert entry[DROPPED] is False

    assert "not worth filing" not in the_accounting_line(run)
    assert filings_of(filings, OWN_FINDING) == 1
    assert filings_of(filings, BOTH_FINDING) == 1
    (filed,) = filed_events(run)
    assert (filed["filed"], filed[DROPPED]) == (2, 0)
    assert inspection.NOT_WORTH_FILING not in filed["message"]


# ==========================================================================
# A resumed run
# ==========================================================================


@pytest.fixture
def ceiling_harness(tmp_path) -> Path:
    """The same workflow the sibling modules build, materialized under the
    retry ceiling the dedupe module owns — three attempts need two retries,
    and a ceiling read off the shipped rules would make this a fact about the
    deployment rather than about the filing."""
    return materialize(WORKFLOW, tmp_path / "dropped-once-harness",
                       rules=filed_once.RULES)


def test_a_finding_an_entry_dropped_is_not_recovered_from_that_entry(
        ceiling_harness, tmp_path, filings):
    """The criterion about a resumed run.

    A run escalates on its third attempt, whose verdict declines the finding
    routed to it as not worth filing. The developer changes the tree and the
    run is resumed and completes. What the resume recovers from the closed
    entry is that entry's held finding — which is the control: the entry does
    contribute to the filing, so the dropped finding's absence is not an
    entry that contributed nothing. The finding its own verdict dropped
    reaches neither the queue nor a filing call, because a drop is a drop
    wherever in the run it was decided.
    """
    target = build_target(tmp_path / "dropped-once-target")
    third, beside_third = filed_once.THIRD, filed_once.BESIDE_THIRD
    verdicts = list(filed_once.TO_THE_CEILING)
    verdicts[-1] = verdict_with(dropping_entry(third), base=verdicts[-1])

    escalated = filed_once.drive(
        target, ceiling_harness, tmp_path,
        per_attempt=[[filed_once.FIRST, filed_once.BESIDE_FIRST],
                     [filed_once.SECOND, filed_once.BESIDE_SECOND],
                     [third, beside_third]],
        verdicts=verdicts, name="escalated-drop")

    assert escalated.state["status"] == "escalated"
    # Found, routed and left by that verdict before it went unfiled.
    last = accounting(escalated, len(verdicts))
    assert last["remainder_slugs"] == [third["slug"]]
    assert last[DROPPED_SLUGS] == [third["slug"]]
    assert escalated.queue == []

    fourth = filed_once.drifting(4, [CHANGED_FILE],
                                 "MARKER-FOURTH the change is wrong")
    beside_fourth = filed_once.beside(
        4, "MARKER-BESIDE-FOURTH the neighbour is wrong")
    filed_once.ready_to_resume(escalated, "the developer's change")
    resumed = filed_once.drive(target, ceiling_harness, tmp_path,
                               per_attempt=[[fourth, beside_fourth]],
                               verdicts=[PASS], name="resumed-drop")

    assert resumed.code == 0
    assert resumed.state["status"] == "completed"
    entries = queue_text(resumed)
    assert any(fourth["body"] in one for one in entries)
    # The closed entry did contribute: what it held is here.
    assert any(beside_third["body"] in one for one in entries)
    # And what its verdict dropped is not, by either reading.
    assert not any(third["body"] in one for one in entries)
    assert not any(DROP_WORDS in one for one in entries)
    assert filings_of(filings, third) == 0

    # The line says the same thing the queue does. A finding filed from the
    # closed entry and *also* named as dropped would be counted on both sides
    # of this event, so the count of what was filed is asserted against the
    # queue the filing left rather than against a number of its own.
    (filed,) = filed_events(resumed)
    assert filed["filed"] == len(entries)
    assert filed[DROPPED] == 1
    assert f"{inspection.NOT_WORTH_FILING}: 1" in filed["message"], filed
