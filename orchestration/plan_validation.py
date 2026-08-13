"""Validate a story artifact at plan time, where it was written.

The planner writes an artifact and hands it to the developer; until this
module existed nothing read it back until l5-run refused it at pre-flight, by
which time the interactive session that could have repaired it in one
exchange had closed. Since story-023 scripts/l5-plan outlives the session it
runs, so validation fits between the snapshot it takes before the session and
the commit it makes after: a failing artifact is left in the working tree,
uncommitted, with its problems reported, which is the state a developer can
fix and re-run from.

Four classes of problem are reported and this module invents none of the
first two. Schema conformance is story_coordinator.read_story; agreement of a
story's stage_exceptions with the loaded workflow is
story_coordinator.stage_exception_problems. Neither parsing, schema
validation nor the exception cross-check is reimplemented here — a plan-time
check with its own reader is the divergence story-005 existed to remove — and
the messages are the coordinator's own, so a given defect reads the same at
plan time as at pre-flight.

The third and fourth classes are this module's own and both run at plan time
only, for one shared reason: neither is refused by l5-run, because adding
either to pre-flight would start refusing committed artifacts that have
already run. They are otherwise unlike each other, and the difference is
worth stating rather than blending. The third scans English prose and carries
the hedging that entails. The fourth is structural — two literals compared,
one in the artifact and one in the workflow definition — and inherits none of
that hedging: it has no clause split, no vocabulary of scoping words, no
paraphrase it can be evaded by, and nothing it declines to decide.

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

What the fourth check is
------------------------
A plan must not assign work to a stage that cannot own it. A
technical_plan.likely_file_changes entry naming a file beneath a prefix its
own stage is restricted from creating under, with no grant covering that
file, describes a run that can only end one way: the stage does exactly what
the plan named, and the coordinator refuses the result. Both halves of the
conflict are literals — one in the artifact, one in the workflow definition —
so it is fully decidable before the run starts, and the decision belongs
where a developer is present to repair it in one exchange.

That is why it is structural rather than a scan of English. It compares an
entry's declared file against an entry's declared stage; it does not read
prose, does not guess at intent, and does not err in either direction.
Nothing about it is hedged, and the limits recorded above for the third check
are not its limits. likely_file_changes is its subject because it is the only
field carrying a file and a stage together — scope.modify names paths with no
stage and cannot state this conflict at all. A story with no technical_plan,
or an entry missing either field, yields nothing rather than raising.

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


def assignment_problems(story: dict, stages: list[dict]) -> list[str]:
    """Report plan entries assigning a file to a stage that cannot own it.

    The subject is technical_plan.likely_file_changes and it is the only place
    this conflict can be stated: an entry carries an explicit file and stage
    pair, while scope.modify names paths with no stage at all and so cannot
    say who was meant to write one. One problem per offending entry and
    restriction.

    An entry offends when its file falls under a prefix the entry's own stage
    is restricted from creating under and no grant on that stage covers the
    file. Both halves come off story_coordinator.stage_restrictions and the
    grant is decided by story_coordinator.grant_covers, so no stage name and
    no prefix is written here — the same promise the check beside this one
    makes.

    A story carrying no technical_plan, and an entry missing either field,
    yield no problem rather than an error: this reports a conflict it can see
    both halves of, and an absent half is nothing to report.
    """
    restrictions = story_coordinator.stage_restrictions(stages)
    plan = story.get("technical_plan")
    entries = plan.get("likely_file_changes", []) if isinstance(plan, dict) else []
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        path, name = entry.get("file"), entry.get("stage")
        if not path or not name:
            continue
        granted = story_coordinator.granted_paths(story, name)
        if story_coordinator.grant_covers(granted, path):
            continue
        for stage, prefix in restrictions:
            if stage == name and path.startswith(prefix):
                problems.append(
                    f"$.technical_plan.likely_file_changes[{index}]: assigns "
                    f"'{path}' to stage '{name}', which the workflow declares: "
                    f"{stage} may not create files under {prefix}. Either "
                    f"assign '{path}' to a stage that may own it, or declare a "
                    f"stage_exceptions grant naming '{path}' for {stage}."
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
    itself makes — and its parse is what the later checks are given.
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
            found += assignment_problems(reading.parsed, stages)
        if found:
            problems[Path(artifact)] = found
    return problems
