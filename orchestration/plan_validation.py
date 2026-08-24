"""Validate a story artifact at plan time, where it was written.

The planner writes an artifact and hands it to the developer; until this
module existed nothing read it back until l5-run refused it at pre-flight, by
which time the interactive session that could have repaired it in one
exchange had closed. Since story-023 scripts/l5-plan outlives the session it
runs, so validation fits between the snapshot it takes before the session and
the commit it makes after: a failing artifact is left in the working tree,
uncommitted, with its problems reported, which is the state a developer can
fix and re-run from.

Five classes of problem are reported and this module invents none of the
first two. Schema conformance is story_coordinator.read_story; agreement of a
story's stage_exceptions with the loaded workflow is
story_coordinator.stage_exception_problems. Neither parsing, schema
validation nor the exception cross-check is reimplemented here — a plan-time
check with its own reader is the divergence story-005 existed to remove — and
the messages are the coordinator's own, so a given defect reads the same at
plan time as at pre-flight.

The third, fourth and fifth classes are this module's own and all three run
at plan time only, for one shared reason: none is refused by l5-run, because
adding it to pre-flight would, for each of the three, start refusing
committed artifacts that have already run. They are otherwise unlike each other, and the
difference is worth stating rather than blending. The third scans English
prose and carries the hedging that entails. The fourth and fifth are
structural — a literal in the artifact against a literal in the workflow
definition, and a literal in the artifact against one pattern — and inherit
none of that hedging: no clause split, no vocabulary of scoping words, no
paraphrase they can be evaded by, nothing they decline to decide.

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
A plan must not assign a governed path to a stage without saying what makes
it acceptable. A technical_plan.likely_file_changes entry offends when its
file falls beneath a prefix the entry's own stage is restricted under, no
grant on that stage covers it, and — for a file the target root already holds
— the entry does not declare the edit a forced test adaptation. Literals
decide it: one in the artifact and one in the workflow definition, plus the
story's own grants and the presence of the entry's own declaration.

Two run-time checks act on a stage beneath a governed prefix, and an entry
falls into one of three categories against them. A **creation** the ownership
check refuses outright. An **unforced modification** the revert check
refuses: it restores the stage's edits beneath the governed prefixes and
re-runs the suite, and an implementation change reverts cleanly by
construction — the assertion that would hold the new behaviour belongs to a
later stage and has not been written yet — while a comment-only change is
refused by the same arithmetic. A **forced test adaptation**, made necessary
by a deliberate change elsewhere in the same story, is a modification the
revert check *permits*, because reverting it does break the suite; five
consecutive runs contain one being permitted while ungranted. Refusing that
third category would refuse plans that would have run to completion.

Nothing at plan time can compute which category an entry is in — whether
reverting an edit breaks the suite depends on an edit that does not exist yet
— and nothing structural separates the second from the third either, since
both name files the target root already holds. The judgement is the
planner's, and the entry states it in reverting_breaks_the_suite. Its
presence is the structural signal and its text is what a reviewer weighs, the
shape stage_exceptions.reason already has; no check parses, matches or scores
it.

The grant remains the other reconciliation, and it already existed: a granted
path is skipped by the revert check's governed_edits exactly as it is skipped
by the ownership check. The two are not interchangeable and the difference is
the point. A grant makes the revert check skip the path, which is what a
story whose deliverable is the governed file itself needs; a declaration
leaves the revert check governing it, so the half of the claim plan time
cannot check is adjudicated at run time by the check that can. No run-time
check is weakened, anticipated or told the declaration exists.

Existence decides the **wording** of a refusal, and it decides whether the
declaration is read at all. An absent file describes a creation the ownership
check refuses, declaration or not, so it is refused exactly as it always was.
A present one describes a modification the revert check governs. Both
wordings open with the same resolution clause — reassign the file to a stage
that may own it, or declare a stage_exceptions grant naming that file for
that stage, whose reason field is required — so a plan repaired at either
fault is repaired the same way, and the grant is named because the failure
mode this check exists against was not knowing the field exists. A refused
modification names a third way out after it, declaring the edit forced, which
is a resolution only a file that exists can have.

Existence is resolved against the **target root**, the repository the story
will run in, which artifact_problems requires of its caller and passes down.
It is neither the harness root nor the process working directory: the three
coincide when the harness is its own target and will not in general, and no
default hides which one was consulted.

The judgement being moved here is *is this file implementation or test logic
for this story*, which is a question about intent that a human answers where
a human is present. It is not moved into the revert check, which would have
to read a diff as language to answer it. Neither run-time check is weakened,
anticipated or duplicated by this: what a stage may do once a run has started
is exactly what it was.

That is why it is structural rather than a scan of English. It compares an
entry's declared file against an entry's declared stage and against the
story's grants; it does not read prose, does not guess at intent, and does
not err in either direction. Nothing about it is hedged, and the limits
recorded above for the third check are not its limits. likely_file_changes is
its subject because it is the only field carrying a file and a stage together
— scope.modify names paths with no stage and cannot state this conflict at
all. A story with no technical_plan, or an entry missing either field, yields
nothing rather than raising.

What it stays is a **prediction**. A file present when the plan is written
may be gone by the time the story runs, and this check makes no promise about
that beyond which of the two faults it named: the run-time ownership check
and the revert check remain the authority on what a stage was actually
allowed to do.

What the fifth check is
-----------------------
A validation module must not be named for the story number that produced it.
The number is meaningful while the story is in flight and meaningless once it
merges: the name then says only that somebody once worked on a numbered
thing, and a reader looking for the revert check, the resume guard or the
retry routing can find it by grep and by nothing else. story-038 renamed
thirty-four such modules; this is one of the three mechanisms that hold the
convention afterwards, and it is the deterministic one — the other two are a
standing scan in the suite and one sentence in a prompt, and a prompt is the
layer that has failed at holding a convention here before.

Its subject is technical_plan.likely_file_changes, like the fourth check's,
because that is where a plan states which files it expects to be written and
a name is decidable there, before anything exists under it. It matches the
module's *basename* and names no directory, so this module still contains no
path prefix. A module wearing a story number is the same planning error
wherever it is put, and the pattern being independent of location is what
lets the promise above stay true.

Nothing here repairs an artifact and nothing here deletes one. The problems
are returned rather than printed, and the caller decides.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
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


#: The fault an offending entry describes when the target root holds no such
#: file: the stage would have to create it, and the ownership check refuses a
#: created file beneath a governed prefix outright.
_CREATION_FAULT = (
    "The target root holds no such file, so the entry describes a creation, "
    "which the stage output ownership check refuses outright."
)

#: The fault an offending entry describes when the file is already there and
#: the entry declares nothing. The revert check is the instrument, and stating
#: what it does is what stops the message reading as a claim that modifying is
#: forbidden: it is permitted exactly when reverting it breaks the suite,
#: which an implementation change or a comment-only change never does — and
#: which a test adaptation forced by a deliberate change elsewhere in the same
#: story does. Plan time cannot compute which of those an entry is, because
#: whether reverting an edit breaks the suite depends on an edit that does not
#: exist yet, so an entry claiming the third category says so in
#: reverting_breaks_the_suite and is accepted here without a grant.
_MODIFICATION = (
    "The target root already holds that file, so the entry describes a "
    "modification, which the revert check governs: it restores the stage's "
    "edits beneath that prefix and re-runs the suite, and refuses them unless "
    "reverting them breaks it. The entry declares no such forced adaptation."
)

#: Both wordings open with this clause, identically, and a refused
#: modification names a third way out after it. The grant is named because
#: not knowing the field exists is the failure this check is written against,
#: and its required reason is stated because that is what makes the grant a
#: judgement a reviewer can weigh.
_RESOLUTIONS = (
    "Either assign '{path}' to a stage that may own it, or declare a "
    "stage_exceptions grant naming '{path}' for {stage}, whose reason field is "
    "required."
)

#: The third way out, and it applies to a modification alone: declaring
#: nothing helps a file that is not there, because a claim about reverting a
#: file that does not exist asserts nothing and the ownership check refuses a
#: creation outright either way. It is named beside the grant, and against it:
#: a grant makes the revert check skip the path, while a declaration leaves it
#: governing, so the half of the claim plan time cannot check is adjudicated
#: at run time by the check that can.
_DECLARATION_RESOLUTION = (
    " If instead this edit is a test adaptation forced by a deliberate change "
    "elsewhere in this story, so that reverting it would break the suite, say "
    "so in reverting_breaks_the_suite on the entry: unlike a grant, that "
    "leaves the revert check governing '{path}' and deciding the claim when "
    "the story runs."
)


def assignment_problems(story: dict, stages: list[dict], root: Path) -> list[str]:
    """Report plan entries assigning a file to a stage that cannot own it.

    The subject is technical_plan.likely_file_changes and it is the only place
    this conflict can be stated: an entry carries an explicit file and stage
    pair, while scope.modify names paths with no stage at all and so cannot
    say who was meant to write one. One problem per offending entry and
    restriction.

    An entry offends when its file falls beneath a prefix the entry's own
    stage is restricted under, no grant on that stage covers the file, and it
    is not a modification the entry declares forced. The grant short-circuits
    above everything else here; the declaration is read only for a path the
    target root already holds, because a claim about reverting a file that is
    not there asserts nothing and the ownership check refuses a creation
    outright whatever the entry says.

    Existence chooses between the two **wordings** once an entry has already
    been decided to offend, so the two faults read as what they are while
    remaining one verdict. Both wordings open with the same resolution clause,
    built once, so a plan repaired at either fault is repaired the same way; a
    modification carries a third way out beside it, since declaring the edit
    forced is a resolution only a file that exists can have.

    `root` is the repository existence is resolved against — the target root,
    not the harness root and not the process working directory. It is required
    and carries no default, so no caller can silently be given whatever
    directory the process happens to be standing in. Existence is decided with
    exists() on the root joined with the entry's path, so a path present as a
    directory counts exactly as a regular file does.

    Both halves of the restriction come off story_coordinator.stage_restrictions
    and the grant is decided by story_coordinator.grant_covers, so no stage name
    and no prefix is written here — the same promise the check beside this one
    makes; the path comes off the entry.

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
        declared = entry.get("reverting_breaks_the_suite")
        declares = isinstance(declared, str) and declared.strip() != ""
        for stage, prefix in restrictions:
            if stage != name or not path.startswith(prefix):
                continue
            present = (Path(root) / path).exists()
            if present and declares:
                continue
            fault = _MODIFICATION if present else _CREATION_FAULT
            resolutions = _RESOLUTIONS.format(path=path, stage=stage)
            if present:
                resolutions += _DECLARATION_RESOLUTION.format(path=path)
            problems.append(
                f"$.technical_plan.likely_file_changes[{index}]: assigns "
                f"'{path}' to stage '{name}', which the workflow declares: "
                f"{stage} may not create files under {prefix}. {fault} "
                + resolutions
            )
    return problems


#: A validation module named for the story that produced it rather than for
#: what it checks. The number is meaningful only while the story is in
#: flight; once it merges, the name says that somebody once worked on a
#: numbered thing, and a reader looking for the revert check or the resume
#: guard can only grep.
#:
#: Matched on the *basename*, and the directory is deliberately not part of
#: the pattern — this module names no path prefix, which is the same promise
#: the two checks above make and which a test holds it to. A module wearing
#: this name is the same planning error wherever it is put.
STORY_NUMBERED_MODULE = re.compile(r"^test_story_\d+")


def naming_problems(story: dict) -> list[str]:
    """Report plan entries naming a test module for a story number.

    The fifth class, plan-time only for the reason the third and fourth are:
    committed artifacts that have already run carry these names, and refusing
    them at pre-flight would make those stories unrunnable.

    The subject is technical_plan.likely_file_changes, for the same reason
    assignment_problems takes it — it is where a plan says which files it
    expects to be written, and a name is decidable there, before anything is
    written under it. Structural, like its neighbour: one literal in the
    artifact against one pattern, with no prose to read and nothing it
    declines to decide.

    A story carrying no technical_plan, and an entry with no file, yield no
    problem rather than an error.
    """
    plan = story.get("technical_plan")
    entries = plan.get("likely_file_changes", []) if isinstance(plan, dict) else []
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        path = entry.get("file")
        if not path or not STORY_NUMBERED_MODULE.match(PurePosixPath(path).name):
            continue
        problems.append(
            f"$.technical_plan.likely_file_changes[{index}]: names "
            f"'{path}'. Name a validation module for the behaviour it "
            f"validates, not for the story number that produced it — the "
            f"number stops meaning anything the moment the story merges, "
            f"and a reader looking for that behaviour can then only grep."
        )
    return problems


def artifact_problems(
    artifacts: Iterable[Path], stages: list[dict], root: Path
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

    `root` is the target repository the stories will run in, required for the
    same reason assignment_problems requires it: it is the only check here that
    asks the filesystem anything, and a defaulted root would let a caller
    resolve existence against the process working directory without saying so.
    The two checks that read no filesystem — strictness_problems and
    naming_problems — keep their signatures.
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
            found += assignment_problems(reading.parsed, stages, root)
            found += naming_problems(reading.parsed)
        if found:
            problems[Path(artifact)] = found
    return problems
