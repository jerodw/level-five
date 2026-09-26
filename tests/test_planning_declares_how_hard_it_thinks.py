"""Independent validation for story-210: planning states how hard it thinks,
at the ceiling for the interview.

Before this story the harness told the planning session nothing about how hard
to think. It launched `claude` and let the session inherit whatever the
developer's own tool happened to be set to, so planning's thinking could not be
raised without raising it for every unrelated piece of work the developer does,
and a change to their personal settings silently changed how the harness plans.
The story has the script state the level for both invocations planning makes:
the long interview that produces the story at the maximum, and the short
classifying turn that reads the request and proposes a workflow below it.

Written from the story's acceptance criteria rather than from the
implementation. The subjects are kept apart, because they fail for different
reasons:

  * **the argument lists.** Both invocations are captured from one real
    `l5-plan` session driven through a terminal, with a stub `claude` on PATH
    that logs the argument list it was given, and each list is compared to the
    whole list it is expected to be — every other argument included, so an
    argument quietly added, dropped or reordered is reported by the same
    assertion that reads the level.
  * **the declarations.** `scripts/l5-plan` is parsed, and every module-level
    constant whose value is a level the CLI accepts is read out with the comment
    block written immediately above it. The names are not written here: what a
    declaration *is* — module level, a level in the CLI's own vocabulary, a
    reason beside it — is the property, and the two names are whatever the
    script declares.
  * **the vocabulary.** Every level that reaches either command line is held to
    `agent_runner.EFFORT_LEVELS`, read off the one module that talks to the CLI
    rather than restated here, and the interview's is held to the ceiling of
    that tuple rather than to a word.
  * **what nothing reads.** Neither level is read back out to decide anything:
    each is loaded exactly once, at the argument list it is passed on, and no
    branch or comparison in the script mentions either.
  * **what the story left alone.** The seam a workflow stage's level travels
    through, both workflow definitions, and the three places a configuration
    key would have had to be declared.

Every absence asserted here carries a demonstration that the same reading
reports the violation it exists to catch:

  * "the two levels are not transposed" sits beside a second session run
    against a harness whose copy of the script has the two constants' values
    swapped, which the same reading reports;
  * "both invocations carry the flag" sits beside a third session against a
    harness whose copy has the argument taken out of both lists, where the same
    reading finds no level at either invocation — so the module is a control
    that can fail rather than one that cannot;
  * "a level declared inside a function is not a declaration" sits beside the
    same source with the declaration lifted to module level, which the same
    reading returns;
  * "every declaration carries a reason" sits beside declarations whose comment
    is absent, blank and a bare label, each reported, and beside a sound
    two-sentence reason, which is not;
  * "no reason is another's repeated" sits beside two constants sharing one
    comment, which the same reading reports;
  * "nothing reads either level to decide anything" sits beside a source that
    branches on one, which the same reading reports;
  * "this story left the orchestration, the workflows and the configuration
    alone" sits beside two histories built by `conftest.constructed_story`, one
    whose story respects those paths and one whose story edits them, which the
    identical call tells apart.

What is deliberately *not* asserted: that a reason names its own level as a
word. The sibling story that gave a workflow *stage* a level holds every
`effort_reason` string to that convention, and it is a good one — but this
story's criterion asks for a comment stating why the level was chosen, not for
one naming it, and a test may not invent a criterion the story does not carry.
What is held here is the floor: a reason is present, is more than a label, and
is not the other reason repeated.

The harness root these sessions run out of is a mirror carrying two definitions
this repository does not ship, because the workflow is an *input* here — the
subject is what argument list the script builds, and which definitions were
candidates has nothing to say about it. The script itself is this repository's,
copied into that mirror, because the script is the subject.

Nothing here invokes a model: every `claude` is the stub that
`tests/test_workflow_proposal.py` installs on PATH, which writes the answer it
was told to write and logs what it was given. The only commit graphs read are
this story's own range, through the shared resolution in `tests/conftest.py`,
and the two the control above builds under a temporary directory.
"""
from __future__ import annotations

import ast
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

import agent_runner
import conftest
from test_workflow_proposal import (  # noqa: F401 - planning_harness and
    ADDING,                           # planning are fixtures this module's
    ANSWER_IN_PROMPT,                 # cases request
    CONFIRMS,
    PRESERVING,
    STUB_STDOUT,
    TARGET_LOGS_DIR,
    answer,
    build_planning,
    invocations,
    plan_on_a_terminal,
    planning,
    planning_harness,
    rendered_against,
    transcript_in,
)

HARNESS_ROOT = Path(__file__).resolve().parents[1]

#: The entry point under test, reached as a path rather than through the module
#: loader because two of the readings below are about its *source* — where a
#: constant is declared and what is written above it — and the loader hands back
#: a module, which has forgotten both.
SCRIPTS_DIR = "scripts"
SCRIPT_NAME = "l5-plan"
SCRIPT_PATH = HARNESS_ROOT / SCRIPTS_DIR / SCRIPT_NAME


# --------------------------------------------------------------------------
# The vocabulary, and the two levels this story chose out of it
# --------------------------------------------------------------------------

#: The levels the CLI accepts, in increasing order, read off the one module that
#: talks to the CLI rather than restated here. Every level this module reasons
#: about is held against this tuple, so a vocabulary the CLI widens does not
#: need a second edit here.
LEVELS = agent_runner.EFFORT_LEVELS

#: What the interview is asked to think at, derived from the vocabulary rather
#: than written as a word: "the maximum" is a position in that tuple, and the
#: story's judgement is that planning belongs at whatever the top of it is.
THE_CEILING = LEVELS[-1]

#: What the classifying turn is asked to think at. This one *is* written down,
#: because it is a judgement rather than a position: the turn chooses from a
#: small bounded set and its mistake is reviewed by a human before anything
#: happens, which buys it less than the ceiling but more than the floor. Written
#: here so that moving it is an edit somebody reviews rather than a drift, and
#: asserted below to be a level the CLI accepts.
THE_CLASSIFYING_LEVEL = "medium"


# --------------------------------------------------------------------------
# What a `claude` command line is expected to be
#
# Every argument is named here, once, and both expectations below are built from
# these — so "every other argument is unchanged" is an equality over the whole
# list rather than a membership test that cannot see an argument that went
# missing.
# --------------------------------------------------------------------------

EFFORT_ARGUMENT = "--effort"
PROMPT_ARGUMENT = "--append-system-prompt"
PERMISSION_ARGUMENT = "--permission-mode"
PERMISSION_MODE = "acceptEdits"
PRINT_ARGUMENT = "-p"
TOOL_GRANT_ARGUMENT = "--allowedTools"
WRITE_TOOL = "Write"

#: The request these sessions are given, and the argument the script wraps it
#: in. The wrapper is the script's own wording, which is why it is written out:
#: an assertion that the request stays the last argument has to know what the
#: last argument looks like.
THE_REQUEST = "a story request"
REQUEST_ARGUMENT = "Story request: " + THE_REQUEST

#: The definition the classifying turn proposes and the confirmation accepts.
#: One of the two the mirrored harness carries; which one is immaterial, and it
#: is named once so the interview can be asked whether it was rendered against
#: the definition that was confirmed rather than the one that was not.
CONFIRMED, NOT_CONFIRMED = ADDING, PRESERVING


def interview_arguments(level: str, prompt: str) -> list[str]:
    """The whole argument list the planning interview is expected to carry.

    The rendered prompt is taken from what was captured rather than rendered a
    second time here: what it *contains* is another module's subject, and this
    one is about the arguments around it.
    """
    return [PERMISSION_ARGUMENT, PERMISSION_MODE,
            EFFORT_ARGUMENT, level,
            PROMPT_ARGUMENT, prompt,
            REQUEST_ARGUMENT]


def classifying_arguments(level: str, prompt: str) -> list[str]:
    """The whole argument list the workflow-selection turn is expected to carry:
    the interview's, plus the three arguments that make it one printing turn
    with one tool it may use."""
    return [PRINT_ARGUMENT,
            PERMISSION_ARGUMENT, PERMISSION_MODE,
            TOOL_GRANT_ARGUMENT, WRITE_TOOL,
            EFFORT_ARGUMENT, level,
            PROMPT_ARGUMENT, prompt,
            REQUEST_ARGUMENT]


# --------------------------------------------------------------------------
# One session, and the two invocations it made
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """One `l5-plan` run, with its two invocations told apart."""

    classifying: dict
    interview: dict
    status: int
    output: str
    logs: Path
    script: Path


def the_classifying_turn(made: list[dict]) -> dict:
    """The invocation that was asked to write a workflow selection.

    Recognised by the answer path in the prompt it was handed, which is how the
    stub itself recognises it and how `tests/test_workflow_proposal.py` reads
    it — never by its position in the log, so a session that made them in the
    other order is reported rather than silently read backwards.
    """
    found = [one for one in made if ANSWER_IN_PROMPT.search(one["prompt"])]
    assert len(found) == 1, [one["argv"] for one in made]
    return found[0]


def the_interview(made: list[dict]) -> dict:
    """The other one: the session asked for no selection, which is the session
    that interviews the developer and writes the story."""
    found = [one for one in made if not ANSWER_IN_PROMPT.search(one["prompt"])]
    assert len(found) == 1, [one["argv"] for one in made]
    return found[0]


def a_session(planning_target, harness: Path) -> Session:
    """A real `l5-plan`, run with no workflow named so that both invocations
    happen, its proposal confirmed by the empty reply the pty already holds."""
    status, output = plan_on_a_terminal(
        planning_target, harness, THE_REQUEST, reply=CONFIRMS,
        L5_STUB_SELECTION=answer(CONFIRMED["name"]))
    made = invocations(planning_target)
    assert len(made) == 2, output
    return Session(classifying=the_classifying_turn(made),
                   interview=the_interview(made),
                   status=status, output=output,
                   logs=planning_target.root / TARGET_LOGS_DIR,
                   script=harness / SCRIPTS_DIR / SCRIPT_NAME)


@pytest.fixture
def session(planning, planning_harness) -> Session:
    """The session whose argument lists are this module's subject, run against
    the script this repository ships."""
    return a_session(planning, planning_harness)


@pytest.fixture
def probe(tmp_path, planning_harness):
    """A factory for the same session against a *mutated* copy of the script.

    The mirrored harness is copied whole rather than rebuilt, so the probe and
    the subject differ in the one thing the mutation changes and in nothing
    else, and each probe gets a target of its own because a second session in
    one target would append to a log another session already wrote.
    """
    def make(mutate, *, name: str) -> Session:
        root = tmp_path / (name + "-harness")
        shutil.copytree(planning_harness, root, symlinks=True)
        script = root / SCRIPTS_DIR / SCRIPT_NAME
        script.write_text(mutate(script.read_text(encoding="utf-8")),
                          encoding="utf-8")
        return a_session(build_planning(tmp_path, name=name), root)
    return make


def arguments_of(invocation: dict) -> list[str]:
    """Everything the invocation was given after the program name."""
    return invocation["argv"][1:]


def prompt_of(invocation: dict) -> str:
    return invocation["argv"][
        invocation["argv"].index(PROMPT_ARGUMENT) + 1]


def level_carried_by(invocation: dict) -> str | None:
    """The level an invocation was launched at, or None where it was launched
    without the argument at all.

    None rather than a raised error, because "no level reached this invocation"
    is exactly what the removal probe below has to be able to observe.
    """
    argv = invocation["argv"]
    if EFFORT_ARGUMENT not in argv:
        return None
    following = argv[argv.index(EFFORT_ARGUMENT) + 1:]
    return following[0] if following else None


def transposed(session: Session) -> bool:
    """Whether the two levels are the wrong way round.

    The judgement the story made is an ordering: the interview thinks harder
    than the turn that classifies for it. Read as positions in the CLI's own
    increasing vocabulary rather than as two words, so the reading says which
    invocation is above which rather than merely that they differ.
    """
    interview = level_carried_by(session.interview)
    classifying = level_carried_by(session.classifying)
    if interview is None or classifying is None:
        return False
    return LEVELS.index(interview) <= LEVELS.index(classifying)


# --------------------------------------------------------------------------
# Reading the declarations out of the script
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Declaration:
    """One module-level constant holding a level, and the comment above it."""

    name: str
    level: str
    reason: str


def flowed(text: str) -> str:
    """Text with its whitespace collapsed.

    A comment block is prose a reader reads as one paragraph and `in` reads as
    one line per source line, so every comparison below is made on the flowed
    form — otherwise a sentence that happens to wrap differently is a different
    sentence.
    """
    return " ".join(text.split())


def comment_block_above(lines: list[str], lineno: int) -> str:
    """The unbroken run of comment lines ending immediately above `lineno`.

    Unbroken is the whole of "adjacent": a blank line between a comment and an
    assignment means the comment belongs to whatever is above it, and a reader
    meeting the constant does not meet that comment at all.
    """
    block = []
    index = lineno - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        block.append(lines[index].lstrip().lstrip("#:").strip())
        index -= 1
    return flowed(" ".join(reversed(block)))


def declared_levels(source: str) -> list[Declaration]:
    """Every module-level constant in `source` whose value is a level the CLI
    accepts, in the order they are declared.

    Module level and nothing else: a level assigned inside a function is a local
    a reader does not meet where the script's other constants are, and the story
    asks for a named constant the reader meets. The names are discovered rather
    than looked for, so what the assertions below hold is the shape of a
    declaration rather than two names written twice.
    """
    lines = source.splitlines()
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if node.value.value not in LEVELS:
            continue
        found.append(Declaration(
            name=target.id, level=node.value.value,
            reason=comment_block_above(lines, node.lineno)))
    return found


def sentences(text: str) -> list[str]:
    return [one for one in re.split(r"(?<=[.])\s+", text.strip()) if one.strip()]


def is_a_reason(text: str) -> bool:
    """Whether a comment block says enough to be a reason rather than a label.

    The floor, not the whole shape: what the story asks for — what the
    invocation is, what its mistakes cost, why that buys this level — is prose a
    reviewer weighs. What is mechanical is that a single clause cannot be it, so
    a block that never finishes a sentence and starts another is reported.
    """
    return bool(text.strip()) and len(sentences(text)) > 1


def declarations_with_no_reason(declarations: list[Declaration]) -> list[str]:
    """Every declaration whose adjacent comment is missing, blank or a label."""
    return [one.name for one in declarations if not is_a_reason(one.reason)]


def reasons_repeating_another(declarations: list[Declaration]) -> list[str]:
    """Every declaration whose reason is another's, whole.

    Containment rather than equality: one sentence written twice is the failure
    the story names, and so is one reason with a clause bolted on — in both
    cases the second declaration has no judgement of its own recorded.
    """
    found = []
    for one in declarations:
        for other in declarations:
            if one.name == other.name:
                continue
            if one.reason and one.reason in other.reason:
                found.append(one.name)
                break
    return found


def loads_of(source: str, name: str) -> int:
    """How many times `source` reads the value of `name`."""
    return len([node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Name) and node.id == name
                and isinstance(node.ctx, ast.Load)])


def names_read_to_decide(source: str, names: set[str]) -> list[str]:
    """Every one of `names` that a branch, a comparison or a boolean operator in
    `source` reads.

    Nothing is meant to read either level back out. Like the recorded reasons in
    a workflow definition, they are values handed on and never consulted, so a
    level appearing in a test is a decision being taken on a value that exists
    to be passed to a command line.
    """
    deciding = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            branches = [node.test]
        elif isinstance(node, (ast.Compare, ast.BoolOp)):
            branches = [node]
        else:
            continue
        for branch in branches:
            deciding += [inner.id for inner in ast.walk(branch)
                         if isinstance(inner, ast.Name) and inner.id in names]
    return sorted(set(deciding))


def shipped_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def shipped_declarations() -> list[Declaration]:
    return declared_levels(shipped_source())


# --------------------------------------------------------------------------
# The mutations the probes apply, and the sources the readings are controlled
# with
# --------------------------------------------------------------------------


def with_the_levels_transposed(source: str) -> str:
    """`source` with each declared level replaced by the other's.

    The assignment line only: the reasons are prose about levels and would
    match a blunter substitution, and what the probe needs changed is what the
    script passes rather than what it says about it.
    """
    found = declared_levels(source)
    swapped = dict(zip([one.name for one in found],
                       [one.level for one in reversed(found)]))
    for name, level in swapped.items():
        source = re.sub("(?m)^" + re.escape(name) + " = .*$",
                        name + " = " + repr(level), source)
    return source


def with_the_argument_removed(source: str) -> str:
    """`source` with the effort argument taken out of every argument list.

    The flag and the constant beside it, wherever a list carries them, found by
    the names the script declares rather than by a line number. The declarations
    themselves are left in place, so what the probe changes is only whether the
    levels reach a command line.
    """
    dropped = set(['"' + EFFORT_ARGUMENT + '",'])
    for one in declared_levels(source):
        dropped.add(one.name + ",")
    kept = [line for line in source.splitlines() if line.strip() not in dropped]
    return "\n".join(kept) + "\n"


#: The levels the constructed sources below declare. Taken from the CLI's
#: vocabulary by position rather than written out, and distinct so a source
#: declaring two is not accidentally declaring one twice.
A_LEVEL, ANOTHER_LEVEL = LEVELS[0], LEVELS[-1]

#: A reason of the shape the convention asks for, and its near neighbours: the
#: same declaration with no comment, with a blank one, and with a label that
#: never finishes one sentence and starts another. Each is one respect away from
#: the sound case, which is what makes the sound case worth anything.
A_SOUND_REASON = ("#: What this invocation is, and what a mistake in it costs.\n"
                  "#: Which is what buys it this level rather than another.\n")
A_BLANK_REASON = "#:\n#:\n"
A_LABEL = "#: the cheap one\n"


def a_declaration(name: str, level: str, comment: str = "") -> str:
    return comment + name + " = " + repr(level) + "\n"


def a_module(*declarations: str) -> str:
    """A module source declaring what it was given, and nothing else."""
    return "\n".join(declarations)


# ==========================================================================
# The premises these assertions rest on
# ==========================================================================


def test_the_two_levels_this_story_chose_are_levels_the_cli_accepts():
    """The vocabulary is the CLI's, and the two chosen levels are members of it
    rather than words that happen to look like levels.

    The ordering premise is asserted too: the story's judgement is that the
    interview thinks harder than the turn classifying for it, which is only
    readable if the vocabulary really is ordered and the ceiling really is its
    last entry.
    """
    assert THE_CEILING in LEVELS
    assert THE_CLASSIFYING_LEVEL in LEVELS
    assert THE_CEILING == LEVELS[-1]
    assert THE_CLASSIFYING_LEVEL != THE_CEILING
    assert LEVELS.index(THE_CLASSIFYING_LEVEL) < LEVELS.index(THE_CEILING)
    # And the two levels the constructed sources declare are distinct, so a
    # source declaring both is not accidentally declaring one twice — which
    # would make the shared-reason control below pass for the wrong reason.
    assert A_LEVEL != ANOTHER_LEVEL


def test_the_definitions_the_session_chooses_between_can_be_told_apart():
    """The premise "the interview was rendered against the definition that was
    confirmed" rests on: two definitions whose stage names and restricted prefix
    do not overlap, so a prompt carrying one is not also carrying the other."""
    assert CONFIRMED["name"] != NOT_CONFIRMED["name"]
    stages = {one["name"] for one in CONFIRMED["stages"]}
    others = {one["name"] for one in NOT_CONFIRMED["stages"]}
    assert stages - others


# ==========================================================================
# The two invocations the session made
# ==========================================================================


def test_the_session_makes_one_classifying_turn_and_one_interview(session):
    """What the readings below are readings of: two invocations, exactly one of
    which was asked to write a selection, and the other of which carries the
    stage facts of the definition that was confirmed and not those of the one
    that was not.

    So "the interview" below is the interview rather than whichever invocation
    happened to be logged second.
    """
    assert session.status == 0, session.output
    assert rendered_against(session.interview, CONFIRMED)
    assert not rendered_against(session.interview, NOT_CONFIRMED)
    assert not rendered_against(session.classifying, CONFIRMED)


def test_the_planning_interview_is_launched_at_the_ceiling(session):
    """The story's central claim for the long session: the argument immediately
    after the flag is the maximum the CLI accepts.

    Asserted as the whole argument list and not only as the flag's neighbour, so
    the same assertion says that every other argument of that invocation is what
    it was — the permission mode, the rendered system prompt, and the request
    still last.
    """
    assert Path(session.interview["argv"][0]).name == "claude"
    assert level_carried_by(session.interview) == THE_CEILING
    assert arguments_of(session.interview) == interview_arguments(
        THE_CEILING, prompt_of(session.interview))


def test_the_classifying_turn_is_launched_below_the_interview(session):
    """The same claim for the short turn, and the same equality: its `-p`, its
    one tool grant, its permission mode, its rendered prompt and its request
    are all still there, in the order they were in, with the level inserted
    where the request stays last."""
    assert Path(session.classifying["argv"][0]).name == "claude"
    assert level_carried_by(session.classifying) == THE_CLASSIFYING_LEVEL
    assert arguments_of(session.classifying) == classifying_arguments(
        THE_CLASSIFYING_LEVEL, prompt_of(session.classifying))


def test_every_level_that_reaches_the_cli_is_one_the_cli_accepts(session):
    """Held against `agent_runner.EFFORT_LEVELS` rather than against a list
    retyped here, so a level the CLI stops accepting is reported without this
    module being edited — and a level the script invents is reported at all."""
    for invocation in (session.interview, session.classifying):
        assert level_carried_by(invocation) in LEVELS


def test_the_two_levels_are_not_transposed(session):
    """The interview thinks harder than the turn that classifies for it.

    Read as an ordering rather than as two words, and its control is the probe
    below: the same reading over a session whose script has the two values
    swapped reports it, so a green here is the ordering holding rather than a
    reading that cannot tell the two invocations apart.
    """
    assert transposed(session) is False
    assert level_carried_by(session.interview) != level_carried_by(
        session.classifying)
    # And each level reaching an invocation is one the script declares, so
    # neither is a literal written at a call site beside the constants.
    assert {level_carried_by(session.interview),
            level_carried_by(session.classifying)} == {
        one.level for one in shipped_declarations()}


def test_the_streams_of_both_invocations_are_what_they_were(session):
    """The half of "every other argument is unchanged" that no argument list can
    say: the interview still inherits the developer's terminal on all three
    streams, and the classifying turn still reads no terminal and still has its
    output captured and kept.

    The two sit beside each other in one session, so neither half can pass
    because the pty failed to reach either invocation.
    """
    assert session.interview["tty"] == [True, True, True]
    assert session.classifying["tty"] == [False, False, False]
    kept = transcript_in(session.logs)
    assert kept.is_file()
    assert STUB_STDOUT in kept.read_text(encoding="utf-8")


# ==========================================================================
# The probes: the same readings over a script that gets it wrong
#
# The story asks for a module that fails if either level is changed or either
# flag removed, rather than one that cannot fail. These two run the whole
# session again against a copy of the script with exactly that done to it.
# ==========================================================================


def test_the_same_reading_reports_a_session_whose_levels_are_transposed(probe):
    """The control for the ordering above. The mutation swaps the two declared
    values and nothing else, so what the reading reports is the transposition
    rather than a script that stopped working."""
    swapped = probe(with_the_levels_transposed, name="transposed")

    assert transposed(swapped) is True
    assert level_carried_by(swapped.interview) == THE_CLASSIFYING_LEVEL
    assert level_carried_by(swapped.classifying) == THE_CEILING


def test_the_same_reading_reports_a_session_launched_without_the_flag(probe):
    """The control for both levels above, and for the whole module: with the
    argument taken out of both lists, the reading finds no level at either
    invocation and neither argument list is the list it is expected to be.

    The mutation leaves the declarations in place, so what is observed is the
    argument no longer reaching a command line rather than a script that no
    longer has a level to pass.
    """
    without = probe(with_the_argument_removed, name="no-effort")

    assert level_carried_by(without.interview) is None
    assert level_carried_by(without.classifying) is None
    assert arguments_of(without.interview) != interview_arguments(
        THE_CEILING, prompt_of(without.interview))
    assert arguments_of(without.classifying) != classifying_arguments(
        THE_CLASSIFYING_LEVEL, prompt_of(without.classifying))
    # And the mutation really did leave the declarations alone, so the absence
    # above is the argument no longer reaching a command line rather than the
    # constants having gone with it.
    assert declared_levels(
        without.script.read_text(encoding="utf-8")) == shipped_declarations()


# ==========================================================================
# The declarations in the script's own source
# ==========================================================================


def test_each_level_is_a_module_level_constant_the_script_declares():
    """The story asks for named constants a reader meets, not literals at two
    call sites. What the values are is held above, against what reached the
    CLI; what is held here is that they are declared, at module level, and that
    the module really binds each name to the level its source declares.
    """
    declarations = shipped_declarations()
    assert len(declarations) == 2, [one.name for one in declarations]
    assert {one.level for one in declarations} == {THE_CEILING,
                                                  THE_CLASSIFYING_LEVEL}

    script = conftest.load_script(
        SCRIPT_NAME, name="l5_plan_for_its_declared_levels")
    for one in declarations:
        assert getattr(script, one.name) == one.level
        assert one.level in LEVELS


def test_a_level_assigned_inside_a_function_is_not_a_declaration():
    """The control for "module level" doing work in the reading above: the same
    assignment inside a function is invisible to it, and lifted back out to
    module level it is returned — so the reading is looking at where a constant
    is declared rather than merely for the word."""
    inside = "def build():\n    " + a_declaration("A_LEVEL", A_LEVEL)
    assert declared_levels(inside) == []

    outside = a_declaration("A_LEVEL", A_LEVEL)
    assert [one.name for one in declared_levels(outside)] == ["A_LEVEL"]


def test_a_constant_holding_something_that_is_not_a_level_is_not_a_declaration():
    """The other half of the reading: it recognises a declaration by the CLI's
    vocabulary rather than by a name that looks like one, so a constant named
    for effort and holding something else is not mistaken for a level."""
    not_a_level = a_declaration("AN_EFFORT_LEVEL", "as hard as it likes")
    assert declared_levels(not_a_level) == []


def test_each_declared_level_carries_a_reason_of_its_own():
    """The judgement beside the value. Nothing in the harness reads either
    comment, so the prose is the only place the reason a level was chosen
    survives — and a reason that is the other one repeated records no judgement
    about the second invocation at all.

    Its controls are the constructed declarations below, which the same two
    readings report.
    """
    declarations = shipped_declarations()
    assert declarations_with_no_reason(declarations) == []
    assert reasons_repeating_another(declarations) == []


@pytest.mark.parametrize("comment", ["", A_BLANK_REASON, A_LABEL],
                         ids=["absent", "blank", "a label"])
def test_that_reading_reports_a_declaration_with_no_reason_beside_it(comment):
    """The control for the first absence above, built rather than mutated out of
    what this repository ships: a comment that is missing, blank, or a label that
    never finishes one sentence and starts another is reported in every case,
    and the sound two-sentence reason beside them is not."""
    reported = a_module(a_declaration("A_LEVEL", A_LEVEL, comment))
    assert declarations_with_no_reason(declared_levels(reported)) == ["A_LEVEL"]

    sound = a_module(a_declaration("A_LEVEL", A_LEVEL, A_SOUND_REASON))
    assert declarations_with_no_reason(declared_levels(sound)) == []


def test_that_reading_reports_a_comment_separated_from_its_constant():
    """The control for "adjacent": the same sound reason with a blank line
    between it and the constant is reported, because a reader meeting the
    constant does not meet that comment."""
    apart = a_module(A_SOUND_REASON + "\n"
                     + a_declaration("A_LEVEL", A_LEVEL))
    assert declarations_with_no_reason(declared_levels(apart)) == ["A_LEVEL"]


def test_that_reading_reports_two_constants_sharing_one_reason():
    """The control for the second absence above: one sentence written twice is
    reported for both declarations, while two reasons that differ are reported
    for neither.

    The two sound reasons differ in one clause only, so what the reading
    separates is the judgement rather than the wording.
    """
    shared = a_module(a_declaration("A_LEVEL", A_LEVEL, A_SOUND_REASON),
                      a_declaration("ANOTHER_LEVEL", ANOTHER_LEVEL,
                                    A_SOUND_REASON))
    assert reasons_repeating_another(declared_levels(shared)) == [
        "A_LEVEL", "ANOTHER_LEVEL"]

    its_own = A_SOUND_REASON.replace("this level rather than another.",
                                     "the level below that one.")
    separate = a_module(a_declaration("A_LEVEL", A_LEVEL, A_SOUND_REASON),
                        a_declaration("ANOTHER_LEVEL", ANOTHER_LEVEL, its_own))
    assert reasons_repeating_another(declared_levels(separate)) == []


def test_nothing_reads_either_level_back_out_to_decide_anything():
    """Each level is passed straight onto its own command line: read once, at
    the list it is handed to, and consulted nowhere. Like the recorded reasons
    in a workflow definition, these are values nothing branches on.

    Its control is the source below, which branches on one and is reported.
    """
    source = shipped_source()
    names = {one.name for one in shipped_declarations()}
    assert names_read_to_decide(source, names) == []
    for name in names:
        assert loads_of(source, name) == 1, name


def test_that_reading_reports_a_script_that_branches_on_a_level():
    """The control for the absence above. A source that tests one of the levels
    is reported, and the same source passing it on instead is not — so the
    silence above is nothing consulting the value rather than a reading that has
    stopped looking at branches."""
    branching = a_module(
        a_declaration("A_LEVEL", A_LEVEL, A_SOUND_REASON),
        "def launch(argv):\n"
        "    if A_LEVEL == " + repr(ANOTHER_LEVEL) + ":\n"
        "        return argv\n"
        "    return argv + [A_LEVEL]\n")
    assert names_read_to_decide(branching, {"A_LEVEL"}) == ["A_LEVEL"]

    passing = a_module(
        a_declaration("A_LEVEL", A_LEVEL, A_SOUND_REASON),
        "def launch(argv):\n"
        "    return argv + [A_LEVEL]\n")
    assert names_read_to_decide(passing, {"A_LEVEL"}) == []
    assert loads_of(passing, "A_LEVEL") == 1


# ==========================================================================
# What this story deliberately left alone
# ==========================================================================

#: The paths this story declares it does not touch, each for its own reason.
#: `orchestration/` holds the seam a workflow *stage*'s level travels to its
#: invocation through, and planning is not a stage of any workflow, so it
#: declares its own level rather than reaching through that path. The two
#: definitions hold how hard a stage thinks, which was decided against a
#: different kind of invocation — one made many times per run and again on every
#: retry — and reopening it here would decide it by accident. The last three are
#: the places a configuration key would have had to be declared, and a key is
#: what would let planning's level drift again by a different route from the one
#: this story closes.
LEFT_ALONE = (
    "orchestration",
    "workflows",
    "schemas/harness-config.schema.json",
    ".harness/config.yaml",
    "templates/config.yaml",
)


def test_this_story_left_the_seam_the_definitions_and_the_configuration_alone():
    """Resolved through the shared story range rather than against HEAD.

    A comparison against HEAD goes vacuously green the moment the coordinator
    commits the working tree, which is exactly when this assertion would stop
    saying anything. Its control is the pair of histories below.
    """
    assert conftest.story_diff(list(LEFT_ALONE),
                              validation_file=Path(__file__)) == ""


def test_the_same_comparison_tells_a_story_that_touched_them_apart(tmp_path):
    """The control for the absence above, over histories built here.

    One story respects those paths and one edits them; the identical call is
    empty for the first and non-empty for the second, so an empty answer above
    is this story having left them alone rather than a range bounded at the
    wrong commits.
    """
    respecting = conftest.constructed_story(
        tmp_path, respected=list(LEFT_ALONE), name="planning-left-them-alone")
    assert conftest.constructed_story_diff(respecting, LEFT_ALONE) == ""

    violating = conftest.constructed_story(
        tmp_path, violated=list(LEFT_ALONE), name="planning-edited-them")
    assert conftest.constructed_story_diff(violating, LEFT_ALONE) != ""
