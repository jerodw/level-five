"""What a stage is told about ending its turn, and about which tests to run
before it does.

Two instructions, both about the same moment — the end of a stage's turn — and
both of a kind no schema and no coordinator check can hold:

  * the implementer is told which tests to run before completing, and what runs
    the whole suite after it. It was told to run the whole suite in three
    separate places; at this repository's size that is a command of over ten
    minutes, and story-063's implementer twice ended its turn still holding
    one, having written none of its artifacts.
  * every stage that writes artifacts to the run directory is told that ending
    its turn is how the stage ends. Four prompts carry that sentence, and a
    rule stated four times is four statements that can drift apart. So the
    sentence is taken *out of one rendering* here and looked for in the others:
    one source, and a reworded copy reddens rather than passing as a paraphrase.

The shipped prompts are this module's **subject**, not an input to it, so it
reaches them the way the fixture-guidance module does and for the same reason:
a coordinator run against the shipped harness root, with each *rendered* prompt
read back out of the run directory. Rendered rather than templated, because
what a stage is handed is what it obeys — a phrase that is still a placeholder
at the moment the stage reads it instructs nobody.

No path here is rooted at a module-level repository-root name, so the live
artifact scan in `tests/test_baseline_honesty.py` does not report this module
and its grandfathered list does not grow.

Two places where what this module asserts differs from the wording of the
story that asked for it, both because the repository moved between the
planning and the writing:

  * the story asks that the implementer's rendering name "the stage that
    writes test-results.json" as one of the whole-suite runs. Since story-066
    that stage runs no suite at all: the coordinator runs the configured
    command as a subprocess once the stage's turn has ended. The rendering
    names it that way, so that is what is asserted — the run was moved from
    the stage to the coordinator, not narrowed. The rendering no longer states
    how many whole-suite runs follow the implementer, and what stands where
    that count was checked is the list itself: every declaration the workflow
    puts after the implementer has to be named in the passage, derived from
    the definition rather than written out here.
  * the story asks that the tester prompt still instruct the stage to run the
    full suite. story-066 deliberately removed that instruction, and
    `tests/test_coordinator_runs_the_suite.py` holds the prompt to its
    replacement. Nothing is restated here; what this module asserts is the part
    that is this story's own subject — that a whole-suite run still stands
    between the authoring stage and the story completing, and that the
    implementer's rendering says so.

Every absence asserted here carries a demonstration that the same check reports
when the text it looks for is present or removed, as the case may be: the
whole-suite scan is run again over a rendering with the removed directive
planted back into it, the selection count is run again over that same planted
rendering, the scan for the checks is run again over a rendering with the
passage naming them cut out, and the derivation that holds the passage to the
workflow's declarations is run again both over that cut and over a passage
with one stage's attribution reworded away.

Nothing here asserts that a stage *followed* either instruction. There is no
deterministic check for that, and this module does not build one that looks
like one.
"""
import pytest

import conftest
import harness_config
import story_coordinator
from test_changed_files_records import StageRunner
from test_tester_prompt_fixture_guidance import flat

#: The template whose rendering is the source of the turn-ending sentence. One
#: of the four that carry it, chosen because it is the prompt the sentence was
#: written into first; which one it is does not matter to the comparison, only
#: that the comparison has exactly one source.
SENTENCE_SOURCE_TEMPLATE = "story-verifier.md"

#: The template whose rendering carries the test-selection instruction.
IMPLEMENTER_TEMPLATE = "story-implementer.md"

#: The layer header that follows the implementer's stage layer. Used to bound
#: the cut that removes the passage naming the whole-suite checks, so the
#: control is anchored on the prompt's own structure rather than on a sentence
#: of the passage it is removing.
RUNTIME_LAYER = "[Runtime State Layer]"


# --------------------------------------------------------------------------
# The renderings, as a run hands them to the stages
# --------------------------------------------------------------------------


class Renderings:
    """One coordinator run's rendered prompts, keyed by prompt template.

    Keyed by template rather than by stage name because the templates are the
    subject: the stage that carries one is read off the workflow, so a workflow
    that renames or reorders its stages moves this with it rather than leaving
    it reading nothing.
    """

    def __init__(self, workflow: dict, run_dir, texts: dict[str, str],
                 test_command: str):
        self.workflow = workflow
        self.run_dir = run_dir
        self.texts = texts
        self.test_command = test_command

    def raw(self, template: str) -> str:
        assert template in self.texts, (template, sorted(self.texts))
        return self.texts[template]

    def prose(self, template: str) -> str:
        return flat(self.raw(template))

    @property
    def templates(self) -> list[str]:
        return sorted(self.texts)


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
        texts[stage["prompt"]] = written[0].read_text(encoding="utf-8")

    command = harness_config.load_config(target_root)["test_command"]
    return Renderings(workflow, runner.run_dir, texts, command)


# --------------------------------------------------------------------------
# One statement of the turn-ending rule, taken out of one rendering
# --------------------------------------------------------------------------


def turn_ending_sentence(renderings: Renderings, template: str) -> str:
    """The turn-ending sentence, read out of one rendering.

    Located by the run directory the sentence follows — a value the coordinator
    put there and this test knows independently — and ended at the colon that
    introduces the list of files. Nothing of the sentence itself is written
    here, which is the point: the other renderings are compared against what
    this one says, so four prompts cannot drift into four statements of one
    rule without the comparison reporting it.
    """
    prose = renderings.prose(template)
    anchor = f"{renderings.run_dir}."
    assert anchor in prose, "the rendering does not name the run directory"
    tail = prose[prose.index(anchor) + len(anchor):]
    return tail[:tail.index(":") + 1].strip()


@pytest.fixture
def sentence(renderings) -> str:
    return turn_ending_sentence(renderings, SENTENCE_SOURCE_TEMPLATE)


def test_the_sentence_taken_out_of_a_rendering_is_a_sentence(
    renderings, sentence,
):
    """The extraction, checked before anything is compared against it.

    An extraction that had silently come back with a fragment, an empty string
    or a run of the surrounding prose would make every comparison below pass or
    fail for a reason that has nothing to do with the four prompts agreeing.
    """
    assert sentence.endswith(":")
    assert 80 < len(sentence) < 400, sentence
    assert "{{" not in sentence and "}}" not in sentence
    assert str(renderings.run_dir) not in sentence
    # Whole words rather than a clause chopped at a column: the rendering is
    # flattened before the cut, so a sentence found here is a sentence.
    assert len(sentence.split()) > 10


def test_every_stage_that_writes_to_the_run_directory_carries_the_sentence(
    renderings, sentence,
):
    """All of them, derived from the workflow rather than listed here, so a
    stage added to the workflow is a stage this holds to the same rule."""
    assert len(renderings.templates) > 1, "one rendering cannot drift from itself"
    for template in renderings.templates:
        assert sentence in renderings.prose(template), template


def test_the_sentence_stands_in_the_same_place_in_every_rendering(
    renderings, sentence,
):
    """Directly after the sentence naming the run directory and before the list
    of files — one placement as well as one wording, so a reader of any of the
    four meets it at the same point."""
    for template in renderings.templates:
        assert f"{renderings.run_dir}. {sentence}" in renderings.prose(template), \
            template


def test_no_rendering_states_the_rule_more_than_once(renderings, sentence):
    """One statement per prompt as well as one wording across them: a prompt
    that said it twice would be two places a later edit could change one of."""
    for template in renderings.templates:
        assert renderings.prose(template).count(sentence) == 1, template


def test_the_placement_check_reports_a_rendering_without_the_sentence(
    renderings, sentence,
):
    """The control for the three checks above.

    The same checks over the same renderings with the sentence removed, where
    every one of them must report. Without this, a comparison that had drifted
    to an empty or ubiquitous string would pass while asserting nothing about
    whether the four prompts agree.
    """
    for template in renderings.templates:
        without = renderings.prose(template).replace(sentence, "", 1)
        assert sentence not in without, template
        assert f"{renderings.run_dir}. {sentence}" not in without, template
        assert without.count(sentence) == 0, template


# --------------------------------------------------------------------------
# Which tests the implementer is told to run
# --------------------------------------------------------------------------


#: Ways of directing a stage at the whole suite. The first is the wording the
#: prompt carried in each of the three places the fix removed it from; the
#: others are the two obvious rewrites of it, so a reinstatement that reached
#: for a synonym is reported too.
WHOLE_SUITE_DIRECTIVES = (
    "run the existing test suite",
    "run the full test suite",
    "run the whole test suite",
    "run the whole suite",
)

#: The selection the instruction is to name, and the tail every directive about
#: which tests to run before finishing ends with. Counted against each other
#: below rather than asserted separately: what matters is not that the
#: selection appears somewhere, but that no directive names a different one.
THE_SELECTION = "the tests your change touches"
THE_DIRECTIVE_TAIL = "before completing"

#: The whole-suite runs the rendering has to name, by the words it names each
#: of them with — one entry per run that follows the implementer, which for the
#: revert check means one entry per stage declaring it. Two of them are
#: coordinator subprocesses rather than a stage's own command since story-066,
#: and the rendering says so. No count is written beside this list and none is
#: asserted from it: what the passage is held to is the workflow's
#: declarations, below.
THE_CHECKS = (
    "revert check",
    "the tester's revert check",
    "the documenter's",
    "the stage that authors the validation",
    "clean-clone check",
)

#: The workflow declarations that put a whole-suite run after the implementer.
#: Each one the workflow declares from the implementer's stage onward has to be
#: named in the passage, and which words name it is decided by the two entries
#: below — a list in prose beside the declarations it is a list of is the pair
#: that goes stale, so here the pairing is checked rather than described.
WHOLE_SUITE_DECLARATIONS = ("revert_check", "suite_run", "clean_clone")

#: Of those, the one a stage declares about its own writing: a revert check
#: runs on the edits of the stage that declares it, only when that stage makes
#: one. So the passage has to name it once per declaring stage and say whose
#: edits each run reverts — the reader's own as "your edits", another stage's
#: by that stage's name — while `suite_run` and `clean_clone` put the whole
#: tree, the implementer's work in it, to the suite wherever they are declared
#: and are named once each by the words they carry below. Since story-106 a
#: second stage declares a revert check and since story-044 a third, which is
#: what makes the distinction load-bearing rather than academic: naming the
#: revert check once would leave the other declaring stages' runs unnamed.
DECLARED_ABOUT_THE_DECLARING_STAGE = ("revert_check",)

#: The words the passage names a declaration by when it is not one stage's own
#: — one per declaration key, looked for whichever stage carries it. A key
#: added to `WHOLE_SUITE_DECLARATIONS` and not here fails loudly as a missing
#: key rather than quietly as a declaration nothing looks for.
NAMED_BY_DECLARATION = {
    "suite_run": "the stage that authors the validation",
    "clean_clone": "clean-clone check",
}

#: How the passage names the reader's own revert check: the edits it reverts
#: are the reader's, so the prose says whose they are rather than naming a
#: stage at a reader who is standing in it.
THE_READERS_OWN = "your edits"


def whole_suite_directives(prose: str) -> list[str]:
    """Every direction to run the whole suite that a text still carries.

    A list rather than an assertion so the same statement can be made of a text
    that does carry one, which is what the control needs.
    """
    return [phrase for phrase in WHOLE_SUITE_DIRECTIVES if phrase in prose]


def checks_named(prose: str) -> list[str]:
    """Which of the whole-suite runs a text names. Likewise a list."""
    return [phrase for phrase in THE_CHECKS if phrase in prose]


def namings_required(workflow: dict) -> list[tuple[str, str, str]]:
    """Every whole-suite run the workflow puts after the implementer, as the
    kind of naming it takes, a label for it, and the words that name it.

    Derived from the definition rather than listed: a workflow that declared
    one more, or moved one to a stage that runs before the implementer, changes
    what the passage is required to say without anything here being edited.
    """
    stages = workflow["stages"]
    implementing = next(stage for stage in stages
                        if stage["prompt"] == IMPLEMENTER_TEMPLATE)
    order = [stage["name"] for stage in stages]
    required = []
    for stage in stages[order.index(implementing["name"]):]:
        for key in WHOLE_SUITE_DECLARATIONS:
            if key not in stage:
                continue
            label = f"{stage['name']}.{key}"
            if key not in DECLARED_ABOUT_THE_DECLARING_STAGE:
                required.append(
                    ("wherever it is declared", label, NAMED_BY_DECLARATION[key]))
            elif stage["name"] == implementing["name"]:
                required.append(("the reader's own", label, THE_READERS_OWN))
            else:
                required.append(("another stage's", label, f"{stage['name']}'s"))
    return required


def namings_missing_from(prose: str, workflow: dict) -> list[str]:
    """Which of those a text does not name. A list rather than an assertion so
    the same derivation can be made of a text that names none of them, which is
    what the controls need."""
    return [label for _, label, naming in namings_required(workflow)
            if naming not in prose]


def with_the_removed_directive(prose: str) -> str:
    """The same rendering with a whole-suite directive put back into it.

    Constructed here rather than recovered out of the commit graph: what the
    control needs is a text carrying the directive, and nothing about that
    needs a history to resolve.
    """
    planted = f"- {WHOLE_SUITE_DIRECTIVES[0]} locally {THE_DIRECTIVE_TAIL}, and"
    return f"{prose} {planted}"


def passage_bounds(raw: str, test_command: str) -> tuple[int, int]:
    """Where the passage about the whole suite begins and ends in a rendering.

    Bounded by the rendered test command at one end and the layer header that
    follows the stage layer at the other, so the span is anchored on the
    prompt's own structure rather than on a sentence of the passage itself — a
    control anchored on the text it is demonstrating the absence of would
    report whether the anchor was there rather than whether the check works.
    """
    start = raw.index(test_command) + len(test_command)
    end = raw.index(RUNTIME_LAYER)
    assert start < end, "the stage layer does not precede the runtime layer"
    return start, end


def the_checks_passage(raw: str, test_command: str) -> str:
    """That span, flattened and lowercased. The same bounds the cut below uses,
    so what is read and what is removed are one span of the prompt."""
    start, end = passage_bounds(raw, test_command)
    return flat(raw[start:end]).lower()


def without_the_checks_passage(renderings: Renderings) -> str:
    """The implementer's rendering with the passage naming the checks cut out."""
    raw = renderings.raw(IMPLEMENTER_TEMPLATE)
    start, end = passage_bounds(raw, renderings.test_command)
    return raw[:start] + "\n\n" + raw[end:]


@pytest.fixture
def implementer(renderings) -> str:
    """The implementer's rendering, flattened and lowercased.

    Lowercased because the same directive appears as a bullet and as an
    imperative, and the difference between them is a capital letter rather than
    anything about which tests are to be run.
    """
    return renderings.prose(IMPLEMENTER_TEMPLATE).lower()


@pytest.fixture
def passage(renderings) -> str:
    """The stage layer's passage about the whole suite, on its own.

    The subject of the derivation below is this span rather than the whole
    rendering: the schema the coordinator injects names a stage of its own for
    reasons that have nothing to do with which runs follow the implementer, and
    a derivation reading the whole prompt would take that mention as the
    passage having named a run it never names.
    """
    return the_checks_passage(renderings.raw(IMPLEMENTER_TEMPLATE),
                              renderings.test_command)


def test_the_rendering_directs_no_run_of_the_whole_suite(implementer):
    """The absence: nowhere in what the implementer is handed is it told to run
    all of it. Its control is the case below."""
    assert whole_suite_directives(implementer) == []


def test_that_scan_reports_the_directive_that_was_removed(implementer):
    """The control for the absence above, over the same rendering with the
    directive the fix removed planted back into it."""
    planted = with_the_removed_directive(implementer)
    assert whole_suite_directives(planted) == [WHOLE_SUITE_DIRECTIVES[0]]


def test_every_directive_about_which_tests_to_run_names_the_same_selection(
    implementer,
):
    """One instruction, not three that could disagree.

    Counted rather than searched for: a rendering that named the selection
    somewhere and directed a whole-suite run somewhere else would satisfy a
    search and is exactly what this story removed. Every directive ending in
    the tail names the same selection, so there is one answer to "which tests"
    however many times the prompt says it.
    """
    directives = implementer.count(THE_DIRECTIVE_TAIL)
    assert directives > 0, "the rendering directs no tests to be run at all"
    assert implementer.count(f"{THE_SELECTION} {THE_DIRECTIVE_TAIL}") == directives


def test_that_count_reports_a_directive_naming_a_different_selection(
    implementer,
):
    """The control for the count above: one directive with a different
    selection planted, and the two counts part company."""
    planted = with_the_removed_directive(implementer)
    directives = planted.count(THE_DIRECTIVE_TAIL)
    assert planted.count(f"{THE_SELECTION} {THE_DIRECTIVE_TAIL}") < directives


def test_the_rendering_names_the_test_command_once(renderings, implementer):
    """The one place the instruction is given with the command to give it: the
    stage layer. The command is read off the target's configuration rather than
    written here, so it is the command a run of that target would be handed."""
    assert implementer.count(renderings.test_command.lower()) == 1


def test_the_rendering_names_each_whole_suite_check_that_follows_it(implementer):
    """The runs of the whole suite that stand between this stage and the story
    completing, each named, so the narrowing above is legible as delegation
    rather than as coverage given up."""
    assert checks_named(implementer) == list(THE_CHECKS)


def test_that_scan_reports_a_rendering_with_the_passage_cut_out(
    renderings, implementer,
):
    """The control for the assertion above.

    The same scan over the same rendering with the passage naming the checks
    removed, where it must find none of them — and the turn-ending sentence,
    which lives elsewhere in the prompt, must survive the cut, so what the cut
    removed is what it was aimed at.
    """
    cut = flat(without_the_checks_passage(renderings)).lower()
    assert len(cut) < len(implementer)
    assert checks_named(cut) == []
    assert turn_ending_sentence(renderings, SENTENCE_SOURCE_TEMPLATE) in \
        flat(without_the_checks_passage(renderings))


def test_the_rendering_names_every_whole_suite_run_the_workflow_declares_after_it(
    renderings, passage,
):
    """The list in the prose, held to the declarations behind it.

    The prompt used to tell the implementer how many times the whole suite runs
    after it, and the count was held here against the declarations. The count
    is gone from the prose — a number written beside the list it counts is the
    pair that goes stale — so what is held here is the list itself: every
    declaration the workflow puts from the implementer's stage onward has to be
    named in the passage, so a workflow that gained or lost a coordinator-run
    whole-suite check reddens here instead of leaving the prompt naming a stale
    set.

    Which words name a declaration depends on whose work it puts to the suite —
    see `DECLARED_ABOUT_THE_DECLARING_STAGE` — and the second assertion is what
    keeps that rule from being a way of arriving at whatever the prose happens
    to say: all three kinds of naming occur under this deployment, so the rule
    is discriminating rather than applying to nothing. The third is why it
    matters, since the passage naming a revert check once would leave the other
    declaring stages' runs unnamed.
    """
    required = namings_required(renderings.workflow)
    assert namings_missing_from(passage, renderings.workflow) == []

    kinds = {kind for kind, _, _ in required}
    assert kinds == {"the reader's own", "another stage's",
                     "wherever it is declared"}

    per_stage = [label for kind, label, _ in required
                 if kind != "wherever it is declared"]
    assert len(per_stage) > 1, per_stage


def test_that_derivation_reports_a_passage_that_stopped_naming_one(
    renderings, passage,
):
    """The control for the derivation above, in both directions it can fail.

    First over the rendering with the whole passage cut out, where every run
    the workflow declares has to be reported — so the derivation is reading
    this span and not some other mention of the same words elsewhere in the
    prompt. Then over the same passage with one stage's attribution reworded
    into an anonymous one, where exactly that stage's run has to be reported
    and no other — so the derivation tells one declaring stage from another
    rather than being satisfied by any revert check being named at all.
    """
    cut = the_checks_passage(without_the_checks_passage(renderings),
                             renderings.test_command)
    assert cut.strip() == ""
    assert namings_missing_from(cut, renderings.workflow) == \
        [label for _, label, _ in namings_required(renderings.workflow)]

    kind, label, naming = next(
        entry for entry in namings_required(renderings.workflow)
        if entry[0] == "another stage's")
    anonymous = passage.replace(naming, "that stage's")
    assert anonymous != passage, (kind, naming)
    assert namings_missing_from(anonymous, renderings.workflow) == [label]


def test_the_whole_suite_still_runs_after_the_stage_that_authors_validation(
    renderings,
):
    """The narrowing did not remove a whole-suite run; it moved one.

    The stage that authors the validation is followed by a coordinator
    subprocess that runs the configured command over all of it and records the
    result, declared on that stage in the workflow. What the stage itself is
    told to do with a suite is `tests/test_coordinator_runs_the_suite.py`'s
    subject and is not restated here.
    """
    declaring = [stage for stage in renderings.workflow["stages"]
                 if "suite_run" in stage]
    assert len(declaring) == 1
    assert declaring[0]["suite_run"]["result"]
    assert (renderings.run_dir / declaring[0]["suite_run"]["result"]).is_file()
