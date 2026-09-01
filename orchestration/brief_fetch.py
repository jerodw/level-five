"""Fetching one brief by key, so a planning session can be handed a brief.

This is the second question the configured filed-query command answers, and it
is the link that makes the chain a loop: without it briefs are filed where
nothing in the harness can read them back, and the only way to plan one is for
a human to copy its body into a shell argument or for an agent to relay it in
its own words -- which is worse, because what the planner then works from is a
paraphrase of the evidence rather than the evidence.

**It is a different question from dedupe and is asked as one.** The dedupe
question asks what is filed against a path set and answers with a key, a title,
a short summary and the paths, every text field bounded, because that answer
must stay cheap however large the tracker grows. This one needs the opposite --
one brief, in full, unbounded by those per-field limits, because a truncated
brief is a brief that plans wrong -- so it is asked by key rather than by path,
since asking by path would answer with a set and leave the caller guessing
which member was meant.

**The fetch refuses where the dedupe query absorbs, and the difference is
deliberate.** `filed_query.query` is total because it runs inside a producer
that must not be blocked by a tracker being unreachable: a failed query costs
dedupe and costs nothing else. This runs inside a terminal invocation a human
asked for, where proceeding on a brief nobody could read would mean planning
against nothing, so every failure here refuses that invocation. Nothing about
`query` changes for it -- this module raises on nothing either, and returns
what happened rather than printing it, so the wording a developer reads lives
in the script.

**The key is opaque.** It is sent exactly as it was given and nothing here
resolves it, normalizes it, joins it against a root, checks that it exists or
decides anything from its form; a URL, a repository-relative document path and
a digest are all keys, and which of those a target's keys are is the answering
command's business. An empty key is the one thing refused without asking,
because there is nothing to ask about.

**A brief is held to `schemas/story-brief.schema.json` and to no second,
looser shape.** The envelope says only that what came back is an object
carrying an optional brief; the brief itself is validated on its own, so one
that is malformed in a field is refused naming the field rather than the shape.

**The brief is a plan-time input only.** Nothing here writes it anywhere,
stores it or reads it back, and no coordinator, run, resume or sweep path
reaches this module: the only trace of a brief after the planning session is
its key in the story's description, as prose.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import filed_query
import harness_config
import schema_validator

#: The shape a command answers a brief-fetch question with.
ENVELOPE_SCHEMA = "fetched-brief"

#: The shape the brief it carries is held to, on its own.
BRIEF_SCHEMA = "story-brief"


@dataclass(frozen=True)
class Fetched:
    """One brief, or the reason there is none.

    `brief` is what came back and is None wherever nothing did. `reason` says
    why, and is empty on a fetch that answered. The two are never both set:
    a caller reads the brief or reports the reason.
    """

    brief: dict | None = None
    reason: str = ""

    @property
    def fetched(self) -> bool:
        """Whether there is a brief to plan from."""
        return self.brief is not None


def _refused(reason: str) -> Fetched:
    """The one construction site for a fetch that carries no brief.

    Every way of not having one funnels through here, so they cannot disagree
    about what such an answer looks like: no brief, and a reason saying which
    way it was.
    """
    return Fetched(brief=None, reason=reason)


def fetch(key: str, config: dict, target_root: Path | None = None,
          harness_root: Path | None = None) -> Fetched:
    """The brief filed under `key`, or the reason it could not be planned from.

    Raises on nothing: every failure comes back as a refusal carrying its
    reason, so the caller decides what a refusal is worth and this decides
    only what happened.

    The settings come from `filed_query.resolve_settings`, so this module reads
    no configuration key of its own and the command that answers both questions
    is named in one place. The question is one JSON document on stdin carrying
    the key under "key", where the dedupe question carries the paths under
    "paths"; the key goes into it verbatim.

    An answer that satisfies the envelope but carries no brief is refused as
    the key not having resolved, and is worded differently from an answer that
    could not be obtained at all -- a command that ran and looked has said
    something a command that never ran has not.
    """
    if not str(key).strip():
        # The one refusal made without asking the command, because there is
        # nothing to ask about. Every other judgement about what a key means
        # belongs to the command that answers.
        return _refused(
            "no brief key was given, and an empty key names nothing to fetch"
        )

    settings, problem = filed_query.resolve_settings(config)
    if problem:
        return _refused(
            f"the configuration a fetch runs under was refused: {problem}"
        )
    if settings is None:
        return _refused(
            f"no {filed_query.COMMAND_KEY} is configured, so there is no "
            "command to ask what is filed under that key"
        )

    question = json.dumps({"key": key}, sort_keys=True)
    text, problem = filed_query.run_bounded(settings, question, target_root)
    if text is None:
        return _refused(problem)

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        return _refused(
            "the command's stdout is not a single JSON document, so nothing "
            f"was read from it: {error}. Diagnostics belong on stderr; stdout "
            "carries the document and nothing else"
        )

    problems = schema_validator.validate(
        document, schema_validator.load_schema(ENVELOPE_SCHEMA, harness_root)
    )
    if problems:
        return _refused(
            "the command's answer does not satisfy the fetched-brief schema: "
            + "; ".join(problems)
        )

    brief = document.get("brief")
    if brief is None:
        return _refused(
            f"the command ran and found nothing filed under {key}, so that key "
            "did not resolve to a brief"
        )

    problems = schema_validator.validate(
        brief, schema_validator.load_schema(BRIEF_SCHEMA, harness_root)
    )
    if problems:
        # Named by the field that failed rather than by the shape, in the way
        # the Inspector already names a finding it dropped.
        return _refused(
            f"the brief filed under {key} does not satisfy the story-brief "
            f"schema: {problems[0]}"
        )

    undefined = workflow_problem(brief, harness_root)
    if undefined:
        return _refused(undefined)

    return Fetched(brief=brief, reason="")


def workflow_problem(brief: dict, harness_root: Path | None = None) -> str:
    """Why the brief's workflow cannot be planned under, or "" where it can.

    The acceptable set is the definitions the harness holds rather than a list
    written here, so a third workflow becomes plannable by shipping a
    definition and with no edit to this module or to either schema -- which is
    the same derivation the Inspector makes when it drops a finding naming a
    workflow nothing defines.
    """
    named = brief.get("workflow")
    defined = harness_config.workflow_names(harness_root)
    if named in defined:
        return ""
    listed = ", ".join(defined) if defined else "no workflow definitions"
    return (
        f"the brief names the workflow '{named}', which the harness does not "
        f"define; it defines: {listed}"
    )


def render(brief: dict, key: str) -> str:
    """The brief as the request text a planning session is given.

    A block rather than a summary: the planner receives the brief's own prose,
    so what it plans from is the evidence rather than a paraphrase of it. It
    lives here rather than in the script for the reason
    `workflow_selection.candidate_block` does -- it is content a test should be
    able to assert on without driving a session, and it is derived from the
    brief rather than written at a call site.

    The key is carried into it with the instruction to record it in the story's
    description, which is the whole of the traceability this adds: the brief is
    not written into the artifact, not stored and not re-read, so its key as
    prose is the only trace of it that survives the session.
    """
    lines = [brief["title"], "", brief["body"]]

    paths = brief.get("paths") or ()
    if paths:
        lines.append("")
        lines.append("The paths this is about:")
        lines.extend(f"  - {path}" for path in paths)

    not_in_scope = brief.get("not_in_scope") or ()
    if not_in_scope:
        lines.append("")
        lines.append("What a story planned from this should deliberately "
                     "leave alone:")
        lines.extend(f"  - {one}" for one in not_in_scope)

    lines.append("")
    lines.append(
        f"This request is the brief filed under {key}. Record that key in the "
        "story's description, as prose, so the story says what it was planned "
        "from."
    )
    return "\n".join(lines)
