"""Validate a story artifact at plan time, where it was written.

The planner writes an artifact and hands it to the developer; until this
module existed nothing read it back until l5-run refused it at pre-flight, by
which time the interactive session that could have repaired it in one
exchange had closed. Since story-023 scripts/l5-plan outlives the session it
runs, so validation fits between the snapshot it takes before the session and
the commit it makes after: a failing artifact is left in the working tree,
uncommitted, with its problems reported, which is the state a developer can
fix and re-run from.

Three classes of problem are reported and this module invents none of the
first two. Schema conformance is story_coordinator.read_story; agreement of a
story's stage_exceptions with the loaded workflow is
story_coordinator.stage_exception_problems. Neither parsing, schema
validation nor the exception cross-check is reimplemented here — a plan-time
check with its own reader is the divergence story-005 existed to remove — and
the messages are the coordinator's own, so a given defect reads the same at
plan time as at pre-flight.

The third class is this module's own and runs at plan time only. It is never
refused by l5-run, which is why plan time is the only place it can be caught;
adding it to pre-flight would refuse committed artifacts that already ran.

What the third check is
-----------------------
A story artifact must not state a workflow restriction more strictly than the
workflow declares it. A stage restricted only from *creating* files under a
path may still modify one there — a legitimate change can break an existing
test, and the suite has to stay green — so an entry demanding the stage leave
the path alone entirely is an unenforced rule the harness cannot see broken,
and one a legitimate change can make impossible to satisfy. It costs a
verification finding and a human adjudication on every story that carries it,
and four committed artifacts carry one. Prompt guidance against paraphrasing is
the mechanism that failed all four times, so the check goes where the
artifact is written.

The check is a clause-level scan of the three free-text arrays a story is
evaluated against. An entry is split into clauses on commas, semicolons and
the standalone word "and", and a clause is reported
when it names a stage the loaded workflow defines, names one of that stage's
declared restricted prefixes, and carries no word scoping it to creation.
Both halves of that match are read off the loaded workflow definition through
story_coordinator.stage_restrictions; no stage name and no restricted prefix
is written in this module.

What it does not catch
----------------------
The general problem — is this English sentence stronger than that declared
rule — is not decidable, and this is the narrow, stated version of it rather
than an attempt at it. Two classes are outside it by construction:

- A phrasing that names neither the stage nor the prefix. A sentence that
  describes the stage by its role and the path by what it contains — "the
  stage that writes the code leaves the regression suite untouched" —
  restricts exactly what the reported sentences restrict and is matched on
  nothing, because both halves of the match are literals read off the
  workflow. Any paraphrase of either half passes.
- A strictness the clause split does not isolate. The split is what makes the
  check work at all: the historical entries confirm a file *was created* by
  one stage in their first clause and over-restrict a second stage in their
  second, so an entry-level creation-word test would let every one of them
  through. The cost is the mirror case — a single clause that both restricts
  creation and restricts more than creation, of the form "<stage> neither
  creates nor modifies files under <prefix>" — which is scoped to creation as
  far as this check can see and is not reported, even though it also forbids
  modification.

It errs the other way too, and deliberately: a clause that merely *describes*
the restriction, or grants something wider than it, is reported if it names
both halves without a creation word. Refusing a well-meant sentence costs one
rephrasing at plan time, which is where a human is present to do it.

Nothing here repairs an artifact and nothing here deletes one. The problems
are returned rather than printed, and the caller decides.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import story_coordinator

#: The free-text arrays a story is evaluated against.
#: All three are prose a paraphrased restriction can be written into, and the
#: historical instances sit in two of them.
SCANNED_FIELDS = ("acceptance_criteria", "verification_requirements", "constraints")

#: A clause boundary: a comma, a semicolon, or the standalone word "and".
_CLAUSE_BOUNDARY = re.compile(r"[,;]|\band\b")

#: A clause scoped to creation says so with one of these. The restriction is
#: about adding files, so a clause that speaks of adding is restating it
#: rather than tightening it.
_CREATION = re.compile(
    r"\b(?:creat(?:e|es|ed|ing|ion)|add(?:s|ed|ing|ition|itions)?|new)\b",
    re.IGNORECASE,
)


def _names_stage(clause: str, stage: str) -> bool:
    """A stage is named on a word boundary, so a possessive is still a mention."""
    return re.search(rf"\b{re.escape(stage)}\b", clause) is not None


def strictness_problems(story: dict, stages: list[dict]) -> list[str]:
    """Report entries that restrict a stage more strictly than the workflow does.

    One problem per (entry, restriction) pair: an entry that over-restricts
    two different stages has two things wrong with it, but an entry that says
    the same wrong thing twice is reported once. The message quotes the entry,
    names the offending clause, and states the restriction in the workflow's
    own words, so a planner repairing it can see both the sentence written and
    the rule it was reaching for.
    """
    restrictions = story_coordinator.stage_restrictions(stages)
    problems: list[str] = []
    for field in SCANNED_FIELDS:
        for index, entry in enumerate(story.get(field, [])):
            reported: set[tuple[str, str]] = set()
            for clause in _CLAUSE_BOUNDARY.split(entry):
                if _CREATION.search(clause):
                    continue
                for stage, prefix in restrictions:
                    if (stage, prefix) in reported:
                        continue
                    if prefix in clause and _names_stage(clause, stage):
                        reported.add((stage, prefix))
                        problems.append(
                            f"$.{field}[{index}]: {entry!r} states a restriction "
                            f"the workflow does not. The clause "
                            f"{clause.strip()!r} names {stage} together with "
                            f"{prefix} without scoping it to creation, while the "
                            f"workflow declares only: {stage} may not create "
                            f"files under {prefix}"
                        )
    return problems


def artifact_problems(
    artifacts: Iterable[Path], stages: list[dict]
) -> dict[Path, list[str]]:
    """Validate each artifact a planning session added; report what is wrong.

    Keyed by artifact path, holding only the artifacts that have problems, so
    an empty mapping is the whole of "these may be committed". The order of
    the artifacts given is preserved, because the caller reports in it.

    read_story is called once per artifact — the same one reading the run
    itself makes — and its parse is what the two structural checks are given.
    An artifact read_story has something to say about yields that and nothing
    further, which is run_story's own pre-flight order rather than a second
    one: a story that failed to parse has no parse to check, and a story that
    failed its schema has one whose shape the later checks may not assume.

    No harness_root override is passed to read_story, exactly as run_story
    omits one: the story schema ships with the harness code and
    schema_validator resolves it relative to its own module, so plan time and
    pre-flight load the one file.
    """
    problems: dict[Path, list[str]] = {}
    for artifact in artifacts:
        reading = story_coordinator.read_story(
            Path(artifact).read_text(encoding="utf-8")
        )
        found = list(reading.problems)
        if not found:
            found += story_coordinator.stage_exception_problems(reading.parsed, stages)
            found += strictness_problems(reading.parsed, stages)
        if found:
            problems[Path(artifact)] = found
    return problems
