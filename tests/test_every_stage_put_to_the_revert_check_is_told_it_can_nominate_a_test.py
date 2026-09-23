"""Every stage whose work can be put to the revert check is told it can
nominate a single test, and told what a nomination is worth to it.

The check that decides whether a stage's edit outside its governed paths was
needed re-runs the whole suite, which at this repository's size is a command of
over ten minutes. A stage can have that decided in seconds instead by naming
one test that passes on the tree it is leaving and fails once the edit is
reverted. Whether a stage can do that is decided by the workflow — every stage
carrying a revert-check declaration can — while whether a stage *knows* it can
is decided by its prompt, and the two drifted apart: the declaration reached
three stages and the guidance reached one. This module holds them together, so
a stage that gains the declaration and not the guidance reddens here.

The shipped prompts and the shipped workflow are this module's **subject**, not
inputs to it. An assertion about what this harness hands its stages has to
reach what it ships, and the standing rule in `tests/test_baseline_honesty.py`
— a test reads a live harness artifact only when that artifact is what it is
about — is satisfied rather than bent by that. They are reached the way a run
reaches them: a coordinator run against the shipped harness root, with each
*rendered* prompt read back out of the run directory. Rendered rather than
templated, because what a stage is handed is what it obeys.

No stage is named here and no governed prefix is written here. Which stages are
held to the guidance is derived from the declarations in the workflow, and
which of those are confined to one place rather than merely watched is derived
the same way — so a workflow that renames a stage, or moves the declaration to
another, moves this module with it instead of leaving it reading nothing.

What is read of each rendering is the part the prompt itself says, down to
where the coordinator begins injecting the run's own state. A run's state can
quote the guidance — the story that asked for this work quotes it, and so does
the summary of the stage that wrote it — and a check that read the whole
rendering would take a quotation for the instruction and report a stage told
something it was never told.

No path here is rooted at a module-level repository-root name, so the live
artifact scan does not report this module and its grandfathered list does not
grow.

Every absence asserted here carries a demonstration that it can fail. "The
guidance is present" is a positive assertion and needs none; "the stage that is
not confined is not told what a nomination is worth under a confinement" and
"no placeholder is left unresolved" are checks over text, which pass just as
happily once they have stopped reading anything, so each is run again over a
rendering carrying the thing it looks for — planted into the rendering and read
back out by the same reader the absence used, because a plant made into the
already extracted text would demonstrate string concatenation and would go on
passing over the empty string that reader returns when it finds no paragraph at
all. The scan for the guidance itself is run again over each
rendering with the guidance paragraph cut out, where it must report every
element — and the cut renderings still carry the field's name, injected with
the schema, so a scan that had drifted to searching the whole prompt for that
name would be caught there rather than passing.

Nothing here asserts that a stage *nominated* a test. Whether a nomination was
available to be made is not a deterministic property of a run, and this module
does not build a check that looks like one.
"""
import pytest

import conftest
import story_coordinator
from test_changed_files_records import StageRunner
from test_tester_prompt_fixture_guidance import flat

#: The declaration that puts a stage's edits outside its governed paths to the
#: check. Every stage carrying it is a stage the guidance has to reach.
REVERT_CHECK = "revert_check"

#: The declaration that confines a stage to the paths it names. A stage
#: carrying it has its governed edit undone whichever way the check lands, so
#: what a nomination is worth to it differs from what it is worth to a stage
#: that is merely watched — and the two are told different things below.
CONFINEMENT = "may_only_change"

#: The words the guidance paragraph opens with, in every prompt that carries
#: it. Used to locate the paragraph rather than to assert anything: the
#: elements the paragraph has to carry are asserted inside it, so that a phrase
#: the prompt happens to use elsewhere cannot stand in for the guidance.
THE_OPENER = "can often be answered by one test"

#: The field a nomination is made in. Named in the guidance because a stage
#: cannot make one without knowing where to write it.
THE_FIELD = "test_that_fails_without_this_change"

#: What the guidance has to say a nomination must satisfy and what becomes of
#: it, in the words every prompt carrying the guidance says it in.
WHAT_A_NOMINATION_MUST_SATISFY = (
    "a single test that passes on the tree you are leaving and fails once "
    "that edit is reverted",
    "takes pass-then-fail as its answer",
    "falls through to the whole suite",
    "costs at most one extra selector run",
)

#: The pointer at the one statement of the contract. The schema the coordinator
#: injects into the same prompt says what a nomination must satisfy; the
#: paragraph points at it rather than restating it, because one contract
#: written twice is two statements that can drift.
THE_CONTRACT_IS_POINTED_AT = "says what a nomination must satisfy and what it is worth"

#: What a nomination is worth to a stage confined to one place: the decision
#: without the whole-suite run, and a faster hand-off where the suite did need
#: the edit. Not permission for the edit to stand, which is the next entry.
WHAT_IT_IS_WORTH_UNDER_A_CONFINEMENT = (
    "the decision in seconds rather than a whole-suite run",
    "a faster hand-off to the stage that owns the work",
)

#: The promise a confined stage's guidance has to withhold, stated as the
#: prompts withhold it — by saying so. A stage that is watched rather than
#: confined keeps its edit when the check finds it was needed, so this sentence
#: belongs only to the confined, and the case below asserts it is absent from
#: the other's guidance with a control that plants it.
NO_PERMISSION_UNDER_A_CONFINEMENT = "does not let the edit stand"

#: A placeholder the renderer would have filled had it known it was there.
#: Planted by the control below to show that the scan for an unresolved
#: placeholder reports one when there is one to report.
A_PLACEHOLDER = "{{run_dir}}"

#: The layer header that ends what the prompt itself says and begins what the
#: coordinator injects about this particular run. Everything looked for below
#: is looked for above it: a run's own state can quote the guidance — a story
#: describing this work does, and so does the summary of the stage that wrote
#: it — and a scan that read the whole rendering would take that quotation for
#: the instruction and report a stage told something it was never told.
RUNTIME_LAYER = "[Runtime State Layer]"


# --------------------------------------------------------------------------
# The renderings, as a run hands them to the stages
# --------------------------------------------------------------------------


class Renderings:
    """One coordinator run's rendered prompts, keyed by stage name."""

    def __init__(self, workflow: dict, texts: dict[str, str]):
        self.workflow = workflow
        self.texts = texts

    def stage(self, name: str) -> dict:
        return next(stage for stage in self.workflow["stages"]
                    if stage["name"] == name)

    def declaring(self, key: str) -> list[str]:
        """The stages whose definition carries `key`, in the workflow's order."""
        return [stage["name"] for stage in self.workflow["stages"]
                if key in stage]

    def raw(self, name: str) -> str:
        assert name in self.texts, (name, sorted(self.texts))
        return self.texts[name]


@pytest.fixture
def renderings(target_root, harness_root) -> Renderings:
    """Every stage's prompt as a run of the shipped harness rendered it."""
    runner = StageRunner(target_root)
    code = story_coordinator.run_story(
        "story-001", harness_root, target_root, runner)
    assert code == 0, "the run has to reach every stage for there to be prompts"

    workflow = conftest.shipped_workflow(harness_root)
    texts = {}
    for stage in workflow["stages"]:
        written = sorted(runner.run_dir.glob(f"prompt-{stage['name']}-*.md"))
        assert written, f"the run left no rendered prompt for {stage['name']}"
        texts[stage["name"]] = written[0].read_text(encoding="utf-8")
    return Renderings(workflow, texts)


# --------------------------------------------------------------------------
# Finding the guidance, and saying what it is missing
# --------------------------------------------------------------------------


#: Every element the guidance has to carry, wherever it is carried. The opener
#: is among them so that a rendering which lost the paragraph entirely is
#: reported as having lost the guidance rather than as having lost one phrase.
THE_GUIDANCE = (
    (THE_OPENER, THE_FIELD, THE_CONTRACT_IS_POINTED_AT)
    + WHAT_A_NOMINATION_MUST_SATISFY
)


def what_the_prompt_itself_says(rendered: str) -> str:
    """A rendering down to where the run's own state begins."""
    return rendered[:rendered.index(RUNTIME_LAYER)]


def guidance_paragraph(rendered: str) -> str:
    """The nomination paragraph of a rendering, flattened, or an empty string.

    A paragraph rather than the whole prompt, because the prompt carries the
    field's name elsewhere: the changed-files schema is injected into every one
    of these renderings and describes the field there. A scan over the whole
    text would find that description and report the guidance present in a
    prompt that never gives it.
    """
    found = [flat(block)
             for block in what_the_prompt_itself_says(rendered).split("\n\n")
             if THE_OPENER in flat(block)]
    assert len(found) < 2, "the rendering opens the guidance more than once"
    return found[0] if found else ""


def guidance_missing_from(rendered: str) -> list[str]:
    """Which elements of the guidance a rendering does not give.

    A list rather than an assertion, so the same statement can be made of a
    rendering that gives none of them — which is what the control needs.
    """
    paragraph = guidance_paragraph(rendered)
    return [element for element in THE_GUIDANCE if element not in paragraph]


def with_planted_in_the_guidance(rendered: str, sentence: str) -> str:
    """The same rendering with `sentence` written into its guidance paragraph.

    Planted into the rendering and read back out with `guidance_paragraph`, so
    that a control below demonstrates this module's own reader reporting the
    thing it is meant to catch. A control that planted into the already
    extracted paragraph would assert only that Python concatenated two strings,
    which stays true after the reader has stopped finding any paragraph at all
    — and `guidance_paragraph` returns an empty string in exactly that case, so
    the absence and its control would both pass over nothing.
    """
    said = what_the_prompt_itself_says(rendered)
    blocks = said.split("\n\n")
    planted = [f"{block} {sentence}" if THE_OPENER in flat(block) else block
               for block in blocks]
    assert planted != blocks, "the rendering has no guidance paragraph to plant in"
    return "\n\n".join(planted) + rendered[len(said):]


def without_the_guidance(rendered: str) -> str:
    """The same rendering with the guidance paragraph cut out of it.

    Cut at the paragraph the rendering's own blank lines bound, so the control
    removes the guidance and leaves every other thing the prompt says — the
    injected schema that describes the field among them.
    """
    said = what_the_prompt_itself_says(rendered)
    kept = [block for block in said.split("\n\n") if THE_OPENER not in flat(block)]
    return "\n\n".join(kept) + rendered[len(said):]


# --------------------------------------------------------------------------
# Who has to be told
# --------------------------------------------------------------------------


def test_the_workflow_declares_the_check_on_more_than_one_stage(renderings):
    """Read first, because everything below is a statement about a derived set
    and a derived set can be empty. More than one stage carries the
    declaration, so the cases below are held to a set the workflow really has
    and not to nothing."""
    declaring = renderings.declaring(REVERT_CHECK)
    assert len(declaring) > 1, declaring
    for name in declaring:
        assert renderings.raw(name), name


def test_every_stage_put_to_the_check_is_told_it_can_nominate_a_test(renderings):
    """The story's subject: the declaration and the guidance reach the same
    stages. A workflow that declares the check on a stage whose prompt says
    nothing about nominating reddens here, which is the drift this module
    exists to stop recurring."""
    for name in renderings.declaring(REVERT_CHECK):
        assert guidance_missing_from(renderings.raw(name)) == [], name


def test_that_scan_reports_a_rendering_with_the_guidance_cut_out(renderings):
    """The control for the case above.

    The same scan over the same renderings with the guidance paragraph
    removed, where every element must be reported. The cut renderings still
    carry the field's name — the schema that describes it is injected into each
    of them — so a scan that had drifted into searching the whole prompt for
    that name would pass the case above and fail here.
    """
    for name in renderings.declaring(REVERT_CHECK):
        stripped = without_the_guidance(renderings.raw(name))
        assert len(stripped) < len(renderings.raw(name)), name
        assert THE_FIELD in stripped, name
        assert guidance_missing_from(stripped) == list(THE_GUIDANCE), name


def test_the_guidance_leaves_no_placeholder_unresolved(renderings):
    """The absence: what a stage is handed has nothing left in it for the
    renderer to have filled. Its control is the case beneath it, which plants a
    placeholder into the rendering and reads the paragraph back out with the
    same reader — so an empty result here is the renderer having resolved them
    rather than the reader having stopped finding the paragraph."""
    for name in renderings.declaring(REVERT_CHECK):
        paragraph = guidance_paragraph(renderings.raw(name))
        assert "{{" not in paragraph and "}}" not in paragraph, name


def test_that_check_reports_a_rendering_with_a_placeholder_left_in(renderings):
    """The control for the absence above.

    A placeholder planted into each rendering's guidance paragraph, the
    paragraph re-read out of the planted rendering by the same reader, and the
    placeholder reported there. Planted into the rendering rather than into the
    extracted text, so what is demonstrated is that the reader still reaches
    the paragraph and the check still sees what is in it.
    """
    for name in renderings.declaring(REVERT_CHECK):
        planted = with_planted_in_the_guidance(renderings.raw(name), A_PLACEHOLDER)
        paragraph = guidance_paragraph(planted)
        assert "{{" in paragraph and "}}" in paragraph, name


# --------------------------------------------------------------------------
# What each of them is told it is worth
# --------------------------------------------------------------------------


def watched_stages(renderings) -> list[str]:
    """The stages put to the check without being confined to one place.

    Derived rather than named, and asserted non-empty here so that the case
    reading it and the control beneath it are both held to a set the workflow
    really has: a statement about every member of an empty set is no statement.
    """
    watched = [name for name in renderings.declaring(REVERT_CHECK)
               if CONFINEMENT not in renderings.stage(name)]
    assert watched, "every stage put to the check is confined"
    return watched


def test_a_confined_stage_is_told_what_a_nomination_buys_it(renderings):
    """A stage confined to one place has its governed edit undone whichever way
    the check lands, so what a nomination buys it is the decision without the
    whole-suite run, and the faster hand-off of work the suite did need. Both
    halves are stated, and the guidance says plainly that the edit does not
    stand — a paragraph that stopped short there would leave a confined stage
    reading a promise it does not get."""
    confined = [name for name in renderings.declaring(REVERT_CHECK)
                if CONFINEMENT in renderings.stage(name)]
    assert confined, "no stage is both put to the check and confined"

    for name in confined:
        paragraph = guidance_paragraph(renderings.raw(name))
        for phrase in WHAT_IT_IS_WORTH_UNDER_A_CONFINEMENT:
            assert phrase in paragraph, (name, phrase)
        assert NO_PERMISSION_UNDER_A_CONFINEMENT in paragraph, name


def test_the_confined_stages_are_handed_one_statement_of_it(renderings):
    """One wording across them rather than one rule written once per stage: two
    statements of the same bargain are two things a later edit can change one
    of, which is how the guidance came to reach one stage and not three."""
    confined = [name for name in renderings.declaring(REVERT_CHECK)
                if CONFINEMENT in renderings.stage(name)]
    assert len(confined) > 1, "one statement cannot drift from itself"
    written = {guidance_paragraph(renderings.raw(name)) for name in confined}
    assert len(written) == 1, sorted(len(text) for text in written)


def test_a_stage_that_is_watched_rather_than_confined_is_told_no_such_thing(
    renderings,
):
    """The absence, over the other side of the same derivation.

    A stage put to the check without being confined keeps its edit where the
    check finds it was needed, so its guidance must not carry the confined
    stages' sentence withholding that — the sentence would be false of it —
    and must not carry what a nomination is worth under a confinement either.
    The control is the case beneath, which plants each of those into the
    rendering and reads the paragraph back out with the same reader.
    """
    for name in watched_stages(renderings):
        paragraph = guidance_paragraph(renderings.raw(name))
        assert NO_PERMISSION_UNDER_A_CONFINEMENT not in paragraph, name
        for phrase in WHAT_IT_IS_WORTH_UNDER_A_CONFINEMENT:
            assert phrase not in paragraph, (name, phrase)


def test_that_check_reports_a_watched_stage_told_a_confined_stages_bargain(
    renderings,
):
    """The control for the absences above.

    Each sentence the watched stage's guidance must not carry, planted one at a
    time into that stage's rendering, the paragraph re-read out of the planted
    rendering by the same reader, and the sentence reported there. Every
    absence asserted above gets its own plant, including both halves of what a
    nomination is worth under a confinement, so none of them is vouched for by
    a demonstration of some other phrase.
    """
    sentences = (NO_PERMISSION_UNDER_A_CONFINEMENT,) \
        + WHAT_IT_IS_WORTH_UNDER_A_CONFINEMENT
    for name in watched_stages(renderings):
        for sentence in sentences:
            planted = with_planted_in_the_guidance(
                renderings.raw(name), f"{sentence}.")
            assert sentence in guidance_paragraph(planted), (name, sentence)
