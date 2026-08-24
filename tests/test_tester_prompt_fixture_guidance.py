"""The rendered tester prompt tells a tester where an assertion's inputs come
from, and reconciles that with the rule it already carries.

The shipped prompt is this module's **subject**, not an input to it. An
assertion about what this harness ships has to reach what it ships, and the
standing rule in `tests/test_baseline_honesty.py` — a test reads a live harness
artifact only when that artifact is what it is about — is satisfied rather than
bent by that. It is reached the way a run reaches it: a coordinator run against
the shipped harness root, and the *rendered* prompt read back out of the run
directory. That is stronger than reading the template, because "carries the
guidance" and "renders with nothing left unresolved" are properties of what a
stage is actually handed.

No path here is rooted at a module-level repository-root name, so the live
artifact scan does not report this module and the grandfathered list it
maintains does not grow. That is one of the scan's stated limits rather than a
route around it: the scan reads module-level path joins, and a run driven
through the `harness_root` fixture is the suite's established way of naming the
shipped harness as the subject of a run.

Every absence asserted here carries a demonstration that it can fail. "The
guidance is present" is a positive assertion and needs none, but "no
placeholder is left unresolved" and "the two rules are not written as a
contradiction" are checks over text, and a check over text passes just as
happily when it has stopped reading anything — so each is run again over a
rendering with the guidance stripped out, where it must report.

Nothing here asserts that a tester *followed* the guidance. There is no
deterministic way to check that, and this module does not construct a test that
appears to.
"""
import json

import pytest

import story_coordinator
from test_changed_files_records import StageRunner

#: The tester's template, named because it is the subject. The stage that
#: carries it is derived from the workflow rather than written out, so a
#: workflow that renames or moves the stage reddens here rather than silently
#: leaving this module reading nothing.
TESTER_TEMPLATE = "story-tester.md"

#: The question the guidance is built around, and the two halves of the
#: distinction it draws. Phrases rather than sentences, so rewording the prose
#: around them does not redden this while dropping one of them does.
THE_QUESTION = "is the shipped artifact the subject of this assertion"

#: The five live harness artifacts the guidance has to name, because a rule
#: that names four leaves the fifth looking permitted.
THE_LIVE_ARTIFACTS = (
    "shipped workflow",
    "execution rules",
    "configuration",
    "prompt templates",
    "schemas",
)

#: The instruction the question exists to lead to.
THE_INSTRUCTION = "build a fixture"

#: The idioms the guidance points at, so it names an existing practice rather
#: than describing a principle a tester would have to invent an instance of.
THE_IDIOMS = (
    "mirrored harness root",
    "target repository built under a temporary directory",
    "probe workflow derived from the shipped one",
)

#: The reconciliation: the rule that already stands, and the statement that a
#: fixture satisfies it rather than reversing it.
THE_STANDING_RULE = "no stage name"
THE_RECONCILIATION = "does not reverse the rule"


def stage_carrying_the_template(harness_root) -> str:
    workflow = json.loads(
        (harness_root / "workflows" / "story-workflow.json").read_text(
            encoding="utf-8"))
    return next(stage["name"] for stage in workflow["stages"]
                if stage["prompt"] == TESTER_TEMPLATE)


@pytest.fixture
def rendered(target_root, harness_root) -> str:
    """The tester's prompt as a run actually handed it to the stage."""
    runner = StageRunner(target_root)
    code = story_coordinator.run_story(
        "story-001", harness_root, target_root, runner)
    assert code == 0, "the run has to reach the tester for there to be a prompt"

    stage = stage_carrying_the_template(harness_root)
    written = sorted(runner.run_dir.glob(f"prompt-{stage}-*.md"))
    assert written, "the run left no rendered tester prompt to read"
    return written[0].read_text(encoding="utf-8")


def flat(text: str) -> str:
    """One rendering with its line wrapping taken out.

    The template is wrapped to a column, so a sentence of the guidance is
    broken across lines at a position nothing about the guidance decides. A
    phrase searched for in the raw text would then be absent for a reason that
    has nothing to do with whether the guidance is there — which is a check
    that fails for the wrong reason, and would as easily pass for one.
    """
    return " ".join(text.split())


@pytest.fixture
def prose(rendered) -> str:
    return flat(rendered)


# --------------------------------------------------------------------------
# What the rendering carries
# --------------------------------------------------------------------------


def test_the_rendered_prompt_asks_the_subject_or_input_question(prose):
    """The rule stated as a question the tester can answer at the moment it
    writes an assertion, rather than as a principle to remember later."""
    assert THE_QUESTION in prose


@pytest.mark.parametrize("artifact", THE_LIVE_ARTIFACTS)
def test_the_rendered_prompt_names_each_live_harness_artifact(prose, artifact):
    """All five, individually: a rule naming four of them leaves the fifth
    reading as though it were exempt."""
    assert artifact in prose


def test_the_rendered_prompt_says_a_live_artifact_is_a_legitimate_subject(prose):
    """The guidance is not "never read what this repository ships". An
    assertion about what the harness ships has to, and the prompt has to say so
    or a tester reads it as a blanket prohibition."""
    assert "legitimate subjects" in prose
    assert "has to read what it ships" in prose


def test_the_rendered_prompt_says_what_goes_wrong_when_it_is_an_input(prose):
    """The cost, stated: a deployment fact becomes something the suite
    enforces, and a correct change then reddens assertions with nothing to say
    about it."""
    assert "deployment fact" in prose
    assert "reddens assertions" in prose


def test_the_rendered_prompt_instructs_a_fixture_and_names_the_existing_ones(
    prose,
):
    """The instruction, and the three idioms the suite already provides — so
    the guidance points at a practice rather than leaving a tester to invent a
    fourth shape beside them."""
    assert THE_INSTRUCTION in prose
    for idiom in THE_IDIOMS:
        assert idiom in prose, idiom
    assert "rather than writing a fourth" in prose


def test_the_rendered_prompt_reconciles_the_fixture_rule_with_the_standing_one(
    prose,
):
    """The two rules cannot be read as contradicting each other.

    Both halves have to be present for the reconciliation to mean anything: the
    derive-from-the-workflow rule as it stands, the statement that a fixture
    does not reverse it, and the reason — a fixture defines the names once, so
    the test derives them exactly as it would have from the shipped definition.
    """
    assert THE_STANDING_RULE in prose
    assert THE_RECONCILIATION in prose
    assert "defines those names once" in prose
    assert "not whether they are derived" in prose

    # The two are in one statement rather than in two paragraphs a reader could
    # meet separately and read as disagreeing: the standing rule is stated
    # inside the sentence that says the fixture rule does not reverse it.
    reconciliation = prose.index(THE_RECONCILIATION)
    assert 0 < prose.index(THE_STANDING_RULE) - reconciliation < 120


def test_the_rendered_prompt_leaves_a_hard_coded_name_wrong_either_way(prose):
    """The one sentence that stops the reconciliation reading as a licence:
    deriving from a fixture is derivation, and a literal is still a literal."""
    assert "wrong either way" in prose


# --------------------------------------------------------------------------
# The two absence assertions, each with its control
# --------------------------------------------------------------------------


#: A rendering with the guidance removed, built by cutting the prompt at the
#: question and rejoining it at the section that follows. Used as the negative
#: control for every check below: each must report against it.
GUIDANCE_END = "When you finish, write these files to the run directory"


#: Where the guidance begins, as the rendering's own first words of it. Read
#: off the rendering rather than assumed, so the cut is at the paragraph the
#: guidance opens rather than at a column.
GUIDANCE_START = "Ask, at the moment you write an assertion"


def without_the_guidance(rendered: str) -> str:
    """The same rendering with the guidance paragraphs cut out of it."""
    start = rendered.index(GUIDANCE_START)
    return rendered[:start] + rendered[rendered.index(GUIDANCE_END):]


def test_the_control_really_removes_the_guidance_and_nothing_else(rendered):
    """The control the cases below lean on, asserted rather than assumed:
    shorter, missing every phrase the guidance contributed, and still carrying
    the rest of the prompt at both ends."""
    stripped = flat(without_the_guidance(rendered))
    whole = flat(rendered)

    assert len(stripped) < len(whole)
    assert THE_QUESTION not in stripped
    assert THE_INSTRUCTION not in stripped
    assert THE_RECONCILIATION not in stripped
    assert stripped.startswith(whole[:200])
    assert stripped.endswith(whole[-200:])


def test_the_rendering_leaves_no_unresolved_placeholder(rendered):
    """The absence, and its control is the placeholder convention itself: the
    template really does carry placeholders here, so an empty result is the
    rendering having resolved them rather than the check reading nothing."""
    assert "{{" not in rendered
    assert "}}" not in rendered

    # The control: the same check over the same text with a placeholder put
    # back reports it, so "no placeholder" is a property of the rendering.
    assert "{{" in rendered + "{{run_dir}}"
    planted = rendered.replace(GUIDANCE_END, "{{unresolved}}" + GUIDANCE_END, 1)
    assert "{{" in planted


@pytest.mark.parametrize("phrase", [
    THE_QUESTION, THE_INSTRUCTION, THE_STANDING_RULE, THE_RECONCILIATION,
    "legitimate subjects", "deployment fact", "defines those names once",
])
def test_every_phrase_this_module_looks_for_is_absent_once_it_is_removed(
    rendered, phrase,
):
    """The control for every positive check above, stated once.

    Each phrase is found in the rendering and not found in the same rendering
    with the guidance cut out. Without this, a check that had drifted to a
    phrase the prompt happens to contain elsewhere would pass while asserting
    nothing about the guidance.
    """
    assert phrase in flat(rendered)
    assert phrase not in flat(without_the_guidance(rendered))
