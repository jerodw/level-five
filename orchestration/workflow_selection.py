"""The decisions behind l5-plan's two-phase planning, kept out of the script.

Since story-072 an l5-plan invocation with no --workflow runs the planner
twice. The first invocation carries the request and what each defined workflow
says it is for, and nothing else -- it cannot be rendered against a workflow,
because choosing one is its job. It writes a name and the reasoning behind it;
l5-plan reads the name mechanically, shows the reasoning, and asks the
developer to accept it, name another, or abort. The confirmed name is what the
real planning session is rendered against.

Every function here is pure over its inputs and **returns what happened rather
than printing it**, the way plan_commit and plan_run_offer do, so
`scripts/l5-plan` stays the one place the developer-facing wording lives.
Nothing here prompts, nothing here reads a terminal, and nothing here decides
what the proposal is worth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import harness_config
import schema_validator

#: The prompt the first invocation carries. Named here rather than at the call
#: site for the reason every other artifact name in this harness is read off a
#: declaration: one spelling, in the module that owns the mechanism.
SELECTOR_PROMPT = "workflow-selector.md"

#: The schema the first invocation's answer is held to, and the name
#: `schema_validator.load_schema` takes.
SELECTION_SCHEMA = "workflow-selection"

#: The file phase one is asked to write its answer to, and the file its own
#: output is kept in. Named here for the reason SELECTOR_PROMPT is: the module
#: that owns the mechanism owns the spelling, so neither name is written at a
#: call site.
SELECTION_ANSWER = "workflow-selection.json"
SELECTION_TRANSCRIPT = "workflow-selection.log"

#: Where both live when the target configures no logs directory, spelled as
#: the coordinator spells it when it resolves the stage log, so phase one's
#: evidence lands beside the evidence every run already leaves.
DEFAULT_LOGS_DIR = ".harness/logs"


@dataclass(frozen=True)
class SelectionPaths:
    """Where phase one's answer goes and where its output is kept."""

    answer: Path
    transcript: Path


def selection_paths(target_root: Path, config: dict) -> SelectionPaths:
    """Derive both paths from the target root and its configuration.

    The answer is beneath the target root because that is the workspace the
    permission mode accepts edits within: asked for a path outside it, the
    classifying turn reasons correctly and then cannot deliver, which is the
    whole of why an unnamed l5-plan never once reached a proposal outside the
    test suite. The transcript sits beside it, under the same configured
    directory l5-plan already leaves evidence in.

    Pure, like everything else here: it returns paths, and creates, removes
    and prints nothing.
    """
    logs = target_root / config.get("logs_dir", DEFAULT_LOGS_DIR)
    return SelectionPaths(logs / SELECTION_ANSWER, logs / SELECTION_TRANSCRIPT)


@dataclass(frozen=True)
class Candidate:
    """One workflow the developer may be planning under: its name and its purpose."""

    name: str
    applies_when: str


def candidates(harness_root: Path) -> tuple[Candidate, ...]:
    """Every workflow the harness defines, with what each says it is for.

    Derived from the definitions the harness holds rather than written into
    the prompt, which is the property that makes a third workflow selectable
    by shipping a definition: it becomes a candidate with no edit to
    `prompts/workflow-selector.md` and no edit here.

    A definition carrying no usable `applies_when` is left out rather than
    offered with nothing to choose it by. That is not this function papering
    over the defect -- the coordinator refuses such a definition at pre-flight
    -- it is this function declining to ask a classifying turn to choose
    between a description and a blank.
    """
    found = []
    for name in harness_config.workflow_names(harness_root):
        applies_when = _applies_when(harness_root, name)
        if applies_when:
            found.append(Candidate(name, applies_when))
    return tuple(found)


def _applies_when(harness_root: Path, name: str) -> str:
    """One definition's `applies_when`, or "" where it has no usable one.

    The definition is read directly rather than through `load_workflow`,
    because this reading needs no configuration references resolved and must
    not fail on a definition whose references cannot be resolved: what a
    workflow is for is answerable whether or not this target can run it.
    """
    path = harness_root / "workflows" / f"{name}.json"
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    declared = definition.get("applies_when")
    return declared.strip() if isinstance(declared, str) else ""


def candidate_block(found: tuple[Candidate, ...]) -> str:
    """The candidates as the phase-one prompt carries them.

    One entry per definition, its name and its own words, so the prompt
    enumerates nothing itself. Rendered here rather than in the script because
    this is prompt content rather than developer-facing wording, and it has to
    be derivable from a candidate list a test can construct.
    """
    return "\n\n".join(f"{candidate.name}:\n    {candidate.applies_when}"
                       for candidate in found)


@dataclass(frozen=True)
class Selection:
    """What phase one wrote, read and checked against what the harness defines.

    `workflow` is the proposed name and is None wherever there is nothing to
    propose -- the answer was unsure, absent, unreadable, unparsable, or named
    a workflow no definition has. `reasoning` is what phase one wrote and is
    what the developer is shown; it is "" only where nothing readable was
    written. `fault` is a short phrase naming what went wrong, for the caller
    to put in its own sentence, and is None where the answer was usable.
    """

    workflow: str | None
    reasoning: str
    fault: str | None

    @property
    def proposed(self) -> bool:
        """Whether there is a proposal for the developer to accept."""
        return self.workflow is not None


def read_selection(
    path: Path, found: tuple[Candidate, ...], harness_root: Path | None = None
) -> Selection:
    """Read phase one's answer and decide whether it proposes anything.

    Every unusable shape yields no proposal rather than a guess, and each is
    distinguished in `fault` so the developer is told what actually happened:
    an answer that was never written, one that is not readable, one that is
    not JSON, one that does not satisfy the schema, one that names a workflow
    the harness does not define, and one that says outright it is unsure.
    Nothing is rendered against a name nothing chose.

    An answer that names a workflow the harness defines but which carried no
    usable `applies_when`, and so was never offered as a candidate, is not a
    proposal either: the choice is between the candidates that were shown.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Selection(None, "", "it wrote no answer")
    except OSError as error:
        return Selection(None, "", f"its answer could not be read ({error.strerror})")
    try:
        answer = json.loads(text)
    except ValueError:
        return Selection(None, "", "its answer is not JSON")
    schema = schema_validator.load_schema(SELECTION_SCHEMA, harness_root)
    problems = schema_validator.validate(answer, schema)
    if problems:
        return Selection(None, "", f"its answer is not a workflow selection: "
                                   f"{problems[0]}")
    reasoning = answer["reasoning"]
    named = answer.get("workflow")
    if named is None:
        return Selection(None, reasoning, "it was unsure")
    names = [candidate.name for candidate in found]
    if named not in names:
        return Selection(
            None,
            reasoning,
            f"it named '{named}', which is not one of the workflows it was "
            f"offered: {', '.join(names) if names else 'none'}",
        )
    return Selection(named, reasoning, None)


#: What the developer's reply asked for. Three outcomes and no fourth: run the
#: session under a workflow, or start nothing at all.
ACCEPT = "accept"
OVERRIDE = "override"
ABORT = "abort"


@dataclass(frozen=True)
class Decision:
    """The developer's reply, read as accept, override or abort.

    `workflow` is what the session should be rendered against and is None on
    an abort. `unknown` names the workflow the reply asked for where that name
    has no definition, which is an abort carrying the reason for it.
    """

    action: str
    workflow: str | None
    unknown: str | None = None


def read_reply(
    reply: str, found: tuple[Candidate, ...], proposal: str | None = None
) -> Decision:
    """Read one reply to the confirmation, once.

    With a proposal on the table, an empty reply accepts it and any other
    reply is read as naming the workflow to run under instead. With no
    proposal there is nothing to accept, so an empty reply aborts -- the
    developer was asked to name a workflow and declined, and starting a
    session under a name nobody chose is the one thing this must not do.

    A reply naming a workflow the harness does not define aborts rather than
    being asked again, in `plan_run_offer.should_run`'s shape: the reply is
    read once and never re-asked, and the name it carried is reported so the
    developer knows why nothing started.
    """
    named = reply.strip()
    if not named:
        if proposal is None:
            return Decision(ABORT, None)
        return Decision(ACCEPT, proposal)
    if named not in [candidate.name for candidate in found]:
        return Decision(ABORT, None, unknown=named)
    return Decision(OVERRIDE, named)
