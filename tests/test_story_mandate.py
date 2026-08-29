"""story-087 validation: a story runs on a mandate it can resolve.

The subject is one contract in two halves. The coordinator resolves what
conferred the right to run a story — a walk that follows `source` until `kind`
is `human` — and refuses the run before anything exists when it cannot. And
`l5-plan`, the harness process that observed the developer approve the plan,
is what writes the block, so an artifact that comes back from a session
already carrying one is refused rather than trusted.

What is asserted, and how:

* The walk is exercised directly, against mandates constructed here. It reads
  no repository, no configuration and no history, so nothing below builds one
  for it; the lookup it is handed is a function this module writes, which is
  the whole of the seam a deployment with an artifact store would use.

* The pre-flight refusal is exercised through a real `run_story` against the
  shared target fixture and a workflow this module builds. The stage names
  come off that built definition, never off the shipped one: whether a run is
  refused before it creates anything has nothing to say about how many stages
  this repository happens to deploy.

* The stamping is exercised through the real `scripts/l5-plan` as a
  subprocess, against the throwaway planning repository and stub `claude` that
  `tests/test_plan_commit.py` already builds. That fixture is imported rather
  than rebuilt: a second repository-with-a-stub-session beside it would be a
  fourth idiom for the same thing, and this module's subject is what the
  script stamps rather than how a session is faked.

Every absence asserted here carries a control that constructs the violation:

* "the refusal leaves no run directory, no state, no log and no branch" sits
  beside the same fixture with the mandate intact, where every one of those
  appears;
* "the walk consults no lookup for a human source" sits beside the same walk
  over a non-human source, where the same lookup is called and raises;
* "the refusal names no level and no foreign artifact kind" sits beside the
  same reader over a rendering with a level and a foreign kind planted in it,
  which reports both;
* "an entry of an undeclared kind writes nothing" sits beside the same append
  of a declared kind, which writes;
* "no artifact of the mandate era fails the requirement" sits beside a
  constructed artifact with the block removed, which does fail it — and the
  era set is empty as this story runs, which is stated in the test itself
  rather than left for a reader to discover.

No model is invoked anywhere in this file, and nothing here resolves a
baseline out of this repository's commit graph.
"""
from __future__ import annotations

import inspect
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import harness_config
import plan_mandate
import plan_run_offer
import schema_validator
import story_coordinator
import story_parser
from agent_runner import AgentResult, CapacityStop

import conftest
from conftest import MANDATE_ERA_STORY, commit_setup
from test_plan_commit import (
    Planning,
    artifact,
    bare_remote,
    make_planning,
    run_plan,
    writes,
)
from test_story_parser import FIRST_SCHEMA_ERA_STORY

REPO_ROOT = Path(story_coordinator.__file__).resolve().parents[1]
STORIES_DIR = REPO_ROOT / ".harness" / "stories"
L5_PLAN = REPO_ROOT / "scripts" / "l5-plan"

STORY_ID = "story-001"


# --------------------------------------------------------------------------
# The walk
#
# Constructed mandates and a lookup written here. Nothing in this section
# reads a repository, a configuration or a history: the walk takes what it is
# given and answers, which is what makes it testable without one.
# --------------------------------------------------------------------------


#: A bound generous enough that nothing below reaches it by accident. The
#: depth failures state their own bound at the point they are asserted.
ROOMY = 16


def a_human_mandate(conferred_by: str = "A Developer <developer@example.com>") -> dict:
    return {
        "source": {"kind": story_coordinator.HUMAN},
        "conferred_at": "2026-08-28 09:00:00",
        "conferred_by": conferred_by,
        "recorded_by": plan_mandate.RECORDED_BY,
    }


def a_linked_mandate(kind: str, identifier: str) -> dict:
    """A mandate whose source is not a human, and so has to be resolved."""
    return {
        "source": {"kind": kind, "id": identifier},
        "conferred_at": "2026-08-28 09:00:00",
        "conferred_by": "",
        "recorded_by": plan_mandate.RECORDED_BY,
    }


#: A kind this harness has no meaning for, which is the point: the walk knows
#: `human` and nothing else, and what any other kind means is settled by the
#: schema that declares it and by what a lookup can answer for it.
FOREIGN_KIND = "xyzzy-record"


def answering(chain: dict[str, dict]):
    """A lookup that answers for the ids `chain` names, and for nothing else.

    The shape a deployment with an artifact store would pass. This harness
    passes `resolves_nothing`, which is a fact about the deployment rather
    than about the walk, and the two tests below hold that distinction: the
    walk resolves a chain when it is given a lookup that can answer one.
    """

    def lookup(source: dict) -> dict | None:
        return chain.get(source.get("id", ""))

    return lookup


def refuses_to_be_called(source: dict) -> dict:
    """A lookup that fails if it is consulted at all.

    The control that makes "no lookup was consulted" mean something: a walk
    that terminates on a human and a walk that silently resolved through a
    lookup returning nothing useful are otherwise the same green.
    """
    raise AssertionError(
        f"the walk consulted a lookup for source {source!r}, which it must not "
        f"do for a source whose kind is '{story_coordinator.HUMAN}'"
    )


def test_a_human_source_terminates_in_one_hop_with_no_lookup_consulted():
    resolution = story_coordinator.resolve_mandate(
        a_human_mandate(), refuses_to_be_called, ROOMY)
    assert resolution.resolved
    assert resolution.problems == []
    assert resolution.kind == story_coordinator.HUMAN
    assert resolution.identity == "A Developer <developer@example.com>"
    assert resolution.hops == 0


def test_the_same_lookup_is_consulted_for_a_source_that_is_not_a_human():
    """The control for the assertion above.

    `refuses_to_be_called` passing proves nothing until it has been shown to
    fire, so the same function is handed a walk that does have a link to
    follow, where it must be reached.
    """
    with pytest.raises(AssertionError) as raised:
        story_coordinator.resolve_mandate(
            a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"),
            refuses_to_be_called, ROOMY)
    assert "consulted a lookup" in str(raised.value)


def test_a_human_source_reaches_no_network(monkeypatch):
    """Nothing is fetched to resolve a mandate a person conferred.

    Asserted by making a socket impossible to open rather than by reading the
    source, with the control immediately beside it: the same block that makes
    the walk's silence meaningful must make an actual connection attempt fail.
    """

    def no_sockets(*args, **kwargs):
        raise AssertionError("the walk opened a socket")

    monkeypatch.setattr(socket, "socket", no_sockets)
    resolution = story_coordinator.resolve_mandate(
        a_human_mandate(), refuses_to_be_called, ROOMY)
    assert resolution.resolved
    # The control: with the same patch in force, something that does reach for
    # the network fails, so the silence above is the walk's and not the patch's.
    with pytest.raises(AssertionError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_a_chain_of_links_resolves_when_a_lookup_can_answer_it():
    """The positive control for the three refusals below.

    Every failure mode asserts that a walk did *not* resolve. That says
    nothing about whether a walk can resolve through a lookup at all, so one
    that can is run here: two links, then a human, and the identity the walk
    reports is the one at the end of the chain rather than the one it started
    from.
    """
    lookup = answering({
        "xyzzy-source-1": a_linked_mandate(FOREIGN_KIND, "xyzzy-source-2"),
        "xyzzy-source-2": a_human_mandate("The Approver <approver@example.com>"),
    })
    resolution = story_coordinator.resolve_mandate(
        a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"), lookup, ROOMY)
    assert resolution.resolved
    assert resolution.kind == story_coordinator.HUMAN
    assert resolution.identity == "The Approver <approver@example.com>"
    assert resolution.hops == 2


def unresolved(mandate, lookup, maximum: int) -> str:
    """The one problem a walk that did not resolve reported.

    Exactly one, because each failure mode below is a single stop rather than
    an accumulation, and a walk reporting two would mean the mode under test
    is not the mode that fired.
    """
    resolution = story_coordinator.resolve_mandate(mandate, lookup, maximum)
    assert not resolution.resolved
    assert resolution.kind == "" and resolution.identity == ""
    (problem,) = resolution.problems
    return problem


def test_an_absent_block_says_nothing_recorded_what_conferred_the_right():
    problem = unresolved(None, refuses_to_be_called, ROOMY)
    assert "no mandate to resolve" in problem
    assert "conferred the right" in problem


def test_a_block_that_is_not_a_mapping_is_the_same_absence():
    """An artifact carrying `mandate: approved` has no block either.

    The parse produces a string where the declaration says object, and the
    walk has nothing to read a source out of. It is the absent case rather
    than a fifth mode, and saying so here is what stops it from becoming one.
    """
    assert unresolved("approved", refuses_to_be_called, ROOMY) == \
        unresolved(None, refuses_to_be_called, ROOMY)


def test_an_id_nothing_answers_for_says_the_id_did_not_resolve():
    problem = unresolved(
        a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"),
        story_coordinator.resolves_nothing, ROOMY)
    assert "xyzzy-source-1" in problem
    assert "did not resolve" in problem
    assert "nothing answered for it" in problem


def test_a_source_with_no_id_says_there_is_nothing_to_resolve_it_through():
    """The other way an id fails to resolve: there was none to resolve.

    Reported rather than followed, because a lookup handed a source with no id
    could only answer by guessing.
    """
    problem = unresolved(
        {"source": {"kind": FOREIGN_KIND}, "conferred_at": "", "conferred_by": "",
         "recorded_by": ""},
        refuses_to_be_called, ROOMY)
    assert "carries no id" in problem
    assert FOREIGN_KIND in problem


def a_cycle() -> tuple[dict, object]:
    """A chain of links that comes back to where it has already been."""
    lookup = answering({
        "xyzzy-source-1": a_linked_mandate(FOREIGN_KIND, "xyzzy-source-2"),
        "xyzzy-source-2": a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"),
    })
    return a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"), lookup


def test_a_chain_returning_to_a_visited_id_is_reported_as_a_cycle():
    mandate, lookup = a_cycle()
    problem = unresolved(mandate, lookup, ROOMY)
    assert "cycle" in problem
    assert "xyzzy-source-1" in problem
    assert "already visited" in problem


def test_a_cycle_says_it_is_a_cycle_rather_than_exhausting_the_depth_bound():
    """Which of the two a small bound reaches first must not decide the message.

    A cycle under a bound of two would hit the bound within three hops, so a
    walk that compared the bound before it recognised the repeat visit would
    report a depth failure for a chain that is not deep — and the developer
    would raise the bound rather than fix the loop.
    """
    mandate, lookup = a_cycle()
    problem = unresolved(mandate, lookup, 2)
    assert "cycle" in problem
    assert story_coordinator.MANDATE_MAX_DEPTH_KEY not in problem


def a_chain_of(length: int) -> tuple[dict, object]:
    """A chain of `length` links that never reaches a human."""
    chain = {
        f"xyzzy-source-{index}": a_linked_mandate(
            FOREIGN_KIND, f"xyzzy-source-{index + 1}")
        for index in range(1, length + 2)
    }
    return a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"), answering(chain)


def test_a_chain_longer_than_the_bound_names_the_configured_bound():
    bound = 3
    mandate, lookup = a_chain_of(bound + 4)
    problem = unresolved(mandate, lookup, bound)
    assert story_coordinator.MANDATE_MAX_DEPTH_KEY in problem
    assert f"({bound})" in problem
    # The control: the same chain under a bound that accommodates it stops for
    # a different reason entirely, so the bound is what decided this.
    assert story_coordinator.MANDATE_MAX_DEPTH_KEY not in unresolved(
        mandate, lookup, bound + 40)


#: Each failure mode, and the mandate and lookup that produce it. The keys are
#: this module's own names for them; what is asserted is that a reader of the
#: message can tell which one they are looking at.
FAILURE_MODES = {
    "absent": (None, story_coordinator.resolves_nothing, ROOMY),
    "unresolvable": (a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"),
                     story_coordinator.resolves_nothing, ROOMY),
    "cyclic": (*a_cycle(), 2),
    "too-deep": (*a_chain_of(6), 3),
}


def test_the_four_failure_modes_each_produce_a_message_of_their_own():
    """No two of them read alike, in either direction.

    Distinctness alone would be satisfied by four messages differing in an id;
    what a reader needs is a word that says which mode it was, so each is also
    required to carry its own.
    """
    messages = {name: unresolved(*case) for name, case in FAILURE_MODES.items()}
    assert len(set(messages.values())) == len(messages), messages
    assert "no mandate to resolve" in messages["absent"]
    assert "did not resolve" in messages["unresolvable"]
    assert "cycle" in messages["cyclic"]
    assert story_coordinator.MANDATE_MAX_DEPTH_KEY in messages["too-deep"]


# --------------------------------------------------------------------------
# The bound is a configured value rather than a number in harness source
# --------------------------------------------------------------------------


def test_the_bound_is_read_from_configuration_and_defaults_when_it_is_absent():
    assert story_coordinator.mandate_max_depth(
        {story_coordinator.MANDATE_MAX_DEPTH_KEY: "3"}) == 3
    assert story_coordinator.mandate_max_depth({}) == \
        int(story_coordinator.DEFAULT_MANDATE_MAX_DEPTH)


@pytest.mark.parametrize("configured", ["", "eight", "-1", "3.5", None])
def test_a_bound_that_is_not_a_count_falls_back_rather_than_widening(configured):
    """A bad value must not be able to make an unresolvable chain resolve.

    The bound decides only how long a walk that is not going to terminate is
    followed, so a value that is not a non-negative count falls back to the
    default rather than being obeyed as zero, as infinity, or as itself.
    """
    assert story_coordinator.mandate_max_depth(
        {story_coordinator.MANDATE_MAX_DEPTH_KEY: configured}) == \
        int(story_coordinator.DEFAULT_MANDATE_MAX_DEPTH)


# --------------------------------------------------------------------------
# The vocabulary: source, kind, id, human — and nothing else
# --------------------------------------------------------------------------


#: Words that would make the walk know about a hierarchy it must not know
#: about. The check that a later artifact kind is a schema entry rather than a
#: condition in the coordinator has to be able to fail, and this is what makes
#: it able to.
FOREIGN_VOCABULARY = ("level", "epic", "initiative", "portfolio", "milestone",
                      "programme")


def foreign_words(text: str, permitted: tuple[str, ...] = ()) -> list[str]:
    """The words in `text` that name a hierarchy the walk must not know.

    A function rather than an inline loop, so the check can be *shown* to
    report a violation against a constructed rendering rather than only
    observed to be silent against the real one.
    """
    lowered = text.lower()
    return sorted(word for word in FOREIGN_VOCABULARY
                  if word in lowered and word not in permitted)


def test_the_reader_of_foreign_words_reports_one_that_is_planted():
    """The control for every silence below."""
    assert foreign_words(
        "this work sits below a level 3 epic and cannot run") == \
        ["epic", "level"]
    assert foreign_words("the source of kind 'human' conferred it") == []


def refusal_text(capsys, mandate, lookup, maximum: int) -> str:
    resolution = story_coordinator.resolve_mandate(mandate, lookup, maximum)
    assert story_coordinator._refuse_unresolved_mandate(resolution.problems) == 1
    return capsys.readouterr().err


@pytest.mark.parametrize("mode", sorted(FAILURE_MODES))
def test_the_refusal_names_no_level_and_no_kind_it_was_not_given(mode, capsys):
    """What the refusal is allowed to say, for each mode that produces one.

    The kind under test is `xyzzy-record`, which the refusal may name because
    it was read out of the mandate it was handed. Anything else would be a
    vocabulary the coordinator supplied, which is what this story put in the
    schema instead.
    """
    printed = refusal_text(capsys, *FAILURE_MODES[mode])
    assert foreign_words(printed) == []
    assert story_coordinator.HUMAN in printed


def test_the_refusal_names_the_kind_it_was_given_and_no_other():
    """The control for the assertion above, from the other side.

    A refusal that named no kind at all would satisfy "names no kind it was
    not given" vacuously, so the kind it *was* given is required to appear.
    """
    resolution = story_coordinator.resolve_mandate(
        a_linked_mandate(FOREIGN_KIND, "xyzzy-source-1"),
        story_coordinator.resolves_nothing, ROOMY)
    (problem,) = resolution.problems
    assert FOREIGN_KIND in problem


def added_source() -> str:
    """The source of the walk and its refusal, read off the live harness.

    A legitimate subject rather than an input: the claim is about what this
    harness ships, so it has to read what it ships.
    """
    return "\n".join(inspect.getsource(function) for function in (
        story_coordinator.resolve_mandate,
        story_coordinator.resolves_nothing,
        story_coordinator.mandate_max_depth,
        story_coordinator._refuse_unresolved_mandate,
    ))


def test_the_added_source_names_no_level_and_no_artifact_kind_of_its_own():
    """The walk's own vocabulary, by search over what it ships.

    `human` is the one kind the walk knows, and it reaches this source through
    the shared constant rather than as a literal — so the value is written down
    once, where the schema's own vocabulary is. Every other kind reaches the
    walk from the mandate it was handed, so no other may be spelled here.
    """
    source = added_source()
    assert foreign_words(source) == []
    assert "HUMAN" in source
    assert f'"{story_coordinator.HUMAN}"' not in source


# --------------------------------------------------------------------------
# The pre-flight refusal, against a workflow this module builds
# --------------------------------------------------------------------------


WORKFLOW = conftest.build_workflow(
    conftest.workflow_stage(
        outputs=(conftest.CHANGED_FILES, conftest.IMPLEMENTATION_SUMMARY),
        changed_files=conftest.CHANGED_FILES,
        schemas={conftest.CHANGED_FILES: "changed-files"}),
    conftest.workflow_stage(
        name=conftest.VERIFYING_STAGE,
        outputs=(conftest.VERIFICATION_RESULT,),
        schemas={conftest.VERIFICATION_RESULT: "verification-result"}),
    escalation_rules={"max_retries_exceeded": {"action": "escalate"}},
    name="mandate-workflow",
)
WRITING, VERIFYING = [stage["name"] for stage in WORKFLOW["stages"]]

PASS = {"status": "passed", "blocking_issues": [], "unverified": [],
        "retry_recommended": False}


@pytest.fixture
def configured_workflow() -> str:
    return WORKFLOW["name"]


@pytest.fixture
def harness_root(tmp_path) -> Path:
    return conftest.materialize_workflow(WORKFLOW, tmp_path / "mandate-harness")


class Runner:
    """Stands in for `agent_runner.run_agent`, writing each stage's artifacts.

    It records every invocation, which is how "no agent was invoked" is
    observed: the refusals below must leave `calls` empty, and the control
    beside them must not.
    """

    def __init__(self, target_root: Path, story_id: str = STORY_ID,
                 capacity_at: int | None = None):
        self.run_dir = target_root / ".harness" / "runs" / story_id
        self.capacity_at = capacity_at
        self.calls: list[str] = []

    def __call__(self, prompt, *, stage, cwd, log_path, permission_mode, model,
                 allowed_tools=None, max_budget_usd=None, suite_command=None):
        self.calls.append(stage)
        # Written for the same reason the real runner writes it: the stage log
        # is one of the traces a refusal has to leave absent, and a fake that
        # never wrote one would make that absence the fake's rather than the
        # refusal's.
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"===== stage: {stage} =====\n")
        if self.capacity_at is not None and len(self.calls) > self.capacity_at:
            return AgentResult(
                ok=False,
                result_text="Claude AI usage limit reached",
                capacity=CapacityStop(
                    signal="claude ai usage limit reached", reset_at=None),
            )
        if stage == WRITING:
            (self.run_dir / conftest.CHANGED_FILES).write_text(
                json.dumps({"modified": ["src/app.py"], "created": [],
                            "deleted": []}), encoding="utf-8")
            (self.run_dir / conftest.IMPLEMENTATION_SUMMARY).write_text(
                "Did the work.\n", encoding="utf-8")
        elif stage == VERIFYING:
            (self.run_dir / conftest.VERIFICATION_RESULT).write_text(
                json.dumps(PASS), encoding="utf-8")
        return AgentResult(ok=True, result_text=f"{stage} done")


def install(target_root: Path, text: str, story_id: str = STORY_ID) -> Path:
    """Put a story artifact where the run reads it, and commit it.

    Committed because a run refuses to start from a tree holding work it
    cannot account for, and what a test installs is part of the repository the
    run starts *from*.
    """
    path = target_root / ".harness" / "stories" / f"{story_id}.yaml"
    path.write_text(text, encoding="utf-8")
    commit_setup(target_root, "the story artifact this test runs")
    return path


def without_a_mandate(text: str = conftest.STORY) -> str:
    """The same artifact with the block removed and nothing else changed.

    The removal is asserted rather than assumed: a replacement that silently
    matched nothing would leave the artifact carrying a mandate, and every
    refusal below would then be a refusal for some other reason.
    """
    stripped = text.replace(conftest.MANDATE_BLOCK, "")
    assert stripped != text, "the block was not in the artifact to begin with"
    assert not plan_mandate.carries_a_mandate(stripped)
    return stripped


def branches(target_root: Path) -> set[str]:
    listed = subprocess.run(
        ["git", "-C", str(target_root), "branch", "--format=%(refname:short)"],
        capture_output=True, text=True, check=True)
    return set(listed.stdout.split())


def traces(target_root: Path, story_id: str = STORY_ID) -> dict[str, bool]:
    """Everything a run creates, as a mapping a refusal must leave all false.

    One reader for both halves of the pair below, so the refusal and its
    control are answered by the same question rather than by two.
    """
    run_dir = target_root / ".harness" / "runs" / story_id
    return {
        "run directory": run_dir.exists(),
        "state.json": (run_dir / "state.json").exists(),
        "events.log": (run_dir / "events.log").exists(),
        "stage log": (target_root / ".harness" / "logs" /
                      f"{story_id}.log").exists(),
        "story branch": f"story/{story_id}" in branches(target_root),
    }


def test_a_story_with_no_mandate_is_refused_before_anything_is_created(
        target_root, harness_root, capsys):
    """An artifact with no block does not run, and leaves nothing behind.

    What refuses it is the schema requirement, which sits directly above the
    walk and inside the same band: the block is in the story schema's
    top-level `required` list, so `read_story` reports it missing before there
    is a mandate for the walk to be handed. The walk's own absent-block
    message is the defensive answer to a caller that hands it nothing, and it
    is asserted against the walk rather than claimed to be reachable here.
    What this test is about is the outcome the story states — the run is
    refused, and nothing a run creates exists afterwards.
    """
    install(target_root, without_a_mandate())
    runner = Runner(target_root)
    code = story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner)
    assert code == 1
    assert runner.calls == []
    assert traces(target_root) == {name: False for name in traces(target_root)}
    printed = capsys.readouterr().err
    assert "mandate" in printed
    assert foreign_words(printed) == []


def test_the_same_fixture_with_a_mandate_creates_every_one_of_those_traces(
        target_root, harness_root):
    """The control for the refusal above.

    Absent files are what a fixture that never ran leaves too, so the same
    target, the same workflow and the same runner are given an artifact whose
    mandate resolves, and every trace the refusal left absent must appear.
    """
    install(target_root, conftest.STORY)
    runner = Runner(target_root)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner) == 0
    assert runner.calls == [WRITING, VERIFYING]
    assert traces(target_root) == {name: True for name in traces(target_root)}


#: The four modes as artifacts, so the pre-flight is driven by a story rather
#: than by a mandate handed straight to the walk. The bound is the fixture's
#: configuration for the depth case, which is what makes that case reachable
#: at all: this deployment's lookup answers for nothing, so a chain is
#: unresolvable at its first link under any bound above zero.
ARTIFACT_MODES = {
    "absent": ("", None, "mandate"),
    "unresolvable": (f"""
mandate:
  source:
    kind: {FOREIGN_KIND}
    id: xyzzy-source-1
  conferred_at: 2026-08-28 09:00:00
  conferred_by: ''
  recorded_by: l5-plan
""", None, "did not resolve"),
    "too-deep": (f"""
mandate:
  source:
    kind: {FOREIGN_KIND}
    id: xyzzy-source-1
  conferred_at: 2026-08-28 09:00:00
  conferred_by: ''
  recorded_by: l5-plan
""", "0", story_coordinator.MANDATE_MAX_DEPTH_KEY),
}


def configure(target_root: Path, **values: str) -> None:
    path = target_root / ".harness" / "config.yaml"
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text += f"{key}: {value}\n"
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("mode", sorted(ARTIFACT_MODES))
def test_each_mode_refuses_a_real_run_with_a_message_of_its_own(
        mode, target_root, harness_root, capsys):
    """The modes a story artifact can actually carry, through `run_story`.

    Two of the four are not here, and neither absence is an oversight. A
    cyclic chain needs a lookup that answers, and this deployment's answers
    for nothing, so it is refused as unresolvable at its first link; that the
    walk reports a cycle when a lookup can produce one is asserted above,
    against the walk itself. And the absent block is refused by the schema
    requirement above the walk, so what it produces here is the schema's
    message rather than the walk's — which the row asks for by name.
    """
    block, bound, expected = ARTIFACT_MODES[mode]
    if bound is not None:
        configure(target_root,
                  **{story_coordinator.MANDATE_MAX_DEPTH_KEY: bound})
    install(target_root, without_a_mandate() + block)
    runner = Runner(target_root)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, runner) == 1
    assert runner.calls == []
    assert traces(target_root) == {name: False for name in traces(target_root)}
    printed = capsys.readouterr().err
    assert expected in printed
    assert foreign_words(printed) == []


def test_a_configured_bound_decides_which_chains_are_refused_for_depth(
        target_root, harness_root, capsys):
    """The same artifact under two bounds, refused for two different reasons.

    A bound of zero refuses a chain before its first link is followed; the
    default bound follows it and refuses it for the id nothing answered. So
    the number in the configuration is what separated them.
    """
    block = ARTIFACT_MODES["unresolvable"][0]
    install(target_root, without_a_mandate() + block)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, Runner(target_root)) == 1
    assert story_coordinator.MANDATE_MAX_DEPTH_KEY not in capsys.readouterr().err

    configure(target_root, **{story_coordinator.MANDATE_MAX_DEPTH_KEY: "0"})
    commit_setup(target_root, "a bound this harness would never pick")
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, Runner(target_root)) == 1
    assert story_coordinator.MANDATE_MAX_DEPTH_KEY in capsys.readouterr().err


# --------------------------------------------------------------------------
# What a run records, and what a resume is judged by
# --------------------------------------------------------------------------


def events(target_root: Path, story_id: str = STORY_ID) -> list[str]:
    path = target_root / ".harness" / "runs" / story_id / "events.log"
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_run_records_the_mandate_it_resolved_in_its_own_evidence(
        target_root, harness_root):
    """The run's evidence names the kind it terminated at and the identity.

    Read off the run rather than off the artifact: the point of the record is
    that a reader of one execution's evidence can say what it ran on without
    opening the story it came from.
    """
    install(target_root, conftest.STORY)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, Runner(target_root)) == 0
    identity = story_parser.parse(
        conftest.STORY, story_schema())["mandate"]["conferred_by"]
    recorded = [line for line in events(target_root) if "mandate resolved" in line]
    assert len(recorded) == 1, events(target_root)
    assert story_coordinator.HUMAN in recorded[0]
    assert identity in recorded[0]


def test_the_first_thing_a_run_says_is_still_which_workflow_is_executing(
        target_root, harness_root):
    """The record does not displace story-072's announcement.

    A new event on every entry is exactly the kind of change that quietly
    takes the first line of a run's log, so the order is asserted rather than
    left to where the call happens to sit.
    """
    install(target_root, conftest.STORY)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, Runner(target_root)) == 0
    lines = events(target_root)
    announced = next(i for i, line in enumerate(lines)
                     if f"workflow {WORKFLOW['name']} started" in line)
    resolved = next(i for i, line in enumerate(lines) if "mandate resolved" in line)
    assert announced < resolved


def test_a_resume_is_judged_by_the_story_artifact_as_it_is_now(
        target_root, harness_root, capsys):
    """A story edited between two entries of one run is judged by the edit.

    The first entry stops on capacity with a mandate that resolves, leaving a
    resumable run. The block is then replaced by one that does not resolve,
    and the second entry is refused for it — so the walk is re-run against the
    artifact as it is now rather than trusted from what the first entry read.
    The replacement is a block the *walk* rejects rather than an absent one,
    so what refuses the resume is the resolution and not the schema.
    """
    install(target_root, conftest.STORY)
    paused = Runner(target_root, capacity_at=0)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, paused) == \
        story_coordinator.PAUSE_EXIT_CODE
    state = target_root / ".harness" / "runs" / STORY_ID / "state.json"
    assert json.loads(state.read_text(encoding="utf-8"))["status"] == "paused"

    install(target_root,
            without_a_mandate() + ARTIFACT_MODES["unresolvable"][0])
    refused = Runner(target_root)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, refused) == 1
    assert refused.calls == []
    assert "did not resolve" in capsys.readouterr().err
    # The refusal left the paused run exactly as it was, which is what makes
    # the control below a resume rather than a fresh start.
    assert json.loads(state.read_text(encoding="utf-8"))["status"] == "paused"


def test_the_same_paused_run_resumes_when_the_artifact_still_carries_one(
        target_root, harness_root):
    """The control for the refusal above: without the edit, the run resumes."""
    install(target_root, conftest.STORY)
    paused = Runner(target_root, capacity_at=0)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, paused) == \
        story_coordinator.PAUSE_EXIT_CODE
    resumed = Runner(target_root)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, resumed) == 0
    assert resumed.calls == [WRITING, VERIFYING]


# --------------------------------------------------------------------------
# The seam: the per-log append, reachable from outside a run
# --------------------------------------------------------------------------


def declared_logs() -> dict[str, dict]:
    return story_coordinator.history_log_declarations()


def a_kind_of(log: str) -> str:
    """One event kind the declaration routes to `log`, read off the schema.

    Derived rather than written: which kinds reach which log is settled in
    `schemas/cross-run-history.schema.json`, and a test that spelled one would
    be a second place the routing is decided.
    """
    declaration = declared_logs()[log]
    return declaration["properties"][
        story_coordinator.HISTORY_EVENT_PROPERTY]["enum"][0]


@pytest.mark.parametrize("log", sorted(declared_logs()))
def test_every_declared_log_is_reachable_through_the_explicit_directory(
        log, tmp_path):
    """The seam, for each log the declaration names rather than for one.

    The directory is handed in, which is the whole of what the factoring
    added: a process that observes something the harness records and has no
    run directory appends through exactly this path.
    """
    directory = tmp_path / "history-outside-a-run"
    story_coordinator.append_history_records(
        directory,
        {"event": a_kind_of(log), "timestamp": "2026-08-28 09:00:00"},
        "story-900", [])
    written = [json.loads(line) for line in
               (directory / log).read_text(encoding="utf-8").splitlines() if line]
    assert [record["story_id"] for record in written] == ["story-900"]


def test_an_entry_of_a_kind_no_log_declares_writes_nothing_at_all(tmp_path):
    """The selection is the declaration's and nothing else's.

    The control is the parametrized test above: the same call with a declared
    kind writes, so an empty directory here is the enum's answer rather than a
    call that could not write at all.
    """
    directory = tmp_path / "history-outside-a-run"
    kinds = {kind for declaration in declared_logs().values()
             for kind in declaration["properties"][
                 story_coordinator.HISTORY_EVENT_PROPERTY]["enum"]}
    assert "xyzzy-undeclared-kind" not in kinds
    story_coordinator.append_history_records(
        directory,
        {"event": "xyzzy-undeclared-kind", "timestamp": "2026-08-28 09:00:00"},
        "story-900", [])
    assert not directory.exists()


def log_declaring(kind: str) -> str:
    """The one declared log whose enum names `kind`.

    Derived, so the conferring record's destination is read off the schema
    that routes it rather than written here a second time.
    """
    named = [log for log, declaration in declared_logs().items()
             if kind in declaration["properties"][
                 story_coordinator.HISTORY_EVENT_PROPERTY]["enum"]]
    assert len(named) == 1, named
    return named[0]


def test_the_conferring_log_is_declared_and_no_run_produces_it(
        target_root, harness_root):
    """The log this story declares, and the fact that nothing in a run reaches it.

    Its records come from a process outside a run, so a completed run must
    leave it absent while writing the logs a run does produce — which is the
    control that keeps the absence from being a history directory nothing
    wrote to at all.
    """
    log = log_declaring(plan_mandate.CONFERRED_EVENT)

    install(target_root, conftest.STORY)
    assert story_coordinator.run_story(
        STORY_ID, harness_root, target_root, Runner(target_root)) == 0
    directory = target_root / harness_config.DEFAULT_HISTORY_DIR
    written = {path.name for path in directory.iterdir()}
    assert log not in written
    assert written
    assert written <= set(declared_logs())


# --------------------------------------------------------------------------
# The stamp: what l5-plan writes, and what it refuses to
# --------------------------------------------------------------------------


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    """The planning repository `tests/test_plan_commit.py` builds.

    Imported rather than rebuilt: a stub `claude` on PATH, a throwaway target
    and a bare origin to push to are that module's fixture, and a second copy
    of them here would be a fourth idiom for one thing. What this module adds
    is what it asks of the artifact afterwards.
    """
    made = make_planning(tmp_path)
    made.remote = bare_remote(tmp_path, made, upstream=True)
    return made


PLANNED_ID = "story-900"
PLANNED_REL = f".harness/stories/{PLANNED_ID}.yaml"


def session_writing(text: str, story_id: str = PLANNED_ID) -> str:
    return writes((f".harness/stories/{story_id}.yaml", text))


def test_l5_plan_stamps_a_mandate_the_coordinator_then_resolves(planning):
    """End to end: written by a session, stamped, validated, committed.

    The stub session writes an artifact with no block — which is what a
    session writes, since the block is not an agent's to write — and what is
    committed afterwards carries one that resolves in one hop to the
    repository's own git identity.
    """
    result = run_plan(planning, L5_STUB_WRITE=session_writing(artifact(PLANNED_ID)))
    assert result.returncode == 0, result.stdout + result.stderr

    committed = subprocess.run(
        ["git", "-C", str(planning.root), "show", f"HEAD:{PLANNED_REL}"],
        capture_output=True, text=True, check=True).stdout
    assert plan_mandate.carries_a_mandate(committed)

    reading = story_coordinator.read_story(committed)
    assert reading.problems == []
    resolution = story_coordinator.resolve_mandate(
        reading.parsed["mandate"], refuses_to_be_called,
        story_coordinator.mandate_max_depth({}))
    assert resolution.resolved
    assert resolution.kind == story_coordinator.HUMAN
    assert resolution.identity == "Test <test@example.com>"


def test_the_stamped_artifact_is_accepted_at_a_real_pre_flight(
        planning, target_root, harness_root):
    """The other end of the same contract, in a repository that can run it.

    The text l5-plan committed is installed in the target the coordinator runs
    against, and the run completes — so what one process writes is what the
    other accepts, rather than two independent readings of one schema.
    """
    assert run_plan(
        planning,
        L5_STUB_WRITE=session_writing(artifact(PLANNED_ID))).returncode == 0
    stamped = (planning.root / PLANNED_REL).read_text(encoding="utf-8")
    install(target_root, stamped, story_id=PLANNED_ID)
    runner = Runner(target_root, story_id=PLANNED_ID)
    assert story_coordinator.run_story(
        PLANNED_ID, harness_root, target_root, runner) == 0
    assert runner.calls == [WRITING, VERIFYING]


def test_an_artifact_that_arrives_carrying_a_mandate_is_refused(planning):
    """Nothing stamped, nothing committed, and the artifact left where it is.

    A block this process did not write is a block nothing observed, so it is
    refused rather than trusted or replaced. The artifact stays exactly as the
    session wrote it: this process never repairs, rewrites or removes one.

    Driven without a terminal since story-088, which is where the refusal
    now lives: with a terminal there is a developer to put the block to, and
    approving it is an act this process observed and may act on, so the
    unconditional refusal is what a headless invocation gets. That is also the
    forgery this closes — a headless l5-plan committing a block nobody saw.
    """
    forged = artifact(PLANNED_ID) + conftest.MANDATE_BLOCK
    head = planning.head()
    result = plan_without_a_terminal(
        planning, L5_STUB_WRITE=session_writing(forged))

    assert result.returncode == 1
    assert planning.head() == head
    path = planning.root / PLANNED_REL
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == forged
    assert PLANNED_REL in planning.status()
    assert "already carries a mandate block" in result.stderr
    assert "stamped nothing and committed nothing" in result.stdout


def test_one_session_artifact_carrying_a_block_stamps_none_of_them(planning):
    """A partial stamp would invent a split the session did not have.

    Two artifacts, one of them carrying a block the session wrote. Neither is
    stamped, which is the same rule the commit already follows: one artifact
    that cannot be committed commits none of them.

    Driven without a terminal for the reason its neighbour above is: since
    story-088 that is where a session-written block is refused outright.
    """
    clean = artifact("story-901", title="A second planned story")
    result = plan_without_a_terminal(planning, L5_STUB_WRITE=writes(
        (f".harness/stories/{PLANNED_ID}.yaml",
         artifact(PLANNED_ID) + conftest.MANDATE_BLOCK),
        (".harness/stories/story-901.yaml", clean)))

    assert result.returncode == 1
    assert (planning.root / ".harness" / "stories" / "story-901.yaml").read_text(
        encoding="utf-8") == clean
    assert not plan_mandate.carries_a_mandate(clean)


def plan_without_a_terminal(planning: Planning,
                            **stub) -> subprocess.CompletedProcess:
    """The real script with stdin a pipe rather than a terminal.

    A missing terminal is the subject here rather than an inconvenience, so
    this invocation deliberately does not reach for `a_terminal_for_stdin`.
    """
    return subprocess.run(
        [sys.executable, str(L5_PLAN), "--workflow", "story-workflow",
         "a story request"],
        cwd=planning.root, env=planning.env(**stub), stdin=subprocess.DEVNULL,
        capture_output=True, text=True)


def test_a_headless_invocation_stamps_nothing_and_commits_nothing(planning):
    """No terminal, no human, no mandate — and so no commit.

    The message has to say more than the schema fault, because "mandate is
    required" tells a developer nothing about why an artifact they watched be
    written is missing something no agent may write.
    """
    head = planning.head()
    result = plan_without_a_terminal(
        planning, L5_STUB_WRITE=session_writing(artifact(PLANNED_ID)))

    assert result.returncode == 1
    assert planning.head() == head
    path = planning.root / PLANNED_REL
    assert path.is_file()
    assert not plan_mandate.carries_a_mandate(path.read_text(encoding="utf-8"))
    assert "no human present" in result.stdout
    assert "no terminal" in result.stdout
    assert "committed nothing" in result.stdout


def test_the_same_invocation_with_a_terminal_does_commit(planning):
    """The control for the headless refusal.

    Nothing about the fixture changes but the terminal, so the terminal is
    what decided it.
    """
    assert run_plan(
        planning,
        L5_STUB_WRITE=session_writing(artifact(PLANNED_ID))).returncode == 0
    assert plan_mandate.carries_a_mandate(
        (planning.root / PLANNED_REL).read_text(encoding="utf-8"))


def test_can_prompt_is_what_decides_whether_a_mandate_is_stamped():
    """The two invocations above differ in exactly this reading.

    Stated against the function itself so the pair is a difference in one
    named condition rather than in whatever else a pty might change.
    """
    with conftest.a_terminal_for_stdin() as terminal:
        with os.fdopen(os.dup(terminal), "r") as stream:
            assert plan_run_offer.can_prompt(stream)
    with open(os.devnull, "r") as pipe:
        assert not plan_run_offer.can_prompt(pipe)


# --------------------------------------------------------------------------
# The conferring record
# --------------------------------------------------------------------------


#: Where a conferring record goes, read off the declaration that routes it.
CONFERRED_LOG = log_declaring(plan_mandate.CONFERRED_EVENT)


def conferring_records(root: Path, directory: str) -> list[dict]:
    path = root / directory / CONFERRED_LOG
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_l5_plan_writes_one_conferring_record_per_story_it_stamped(planning):
    """Named, timed and attributed, in the declared log under the history dir.

    Two artifacts, two records: the record is per conferral rather than per
    invocation, because what it records is an act observed about one story.
    """
    result = run_plan(planning, L5_STUB_WRITE=writes(
        (f".harness/stories/{PLANNED_ID}.yaml", artifact(PLANNED_ID)),
        (".harness/stories/story-901.yaml",
         artifact("story-901", title="A second planned story"))))
    assert result.returncode == 0, result.stdout + result.stderr

    records = conferring_records(planning.root, harness_config.DEFAULT_HISTORY_DIR)
    assert [record["story_id"] for record in records] == [PLANNED_ID, "story-901"]
    for record in records:
        assert record["conferred_by"] == "Test <test@example.com>"
        assert record["source_kind"] == story_coordinator.HUMAN
        assert record["timestamp"]
    # The time in the record is the time in the block, so the two renderings of
    # one act cannot disagree.
    block = (planning.root / PLANNED_REL).read_text(encoding="utf-8")
    assert f"conferred_at: {records[0]['timestamp']}" in block


def test_the_conferring_record_is_committed_rather_than_left_in_the_tree(
        planning):
    """A record left behind would be a dirty path the offered run is refused for.

    The cross-run history is versioned, so this process commits what it wrote —
    beneath the artifact commit, which stays the branch tip and keeps saying
    what it said.
    """
    assert run_plan(
        planning,
        L5_STUB_WRITE=session_writing(artifact(PLANNED_ID))).returncode == 0
    relative = f"{harness_config.DEFAULT_HISTORY_DIR}/{CONFERRED_LOG}"
    assert relative not in planning.status()
    assert planning.status() == ""
    assert subprocess.run(
        ["git", "-C", str(planning.root), "cat-file", "-e", f"HEAD:{relative}"]
    ).returncode == 0
    # The artifact commit is still the tip and still says what it always said.
    assert planning.subject().startswith(f"Plan {PLANNED_ID}:")
    assert PLANNED_ID in planning.subject("HEAD~1")


def test_the_record_goes_to_the_configured_history_directory(tmp_path):
    """A directory this harness would never pick, obeyed.

    A record written to the default would land where the assertion is not
    looking, and the second half — that the default holds nothing — is what
    makes the first mean the configuration was read.
    """
    configured = ".harness/xyzzy-history"
    planning = make_planning(tmp_path)
    planning.remote = bare_remote(tmp_path, planning, upstream=True)
    path = planning.root / ".harness" / "config.yaml"
    path.write_text(path.read_text(encoding="utf-8") +
                    f"history_dir: {configured}\n", encoding="utf-8")
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", "a history directory of its own")
    # Pushed as well as committed: l5-plan refuses a session whose branch has
    # left its remote behind, above everything this test is about, and a
    # developer who configured a history directory would have pushed it.
    planning.git("push", "-q", "origin", "main")

    assert run_plan(
        planning,
        L5_STUB_WRITE=session_writing(artifact(PLANNED_ID))).returncode == 0
    assert [record["story_id"] for record in
            conferring_records(planning.root, configured)] == [PLANNED_ID]
    assert not (planning.root / harness_config.DEFAULT_HISTORY_DIR).exists()


def test_a_refused_session_writes_no_conferring_record(planning):
    """Nothing observed, nothing recorded.

    The control is every test above that does write one: the same fixture and
    the same invocation, differing only in the block the session wrote — and,
    since story-088, in the terminal, because a block put to a developer who
    approves it is conferred rather than refused.
    """
    assert plan_without_a_terminal(planning, L5_STUB_WRITE=session_writing(
        artifact(PLANNED_ID) + conftest.MANDATE_BLOCK)).returncode == 1
    assert not (planning.root / harness_config.DEFAULT_HISTORY_DIR /
                CONFERRED_LOG).exists()


def test_the_block_and_the_record_are_composed_from_one_place(tmp_path):
    """What is appended and what is recorded come off the same conferral.

    Asserted against `plan_mandate` directly, with the clock handed in, so the
    two renderings can be compared without a second process between them.
    """
    root = tmp_path / "identity"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Xyzzy Approver"],
                   cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "xyzzy@example.com"],
                   cwd=root, check=True)
    path = root / f"{PLANNED_ID}.yaml"
    path.write_text(artifact(PLANNED_ID), encoding="utf-8")

    conferred = plan_mandate.confer(path, root, now=0)
    assert conferred.stamped
    assert conferred.story_id == PLANNED_ID
    assert conferred.conferred_by == "Xyzzy Approver <xyzzy@example.com>"
    assert conferred.source_kind == story_coordinator.HUMAN
    stamped = path.read_text(encoding="utf-8")
    assert stamped.startswith(artifact(PLANNED_ID))
    assert f"conferred_by: {conferred.conferred_by}" in stamped
    assert f"conferred_at: {conferred.conferred_at}" in stamped


def test_a_target_whose_git_names_nobody_confers_nothing(tmp_path):
    """An act with nobody attached to it is not an act that was observed.

    Recording one would produce a block that resolves to a human the record
    cannot name, which is exactly the thing the walk exists to refuse.
    """
    root = tmp_path / "anonymous"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", ""], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", ""], cwd=root, check=True)
    path = root / f"{PLANNED_ID}.yaml"
    path.write_text(artifact(PLANNED_ID), encoding="utf-8")

    conferred = plan_mandate.confer(path, root)
    assert not conferred.stamped
    assert "no human to record" in conferred.detail
    # The artifact is untouched, which is what "nothing was stamped" means.
    assert path.read_text(encoding="utf-8") == artifact(PLANNED_ID)


# --------------------------------------------------------------------------
# The corpus, and the era the requirement applies from
# --------------------------------------------------------------------------


def story_schema() -> dict:
    return schema_validator.load_schema("story")


def committed_artifacts() -> list[Path]:
    return sorted(STORIES_DIR.glob("*.yaml"))


def mandate_problems(path: Path) -> list[str]:
    """What the mandate requirement alone says about one committed artifact."""
    parsed = story_parser.parse(path.read_text(encoding="utf-8"), story_schema())
    full = schema_validator.validate(parsed, story_schema())
    relaxed = schema_validator.validate(
        parsed, conftest.schema_without_the_mandate_requirement(story_schema()))
    return [problem for problem in full if problem not in relaxed]


def test_the_mandate_era_corpus_set_has_filled():
    """The inversion of what this asserted when the era was declared.

    story-087 wrote its own artifact with the l5-plan that predates the
    stamping it adds, so no committed artifact fell inside the era and this
    asserted the set empty — saying outright that an empty set proves nothing
    and that what held the requirement was the constructed pair below. The
    first artifact stamped by the process story-087 landed has since been
    committed, so the set has filled, and asserting it non-empty is the
    stronger statement: it is what stops the test after this one iterating over
    nothing and staying green whatever the requirement says. The constructed
    pair below is kept, because it is still what shows the same validation
    accepting a block and refusing its absence.
    """
    era = [path.stem for path in committed_artifacts()
           if path.stem >= MANDATE_ERA_STORY]
    assert era != []
    assert MANDATE_ERA_STORY <= max(path.stem for path in committed_artifacts())


def test_every_artifact_of_the_mandate_era_carries_a_mandate():
    """The requirement, over the artifacts it applies to.

    Written when the set was empty, because the boundary it reads is a
    constant one story moves and this is what makes moving it enough. The set
    has since filled, and the test above is what says so.
    """
    for path in committed_artifacts():
        if path.stem >= MANDATE_ERA_STORY:
            assert mandate_problems(path) == [], path.name


def test_the_requirement_bites_on_a_constructed_artifact_that_lacks_one(
        tmp_path):
    """The control the empty set above cannot supply.

    Constructed rather than committed, because a committed artifact predating
    the era is an execution record and is never edited to satisfy a contract
    written after it.
    """
    with_block = tmp_path / f"{MANDATE_ERA_STORY}.yaml"
    with_block.write_text(artifact(MANDATE_ERA_STORY) + conftest.MANDATE_BLOCK,
                          encoding="utf-8")
    assert mandate_problems(with_block) == []

    without = tmp_path / f"{MANDATE_ERA_STORY}-bare.yaml"
    without.write_text(artifact(MANDATE_ERA_STORY), encoding="utf-8")
    problems = mandate_problems(without)
    assert len(problems) == 1
    assert "mandate" in problems[0]


def test_no_pre_era_artifact_drops_out_of_validation_for_any_other_reason():
    """The relaxation is the requirement and nothing else.

    Every committed artifact older than the era is validated against the
    schema with the mandate requirement dropped, and must be clean: if the
    relaxation had reached anything else, an artifact failing some other part
    of the contract would be passing here.
    """
    relaxed = conftest.schema_without_the_mandate_requirement(story_schema())
    assert set(story_schema()["required"]) - set(relaxed["required"]) == {"mandate"}
    assert relaxed["properties"] == story_schema()["properties"]

    failures = []
    for path in committed_artifacts():
        if not (FIRST_SCHEMA_ERA_STORY <= path.stem < MANDATE_ERA_STORY):
            continue
        parsed = story_parser.parse(path.read_text(encoding="utf-8"),
                                    story_schema())
        failures += [f"{path.name}: {problem}"
                     for problem in schema_validator.validate(parsed, relaxed)]
    assert failures == [], "\n".join(failures)
