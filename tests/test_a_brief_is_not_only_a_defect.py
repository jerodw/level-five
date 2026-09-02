"""A brief describes work, not only a defect.

`schemas/story-brief.schema.json` used to open on "one defect the Inspector
found", and its whole shape followed from that premise: the categories were
kinds of defect, severity asked how bad the consequence was if the finding was
left alone, and confidence asked whether the finding was real. That reading is
wrong the first time a human files a brief for work that was never built —
leaving an unbuilt feature alone has no consequence, and a feature somebody has
just asked for cannot be imagined.

What this module holds is the widened contract and the two prompts that restate
it:

  * both appended categories validate, every category the schema accepted
    before is still accepted, and the ten keep their positions ahead of the two;
  * the severity and confidence enums are unchanged in membership and in type,
    the declared fields and the required list are unchanged, so a brief written
    against the previous schema validates unchanged;
  * what a brief is filed under is untouched, and one brief written twice with
    its title rewritten and its severity changed still files one entry;
  * no description defines severity by what happens if the finding is left
    alone, none defines confidence solely as whether the finding is real, and
    none calls the brief's subject a defect without also saying that work to be
    done is a brief's subject too;
  * `prompts/assist.md` says a brief may state a defect, a feature or a
    refactor and restates severity and confidence in the schema's terms;
  * `prompts/inspector.md` still lists its own categories, still carries its two
    mechanical rules and its slug derivation, and its slug sentence is scoped to
    the briefs the Inspector writes.

The shipped schema and the two shipped prompts are read live, because they
*are* the subject: a fixture schema would say only what this module had just
written into it, and the criteria are about what this repository ships to its
own two producers of briefs. `tests/test_baseline_honesty.py` records that
classification.

Every absence asserted here is shown to be detectable. The pre-story
descriptions are carried as constants and each scan is run over them, so a
silence below is a fact about the text rather than about a search that has
stopped looking; the prompt scans are run over copies of the shipped prompts
with the violation planted in them. Nothing here resolves this repository's
commit graph: the previous contract is recorded as literal values, which is
what it is — a fact about a document a reader of this file cannot see — and
recording it that way keeps these assertions still under a rebase, a squash or
a rename.

Nothing here invokes a model and nothing here runs the suite.
"""
from pathlib import Path
import json
import re

import pytest

import inspection
import outbox
import schema_validator

REPO_ROOT = Path(inspection.__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"

#: The two shapes as they ship. The brief schema is the subject of nearly
#: everything below; the envelope's is here only so a name the Inspector's
#: prompt states about the file it writes is recognised as declared somewhere
#: rather than mistaken for an invented brief field.
BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
FINDINGS_SCHEMA = schema_validator.load_schema(inspection.FINDINGS_SCHEMA)

PROPERTIES = BRIEF_SCHEMA["properties"]
DECLARED_FIELDS = tuple(PROPERTIES)
REQUIRED_FIELDS = tuple(BRIEF_SCHEMA["required"])

#: Every description the schema carries, its own included, keyed by what it
#: describes. Read off the document rather than listed, so a field added later
#: is scanned without this module being told about it.
DESCRIPTIONS = {"(the schema itself)": BRIEF_SCHEMA["description"]} | {
    name: prop.get("description", "") for name, prop in PROPERTIES.items()
}

CATEGORIES = PROPERTIES["category"]["enum"]
SEVERITIES = PROPERTIES["severity"]["enum"]
CONFIDENCES = PROPERTIES["confidence"]["enum"]
EFFORTS = PROPERTIES["effort"]["enum"]

# --------------------------------------------------------------------------
# The contract as it stood before this story
#
# Literal values rather than a text recovered from this repository's history.
# What is being asserted is that a document a reader of this file cannot see
# said these things, which is the one case the prose rules call a fact rather
# than a restatement — and an answer resolved out of the commit graph would
# move under a rebase, a squash or a rename, none of which is a property of
# the contract.
# --------------------------------------------------------------------------

#: The categories the schema accepted before this story, in the order it
#: listed them. They are also exactly the kinds of defect the Inspector reports.
CATEGORIES_BEFORE = (
    "standards-drift",
    "docs-drift",
    "cross-path-parity",
    "structural-duplication",
    "correctness",
    "robustness",
    "complexity",
    "coverage-gap",
    "security",
    "performance",
)

#: The two this story appends, in the order it appends them.
APPENDED_CATEGORIES = ("feature", "refactor")

SEVERITIES_BEFORE = [1, 2, 3]
CONFIDENCES_BEFORE = ["low", "medium", "high"]
EFFORTS_BEFORE = ["S", "M", "L"]

FIELDS_BEFORE = ("title", "slug", "body", "category", "severity", "confidence",
                 "effort", "workflow", "paths", "not_in_scope")
REQUIRED_BEFORE = ("title", "slug", "body", "category", "severity",
                   "confidence", "effort", "workflow")

#: What a brief is filed under, and the order `inspection.identity` returns it
#: in. Widening what a brief may say must not touch this: a brief filed before
#: this story is filed under the same key after it.
IDENTITY_MEMBERS = ("kind", "category", "paths", "slug")

#: The descriptions this story replaced, each carried so the scan that reports
#: it can be shown reporting something. They are controls and nothing else:
#: no assertion below claims the schema still says any of this.
SUPERSEDED_DESCRIPTIONS = {
    "title":
        "What the defect is, in one line, for a human scanning a tracker. "
        "Prose, and deliberately not part of what the brief is filed under: "
        "two inspections of one defect will phrase it differently, and filing "
        "on the phrasing would file it twice.",
    "slug":
        "A short kebab-case name for the defect, derived by the rule "
        "prompts/inspector.md states so that two inspections of the same "
        "defect derive the same one. It is the part of the identity that "
        "distinguishes two findings of the same category against the same "
        "paths, so a brief without one has no stable name to be filed under "
        "and is dropped.",
    "category":
        "What kind of defect this is. Part of the identity, because it is a "
        "classification the Inspector makes from the code rather than a "
        "phrasing it chooses.",
    "severity":
        "How bad the consequence is if this is left alone: 3 is a defect that "
        "produces wrong behaviour or silently disables a check, 2 is one that "
        "costs correctness of understanding or will produce a defect under a "
        "foreseeable change, 1 is one worth fixing when the code is next open. "
        "Defined by consequence and not by effort or by confidence, and a "
        "finding may not be 3 unless its confidence is high. It is payload "
        "rather than identity, because it is a judgement a second inspection "
        "may rate differently about one defect.",
}

#: The confidence description this story replaced. Kept apart from the four
#: above because it is the control for a different scan: it never used the word
#: defect, so the defect-premise scan has nothing to say about it, and what is
#: wrong with it is that it asks only whether the finding is real.
SUPERSEDED_CONFIDENCE = (
    "How sure the Inspector is that this is real, as an axis separate from "
    "severity: a high-consequence guess and a certain triviality are different "
    "things and collapsing them into one number loses both."
)

#: The sentence `prompts/inspector.md` opened its slug section with, stated of
#: every brief rather than of the ones the Inspector writes, hard-wrapped as the
#: file carried it — so the search below is shown finding a sentence across the
#: line break, which is the only form it could ever have appeared in.
SUPERSEDED_SLUG_OPENING_AS_WRAPPED = (
    "Each brief carries a `slug`: a short kebab-case name for the defect\n"
    "itself."
)


def collapsed(text: str) -> str:
    """`text` with every run of whitespace reduced to one space.

    A prompt and a schema description are both wrapped for a reader, so a
    sentence in either is not a line: a search for a phrase has to run over the
    prose rather than over the wrapping.
    """
    return " ".join(text.split())


def brief(**overrides) -> dict:
    """One brief this module wrote, valid by construction.

    Every enum-valued field takes a member the schema declares, so the fixture
    carries no value of its own and a member renamed in the schema renames it
    here too.
    """
    written = {
        "title": "A brief a test wrote, phrased one way",
        "slug": "a-brief-a-test-wrote",
        "body": "The case, with its evidence at orchestration/inspection.py:1.",
        "category": CATEGORIES[0],
        "severity": SEVERITIES[0],
        "confidence": CONFIDENCES[0],
        "effort": EFFORTS[0],
        "workflow": "a-workflow-a-definition-carries",
    }
    written.update(overrides)
    return written


def problems(instance: dict) -> list[str]:
    return schema_validator.validate(instance, BRIEF_SCHEMA)


# --------------------------------------------------------------------------
# What the schema accepts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", CATEGORIES)
def test_a_brief_carrying_each_declared_category_validates(category):
    """Both appended categories and all ten that preceded them, one case each.

    Parametrized over the enum rather than over a list written here, so a
    category the schema gains is validated the moment it is declared. The
    control is below: a category outside the enum is refused, naming the field.
    """
    assert problems(brief(category=category)) == [], category


def test_a_category_outside_the_enum_is_still_refused_naming_the_field():
    """The control for every acceptance above: this validator does say no."""
    refused = problems(brief(category="zzz-no-enum-carries-this-name"))
    assert refused
    assert any("$.category" in problem for problem in refused), refused


def test_the_ten_earlier_categories_keep_their_positions_ahead_of_the_two():
    """Appended, not merged in: a brief filed before this story keeps the key
    it was filed under, and the key carries the category."""
    assert tuple(CATEGORIES[:len(CATEGORIES_BEFORE)]) == CATEGORIES_BEFORE
    assert tuple(CATEGORIES[len(CATEGORIES_BEFORE):]) == APPENDED_CATEGORIES


def test_the_severity_and_confidence_enums_are_unchanged_in_value_and_in_type():
    """Membership, order and type. `severity` staying integer matters beyond
    validation: `orchestration/inspection.py` orders on it when it caps an
    inspection, and a string enum would order alphabetically and silently."""
    assert SEVERITIES == SEVERITIES_BEFORE
    assert CONFIDENCES == CONFIDENCES_BEFORE
    assert EFFORTS == EFFORTS_BEFORE
    assert all(isinstance(level, int) and not isinstance(level, bool)
               for level in SEVERITIES), SEVERITIES
    assert max(SEVERITIES) == SEVERITIES[-1]


def test_no_field_was_added_or_removed_and_the_required_list_is_unchanged():
    assert DECLARED_FIELDS == FIELDS_BEFORE
    assert REQUIRED_FIELDS == REQUIRED_BEFORE


@pytest.mark.parametrize("category", CATEGORIES_BEFORE)
def test_a_brief_written_against_the_previous_schema_validates_unchanged(
        category):
    """The whole of what a brief could have said before this story: any of the
    ten categories, the highest severity, the highest confidence, the largest
    effort, and the two optional fields carried."""
    written = brief(category=category, severity=SEVERITIES_BEFORE[-1],
                    confidence=CONFIDENCES_BEFORE[-1],
                    effort=EFFORTS_BEFORE[-1],
                    paths=["orchestration/inspection.py"],
                    not_in_scope=["the queue, which this brief does not touch"])
    assert problems(written) == [], category


@pytest.mark.parametrize("missing", REQUIRED_BEFORE)
def test_a_brief_missing_a_required_field_is_still_refused(missing):
    """The control for the acceptance above: `required` still bites, field by
    field, so "it validates" is a fact about the brief rather than about a
    schema that had stopped requiring anything."""
    written = brief()
    del written[missing]
    refused = problems(written)
    assert refused
    assert any(missing in problem for problem in refused), refused


# --------------------------------------------------------------------------
# What a brief is filed under
# --------------------------------------------------------------------------


def test_what_a_brief_is_filed_under_is_still_kind_category_paths_and_slug():
    identity = inspection.identity(brief(paths=["orchestration/inspection.py"]))
    assert tuple(identity) == IDENTITY_MEMBERS
    assert identity["category"] == CATEGORIES[0]
    assert identity["slug"] == brief()["slug"]


@pytest.mark.parametrize("category", APPENDED_CATEGORIES)
def test_a_brief_in_an_appended_category_is_filed_under_the_same_members(
        category):
    """Widening the enum widens what the identity's category may hold and
    nothing else."""
    identity = inspection.identity(brief(category=category))
    assert tuple(identity) == IDENTITY_MEMBERS
    assert identity["category"] == category


def test_one_brief_written_twice_files_one_entry(tmp_path):
    """Rewritten title, changed severity, one entry.

    Both are payload rather than identity, which is what the identity exists
    for: a model rephrases a title and re-rates a severity between runs, and an
    identity carrying either files a duplicate on every inspection. The control
    is the third writing, which differs in a member the identity *does* carry
    and lands as a second entry — so "one entry" is a fact about what changed
    rather than about a queue that files everything under one name.
    """
    queue = tmp_path / "queue"
    first = brief()
    rewritten = brief(title="The same work, phrased another way",
                      severity=SEVERITIES[-1], confidence=CONFIDENCES[-1])

    assert inspection.identity(first) == inspection.identity(rewritten)
    for written in (first, rewritten):
        assert outbox.enqueue(queue, written, inspection.identity(written))
    assert len(outbox.entry_files(queue)) == 1

    elsewhere = brief(slug="other-work-entirely")
    assert outbox.enqueue(queue, elsewhere, inspection.identity(elsewhere))
    assert len(outbox.entry_files(queue)) == 2


# --------------------------------------------------------------------------
# What the schema's own prose says
#
# Three scans, each written so it can report as well as stay silent, and each
# run over the descriptions this story replaced. A scan that has stopped seeing
# anything passes a schema that says nothing; a scan shown reporting the text
# it was written against does not.
# --------------------------------------------------------------------------

#: The words that say a description has work to be done in view and not only a
#: defect. Deliberately more than the two appended categories: a description may
#: say "work" without naming either kind, which is what most of them do.
WORK_WORDS = ("work",) + APPENDED_CATEGORIES

#: The clause that defined severity by the consequence of inaction. Unquotable
#: as a fact about work somebody wants: nothing happens if a feature nobody
#: built is left alone.
CONSEQUENCE_OF_INACTION = "left alone"


def defect_only_descriptions(descriptions: dict[str, str]) -> list[str]:
    """Every description that calls the brief's subject a defect and stops there.

    Naming a defect is not the fault — the Inspector's briefs are defects and
    the schema has to say so. The fault is a description in which a defect is
    the *only* thing a brief can be about, which is what a description saying
    "defect" and never saying that work to be done is also a brief's subject
    says to the reader deciding whether their feature belongs here.
    """
    return sorted(
        name for name, text in descriptions.items()
        if "defect" in text.lower()
        and not any(word in text.lower() for word in WORK_WORDS)
    )


def severity_levels_missing_a_reading(description: str,
                                      levels: list) -> list[str]:
    """Every severity level the description does not give both readings for.

    A level's reading is the run of text from where the description says
    "<level> is" up to where it says so of the *next level it states*, or to
    the end. Ordered by where each level is stated rather than by its value,
    because a description is free to state them highest-first, which is what
    this one does. Both readings are required in the run: what the level means
    for a defect, and what it means for work that is wanted. A level the
    description never states at all is reported too, since a level with no
    reading at all has neither.
    """
    marked = [(level, re.search(rf"\b{level} is\b", description))
              for level in levels]
    found = sorted(((level, match) for level, match in marked if match),
                   key=lambda pair: pair[1].start())
    missing = [str(level) for level, match in marked if not match]
    for index, (level, match) in enumerate(found):
        end = (found[index + 1][1].start() if index + 1 < len(found)
               else len(description))
        reading = description[match.start():end].lower()
        if "defect" not in reading or not any(word in reading
                                              for word in WORK_WORDS):
            missing.append(str(level))
    return sorted(missing)


def test_no_description_calls_the_briefs_subject_a_defect_and_stops_there():
    """The absence, with the same scan shown reporting the four descriptions
    this story replaced — each of which named a defect and nothing else."""
    assert defect_only_descriptions(DESCRIPTIONS) == []
    assert defect_only_descriptions(SUPERSEDED_DESCRIPTIONS) \
        == sorted(SUPERSEDED_DESCRIPTIONS)


def test_the_schemas_own_description_opens_on_work_rather_than_on_a_defect():
    described = collapsed(BRIEF_SCHEMA["description"]).lower()
    assert "work" in described
    for category in APPENDED_CATEGORIES:
        assert category in described, category


def test_severity_is_no_longer_defined_by_what_happens_if_this_is_left_alone():
    """The absence, controlled by the clause it replaced."""
    assert CONSEQUENCE_OF_INACTION not in collapsed(DESCRIPTIONS["severity"])
    assert CONSEQUENCE_OF_INACTION in collapsed(
        SUPERSEDED_DESCRIPTIONS["severity"])


def test_every_severity_level_is_given_a_defect_reading_and_a_work_reading():
    """Both readings at every level, so no existing defect brief changes
    meaning and no brief for work that is wanted has to guess.

    The control is the description this replaced, in which the same scan reports
    all three: it gave each level a defect reading and no other.
    """
    assert severity_levels_missing_a_reading(DESCRIPTIONS["severity"],
                                             SEVERITIES) == []
    assert severity_levels_missing_a_reading(
        SUPERSEDED_DESCRIPTIONS["severity"], SEVERITIES) \
        == sorted(str(level) for level in SEVERITIES)


def test_severity_still_says_it_is_payload_and_is_neither_effort_nor_confidence():
    """The two sentences the widening had to keep: what the field is not, and
    why it is outside the identity."""
    described = collapsed(DESCRIPTIONS["severity"]).lower()
    assert "neither effort nor confidence" in described
    assert "payload rather than identity" in described
    assert f"may not be {max(SEVERITIES)} unless its confidence is high" \
        in described


def test_confidence_asks_about_the_judgement_the_brief_makes():
    """No longer only whether the finding is real, and still a separate axis.

    The control is the description this replaced, which fails both halves of
    the first assertion: it named no judgement and no work.
    """
    described = collapsed(DESCRIPTIONS["confidence"]).lower()
    assert "judgement" in described
    assert any(word in described for word in WORK_WORDS)
    assert "separate from severity" in described

    superseded = collapsed(SUPERSEDED_CONFIDENCE).lower()
    assert "judgement" not in superseded
    assert not any(word in superseded for word in WORK_WORDS)


def test_the_category_description_says_what_each_appended_kind_is():
    described = collapsed(DESCRIPTIONS["category"])
    assert "kind of work" in described.lower()
    for category in APPENDED_CATEGORIES:
        assert category in described, category
    assert "identity" in described


def test_the_two_mechanical_paragraphs_are_preserved_in_substance():
    """Neither depends on the premise this story widened: paths are bare because
    the query searches for the marker of a bare path, and the identity is what
    it is because a title drifts."""
    described = collapsed(BRIEF_SCHEMA["description"]).lower()
    assert "bare repository-relative paths" in described
    assert "marker" in described
    assert "line number" in described
    assert "filed under" in described
    for member in IDENTITY_MEMBERS:
        assert member in described, member


# --------------------------------------------------------------------------
# The two shipped prompts
# --------------------------------------------------------------------------


def prompt_text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def inspector_prompt() -> str:
    return prompt_text(inspection.INSPECTOR_PROMPT)


def assist_prompt() -> str:
    return prompt_text("assist.md")


#: A bullet naming a category, as `prompts/inspector.md` writes one: the name
#: in lower case at the start of the line, then an em dash, then what it means.
CATEGORY_BULLET = re.compile(r"^- ([a-z][a-z-]*) — ", re.MULTILINE)


def categories_the_prompt_lists(text: str) -> list[str]:
    return CATEGORY_BULLET.findall(text)


def test_the_inspector_prompt_still_lists_its_ten_categories_as_it_spelled_them():
    """Order and spelling both, because the prompt's list is what the Inspector
    chooses from and the schema's enum is what the choice is validated against.

    The control is below: the same extraction over a copy of the prompt
    carrying a bullet for a category the schema does not accept reports it.
    """
    assert categories_the_prompt_lists(inspector_prompt()) \
        == list(CATEGORIES_BEFORE)


def test_the_inspector_prompt_lists_no_category_the_schema_does_not_accept():
    listed = categories_the_prompt_lists(inspector_prompt())
    assert set(listed) <= set(CATEGORIES), sorted(set(listed) - set(CATEGORIES))

    invented = "zzz-no-enum-carries-this-name"
    planted = inspector_prompt().replace(
        f"- {CATEGORIES_BEFORE[0]} — ",
        f"- {invented} — a kind of finding nothing declares.\n"
        f"- {CATEGORIES_BEFORE[0]} — ", 1)
    reported = categories_the_prompt_lists(planted)
    assert invented in reported
    assert not set(reported) <= set(CATEGORIES)


def test_the_inspector_prompt_lists_neither_category_this_story_appended():
    """Not an omission: the Inspector reads code and reports what is wrong with
    it, and neither a feature nobody built nor a rearrangement of what works is
    something it can find. The story leaves its behaviour alone.

    The control is the extraction above, shown reporting a bullet that is
    present; here the same extraction is silent about two that are not.
    """
    listed = categories_the_prompt_lists(inspector_prompt())
    for category in APPENDED_CATEGORIES:
        assert category not in listed, category


def test_the_inspector_prompt_keeps_its_two_mechanical_rules():
    described = collapsed(inspector_prompt()).lower()
    assert "file:line" in described
    assert f"may not be severity {max(SEVERITIES)} unless its confidence is high" \
        in described


def test_the_inspector_prompt_keeps_its_slug_derivation_rule():
    described = collapsed(inspector_prompt())
    assert "kebab-case" in described
    assert "Name the defect, not the fix and not the file" in described
    assert "hyphen-separated" in described
    assert "filed under" in described


def test_the_slug_sentence_is_scoped_to_the_briefs_the_inspector_writes():
    """One sentence, and the only change this story makes to that prompt.

    It read as a claim about every brief while the Inspector was the only
    producer of them. The control is the shipped prompt with the unscoped
    sentence restored in the hard-wrapped form the file actually carried it in,
    so what is shown is that this comparison finds the sentence as it was
    written, line break and all — the silence above is then a fact about the
    text rather than about a search looking for something it could never match.
    """
    text = inspector_prompt()
    assert "Each brief you write carries a `slug`" in collapsed(text)
    assert collapsed(SUPERSEDED_SLUG_OPENING_AS_WRAPPED) not in collapsed(text)

    restored = text.replace(
        "Each brief you write carries a `slug`: a short kebab-case name for the\n"
        "defect itself.", SUPERSEDED_SLUG_OPENING_AS_WRAPPED, 1)
    assert restored != text
    assert collapsed(SUPERSEDED_SLUG_OPENING_AS_WRAPPED) in collapsed(restored)


def test_the_assist_prompt_says_a_brief_may_state_any_of_the_three_kinds():
    """One sentence naming all three, rather than three words somewhere in the
    document: a prompt that mentions features in one paragraph and defects in
    another has not told its reader that a brief may be either."""
    sentences = collapsed(assist_prompt()).split(". ")
    naming_all_three = [
        sentence for sentence in sentences
        if "defect" in sentence
        and all(category in sentence for category in APPENDED_CATEGORIES)
    ]
    assert naming_all_three, sentences


def test_the_assist_prompt_restates_severity_and_confidence_as_the_schema_does():
    """Agreement rather than similarity: each phrase asserted of the prompt is
    asserted of the schema description beside it, so the two cannot drift apart
    without one of the pairs going red."""
    stated = collapsed(assist_prompt()).lower()
    severity = collapsed(DESCRIPTIONS["severity"]).lower()
    confidence = collapsed(DESCRIPTIONS["confidence"]).lower()

    assert "how much the work matters" in stated
    assert "how much this work matters" in severity

    assert "how sure you are of the judgement" in stated
    assert "how sure the writer is of the judgement" in confidence

    assert CONSEQUENCE_OF_INACTION not in stated


def test_the_assist_prompt_still_names_the_schema_and_the_evidentiary_standard():
    stated = collapsed(assist_prompt())
    assert f"schemas/{inspection.BRIEF_SCHEMA}.schema.json" in stated
    assert "file:line" in stated
    assert "mandate" in stated


# --------------------------------------------------------------------------
# What the prompts may not say
#
# Two absences, each over a vocabulary of things a prompt could have drifted
# into and the schema does not declare. The limit is stated rather than implied:
# a name nobody thought of is not caught, which is why the field scan is run
# beside the declared-field membership check above rather than instead of it.
# --------------------------------------------------------------------------

#: Names a brief might plausibly have carried and does not. Each is searched
#: for the way a prompt names a *field* — backticked, or introduced by an
#: article — so a prompt telling the Inspector not to score a priority is not
#: mistaken for one declaring a priority field.
FIELDS_THE_SCHEMA_DOES_NOT_DECLARE = (
    "priority", "impact", "urgency", "owner", "assignee", "estimate",
    "status", "labels", "reporter", "component",
)

#: Values for the schema's three enum-valued fields that it does not accept.
#: Searched as bare words, since a prompt states an enum member as one.
VALUES_THE_SCHEMA_DOES_NOT_ACCEPT = (
    "critical", "blocker", "major", "minor", "trivial", "urgent",
    "unsure", "XS", "XL",
)


def fields_named(text: str, vocabulary) -> list[str]:
    """Every name in `vocabulary` the text names the way it names a field."""
    stated = collapsed(text).lower()
    return sorted(
        name for name in vocabulary
        if re.search(rf"`{re.escape(name.lower())}`"
                     rf"|\b(?:a|an|the|its)\s+{re.escape(name.lower())}\b",
                     stated)
    )


def values_named(text: str, vocabulary) -> list[str]:
    stated = collapsed(text).lower()
    return sorted(value for value in vocabulary
                  if re.search(rf"\b{re.escape(value.lower())}\b", stated))


PROMPT_NAMES = pytest.mark.parametrize(
    "prompt", [pytest.param(inspector_prompt, id=inspection.INSPECTOR_PROMPT),
               pytest.param(assist_prompt, id="assist.md")])


@PROMPT_NAMES
def test_neither_prompt_names_a_brief_field_the_schema_does_not_declare(prompt):
    """The absence, with the same scan shown reporting every one of them when
    they are planted in a copy of this prompt — so a silence here is a fact
    about what the prompt says rather than about a search over a vocabulary
    this text could never have matched."""
    assert fields_named(prompt(), FIELDS_THE_SCHEMA_DOES_NOT_DECLARE) == []

    planted = prompt() + "\n\nA brief also carries " + ", ".join(
        f"a {name}" for name in FIELDS_THE_SCHEMA_DOES_NOT_DECLARE) + ".\n"
    assert fields_named(planted, FIELDS_THE_SCHEMA_DOES_NOT_DECLARE) \
        == sorted(FIELDS_THE_SCHEMA_DOES_NOT_DECLARE)


@PROMPT_NAMES
def test_neither_prompt_states_an_enum_value_the_schema_does_not_accept(prompt):
    assert values_named(prompt(), VALUES_THE_SCHEMA_DOES_NOT_ACCEPT) == []

    planted = prompt() + "\n\nRate it " + ", ".join(
        VALUES_THE_SCHEMA_DOES_NOT_ACCEPT) + ".\n"
    assert values_named(planted, VALUES_THE_SCHEMA_DOES_NOT_ACCEPT) \
        == sorted(VALUES_THE_SCHEMA_DOES_NOT_ACCEPT)


def test_the_shipped_schema_is_the_one_these_assertions_read():
    """The subject named once, so a reader can see that nothing above validated
    a brief against a shape this module wrote for itself."""
    shipped = (REPO_ROOT / "schemas"
               / f"{inspection.BRIEF_SCHEMA}.schema.json")
    assert shipped.is_file()
    assert json.loads(shipped.read_text(encoding="utf-8")) == BRIEF_SCHEMA
    assert inspection.BRIEF_SCHEMA in schema_validator.shipped_schemas()
