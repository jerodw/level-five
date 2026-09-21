"""A brief's file:line evidence follows its opening rather than sitting in it.

The documents a brief writer reads told them two things about where a brief's
evidence goes. The shared writing rule and the brief schema's body description
said the opening is plain English — what is wrong, what the work does about
it, what it leaves alone — and that the paths and the line-level names come in
the paragraphs after. The Inspector's own instruction, the schema's title
description and the filing skill's title section each said, in one clause,
that the file:line evidence goes in the opening. A writer obeying either
disobeyed the other in every brief.

Those three clauses now say what the rule says: the opening says what is
wrong in plain terms, and the evidence follows in the paragraphs after it.
What this module holds is the documents a brief writer reads against that:

  * no sentence of any of them that names the file:line evidence beside the
    opening places the evidence anywhere but after it, and each of the three
    repaired documents still carries such a sentence, so the absence is not
    read off a document that has stopped saying where the evidence goes;
  * the rendered Inspector prompt — the template with the prose layer and the
    schema resolved into it, which is what the Inspector actually reads — says
    the same, so the two texts it obeys no longer contradict each other;
  * no document has regained any of the three superseded clauses;
  * the texts the repaired sentences were brought to — the prose layer, the
    schema's body description, the story schema's description, the skill's
    body section, the assist prompt and the outbox module — are what they were
    before this story, and nothing under `orchestration/` changed.

The documents are read live, through the same paths
`tests/test_a_brief_is_titled_for_its_end_state.py` reads them, because they
are the subject: the claim is about what this repository ships to the agents
that write briefs. The rendered prompt is built the way
`tests/test_every_story_and_brief_opens_in_plain_english.py` builds it.

Every absence asserted here is shown to be detectable. The superseded clauses
are carried as literal constants — a fact about documents a reader of this
file cannot see, written out rather than resolved out of the commit graph,
where a rebase or a squash would move them — and the placement scan is run
over each superseded sentence and shown reporting it. It is also run over a
copy of each shipped sentence with its "after the opening" clause cut out,
which shows that the sentence the scan passes is the one it read and that the
clause is what it decided on. The unchanged-path assertions are read against
a diff of a path this story did change.

What the scan does not cover is stated rather than left to be discovered. It
decides on the sentences that name both the evidence and the opening; a
sentence placing the evidence in the opening without naming the opening —
"the evidence goes in the first paragraph" — is outside it. The vocabulary
of after-the-opening phrasings is written out below, so a document that says
the evidence follows the opening in words nobody thought of is reported as
placing it wrongly, which is a false alarm and never a false silence.

Nothing here invokes a model and nothing here runs the suite.
"""
from pathlib import Path
import re

import pytest

from conftest import story_diff

import test_a_brief_is_titled_for_its_end_state as titled
import test_every_story_and_brief_opens_in_plain_english as plain

collapsed = titled.collapsed

#: The documents a brief writer reads, keyed as the titled module keys them:
#: the schema's title description, the Inspector prompt, the assist prompt and
#: the skill. Reused rather than listed again, so a document added there is
#: held here without a second list to keep in step.
SHIPPED = titled.SHIPPED

#: The three documents this story repaired, each with the section a reader of
#: it finds the rule in — the schema's whole title description, and the title
#: section of each prose document, resolved by its own heading.
REPAIRED = {
    name: text for name, (text, _) in titled.WORKED_PAIRS.items()
}

# --------------------------------------------------------------------------
# What these documents said before this story
#
# Literal values rather than a text recovered from this repository's history,
# for the reason the titled module gives: what is asserted is that documents a
# reader of this file cannot see said these things, and an answer resolved out
# of the commit graph would move under a rebase, a squash or a rename.
# --------------------------------------------------------------------------

#: The Inspector's sentence, hard-wrapped as the file carried it, so the scan
#: is shown finding the clause across the line breaks it was written over.
SUPERSEDED_INSPECTOR_SENTENCE = (
    "What is wrong today still has to be said, and it is said in the body's\n"
    "opening line: that is the case for the work, and it is where your\n"
    "file:line evidence already goes. `category` and `severity` are what "
    "carry\n"
    "the fact that something is broken now, being fields a board can sort "
    "and\n"
    "filter on rather than a tone you put in a sentence."
)

#: The sentence of the schema's title description that placed the evidence
#: where the body opens.
SUPERSEDED_SCHEMA_SENTENCE = (
    "What is wrong today is not lost by this: a defect brief's body opens by "
    "saying it, which is where the case rests and where the file:line "
    "evidence already lives, and category and severity are what carry the "
    "fact that something is broken now, being fields a board can sort and "
    "filter on rather than a tone in a sentence."
)

#: The skill's sentence, hard-wrapped as the file carried it.
SUPERSEDED_SKILL_SENTENCE = (
    "And where the brief\n"
    "reports a defect, open the body by saying what is wrong today: that is "
    "the case\n"
    "for the work, the file:line evidence is already there, and `category` "
    "and\n"
    "`severity` are the fields a board sorts on to know something is broken."
)

SUPERSEDED = {
    "the Inspector's sentence": SUPERSEDED_INSPECTOR_SENTENCE,
    "the schema's title sentence": SUPERSEDED_SCHEMA_SENTENCE,
    "the skill's title sentence": SUPERSEDED_SKILL_SENTENCE,
}

#: The three clauses this story replaced, each as a reader met it. A document
#: that regains any of them has put the evidence back in the opening,
#: whatever else it also says.
SUPERSEDED_CLAUSES = (
    "where your file:line evidence already goes",
    "where the file:line evidence already lives",
    "the file:line evidence is already there",
)

#: The ways the shipped documents say the evidence comes after the opening. A
#: sentence naming the evidence and the opening that carries none of these is
#: reported as placing the evidence in the opening, so a phrasing missing from
#: this tuple produces a false alarm and never a false silence.
AFTER_THE_OPENING = (
    "paragraphs after",
    "paragraphs that follow",
    "paragraphs following",
    "following in the paragraphs",
    "follows in the paragraphs",
    "after the opening",
    "after that opening",
)

#: The paths this story was to leave alone, relative to the repository root.
#: `orchestration/` is the whole directory: no check scores where a brief's
#: evidence sits, and none was to be added.
LEFT_ALONE = (
    f"prompts/{plain.PROSE_LAYER}",
    f"prompts/{plain.ASSIST_TEMPLATE}",
    "schemas/story.schema.json",
    "orchestration/",
)

#: A path this story did change, which the unchanged-path assertions are read
#: against: a diff that reports nothing here would be reporting nothing about
#: anything.
CHANGED = f"prompts/{plain.INSPECTOR_TEMPLATE}"


# --------------------------------------------------------------------------
# The scan
# --------------------------------------------------------------------------

OPENING = re.compile(r"\bopen(?:s|ing)?\b")

#: A line that opens a block of its own whatever came before it: a markdown
#: heading, or a bulleted or numbered list item. Neither has to end in a full
#: stop, so a splitter that only looks for one glues it to whatever follows
#: and judges the two as one sentence.
HEADING = re.compile(r"^\s*#+\s+")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def blocks_of(text: str) -> list[str]:
    """`text` as the blocks a reader sees, each with its marker removed:
    paragraphs separated by a blank line, a list item opening a block of its
    own, and a heading a block of its own that its next line does not join."""
    blocks: list[list[str]] = []
    open_block = False
    for line in text.splitlines():
        if not line.strip():
            open_block = False
        elif HEADING.match(line):
            blocks.append([HEADING.sub("", line)])
            open_block = False
        elif LIST_ITEM.match(line) or not open_block:
            blocks.append([LIST_ITEM.sub("", line)])
            open_block = True
        else:
            blocks[-1].append(line)
    return ["\n".join(lines) for lines in blocks]


def sentences_of(text: str) -> list[str]:
    """`text` as sentences, block by block, with the emphasis marks markdown
    wraps a phrase in removed first, so a bold sentence ending in `.**` still
    ends a sentence.

    Split into blocks before sentences so a heading or a bullet with no
    terminal full stop ends where its line ends rather than running into the
    block after it, where a clause placing the evidence in the opening would
    otherwise be hidden behind a neighbour that placed it correctly.
    """
    return [sentence
            for block in blocks_of(text)
            for sentence in re.split(r"(?<=[.!?]) ",
                                     collapsed(block.replace("*", "")))]


def naming_the_evidence_and_the_opening(text: str) -> list[str]:
    """Every sentence that names the file:line evidence beside the opening.

    These are the sentences that say where the evidence goes relative to the
    opening, which is the only thing this module decides. A sentence naming
    file:line alone — that every finding cites it, that it belongs in the body
    rather than in the paths — says nothing about the opening and is left out.
    """
    return [sentence for sentence in sentences_of(text)
            if "file:line" in sentence.lower()
            and OPENING.search(sentence.lower())]


def placing_the_evidence_in_the_opening(text: str) -> list[str]:
    """Every sentence naming the evidence and the opening that does not put
    the evidence after it."""
    return [sentence for sentence in naming_the_evidence_and_the_opening(text)
            if not any(phrase in sentence.lower()
                       for phrase in AFTER_THE_OPENING)]


def superseded_clauses_in(text: str) -> list[str]:
    said = collapsed(text).lower()
    return sorted(clause for clause in SUPERSEDED_CLAUSES if clause in said)


def without_the_after_clause(sentence: str) -> str:
    """`sentence` with every after-the-opening phrasing cut out: what the same
    sentence would say if it still placed the evidence in the opening."""
    stripped = sentence
    while any(phrase in stripped.lower() for phrase in AFTER_THE_OPENING):
        for phrase in AFTER_THE_OPENING:
            stripped = re.sub(re.escape(phrase), "", stripped,
                              flags=re.IGNORECASE)
    assert stripped != sentence, sentence
    return collapsed(stripped)


DOCUMENTS = pytest.mark.parametrize(
    "document", sorted(SHIPPED), ids=sorted(SHIPPED))
REPAIRED_DOCUMENTS = pytest.mark.parametrize(
    "document", sorted(REPAIRED), ids=sorted(REPAIRED))
SUPERSEDED_TEXTS = pytest.mark.parametrize(
    "superseded", sorted(SUPERSEDED), ids=sorted(SUPERSEDED))


# --------------------------------------------------------------------------
# Where each document places the evidence
# --------------------------------------------------------------------------


@DOCUMENTS
def test_no_document_places_the_evidence_in_the_opening(document):
    """The absence. Its controls are the two tests below: the same scan over
    each superseded sentence, and over each shipped sentence with its after
    clause cut out."""
    assert placing_the_evidence_in_the_opening(SHIPPED[document]) == [], \
        document


@REPAIRED_DOCUMENTS
def test_each_repaired_document_still_says_where_the_evidence_goes(document):
    """The positive half: each of the three carries a sentence naming the
    evidence beside the opening, so the silence above is about what that
    sentence says and not about a document that no longer says anything."""
    assert naming_the_evidence_and_the_opening(REPAIRED[document]), document


@SUPERSEDED_TEXTS
def test_the_scan_reports_each_sentence_this_story_replaced(superseded):
    """Run over what the documents said before, the same scan reports the one
    sentence each of them placed the evidence in the opening with."""
    reported = placing_the_evidence_in_the_opening(SUPERSEDED[superseded])
    assert len(reported) == 1, reported
    assert "file:line" in reported[0]


#: The rule restated as a list or under a heading, as a later edit to one of
#: these documents might well write it: a first block that places the names
#: after the opening, with no full stop to end it, and a second that puts the
#: evidence in the opening itself. The second is what the scan must report,
#: and it is only reported if the first block's after-clause does not reach it.
BLOCKED_ARRANGEMENTS = {
    "bulleted": (
        "- keep the paths and field names for the paragraphs after the "
        "opening\n"
        "- put the file:line evidence in the opening itself\n"
    ),
    "headed": (
        "### Keep the paths for the paragraphs after the opening\n"
        "Put the file:line evidence in the opening itself\n"
    ),
}


@pytest.mark.parametrize("arrangement", sorted(BLOCKED_ARRANGEMENTS),
                         ids=sorted(BLOCKED_ARRANGEMENTS))
def test_the_scan_reports_a_block_placing_the_evidence_in_the_opening(
        arrangement):
    """Control for the splitter: a bullet or a heading with no terminal full
    stop ends where its block ends, so the after-clause it carries does not
    excuse the block beneath it. Read as one sentence, the pair below would
    pass; read as two, the second is reported."""
    text = BLOCKED_ARRANGEMENTS[arrangement]
    reported = [sentence.lower()
                for sentence in placing_the_evidence_in_the_opening(text)]
    assert reported == ["put the file:line evidence in the opening itself"]
    # Read as one line, the same pair carries an after-clause and is passed.
    assert placing_the_evidence_in_the_opening(collapsed(text)) == []


@DOCUMENTS
def test_the_scan_reports_each_shipped_sentence_with_its_after_clause_cut(
        document):
    """The sentences the scan passes are the ones it read: each, with the
    clause that places the evidence after the opening taken out, is reported.
    So a pass above is decided on that clause and not on a sentence the scan
    never saw."""
    sentences = naming_the_evidence_and_the_opening(SHIPPED[document])
    assert sentences, document
    for sentence in sentences:
        cut = without_the_after_clause(sentence)
        assert placing_the_evidence_in_the_opening(cut) == [cut], sentence


@REPAIRED_DOCUMENTS
def test_each_repaired_document_still_says_the_opening_says_what_is_wrong(
        document):
    """What the repair had to keep: the opening is where what is wrong today
    is said, and category and severity carry the fact that something is
    broken. Asked through the titled module's own scan, which is what holds
    that rule, so the two modules agree on what the rule is."""
    assert titled.missing_where_the_fault_is_said(REPAIRED[document]) == [], \
        document
    sentence = naming_the_evidence_and_the_opening(REPAIRED[document])[0]
    assert "wrong" in sentence.lower(), sentence


# --------------------------------------------------------------------------
# The rendered Inspector prompt
# --------------------------------------------------------------------------


def test_the_rendered_inspector_prompt_places_the_evidence_after_the_opening(
        tmp_path):
    """What the Inspector reads is the template with the prose layer and the
    brief schema resolved into it. All three used to reach it, and the two
    that agreed with each other contradicted the third; now every sentence in
    the render that says where the evidence goes says the same thing, and the
    template's own sentence is among them."""
    rendered = plain.inspector_prompt(tmp_path)
    assert placing_the_evidence_in_the_opening(rendered) == []
    assert superseded_clauses_in(rendered) == []
    saying = naming_the_evidence_and_the_opening(rendered)
    assert saying, rendered
    assert any(sentence in saying for sentence
               in naming_the_evidence_and_the_opening(titled.INSPECTOR))


def test_the_render_scan_reports_the_superseded_inspector_sentence(tmp_path):
    """Control: the same render with the Inspector's superseded sentence
    appended is reported, so the silence above is about what the render says."""
    planted = plain.inspector_prompt(tmp_path) + "\n\n" \
        + SUPERSEDED_INSPECTOR_SENTENCE + "\n"
    assert placing_the_evidence_in_the_opening(planted)
    assert superseded_clauses_in(planted) == [
        "where your file:line evidence already goes"]


# --------------------------------------------------------------------------
# The superseded clauses, gone from every document
# --------------------------------------------------------------------------


@DOCUMENTS
def test_no_document_carries_a_clause_this_story_superseded(document):
    assert superseded_clauses_in(SHIPPED[document]) == [], document


def test_the_clause_scan_reports_each_of_them_in_the_sentence_it_came_from():
    """Each superseded clause is found in exactly the sentence that carried
    it, across the line breaks the file wrapped it over."""
    reported = {}
    for name, sentence in SUPERSEDED.items():
        found = superseded_clauses_in(sentence)
        assert len(found) == 1, (name, found)
        reported[name] = found[0]
    assert set(reported.values()) == set(SUPERSEDED_CLAUSES)


# --------------------------------------------------------------------------
# What the repaired sentences were brought to, unchanged
# --------------------------------------------------------------------------


def test_the_texts_the_repair_was_brought_to_are_unchanged():
    """The prose layer, the assist prompt, the story schema and everything
    under orchestration/ are what this story found them, by the shared
    baseline the suite resolves a story's range with."""
    assert story_diff(list(LEFT_ALONE), validation_file=Path(__file__)) == ""


def test_the_same_diff_reports_a_path_this_story_did_change():
    """The control the emptiness above is read against."""
    assert story_diff([CHANGED], validation_file=Path(__file__)) != ""


def test_the_brief_schemas_body_description_is_what_it_was():
    """The body description is pinned by the plain-English module, and this
    module reads it as the wording the title description was brought to; it
    still puts the names after the opening and the evidence in the body."""
    body = plain.BRIEF_BODY_FIELD["description"]
    assert plain.missing(body, plain.LAYER_STATES["the three things the opening says"]) \
        == []
    assert "follow that opening" in collapsed(body)
    assert placing_the_evidence_in_the_opening(body) == []


def test_the_skills_body_section_is_what_it_was():
    """The skill's own body section is the wording its title section was
    brought to: the names and the evidence come in the paragraphs after the
    opening."""
    section = plain.section_named(titled.SKILL, "The body")
    assert "paragraphs after the opening" in collapsed(section)
    assert "file:line evidence also belongs" in collapsed(section)
    assert placing_the_evidence_in_the_opening(section) == []
    assert naming_the_evidence_and_the_opening(section)
