"""Validate a story artifact at plan time, where it was written.

The planner writes an artifact and hands it to the developer; until this
module existed nothing read it back until l5-run refused it at pre-flight, by
which time the interactive session that could have repaired it in one
exchange had closed. Since story-023 scripts/l5-plan outlives the session it
runs, so validation fits between the snapshot it takes before the session and
the commit it makes after: a failing artifact is left in the working tree,
uncommitted, with its problems reported, which is the state a developer can
fix and re-run from.

The classes of problem reported here are listed below, and this module
invents neither of the first two. Schema conformance is
story_coordinator.read_story; agreement of a story's stage_exceptions with
the loaded workflow is story_coordinator.stage_exception_problems. Neither parsing, schema
validation nor the exception cross-check is reimplemented here — a plan-time
check with its own reader is the divergence story-005 existed to remove — and
the messages are the coordinator's own, so a given defect reads the same at
plan time as at pre-flight.

The third, fourth, fifth, seventh and ninth classes are this module's own
and each runs at plan time only, for one shared reason: none is refused by
l5-run, because adding it to pre-flight would, for each of them, start
refusing committed artifacts that have already run. The sixth is this
module's own too and is described below, and it is the one class of which
pre-flight also refuses a half. The eighth is this module's own and runs at
plan time only for a reason of its own, which its section below gives,
because no committed artifact carries the field it reads. They are otherwise
unlike each other, and the difference is worth stating rather than blending.
The third scans English prose and carries the hedging that entails. The
fourth, fifth, seventh, eighth and ninth are structural — a literal in the
artifact against a literal in the workflow definition, a literal in the
artifact against one pattern, the shape of an entry against one pattern, and
a path in the artifact against the paths the execution rules block — and
inherit none of that hedging: no clause split, no vocabulary of scoping
words, no paraphrase they can be evaded by, nothing they decline to decide.

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
declared restricted prefixes, and carries no word scoping it to creation. A
pairing whose two halves both sit inside one quoted span — a run delimited by
backticks or by double quotes — is a quoted pattern rather than a clause about
the stage and is not reported; strictness_problems says why.
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

What the sixth check is
-----------------------
A story artifact may name the workflow definition its run loads, and neither
thing that can be wrong with that name survives to run time usefully. A name
with no definition under workflows/ refuses the run at pre-flight, so
catching it here is catching it where the session that could repair it is
still open; the wording comes from harness_config, so the two refusals are
one fact rather than two spellings. A name other than the one this planning
session was rendered against is refused here and only here: the planner's
stage list and create restrictions are injected before the interview, so an
artifact declaring another workflow was planned against facts that are not
its own, and refusing it is what makes those injected facts trustworthy
rather than merely usual. An artifact naming no workflow is nothing to
report — its run loads the target configuration's default.

What the seventh check is
-------------------------
Every scope.do_not_modify entry carries the developer's reason, written in a
closing parenthetical after the path or area. The list had been filled in by
default: a planner left a file alone because nothing told it otherwise, and a
story then changed a rule in one file while the same rule stood written in
two or three others, each of which the judging stage declined for scope alone
and an inspection later found. The reason is what a later reader uses to tell a
developer's decision from a planner's default, and the check refuses an
entry carrying none where the developer is still present to supply one.

It reads shape and not prose. An entry passes when non-empty text precedes
a closing parenthetical holding at least one word character; a bare path
and an empty parenthetical are reported. Nothing parses, matches or scores
the reason's text — what a reason says is what a reviewer weighs, the shape
stage_exceptions.reason and reverting_breaks_the_suite already have. The
reason is a parenthetical on the string rather than a second field, because
the schema-directed parser reads a field's items as one type and object
items would stop every committed artifact parsing at pre-flight.

It is plan-time only, for the reason the third, fourth and fifth are:
committed artifacts under the stories directory are history, are not
rewritten, and carry bare entries, so refusing them at pre-flight would make
those stories unrunnable and l5-run keeps accepting them.

What the eighth check is
------------------------
A plan fixes every problem its planning finds in the part of the system the
story changes, and a gap it leaves — one lying in a different part of the
system, one the story's workflow cannot do, or one waiting on a decision the
developer chooses not to make now — is recorded in gaps_left, the one place
in a plan a gap is recorded. Each entry is the gap followed by the
developer's reason in a closing parenthetical, and the reason names the
ground. An entry carrying no reason is reported, on exactly the terms the
seventh class reports a do_not_modify entry and through the same predicate,
carries_a_reason: a bare gap and an empty parenthetical are the two things
refused, and nothing parses, matches or scores what the reason says, which
is the developer's to give and a reviewer's to weigh.

It is plan-time only, and the seventh class's reason is not its reason: no
committed artifact carries gaps_left, so there is nothing at pre-flight it
would start refusing. What makes it a plan-time check is what the reason is
for. A reason is the developer's to supply, plan time is where the developer
is present to supply one, and no check, routing or pre-flight decision in a
run reads the list — every stage is handed it only as part of the story text
— so a refusal at pre-flight would stop a run, with nobody there to supply
the reason, over a record no decision of the coordinator's depends on.

What the ninth check is
-----------------------
A plan must not schedule work on a path no stage of a run may write. A
scope.modify entry, or a technical_plan.likely_file_changes file, at or
beneath a path the execution rules block for every stage describes work the
run cannot do: the run discovers it only after a stage has spent its turn
and its changed-files record names the path. Such an entry is reported,
naming the entry and the blocked path, and saying that the change goes in
gaps_left instead, as a gap left because the story's workflow cannot do it.

Whether a path is blocked is decided by story_coordinator.is_blocked, the
reading the run-time blocked-path check makes, against the blocked paths in
the execution rules the coordinator enforces, loaded from the harness root
artifact_problems is handed. A path merely beside a blocked one is not
refused, because is_blocked does not refuse it.

It is plan-time only, for the reason the third, fourth, fifth and seventh
are: story-074's committed artifact names a blocked file in its modify list
and has already run, so refusing it at pre-flight would make that story
unrunnable.

Nothing here repairs an artifact and nothing here deletes one. The problems
are returned rather than printed, and the caller decides.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Iterable

import harness_config
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

#: A quoted span: a run delimited by backticks or by double quotes, holding no
#: delimiter of its own kind inside it. Single quotes are not a delimiter —
#: an apostrophe would open a span an ordinary possessive never closes.
_QUOTED_SPAN = re.compile(r"`[^`]*`|\"[^\"]*\"")


def _names_stage(clause: str, stage: str) -> bool:
    """A stage is named on a word boundary, so a possessive is still a mention."""
    return re.search(rf"\b{re.escape(stage)}\b", clause) is not None


def _without_quoted_pairings(entry: str, restrictions) -> str:
    """Blank out each quoted span holding a stage name beside a prefix.

    Found on the whole entry before it is split into clauses, so a quoted
    pattern that happens to contain a clause boundary stays one span. A span
    holding only one half of a pairing is left as it is, so that half still
    counts as sitting outside every quoted span. The two halves are read off
    one restriction at a time: a span pairing one restriction's stage with
    another restriction's prefix is no pairing the clause loop would report,
    so it is left as it is.
    """
    restrictions = list(restrictions)

    def blank(match: re.Match) -> str:
        span = match.group(0)
        if any(restriction.prefix in span
               and _names_stage(span, restriction.stage)
               for restriction in restrictions):
            return " "
        return span

    return _QUOTED_SPAN.sub(blank, entry)


def strictness_problems(story: dict, stages: list[dict]) -> list[str]:
    """Report entries that restrict a stage more strictly than the workflow does.

    One problem per (entry, restriction) pair: an entry that over-restricts
    two different stages has two things wrong with it, but an entry that says
    the same wrong thing twice is reported once. The message quotes the entry,
    names the offending clause, and states the restriction in the workflow's
    own words, so a planner repairing it can see both the sentence written and
    the rule it was reaching for.

    Only creation-sense restrictions are scanned, because only they have an
    over-strict restatement to report. What this reports is a sentence that
    names a stage together with a prefix without scoping it to creation — a
    stage restricted only from *creating* under a path may still modify one
    there, so an unscoped sentence demands more than the workflow does. A
    confinement makes no such distinction: it governs everything outside the
    prefixes it names whatever the act, so a sentence naming the stage together
    with the prefix it is confined to restates the rule accurately and has
    nothing over-strict in it. Reporting one would refuse a planner for writing
    down what the workflow says.

    A pairing that sits inside a quoted span is not reported either. A quoted
    span is a run of the entry delimited by backticks or by double quotes;
    single quotes are not a delimiter, because an apostrophe would make an
    ordinary possessive read as the start of one. A sentence such as "grep for
    `<stage>|<prefix>` over a file returns nothing" quotes a search pattern —
    it is text about text, and the stage name sits beside the prefix by
    construction of the pattern rather than as a clause about the stage — so it
    restates no restriction and there is nothing over-strict in it. The spans
    are found on the whole entry before it is split into clauses, so a quoted
    pattern that carries a comma or the word "and" stays one span. Only a span
    holding both halves excludes the pairing: a pairing with either half outside
    every quoted span is reported exactly as it always was, whether or not the
    entry carries a quote mark elsewhere.
    """
    restrictions = [
        restriction
        for restriction in story_coordinator.stage_restrictions(stages)
        if restriction.sense == story_coordinator.CREATE_RESTRICTION
    ]
    problems: list[str] = []
    for field in SCANNED_FIELDS:
        for index, entry in enumerate(story.get(field, [])):
            reported: set[tuple[str, str]] = set()
            scanned = _without_quoted_pairings(entry, restrictions)
            for clause in _CLAUSE_BOUNDARY.split(scanned):
                if _CREATION.search(clause):
                    continue
                for restriction in restrictions:
                    stage, prefix = restriction.stage, restriction.prefix
                    if (stage, prefix) in reported:
                        continue
                    if prefix in clause and _names_stage(clause, stage):
                        reported.add((stage, prefix))
                        problems.append(
                            f"$.{field}[{index}]: {entry!r} states a restriction "
                            f"the workflow does not. The clause "
                            f"{clause.strip()!r} names {stage} together with "
                            f"{prefix} without scoping it to creation, while the "
                            f"workflow declares only: {restriction.wording}"
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
    "modification, which the revert check governs: it restores the edits that "
    "restriction governs and re-runs the suite, and unless reverting them "
    "breaks it, it undoes them in the working tree and the run carries on "
    "without them. The entry declares no such forced adaptation."
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

    An entry offends when a restriction on the entry's own stage governs its
    file, no grant on that stage covers the file, and it is not a modification
    the entry declares forced. Whether a restriction governs a path is the
    restriction's decision and no sense is read here, so an entry naming a file
    outside the stage's confinement is refused for the reason one naming a file
    beneath its may_not_create prefix is, with the same three ways out stated
    in the confinement's own wording rather than the creation wording. An entry
    naming a file *at or beneath* a confinement is not refused: that is where
    the stage may write. The grant short-circuits
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
        for restriction in restrictions:
            if restriction.stage != name or not restriction.governs(path):
                continue
            present = (Path(root) / path).exists()
            if present and declares:
                continue
            fault = _MODIFICATION if present else _CREATION_FAULT
            resolutions = _RESOLUTIONS.format(path=path, stage=restriction.stage)
            if present:
                resolutions += _DECLARATION_RESOLUTION.format(path=path)
            problems.append(
                f"$.technical_plan.likely_file_changes[{index}]: assigns "
                f"'{path}' to stage '{name}', which the workflow declares: "
                f"{restriction.wording}. {fault} " + resolutions
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


def workflow_problems(story: dict, harness_root: Path, selected: str) -> list[str]:
    """Report a story whose declared workflow cannot be the one it will run under.

    A story artifact may name the workflow its run loads. Two things can be
    wrong with that name and both are decidable the moment the artifact is
    written. It may have no definition under workflows/, which is refused with
    the harness's own wording — the name asked for beside the names the
    harness holds — from harness_config, so plan time and pre-flight report an
    unknown workflow identically rather than from two spellings that agree
    today. And it may name a workflow other than the one this planning session
    was rendered against, which is refused naming both: the planner's stage
    list and create restrictions are injected before the interview that would
    otherwise choose, so an artifact disagreeing with the invocation is a plan
    written against facts that are not the facts of the workflow it declares.
    That refusal is what makes the injected facts trustworthy rather than
    merely usual.

    An artifact naming no workflow yields nothing: its run loads the target
    configuration's default, exactly as every artifact written before the
    field existed does.

    Both `harness_root` and `selected` are required and neither is defaulted.
    Defaulting the root would resolve the definitions against whatever
    directory the process stands in, and defaulting the selection would let a
    caller check agreement against a workflow it never rendered.
    """
    declared = story.get("story", {}).get("workflow")
    if not declared:
        return []
    problems: list[str] = []
    if declared not in harness_config.workflow_names(harness_root):
        problems += harness_config.UnknownWorkflow(
            declared, harness_config.workflow_names(harness_root)
        ).problems
    if declared != selected:
        problems.append(
            f"$.story.workflow: names '{declared}', but this planning session "
            f"was rendered against '{selected}'. The planner's stage list and "
            f"create restrictions are injected before the session starts, so a "
            f"plan declaring another workflow was written against facts that "
            f"are not that workflow's. Re-plan with --workflow {declared}, or "
            f"name '{selected}' in the artifact."
        )
    return problems


#: The form a passing entry takes, shown in each problem so a planner repairing
#: one sees what to write rather than only what was wrong.
_PASSING_ENTRY_FORM = "<path or area> (<the developer's reason for leaving it alone>)"


# Whether a scope.do_not_modify entry or a gaps_left entry carries a reason is
# decided by one definition, and it lives in the coordinator since story-183:
# the run-time check that refuses a scope extension against a reasoned entry
# reads the same predicate the plan-time checks over both lists read, and this
# module already imports the coordinator, so the reverse import would be
# circular. Imported under the same name, so every reader of
# plan_validation.carries_a_reason is unchanged.
carries_a_reason = story_coordinator.carries_a_reason


def scope_reason_problems(story: dict) -> list[str]:
    """Report scope.do_not_modify entries carrying no reason.

    The seventh class, plan-time only for the reason the third, fourth and
    fifth are: committed artifacts carry bare entries and refusing them at
    pre-flight would make those stories unrunnable.

    One problem per offending entry, naming the entry's index, quoting the
    entry and showing the form a passing entry takes. An entry passes when
    non-empty text precedes a closing parenthetical holding at least one word
    character; a bare path and an empty parenthetical are each reported.
    Structural, like the fourth and fifth: one entry against one pattern, with
    no prose read and nothing it declines to decide — what the reason says is
    a reviewer's to weigh.

    A story with no scope, a scope with no do_not_modify, and an empty list
    each yield nothing rather than raising; an entry that is not a string is
    the schema's to refuse and is skipped here.
    """
    scope = story.get("scope")
    entries = scope.get("do_not_modify", []) if isinstance(scope, dict) else []
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, str) or carries_a_reason(entry):
            continue
        problems.append(
            f"$.scope.do_not_modify[{index}]: {entry!r} carries no reason. "
            f"Every do_not_modify entry is the path or area followed by the "
            f"developer's reason for leaving it alone, in a closing "
            f"parenthetical: {_PASSING_ENTRY_FORM}. The reason is what a "
            f"later reader uses to tell a developer's decision from a "
            f"planner's default."
        )
    return problems


#: The form a passing gaps_left entry takes, shown in each problem the eighth
#: and ninth classes report so a planner repairing one sees what to write.
_PASSING_GAP_FORM = (
    "<the gap the plan leaves> (<the developer's reason, naming the ground "
    "it is left on>)"
)

#: The grounds a gap may be left on, as a reason names them. Size is not one
#: of them: a gap in the part of the system the story changes is fixed by the
#: story, because a gap left comes back as a brief and a brief costs far more
#: than the task and criterion that would have fixed it here.
_GAP_GROUNDS = (
    "it lies in a different part of the system",
    "it cannot be done under the story's workflow",
    "it waits on a decision the developer chooses not to make now",
)


def gap_reason_problems(story: dict) -> list[str]:
    """Report gaps_left entries carrying no reason.

    The eighth class, plan-time only for the reason the module docstring
    gives it. One problem per offending entry, naming the entry's index,
    quoting the entry, and showing the form a passing entry takes and the
    grounds a reason may name. Whether an entry carries a reason is decided
    by carries_a_reason, the predicate that decides it for a do_not_modify
    entry, so the two lists are held to one reading: a bare gap and an empty
    parenthetical are refused, and any reason at all passes whatever it says.

    The entries are read through story_coordinator.gaps_left, the reader the
    approval display takes them from, so what is checked here is what the
    developer is shown. A story with no gaps_left, and an empty list, yield
    nothing.
    """
    problems: list[str] = []
    for index, entry in enumerate(story_coordinator.gaps_left(story)):
        if carries_a_reason(entry):
            continue
        problems.append(
            f"$.{story_coordinator.GAPS_LEFT}[{index}]: {entry!r} carries no "
            f"reason. Every {story_coordinator.GAPS_LEFT} entry is the gap "
            f"followed by the developer's reason for leaving it, in a closing "
            f"parenthetical: {_PASSING_GAP_FORM}. The reason names the ground "
            f"the gap is left on — {'; '.join(_GAP_GROUNDS)} — and size is not "
            f"a ground: a gap in the part of the system the story changes is "
            f"fixed by the story."
        )
    return problems


def blocked_work_problems(story: dict, blocked: list[str]) -> list[str]:
    """Report plan entries scheduling work on a path no stage may write.

    The ninth class, plan-time only for the reason the module docstring
    gives it. One problem per scope.modify entry, and per
    technical_plan.likely_file_changes file, at or beneath a path in
    `blocked` — the execution rules' blocked paths, which apply to every stage
    — naming the entry and the blocked path and saying the change goes in
    gaps_left instead. Whether a path is blocked is decided by
    story_coordinator.is_blocked, asked of one blocked path at a time so the
    problem can name the one that blocks it, so a path merely beside a blocked
    one is not refused.

    A story with no scope.modify and no technical_plan yields nothing; an
    entry that is not a string, and a likely_file_changes entry with no file,
    are the schema's to refuse and are skipped here.
    """
    def blocking(path: str) -> str | None:
        for prefix in blocked:
            if story_coordinator.is_blocked(path, [prefix]):
                return prefix
        return None

    def problem(where: str, path: str, prefix: str) -> str:
        return (
            f"{where}: names {path!r}, which is at or beneath {prefix!r}, a "
            f"path the execution rules block for every stage. No stage of a "
            f"run may change it, so a plan scheduling work there would be "
            f"stopped only after a stage had spent its turn on it. Leave it "
            f"out of the plan's work and record the change it would need in "
            f"{story_coordinator.GAPS_LEFT} instead, as a gap left because the "
            f"story's workflow cannot do it: {_PASSING_GAP_FORM}."
        )

    problems: list[str] = []
    declared = story.get("scope")
    modify = declared.get("modify", []) if isinstance(declared, dict) else []
    for index, entry in enumerate(modify):
        if not isinstance(entry, str):
            continue
        prefix = blocking(entry)
        if prefix is not None:
            problems.append(problem(f"$.scope.modify[{index}]", entry, prefix))
    plan = story.get("technical_plan")
    entries = plan.get("likely_file_changes", []) if isinstance(plan, dict) else []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            continue
        prefix = blocking(entry["file"])
        if prefix is not None:
            problems.append(problem(
                f"$.technical_plan.likely_file_changes[{index}]",
                entry["file"], prefix))
    return problems


def artifact_problems(
    artifacts: Iterable[Path],
    stages: list[dict],
    root: Path,
    harness_root: Path,
    selected: str,
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
    asks the target repository anything, and a defaulted root would let a
    caller resolve existence against the process working directory without
    saying so. The checks that read no filesystem — strictness_problems,
    naming_problems, scope_reason_problems and gap_reason_problems — keep
    their signatures.

    `harness_root` and `selected` are required for workflow_problems, which
    needs both of its one caller: the root is where the definitions live, and
    the selection is the workflow this session rendered the planner against.
    The harness root is also where the execution rules are read from for
    blocked_work_problems, which is handed the blocked paths rather than
    reading them itself — the rules file the coordinator enforces, read once
    per call rather than once per artifact.
    """
    blocked = harness_config.load_rules(Path(harness_root))["blocked_paths"]
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
            found += workflow_problems(reading.parsed, harness_root, selected)
            found += scope_reason_problems(reading.parsed)
            found += gap_reason_problems(reading.parsed)
            found += blocked_work_problems(reading.parsed, blocked)
        if found:
            problems[Path(artifact)] = found
    return problems
