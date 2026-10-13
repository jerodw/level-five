"""Independent validation for the story that makes a story's description and a
brief's body open with paragraphs a developer can read without knowing the
harness, and makes whatever shows either for approval show that opening first.

Written from the acceptance criteria rather than from the implementation. The
rule is stated once, in the shared prose layer, and what this module holds is
that it reaches where it is read:

  * **the layer's own text.** The entry is a fact about what this repository
    ships, so the layer is read as the subject: the entry states each thing
    the story requires, it sits after the conversational entry and before the
    worked pair, it names no agent by role and adds no example block — the
    constraints `tests/test_a_question_to_the_developer_is_about_consequences.py`
    already holds the layer to, re-asked here of the layer with the entry in
    it, through that module's own predicates rather than a second spelling.
  * **the reach.** The planner's prompt is captured off `scripts/l5-plan`
    with a stub `claude` on PATH, the assist prompt off `scripts/l5-assist`
    with `execvp` intercepted, and the Inspector's off the render the
    inspection module builds — each the text an agent actually receives,
    because a placeholder in a template that nothing resolves is not reach.
    The two schema descriptions and the skill are read as the documents a
    reader meets the artifact under.
  * **the form, not a score.** Both fields keep `type` and `description` and
    nothing else, and a one-line description and a one-line body still
    validate: the rule is prose for a writer, and nothing refuses on it.

Every absence asserted here carries a demonstration that it can fail, and
no control here deletes a search term from a text and then searches for it,
because that cannot fail for any document. Each negative case is a text that
lacks the statement for a reason of its own:

  * "the document states each of these things" sits beside the same searches
    over the text the document carried before this story — the planner
    template with its draft step and skeleton restored to their superseded
    wording, the assist template with its body clause and filing paragraph
    restored, the skill with its body section removed and its show step
    restored, each schema description restored to its superseded text —
    which report every statement absent. The superseded wordings are carried
    here as literals, the way the title module carries the ones it
    superseded, because they are a fact about documents a reader of this
    file cannot see. The restored templates are rendered through the real
    launchers against a mirrored harness root, so the search that reports
    them absent is the one that reports the shipped render carrying them;
  * "the rendered prompt carries the entry" sits beside the same searches
    over prompts rendered against a mirrored harness root whose layer lacks
    the entry. What that control asserts absent is exactly the phrases that
    reach each render through the layer and nowhere else — a template's own
    restatement of the rule, and the schema description a render resolves,
    carry some of the layer's phrases in their own words, and those are
    still found once the entry is cut, which is the point of a rule stated
    once and applied in each document's own words. The conversational entry
    beside the cut is still found, so the strip took the entry and nothing
    else;
  * "no document quotes a whole new sentence of either description" sits
    beside the same scan over a copy with one such sentence planted in it;
  * "the Inspector's prompt was left alone" sits beside the same diff over a
    path this story did change;
  * "the fields gained no constraint" sits beside the same validator refusing
    a value of the wrong type.

The layer's own statements have a control of a different kind: the layer with
the entry cut is shown to lack every one of them and to keep everything
beside them, which is the same cut the reach controls depend on.

story-222 qualified the third of the opening's three things in both the layer
and the story schema's description of story.description: what the work
deliberately leaves alone is something that stays correct as it is, never a
defect it knows of. Each qualification is folded into its document's table,
so every assertion above that the table drives is made of it — the superseded
description and the layer beside the entry, which never carried it, among
them — and each has a second control: the document with that one statement
cut out, where it is reported missing and everything else is still found.

Nothing here invokes a model and nothing here runs the suite.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import conftest
from conftest import story_diff

import context_assembler
import filed_query
import inspection
import schema_validator
import story_parser

import test_a_brief_is_not_only_a_defect as contract
import test_a_brief_is_titled_for_its_end_state as titled
import test_a_question_to_the_developer_is_about_consequences as conversations
import test_planner_receives_the_standards as standards
import test_the_assist_agent_files_a_brief as filing
# The planner prompt as l5-plan hands it to a session, captured whole: the
# fixture the conversational module built for the same reason, made available
# to the tests here by being imported into this module's namespace.
from test_a_question_to_the_developer_is_about_consequences import (  # noqa: F401
    planner_prompt,
)

REPO_ROOT = Path(context_assembler.__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "prompts"

PROSE_LAYER = context_assembler.PROSE_LAYER
ASSIST_TEMPLATE = conversations.ASSIST_TEMPLATE
PLANNER_TEMPLATE = conversations.PLANNER_TEMPLATE
INSPECTOR_TEMPLATE = inspection.INSPECTOR_PROMPT

STORY_SCHEMA = schema_validator.load_schema("story")
BRIEF_SCHEMA = schema_validator.load_schema(inspection.BRIEF_SCHEMA)
STORY_DESCRIPTION_FIELD = STORY_SCHEMA["properties"]["story"]["properties"]["description"]
BRIEF_BODY_FIELD = BRIEF_SCHEMA["properties"]["body"]

collapsed = conversations.collapsed
prose_layer = conversations.prose_layer

#: Where the entry this story adds begins, in the layer's own words, and where
#: the worked pair it precedes begins. The conversational entry's opening is
#: the neighbouring module's constant, so the ordering below is asked in the
#: vocabulary that module already fixed.
ENTRY_OPENS = "The same altitude governs a story or a brief"
PAIR_OPENS = "As it was given:"
CONVERSATIONAL_OPENS = conversations.CONVERSATIONAL_OPENS
COUNT_ENTRY_OPENS = conversations.COUNT_ENTRY_OPENS


def entry_of(layer: str) -> str:
    """The entry this story added, cut from the layer by its neighbours."""
    start = layer.index(ENTRY_OPENS)
    end = layer.index(PAIR_OPENS, start)
    return layer[start:end]


def without_the_entry(layer: str) -> str:
    """A copy of the layer with the entry taken out and nothing else."""
    entry = entry_of(layer)
    assert entry in layer
    return layer.replace(entry, "", 1)


def missing(text: str, phrases: tuple[str, ...]) -> list[str]:
    """The phrases `text` does not carry, searched across line breaks and
    case. The one check every statement assertion and its control use."""
    said = collapsed(text)
    return [phrase for phrase in phrases if phrase not in said]


def own_words_of(template_name: str) -> str:
    """What a rendered prompt says without the layer: its template unrendered,
    with the schemas the template's own placeholders resolve beside it. The
    template is read raw rather than rendered, so nothing of the layer is in
    it; the schemas are the same texts `schema_context` injects, and only the
    ones this template names, because a schema no placeholder here resolves
    is not in this render."""
    template = (PROMPTS / template_name).read_text(encoding="utf-8")
    schemas = [text for name, text
               in context_assembler.schema_context(REPO_ROOT).items()
               if f"{{{{{name}}}}}" in template]
    return "\n".join([template, *schemas])


def through_the_layer_alone(own_words: str) -> dict[str, tuple[str, ...]]:
    """The layer's phrases a document does not carry in its own words — the
    ones that reach its render through the layer and nowhere else, which are
    the only ones cutting the entry from the layer can be expected to take.
    Requirements every phrase of which the document carries itself are left
    out, so what remains is what a layer-absence control may assert."""
    said = collapsed(own_words)
    only = {requirement: tuple(phrase for phrase in phrases if phrase not in said)
            for requirement, phrases in LAYER_STATES.items()}
    return {requirement: phrases for requirement, phrases in only.items() if phrases}


# ==========================================================================
# What each document has to state
#
# Each document restates the rule in its own words, so each has its own
# phrases; the key names the requirement the phrases serve. What every set
# shares is the opening's shape — one to three paragraphs of plain English;
# what is wrong or wanted, what the work does, what it leaves alone; a
# developer who does not know the harness — and that the names come after.
# ==========================================================================

LAYER_STATES = {
    "whose opening, and what it opens with":
        ("a story's description and a brief's body open with one to three "
         "paragraphs of plain english",),
    "the three things the opening says":
        ("what is wrong or wanted, what the work does about it, and what it "
         "deliberately leaves alone",),
    "who it is written for":
        ("developer who does not know this harness intimately",),
    "the names kept for later":
        ("paths, stage names, check names and field names are kept for the "
         "paragraphs that follow the opening",),
    "what shows it for approval shows the opening first":
        ("shows it in that form first, before anything else",),
}

#: What the layer has said since story-222 about what a piece of work
#: deliberately leaves alone: something that stays correct as it is, never a
#: defect it knows of, which is fixed or set out as a gap left with its
#: reason. Folded into LAYER_STATES so every assertion that table drives — the
#: entry, the remainder beside it, each render and each mirror without the
#: entry — is made of it too.
LAYER_LEAVES_ALONE_STATES = {
    "what the work leaves alone stays correct, never a known defect":
        ("what the work deliberately leaves alone is something that stays "
         "correct as it is, never a defect it knows of",),
    "a known defect is fixed, or set out as a gap left with its reason":
        ("a defect it knows of is either fixed by the work or set out on its "
         "own as a gap left, with the reason it is left",),
}
LAYER_STATES.update(LAYER_LEAVES_ALONE_STATES)

PLANNER_DRAFT_STEP_STATES = {
    "the draft opens with the plain paragraphs":
        ("opens with the plain-english paragraphs",),
    "before anything else in it":
        ("before anything else",),
    "what is approved is what is read first":
        ("what the developer approves is what they read first",),
    "the names come after":
        ("paths, stages and fields following them",),
}

#: The two placeholders the skeleton's description shows the form by, in the
#: order they have to stand in.
SKELETON_PLAIN_FIRST = (
    "<one to three paragraphs of plain english first: what is wrong or wanted, "
    "what this story does about it, and what it deliberately leaves alone, "
    "readable by a developer who does not know the harness>")
SKELETON_NAMES_AFTER = "<then the paragraphs that name paths, stages and fields>"

ASSIST_STATES = {
    "the body opens with the plain paragraphs":
        ("opening with one to three paragraphs of plain english",),
    "the three things the opening says":
        ("what is wrong or wanted, what the work does about it, and what it "
         "deliberately leaves alone",),
    "who it is written for":
        ("a developer who does not know the harness intimately can follow",),
    "the names come after":
        ("paths, stage names and field names kept for the paragraphs after "
         "them",),
    "shown to the developer in that form before filing":
        ("shown to the developer in that form before it is filed",
         "the plain-english opening first"),
}

SKILL_BODY_STATES = {
    "the body opens with the plain paragraphs":
        ("opens with one to three paragraphs of plain english",),
    "the three things the opening says":
        ("what is wrong or wanted, what the work does about it, and what it "
         "deliberately leaves alone",),
    "who it is written for":
        ("developer who does not know the harness intimately",),
    "the names come after":
        ("keep the paths, the stage names, the check names and the field "
         "names for the paragraphs after the opening",),
}

SKILL_SHOW_STEP_STATES = {
    "the opening goes first when it is shown":
        ("put the body's plain-english opening first",),
    "the developer reads it before anything else":
        ("reads it before anything else",),
}

#: The sentence the skill's title section carried before this story and has
#: to keep: a defect brief opens by saying what is wrong today.
SKILL_KEPT = ("open the body by saying what is wrong today",)

STORY_DESCRIPTION_STATES = {
    "the description opens with the plain paragraphs":
        ("opens with one to three paragraphs of plain english",),
    "the three things the opening says":
        ("what is wrong or wanted, what the story does about it, and what it "
         "deliberately leaves alone",),
    "who it is written for":
        ("developer who does not know the harness intimately",),
    "the names come after":
        ("paragraphs naming paths, stage names, check names and field names "
         "follow it",),
}

#: What the story schema's description of story.description has said since
#: story-222, in its own words rather than the layer's: what a story leaves
#: alone stays correct as it is, never a defect the plan knows of, and a
#: defect is fixed by the story or recorded as a gap left. Folded into the
#: table for the reason the layer's is.
STORY_DESCRIPTION_LEAVES_ALONE_STATES = {
    "what a story leaves alone stays correct, never a known defect":
        ("what a story deliberately leaves alone is something that stays "
         "correct as it is, never a defect the plan knows of",),
    "a defect in what the story changes is fixed by it, a gap left recorded "
    "apart":
        ("a defect in the part of the system the story changes is fixed by "
         "the story",
         "is recorded there rather than in this text"),
}
STORY_DESCRIPTION_STATES.update(STORY_DESCRIPTION_LEAVES_ALONE_STATES)

BRIEF_BODY_STATES = {
    "the body opens with the plain paragraphs":
        ("opens with one to three paragraphs of plain english",),
    "the three things the opening says":
        ("what is wrong or wanted, what the work does about it, and what it "
         "deliberately leaves alone",),
    "who it is written for":
        ("developer who does not know the harness intimately",),
    "the names come after":
        ("paragraphs naming paths, stage names, check names and field names "
         "follow that opening",),
    "what it said before about evidence is kept":
        ("cited as file:line", "work that does not exist yet has no line to "
         "cite"),
}


# ==========================================================================
# Readers
# ==========================================================================


def skill_text() -> str:
    return filing.SKILL_PATH.read_text(encoding="utf-8")


def section_named(text: str, name: str) -> str:
    """The one section of `text` whose heading is exactly `name`, resolved the
    way the title module resolves its section and refused when it is not
    exactly one."""
    headings = [
        (match.start(), match.group("bracketed") or match.group("marked"))
        for match in titled.HEADING.finditer(text)
    ]
    naming = [index for index, (_, heading) in enumerate(headings)
              if heading.strip().lower() == name.lower()]
    assert len(naming) == 1, [heading for _, heading in headings]
    index = naming[0]
    start = headings[index][0]
    end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
    return text[start:end]


def show_step_of(skill: str) -> str:
    """The numbered step of "Filing it" that shows the brief to the developer,
    found by its bold lead rather than by its number."""
    steps = re.split(r"^\d+\. ", section_named(skill, "Filing it"),
                     flags=re.MULTILINE)[1:]
    showing = [step for step in steps
               if step.startswith("**Show it to the developer**")]
    assert len(showing) == 1, [step[:40] for step in steps]
    return showing[0]


def draft_step_of(prompt: str) -> str:
    """The planner's process step that presents the draft, resolved through
    the readers the standards module already derives it with."""
    steps = standards.numbered_steps(standards.process_section(prompt))
    return steps[standards.step_index(steps, standards.DRAFT_STEP)]


def assist_prompt(monkeypatch, harness_root: Path | None = None) -> str:
    """What `l5-assist` would have handed the session."""
    _, args = conversations.launched(monkeypatch, [conversations.ASSIST_LAUNCHER],
                                     harness_root=harness_root)
    return conversations.appended(args)


def inspector_prompt(tmp_path: Path, harness_root: Path = REPO_ROOT) -> str:
    """What the inspection module renders for the Inspector, against a bare
    target: the placeholders this story cares about resolve from the harness
    root and need nothing of the target."""
    target = tmp_path / "inspected"
    target.mkdir(exist_ok=True)
    return inspection._render(
        harness_root, target, {},
        inspection.Scope(path="", kind=inspection.SOURCE), (),
        filed_query.Answer(answered=False, reason="no query ran for this test"),
        target / "findings.json",
    )


# ==========================================================================
# The layer: the entry, where it sits, and what it does not do
# ==========================================================================


@pytest.mark.parametrize("requirement", sorted(LAYER_STATES),
                         ids=sorted(LAYER_STATES))
def test_the_layer_states_what_the_rule_requires(requirement):
    assert missing(entry_of(prose_layer()), LAYER_STATES[requirement]) == []


def test_the_same_searches_report_the_layer_beside_the_entry_lacks_every_statement():
    """The control for the searches above: the rest of the layer — the count
    entry, the conversational entry and the worked pair, which is the layer
    as it was before this story — is reported missing every one of them, so
    a search that has stopped seeing anything is told apart from a layer
    that says these things in its entry and nowhere else."""
    remainder = without_the_entry(prose_layer())

    for requirement, phrases in LAYER_STATES.items():
        assert missing(remainder, phrases) == list(phrases), requirement


def cut_as_one_span(text: str, statements: dict[str, tuple[str, ...]]) -> str:
    """`text`, collapsed, with `statements` cut out of it as one span: from the
    first words of the first to the sentence end after the last. Asserted to
    have found the span once, so a rewording the cut no longer finds reddens
    rather than leaving its control asserting nothing."""
    said = [phrase for phrases in statements.values() for phrase in phrases]
    span = re.compile(rf"{re.escape(said[0])}.*?{re.escape(said[-1])}\.\s*")
    cut, count = span.subn("", collapsed(text))
    assert count == 1, "the statement is no longer one span of the text"
    return cut


def test_the_entry_with_story_222s_qualification_cut_lacks_it_and_keeps_the_rest():
    """The control for the qualification in the other direction: the entry
    with that one statement cut out is reported missing every phrase of it,
    while every other statement the entry makes is still found — so the
    qualification is its own sentence of the entry, and the searches above
    find it there rather than in a statement the entry made before."""
    cut = cut_as_one_span(entry_of(prose_layer()), LAYER_LEAVES_ALONE_STATES)

    for requirement, phrases in LAYER_STATES.items():
        expected = list(phrases) if requirement in LAYER_LEAVES_ALONE_STATES \
            else []
        assert missing(cut, phrases) == expected, requirement


def test_the_entry_sits_after_the_conversational_entry_and_before_the_pair():
    """Beside the neighbouring rule about leading with the consequence, and
    ahead of the worked pair that illustrates both; the count entry is still
    ahead of the conversational one, so nothing was interleaved."""
    layer = prose_layer()

    assert layer.index(COUNT_ENTRY_OPENS) < layer.index(CONVERSATIONAL_OPENS)
    assert layer.index(CONVERSATIONAL_OPENS) < layer.index(ENTRY_OPENS)
    assert layer.index(ENTRY_OPENS) < layer.index(PAIR_OPENS)
    assert layer.count(ENTRY_OPENS) == 1


def test_the_entry_is_about_the_act_and_names_no_agent_by_role():
    """Asked with the neighbouring module's own predicate, so the roles
    looked for are the ones the harness ships. Its control — the same
    search reporting a role planted in the layer — is that module's."""
    assert conversations.roles_named_in(entry_of(prose_layer())) == set()
    assert conversations.roles_named_in(prose_layer()) == set()


def test_the_entry_adds_no_example_block_and_the_pair_still_holds():
    """The layer still carries exactly two indented example blocks, and they
    are still the worked pair; the entry itself has no indented line."""
    layer = prose_layer()

    assert not any(line.startswith("    ") and line.strip()
                   for line in entry_of(layer).splitlines())
    assert len(conversations.example_blocks(layer)) == 2
    assert conversations.the_pair_holds(layer)


def test_the_layer_without_the_entry_is_still_the_layer_the_neighbour_holds():
    """Control for the cut above: what `without_the_entry` leaves is a layer
    that still states the conversational entry and still carries the pair,
    so the reach controls below are removing the entry and nothing beside
    it."""
    remainder = without_the_entry(prose_layer())

    assert ENTRY_OPENS not in remainder
    assert COUNT_ENTRY_OPENS in remainder
    for phrases in conversations.ENTRY_STATES.values():
        assert missing(remainder, phrases) == []
    assert conversations.the_pair_holds(remainder)


# ==========================================================================
# The reach: the prompt l5-plan hands the planner
# ==========================================================================


def test_the_planner_prompt_carries_the_entry(planner_prompt):
    for requirement, phrases in LAYER_STATES.items():
        assert missing(planner_prompt, phrases) == [], requirement
    assert context_assembler.PLACEHOLDER.search(planner_prompt) is None


@pytest.mark.parametrize("requirement", sorted(PLANNER_DRAFT_STEP_STATES),
                         ids=sorted(PLANNER_DRAFT_STEP_STATES))
def test_the_planner_presents_the_draft_opening_with_the_plain_paragraphs(
        planner_prompt, requirement):
    """In the step that presents the draft, not somewhere else in the
    prompt: what the developer approves is the text they read first."""
    step = draft_step_of(planner_prompt)

    assert missing(step, PLANNER_DRAFT_STEP_STATES[requirement]) == []


def test_the_planner_skeleton_shows_the_plain_paragraphs_first(planner_prompt):
    """The description placeholder shows the form rather than restating the
    rule: the plain paragraphs, then the paragraphs that name things."""
    said = collapsed(planner_prompt)

    assert SKELETON_PLAIN_FIRST in said
    assert SKELETON_NAMES_AFTER in said
    assert said.index(SKELETON_PLAIN_FIRST) < said.index(SKELETON_NAMES_AFTER)
    # Both inside the skeleton's description, which is the only description
    # the skeleton carries.
    description = said[said.index("description: |"):]
    assert SKELETON_PLAIN_FIRST in description
    assert SKELETON_NAMES_AFTER in description


def test_the_planner_prompt_carries_the_story_schemas_new_description(
        planner_prompt):
    """The schema is what a reader meets the artifact under, and the planner
    receives it whole — so the description the planner writes to is the one
    that declares the opening."""
    assert STORY_DESCRIPTION_FIELD["description"] in planner_prompt


# --------------------------------------------------------------------------
# The controls: the two templates as they read before this story, rendered
# through the same launchers against a mirrored root with the shipped layer
# --------------------------------------------------------------------------

#: What the planner's draft step and the skeleton's description said before
#: this story — literal, because what is asserted is that a template a reader
#: of this file cannot see said these things and no more.
PLANNER_DRAFT_STEP_BEFORE = (
    "Present the draft plan and iterate until the developer approves it.")
SKELETON_DESCRIPTION_BEFORE = "<what this story adds or changes, and why>"

#: The same for the assist template: the body clause of its "A brief carries
#: a title …" sentence, and the filing paragraph's sentence that this story
#: added, which the superseded paragraph did not carry at all.
ASSIST_BODY_CLAUSE_BEFORE = (
    "a body making the case, with its evidence cited as file:line wherever "
    "the brief is about code that exists;")
ASSIST_BODY_CLAUSE_OPENS = "a body making the case"
ASSIST_BODY_CLAUSE_CLOSES = "code that exists;"
ASSIST_FILING_SENTENCE = re.compile(
    r"The brief is shown to the developer in that form before it is\s+filed.*?"
    r"until they say so\.\s*", re.DOTALL)


def planner_template_before() -> str:
    """The shipped planner template with the draft step and the skeleton's
    description restored to their superseded wording, and nothing else
    changed. The step is located by the reader the standards module derives
    it with, so what is restored is the whole step and not a phrase of it."""
    template = (PROMPTS / PLANNER_TEMPLATE).read_text(encoding="utf-8")
    step = draft_step_of(template)
    assert step != PLANNER_DRAFT_STEP_BEFORE
    restored = template.replace(step, PLANNER_DRAFT_STEP_BEFORE, 1)

    start = restored.index("description: |")
    end = restored.index("workflow:", start)
    return (restored[:start]
            + f"description: |\n\t    {SKELETON_DESCRIPTION_BEFORE}\n\t  "
            + restored[end:])


def assist_template_before() -> str:
    """The shipped assist template with the body clause restored to its
    superseded wording and the filing sentence this story added taken out;
    the rest of the template, the placeholder the layer lands in included, is
    as shipped."""
    template = (PROMPTS / ASSIST_TEMPLATE).read_text(encoding="utf-8")
    start = template.index(ASSIST_BODY_CLAUSE_OPENS)
    end = template.index(ASSIST_BODY_CLAUSE_CLOSES, start) \
        + len(ASSIST_BODY_CLAUSE_CLOSES)
    restored = template[:start] + ASSIST_BODY_CLAUSE_BEFORE + template[end:]
    assert ASSIST_FILING_SENTENCE.search(restored) is not None
    return ASSIST_FILING_SENTENCE.sub("", restored, count=1)


@pytest.fixture
def mirrored_with_the_templates_before(tmp_path: Path) -> Path:
    """A harness root carrying the shipped layer beside the two templates as
    they read before this story. A copy, because editing the shipped
    templates is not something a test may do."""
    root = tmp_path / "harness"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / PROSE_LAYER).write_text(prose_layer(), encoding="utf-8")
    (root / "prompts" / PLANNER_TEMPLATE).write_text(
        planner_template_before(), encoding="utf-8")
    (root / "prompts" / ASSIST_TEMPLATE).write_text(
        assist_template_before(), encoding="utf-8")
    return root


def test_the_superseded_templates_differ_from_the_shipped_ones_only_where_restored():
    """Anchor for the controls below: each restoration changed the template,
    and the layer's placeholder survived it, so a render against the mirror
    still receives the layer."""
    for name, before in ((PLANNER_TEMPLATE, planner_template_before()),
                         (ASSIST_TEMPLATE, assist_template_before())):
        shipped = (PROMPTS / name).read_text(encoding="utf-8")
        assert before != shipped, name
        assert context_assembler.PLACEHOLDER.search(before) is not None, name
        assert len(before) < len(shipped), name


def test_the_same_searches_report_the_planner_render_of_the_superseded_template(
        monkeypatch, mirrored_with_the_templates_before):
    """The control for the draft-step and skeleton searches above: rendered
    from the template as it read before this story, the same step is found
    and states none of it, and the skeleton shows the superseded placeholder
    rather than the form — while the layer's entry, untouched in the mirror,
    still arrives."""
    prompt = conversations.rendered_pair(
        monkeypatch, mirrored_with_the_templates_before)[PLANNER_TEMPLATE]
    step = draft_step_of(prompt)

    assert step == PLANNER_DRAFT_STEP_BEFORE
    for requirement, phrases in PLANNER_DRAFT_STEP_STATES.items():
        assert missing(step, phrases) == list(phrases), requirement

    said = collapsed(prompt)
    assert SKELETON_PLAIN_FIRST not in said
    assert SKELETON_NAMES_AFTER not in said
    assert SKELETON_DESCRIPTION_BEFORE in said

    for requirement, phrases in LAYER_STATES.items():
        assert missing(prompt, phrases) == [], requirement


# ==========================================================================
# The reach: the prompt l5-assist appends
# ==========================================================================


def test_the_assist_prompt_carries_the_entry(monkeypatch):
    prompt = assist_prompt(monkeypatch)

    for requirement, phrases in LAYER_STATES.items():
        assert missing(prompt, phrases) == [], requirement
    assert context_assembler.PLACEHOLDER.search(prompt) is None


@pytest.mark.parametrize("requirement", sorted(ASSIST_STATES),
                         ids=sorted(ASSIST_STATES))
def test_the_assist_prompt_states_the_bodys_opening_and_shows_it_first(
        monkeypatch, requirement):
    assert missing(assist_prompt(monkeypatch), ASSIST_STATES[requirement]) == []


def test_the_assist_template_itself_states_the_bodys_opening():
    """In the template's own words rather than the layer's, which is what
    the render-level control below has to allow for."""
    template = (PROMPTS / ASSIST_TEMPLATE).read_text(encoding="utf-8")

    for requirement, phrases in ASSIST_STATES.items():
        assert missing(template, phrases) == [], requirement


def test_the_same_searches_report_the_assist_template_as_it_read_before():
    """The control for the statements above at the template's level: the
    template with the body clause and the filing sentence restored to their
    superseded wording states none of them."""
    for requirement, phrases in ASSIST_STATES.items():
        assert missing(assist_template_before(), phrases) == list(phrases), \
            requirement


def test_the_same_searches_report_the_assist_render_of_the_superseded_template(
        monkeypatch, mirrored_with_the_templates_before):
    """The same control at the render's level, where the layer is also in the
    text: rendered through the real launcher from the superseded template,
    every statement the layer does not carry in the same words is reported
    absent, the ones it does carry are still found — because the layer is
    untouched in the mirror — and at least one is reported absent, so the
    restoration is shown to have reached the render."""
    prompt = conversations.rendered_pair(
        monkeypatch, mirrored_with_the_templates_before)[ASSIST_TEMPLATE]
    layer_says = collapsed(prose_layer())

    reported: list[str] = []
    for requirement, phrases in ASSIST_STATES.items():
        expected = [phrase for phrase in phrases if phrase not in layer_says]
        assert missing(prompt, phrases) == expected, requirement
        reported.extend(expected)
    assert reported

    for requirement, phrases in LAYER_STATES.items():
        assert missing(prompt, phrases) == [], requirement


# --------------------------------------------------------------------------
# The entry reaches both prompts from the one file: rendered against a
# mirrored harness root whose layer lacks it, neither prompt carries it
# --------------------------------------------------------------------------


@pytest.fixture
def mirrored_without_the_entry(tmp_path: Path) -> Path:
    """A harness root carrying copies of the three prompts, with the entry cut
    from the layer and nothing else changed. A copy, because editing the
    shipped layer is not something a test may do."""
    root = tmp_path / "harness"
    (root / "prompts").mkdir(parents=True)
    for name in (ASSIST_TEMPLATE, PLANNER_TEMPLATE):
        (root / "prompts" / name).write_text(
            (PROMPTS / name).read_text(encoding="utf-8"), encoding="utf-8")
    (root / "prompts" / PROSE_LAYER).write_text(
        without_the_entry(prose_layer()), encoding="utf-8")
    return root


def test_the_split_between_the_layers_words_and_a_documents_own_can_go_either_way():
    """Control for `through_the_layer_alone`: a document that says nothing
    of its own leaves every phrase to the layer, and the entry itself, read
    as a document, leaves none — so the split below is deciding on the
    document's words rather than returning one answer for any input."""
    assert through_the_layer_alone("") == LAYER_STATES
    assert through_the_layer_alone(entry_of(prose_layer())) == {}


def test_prompts_rendered_against_a_layer_without_the_entry_lack_it(
        monkeypatch, mirrored_without_the_entry):
    """The control for the two reach assertions above, needing no history:
    the same searches over prompts rendered from a layer without the entry
    report absent every phrase that reaches the render through the layer
    alone, while the conversational entry beside it is still found — so
    green above is the entry arriving through the layer, and not the
    templates already saying it.

    What each template says in its own words — the assist template's body
    clause restates the three things the opening says, in the same words
    the layer uses — is still there after the cut, and is asserted to be:
    that is each document's own application of the rule, which the story
    asks for and which cutting the layer cannot reach."""
    rendered = conversations.rendered_pair(monkeypatch,
                                           mirrored_without_the_entry)

    for name, prompt in rendered.items():
        only = through_the_layer_alone(own_words_of(name))
        assert only, name
        for requirement, phrases in only.items():
            assert missing(prompt, phrases) == list(phrases), (name, requirement)
        for requirement, phrases in LAYER_STATES.items():
            carried = [phrase for phrase in phrases
                       if phrase not in only.get(requirement, ())]
            assert missing(prompt, tuple(carried)) == [], (name, requirement)
        for phrases in conversations.ENTRY_STATES.values():
            assert missing(prompt, phrases) == [], name
    # The templates' own applications of the rule are still there: they are
    # each document's own words, not the layer's, and the cut did not reach
    # them.
    for requirement, phrases in ASSIST_STATES.items():
        assert missing(rendered[ASSIST_TEMPLATE], phrases) == [], requirement
    assert SKELETON_PLAIN_FIRST in collapsed(rendered[PLANNER_TEMPLATE])


# ==========================================================================
# The skill
# ==========================================================================


@pytest.mark.parametrize("requirement", sorted(SKILL_BODY_STATES),
                         ids=sorted(SKILL_BODY_STATES))
def test_the_skill_states_the_bodys_opening_form(requirement):
    """In a section of its own about the body, so a reader of the skill who
    wants the rule for the body finds it under that heading."""
    body = section_named(skill_text(), "The body")

    assert missing(body, SKILL_BODY_STATES[requirement]) == []


@pytest.mark.parametrize("requirement", sorted(SKILL_SHOW_STEP_STATES),
                         ids=sorted(SKILL_SHOW_STEP_STATES))
def test_the_skills_show_step_puts_the_opening_first(requirement):
    step = show_step_of(skill_text())

    assert missing(step, SKILL_SHOW_STEP_STATES[requirement]) == []


def test_the_skill_keeps_that_a_defect_brief_opens_with_what_is_wrong_today():
    """The existing sentence under "The title", kept where it was: the new
    section says how the body opens and does not replace what the title
    section says a defect's body opens with."""
    assert missing(titled.section_about_the_title(skill_text()), SKILL_KEPT) == []


#: The sentences this story added to the skill's show step, which the step
#: did not carry before; taken out whole, the step reads as it did.
SHOW_STEP_ADDED = re.compile(
    r"Put\s+the body's plain-English opening first.*?not in the code\.\s*",
    re.DOTALL)


def skill_without_the_body_section() -> str:
    """The shipped skill with the whole body section removed — the skill as
    it read before this story added that section."""
    skill = skill_text()
    return skill.replace(section_named(skill, "The body"), "", 1)


def skill_with_the_show_step_before() -> str:
    """The shipped skill with the sentences this story added to the show step
    taken out, so the step reads as it did before."""
    skill = skill_text()
    assert SHOW_STEP_ADDED.search(skill) is not None
    return SHOW_STEP_ADDED.sub("", skill, count=1)


def test_the_same_searches_report_the_skill_without_its_body_section():
    """The control for the body statements above: with the section removed,
    the resolver refuses to find it, and the whole of what remains — the
    title section's own sentence about a defect's body included — states
    none of the body's form."""
    without = skill_without_the_body_section()

    with pytest.raises(AssertionError):
        section_named(without, "The body")
    for requirement, phrases in SKILL_BODY_STATES.items():
        assert missing(without, phrases) == list(phrases), requirement
    # Nothing beside the section went with it.
    assert missing(titled.section_about_the_title(without), SKILL_KEPT) == []
    assert show_step_of(without) == show_step_of(skill_text())


def test_the_same_searches_report_the_show_step_as_it_read_before():
    """The control for the show-step statements above: the same step, still
    found by its bold lead, states none of them once the sentences this
    story added are taken out."""
    before = skill_with_the_show_step_before()
    step = show_step_of(before)

    for requirement, phrases in SKILL_SHOW_STEP_STATES.items():
        assert missing(step, phrases) == list(phrases), requirement
    # The body section was not touched by it.
    for requirement, phrases in SKILL_BODY_STATES.items():
        assert missing(section_named(before, "The body"), phrases) == [], \
            requirement


def test_the_kept_sentence_is_the_title_sections_and_not_the_body_sections():
    """The control for the kept sentence: the same search over the section
    this story added reports it absent, so finding it under "The title" is
    finding it where it was rather than anywhere in the skill."""
    assert missing(section_named(skill_text(), "The body"), SKILL_KEPT) \
        == list(SKILL_KEPT)


def test_the_body_section_is_one_section_and_the_title_section_is_still_one():
    """The new heading does not carry the word the title module resolves its
    section by, so that module still finds exactly one; and this module finds
    exactly one body section. Both resolvers refuse otherwise, so this is a
    statement that neither did."""
    skill = skill_text()

    assert "title" not in section_named(skill, "The body").splitlines()[0].lower()
    assert titled.section_about_the_title(skill)
    assert skill.index("### The title") < skill.index("### The body")


# ==========================================================================
# The two schemas: the description declares the opening, and nothing more
# ==========================================================================


@pytest.mark.parametrize("requirement", sorted(STORY_DESCRIPTION_STATES),
                         ids=sorted(STORY_DESCRIPTION_STATES))
def test_the_story_schemas_description_declares_the_opening(requirement):
    assert missing(STORY_DESCRIPTION_FIELD["description"],
                   STORY_DESCRIPTION_STATES[requirement]) == []


@pytest.mark.parametrize("requirement", sorted(BRIEF_BODY_STATES),
                         ids=sorted(BRIEF_BODY_STATES))
def test_the_brief_schemas_body_declares_the_opening_and_keeps_its_evidence_rule(
        requirement):
    assert missing(BRIEF_BODY_FIELD["description"],
                   BRIEF_BODY_STATES[requirement]) == []


def test_the_same_searches_report_each_description_as_it_read_before():
    """The control for the two descriptions above: each restored to its
    superseded text — carried below as a literal — states none of what this
    story added, while the brief body's superseded text still carries the
    evidence statements the story kept, which is what "kept" means."""
    for requirement, phrases in STORY_DESCRIPTION_STATES.items():
        assert missing(STORY_DESCRIPTION_BEFORE, phrases) == list(phrases), \
            requirement

    kept = "what it said before about evidence is kept"
    for requirement, phrases in BRIEF_BODY_STATES.items():
        expected = [] if requirement == kept else list(phrases)
        assert missing(BRIEF_BODY_BEFORE, phrases) == expected, requirement
    # The kept statements are the brief body's and not a phrase any
    # description happens to carry: the story description never had them.
    assert missing(STORY_DESCRIPTION_BEFORE, BRIEF_BODY_STATES[kept]) \
        == list(BRIEF_BODY_STATES[kept])


def test_the_story_description_with_story_222s_qualification_cut_keeps_the_rest():
    """The same control for the description: cut that one statement out of
    it, and every phrase of the statement is reported missing while every
    other statement the description makes about the opening is still found
    — so the description says it in a sentence of its own, beside the
    opening's three things rather than in place of one of them."""
    cut = cut_as_one_span(STORY_DESCRIPTION_FIELD["description"],
                          STORY_DESCRIPTION_LEAVES_ALONE_STATES)

    for requirement, phrases in STORY_DESCRIPTION_STATES.items():
        expected = list(phrases) \
            if requirement in STORY_DESCRIPTION_LEAVES_ALONE_STATES else []
        assert missing(cut, phrases) == expected, requirement


@pytest.mark.parametrize("field", [
    pytest.param(STORY_DESCRIPTION_FIELD, id="story.description"),
    pytest.param(BRIEF_BODY_FIELD, id="brief.body"),
])
def test_neither_field_gained_a_constraint_beyond_its_description(field):
    """The form is prose for a writer and nothing the validator enforces: a
    `pattern` or a `minLength` beside it would refuse every story already
    committed and every brief already filed."""
    assert set(field) == {"type", "description"}, field
    assert field["type"] == "string"


def test_a_brief_whose_body_is_one_terse_line_still_validates():
    """Nothing scores the body. The control is the same validator refusing a
    body of the wrong type, naming the field, so "it validates" is a fact
    about the brief rather than about a validator that has stopped
    deciding."""
    assert contract.problems(contract.brief(body="It is broken.")) == []

    refused = contract.problems(contract.brief(body=[]))
    assert refused
    assert any("$.body" in problem for problem in refused), refused


def test_a_story_whose_description_is_one_line_still_validates():
    """The same for a story: the sample story every coordinator test runs
    against has a one-line description, and it is refused for nothing. The
    control is the same validator refusing a description of the wrong
    type."""
    parsed = story_parser.parse(conftest.STORY, STORY_SCHEMA)
    assert schema_validator.validate(parsed, STORY_SCHEMA) == []

    parsed["story"]["description"] = []
    refused = schema_validator.validate(parsed, STORY_SCHEMA)
    assert refused
    assert any("description" in problem for problem in refused), refused


def test_the_brief_schemas_shape_is_the_shape_the_contract_records():
    """Required list, fields and enums, held to the record the not-only-a-
    defect module keeps of them: description text changed and nothing else."""
    assert tuple(BRIEF_SCHEMA["required"]) == contract.REQUIRED_BEFORE
    assert set(BRIEF_SCHEMA["properties"]) \
        == set(contract.FIELDS_BEFORE) | set(contract.FIELDS_ADDED_SINCE)
    assert BRIEF_SCHEMA["properties"]["severity"]["enum"] == contract.SEVERITIES_BEFORE
    assert BRIEF_SCHEMA["properties"]["confidence"]["enum"] == contract.CONFIDENCES_BEFORE


# --------------------------------------------------------------------------
# The prompts and the skill restate the schemas rather than quoting them
# --------------------------------------------------------------------------

#: What each description said before this story — literal, as the title
#: module carries its superseded text, because what is asserted is that a
#: document a reader of this file cannot see said these things.
STORY_DESCRIPTION_BEFORE = "What the story changes and why."
BRIEF_BODY_BEFORE = (
    "The case for the work: what is wrong or what is wanted, why it matters, "
    "and what would resolve it. Where the brief is about code that exists, the "
    "evidence for it is cited as file:line, and this is where that line-level "
    "evidence lives, because the paths above may not carry it. Work that does "
    "not exist yet has no line to cite, and a citation invented for it would "
    "be evidence of nothing; the case is then made from what the system does "
    "instead."
)


def sentences_of(text: str) -> list[str]:
    return [sentence for sentence in re.split(r"(?<=[.]) ", collapsed(text))
            if len(titled.tokens(sentence)) >= 8]


def new_schema_sentences() -> list[str]:
    """The long sentences this story added to either description: the ones
    it carries now that the superseded text did not."""
    before = set(sentences_of(STORY_DESCRIPTION_BEFORE)
                 + sentences_of(BRIEF_BODY_BEFORE))
    return [sentence for sentence
            in sentences_of(STORY_DESCRIPTION_FIELD["description"])
            + sentences_of(BRIEF_BODY_FIELD["description"])
            if sentence not in before]


def quoted_in(text: str) -> list[str]:
    return [sentence for sentence in new_schema_sentences()
            if titled.contains_run(text, sentence)]


#: The documents that restate the rule in their own words. The templates
#: rather than their renders, because a render carries the schema by
#: construction; the Inspector's is left out for the same reason the title
#: module leaves the schema out of its scan.
RESTATING = {
    f"prompts/{ASSIST_TEMPLATE}": lambda: (PROMPTS / ASSIST_TEMPLATE).read_text(
        encoding="utf-8"),
    f"prompts/{PLANNER_TEMPLATE}": lambda: (PROMPTS / PLANNER_TEMPLATE).read_text(
        encoding="utf-8"),
    f"prompts/{PROSE_LAYER}": prose_layer,
    filing.SKILL_REL: skill_text,
}


def test_this_story_added_sentences_for_the_scan_to_look_for():
    """Anchor for the absence below: an empty scan over an empty list would
    prove nothing."""
    added = new_schema_sentences()

    assert added
    for sentence in added:
        assert sentence not in sentences_of(BRIEF_BODY_BEFORE)
        assert sentence not in sentences_of(STORY_DESCRIPTION_BEFORE)


@pytest.mark.parametrize("document", sorted(RESTATING), ids=sorted(RESTATING))
def test_no_document_quotes_a_whole_new_sentence_of_either_description(document):
    assert quoted_in(RESTATING[document]()) == []


def test_the_quotation_scan_reports_a_document_with_a_sentence_planted():
    """Control: the same scan over a copy of the assist prompt with one of the
    new sentences written into it reports that sentence."""
    planted_sentence = new_schema_sentences()[0]
    planted = RESTATING[f"prompts/{ASSIST_TEMPLATE}"]() + "\n" + planted_sentence

    assert quoted_in(planted) == [planted_sentence]


# ==========================================================================
# The Inspector: its prompt unchanged, its render carrying the new text
# ==========================================================================


def test_the_inspectors_prompt_was_left_alone():
    assert story_diff([f"prompts/{INSPECTOR_TEMPLATE}"],
                      validation_file=Path(__file__)) == ""


def test_the_same_diff_reports_a_path_this_story_did_change():
    """The control the emptiness above is read against."""
    assert story_diff([f"prompts/{PROSE_LAYER}"],
                      validation_file=Path(__file__)) != ""


def test_the_rendered_inspector_prompt_carries_the_entry_and_the_new_body_description(
        tmp_path):
    """Through the placeholders it already resolves, so the form reaches the
    Inspector without a fifth statement of the rule."""
    rendered = inspector_prompt(tmp_path)

    for requirement, phrases in LAYER_STATES.items():
        assert missing(rendered, phrases) == [], requirement
    assert BRIEF_BODY_FIELD["description"] in rendered
    assert context_assembler.PLACEHOLDER.search(rendered) is None


def test_the_inspector_render_against_a_layer_without_the_entry_lacks_it(
        tmp_path):
    """Control: the same render against a mirrored harness root whose layer
    lacks the entry reports it absent, while the body description — which
    comes from the schema, not the layer — still arrives."""
    root = tmp_path / "harness"
    (root / "prompts").mkdir(parents=True)
    (root / "schemas").mkdir()
    for path in PROMPTS.glob("*.md"):
        (root / "prompts" / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "prompts" / PROSE_LAYER).write_text(
        without_the_entry(prose_layer()), encoding="utf-8")
    for path in (REPO_ROOT / "schemas").glob("*.schema.json"):
        (root / "schemas" / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")

    rendered = inspector_prompt(tmp_path, harness_root=root)

    # Only what reaches the Inspector through the layer alone: the body
    # description it resolves from the brief schema restates the three things
    # the opening says in the layer's own words, and the cut cannot take
    # those — so they are asserted still found, and the rest asserted gone.
    only = through_the_layer_alone(own_words_of(INSPECTOR_TEMPLATE))
    assert only
    for requirement, phrases in only.items():
        assert missing(rendered, phrases) == list(phrases), requirement
    for requirement, phrases in LAYER_STATES.items():
        carried = [phrase for phrase in phrases
                   if phrase not in only.get(requirement, ())]
        assert missing(rendered, tuple(carried)) == [], requirement
    assert BRIEF_BODY_FIELD["description"] in rendered
    # The mirrored schema is the shipped one, read back whole: the render
    # carried it from the mirror rather than from this repository.
    assert json.loads((root / "schemas" / f"{inspection.BRIEF_SCHEMA}.schema.json")
                      .read_text(encoding="utf-8")) == BRIEF_SCHEMA


def test_the_shipped_documents_are_the_ones_these_assertions_read():
    """The subject named once, so a reader can see nothing above was decided
    against a document this module wrote for itself."""
    assert (PROMPTS / PROSE_LAYER).is_file()
    assert (PROMPTS / ASSIST_TEMPLATE).is_file()
    assert (PROMPTS / PLANNER_TEMPLATE).is_file()
    assert (PROMPTS / INSPECTOR_TEMPLATE).is_file()
    assert filing.SKILL_PATH.is_file()
    assert (REPO_ROOT / "schemas" / "story.schema.json").is_file()
    assert (REPO_ROOT / "schemas" / f"{inspection.BRIEF_SCHEMA}.schema.json").is_file()
