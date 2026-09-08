"""A brief's title states the behaviour the finished work produces.

Four documents used to teach a split rule: a title said what is wrong where the
brief stated a defect, and what should exist where it stated work to be done.
The consequence was a tracker column of complaints, every line of which
described something that will not exist once the work lands. One rule now
covers every brief, defects included — the title is the end state, in the
present tense, as though it already held — and the obligations that make that
rule safe come with it: the behaviour named has to pick this work out from its
neighbours, and what is wrong today is said in the body's opening line, with
`category` and `severity` carrying the fact that something is broken now.

What this module holds is the four shipped documents against that rule, and the
slug rule against the fact that this story did not touch it:

  * `schemas/story-brief.schema.json`'s `title` description states one rule for
    every brief, carries each obligation, and still says why a title is outside
    what a brief is filed under;
  * `prompts/inspector.md`, `plugin/skills/file-a-brief/SKILL.md` and
    `prompts/assist.md` each state that rule in their own words;
  * the Inspector prompt, the skill and the schema each teach it by contrast —
    a mis-pitched title marked as the one not to write, beside a corrected form
    — and the skill's examples are its own rather than the schema's or the
    Inspector's;
  * no document has regained the wording this story superseded;
  * the slug rule is byte-for-byte what it was in all three places that state
    it, each still says a slug is part of what a brief is filed under, and
    nothing added asks a writer to re-derive a slug because a title changed;
  * a brief titled in the superseded style still validates, because the rule is
    prose for a writer and not a constraint the schema enforces.

The four shipped documents are read live, because they *are* the subject: the
claim is about what this repository ships to the two agents that write briefs
and to the humans who read them. `tests/test_baseline_honesty.py` records that
classification. A fixture schema would say back only what this module had just
written into it.

Every absence asserted here is shown to be detectable. The superseded wordings
are carried as literal constants — a fact about documents a reader of this file
cannot see, which is why they are written out rather than resolved out of the
commit graph, where a rebase or a squash would move them — and each absence
scan is run over them and shown reporting what it is looking for. The contrast
scan is run over a copy of each shipped section with its good and bad examples
swapped, and the copy-detection scan over a copy of the skill carrying the
schema's sentence and the schema's example.

What the scans do not cover is stated rather than left to be discovered. The
copy-detection scan works at sentence granularity: it reports a document that
repeats a whole sentence of the schema's title description, and says nothing
about a shorter clause reused between two of them. The slug-instruction scan
works over a vocabulary of phrasings written out below, so a way of asking for
a re-derived slug that nobody thought of is not caught; it sits beside the
byte-for-byte assertions rather than instead of them.

The shape of a brief — its fields, its required list and its enums — is held by
`tests/test_a_brief_is_not_only_a_defect.py`, and the pre-story values recorded
there are imported rather than written out a second time. What is asserted here
about the shape is only what retitling could have disturbed.

Nothing here invokes a model and nothing here runs the suite.
"""
from pathlib import Path
import re

import pytest

import inspection
import schema_validator

import test_a_brief_is_not_only_a_defect as contract
import test_the_assist_agent_files_a_brief as filing

REPO_ROOT = Path(inspection.__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"
SCHEMAS = REPO_ROOT / "schemas"

collapsed = contract.collapsed

BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
TITLE = BRIEF_SCHEMA["properties"]["title"]
TITLE_DESCRIPTION = TITLE["description"]
SLUG_DESCRIPTION = BRIEF_SCHEMA["properties"]["slug"]["description"]

INSPECTOR = (PROMPTS / inspection.INSPECTOR_PROMPT).read_text(encoding="utf-8")
ASSIST = (PROMPTS / "assist.md").read_text(encoding="utf-8")
SKILL = filing.SKILL_TEXT
SKILL_REL = filing.SKILL_REL

#: The four documents this story brought into line, keyed by how a reader would
#: name them. Every scan below that is run over one is run over all four, or
#: over the subset the criterion names, so no document is held to the rule by
#: accident of being listed.
SHIPPED = {
    f"schemas/{inspection.BRIEF_SCHEMA}.schema.json": TITLE_DESCRIPTION,
    f"prompts/{inspection.INSPECTOR_PROMPT}": INSPECTOR,
    "prompts/assist.md": ASSIST,
    SKILL_REL: SKILL,
}

# --------------------------------------------------------------------------
# What these documents said before this story
#
# Literal values rather than a text recovered from this repository's history.
# What is asserted is that documents a reader of this file cannot see said
# these things, which is the one case the prose rules call a fact rather than a
# restatement — and an answer resolved out of the commit graph would move under
# a rebase, a squash or a rename, none of which is a property of the rule.
# --------------------------------------------------------------------------

#: The `title` description the schema carried, split rule and all. It is the
#: statement the other three took their wording from, which is why it is the
#: one this story replaced first.
SUPERSEDED_TITLE_DESCRIPTION = (
    "What the work is, in one line, for a human scanning a tracker: what is "
    "wrong where the brief states a defect, what should exist where it states "
    "work to be done. Prose, and deliberately not part of what the brief is "
    "filed under: two writings of one brief will phrase it differently, and "
    "filing on the phrasing would file it twice."
)

#: The Inspector's whole instruction about titles, hard-wrapped as the file
#: carried it — so the scans below are shown finding a sentence across the line
#: break, which is the only form it could ever have appeared in.
SUPERSEDED_INSPECTOR_SENTENCE = (
    "So the title may be as readable as\n"
    "you like, and the slug has to be derived by the rule above."
)

#: The two lines the skill said the same superseded thing in.
SUPERSEDED_SKILL_LINES = (
    "The title is prose for a human scanning a list and is deliberately not "
    "part of\nwhat the brief is filed under, so it may be as readable as you "
    "like."
)

#: The clause `prompts/assist.md` enumerated the title with, which named who
#: reads a title and nothing about what it should say.
SUPERSEDED_ASSIST_CLAUSE = (
    "A brief carries a title written for a human scanning a list; a short\n"
    "kebab-case slug naming the work itself rather than the fix or the file;"
)

SUPERSEDED = {
    "the schema's title description": SUPERSEDED_TITLE_DESCRIPTION,
    "the Inspector's title sentence": SUPERSEDED_INSPECTOR_SENTENCE,
    "the skill's title lines": SUPERSEDED_SKILL_LINES,
    "the assist prompt's title clause": SUPERSEDED_ASSIST_CLAUSE,
}

#: The wordings this story superseded, each written as a reader would meet it
#: in the prose. A document that regains any of them is teaching the rule this
#: story replaced, whatever else it also says.
SUPERSEDED_WORDINGS = (
    "as readable as you like",
    "what is wrong where the brief states a defect",
    "a title written for a human scanning a list",
    "the title is prose for a human scanning a list",
)

# --------------------------------------------------------------------------
# The slug rule, as it stood before this story and must stand after it
#
# A slug is part of what a brief is filed under, so a rewritten one files a
# duplicate; a title is outside the identity, which is what makes it safe to
# rewrite. The whole risk this story carries is the title rule leaking into the
# slug rule, so the slug rule is asserted byte for byte rather than in
# substance — including the wrapping, since a reflowed paragraph is a rewritten
# one for anything downstream that diffs it.
# --------------------------------------------------------------------------

SLUG_RULE_IN_THE_INSPECTOR_PROMPT = (
    "[The slug, and what a brief is filed under]\n"
    "Each brief you write carries a `slug`: a short kebab-case name for the\n"
    "defect itself. Derive it by this rule, so that two inspections of one "
    "defect\n"
    "derive the same slug and the second does not file a duplicate.\n"
    "\n"
    "- Name the defect, not the fix and not the file. "
    "`duplicated-blocked-path-\n"
    "  list`, not `fix-blocked-paths` and not `config-loader`.\n"
    "- Three to six words, lowercase, hyphen-separated, no digits unless the\n"
    "  defect is genuinely about a specific number.\n"
    "- Describe what is wrong in the most general terms that are still true "
    "of\n"
    "  this defect and not of its neighbours.\n"
)

SLUG_RULE_IN_THE_SKILL = (
    "### The slug\n"
    "\n"
    "The slug is part of what the brief is **filed under**, so two writings "
    "of one\n"
    "piece of work must derive the same one or the second is filed as a "
    "duplicate.\n"
    "Derive it by the rule the harness states:\n"
    "\n"
    "- Name the work itself — the defect, the missing behaviour, the "
    "structure to\n"
    "  be changed — and name neither the fix nor the file.\n"
    "- Three to six words, lowercase, hyphen-separated, no digits unless the "
    "work\n"
    "  is genuinely about a particular number.\n"
    "- Describe it in the most general terms still true of this work and not "
    "of its\n"
    "  neighbours.\n"
)

SLUG_RULE_IN_THE_SCHEMA = (
    "A short kebab-case name for the work itself — the defect where the brief "
    "states one, the behaviour that is missing or the structure to be changed "
    "where it states work to be done — naming neither the fix nor the file. "
    "The Inspector derives its own by the rule prompts/inspector.md states, so "
    "that two inspections of one defect derive the same one; a brief written "
    "by hand is under the same obligation for the same reason. It is the part "
    "of the identity that distinguishes two briefs of the same category "
    "against the same paths, so a brief without one has no stable name to be "
    "filed under and is dropped."
)

#: Ways of asking a writer to redo a slug. Searched as phrases, because the
#: bare words cannot be: two of the shipped documents say a slug is *not* safe
#: to rewrite, which is the opposite instruction and must not be reported. The
#: limit is that a phrasing nobody thought of is not caught, which is why the
#: byte-for-byte assertions above stand beside this rather than behind it.
SLUG_REDERIVATION_PHRASINGS = (
    "re-derive the slug",
    "re-derive its slug",
    "rederive the slug",
    "derive a new slug",
    "rewrite the slug",
    "rewriting the slug",
    "update the slug",
    "change the slug",
    "a new slug",
)


# --------------------------------------------------------------------------
# The scans
#
# Each is a function over a text, so the same one that decides a shipped
# document is run over a superseded text or a planted copy and shown reporting.
# --------------------------------------------------------------------------

#: A heading, as either document writes one: the Inspector's bracketed section
#: markers and the skill's markdown headings. Which heading names the title is
#: read off the document rather than written here, so a section renamed later
#: is still found by what it is about.
HEADING = re.compile(r"^(?:\[(?P<bracketed>[^\]\n]+)\]|#+\s+(?P<marked>.+))$",
                     re.MULTILINE)


def section_about_the_title(text: str) -> str:
    """The one section of `text` whose own heading names the title.

    Resolved rather than named, and refused when it is not exactly one: two
    sections naming the title would make every assertion below silently about
    whichever came first.
    """
    headings = [
        (match.start(), match.group("bracketed") or match.group("marked"))
        for match in HEADING.finditer(text)
    ]
    naming = [index for index, (_, heading) in enumerate(headings)
              if "title" in heading.lower()]
    assert len(naming) == 1, [heading for _, heading in headings]
    index = naming[0]
    start = headings[index][0]
    end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
    return text[start:end]


def missing_from_the_rule(text: str) -> list[str]:
    """Every term of the end-state rule the text does not state.

    The rule has three parts and a document that drops any of them has stopped
    teaching it: the subject a title names, the tense it is written in, and the
    stance that the behaviour is spoken of as holding already.
    """
    said = collapsed(text).lower()
    return [term for term in ("behaviour the finished work produces",
                              "present tense", "already held")
            if term not in said]


def missing_the_specificity_obligation(text: str) -> list[str]:
    """Every term of the obligation that comes with the rule the text drops.

    An end state that fits twenty briefs is worse than the fault it replaced,
    so the rule is only safe where the remedy is stated with it: a sharper
    behaviour, never a return to naming the fault.
    """
    said = collapsed(text).lower()
    missing = [term for term in ("underspecified", "fault") if term not in said]
    if not any(term in said for term in ("sharper", "sharpen")):
        missing.append("sharper")
    return sorted(missing)


def missing_where_the_fault_is_said(text: str) -> list[str]:
    """Every term of what replaces a fault-named title the text drops.

    The fact that something is broken now is not lost by the rule, and a
    document that says only "write the end state" has lost it: it moves to the
    body's opening line, and to the two fields a board sorts on.
    """
    said = collapsed(text).lower()
    missing = [term for term in ("body", "category", "severity")
               if term not in said]
    if not any(term in said for term in ("broken", "wrong")):
        missing.append("broken")
    return sorted(missing)


def superseded_wordings_in(text: str) -> list[str]:
    """Every wording this story replaced that the text still carries."""
    said = collapsed(text).lower()
    return sorted(wording for wording in SUPERSEDED_WORDINGS
                  if wording in said)


def quoted(text: str, quote: str) -> list[str]:
    return re.findall(f"{quote}([^{quote}]+){quote}", collapsed(text))


def example_titles(text: str, quote: str) -> list[str]:
    """Every quoted span long enough to be a title rather than a field name.

    A document quotes its field names the same way it quotes its examples, so
    length is what tells them apart: `title` and `severity` are one word and no
    title this rule would accept is.
    """
    return [span for span in quoted(text, quote) if len(span.split()) >= 4]


def titles_marked_as_wrong(text: str, quote: str) -> list[str]:
    """Every example title the text introduces as the one not to write."""
    marked = re.findall(f"(?i)\\bnot {quote}([^{quote}]+){quote}",
                        collapsed(text))
    return [span for span in marked if len(span.split()) >= 4]


#: Words by which a title describes a deficiency rather than a behaviour. A
#: title carrying one is pitched at the state the system is in today, which is
#: the whole thing this story replaced.
FAULT_WORDS = ("never", "nothing", "fails", "missing", "stuck", "cannot",
               "does not", "no longer")


def names_a_fault(title: str) -> bool:
    said = title.lower()
    return any(word in said for word in FAULT_WORDS)


def contrast_complaints(text: str, quote: str) -> list[str]:
    """Every way the text's worked pair fails to teach the rule by contrast.

    Three things have to hold together, and each is reported by name so a
    failure says which: the text marks at least one title as the wrong pitch,
    every title it so marks describes a fault, and at least one title it offers
    without that mark describes a behaviour instead. A section that satisfies
    the first two and not the third has shown a reader what not to write and
    left them without the corrected form.
    """
    examples = example_titles(text, quote)
    wrong = titles_marked_as_wrong(text, quote)
    complaints = []
    if not wrong:
        complaints.append("no title is marked as the one not to write")
    complaints.extend(f"marked as wrong but names no fault: {title}"
                      for title in wrong if not names_a_fault(title))
    offered = [title for title in examples if title not in wrong]
    complaints.extend(f"offered as the corrected form but names a fault: "
                      f"{title}" for title in offered if names_a_fault(title))
    if not any(not names_a_fault(title) for title in offered):
        complaints.append("no corrected form is offered")
    return complaints


def with_the_pair_swapped(text: str, quote: str) -> str:
    """`text` with its worked pair exchanged, as a section written the old way.

    The control for the contrast scan: a document that had taught the rule
    backwards would carry exactly this — the fault-named title offered as the
    right one, and the end state marked as the one not to write.
    """
    said = collapsed(text)
    wrong = titles_marked_as_wrong(text, quote)
    right = [title for title in example_titles(text, quote)
             if title not in wrong and not names_a_fault(title)]
    assert wrong and right, (wrong, right)
    placeholder = "\x00"
    return (said.replace(wrong[0], placeholder)
                .replace(right[0], wrong[0])
                .replace(placeholder, right[0]))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9:._-]+", collapsed(text).lower())


def contains_run(haystack: str, needle: str) -> bool:
    """Whether `haystack` carries `needle`'s words consecutively.

    Compared as words rather than as characters, so the emphasis marks and
    backticks a markdown document wraps a phrase in do not hide a quotation.
    """
    outer, inner = tokens(haystack), tokens(needle)
    if not inner:
        return False
    return any(outer[index:index + len(inner)] == inner
               for index in range(len(outer) - len(inner) + 1))


def schema_sentences() -> list[str]:
    """The sentences of the schema's `title` description, long ones only.

    A short sentence shared between two documents is agreement rather than
    quotation; these are all long, and each is a whole statement of a part of
    the rule.
    """
    return [sentence for sentence
            in re.split(r"(?<=[.]) ", collapsed(TITLE_DESCRIPTION))
            if len(tokens(sentence)) >= 8]


def quoted_schema_sentences_in(text: str) -> list[str]:
    return [sentence for sentence in schema_sentences()
            if contains_run(text, sentence)]


def sentences_pairing_a_retitle_with_a_slug(text: str) -> list[str]:
    """Every sentence that mentions retitling and the slug together.

    A title is outside the identity and a slug is inside it, so retitling asks
    nothing of the slug. A sentence that names both is the leak this story had
    to avoid — and the bare words will not do here, because two of these
    documents say a slug is *not* safe to rewrite, which is the opposite.
    """
    return [sentence for sentence in re.split(r"(?<=[.]) ", collapsed(text))
            if "retitl" in sentence.lower() and "slug" in sentence.lower()]


def slug_rederivations_in(text: str) -> list[str]:
    said = collapsed(text).lower()
    return sorted(phrase for phrase in SLUG_REDERIVATION_PHRASINGS
                  if phrase in said)


DOCUMENTS = pytest.mark.parametrize(
    "document", sorted(SHIPPED), ids=sorted(SHIPPED))

#: The three documents that teach the rule by a worked pair, with the character
#: each quotes an example in: the two prose documents use markdown's backtick,
#: and the schema — a JSON string, where a double quote would have to be
#: escaped at every reader's expense — uses an apostrophe.
WORKED_PAIRS = {
    f"schemas/{inspection.BRIEF_SCHEMA}.schema.json": (TITLE_DESCRIPTION, "'"),
    f"prompts/{inspection.INSPECTOR_PROMPT}":
        (section_about_the_title(INSPECTOR), "`"),
    SKILL_REL: (section_about_the_title(SKILL), "`"),
}

PAIRS = pytest.mark.parametrize("document", sorted(WORKED_PAIRS),
                                ids=sorted(WORKED_PAIRS))

#: The three documents that restate what the schema states. The schema is left
#: out of the copy-detection scan below for the reason it exists: it is where
#: the sentence is stated, so it carries its own sentences by construction.
SCHEMA_DOCUMENT = f"schemas/{inspection.BRIEF_SCHEMA}.schema.json"
RESTATING = sorted(name for name in SHIPPED if name != SCHEMA_DOCUMENT)
RESTATEMENTS = pytest.mark.parametrize("document", RESTATING, ids=RESTATING)


# --------------------------------------------------------------------------
# The rule, in all four documents
# --------------------------------------------------------------------------


@DOCUMENTS
def test_every_shipped_document_states_the_end_state_rule(document):
    """The subject a title names, the tense, and the stance — in each of the
    four, in its own place.

    The control is the same scan over the four texts this story replaced, which
    is the assertion below: none of them stated any of it, so a silence here is
    a fact about what these documents say rather than about a scan that has
    stopped looking for anything.
    """
    assert missing_from_the_rule(SHIPPED[document]) == [], document


@pytest.mark.parametrize("superseded", sorted(SUPERSEDED), ids=sorted(SUPERSEDED))
def test_the_rule_scan_reports_every_text_this_story_replaced(superseded):
    """The other half of the assertion above: run over what these documents
    said before, the same scan reports the rule missing entirely."""
    assert missing_from_the_rule(SUPERSEDED[superseded]) \
        == ["behaviour the finished work produces", "present tense",
            "already held"], superseded


def test_the_schema_states_one_rule_covering_a_defect_and_work_to_be_done():
    """One sentence naming both, rather than a document that mentions defects
    in one place and work in another: what the split rule did was give a reader
    two rules, and a description that names each kind separately has kept them.
    """
    sentences = re.split(r"(?<=[.]) ", collapsed(TITLE_DESCRIPTION))
    covering_both = [sentence for sentence in sentences
                     if "defect" in sentence.lower()
                     and "work to be done" in sentence.lower()]
    assert covering_both, sentences


@DOCUMENTS
def test_no_shipped_document_states_a_rule_that_differs_for_a_defect(document):
    """Each of the four says the rule holds for a defect brief too.

    Stated where the rule is stated rather than anywhere in the document: an
    Inspector prompt that mentions defects on every page has not thereby said
    that its own briefs are titled for their end state.
    """
    stated = SHIPPED[document]
    if document in WORKED_PAIRS:
        stated = WORKED_PAIRS[document][0]
    else:
        stated = next(sentence for sentence
                      in re.split(r"(?<=[.]) ", collapsed(stated))
                      if "behaviour the finished work produces" in sentence)
    assert "defect" in collapsed(stated).lower(), stated


@pytest.mark.parametrize("document", sorted(WORKED_PAIRS), ids=sorted(WORKED_PAIRS))
def test_the_specificity_obligation_travels_with_the_rule(document):
    """An end state that fits twenty briefs is useless, and the remedy is a
    sharper behaviour rather than a return to naming the fault.

    The control is the same scan over the four superseded texts, which is the
    assertion below.
    """
    assert missing_the_specificity_obligation(WORKED_PAIRS[document][0]) \
        == [], document


@pytest.mark.parametrize("superseded", sorted(SUPERSEDED), ids=sorted(SUPERSEDED))
def test_the_specificity_scan_reports_every_text_this_story_replaced(superseded):
    assert missing_the_specificity_obligation(SUPERSEDED[superseded]) \
        == ["fault", "sharper", "underspecified"], superseded


@pytest.mark.parametrize("document", sorted(WORKED_PAIRS), ids=sorted(WORKED_PAIRS))
def test_each_document_says_where_what_is_wrong_today_is_said_instead(document):
    """The body's opening line, and the two fields a board sorts on."""
    assert missing_where_the_fault_is_said(WORKED_PAIRS[document][0]) == [], \
        document


def test_the_assist_prompts_title_clause_says_what_a_title_should_say():
    """The clause names the behaviour and the tense, not only who reads it.

    Located by what it is about rather than by where it sits, and the control
    is the clause it replaced: the same search over that one finds a reader and
    no rule.
    """
    clause = next(sentence for sentence
                  in re.split(r"(?<=[.]) ", collapsed(ASSIST))
                  if "carries a title" in sentence)
    assert missing_from_the_rule(clause) == [], clause

    superseded = collapsed(SUPERSEDED_ASSIST_CLAUSE)
    assert "carries a title" in superseded
    assert missing_from_the_rule(superseded) == [
        "behaviour the finished work produces", "present tense", "already held"]


# --------------------------------------------------------------------------
# The rule taught by contrast
# --------------------------------------------------------------------------


@PAIRS
def test_each_document_shows_a_mis_pitched_title_beside_its_corrected_form(
        document):
    """A worked pair, in each of the three that carry one.

    The control is below: the same scan over the same section with its pair
    exchanged reports it, so a silence here is a fact about which title the
    document marks rather than about a scan that finds no titles at all.
    """
    text, quote = WORKED_PAIRS[document]
    assert example_titles(text, quote), text
    assert contrast_complaints(text, quote) == [], document


@PAIRS
def test_the_contrast_scan_reports_a_section_that_teaches_it_backwards(document):
    """The same section with the fault-named title offered as the right one."""
    text, quote = WORKED_PAIRS[document]
    swapped = with_the_pair_swapped(text, quote)
    assert swapped != collapsed(text)
    assert contrast_complaints(swapped, quote), swapped


def test_the_skills_examples_are_its_own_and_not_the_schemas_or_the_prompts():
    """An example copied across is not an example of the document's own.

    The control is a copy of the skill's section with the schema's example
    planted in it, which the same comparison reports.
    """
    section = section_about_the_title(SKILL)
    mine = set(example_titles(section, "`"))
    theirs = set(example_titles(TITLE_DESCRIPTION, "'")) \
        | set(example_titles(section_about_the_title(INSPECTOR), "`"))
    assert mine, section
    assert theirs
    assert mine.isdisjoint(theirs), sorted(mine & theirs)

    borrowed = next(iter(theirs))
    planted = collapsed(section).replace(
        f"`{example_titles(section, '`')[0]}`", f"`{borrowed}`", 1)
    assert not set(example_titles(planted, "`")).isdisjoint(theirs)


@RESTATEMENTS
def test_no_document_quotes_a_whole_sentence_of_the_schemas_description(
        document):
    """The rule is stated once and restated, never quoted at the reader.

    Sentence granularity, and that is the whole of what this reports: a clause
    shorter than a sentence reused between two documents is outside it. The
    control is the same document with one of the schema's sentences appended,
    which the same scan reports.
    """
    assert schema_sentences()
    assert quoted_schema_sentences_in(SHIPPED[document]) == [], document

    planted = SHIPPED[document] + "\n\n" + schema_sentences()[0] + "\n"
    assert quoted_schema_sentences_in(planted) == [schema_sentences()[0]]


@DOCUMENTS
def test_no_document_carries_a_wording_this_story_superseded(document):
    """The absence, with the same scan shown reporting each superseded wording
    in the text it came from — so a silence here is a fact about the shipped
    document rather than about a search for phrases nothing could match."""
    assert superseded_wordings_in(SHIPPED[document]) == [], document


def test_the_superseded_wording_scan_reports_each_of_them():
    reported = set()
    for text in SUPERSEDED.values():
        reported.update(superseded_wordings_in(text))
    assert reported == set(SUPERSEDED_WORDINGS)


# --------------------------------------------------------------------------
# The slug rule, which this story does not touch
# --------------------------------------------------------------------------


@pytest.mark.parametrize("document,rule", [
    pytest.param(INSPECTOR, SLUG_RULE_IN_THE_INSPECTOR_PROMPT,
                 id=inspection.INSPECTOR_PROMPT),
    pytest.param(SKILL, SLUG_RULE_IN_THE_SKILL, id="SKILL.md"),
    pytest.param(SLUG_DESCRIPTION, SLUG_RULE_IN_THE_SCHEMA, id="slug"),
])
def test_the_slug_rule_is_byte_for_byte_what_it_was(document, rule):
    """Wrapping included. A slug is part of what a brief is filed under, so a
    reflowed paragraph is a rewritten rule as far as anything that diffs these
    documents is concerned, and the risk this story carried was the title rule
    leaking into it."""
    assert rule in document, rule


@pytest.mark.parametrize("stated,names_it", [
    pytest.param(INSPECTOR, "slug", id=inspection.INSPECTOR_PROMPT),
    pytest.param(SKILL, "slug", id="SKILL.md"),
    pytest.param(SLUG_DESCRIPTION, "identity", id="slug"),
])
def test_each_of_the_three_still_says_a_slug_is_part_of_the_identity(
        stated, names_it):
    """What each has to go on saying, in the term it says it in.

    The two prose documents name the slug; the schema's description names the
    identity instead, because the field it is describing *is* the slug and
    saying so would be the description repeating its own key.
    """
    said = collapsed(stated).lower()
    assert "filed under" in said
    assert names_it in said


@DOCUMENTS
def test_no_document_asks_for_a_slug_to_be_redone_when_a_title_changes(document):
    """Two scans, because one of them cannot use the bare words: two of these
    documents say a slug is *not* safe to rewrite, which is the opposite
    instruction. The controls are the same two scans over a planted paragraph
    that does ask for it.
    """
    text = SHIPPED[document]
    assert slug_rederivations_in(text) == [], document
    assert sentences_pairing_a_retitle_with_a_slug(text) == [], document

    planted = text + "\n\nWhen you retitle a brief, " + ", ".join(
        SLUG_REDERIVATION_PHRASINGS) + " to match.\n"
    assert slug_rederivations_in(planted) == sorted(SLUG_REDERIVATION_PHRASINGS)
    assert sentences_pairing_a_retitle_with_a_slug(planted)


# --------------------------------------------------------------------------
# What retitling could have disturbed in the shape
#
# The fields, the required list and the enums are held by
# `tests/test_a_brief_is_not_only_a_defect.py`, and the pre-story values it
# records are imported here rather than written out again. What is asserted
# here is only what a rule about how a title is pitched could have reached.
# --------------------------------------------------------------------------


def test_the_title_field_gained_no_constraint_beyond_its_description():
    """The rule is prose for a writer, not something the validator enforces.

    A `pattern` or a `minLength` added with it would refuse briefs already
    filed, which is the one way a rule about phrasing can break a queue.
    """
    assert set(TITLE) == {"type", "description"}, TITLE
    assert TITLE["type"] == "string"


def test_a_brief_titled_the_superseded_way_still_validates():
    """A title naming the fault is now badly pitched and is not invalid.

    Nothing in this story retitles a brief already filed and nothing asks
    anyone to, so a brief written under the old rule has to go on validating.
    The control is the same validator refusing a title of the wrong type, so
    "it validates" is a fact about the brief rather than about a validator that
    has stopped deciding anything.
    """
    written = contract.brief(
        title="A pending outbox entry never says why it is pending")
    assert contract.problems(written) == []

    refused = contract.problems(contract.brief(title=[]))
    assert refused
    assert any("$.title" in problem for problem in refused), refused


def test_the_shape_this_story_leaves_is_the_shape_it_found():
    """Imported rather than restated: the pre-story fields, required list and
    enums are recorded once, in the module whose subject they are."""
    assert tuple(BRIEF_SCHEMA["properties"]) == contract.FIELDS_BEFORE
    assert tuple(BRIEF_SCHEMA["required"]) == contract.REQUIRED_BEFORE
    assert BRIEF_SCHEMA["properties"]["severity"]["enum"] \
        == contract.SEVERITIES_BEFORE
    assert BRIEF_SCHEMA["properties"]["confidence"]["enum"] \
        == contract.CONFIDENCES_BEFORE


@pytest.mark.parametrize("field", ["severity", "confidence"])
def test_the_scales_say_nothing_about_how_a_title_is_pitched(field):
    """They carry the fact that something is broken now, and this story left
    them alone. The control is the same scan over each description with the
    rule planted in it.
    """
    described = BRIEF_SCHEMA["properties"][field]["description"]
    assert missing_from_the_rule(described), field
    planted = described + " " + collapsed(TITLE_DESCRIPTION)
    assert missing_from_the_rule(planted) == []


def test_the_shipped_documents_are_the_ones_these_assertions_read():
    """The subject named once, so a reader can see that nothing above was
    decided against a document this module wrote for itself."""
    assert (SCHEMAS / f"{inspection.BRIEF_SCHEMA}.schema.json").is_file()
    assert (PROMPTS / inspection.INSPECTOR_PROMPT).is_file()
    assert (PROMPTS / "assist.md").is_file()
    assert filing.SKILL_PATH.is_file()
    assert set(SHIPPED) == {
        f"schemas/{inspection.BRIEF_SCHEMA}.schema.json",
        f"prompts/{inspection.INSPECTOR_PROMPT}",
        "prompts/assist.md",
        SKILL_REL,
    }
