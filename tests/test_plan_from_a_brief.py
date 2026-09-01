"""Independent validation for planning from a brief: `l5-plan --brief <key>`.

`l5-plan` took its request as command-line text and had no other input, so a
brief filed in a tracker could only be planned by a human copying its body into
a shell argument or by an agent relaying it in its own words. This story lets
the script be handed a brief instead: it fetches it through the same configured
command the dedupe question is asked of, renders it as the request the planning
session is given, and otherwise behaves exactly as it did -- the interview
happens, the developer approves, and the mandate is stamped from what the
process observed them answer.

Written from the story's acceptance criteria rather than from the
implementation, at two altitudes:

  * **the fetch itself** is `tests/test_brief_fetch.py`'s subject and is not
    re-driven here. What this module asks is what the *script* does with it:
    where the fetch sits relative to everything a session touches, what the
    session is handed, which workflow it is rendered against, and what a
    refusal leaves behind.

  * **the session**, driven through the real `scripts/l5-plan` against a
    throwaway repository with a stub `claude` on PATH and a fake filed-query
    command this module wrote -- so how many invocations were made, what each
    carried, what was written and what was committed are observations of the
    script rather than of its source.

**The workflows driven here are built, not shipped.** Every definition a
session is rendered against comes from the harness root
`tests/test_workflow_proposal.py` builds, because the subject is the
*mechanism* -- which workflow a brief-driven session ends up rendered against
-- and a workflow is its input. The brief names one of those built definitions
for the same reason.

Every absence asserted here carries a demonstration that it can fail:

  * "the brief path invokes no selector and writes no answer" sits beside the
    same target planned from request text without `--workflow`, which invokes
    one and writes both;
  * "a refused fetch invoked nothing, wrote nothing and committed nothing"
    sits beside the same target whose fetch succeeds, which invokes a session
    and commits an artifact;
  * "nothing in the tree carries the brief's body afterwards" sits beside the
    request the session was handed, which does carry it;
  * "a brief-driven invocation without a terminal is not refused" sits beside
    the same invocation without `--brief` and without `--workflow`, which is
    refused exactly as it was before;
  * "an explicit `--workflow` wins" sits beside the same invocation without
    it, where the brief's workflow is what the session is rendered against.

Nothing here invokes a model, reaches a network or touches a tracker: every
session goes through the stub `claude`, and every brief comes from a shell
script this module wrote.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from test_filed_query import fixture_command, fixture_file
from test_plan_commit import (  # noqa: F401 - shared idioms
    Planning,
    bare_remote,
    writes,
)
from test_workflow_proposal import (  # noqa: F401 - fixtures used by name
    ADDING,
    ANSWER_IN_PROMPT,
    APPROVES,
    CONFIRMS,
    DECLINES,
    PRESERVING,
    TARGET_LOGS_DIR,
    UNDEFINED,
    answer_in,
    artifact_path,
    build_planning,
    invocations,
    plan_on_a_terminal,
    plan_without_a_terminal,
    planned,
    planning_harness,
    relative_artifact,
    rendered_against,
    transcript_in,
)

import brief_fetch
import filed_query
import plan_mandate

#: What the modules say about themselves, so this file names no configuration
#: key of its own.
COMMAND_KEY = filed_query.COMMAND_KEY
TIMEOUT_KEY = filed_query.TIMEOUT_KEY

#: How long a fake command that is meant to be killed is asked to run, and the
#: bound it is given. The sleep is far longer than the bound, so a session that
#: reached a refusal reached it because the command was killed.
LONGER_THAN_THE_BOUND = 45
FETCH_BOUND_SECONDS = 1.0

#: The key the brief is fetched under. A URL, because the reference
#: implementation's keys are URLs -- and opaque, which is
#: `tests/test_brief_fetch.py`'s subject rather than this module's.
KEY = "https://tracker.invalid/issues/17"


def a_brief(**overrides) -> dict:
    """The brief the fake command answers with.

    Its workflow is one of the definitions the built harness root holds, so
    "the session was rendered against the workflow the brief named" is an
    observation about a definition this suite wrote rather than about one this
    repository happens to ship.
    """
    brief = {
        "title": "the parser drops the last token",
        "slug": "parser-drops-last-token",
        "body": "The reader stops one token early; see src/parser.py:118.",
        "category": "correctness",
        "severity": 3,
        "confidence": "high",
        "effort": "M",
        "workflow": ADDING["name"],
        "paths": ["src/parser.py", "src/reader.py"],
        "not_in_scope": ["rewriting the tokenizer"],
    }
    brief.update(overrides)
    return brief


# --------------------------------------------------------------------------
# A target that configures a filed-query command
# --------------------------------------------------------------------------


def answering_command(directory: Path, document: dict | str, *,
                      name: str = "answers.sh") -> Path:
    """A fake filed-query command printing one document and exiting zero.

    Written by this module, like every command driven as a filed-query command
    in this suite: the document is written beside the script and printed with
    `cat`, so nothing about the answer depends on a shell's quoting.
    """
    text = document if isinstance(document, str) else json.dumps(document)
    body = fixture_file(directory, f"{name}.document", text, executable=False)
    return fixture_command(directory, name, f'cat "{body}"\n')


def asking(tmp_path: Path, name: str, command: str | Path,
           **settings: str) -> Planning:
    """A throwaway target configured to ask `command` what is filed.

    The configuration is written and committed *before* the bare origin is
    made, so the target stands level with its remote: `l5-plan` runs the same
    base check `l5-run` runs at pre-flight, and a target one commit ahead of
    its origin is refused there for a reason that has nothing to do with any
    brief. The commit is what keeps a later "the working tree holds nothing
    new" about what a refused invocation did rather than about this fixture.
    """
    planning = build_planning(tmp_path, name=name, remote=False)
    config = planning.root / ".harness" / "config.yaml"
    declared = [f"{COMMAND_KEY}: {command}"]
    declared += [f"{key}: {value}" for key, value in settings.items()]
    config.write_text(
        config.read_text(encoding="utf-8") + "\n".join(declared) + "\n",
        encoding="utf-8")
    planning.git("add", "-A")
    planning.git("commit", "-q", "-m", "configure the filed-query command")
    planning.remote = bare_remote(tmp_path, planning, name=f"origin-{name}",
                                  upstream=True)
    return planning


@pytest.fixture
def briefed(tmp_path) -> Planning:
    """A throwaway target whose command answers with the brief above."""
    return asking(tmp_path, "brief-target",
                  answering_command(tmp_path / "command",
                                    {"brief": a_brief()}))


def request_given(invocation: dict) -> str:
    """The request text one session invocation was handed.

    Read off the argument list the script passed rather than out of the
    script's source, so this is what the session actually received.
    """
    given = [argument for argument in invocation["argv"]
             if argument.startswith("Story request: ")]
    assert len(given) == 1, invocation["argv"]
    return given[0]


def stories(planning: Planning) -> list[str]:
    return sorted(path.name for path in planning.stories_dir.iterdir())


# ==========================================================================
# 1. The brief becomes the request the session is given
# ==========================================================================


def test_a_brief_driven_session_is_given_the_briefs_own_prose(
        briefed, planning_harness):
    """The planner receives the evidence rather than a paraphrase of it: the
    title, the body, the paths and what the brief says to leave alone."""
    brief = a_brief()

    result = plan_without_a_terminal(briefed, planning_harness,
                                     "--brief", KEY)

    made = invocations(briefed)
    assert len(made) == 1, result.stdout
    request = request_given(made[0])
    assert brief["title"] in request
    assert brief["body"] in request
    for path in brief["paths"]:
        assert path in request, path
    for one in brief["not_in_scope"]:
        assert one in request, one


def test_the_session_is_told_to_record_the_briefs_key_in_the_description(
        briefed, planning_harness):
    """The whole of the traceability this adds. The brief is not written into
    the artifact and is not stored, so its key as prose is the only trace of
    it that survives the session."""
    plan_without_a_terminal(briefed, planning_harness, "--brief", KEY)

    request = request_given(invocations(briefed)[0])
    assert KEY in request
    assert re.search(r"(?i)record", request), request
    assert re.search(r"(?i)description", request), request


def test_a_brief_and_the_same_content_as_request_text_agree_on_the_prose(
        tmp_path, planning_harness):
    """Two invocations that differ only in where the request came from.

    The same content is handed to one session as a brief and to another as
    command-line text, and what the two sessions were given is required to
    agree on the brief's prose -- so what a brief-driven session plans from is
    what a developer pasting the same words would have planned from.
    """
    brief = a_brief()
    rendered = brief_fetch.render(brief, KEY)

    from_a_brief = asking(tmp_path, "from-a-brief",
                          answering_command(tmp_path / "command",
                                            {"brief": brief}))
    plan_without_a_terminal(from_a_brief, planning_harness, "--brief", KEY)

    from_text = build_planning(tmp_path, name="from-text")
    plan_without_a_terminal(from_text, planning_harness,
                            "--workflow", brief["workflow"], rendered)

    briefed_request = request_given(invocations(from_a_brief)[0])
    text_request = request_given(invocations(from_text)[0])

    assert briefed_request == text_request
    for prose in (brief["title"], brief["body"], KEY, *brief["paths"],
                  *brief["not_in_scope"]):
        assert prose in briefed_request, prose
        assert prose in text_request, prose
    assert rendered_against(invocations(from_a_brief)[0], ADDING)
    assert rendered_against(invocations(from_text)[0], ADDING)


def test_nothing_in_the_target_persists_the_fetched_brief(
        briefed, planning_harness):
    """The brief is a plan-time input and nowhere else: no file is written
    carrying it, and the artifact the session produced records only what the
    planner wrote.

    Its control is the request beside it, which does carry the body -- so an
    absence in the tree is the brief not being persisted rather than a search
    that would not have found it anyway.
    """
    brief = a_brief()
    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=CONFIRMS + APPROVES + DECLINES,
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status == 0, output
    assert brief["body"] in request_given(invocations(briefed)[0])

    carrying = [relative for relative, content in briefed.tree().items()
                if brief["body"].encode() in content]
    assert carrying == [], carrying
    assert brief["slug"] not in artifact_path(briefed).read_text(
        encoding="utf-8")


# ==========================================================================
# 2. The selector is not invoked on the brief path
# ==========================================================================


def test_the_brief_path_invokes_no_selector_and_writes_no_answer(
        briefed, planning_harness):
    """A brief names its workflow, so there is a proposal already and the
    classifying turn has nothing to add.

    Its control is the second half: the same target planned from request text
    with no `--workflow` invokes the selector, is handed an answer path, and
    keeps a transcript -- so the absences here are the brief path rather than a
    reading that finds nothing anywhere.
    """
    logs = briefed.root / TARGET_LOGS_DIR

    status, output = plan_on_a_terminal(briefed, planning_harness,
                                        "--brief", KEY, reply=CONFIRMS)

    assert status == 0, output
    made = invocations(briefed)
    assert len(made) == 1
    assert ANSWER_IN_PROMPT.search(made[0]["prompt"]) is None
    assert not answer_in(logs).exists()
    assert not transcript_in(logs).exists()

    briefed.log.unlink()
    plan_on_a_terminal(briefed, planning_harness, "a story request",
                       reply=CONFIRMS,
                       L5_STUB_SELECTION=json.dumps(
                           {"workflow": ADDING["name"], "reasoning": "why"}))

    selecting = invocations(briefed)[0]
    assert ANSWER_IN_PROMPT.search(selecting["prompt"])
    assert transcript_in(logs).is_file()


# ==========================================================================
# 3. Which workflow a brief-driven session is planned under
# ==========================================================================


def test_a_terminal_is_offered_the_briefs_workflow_and_enter_accepts_it(
        briefed, planning_harness):
    """The same confirmation the selector's proposal goes through, with the
    brief named as the reason -- so the wording of that exchange lives in one
    place and the developer is shown who proposed what."""
    status, output = plan_on_a_terminal(briefed, planning_harness,
                                        "--brief", KEY, reply=CONFIRMS)

    assert status == 0, output
    assert re.search(r"(?i)brief", output), output
    assert ADDING["name"] in output
    assert rendered_against(invocations(briefed)[0], ADDING)


def test_naming_another_workflow_at_the_confirmation_overrides_the_brief(
        briefed, planning_harness):
    """Observed from the facts injected into the session rather than from what
    was asked for. Its control is the acceptance above, where the same
    proposal with an empty reply plans under the brief's workflow."""
    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=f"{PRESERVING['name']}\n".encode())

    assert status == 0, output
    made = invocations(briefed)
    assert rendered_against(made[0], PRESERVING)
    assert not rendered_against(made[0], ADDING)


def test_naming_a_workflow_with_no_definition_at_the_confirmation_aborts(
        briefed, planning_harness):
    """Anything that is not Enter and not a defined workflow aborts without a
    session being started, and the name that was typed is reported."""
    head = briefed.head()

    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=f"{UNDEFINED}\n".encode(),
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status != 0, output
    assert UNDEFINED in output
    assert invocations(briefed) == []
    assert stories(briefed) == []
    assert briefed.head() == head


def test_without_a_terminal_the_briefs_workflow_is_taken_without_asking(
        briefed, planning_harness):
    """A brief-driven invocation is not refused for having nobody to confirm a
    proposal: the proposal came from the brief rather than from a classifying
    turn, and refusing it would make the loop closable only by hand.

    Its control is the second half: the same invocation without `--brief` and
    without `--workflow` is refused exactly as it was before this story, so the
    session reached here is the brief's doing.
    """
    result = plan_without_a_terminal(briefed, planning_harness, "--brief", KEY)

    made = invocations(briefed)
    assert len(made) == 1, result.stdout
    assert rendered_against(made[0], ADDING)
    assert ADDING["name"] in result.stdout

    briefed.log.unlink()
    refused = plan_without_a_terminal(briefed, planning_harness,
                                      "a story request")
    assert refused.returncode != 0
    assert "--workflow" in refused.stderr
    assert invocations(briefed) == []


def test_an_explicit_workflow_wins_and_both_are_reported(briefed,
                                                          planning_harness):
    """Which one the session is planned under, and that the brief named
    another. Its control is the test above, where the same invocation without
    the flag is rendered against the brief's workflow."""
    result = plan_without_a_terminal(briefed, planning_harness,
                                     "--workflow", PRESERVING["name"],
                                     "--brief", KEY)

    made = invocations(briefed)
    assert len(made) == 1, result.stdout
    assert rendered_against(made[0], PRESERVING)
    assert not rendered_against(made[0], ADDING)
    assert PRESERVING["name"] in result.stdout
    assert ADDING["name"] in result.stdout


# ==========================================================================
# 4. A brief and request text are two answers to one question
# ==========================================================================


def test_a_brief_and_request_text_together_are_refused(briefed,
                                                       planning_harness):
    """Neither is preferred silently: choosing one would plan something nobody
    asked for. Nothing is fetched, invoked, written or committed."""
    head = briefed.head()
    before = briefed.tree()

    result = plan_without_a_terminal(briefed, planning_harness,
                                     "--brief", KEY, "a story request")

    assert result.returncode != 0
    said = result.stdout + result.stderr
    assert KEY in said
    assert "a story request" in said
    assert invocations(briefed) == []
    assert stories(briefed) == []
    assert briefed.head() == head
    assert briefed.tree() == before
    assert briefed.status() == ""


# ==========================================================================
# 5. Every refusal happens before anything is invoked or written
# ==========================================================================


def refusing_targets(tmp_path: Path) -> dict[str, tuple[Planning, str]]:
    """One target per way a fetch can fail, each with the key to ask it for.

    Built as a mapping rather than as a parametrization so the refusals can
    also be compared with one another below: what separates them is what the
    developer is told, and a repair that funnelled two into one sentence would
    pass every assertion made about them individually.
    """
    def target(name: str, command: str | Path, **settings) -> Planning:
        return asking(tmp_path, name, command, **settings)

    unconfigured = build_planning(tmp_path, name="unconfigured")
    nothing_there = tmp_path / "never-written" / "nothing.sh"
    failing = fixture_command(tmp_path / "failing", "exits-non-zero.sh",
                              "echo 'the tracker refused the read' >&2\n"
                              "exit 1\n")
    sleeping = fixture_command(tmp_path / "sleeping", "sleeps.sh",
                               f"sleep {LONGER_THAN_THE_BOUND}\n")
    noisy = fixture_command(tmp_path / "noisy", "noisy.sh",
                            "echo 'reading the tracker'\n"
                            "printf '%s' '{}'\n")
    return {
        "no command configured": (unconfigured, KEY),
        "an empty key": (target("empty-key", answering_command(
            tmp_path / "for-empty", {"brief": a_brief()})), ""),
        "a command that cannot be launched": (
            target("unlaunchable", nothing_there), KEY),
        "a non-zero exit": (target("non-zero", failing), KEY),
        "a timeout": (target("timeout", sleeping,
                             **{TIMEOUT_KEY: str(FETCH_BOUND_SECONDS)}), KEY),
        "prose beside the document": (target("noisy", noisy), KEY),
        "an answer failing the envelope": (target(
            "envelope", answering_command(tmp_path / "envelope",
                                          {"brief": "not an object"})), KEY),
        "a key that did not resolve": (target(
            "unresolved", answering_command(tmp_path / "unresolved", {})), KEY),
        "a brief failing its schema": (target(
            "malformed", answering_command(
                tmp_path / "malformed",
                {"brief": a_brief(severity="very bad")})), KEY),
        "a workflow with no definition": (target(
            "undefined-workflow", answering_command(
                tmp_path / "undefined",
                {"brief": a_brief(workflow=UNDEFINED)})), KEY),
    }


def refusal_output(planning: Planning, harness: Path, key: str) -> str:
    """One refused invocation, with everything it could have touched checked.

    The observations are made here rather than at each call site because they
    are the same observations for every way of failing: no session was
    invoked, the stories directory is unchanged, the working tree holds
    nothing new, and nothing was committed.
    """
    head = planning.head()
    before = planning.tree()

    result = plan_without_a_terminal(planning, harness, "--brief", key,
                                     L5_STUB_WRITE=writes(
                                         (relative_artifact(),
                                          planned(ADDING["name"]))))

    assert result.returncode != 0, result.stdout
    assert invocations(planning) == [], "a session was invoked"
    assert stories(planning) == [], "an artifact was written"
    assert planning.tree() == before, "the working tree changed"
    assert planning.status() == ""
    assert planning.head() == head
    return result.stdout + result.stderr


def test_every_fetch_failure_refuses_before_anything_is_invoked_or_written(
        tmp_path, planning_harness):
    """Each way on its own, because a repair can get one right while getting
    another wrong. Its control is the successful fetch elsewhere in this
    module, where the same fixture does invoke a session and does commit."""
    for way, (planning, key) in refusing_targets(tmp_path).items():
        said = refusal_output(planning, planning_harness, key)
        assert re.search(r"(?i)as text", said), (way, said)


def test_the_wording_of_the_refusals_tells_them_apart(tmp_path,
                                                      planning_harness):
    """A developer told only "the brief could not be fetched" cannot tell a key
    that did not resolve from a tracker that could not be reached."""
    said = {way: refusal_output(planning, planning_harness, key)
            for way, (planning, key) in refusing_targets(tmp_path).items()}

    assert len(set(said.values())) == len(said), sorted(said)
    assert re.search(r"(?i)did not resolve", said["a key that did not resolve"])
    assert "severity" in said["a brief failing its schema"]
    assert UNDEFINED in said["a workflow with no definition"]
    for name in (ADDING["name"], PRESERVING["name"]):
        assert name in said["a workflow with no definition"], name


def test_a_fetch_that_answers_invokes_a_session_and_commits(
        briefed, planning_harness):
    """The control every refusal above needs: the same script, the same target
    shape, and a command that answers -- which reaches a session, writes an
    artifact and commits it."""
    head = briefed.head()

    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=CONFIRMS + APPROVES + DECLINES,
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status == 0, output
    assert len(invocations(briefed)) == 1
    assert stories(briefed) != []
    assert briefed.head() != head


# ==========================================================================
# 6. The artifact contract is the one every other session is held to
# ==========================================================================


def test_a_brief_planned_artifact_is_stamped_from_an_observed_approval(
        briefed, planning_harness):
    """This story adds an input path and touches no output: the artifact goes
    to the configured stories directory, carries a mandate this process
    stamped, names its workflow, and is committed by l5-plan."""
    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=CONFIRMS + APPROVES + DECLINES,
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status == 0, output
    artifact = artifact_path(briefed)
    assert artifact.is_file()
    written = artifact.read_text(encoding="utf-8")
    assert f"workflow: {ADDING['name']}" in written
    assert f"{plan_mandate.MANDATE_KEY}:" in written
    assert briefed.git("status", "--porcelain", str(artifact)).stdout == ""
    assert artifact.name.removesuffix(".yaml") in briefed.subject()


def test_a_rejected_brief_driven_session_stamps_nothing_and_commits_nothing(
        briefed, planning_harness):
    """A rejection leaves the artifact in the working tree, unstamped and
    uncommitted, exactly as it does on any other path. Its control is the test
    above, where the same session approved is stamped and committed."""
    head = briefed.head()

    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=CONFIRMS + DECLINES,
        L5_STUB_WRITE=writes((relative_artifact(), planned(ADDING["name"]))))

    assert status != 0, output
    assert briefed.head() == head
    written = artifact_path(briefed).read_text(encoding="utf-8")
    assert f"{plan_mandate.MANDATE_KEY}:" not in written


def test_the_artifact_is_still_held_to_the_workflow_the_session_used(
        briefed, planning_harness):
    """The end-of-session refusal keeps working on the brief path: an artifact
    naming another workflow was planned against facts that are not its own.

    Its control is the test above, whose artifact names the workflow the
    session was rendered against and commits.
    """
    head = briefed.head()

    status, output = plan_on_a_terminal(
        briefed, planning_harness, "--brief", KEY,
        reply=CONFIRMS + APPROVES,
        L5_STUB_WRITE=writes((relative_artifact(),
                              planned(PRESERVING["name"]))))

    assert status != 0, output
    assert ADDING["name"] in output
    assert PRESERVING["name"] in output
    assert briefed.head() == head
