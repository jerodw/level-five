"""Independent validation for the story that gives a brief the area it concerns.

A brief said what kind of work it is and how much it matters and nothing about
which part of the system it is in, which is the axis a developer sorts a board
by. This story adds one optional free-text field carrying the area, named from
a vocabulary the *target* declares in its own standards directory — so the
harness gains a slot and not a taxonomy, on the terms `paths` is already
carried. Nothing coins a name: where a producer can name none it writes a
suggestion, and a suggestion is reported and filed by nothing.

Written from the acceptance criteria rather than from the implementation. The
subjects are kept apart deliberately:

  * **the two schemas.** `schemas/story-brief.schema.json` and
    `schemas/inspection-findings.schema.json` are live harness artifacts and
    are the subject of the assertions that name them: what is claimed is what
    this harness declares a brief and an envelope to be. Each is driven
    through the harness's own validator rather than eyeballed.

  * **the filing module this story does not touch.** The claim that
    `orchestration/story_brief.py` is unchanged is resolved through the shared
    story-range comparison in `conftest`, never through `HEAD`. Beside it, the
    two halves of *why* it needed no change, asserted on the shipped functions:
    `payload` spreads the brief so an area reaches the tracker payload with no
    line of code, and `identity` names its members so two briefs differing only
    in their area are one piece of work.

  * **what an envelope carries into a report.** Real inspections of a target
    this module builds under a temporary directory, against a fake runner that
    writes the envelope — so a suggestion reaching `Report` and an area
    reaching `Filed` are read off the code that reads envelopes rather than off
    a constructed report.

  * **the two report surfaces.** The post-story report is read out of a whole
    run's events.log, where the ordering claim — a line of its own *before* the
    summary — can be made at all. The broad inspection's report is read through
    `scripts/l5-inspect` itself, against reports this module constructs,
    because what is under test there is the words that script prints and a
    report is the input it prints them from.

  * **the two producers' prose.** `prompts/inspector.md` and
    `plugin/skills/file-a-brief/SKILL.md` are read and searched, never
    eyeballed.

  * **the board.** Both copies of the sync script are run end to end against
    the stub `gh` that `tests/test_filed_query.py` wrote, on a board this
    module gives an Area column to — so "writes through the existing mechanism
    and adds no new failure path" is watched happening rather than read off the
    source.

Every absence asserted here carries a demonstration that it can fail:

  * "the area is absent from the brief schema's required list" sits beside the
    same validator against the same schema with the area appended to that list,
    which reports the same brief;
  * "the suggestions are optional on the envelope" sits beside the same
    declaration with them required, which the same validator reports;
  * "the shipped envelope accepts an unusable suggestion" sits beside the same
    validator against the same envelope with the findings absent and with the
    findings not an array, both of which it reports — so what was relaxed is
    the suggestions declaration and not the validator;
  * "an inspection whose envelope carries an unusable suggestion records no
    drop naming the scope as having produced no usable artifact" sits beside
    the same inspection of the same fixture over an envelope whose findings are
    absent and whose findings are not an array, both of which do record one;
  * "an unusable suggestion is printed nowhere and reaches no queue entry" sits
    beside the filed finding's own slug, which the identical reads of the
    identical report and queue do find;
  * "the filter's docstring no longer concedes that the envelope schema
    refused everything first" sits beside that same docstring with the
    conceding sentence spliced back onto it, which the identical search finds;
  * "this story left `orchestration/story_brief.py` alone" sits beside two
    synthetic histories built by `conftest.constructed_story`, one whose story
    respects that path and one whose story edits it, which the identical call
    tells apart;
  * "an inspection where every filed brief named an area writes no line about
    areas" sits beside the same run over a brief that named none, which writes
    one;
  * "a suggestion for a finding that was dropped is printed by neither surface"
    sits beside the suggestion for the brief that *was* filed, which both
    surfaces print, so a surface that had stopped printing anything could not
    pass;
  * "a target declaring no area vocabulary is reported exactly as before" is
    made by comparing the whole broad report against the same report with every
    filed brief carrying an area, which Section F shows differ.

One reading is written down here rather than left implicit, because two
criteria pull against each other and the constraints decide which wins: an
area-less filing is reported **where that inspection shows the target declares
a vocabulary at all**, which is either a suggestion the Inspector wrote or a
sibling filing that named an area. The harness may read no target's vocabulary,
so those are the only things that tell a target which declared one from a target
which declared none — and a line per area-less filing regardless would put a
line on every brief every target that never opted in ever files, which the
criterion that such a target is reported exactly as it is today forbids. The
tests that say so carry the argument beside their assertions.

`Report.unnamed_areas` in `orchestration/inspection.py` is where that gate
lives, and both report surfaces derive their lines from it, so the rule is one
answer rather than one per surface.

Nothing here reaches a model: the fake runner every inspection is driven
against is a subclass of `tests/test_an_inspection_records_what_it_cost.py`'s,
whose autouse guard fails any test in this module that reaches the real one.
Nothing here reaches a tracker either: the filed-query command is a file this
module writes, and `gh` is the stub first on PATH.
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import re
from pathlib import Path

import pytest

import conftest
import filed_query
import inspection
import schema_validator
import story_brief
import story_inspection
import agent_runner
import harness_config
import outbox

from test_an_inspection_records_what_it_cost import (  # noqa: F401 - shared
    Inspector,                                        # idioms and fixtures
    ROOMY_CAP,
    Runner,
    build_target,
    finding,
    harness,
    messages,
    no_model,
    run,
)
from test_a_failed_dedupe_is_visible import (  # noqa: F401 - the same run's
    ANSWERS,                                   # events.log, read the one way
    QUERY_COMMAND,
    inspection_lines,
    notes_in,
    summary_of,
)
from test_filed_query import (  # noqa: F401 - the stub tracker and the pair
    BOTH_SYNC_COPIES,
    CLASSIFICATION,
    INSTALLED_SCRIPT,
    TEMPLATE_CONSTANTS,
    a_brief_carrying,
    a_filed_brief,
    board_field_value,
    board_items,
    needs_jq,
    rewrite_the_board,
    stub_tracker,
    sync_to_the_board,
    this_targets,
)

# --------------------------------------------------------------------------
# What this module names, and what it derives
#
# The two field names are read off the modules that define them rather than
# spelled here, so a rename is a resolution that fails rather than an assertion
# that quietly stops asserting. The area *values* are this module's own and
# have to be: an area vocabulary is the target's document, so there is nothing
# in this harness to derive one from — which is the whole claim.
# --------------------------------------------------------------------------

#: The slot on a brief, spelled once. It is derived from nothing because the
#: whole claim of the first section is that the schema declares this name.
AREA = "area"

#: The sibling of `findings`, read off the module that reads envelopes.
SUGGESTIONS = inspection.AREA_SUGGESTIONS

#: Two names out of a vocabulary no harness holds. They are prose because a
#: target's areas are prose, and they are two so that a brief carrying one is
#: carrying a choice rather than the only value there was.
AN_AREA = "the filing path"
ANOTHER_AREA = "the report surfaces"

#: What an Inspector says about a brief it could name no area for. Distinct
#: sentences, so a line carrying one is traceable to the suggestion that
#: produced it rather than to anything the harness supplied.
CONCERNS = "how a run decides it may stop, which no declared area covers"
CONCERNS_OF_THE_DROPPED = "something nobody filed, so nobody should read this"

BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
ENVELOPE_SCHEMA = schema_validator.load_schema(inspection.FINDINGS_SCHEMA)

REPO_ROOT = Path(conftest.HARNESS_ROOT)
STORY_BRIEF_REL = "orchestration/story_brief.py"


def flattened(text: str) -> str:
    """One text with its wrapping and its markdown emphasis taken out.

    A prompt and a skill are wrapped for a reader and emphasised for one, so a
    sentence in either is neither a line nor a plain string. Searching the
    flattened, unemphasised, lowered text asks about the claim rather than
    about where the line broke or which words were bolded.
    """
    return " ".join(text.replace("*", "").replace("`", "").split()).lower()


def said_of(schema: dict, field: str) -> str:
    """One declared property's description, flattened for searching."""
    return flattened(schema["properties"][field]["description"])


# ==========================================================================
# The brief schema: one optional free-text slot
# ==========================================================================


def test_the_brief_schema_declares_the_area_optional_and_free_text():
    """A shipped schema and the legitimate subject: the claim is about what
    this harness declares a brief to be.

    Free text is asserted as the absence of anything that would resolve it —
    no enum, no pattern, no format — because the constraint the story turns on
    is that the harness holds no vocabulary and can therefore check a name
    against nothing. The description is required to say the two things a reader
    cannot get from the value: whose knowledge the vocabulary is, and that this
    is payload rather than identity.
    """
    declared = BRIEF_SCHEMA["properties"][AREA]

    assert declared["type"] == "string"
    for resolving in ("enum", "pattern", "format", "const"):
        assert resolving not in declared, resolving
    assert AREA not in BRIEF_SCHEMA["required"]

    said = said_of(BRIEF_SCHEMA, AREA)
    assert "held by the harness and resolved by nothing" in said, said
    assert "the target's knowledge" in said, said
    assert "naming none is a first-class answer" in said, said
    assert "payload rather than identity" in said, said


def test_a_brief_with_no_area_and_a_brief_carrying_one_both_validate():
    """Both halves, through the harness's own validator.

    The brief is the one the fixtures build, so what differs between the two
    calls is the area and nothing else.
    """
    assert schema_validator.validate(finding(), BRIEF_SCHEMA) == []
    assert schema_validator.validate(finding(area=AN_AREA), BRIEF_SCHEMA) == []


def test_the_same_validator_reports_the_brief_once_the_area_is_required():
    """The control for the absence above.

    "Absent from the required list" passes just as happily when the validator
    is not reading the required list at all, or when the schema resolved to
    something with no required list to read. Here the identical call is made
    against the identical schema with the area appended to that list, where the
    brief that has no area is reported by name and the brief that has one is
    still accepted — so the emptiness above is the declaration and not the
    check.
    """
    demanding = {**BRIEF_SCHEMA,
                 "required": [*BRIEF_SCHEMA["required"], AREA]}

    reported = schema_validator.validate(finding(), demanding)
    assert reported, "the validator accepts a brief missing a required field"
    assert any(AREA in problem for problem in reported), reported
    assert schema_validator.validate(finding(area=AN_AREA), demanding) == []


# ==========================================================================
# The filing module this story does not touch, and both halves of why
# ==========================================================================


def test_this_story_left_the_filing_module_alone():
    """Resolved through the shared story range rather than against HEAD.

    A comparison against HEAD goes vacuously green the moment the coordinator
    commits the working tree, which is exactly when this assertion would stop
    saying anything. Its control is the pair of synthetic histories below.
    """
    assert conftest.story_diff([STORY_BRIEF_REL],
                               validation_file=Path(__file__)) == ""


def test_the_same_comparison_tells_an_edited_module_apart(tmp_path):
    """The control for the absence above, over histories built here.

    One story respects the path and one edits it; the identical call is empty
    for the first and non-empty for the second, so an empty answer above is the
    story having left it alone rather than a range bounded at the wrong
    commits.
    """
    respecting = conftest.constructed_story(tmp_path,
                                            respected=[STORY_BRIEF_REL],
                                            name="filing-module-left-alone")
    assert conftest.constructed_story_diff(respecting, [STORY_BRIEF_REL]) == ""

    violating = conftest.constructed_story(tmp_path,
                                           violated=[STORY_BRIEF_REL],
                                           name="filing-module-edited")
    assert conftest.constructed_story_diff(violating, [STORY_BRIEF_REL]) != ""


def test_the_payload_of_a_brief_carrying_an_area_carries_that_area():
    """The first half of why no line of that module had to change: `payload`
    spreads the brief, so a field a producer supplies reaches the tracker
    payload without being named anywhere.

    The brief that names none is beside it, so what the payload carries is the
    brief's own value rather than a key written on every payload.
    """
    assert story_brief.payload(finding(area=AN_AREA))[AREA] == AN_AREA
    assert AREA not in story_brief.payload(finding())


def test_two_briefs_differing_only_in_their_area_are_one_identity():
    """The second half: the area is payload and may not become part of what a
    brief is filed under.

    A growing vocabulary re-sorts areas, so an identity carrying the area would
    refile every brief whose area moved, against the same piece of work. The
    three briefs here differ in that field alone — one naming none, and two
    naming different names — and derive one identity, which is also the
    demonstration that the identity does not carry the field at all.
    """
    without = story_brief.identity(finding())
    one = story_brief.identity(finding(area=AN_AREA))
    another = story_brief.identity(finding(area=ANOTHER_AREA))

    assert without == one == another
    assert AREA not in one

    # The control for that absence: the identity does move when something it
    # *is* made of moves, so the equality above is the area being excluded
    # rather than the derivation having stopped reading its input.
    assert story_brief.identity(finding(2)) != without


# ==========================================================================
# The envelope schema: an optional sibling of the findings
# ==========================================================================


def a_suggestion(slug: str, concerns: str = CONCERNS) -> dict:
    return {"slug": slug, "concerns": concerns}


def an_envelope(*found: dict, suggestions=()) -> dict:
    envelope = {"findings": list(found)}
    if suggestions:
        envelope[SUGGESTIONS] = list(suggestions)
    return envelope


def test_the_envelope_declares_the_suggestions_by_their_description_alone():
    """A shipped schema and the subject: what an inspection may write.

    The field is declared and held to nothing — no type of its own and no item
    declaration — because it is read for the report and filed by nothing, so a
    slip in one entry may not cost a whole scope the findings beside it. What
    the removed declarations said is required to be said in the description
    instead: the shape expected of an entry, and that an entry failing it
    yields nothing of its own and costs the entries beside it nothing.
    """
    declared = ENVELOPE_SCHEMA["properties"][SUGGESTIONS]

    assert set(declared) == {"description"}, declared
    assert SUGGESTIONS not in ENVELOPE_SCHEMA.get("required", [])

    said = said_of(ENVELOPE_SCHEMA, SUGGESTIONS)
    assert "read for the report and filed by nothing" in said, said
    assert "slug" in said and "concerns" in said, said
    assert "holds the field to none of that" in said, said
    assert "cost the entries beside them nothing" in said, said


def test_an_envelope_carrying_only_findings_still_validates():
    """What every inspection wrote before this existed, and what an inspection
    of a target declaring no areas goes on writing."""
    assert schema_validator.validate(an_envelope(), ENVELOPE_SCHEMA) == []
    assert schema_validator.validate(an_envelope(finding()),
                                     ENVELOPE_SCHEMA) == []


def test_an_envelope_carrying_suggestions_validates_too():
    envelope = an_envelope(
        finding(), suggestions=[a_suggestion(finding()["slug"])])
    assert schema_validator.validate(envelope, ENVELOPE_SCHEMA) == []


def test_the_same_declaration_reports_an_envelope_missing_the_sibling():
    """The control for "optional".

    It makes the same declaration demand the sibling, where the findings-only
    envelope above is reported — so that envelope's acceptance is the
    declaration and not a validator that reads no required list.
    """
    demanding = {**ENVELOPE_SCHEMA,
                 "required": [*ENVELOPE_SCHEMA.get("required", []),
                              SUGGESTIONS]}
    reported = schema_validator.validate(an_envelope(finding()), demanding)
    assert reported, "the validator accepts an envelope missing the sibling"
    assert any(SUGGESTIONS in problem for problem in reported), reported


A_SLUG = finding()["slug"]

UNUSABLE_SUGGESTIONS = {
    "an entry with no concerns key": [{"slug": A_SLUG}],
    "an entry with no slug key": [{"concerns": CONCERNS}],
    "an entry whose concerns is null": [{"slug": A_SLUG, "concerns": None}],
    "an entry that is a string rather than an object": ["inspection-and-briefs"],
    "a field that is not a list at all": "inspection-and-briefs",
}


@pytest.mark.parametrize("described", sorted(UNUSABLE_SUGGESTIONS))
def test_an_unusable_suggestion_satisfies_the_shipped_envelope(described):
    """The new rule, where the item declaration used to be.

    An entry the reader will refuse is not an envelope the validator refuses:
    the field carries a shape the reader judges, so a slip in one entry — a
    missing, misspelled or null `concerns` is the ordinary case, the producer
    being a model asked to write one suggestion per brief it named no area for
    — costs itself and leaves the findings beside it filed. The field not being
    a list at all is as unusable as an entry that is not a pair, and neither
    may stop an inspection.
    """
    envelope = {"findings": [finding()],
                SUGGESTIONS: UNUSABLE_SUGGESTIONS[described]}
    assert schema_validator.validate(envelope, ENVELOPE_SCHEMA) == []


def test_the_same_validator_still_reports_a_malformed_findings_array():
    """The control for the acceptances above, and the story's own boundary.

    "The schema accepts an unusable suggestion" passes just as happily against
    a validator that has stopped reading `required` and `items` at all. Here
    the identical call is made against the same shipped envelope with the
    findings absent and with the findings not an array, both of which it
    reports — so what was relaxed is the suggestions declaration and not the
    validator's handling of items or of required properties.
    """
    absent = schema_validator.validate(
        {SUGGESTIONS: [a_suggestion(A_SLUG)]}, ENVELOPE_SCHEMA)
    assert absent, "an envelope with no findings at all is accepted"
    assert any("findings" in problem for problem in absent), absent

    not_an_array = schema_validator.validate(
        {"findings": {"slug": A_SLUG}}, ENVELOPE_SCHEMA)
    assert not_an_array, "a findings value that is not an array is accepted"
    assert any("findings" in problem for problem in not_an_array), not_an_array


# ==========================================================================
# Driving a real inspection: the fake that writes the envelope
# ==========================================================================


class Inspecting(Inspector):
    """The shared fake, writing the suggestions beside the findings.

    The envelope is the one input this story added, so it is written by the
    invocation rather than planted underneath one: what `inspect_scope` reads
    is a file the fake wrote, exactly as a real invocation's would be.
    """

    def __init__(self, *args, suggestions=(), **keywords):
        super().__init__(*args, **keywords)
        self.suggestions = [dict(one) for one in suggestions]

    def __call__(self, *args, **keywords):
        result = super().__call__(*args, **keywords)
        if self.suggestions:
            document = json.loads(self.artifact.read_text(encoding="utf-8"))
            document[SUGGESTIONS] = self.suggestions
            self.artifact.write_text(json.dumps(document), encoding="utf-8")
        return result


def a_target(tmp_path: Path, name: str, **config_keys) -> Path:
    """A target whose inspections ask a query that answers.

    The query is configured for the reason the dedupe module configures one:
    an inspection whose filed query could not answer writes a note of its own,
    and this module's claims are about which *other* notes are written. The
    command is written before the target is built, so it is committed with
    everything else and is there when the run reaches it.
    """
    root = tmp_path / name
    script = root / QUERY_COMMAND
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(ANSWERS, encoding="utf-8")
    script.chmod(0o755)
    config_keys.setdefault(story_inspection.MAX_FILES_KEY, ROOMY_CAP)
    return build_target(root, **{filed_query.COMMAND_KEY: QUERY_COMMAND},
                        **config_keys)


def narrow_run_with(target: Path, harness_root: Path, monkeypatch, *,
                    findings=(), suggestions=()):
    """One completing run of the fixture, with the fake installed."""
    config = harness_config.load_config(target)
    inspector = Inspecting(target, config, findings=list(findings),
                           suggestions=suggestions)
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    return run(target, harness_root, Runner(target)), inspector


def broad_with(target: Path, harness_root: Path, *,
               findings=(), suggestions=()):
    """One whole broad inspection, against the same fake.

    The runner is passed explicitly rather than defaulted, so nothing here can
    fall through to the real one.
    """
    config = harness_config.load_config(target)
    inspector = Inspecting(target, config, findings=list(findings),
                           suggestions=suggestions)
    return inspection.inspect(target, config, harness_root,
                              runner=inspector), inspector


def filed_areas(report) -> dict:
    """What each filed brief's record says its area is, by slug."""
    return {one.slug: one.area for one in report.filed}


# ==========================================================================
# What an envelope carries into a report
# ==========================================================================


def test_the_suggestions_an_envelope_carries_reach_the_report(tmp_path,
                                                              harness):
    """Read off the envelope where the findings are read, and carried whole.

    Both members are asserted, because a suggestion that reached the report
    having lost the words it was written in is a line a person cannot act on.
    """
    target = a_target(tmp_path, "suggestions-reach-the-report")
    report, inspector = broad_with(
        target, harness, findings=[finding(1)],
        suggestions=[a_suggestion(finding(1)["slug"])])

    assert inspector.invocations, "the inspection was never attempted"
    assert report.area_suggestions, "the envelope's suggestions reached nothing"
    for one in report.area_suggestions:
        assert one.slug == finding(1)["slug"]
        assert one.concerns == CONCERNS


def test_an_inspection_whose_envelope_offered_none_carries_none(tmp_path,
                                                                harness):
    """The control for the reading above: the same inspection, an envelope
    written without the sibling, which is every envelope written before this
    story and every envelope a target declaring no areas produces."""
    target = a_target(tmp_path, "no-suggestions-in-the-envelope")
    report, inspector = broad_with(target, harness, findings=[finding(1)])

    assert inspector.invocations, "the inspection was never attempted"
    assert report.area_suggestions == ()


def test_a_filed_briefs_record_carries_the_area_that_brief_named(tmp_path,
                                                                 harness):
    """One inspection, two briefs, one of which named an area.

    They differ in that field alone, so what a record carries came from the
    brief rather than from a value written onto every record. The brief that
    named none carries the empty string, which is what makes an area-less
    filing reportable at all.
    """
    target = a_target(tmp_path, "filed-records-carry-the-area")
    report, inspector = broad_with(
        target, harness, findings=[finding(1, area=AN_AREA), finding(2)])

    assert inspector.invocations, "the inspection was never attempted"
    areas = filed_areas(report)
    assert areas[finding(1)["slug"]] == AN_AREA, areas
    assert areas[finding(2)["slug"]] == "", areas


# ==========================================================================
# One unusable suggestion costs itself and leaves the findings beside it filed
#
# The schema section above says the relaxed envelope accepts such a suggestion.
# What that buys is only visible through the code that reads an envelope: an
# entry the validator used to refuse cost `_read_findings` its whole document,
# which `inspect_scope` turned into one "no findings artifact" drop before the
# findings loop ran, so every brief the invocation actually wrote was gone and
# the spend was paid for and discarded. These drive real inspections against an
# invocation that writes the envelope, on both surfaces that read one.
# ==========================================================================


#: An envelope key to be taken off rather than rewritten, so a planted envelope
#: can say "no findings at all" as well as "findings of the wrong shape".
REMOVED = object()


class Planting(Inspecting):
    """The same fake, writing one envelope of this module's own making.

    `Inspecting` coerces each suggestion to a dict, which is exactly what an
    unusable entry is not, so an envelope here is written key by key and
    verbatim: a suggestion that is a bare string, a field that is not a list at
    all, findings that are not an array. It is still the invocation that writes
    it, so what the reader reads is a file an inspection produced rather than
    one planted underneath it.
    """

    def __init__(self, *args, envelope=(), **keywords):
        super().__init__(*args, **keywords)
        self.envelope = dict(envelope)

    def __call__(self, *args, **keywords):
        result = super().__call__(*args, **keywords)
        document = json.loads(self.artifact.read_text(encoding="utf-8"))
        for key, value in self.envelope.items():
            if value is REMOVED:
                document.pop(key, None)
            else:
                document[key] = value
        self.artifact.write_text(json.dumps(document), encoding="utf-8")
        return result


def broad_planting(target: Path, harness_root: Path, *,
                   findings=(), envelope=()):
    """One whole broad inspection whose invocation writes that envelope."""
    config = harness_config.load_config(target)
    inspector = Planting(target, config, findings=list(findings),
                         envelope=envelope)
    return inspection.inspect(target, config, harness_root,
                              runner=inspector), inspector


def narrow_planting(target: Path, harness_root: Path, monkeypatch, *,
                    findings=(), envelope=()):
    """One completing run whose post-story inspection reads that envelope."""
    config = harness_config.load_config(target)
    inspector = Planting(target, config, findings=list(findings),
                         envelope=envelope)
    monkeypatch.setattr(agent_runner, "run_agent", inspector)
    return run(target, harness_root, Runner(target)), inspector


def filed_slugs(report) -> set:
    return {one.slug for one in report.filed}


def as_a_path(described: str) -> str:
    """One case's description as a directory name, so a failure names itself."""
    return re.sub(r"[^a-z0-9]+", "-", described.lower()).strip("-")


#: The two ways an envelope is genuinely unreadable, which is what the
#: relaxation deliberately did not touch. They are the control for every
#: "records no such drop" below: a run that had stopped recording drops at all,
#: or a reader looking at the wrong report, would pass those assertions just as
#: happily, and fails these.
UNREADABLE_ENVELOPES = {
    "findings absent altogether": {"findings": REMOVED},
    "findings that are not an array": {"findings": {"slug": A_SLUG}},
}


@pytest.mark.parametrize("described", sorted(UNUSABLE_SUGGESTIONS))
def test_an_inspection_files_the_finding_beside_an_unusable_suggestion(
        described, tmp_path, harness):
    """The regression this story exists for, driven rather than reasoned about.

    One well-formed finding is written beside a suggestion the reader will
    refuse, and the claim is that the finding is filed — named, so a report
    that filed something else could not pass — and that nothing reports the
    scope as having produced no usable artifact. The suggestions the report
    carries are empty because the entry yielded nothing of its own, and the
    control for that emptiness is
    `test_the_suggestions_an_envelope_carries_reach_the_report`, where the same
    reading of the same fixture carries a well-formed one.
    """
    target = a_target(tmp_path, f"unusable-{as_a_path(described)}")
    report, inspector = broad_planting(
        target, harness, findings=[finding(1)],
        envelope={SUGGESTIONS: UNUSABLE_SUGGESTIONS[described]})

    assert inspector.invocations, "the inspection was never attempted"
    assert filed_slugs(report) == {finding(1)["slug"]}, report.dropped
    assert report.dropped_for(inspection.NO_ARTIFACT) == (), report.dropped
    assert report.area_suggestions == ()


@pytest.mark.parametrize("described", sorted(UNREADABLE_ENVELOPES))
def test_the_same_inspection_still_reports_an_envelope_it_cannot_read(
        described, tmp_path, harness):
    """The control for the absence above, and the story's boundary in the code.

    "No drop naming the scope as having produced no usable artifact" passes
    just as happily against an inspection that stopped recording drops, or
    against a reader asking a report that never ran. Here the identical drive
    of the identical fixture is given an envelope whose findings are absent and
    one whose findings are not an array, and both are reported by that exact
    reason with nothing filed — so what the relaxation reached is the
    suggestions declaration and not the validator or the drop.
    """
    target = a_target(tmp_path, f"unreadable-{as_a_path(described)}")
    report, inspector = broad_planting(
        target, harness, findings=[finding(1)],
        envelope=UNREADABLE_ENVELOPES[described])

    assert inspector.invocations, "the inspection was never attempted"
    assert report.filed == ()
    reported = report.dropped_for(inspection.NO_ARTIFACT)
    assert reported, report.dropped


def test_a_post_story_inspection_files_it_too_and_says_nothing_was_missing(
        tmp_path, harness, monkeypatch):
    """The narrow path, where the same envelope problem cost a whole story's
    inspection its findings.

    The summary is the line that carries the counts and every way the
    inspection dropped something, so it is where both halves are read: one
    finding found and one filed, and the missing-artifact reason not named on
    it. The control for that absence is the test below, where the same summary
    of the same fixture does name it.
    """
    target = a_target(tmp_path, "a-story-with-an-unusable-suggestion")
    code, inspector = narrow_planting(
        target, harness, monkeypatch, findings=[finding(1)],
        envelope={SUGGESTIONS:
                  UNUSABLE_SUGGESTIONS["an entry with no concerns key"]})

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    said = summary_of(target)
    assert "1 finding(s), 1 filed" in said, said
    assert inspection.NO_ARTIFACT not in said, said


@pytest.mark.parametrize("described", sorted(UNREADABLE_ENVELOPES))
def test_the_same_summary_names_the_missing_artifact_when_it_is_missing(
        described, tmp_path, harness, monkeypatch):
    """The control for the absence read off that summary.

    The identical run of the identical fixture, given an envelope no reader can
    make findings out of, names the reason on the same line and files nothing —
    so the line above is the drop not having happened rather than a summary
    that stopped saying what it dropped.
    """
    target = a_target(tmp_path, f"a-story-with-{as_a_path(described)}")
    code, inspector = narrow_planting(
        target, harness, monkeypatch, findings=[finding(1)],
        envelope=UNREADABLE_ENVELOPES[described])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    said = summary_of(target)
    assert inspection.NO_ARTIFACT in said, said
    assert "0 filed" in said, said


#: Words that exist only inside an unusable entry, so finding them anywhere is
#: that entry having been read as usable. The entry is unusable because its
#: `concerns` is not a string, which is the shape `_area_suggestions` refuses
#: while leaving the words on disk for a search to find.
UNUSABLE_WORDS = "this entry was never a pair and must reach nothing"

AN_UNUSABLE_PAIR = {"slug": A_SLUG, "concerns": {"words": UNUSABLE_WORDS}}


def queued_text(target: Path) -> str:
    """Every entry this inspection left in the queue, as one text.

    The queue entry is what a brief, its payload, its outbox entry and its
    tracker item are all made from downstream — nothing else is written and
    nothing is sent from anywhere else — so words absent from every byte of it
    reach none of them.
    """
    queue = outbox.queue_dir(target)
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(queue.rglob("*")) if path.is_file())


def test_an_unusable_suggestion_reaches_no_queue_entry_and_no_report(
        tmp_path, harness):
    """What an unusable entry costs beside the finding: nothing, and nothing of
    its own either.

    Both absences are read beside the filed finding's own slug, which the
    identical read of the identical report and the identical read of the
    identical queue do find — so neither absence can be satisfied by a report
    that printed nothing, a queue this read cannot see, or a slug resolved to
    something that appears in neither.
    """
    target = a_target(tmp_path, "an-unusable-suggestion-yields-nothing")
    report, inspector = broad_planting(
        target, harness, findings=[finding(1)],
        envelope={SUGGESTIONS: [AN_UNUSABLE_PAIR]})

    assert inspector.invocations, "the inspection was never attempted"
    assert report.area_suggestions == ()

    text, code = printed(report)
    assert code == 0
    assert finding(1)["slug"] in text, text
    assert UNUSABLE_WORDS not in text, text

    queued = queued_text(target)
    assert finding(1)["slug"] in queued, queued
    assert UNUSABLE_WORDS not in queued, queued


THE_CONCESSION = "the envelope schema has already been satisfied"


def test_the_filters_docstring_no_longer_concedes_the_schema_refused_first():
    """The sentence that made the tolerance unreachable, gone from the filter
    that is now the only thing judging a suggestion.

    A reader who believes the envelope refused these cases already has no
    reason to keep the guards, and that belief is what this docstring used to
    state. The absence is controlled by splicing the conceding sentence back
    onto the shipped docstring, where the identical search finds it — so the
    emptiness above is the words having changed rather than the search looking
    at nothing.
    """
    said = flattened(inspection._area_suggestions.__doc__)

    assert THE_CONCESSION not in said, said
    assert "where a suggestion is judged" in said, said
    assert "the only thing that judges one" in said, said
    assert "deliberate" in said, said

    spliced = flattened(f"{inspection._area_suggestions.__doc__}\n"
                        f"The envelope schema has already been satisfied by "
                        f"the time this is reached.")
    assert THE_CONCESSION in spliced, spliced


# ==========================================================================
# The post-story report: a line of its own, before the summary
# ==========================================================================


def lines_about(target: Path, slug: str) -> list[str]:
    """Every line of this run's inspection report that names one brief and is
    not the summary."""
    summary = summary_of(target)
    return [line for line in inspection_lines(target)
            if slug in line and line != summary]


def test_an_area_less_filed_brief_gets_a_line_of_its_own_before_the_summary(
        tmp_path, harness, monkeypatch):
    """The line this story exists to add, in the run's events.log.

    A trailing clause on the summary is what a failed dedupe had, and it was
    true on every story from 101 to 130 without being read; so the claims here
    are that the line is its own line, that it names the brief, that it carries
    the Inspector's words for it, and that it is said *before* the summary
    rather than after it.
    """
    target = a_target(tmp_path, "one-brief-with-no-area")
    code, inspector = narrow_run_with(
        target, harness, monkeypatch, findings=[finding(1)],
        suggestions=[a_suggestion(finding(1)["slug"])])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    said = lines_about(target, finding(1)["slug"])
    assert len(said) == 1, inspection_lines(target)
    assert CONCERNS in said[0], said[0]

    ordered = inspection_lines(target)
    assert ordered.index(said[0]) < ordered.index(summary_of(target)), ordered


def test_an_area_less_brief_the_envelope_offered_nothing_for_is_not_reported(
        tmp_path, harness, monkeypatch):
    """The one place two criteria pull against each other, resolved the way the
    story's constraints decide it.

    Read alone, "an inspection that files a brief with no area writes a line of
    its own about it" would hold for every area-less filing. But the only thing
    the harness can tell a target that declares a vocabulary from one that
    declares none by is what the Inspector wrote: the harness may hold, resolve
    and read no target's vocabulary, so it never sees the document, and a target
    that declares none files every brief with no area. A line per area-less
    filing is therefore a line on every brief every target that never opted in
    ever files, which the criterion that such a target is reported exactly as it
    is today forbids, and which would also start writing lines into the reports
    of targets this suite already holds still.

    So the gate is evidence of a vocabulary in the inspection itself, and a
    suggestion is one of the two things that carry it: where the Inspector was
    handed a vocabulary and named no area it writes a suggestion, and where
    there was none to name from it writes neither an area nor a suggestion and
    nothing is said. The other is a sibling filing that named an area, which
    `test_an_area_less_brief_beside_one_that_named_an_area_is_reported` holds.
    The control for this absence is
    `test_an_area_less_filed_brief_gets_a_line_of_its_own_before_the_summary`,
    which is this run with a suggestion added and nothing else changed, and
    which does report a line.
    """
    target = a_target(tmp_path, "no-area-and-no-suggestion")
    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[finding(1)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert notes_in(target) == [], inspection_lines(target)
    assert "1 finding(s)" in summary_of(target)


def test_an_area_less_brief_beside_one_that_named_an_area_is_reported(
        tmp_path, harness, monkeypatch):
    """The other half of the gate, so the rule above is as narrow as it claims.

    A sibling filing that named an area is the second thing an inspection of a
    target declaring no vocabulary cannot have, and it is evidence of a
    vocabulary as good as a suggestion: the Inspector named one of its names. So
    the area-less brief here is reported on the criterion's own terms — named,
    on a line of its own, before the summary — with no suggestion to carry,
    which is what distinguishes this run from
    `test_an_area_less_brief_the_envelope_offered_nothing_for_is_not_reported`.
    The brief that named an area must not be among the lines, or the line would
    be about filings rather than about missing areas.
    """
    target = a_target(tmp_path, "one-named-and-one-not")
    named, unnamed = finding(1, area=AN_AREA), finding(2)
    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[named, unnamed])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    said = lines_about(target, unnamed["slug"])
    assert len(said) == 1, inspection_lines(target)
    assert lines_about(target, named["slug"]) == [], inspection_lines(target)

    ordered = inspection_lines(target)
    assert ordered.index(said[0]) < ordered.index(summary_of(target)), ordered


def test_an_inspection_where_every_filed_brief_named_an_area_says_nothing(
        tmp_path, harness, monkeypatch):
    """The control for the absence the two assertions above rest on.

    The same fixture, the same run, the same query that answers — and one
    brief that named an area. Without it, "a line of its own" would be
    satisfied by a run that says nothing at all, and could not tell a brief
    that named an area from a reader looking in the wrong file. The summary is
    required to still be there and to still carry its counts, so what changed
    is the extra line and not the report.
    """
    target = a_target(tmp_path, "every-brief-named-an-area")
    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[finding(1, area=AN_AREA)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert notes_in(target) == []
    assert "1 finding(s)" in summary_of(target)


def test_a_suggestion_for_a_dropped_finding_is_not_printed_after_a_story(
        tmp_path, harness, monkeypatch):
    """Matched to a filing by slug, so a suggestion about work nobody filed is
    a line about nothing and is not written.

    The severity floor does the dropping, which is the harness's own way of
    not filing a finding rather than one this module invented. The brief that
    *was* filed is required to still get its line, so a surface that had
    stopped printing anything at all could not pass this.
    """
    target = a_target(tmp_path, "a-suggestion-for-a-dropped-finding",
                      **{inspection.MIN_SEVERITY_KEY: "3"})
    kept, dropped = finding(1, severity=3), finding(2, severity=1)
    code, inspector = narrow_run_with(
        target, harness, monkeypatch, findings=[kept, dropped],
        suggestions=[a_suggestion(kept["slug"]),
                     a_suggestion(dropped["slug"], CONCERNS_OF_THE_DROPPED)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"

    said = lines_about(target, kept["slug"])
    assert len(said) == 1, inspection_lines(target)
    assert CONCERNS in said[0], said[0]

    whole = "\n".join(inspection_lines(target))
    assert CONCERNS_OF_THE_DROPPED not in whole, whole


# ==========================================================================
# The broad inspection's report, read through scripts/l5-inspect itself
# ==========================================================================


def a_filed(slug: str, area: str = "") -> inspection.Filed:
    """One filing, as a report carries it."""
    return inspection.Filed(key=f"key-{slug}", slug=slug,
                            title=f"something to do about {slug}",
                            severity=2, scope="src/", area=area)


def a_report(*filed: inspection.Filed, suggestions=()) -> inspection.Report:
    return inspection.Report(
        filed=tuple(filed),
        area_suggestions=tuple(inspection.AreaSuggestion(**one)
                               for one in suggestions))


def printed(report: inspection.Report) -> tuple[str, int]:
    """What `l5-inspect` prints for a report, and what it returns.

    The report is constructed rather than inspected out of a target: what is
    under test is the words that script prints for a filing that named no area,
    and a report is the input it prints them from.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = conftest.load_script("l5-inspect").report(report,
                                                         report.dry_run)
    return buffer.getvalue(), code


UNNAMED, NAMED = "sorts-by-nothing", "sorts-by-something"


def test_the_broad_report_gives_an_area_less_brief_its_own_lines():
    """The same thing the post-story report says, said where a developer
    running an inspection by hand reads it.

    The two reports differ in one field of one filing — whether that brief
    named an area — so the lines that appear in one and not the other are that
    field deciding. The brief that named an area must not be among them, and
    the command still exits zero, because the findings were filed.
    """
    unnamed, code = printed(a_report(
        a_filed(UNNAMED), a_filed(NAMED, area=AN_AREA),
        suggestions=[{"slug": UNNAMED, "concerns": CONCERNS}]))
    named, control_code = printed(a_report(
        a_filed(UNNAMED, area=ANOTHER_AREA), a_filed(NAMED, area=AN_AREA)))

    assert code == control_code == 0

    before = named.splitlines()
    added = [line for line in unnamed.splitlines() if line not in before]
    assert added, f"unnamed:\n{unnamed}\nnamed:\n{named}"
    assert any(UNNAMED in line for line in added), added
    assert any(CONCERNS in line for line in added), added
    assert not any(NAMED in line for line in added), added
    assert CONCERNS not in named, named


def test_the_broad_report_prints_no_suggestion_for_a_finding_it_did_not_file():
    """The same by-slug rule, on the other surface.

    The suggestion for the brief that was filed is required to be printed in
    the same call, so "the dropped one is absent" cannot be satisfied by a
    report that printed nothing.
    """
    text, code = printed(a_report(
        a_filed(UNNAMED),
        suggestions=[{"slug": UNNAMED, "concerns": CONCERNS},
                     {"slug": "never-filed",
                      "concerns": CONCERNS_OF_THE_DROPPED}]))

    assert code == 0
    assert CONCERNS in text, text
    assert CONCERNS_OF_THE_DROPPED not in text, text
    assert "never-filed" not in text, text


# ==========================================================================
# A target that declares no vocabulary of areas
# ==========================================================================


def standards_body(target: Path) -> str:
    """Every standards document this target declares, as one text.

    The Inspector is handed the directory whole and looks for no file by name,
    so this is the whole of what could declare a vocabulary.
    """
    directory = target / ".harness" / "standards"
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(directory.glob("*.md")))


def test_a_target_declaring_no_areas_files_every_brief_with_none(
        tmp_path, harness, monkeypatch):
    """Nothing infers, defaults or coins an area for such a target.

    Its standards declare no vocabulary — asserted, so the case is the one it
    claims to be — and its Inspector names no area and offers no suggestions,
    which is what `prompts/inspector.md` tells it to do there. The filing still
    happens and the run still completes, which is the half of "exactly as it
    does today" that is about what is filed.
    """
    target = a_target(tmp_path, "a-target-with-no-vocabulary")
    assert AREA not in standards_body(target).lower(), standards_body(target)

    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[finding(1)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert "1 finding(s)" in summary_of(target)


def test_such_a_target_is_told_nothing_about_areas_after_a_story(
        tmp_path, harness, monkeypatch):
    """The other half: its report is the report it wrote before this story.

    This is the criterion that a target declaring no area vocabulary is
    unaffected by every part of the story, and it is the one an area-less line
    written per filing cannot satisfy — every brief such a target files names
    no area, so a line for each of them is a line on every brief a target that
    never opted in ever files. The control for the absence is
    `test_an_area_less_filed_brief_gets_a_line_of_its_own_before_the_summary`,
    where the identical reading of the identical fixture does report one.
    """
    target = a_target(tmp_path, "no-vocabulary-and-no-line")
    assert AREA not in standards_body(target).lower(), standards_body(target)

    code, inspector = narrow_run_with(target, harness, monkeypatch,
                                      findings=[finding(1)])

    assert code == 0
    assert inspector.invocations, "the inspection was never attempted"
    assert notes_in(target) == [], inspection_lines(target)


def test_the_broad_report_of_such_a_target_is_the_report_it_was_before(
        tmp_path, harness):
    """The same claim on the other surface, made by comparison rather than by
    searching for wording this module would have to spell.

    The report a target declaring no vocabulary produces is printed beside the
    same report with every filing carrying an area, and the two must be the
    same text: a filing's area is not printed among what was filed, so any
    difference between them is a line about a missing area. Section F is the
    control — there, the identical comparison differs.
    """
    target = a_target(tmp_path, "a-broad-target-with-no-vocabulary")
    report, inspector = broad_with(target, harness,
                                   findings=[finding(1), finding(2)])

    assert inspector.invocations, "the inspection was never attempted"
    assert report.area_suggestions == ()
    assert set(filed_areas(report).values()) == {""}, filed_areas(report)

    as_written, code = printed(report)
    as_if_named, control_code = printed(dataclasses.replace(
        report,
        filed=tuple(dataclasses.replace(one, **{AREA: AN_AREA})
                    for one in report.filed)))

    assert code == control_code == 0
    assert as_written == as_if_named, f"wrote:\n{as_written}\n" \
                                      f"would have written:\n{as_if_named}"


# ==========================================================================
# The two producers' prose
# ==========================================================================


INSPECTOR_PROMPT = flattened(
    (REPO_ROOT / "prompts" / inspection.INSPECTOR_PROMPT)
    .read_text(encoding="utf-8"))

SKILL = flattened(
    (REPO_ROOT / "plugin" / "skills" / "file-a-brief" / "SKILL.md")
    .read_text(encoding="utf-8"))


#: The rule the Inspector is given, one claim per entry. Each is a sentence
#: the prompt has to state for the producer that files most briefs to do the
#: right thing with a vocabulary it did not write.
THE_INSPECTORS_RULE = (
    "name exactly one, from that vocabulary",
    "where none of the declared names fits, name none",
    "a wrong area is worse than a missing one",
    "never coin a name",
    "do not derive the area from the paths",
    "write a suggestion",
    "reported and never filed",
)


@pytest.mark.parametrize("stated", THE_INSPECTORS_RULE)
def test_the_inspector_prompt_states_the_area_rule(stated):
    """Read as it ships, and searched rather than eyeballed.

    A prompt is the only thing standing between "name an area" and a model
    naming one that does not exist, so each half of the rule is asserted
    separately: a prompt that kept four of them and dropped the fifth would
    otherwise pass.
    """
    assert stated in INSPECTOR_PROMPT, stated


def test_the_inspector_prompt_names_the_field_and_the_channel():
    """The two names the Inspector has to write, spelled as the schemas spell
    them — a rule stated over a field the producer cannot name is a rule it
    cannot follow."""
    assert AREA in INSPECTOR_PROMPT
    assert SUGGESTIONS in INSPECTOR_PROMPT


def test_the_inspector_prompt_tells_a_target_with_no_vocabulary_to_name_none():
    """The case that has to cost such a target nothing, said to the producer
    as well as handled by the code."""
    assert "declares no vocabulary of areas" in INSPECTOR_PROMPT
    assert "name no area on any brief and write no suggestions" in \
        INSPECTOR_PROMPT


#: What the assist agent is told, one claim per entry: whose document the
#: vocabulary is, that it proposes one name from it, that the developer sees it
#: before anything is filed, that none is an answer, and that it invents
#: nothing.
THE_ASSIST_AGENTS_RULE = (
    "the vocabulary of areas is the target's own document",
    "propose one name from it",
    "propose exactly one",
    "never invent a name that is not in the document",
    "proposing none is a correct answer",
    "show the area you propose to the developer with the rest of the brief",
)


@pytest.mark.parametrize("stated", THE_ASSIST_AGENTS_RULE)
def test_the_filing_skill_states_the_area_rule(stated):
    assert stated in SKILL, stated


def test_the_filing_skill_shows_the_area_among_what_is_filed():
    """The brief the developer is shown is the brief that is filed, so the area
    is in the list of what is shown rather than mentioned somewhere above it."""
    assert "the area you propose" in SKILL
    assert "do not file it until they say so" in SKILL


# ==========================================================================
# The board: a sixth user of the mechanism that was already there
# ==========================================================================


#: What the sync scripts name the board field with, read off the installed copy
#: rather than written here, for the reason every other board value in this
#: suite is read off it: a target that renamed the column would otherwise have
#: to edit this module to keep the suite green.
AREA_CONSTANT = "AREA_FIELD"
THE_BOARDS_AREA_FIELD = this_targets(AREA_CONSTANT)


def area_environment_for(script: Path) -> dict:
    """What a copy needs in its environment to write the area.

    The installed copy needs nothing, which is the point of it. The template
    carries no field name by design, so it is handed exactly the value the
    installed copy sets in its own text.
    """
    if script == INSTALLED_SCRIPT:
        return {}
    return {TEMPLATE_CONSTANTS[AREA_CONSTANT][0]: THE_BOARDS_AREA_FIELD}


def board_offering_the_area(ledger: Path, *options: str) -> None:
    """The stub's board with an Area column on it, offering `options`.

    Added here rather than seeded, because the claim below is that a board
    *with* the column gets the value written and a board without it does not:
    the two have to be tellable apart.
    """
    def add(project):
        project["fields"].append({
            "id": "PVTSSF_area",
            "name": THE_BOARDS_AREA_FIELD,
            "type": "SINGLE_SELECT",
            "options": [{"id": f"opt-area-{index}", "name": name}
                        for index, name in enumerate(options)],
        })
    rewrite_the_board(ledger, add)


def classification_written(ledger: Path, brief: dict) -> None:
    """Every field beside the area, still carrying what the payload said.

    Asserted wherever the area's own write is asserted, because the whole
    claim about this being a sixth user of an existing mechanism is that
    whatever the area costs, it costs nothing beside it.
    """
    for axis in CLASSIFICATION:
        assert board_field_value(ledger, axis.field_name) == \
            str(brief[axis.payload_field]), axis.field_name


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_filed_brief_carries_its_area_on_the_board(script, tmp_path):
    """The value the payload carries, written through the same call the five
    beside it are written through.

    The board offers two options, so landing on the one the brief names is a
    resolution rather than the only option there was.
    """
    environment, ledger = stub_tracker(tmp_path)
    board_offering_the_area(ledger, AN_AREA, ANOTHER_AREA)
    brief = a_brief_carrying(**{AREA: AN_AREA})

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-with-an-area", payload=brief,
                               breaking=area_environment_for(script))

    assert result.returncode == 0, result.stderr
    assert board_field_value(ledger, THE_BOARDS_AREA_FIELD) == AN_AREA
    classification_written(ledger, brief)


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_brief_that_named_no_area_writes_nothing_there_and_still_lands(
        script, tmp_path):
    """Naming none stays a valid brief all the way to the board.

    The column is there and the payload has nothing for it, so nothing is
    written and the entry lands with everything else on it. Its control is the
    assertion above, where the same drive against the same board writes the
    value the payload carries.
    """
    environment, ledger = stub_tracker(tmp_path)
    board_offering_the_area(ledger, AN_AREA, ANOTHER_AREA)
    brief = a_filed_brief()
    assert AREA not in brief, brief

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-with-no-area", payload=brief,
                               breaking=area_environment_for(script))

    assert result.returncode == 0, result.stderr
    assert board_field_value(ledger, THE_BOARDS_AREA_FIELD) == ""
    assert len(board_items(ledger)) == 1
    classification_written(ledger, brief)


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_a_board_with_no_area_field_costs_that_field_and_nothing_else(
        script, tmp_path):
    """No new failure path: a board this harness cannot configure is said on
    stderr and skipped.

    gh cannot create a project field, so a column the board does not have is
    exactly what a target that has not added one looks like — which is every
    target today. The entry is created, the item is on the board, every other
    field is written, and the exit is zero.
    """
    environment, ledger = stub_tracker(tmp_path)
    brief = a_brief_carrying(**{AREA: AN_AREA})

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-no-area-field", payload=brief,
                               breaking=area_environment_for(script))

    assert result.returncode == 0, result.stderr
    assert THE_BOARDS_AREA_FIELD in result.stderr
    assert board_field_value(ledger, THE_BOARDS_AREA_FIELD) == ""
    assert len(board_items(ledger)) == 1
    classification_written(ledger, brief)


@needs_jq
@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_an_area_field_that_lacks_the_option_costs_that_field_and_nothing_else(
        script, tmp_path):
    """The same answer for a column that does not offer the name.

    This is the ordinary case for a free-text vocabulary: the target's document
    gains a name and the board has not been given the matching option yet. It
    is said on stderr, naming both the field and the value, and the entry lands
    with everything else written.
    """
    environment, ledger = stub_tracker(tmp_path)
    board_offering_the_area(ledger, ANOTHER_AREA)
    brief = a_brief_carrying(**{AREA: AN_AREA})

    result = sync_to_the_board(script, tmp_path, environment,
                               key="k-no-area-option", payload=brief,
                               breaking=area_environment_for(script))

    assert result.returncode == 0, result.stderr
    assert THE_BOARDS_AREA_FIELD in result.stderr
    assert AN_AREA in result.stderr
    assert board_field_value(ledger, THE_BOARDS_AREA_FIELD) == ""
    classification_written(ledger, brief)


#: How the sync script writes one classification field, and how it reads one
#: value off the payload. Both are read out of the script rather than listed,
#: so the count the comments are held to is the script's own.
A_BOARD_WRITE = re.compile(r'^\s*set_board_field "\$([A-Z_]+)"', re.M)
A_PAYLOAD_READ = re.compile(r'^\s*(\w+)="\$\(payload_value (\w+)\)"', re.M)

#: What a count reads as in prose. Bounded well above any plausible number of
#: board fields, so a comment counting them can be compared against every other
#: word it might have said.
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

#: The two things the script's comments count, spelled as the phrase each
#: count sits in front of. Written this way rather than as the bare subject,
#: because the script also describes what one call does to *one* classification
#: field — a sentence that is not a count and must not be read as one.
COUNTED = ("classification field names", "board fields")


@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_the_area_is_read_off_the_payload_and_written_like_its_five_siblings(
        script):
    """A shipped script and the subject: what this harness ships to a target.

    The area has to be read the way the five beside it are read and written
    through the same call, because that is what makes it a sixth user of a
    mechanism rather than a sixth failure path. Each write is required to name
    a distinct constant, so the count the comments are held to below is a count
    of fields rather than of repetitions.
    """
    text = script.read_text(encoding="utf-8")

    written = A_BOARD_WRITE.findall(text)
    assert len(set(written)) == len(written), written
    assert AREA_CONSTANT in written, written

    read = {name: field for name, field in A_PAYLOAD_READ.findall(text)}
    assert read.get(AREA) == AREA, read


def prose_of(text: str) -> str:
    """A shell script's comments as prose, flattened for searching.

    The comment marker is taken off each line before the wrapping is, because a
    sentence that wrapped between a number and the thing it counts otherwise
    reads as "six # classification field names" and no search for the phrase
    would find it.
    """
    return flattened(re.sub(r"^\s*#", "", text, flags=re.M))


@pytest.mark.parametrize("script", BOTH_SYNC_COPIES)
def test_the_comments_counting_the_classification_fields_agree_with_them(
        script):
    """A number nothing checks is a number that will be wrong.

    The count is derived from the writes the script makes, and each comment
    that states one is required to state that number and no other — so a
    seventh field added without the comments being touched reddens here rather
    than leaving a comment that says six beside seven of them.
    """
    text = script.read_text(encoding="utf-8")
    flat = prose_of(text)
    count = len(A_BOARD_WRITE.findall(text))
    assert count in NUMBER_WORDS, count
    word = NUMBER_WORDS[count]

    for counted in COUNTED:
        assert f"{word} {counted}" in flat, counted
        for number, other in NUMBER_WORDS.items():
            if number != count:
                assert f"{other} {counted}" not in flat, (other, counted)
