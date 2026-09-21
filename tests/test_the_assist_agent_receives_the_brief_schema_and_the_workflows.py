"""Independent validation for the story that hands the assist agent the brief
schema file and the defined workflows, the way the Inspector is handed them.

Written from the story's acceptance criteria rather than from the
implementation. The subject is what `scripts/l5-assist` hands `claude`: the
launcher is driven with `execvp` intercepted, as the modules beside this one
drive it, and the prompt it would have appended is read rather than reasoned
about. What the launcher is supposed to carry — `schemas/story-brief.schema.json`
and the names under `workflows/` — is read as it ships, because the claim is
about this deployment's own schema and definitions reaching the session; a
fixture schema would say back only what this module wrote into it.

Every positive assertion here sits beside a control over a launcher that
renders with the prose partial alone — a mutant of the shipped launcher whose
context holds neither the schemas nor the candidates, which is what the
launcher was before this story. The same searches over that launcher's
prompt must report the schema absent and the workflow names absent, and the
literal the shared render puts in for a key its context lacks must stand
exactly where the shipped render carries the schema text and the candidate
block, so a green search over the shipped launcher is a fact about the
injection rather than about a search that would pass over anything. And a
harness root this module builds, carrying a workflow this
repository does not ship, is put through the real launcher: the invented
name reaches the prompt with no edit to the template, which is the property
the story was filed for.

Every absence asserted here carries its own demonstration that it can fail:
the harness-layer searches are run over the partial that does carry those
words and over a rendering of the assist prompt with the partial planted in
it, and the superseded wording the prompt no longer carries is held here as
literals the same searches report.

Nothing here invokes a model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import load_mutant, load_script

import brief_filing
import context_assembler
import harness_config
import workflow_selection

import test_inspection as producer

REPO_ROOT = Path(context_assembler.__file__).resolve().parents[1]

#: The launcher and the templates it renders, by the names the launcher and
#: the shared helper use for them.
ASSIST_LAUNCHER = "l5-assist"
ASSIST_TEMPLATE = "assist.md"
PROSE_LAYER = context_assembler.PROSE_LAYER
HARNESS_LAYER = "harness-layer.md"

#: The schema the story injects, resolved from the name the filing path loads
#: it by, so this module spells the schema's name nowhere of its own. Read as
#: it ships: the criterion is that *this* file reaches the session verbatim.
BRIEF_SCHEMA_NAME = brief_filing.BRIEF_SCHEMA
BRIEF_SCHEMA_PATH = REPO_ROOT / "schemas" / f"{BRIEF_SCHEMA_NAME}.schema.json"
BRIEF_SCHEMA_TEXT = BRIEF_SCHEMA_PATH.read_text(encoding="utf-8")

#: The placeholder the schema lands in, derived the way `schema_context`
#: derives it from the file name, and the placeholder the candidate block
#: lands in, which no helper names and the story therefore spells.
BRIEF_SCHEMA_KEY = BRIEF_SCHEMA_NAME.replace("-", "_") + "_schema"
BRIEF_SCHEMA_PLACEHOLDER = "{{" + BRIEF_SCHEMA_KEY + "}}"
CANDIDATES_KEY = "workflow_candidates"
CANDIDATES_PLACEHOLDER = "{{" + CANDIDATES_KEY + "}}"
PROSE_PLACEHOLDER = "{{prose_layer}}"
HARNESS_PLACEHOLDER = "{{harness_layer}}"

#: What the shared render puts in for a placeholder whose key its context
#: lacks, asked of the render itself rather than spelled here: the controls
#: below look for it standing where the shipped launcher's values stand.
MISSING = context_assembler.render("{{a_key_no_context_carries}}", {})
assert MISSING and not context_assembler.PLACEHOLDER.search(MISSING), MISSING


def framed(template: str, placeholder: str, value: str) -> str:
    """The paragraphs the template puts either side of `placeholder`, with
    `value` standing between them — what a render of that template carries
    at the placeholder's place when the placeholder takes `value`.

    The placeholder must sit as a paragraph of its own, so the frame is the
    neighbours it has in the template and not a guess at them.
    """
    paragraphs = template.split("\n\n")
    assert paragraphs.count(placeholder) == 1, (placeholder, paragraphs)
    at = paragraphs.index(placeholder)
    return "\n\n".join([paragraphs[at - 1], value, paragraphs[at + 1]])

#: What the harness-layer partial opens with and the line the criterion names.
#: Literals, because the criterion states them as text; the control below
#: shows both are what the shipped partial actually says.
HARNESS_HEADING = "[Harness Layer]"
HARNESS_OPENING_LINE = "All work must:"


def test_the_schema_this_module_reads_is_the_one_the_launcher_would_inject():
    """The derivation above is tied to the helper the launcher calls, so an
    assertion about the schema text is about the value the placeholder takes
    rather than about a file that happens to share its name."""
    context = context_assembler.schema_context(REPO_ROOT)

    assert BRIEF_SCHEMA_KEY in context, sorted(context)
    assert context[BRIEF_SCHEMA_KEY] == BRIEF_SCHEMA_TEXT
    # A schema that is empty or not a document would make "verbatim" cheap.
    assert json.loads(BRIEF_SCHEMA_TEXT)["required"], BRIEF_SCHEMA_PATH


# ==========================================================================
# The seam: what the launcher would have handed claude
# ==========================================================================


def launching(monkeypatch, *, harness_root: Path | None = None,
              module=None) -> list[str]:
    """`l5-assist` run with the exec intercepted, returning the argv it built.

    The launcher's last act replaces the process, so what it decided is read
    by standing in for that call. The environment variable it exports is put
    under `monkeypatch`'s control first, so the launcher's own assignment to
    it is reverted when the test ends. `harness_root` repoints the launcher
    at a root the caller owns; `module` substitutes a mutant of it.
    """
    recorded: dict = {}
    module = module or load_script(ASSIST_LAUNCHER, name="l5_assist_under_test")
    if harness_root is not None:
        monkeypatch.setattr(module, "HARNESS_ROOT", Path(harness_root))
    monkeypatch.setenv(module.HARNESS_ROOT_VARIABLE, "a value no launcher wrote")
    monkeypatch.setattr(module.os, "execvp",
                        lambda file, args: recorded.update(file=file,
                                                           args=list(args)))
    monkeypatch.setattr(module.sys, "argv", [ASSIST_LAUNCHER])
    module.main()
    assert recorded, "the launcher exec'd nothing"
    return recorded["args"]


def appended(args: list[str]) -> str:
    """The prompt an argument list hands to --append-system-prompt."""
    assert "--append-system-prompt" in args, args
    return args[args.index("--append-system-prompt") + 1]


@pytest.fixture
def shipped_prompt(monkeypatch) -> str:
    """What the shipped launcher, against this repository, hands the session."""
    return appended(launching(monkeypatch))


@pytest.fixture
def prose_only_launcher(tmp_path):
    """The shipped launcher with its context narrowed to the prose partial.

    A mutant rather than a second launcher written here: the two statements
    this story added are taken out and nothing else is touched, so what it
    renders is what the launcher rendered before this story. Each anchor must
    occur, so a launcher rewritten out from under this control fails as
    itself rather than as a control that silently mutated nothing.
    """
    return load_mutant(
        REPO_ROOT / "scripts" / ASSIST_LAUNCHER,
        [("context_assembler.schema_context(\n        HARNESS_ROOT\n    )", "{}"),
         ('    context["workflow_candidates"] = workflow_selection.candidate_block(\n'
          '        workflow_selection.candidates(HARNESS_ROOT)\n'
          '    )\n', "")],
        name="l5_assist_rendering_the_prose_partial_alone", tmp_path=tmp_path)


@pytest.fixture
def prose_only_prompt(monkeypatch, prose_only_launcher) -> str:
    """What the narrowed launcher, against this repository, hands the session.

    Repointed at this repository explicitly: the mutant lives under tmp_path,
    so the root it would resolve beside itself is not the harness.
    """
    return appended(launching(monkeypatch, harness_root=REPO_ROOT,
                              module=prose_only_launcher))


def test_the_narrowed_launcher_still_renders_the_prose_partial(
        prose_only_prompt):
    """The control launcher is the launcher minus this story and nothing more:
    the partial it kept still resolves, so an absence reported over it is the
    injection's absence rather than a launcher that rendered nothing."""
    partial = context_assembler.resolved_partial(REPO_ROOT, PROSE_LAYER, {})
    assert partial, REPO_ROOT

    assert partial in prose_only_prompt
    assert PROSE_PLACEHOLDER not in prose_only_prompt


# ==========================================================================
# The brief schema reaches the session verbatim
# ==========================================================================


def test_the_appended_prompt_carries_the_brief_schema_verbatim(shipped_prompt):
    assert BRIEF_SCHEMA_TEXT in shipped_prompt
    # And carries it at the placeholder's place, between the neighbours the
    # template gives it, rather than anywhere at all.
    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)
    assert framed(template, BRIEF_SCHEMA_PLACEHOLDER,
                  BRIEF_SCHEMA_TEXT) in shipped_prompt


def test_the_same_search_reports_the_schema_absent_from_a_prose_only_render(
        shipped_prompt, prose_only_prompt):
    """The control: the launcher that does not put the schema in its context
    hands the session a prompt the same search reports the schema absent
    from — and, so the absence is the injection's and not a render that
    dropped the paragraph, the literal the render puts in for a missing key
    stands between the same neighbours the schema stands between in the
    shipped prompt."""
    assert BRIEF_SCHEMA_TEXT not in prose_only_prompt

    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)
    stood_in = framed(template, BRIEF_SCHEMA_PLACEHOLDER, MISSING)
    assert stood_in in prose_only_prompt
    assert stood_in not in shipped_prompt


# ==========================================================================
# Every defined workflow reaches the session by name
# ==========================================================================


def defined_workflows(harness_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Every definition under a root's workflows/, by the name a brief names."""
    names = harness_config.workflow_names(harness_root)
    assert names, harness_root
    return names


def test_the_appended_prompt_names_every_workflow_the_harness_defines(
        shipped_prompt):
    """Each name under workflows/, and the block the selector prompt carries
    them in, so the session is given the same list the filing accepts."""
    for name in defined_workflows():
        assert name in shipped_prompt, name

    block = shipped_candidate_block()
    assert block in shipped_prompt
    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)
    assert framed(template, CANDIDATES_PLACEHOLDER, block) in shipped_prompt


def shipped_candidate_block() -> str:
    """The block the launcher renders the candidates as, for this repository."""
    block = workflow_selection.candidate_block(
        workflow_selection.candidates(REPO_ROOT))
    assert block, REPO_ROOT
    return block


def test_the_same_searches_report_every_name_absent_from_a_prose_only_render(
        shipped_prompt, prose_only_prompt):
    """The control: the assist template names no workflow of its own, so a
    launcher that does not put the candidates in its context hands the
    session none of the names — and the literal the render puts in for a
    missing key stands between the neighbours the candidate block stands
    between in the shipped prompt, so the names are absent because nothing
    filled the place and not because the place was lost."""
    for name in defined_workflows():
        assert name not in prose_only_prompt, name

    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)
    stood_in = framed(template, CANDIDATES_PLACEHOLDER, MISSING)
    assert stood_in in prose_only_prompt
    assert stood_in not in shipped_prompt


@pytest.fixture
def root_defining_an_invented_workflow(tmp_path) -> tuple[Path, str, str]:
    """A harness root whose one definition is one this repository does not
    ship, carrying the shipped assist template and prose partial unchanged.

    Built through the mirror the inspection tests already provide, which
    links the shipped schemas in and writes the definitions it is handed, so
    the only workflow this root defines is the invented one.
    """
    invented = "zzz-a-workflow-only-this-test-defines"
    purpose = "the one purpose this test wrote into its definition"
    root = producer.harness_mirror(tmp_path, workflows={invented: purpose},
                                   name="invented-workflow-harness")
    for name in (ASSIST_TEMPLATE, PROSE_LAYER):
        (root / "prompts" / name).write_text(
            context_assembler.load_template(REPO_ROOT, name), encoding="utf-8")
    return root, invented, purpose


def test_a_definition_added_to_the_harness_reaches_the_prompt_with_no_edit(
        monkeypatch, root_defining_an_invented_workflow):
    """The property the story was filed for: the names come from the
    definitions rather than from the prompt, so a workflow added to the
    harness reaches the assist agent without the template being touched.

    The same launcher, against a root defining only an invented workflow,
    hands the session that name and that purpose — and none of the names
    this repository ships, which that root does not define.
    """
    root, invented, purpose = root_defining_an_invented_workflow

    prompt = appended(launching(monkeypatch, harness_root=root))

    assert invented in prompt
    assert purpose in prompt
    for shipped in defined_workflows():
        assert shipped not in prompt, shipped
    assert context_assembler.PLACEHOLDER.search(prompt) is None, prompt


# ==========================================================================
# Nothing is left unresolved, and the template carries exactly what it should
# ==========================================================================


def test_the_appended_prompt_matches_no_placeholder(shipped_prompt):
    assert context_assembler.PLACEHOLDER.search(shipped_prompt) is None


def test_the_prose_only_render_differs_from_the_shipped_one_exactly_at_the_two_injections(
        shipped_prompt, prose_only_prompt):
    """The control, and a bound on it: the shared render leaves no placeholder
    standing for a key it lacks, so the narrowed launcher's prompt is the
    shipped prompt with the schema text and the candidate block each replaced
    by the missing-key literal — and nothing else differs, so the story added
    those two values to what the session is handed and no other."""
    assert prose_only_prompt != shipped_prompt

    narrowed = (shipped_prompt
                .replace(BRIEF_SCHEMA_TEXT, MISSING)
                .replace(shipped_candidate_block(), MISSING))
    assert narrowed == prose_only_prompt
    # Each replacement took a place: without either, the equality is lost.
    assert shipped_prompt.replace(BRIEF_SCHEMA_TEXT, MISSING) != prose_only_prompt
    assert shipped_prompt.replace(shipped_candidate_block(), MISSING) != prose_only_prompt
    assert context_assembler.PLACEHOLDER.search(prose_only_prompt) is None


def test_the_template_carries_the_three_placeholders_and_not_the_harness_one():
    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)
    carried = set(context_assembler.PLACEHOLDER.findall(template))

    assert carried == {BRIEF_SCHEMA_KEY, CANDIDATES_KEY, "prose_layer"}, carried
    assert HARNESS_PLACEHOLDER not in template


def test_the_placeholders_are_spelled_as_the_inspector_prompt_spells_them():
    """The story brings the assist prompt up to the Inspector's shape, so the
    two names are the Inspector's own rather than two spellings of one
    thing that a renamed helper would then have to fix twice."""
    inspector = context_assembler.load_template(REPO_ROOT, "inspector.md")

    assert BRIEF_SCHEMA_PLACEHOLDER in inspector
    assert CANDIDATES_PLACEHOLDER in inspector


# ==========================================================================
# None of the harness-layer partial reaches the session
# ==========================================================================


def carries_the_harness_layer(prompt: str) -> bool:
    """Whether a prompt carries either mark of the harness-layer partial.

    One predicate, so the control below puts the same question to text that
    does carry it.
    """
    return HARNESS_HEADING in prompt or HARNESS_OPENING_LINE in prompt


def test_the_appended_prompt_carries_none_of_the_harness_layer(shipped_prompt):
    """The assist agent mutates no tree and is deliberately given no
    blocked-path block; widening its context to the schema and the
    candidates did not widen it to that."""
    assert not carries_the_harness_layer(shipped_prompt)
    assert HARNESS_PLACEHOLDER not in shipped_prompt


def test_the_same_predicate_reports_the_partial_and_a_prompt_it_is_planted_in(
        shipped_prompt):
    """The control for the absence above.

    The shipped partial carries both marks, so the predicate is looking for
    words something spells; and the shipped prompt with that partial planted
    in it is reported, so an empty result over the prompt as handed is the
    launcher's reach rather than a predicate that has stopped seeing.
    """
    partial = context_assembler.load_template(REPO_ROOT, HARNESS_LAYER)
    assert HARNESS_HEADING in partial
    assert HARNESS_OPENING_LINE in partial
    assert carries_the_harness_layer(partial)

    planted = shipped_prompt + "\n" + partial
    assert carries_the_harness_layer(planted)


# ==========================================================================
# The prompt's own sentence no longer restates what the schema now carries
# ==========================================================================


def collapsed(text: str) -> str:
    """One line, so a phrase is found across a line break."""
    return " ".join(text.split())


#: The wording the trimmed sentence gave up, carried here as literals because
#: what the prompt no longer says is not something the prompt can be asked
#: for. Each is what the story's fourth criterion names: the workflow clause,
#: the effort enumeration and the category.
SUPERSEDED = (
    "named as the workflow definitions name themselves",
    "effort as S, M or L",
    "a category;",
)


def carries_paragraph(text: str) -> str:
    """The paragraph of a prompt that opens 'A brief carries', collapsed."""
    opens = "A brief carries"
    paragraphs = [collapsed(one) for one in text.split("\n\n")]
    found = [one for one in paragraphs if one.startswith(opens)]
    assert len(found) == 1, found
    return found[0]


def test_the_sentence_describing_a_brief_no_longer_enumerates_what_the_schema_carries():
    template = context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE)
    sentence = carries_paragraph(template)

    for wording in SUPERSEDED:
        assert wording not in sentence, wording
    # It points at the injected shape instead.
    assert f"schemas/{BRIEF_SCHEMA_NAME}.schema.json" in sentence


#: The paragraph as it stood in prompts/assist.md before this story, carried
#: here verbatim rather than resolved out of this repository's history, so
#: the control below does not move when the history does. It is what the
#: assertion above would have been run over before the trimming.
PARAGRAPH_BEFORE_THIS_STORY = """\
A brief carries a title stating the behaviour the finished work produces,
in the present tense as though it already held — that holds for a defect
as much as for anything else, and the fault it starts from is said in the
body's opening line rather than in the title; a short
kebab-case slug naming the work itself rather than the fix or the file;
a body making the case, opening with one to three paragraphs of plain
English — what is wrong or wanted, what the work does about it, and what it
deliberately leaves alone — that a developer who does not know the harness
intimately can follow, with the paths, stage names and field names kept for
the paragraphs after them, and with its evidence cited as file:line wherever
the brief is about code that exists; a category; a severity saying how much
the work matters, with confidence — how sure you are of the judgement the
brief makes — as a separate axis, and effort as S, M or L; and the
workflow it should be planned under, named as the workflow definitions
name themselves. Optionally it carries the bare repository-relative paths
it is about — bare, because line-level evidence belongs in the body — and
what a story planned from it should deliberately leave alone.
schemas/story-brief.schema.json is the shape and says why each part of it
is where it is.
"""


def test_the_same_searches_report_the_superseded_sentence():
    """The control: the same paragraph search and the same phrase searches,
    over the paragraph as it stood before this story, report each phrase —
    so the absence above is the trimming and not a search for words nothing
    ever spelled, nor a paragraph finder that finds nothing."""
    sentence = carries_paragraph(PARAGRAPH_BEFORE_THIS_STORY)
    assert sentence == collapsed(PARAGRAPH_BEFORE_THIS_STORY)

    for wording in SUPERSEDED:
        assert wording in sentence, wording
    # The trimmed paragraph kept what the story said it would keep, so the
    # literal above and the shipped paragraph share their opening clause.
    shipped = carries_paragraph(
        context_assembler.load_template(REPO_ROOT, ASSIST_TEMPLATE))
    assert shipped.split(";")[0] == sentence.split(";")[0]


# ==========================================================================
# The launcher's own contract is what it was
# ==========================================================================


def test_the_launcher_still_exports_the_root_and_loads_the_plugin(monkeypatch):
    """The widening changed the render and nothing around it."""
    module = load_script(ASSIST_LAUNCHER, name="l5_assist_under_test")
    args = launching(monkeypatch, module=module)

    assert os.environ[module.HARNESS_ROOT_VARIABLE] == str(module.HARNESS_ROOT)
    assert "--plugin-dir" in args, args
    assert args[args.index("--plugin-dir") + 1] == str(module.PLUGIN_DIR)
