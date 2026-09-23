"""Reading a target's own code and writing story briefs for what is wrong with it.

The harness has never had a mechanism that looks at a target's code and says
what is wrong with it: every story it has run began with a human noticing
something. This module is the deterministic half of the one that does — scope
resolution, the filed query and what is dropped by it, the identity a brief is
filed under, the cap, the drop report, and the one enqueue call. The judgement
half is `prompts/inspector.md`, and the model is reached only through
`orchestration/agent_runner.py`, which is injected here so the suite drives a
fake one.

**A brief is not a story artifact and nothing executes one.** It is a
pre-planning artifact carrying intent, evidence and a severity; it is not an
approved plan and never becomes one on its own. A human plans it into a story
artifact through l5-plan's interview, and that interview is where the mandate is
conferred. There is nothing here to refuse, because the coordinator has no path
that runs a brief, and nothing in this module confers a mandate or writes
anything under the stories directory.

**This is the outbox's first producer**, and being one carries an obligation
`enqueue` states: it is total over what it is handed and answers with the empty
string when nothing landed. An empty answer is the item having been lost, not a
key — so nothing here is named after one, nothing is reported as filed on one,
and the report says an item was dropped. That is the whole of what a producer
owes the queue, and this module owes it because it is the first one.

**What a brief is filed under is `orchestration/story_brief.py`'s and not this
module's.** The kind, the bare-path rule, the identity and the payload live
there because there are two producers of briefs — this one and an assist
session filing one a developer asked for — and an identity derived twice is a
duplicate filed on every inspection. The names below are that module's, reached
through here so every existing reader of `inspection.identity` goes on reading
the same values. The outbox computes the key from that identity: this module
hashes nothing, derives no digest of its own, and reaches the queue only
through `outbox.enqueue`.

**Dedupe is two sources and neither waits on the other.** The filed query asks a
tracker what is already filed against a scope's paths. The local outbox queue is
read as an index of what *this harness* has already filed — free, with no
network and no configuration, the same read `l5-status` already makes. Both are
consulted on every inspection: the local index is not a fallback for a query
that failed, and the query is not a fallback for an index that is empty. A
fallback would make the answer depend on which source responded, and story-093
went out of its way to make nothing known distinguishable from nothing filed
precisely so a caller can say dedupe did not run. **`.harness/requests/` is
deliberately not a third source.** It holds the hand-written briefs a developer
filed by editing a file, and a request file carries no slug, no category and no
key, so there is nothing there to compare against the identity a brief is filed
under. Any match against it would be a text heuristic rather than a comparison,
and one that fires wrongly in both directions — suppressing a finding because
some sentence there resembled it, and missing one because nobody phrased it the
way the model did. No code this module runs reads that directory, and saying so
is part of the answer to which sources dedupe consults: an unstated omission is
indistinguishable from an oversight.

**Which state a queue entry is in decides what it is evidence of.** A landed
entry means the provider named what it holds, so it suppresses. A pending entry
is in the queue and not yet on any tracker, so it is reported as already queued
rather than already filed — treating it as filed would let the report claim it
filed something new when nothing external has seen it. A failed entry is
terminal and no later sync will file it, so it must not suppress: a failed entry
is a finding that reached nobody, and suppressing on it would lose the finding
permanently with no signal. "It is in the queue" is the tempting wrong rule.

**The local index is a subset and does not complete dedupe.** It knows only what
this machine filed; another machine's filings and anything filed by hand are
invisible to it, so it can miss a duplicate the query would catch. That is why
it is an addition rather than a replacement, and why `Report.dedupe_ran` remains
a statement about the filed query alone.

**What an inspection cost is carried, never re-derived.** The runner's own
result already reports what its invocation spent, so that figure is kept rather
than dropped on the floor and nothing anywhere reads an agent log back to
recover it. An invocation that reported nothing yields None rather than zero,
which is the distinction the record turns on: a zero is what an inspection that
genuinely cost nothing would carry, and conflating the two corrupts every
average taken from the corpus later. An inspection writes one line to the
declared cross-run log carrying that cost, which mode it was, how large its
scope was and how many findings came back and were filed, and commits it — by
name, never with `git add -A` — so the record does not sit in the working tree
as a dirty tree the next run's pre-flight refuses. Both halves are guarded: a
record that cannot be written costs the record and nothing else.

**An inspection that produced no report is told apart from one that reported
nothing.** A reviewer's process can die before it writes anything, and the
absence of a findings artifact was read as an answer: the run reported zero
findings, told the stage that judges the change that the inspection ran and was
clean, and moved on, while whatever that reviewer would have said was lost.
Every other agent the harness runs is already treated differently — a stage
whose process dies is simply run again — so an invocation that produced no
findings artifact is made once more, and only a second failure records the
drop. The trigger is the missing file and nothing else: never the number of
findings, which is why an envelope carrying an empty findings array is a real
answer and is never retried, and never what the invocation reported spending,
which stays corroboration in the record rather than something the routing
reads. Exactly one retry, fixed in code.

**A floor decides what is filed and never what a rating means.** Filing runs at
roughly three and a half briefs per completed story and planning drains none of
them, so a backlog that nothing bounds grows monotonically; `inspect_min_severity`
is the bound, and it is a floor rather than a quality bar because what the corpus
shows is arithmetic rather than thin briefs. It is applied in `file_findings`,
before the cap, so both producers of briefs get it from the same call and cannot
diverge on it. The Inspector's prompt is told the floor as well, and that is not
a duplication of one fact: the filter decides, and the prompt exists so an
invocation does not spend a three-hundred-word brief on a finding that will be
dropped unread. The severity scale itself is untouched by all of it.

**The area a brief names is the target's word, carried and resolved by
nothing.** Which areas a system has is target knowledge: the harness cannot
know them, must not carry them, and cannot assume a target has a tracker to
display them on. So the vocabulary is a document in the target's own standards
directory, which this module already hands the Inspector whole — the standards
are globbed and no file is looked for by name, so a target that declares no
areas is read as declaring none and is unaffected by every part of this. What
the harness gains is a slot rather than a taxonomy: a free-text field on the
brief, carried on the terms `paths` is already carried. Nothing here coins a
name, and where the Inspector could name none it writes a suggestion instead,
which is reported and filed by nothing.

**No silent bound.** Every way of dropping a finding is named in the report with
what it excluded: already filed by the tracker, already filed by this harness,
already queued, malformed, an unknown workflow, beneath the severity floor, past
the cap, lost by the queue.
A scope whose filed query could not answer is reported as dedupe not having run,
in those terms, and its findings are filed anyway — losing dedupe is not a
reason to lose the findings.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import agent_runner
import context_assembler
import filed_query
import harness_config
import outbox
import schema_validator
import story_brief
import workflow_selection

#: What this producer files, and what the other one files: one derivation
#: beneath both, so a brief a developer filed by hand and a finding this module
#: reported land on one key.
KIND = story_brief.KIND

#: The parts of a target's own source an inspection covers when no scope is
#: named on the command line.
SOURCE_DIRS_KEY = "source_dirs"

#: How many briefs one whole inspection may file.
MAX_FINDINGS_KEY = "inspect_max_findings"

#: The allowance one invocation may spend, handed to the invocation.
MAX_COST_KEY = "inspect_max_cost_usd"

#: The lowest severity an inspection files. A floor on what is filed and never
#: on what a rating means: a finding beneath it is rated exactly as the scale
#: says and is not written down anywhere a planner will meet it.
MIN_SEVERITY_KEY = "inspect_min_severity"

#: Where a target's tests live, which is a scope of its own beside the source
#: dirs rather than one of them.
TESTS_DIR_KEY = "tests_dir"

DEFAULT_MAX_FINDINGS = 10
DEFAULT_MAX_COST_USD = 5.0

#: Two, because the Inspector's own scale defines severity 1 as worth fixing the
#: next time somebody has the file open — which by that definition is not a
#: story. One means no floor.
DEFAULT_MIN_SEVERITY = 2

#: The severities the scale defines, which is what a declared floor must name.
MIN_SEVERITY_RANGE = (1, 3)
DEFAULT_LOGS_DIR = ".harness/logs"

#: The prompt the Inspector carries, and the two schemas its answer is held to.
INSPECTOR_PROMPT = "inspector.md"
FINDINGS_SCHEMA = "inspection-findings"
BRIEF_SCHEMA = "story-brief"

#: Where an invocation is asked to write its findings, and where its own output
#: is kept. Inside the workspace, under the configured logs directory, for the
#: reason `workflow_selection.selection_paths` gives: a turn asked to write
#: outside the workspace the permission mode accepts edits within reasons
#: correctly and then cannot deliver.
FINDINGS_ARTIFACT = "inspection-findings.json"
INSPECTION_LOG = "inspection.log"

#: The one tool a turn whose whole output is a file needs, granted on top of
#: whatever the target already grants a stage, so an inspector can search the
#: way a stage can and can also deliver.
DELIVERY_TOOL = "Write"

#: Which scope an invocation is looking at. Source and tests are a union rather
#: than a merge, and the Inspector is told which it has. CHANGE is the third,
#: and it is not a part of the tree at all: it is what one story changed, plus
#: what sits beside it, which is a set of paths a caller computes and hands
#: over rather than one a prefix describes.
SOURCE = "source"
TESTS = "tests"
CHANGE = "change"

#: The kind an inspection's record is appended under, and the two modes that
#: appear on one. Which cross-run log the kind reaches is the cross-run history
#: declaration's to say and not this module's, so no log filename is written
#: here. Both spellings live here rather than one per producer, so the narrow
#: mode's record and the broad mode's are one vocabulary and a reader querying
#: the log for either is querying for the value both producers write.
INSPECTION_EVENT = "inspection-completed"
MODE_BROAD = "broad"
MODE_NARROW = "narrow"

#: The subject the broad-mode record's commit carries. It leads with the
#: harness and carries no completion marker, so it matches neither the
#: completion shape `completion_commits` reads nor the escalation and pause
#: shapes beside it.
COMMIT_SUBJECT = "l5 recorded an inspection"

#: The question broad mode asks. Supplied by `scopes` below rather than left to
#: default in the render, so that both modes' framings are values their own
#: caller passes and neither can reach the template as the literal None.
BROAD_FRAMING = (
    "Ask whether this code is well designed: whether it says what it means, "
    "whether it agrees with the rules this repository declares about itself, "
    "and whether a competent maintainer would want to know about what you "
    "have found."
)

#: The ways a finding can be dropped. Each is named in the report with what it
#: excluded, because a bound whose effect is not stated reads as a scope with
#: nothing wrong in it.
#: The filed query knew it: a tracker reported it against this scope's paths.
ALREADY_FILED = "already filed"

#: The local queue holds it landed: this harness filed it and a provider named
#: what it holds. Distinct from the reason above so a reader is told which
#: source knew, and so a caller counting the ways an inspection dropped things
#: can count the two separately.
ALREADY_FILED_LOCALLY = "already filed by this harness"

#: The local queue holds it pending: this harness has it written down and no
#: tracker has seen it. Distinct from both reasons above because it is not the
#: same evidence — nothing external holds it, so reporting it as filed would
#: claim something that has not happened.
ALREADY_QUEUED = "already queued"

MALFORMED = "malformed"
#: An `area_suggestions` entry the reader could not use — not an object, a
#: `slug` or `concerns` missing or not a string, or blank — or a field that is
#: not a list at all. Distinct from MALFORMED because a suggestion is not a
#: finding: it reaches no brief, no payload, no outbox entry and no tracker, so
#: a count of findings must not count it, and a reader is owed which of the two
#: was refused. Named rather than skipped because a usable suggestion is one of
#: only two clues that the target declares an area vocabulary, and a refused
#: one is the same clue: dropping it silently silenced the report of the
#: area-less briefs beside it.
UNUSABLE_SUGGESTION = "unusable area suggestion"
UNKNOWN_WORKFLOW = "names a workflow the harness does not define"

#: The finding is real and rated honestly and is beneath the floor this
#: inspection files at. Distinct from every reason above and from the cap below,
#: because the floor excluded it on its severity alone and a reader is owed
#: which bound decided: it is not a duplicate, it is not malformed, and it was
#: not crowded out by findings that mattered more.
BENEATH_THE_FLOOR = "beneath the severity floor"

#: The finding is real and correct and the verdict that read it judged it
#: beneath what this repository files a brief at. Distinct from the floor above
#: and from the cap below, because a reader is owed which bound decided: the
#: floor reads the severity the Inspector rated, the cap excluded what was
#: crowded out by findings that mattered more, and this is a judgement a
#: verifier made about one finding after reading the change it is about. The
#: drop is never inferred from the prose of a decline: only the verdict's
#: explicit not_worth_filing drops a finding, so the coordinator stays
#: deterministic.
NOT_WORTH_FILING = "the verdict judged it not worth filing"

PAST_THE_CAP = "past the cap"
#: The sibling of `findings` an envelope may carry: what the Inspector could
#: name no area for. Spelled once here, because the envelope schema declares it
#: and this is the only module that reads it.
AREA_SUGGESTIONS = "area_suggestions"
LOST_BY_THE_QUEUE = "lost by the queue"
NO_ARTIFACT = "no findings artifact"


# --------------------------------------------------------------------------
# What a scope is
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """One unit of invocation: a part of the tree, and which half of it it is.

    `path` is a repository-relative prefix, or "" for everything the
    repository tracks. `kind` is SOURCE, TESTS or CHANGE and is what the
    Inspector is told it is looking at — the first two are a union and not a
    merge, so a tests scope is never folded into a source one. `excluded` is
    what this scope leaves to another scope, which is how the everything-scope
    keeps a declared tests directory out of itself without becoming a merge of
    the two.

    `paths` is an explicit list of the files this scope covers, for a caller
    that has computed them itself rather than describing them with a prefix.
    Where it is empty — every broad-mode scope — the paths are computed from
    `path` exactly as they always were. Where it is not, they are taken as
    given and no prefix is consulted, which is what lets a scope be a set of
    files with nothing structural in common.

    `origin` is where this scope came from where that is not a part of the
    tree, and it is what such a scope is labelled by and what a brief filed
    from it carries as its provenance. Empty for a scope a prefix describes,
    which leaves both of those exactly what they were.

    `framing` is the question the invocation is asked. It carries a value on
    every scope, supplied by whichever caller built it, so the placeholder it
    fills never renders as the literal None.
    """

    path: str
    kind: str
    excluded: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    origin: str = ""
    framing: str = BROAD_FRAMING

    @property
    def label(self) -> str:
        """What this scope is called in a report and in a prompt."""
        return self.origin or self.path or "the whole tracked tree"


def _prefix(path: str) -> str:
    """A scope written as a prefix, so a path is under it or is not.

    A trailing slash is added where the value does not already carry one, so
    `orchestration` and `orchestration/` name the same scope and neither of
    them matches `orchestration-notes.py`.
    """
    path = path.strip()
    if not path or path.endswith("/"):
        return path
    return path + "/"


def scopes(arguments, config: dict) -> tuple[Scope, ...]:
    """The scopes an invocation covers, one per agent invocation.

    Named paths win: two paths on the command line are two scopes and
    therefore two invocations. A named scope at or beneath the configured
    tests directory is a tests scope, so what the Inspector is told about the
    code it is reading does not depend on how the developer reached it.

    With nothing named, the scopes are one per `source_dirs` entry plus one for
    `tests_dir`. With no `source_dirs`, the source scope is the whole tracked
    tree with the tests directory left to its own scope — a target that
    declares neither key is inspected over its tracked tree rather than
    refused. In every arrangement tests_dir is a scope beside the source ones
    rather than one of them, and setting `source_dirs` decides nothing about
    the create restriction tests_dir governs.
    """
    tests_dir = config.get(TESTS_DIR_KEY)
    tests_prefix = _prefix(tests_dir) if tests_dir else ""

    named = [one for one in (argument.strip() for argument in arguments) if one]
    if named:
        return tuple(
            Scope(
                path=one,
                kind=TESTS
                if tests_prefix and _prefix(one).startswith(tests_prefix)
                else SOURCE,
                framing=BROAD_FRAMING,
            )
            for one in named
        )

    declared = config.get(SOURCE_DIRS_KEY) or []
    found = [
        Scope(path=one, kind=SOURCE, framing=BROAD_FRAMING)
        for one in declared if one.strip()
    ]
    if not found:
        # Nothing declared: everything the repository tracks, with the tests
        # directory left to the scope below rather than merged into this one.
        found = [
            Scope(
                path="",
                kind=SOURCE,
                excluded=(tests_prefix,) if tests_prefix else (),
                framing=BROAD_FRAMING,
            )
        ]
    if tests_prefix:
        found.append(Scope(path=tests_dir, kind=TESTS, framing=BROAD_FRAMING))
    return tuple(found)


def blocked_prefixes(harness_root: Path) -> tuple[str, ...]:
    """The paths no inspection reads, read off the execution rules.

    Read rather than restated, so a rule added there is excluded here with no
    edit to this module. A rules file that cannot be read excludes nothing
    rather than raising — but nothing here reaches that state in a working
    installation, and an inspection is not the place to discover it.
    """
    try:
        rules = harness_config.load_rules(harness_root)
    except (OSError, ValueError):
        return ()
    declared = rules.get("blocked_paths") or []
    return tuple(one for one in declared if isinstance(one, str) and one.strip())


def _tracked(target_root: Path, under: Scope) -> list[str]:
    """What git tracks beneath a scope, as repository-relative paths.

    The parameter is `under` rather than `scope` because a standing rule in
    the suite reports any decision under `orchestration/` whose subject reads
    a name called `scope` — that rule is about a suite run's recorded scope,
    which is a different thing entirely, and its matcher is deliberately blunt.
    Renaming one parameter is cheaper than exempting this module from a rule
    that is right about every other one.
    """
    argv = ["git", "-C", str(target_root), "ls-files", "-z", "--"]
    if under.path:
        argv.append(under.path)
    try:
        completed = subprocess.run(  # noqa: S603 - a fixed argument list
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return [one for one in completed.stdout.split("\0") if one]


def scope_paths(target_root: Path, scope: Scope,
                blocked: tuple[str, ...]) -> tuple[str, ...]:
    """The repository-relative paths beneath a scope that git tracks.

    Every path under a blocked path is excluded, and the blocked set comes
    from the execution rules rather than from anything written here. What the
    scope itself excludes — the tests directory, for the everything-scope — is
    excluded on the same terms, which is what keeps source and tests a union
    of two scopes rather than one scope that happens to contain both.
    """
    excluded = tuple(_prefix(one) for one in (*blocked, *scope.excluded) if one)
    return tuple(
        path for path in _tracked(target_root, scope)
        if not any(path.startswith(one) for one in excluded)
    )


# --------------------------------------------------------------------------
# What a brief is filed under
# --------------------------------------------------------------------------


#: The bare-path rule and the identity, reached through here so that every
#: reader of `inspection.bare_path`, `inspection.bare_paths` and
#: `inspection.identity` goes on reading exactly what it read before, while
#: there is one derivation beneath them rather than one per producer. The
#: reasoning behind each moved with it and is in `story_brief`'s own docstring.
bare_path = story_brief.bare_path
bare_paths = story_brief.bare_paths
identity = story_brief.identity


def payload(finding: dict, scope: Scope) -> dict:
    """What is filed with a brief: the finding, with its paths made bare.

    Where the brief came from is this producer's to supply — a brief nothing
    scoped carries an empty one — so this is the one of the four that is
    reached with an argument of this module's rather than re-exported whole.
    A scope that is a part of the tree carries that part; one that is not
    carries its own account of where it came from, which is what a post-story
    inspection's briefs carry. It is payload either way and never identity, so
    a finding filed under one and rediscovered under the other collapses onto
    one key.
    """
    return story_brief.payload(finding, scope.origin or scope.path)


# --------------------------------------------------------------------------
# What an inspection did
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Drop:
    """One finding that was not filed, and which way it was not filed.

    `severity` is carried where the finding had a readable one, so the cap can
    report the severity of each thing it excluded rather than only how many.
    """

    reason: str
    detail: str
    severity: int | None = None


@dataclass(frozen=True)
class AreaSuggestion:
    """One brief the Inspector could name no area for, and what it concerns.

    Reported and never written: nothing here reaches a brief, a payload, an
    outbox entry or a tracker, because the vocabulary of areas grows by a person
    editing the target's own document and a name no developer has approved must
    not be filed under. `slug` is what matches it to a brief that was actually
    filed, so a suggestion about a finding that was dropped is matched to
    nothing and printed by neither report surface.

    Only a usable pair becomes one of these. An entry the reader refused is
    not silently skipped but recorded as a Drop under UNUSABLE_SUGGESTION,
    named and with what was wrong, in the same list as the inspection's other
    drops — and it still counts as evidence that the target declares an area
    vocabulary, which is what keeps the area-less briefs beside it reported.
    """

    slug: str
    concerns: str


@dataclass(frozen=True)
class Filed:
    """One brief that reached the queue, and the key it was filed under."""

    key: str
    slug: str
    title: str
    severity: int
    scope: str
    #: The area of the target's own declared vocabulary this brief named, and
    #: the empty string where it named none. Carried so a report can say which
    #: filed briefs came with no area, which is the whole of what makes a
    #: missing area reported rather than silent. Defaulted so every existing
    #: construction stays valid, and empty is a brief that named none rather
    #: than a record written before the field existed — the two are the same
    #: thing here, because nothing infers an area for a brief that carries
    #: none.
    area: str = ""
    #: Whether the local queue held this brief's key in the terminal failed
    #: state when the inspection began. Evidence that filing this identity has
    #: been tried and keeps failing — `enqueue` overwrites the whole entry, so
    #: the attempt count and the last error do not survive the refile and this
    #: is the only place a developer meets that fact. It decides nothing: the
    #: brief is filed either way, and the mark is taken from the index read
    #: before any filing, so a key this same inspection just overwrote is still
    #: reported as a refile. Defaulted so every existing construction stays
    #: valid.
    refiled_over_failure: bool = False


def was_made(dropped) -> bool:
    """Whether every invocation behind these drops produced a findings artifact.

    The one predicate beneath everything that has to tell an inspection that
    produced no report from one that reported nothing: the summary line the run
    writes, the findings record the judging stage reads, and the boolean the
    cross-run record carries. Three statements about one inspection, and three
    readings of what makes it true would be three statements that can disagree.

    Read off the drops rather than off a flag, because the drop is already the
    record of it: `NO_ARTIFACT` is appended once per scope whose invocations
    both came back with nothing, and it is appended nowhere else. So this is
    answerable of a scope result and of a whole report alike, which is what
    lets one predicate serve a mode that inspects one scope and a mode that
    inspects several — and over several, false means at least one of them
    produced no report, which is the reading `Report.dedupe_ran` beside it
    already takes of its own question.

    An inspection that made no invocation at all — a bound it could not obey,
    nothing in scope — has no such drop, so this answers true of it. That is
    why its callers state their own case rather than deriving it from here: a
    caller that made no invocation knows it, and this answers the narrower
    question of whether an invocation that was made delivered.
    """
    return not any(one.reason == NO_ARTIFACT for one in dropped)


def not_made_detail(dropped) -> str:
    """Why the inspection behind these drops could not be made, in one string.

    The companion of `was_made`, and here for the reason that one is: the
    vocabulary of drop reasons belongs to this module, so a caller wording a
    line about a missing report asks for the words rather than reaching for the
    constant. A standing rule holds the run's summary line to naming no reason
    constant of this module's — it counts whatever reasons the drops carry, so
    that a reason added here reaches that line with no edit there — and a
    caller that had to name this one to say why would have been the exception
    that makes the rule stop meaning anything.

    The details of each such drop, joined, so a mode that inspects several
    scopes says why each of them produced nothing; empty where every
    invocation delivered, which is a caller asking a question it has already
    had answered.
    """
    return "; ".join(one.detail for one in dropped if one.reason == NO_ARTIFACT)


def finding_key(finding) -> str:
    """The outbox key one finding would be filed under.

    The identity this module already derives, put through the queue's own
    digest — so a caller comparing two findings for sameness compares them by
    exactly what the queue would file them under, rather than by a second
    notion of when two findings are the same one. `outbox.identity_key` is
    still the only derivation of a key in the harness and this module still
    hashes nothing; what this adds is a name for the pair, so a reader that is
    not this module's neighbour need not reach the queue to ask the question.
    """
    return outbox.identity_key(identity(finding))


@dataclass(frozen=True)
class Dedupe:
    """Whether the filed query answered for one scope, and what it said.

    `ran` false is dedupe not having run for that scope. The inspection files
    what it found anyway: a query that could not answer costs dedupe and costs
    nothing else, which is the same bias the query module itself takes.
    """

    scope: str
    ran: bool
    known: int = 0
    reason: str = ""
    excluded: tuple[str, ...] = ()


#: What this harness has already filed, derived in the one module where the two
#: directories are defined. It moved there in story-107 because a filing path
#: asks the same question, and two derivations of one rule are two answers that
#: can disagree — with nothing local able to notice, since a landed entry drops
#: the payload a comparison would have needed. The names this module already
#: exposed resolve to the same objects, so every existing reader of
#: `inspection.LocalIndex` and `inspection.local_index` reads what it read
#: before.
LocalIndex = outbox.LocalIndex
local_index = outbox.local_index


@dataclass(frozen=True)
class Recorded:
    """What recording an inspection did, or why it did nothing.

    The shape `plan_mandate.commit_records` already has, kept here rather
    than shared with it: sharing the class would couple the inspection to the
    planner for no gain. `written` says the record's line reached the history;
    `committed` says the commit that makes it durable landed; `detail` says
    why not where either is false, in git's own words where git had any. The
    default instance means no record was attempted, which is what a dry run's
    report carries.

    A record that was written and not committed is left in the working tree
    with nothing staged — the tree the commit found, restored rather than
    repaired — and that is what the detail is for: the next run's clean-tree
    pre-flight refuses on exactly that tree, and this is the one line that
    connects the refusal to the inspection that caused it.

    `written` false with `committed` false is a record that never reached the
    history, and the detail says why in the words `record` uses for that —
    the history directory could not be written, or the declaration routes an
    inspection to no log — so a reader is sent to the write and not to the
    commit. A record written and not committed because the target's ignore
    rules keep every record path out of version control carries
    `NOT_TRACKED_DETAIL`, which is an answer git gave to `check-ignore` and
    not an inference from a file's absence; it exists so that target is not
    told its record failed.
    """

    written: bool = False
    committed: bool = False
    detail: str = ""


@dataclass(frozen=True)
class Report:
    """What one inspection inspected, filed and dropped.

    Returned rather than printed, in the shape `plan_commit` and
    `workflow_selection` already have: every decision is here and every word a
    developer reads is `scripts/l5-inspect`'s.
    """

    scopes: tuple[Scope, ...] = ()
    invocations: int = 0
    filed: tuple[Filed, ...] = ()
    dropped: tuple[Drop, ...] = ()
    dedupe: tuple[Dedupe, ...] = ()
    #: What the local queue held, on every inspection including one where it
    #: held nothing — a source that is silent when it found nothing is
    #: indistinguishable from one that did not run. Defaulted so this dataclass
    #: stays constructible as the error paths above already construct it.
    local_index: LocalIndex = LocalIndex()
    dry_run: bool = False
    #: What this inspection's invocations reported spending, summed, and None
    #: where none of them reported anything. Carried from the runner's own
    #: result and never re-derived, so a caller has the figure without a second
    #: traversal and without a second parser of the harness's own output.
    cost_usd: float | None = None
    #: How many files were in scope across every invocation. What a cost means
    #: depends on how much was read, so the two are reported together.
    scope_files: int = 0
    #: The severity floor this inspection ran under, carried so the report can
    #: say it on every inspection including one where the floor excluded
    #: nothing: a bound that is silent when it dropped nothing is
    #: indistinguishable from a bound that is not there.
    min_severity: int = DEFAULT_MIN_SEVERITY
    #: What the invocations could name no area for, as they wrote it. Carried
    #: to the report because both report surfaces print it beside the briefs it
    #: is about, and filed by nothing: see `AreaSuggestion`.
    area_suggestions: tuple[AreaSuggestion, ...] = ()
    #: What recording this inspection did — written, committed, or why not.
    #: Defaulted to "no record was attempted", which is what a dry run and
    #: every construction that predates the field carry; `inspect` attaches
    #: the result of `record` on a non-dry run, so `scripts/l5-inspect` can
    #: say on every real inspection whether the record commit landed.
    record: Recorded = Recorded()

    def dropped_for(self, reason: str) -> tuple[Drop, ...]:
        """Everything dropped one way, so a caller can say each way once."""
        return tuple(drop for drop in self.dropped if drop.reason == reason)

    @property
    def unnamed_areas(self) -> tuple[tuple[Filed, str], ...]:
        """Each filed brief that named no area, with its suggestion or "", and
        nothing at all from an inspection of a target that declares no areas.

        One derivation beneath both report surfaces, because the post-story
        report and the broad inspection's report say the same thing on the same
        terms and two derivations of one rule are two answers that can
        disagree. A suggestion is matched to a brief **by slug**, so a
        suggestion naming a finding that was dropped rather than filed matches
        nothing and is printed by neither surface: what an area-less line
        reports is briefs that were filed, and a suggestion about work nobody
        filed would be a line about nothing.

        The gate is what keeps a target that declares no vocabulary reported
        exactly as it was before. The harness may hold and read no target's
        vocabulary, so it never sees the document, and every brief such a target
        files names no area — a line per area-less filing would therefore be a
        line on every brief every target that never opted in ever files. What an
        inspection of such a target cannot have is either of the two things
        below: a suggestion, which the Inspector writes only where a vocabulary
        it was handed declares names none of which fit, or a sibling filing that
        named an area. Where neither is present there is no evidence of a
        vocabulary at all, and nothing is said.

        A suggestion counts as that evidence whether or not it was usable: a
        refused entry — recorded under UNUSABLE_SUGGESTION in `dropped` — was
        still written, and it is written only where a vocabulary was handed
        over. Reading only the usable ones let a slip in the sentence meant to
        explain the gap silence the report of the gap, so an inspection whose
        every brief named no area and whose only suggestion was refused still
        returns each filed brief, with an empty suggestion beside it.
        """
        suggested = {
            one.slug: one.concerns for one in self.area_suggestions
            if one.concerns
        }
        declares_areas = (
            bool(suggested)
            or bool(self.dropped_for(UNUSABLE_SUGGESTION))
            or any(brief.area for brief in self.filed)
        )
        if not declares_areas:
            return ()
        return tuple(
            (brief, suggested.get(brief.slug, ""))
            for brief in self.filed if not brief.area
        )

    @property
    def dedupe_ran(self) -> bool:
        """Whether every scope's filed query answered.

        A statement about the filed query alone, deliberately: the local index
        is a subset that knows only what this machine filed, so a duplicate
        filed elsewhere is invisible to it and reading it successfully does not
        make dedupe complete.
        """
        return all(one.ran for one in self.dedupe)


@dataclass
class _Found:
    """One accepted finding on its way to the queue, with where it came from."""

    finding: dict
    scope: Scope


@dataclass
class _ScopeResult:
    """What one scope produced: what it found, dropped, and cost.

    `cost_usd` is the sum of what the runner's own results reported, carried
    and never computed — nothing here reads an agent log back to recover it.
    None is a runner that returned nothing and a result carrying no cost alike:
    the harness was told no figure, which is not the same as a figure of zero,
    and keeping the two apart is what stops a zero nobody reported from being
    averaged later as though it were one.

    `invocations` is how many invocations were actually made for this scope:
    one ordinarily, and two where the first produced no findings artifact and
    was made again. It is carried rather than assumed by the caller, so the
    `invocations` and `cost_usd` a record reports corroborate each other — a
    record saying two invocations is how a reader of the log afterwards knows
    a retry happened, and a caller that counted one per scope would have said
    the second invocation's cost was the first one's.
    """

    found: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    dedupe: Dedupe = None
    cost_usd: float | None = None
    scope_files: int = 0
    #: What this invocation could name no area for, read off its envelope.
    area_suggestions: tuple = ()
    invocations: int = 0


# --------------------------------------------------------------------------
# The bounds
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """The bounds an inspection runs under, already resolved.

    `min_severity` is the lowest severity this inspection files, defaulted here
    to the harness default so that every construction that predates it stays
    valid and resolves to what a target with the key unset gets.
    """

    max_findings: int
    max_cost_usd: float
    min_severity: int = DEFAULT_MIN_SEVERITY


def bounds(config: dict):
    """The bounds, or the reason they are refused.

    Returns `(bounds, problem)`, in the shape `filed_query.resolve_settings`
    and the sweep's transport build already take: a terminal caller refuses a
    configuration it cannot obey. None of them falls back to its default when it
    is declared and unreadable — a bound that cannot be read is a bound the
    target did not declare, and obeying the default in its place would obey a
    number nobody wrote. Every problem is reported together with the others, so
    a target that got more than one wrong is told all of them rather than one at
    a time.
    """
    problems: list[str] = []

    max_findings = DEFAULT_MAX_FINDINGS
    declared = config.get(MAX_FINDINGS_KEY)
    if declared is not None:
        try:
            max_findings = int(str(declared))
        except (TypeError, ValueError):
            max_findings = 0
        if max_findings <= 0:
            problems.append(
                f"{MAX_FINDINGS_KEY}: {declared!r} is not a positive integer, "
                "and an inspection must be bounded in how many briefs it files"
            )

    max_cost = DEFAULT_MAX_COST_USD
    declared = config.get(MAX_COST_KEY)
    if declared is not None:
        try:
            max_cost = float(declared)
        except (TypeError, ValueError):
            max_cost = 0.0
        if max_cost <= 0:
            problems.append(
                f"{MAX_COST_KEY}: {declared!r} is not a positive number of US "
                "dollars, and every invocation must be bounded in cost"
            )

    lowest, highest = MIN_SEVERITY_RANGE
    min_severity = DEFAULT_MIN_SEVERITY
    declared = config.get(MIN_SEVERITY_KEY)
    if declared is not None:
        try:
            min_severity = int(str(declared))
        except (TypeError, ValueError):
            min_severity = 0
        if not lowest <= min_severity <= highest:
            problems.append(
                f"{MIN_SEVERITY_KEY}: {declared!r} is not an integer in "
                f"{lowest} through {highest}, and the floor an inspection files "
                "at has to name a severity the scale defines"
            )

    if problems:
        return None, "; ".join(problems)
    return Bounds(
        max_findings=max_findings,
        max_cost_usd=max_cost,
        min_severity=min_severity,
    ), ""


# --------------------------------------------------------------------------
# One scope
# --------------------------------------------------------------------------


def findings_paths(target_root: Path, config: dict):
    """Where an invocation writes its findings, and where its output is kept.

    Both inside the workspace, under the configured logs directory, and both
    derived here so neither name is written at a call site.
    """
    logs = target_root / config.get("logs_dir", DEFAULT_LOGS_DIR)
    return logs / FINDINGS_ARTIFACT, logs / INSPECTION_LOG


def _already_filed_block(answer) -> str:
    """The items the query reported, rendered as data for the Inspector.

    Data to recognise its own earlier work by, and not instructions: the
    deterministic drop below has already removed everything whose key matched,
    so what reaches the model is the rest — items a query reported that this
    module could not match by key, which is exactly what a reader rather than
    a comparison is needed for.
    """
    if not answer.answered:
        return ("The query that would say what is already filed did not answer, "
                "so nothing is known about it and dedupe has not run for this "
                f"scope: {answer.reason}")
    if not answer.items:
        return "Nothing is already filed against these paths."
    return "\n\n".join(
        "\n".join(filter(None, [
            f"key: {item.key}",
            f"title: {item.title}",
            f"summary: {item.summary}" if item.summary else "",
            f"paths: {', '.join(item.paths)}" if item.paths else "",
        ]))
        for item in answer.items
    )


def _severity_floor_block(min_severity: int) -> str:
    """The floor in force, rendered for the Inspector.

    In the shape `_already_filed_block` has, and for the reason that one is a
    block rather than a bare value: a floor of 1 has to read as there being no
    floor rather than as nothing below severity 1, which is the same sentence
    and tells an invocation nothing. It states the floor and says what to do
    about it — write no brief — because the failure this exists against is an
    invocation spending a long brief on a finding the filter will drop unread.
    Telling the invocation is not the enforcement: `file_findings` is, and this
    block only saves the effort.
    """
    lowest, _ = MIN_SEVERITY_RANGE
    if min_severity <= lowest:
        return ("There is no severity floor on this inspection. A finding is "
                "filed whatever its severity, so write a brief for anything "
                "worth reporting.")
    return (
        f"This inspection files severity {min_severity} and above. A finding "
        f"beneath severity {min_severity} is not filed: it is dropped, and no "
        "planner ever sees it. So write no brief for such a finding — the "
        "effort is wasted, and raising its rating to get it past the floor is "
        "not the fix, because the scale above means what it says and a rating "
        "that has been inflated to clear a filter makes every other rating "
        "worth less."
    )


def _render(harness_root: Path, target_root: Path, config: dict, scope: Scope,
            paths: tuple[str, ...], answer, artifact: Path,
            min_severity: int = DEFAULT_MIN_SEVERITY) -> str:
    """The prompt one invocation is given."""
    context = context_assembler.schema_context(harness_root)
    context["scope"] = scope.label
    context["scope_kind"] = scope.kind
    # Supplied by the scope its caller built rather than defaulted here, so the
    # two modes' questions are each their own caller's and neither can reach
    # the template unset.
    context["framing"] = scope.framing
    context["scope_paths"] = "\n".join(paths) or "(this scope tracks no files)"
    # Whatever the target declares as standards, as one undifferentiated body,
    # read by the shared reader every judged stage's context is built from —
    # globbed there, never looked for by name, so a target with one standards
    # file and one with twelve are read identically and one with none is read
    # as declaring none. Reading it here a second time would be a second
    # answer to what the standards say.
    context["repository_standards"] = context_assembler.standards_context(
        target_root, config
    )["repository_standards"] or None
    context["already_filed"] = _already_filed_block(answer)
    context["severity_floor"] = _severity_floor_block(min_severity)
    context["findings_path"] = str(artifact)
    context["workflow_candidates"] = workflow_selection.candidate_block(
        workflow_selection.candidates(harness_root)
    )
    prose = context_assembler.resolved_partial(
        harness_root, context_assembler.PROSE_LAYER, context
    )
    if prose is not None:
        context["prose_layer"] = prose
    template = context_assembler.load_template(harness_root, INSPECTOR_PROMPT)
    return context_assembler.render(template, context)


def _read_findings(artifact: Path, harness_root: Path | None):
    """The envelope one invocation wrote, or the reason there is none.

    Returns `(document, problem)`. The findings are a file the agent wrote and
    nothing is parsed out of what it printed: an invocation that wrote no
    file, wrote one that is not JSON, or wrote one that does not satisfy the
    envelope schema yields no findings for that scope and says which of those
    it was. A format enforced by nothing breaks silently, and a malformed
    finding read out of prose would be indistinguishable from none.
    """
    try:
        text = artifact.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "the invocation wrote no findings file"
    except OSError as error:
        return None, f"the findings file could not be read: {error}"
    try:
        document = json.loads(text)
    except ValueError as error:
        return None, f"the findings file is not JSON: {error}"
    problems = schema_validator.validate(
        document, schema_validator.load_schema(FINDINGS_SCHEMA, harness_root)
    )
    if problems:
        return None, ("the findings file does not satisfy the "
                      f"inspection-findings schema: {problems[0]}")
    return document, ""


def _describe(finding, fallback: str) -> str:
    """A finding named for a report, by whatever it carried to be named by."""
    if isinstance(finding, dict):
        for key in ("slug", "title"):
            value = finding.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _reported_cost(invoked) -> float | None:
    """What one invocation reported spending, or None if it reported nothing.

    Read off the result the runner returns and off nothing else: `run_agent`
    already parses `total_cost_usd` from the result event, so reading an agent
    log back here would be a second parser of the harness's own output. A
    runner that returns nothing — which every fake runner in the suite that
    predates this is free to do — and a result whose cost is absent both yield
    None, because the harness was told no figure. None is not zero: an
    inspection that reported no cost records none, where a zero would be
    indistinguishable from one that genuinely cost nothing.
    """
    reported = getattr(invoked, "cost_usd", None)
    if reported is None or isinstance(reported, bool):
        return None
    if not isinstance(reported, (int, float)):
        return None
    return float(reported)


def _area_suggestions(
        document) -> tuple[tuple[AreaSuggestion, ...], tuple[Drop, ...]]:
    """What one envelope said it could name no area for, and what of that it
    said unusably: the usable suggestions, and one Drop per refused entry.

    The envelope declares the field and holds it to nothing, so this filter is
    where a suggestion is judged and it is the only thing that judges one. That
    is deliberate: the field is read for the report and filed by nothing, so a
    suggestion may not become the thing that stops an inspection, and a shape
    the envelope enforced would cost a whole scope's findings over one slip in
    an entry that reaches no brief, no payload, no outbox entry and no tracker.
    So an envelope carrying the sibling not at all — every envelope written
    before the field existed, and every envelope an inspection of a target
    declaring no areas writes — yields nothing and drops nothing.

    A refused entry is a named drop rather than a silent skip, on the rule the
    findings loop already follows for one malformed finding: it costs the
    entries beside it nothing, and it is named — by its slug where it carries a
    readable one, otherwise by its position — with what was wrong with it, so
    it shows up in the same list where the inspection reports its other drops.
    A field that is not a list at all is one such drop rather than a per-entry
    one. Naming it matters beyond the report of the drop itself: a suggestion
    is written only where a vocabulary the Inspector was handed declared names
    none of which fit, so a refused one is evidence of a vocabulary exactly as
    a usable one is, and `Report.unnamed_areas` reads it as such. Nothing here
    reaches a brief, a payload, an outbox entry or a tracker.
    """
    declared = document.get(AREA_SUGGESTIONS)
    if declared is None:
        return (), ()
    if not isinstance(declared, list):
        return (), (Drop(
            UNUSABLE_SUGGESTION,
            f"the {AREA_SUGGESTIONS} field is not a list "
            f"(found {type(declared).__name__})",
        ),)
    found = []
    dropped = []
    for position, one in enumerate(declared, start=1):
        named = _describe(one, f"the suggestion at position {position}")
        problem = _suggestion_problem(one)
        if problem:
            dropped.append(Drop(UNUSABLE_SUGGESTION, f"{named}: {problem}"))
            continue
        found.append(AreaSuggestion(
            slug=one["slug"].strip(), concerns=one["concerns"].strip()))
    return tuple(found), tuple(dropped)


def _suggestion_problem(one) -> str:
    """What is wrong with one `area_suggestions` entry, or "" for a usable pair."""
    if not isinstance(one, dict):
        return f"not an object (found {type(one).__name__})"
    for key in ("slug", "concerns"):
        if key not in one:
            return f"'{key}' is missing"
        value = one[key]
        if not isinstance(value, str):
            return f"'{key}' is not a string (found {type(value).__name__})"
        if not value.strip():
            return f"'{key}' is blank"
    return ""


def _area(finding) -> str:
    """The area a finding named, and "" where it named none.

    Carried exactly as it was given: the vocabulary is the target's and the
    harness resolves, normalizes and validates no name in it. A finding that
    named none is a finding that named none — nothing here infers one, and
    nothing here reads the paths to guess.
    """
    if isinstance(finding, dict):
        value = finding.get("area")
        if isinstance(value, str):
            return value.strip()
    return ""


def _severity(finding) -> int | None:
    """A finding's severity where it has a readable one, for the drop report."""
    if isinstance(finding, dict):
        value = finding.get("severity")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _one_invocation(scope: Scope, target_root: Path, config: dict,
                    harness_root: Path, bound: Bounds, paths: tuple[str, ...],
                    answer, artifact: Path, log_path: Path, runner):
    """Invoke the Inspector once and read back what it wrote.

    Returns `(document, problem, cost)`: the envelope the invocation wrote and
    "" where it wrote one, or None and the reason there is none. Factored out
    of `inspect_scope` so that invoking again is a second call to one thing
    rather than a second copy of the sequence — the artifact removed before
    and after, the runner called, the cost read off its result, the envelope
    read back — because two copies of that sequence are two chances for the
    retry to differ from the attempt it is retrying.

    The artifact is removed before the invocation and again after it is read,
    so an artifact an earlier invocation left is never read as this one's and
    this one leaves none for the next. That is what makes a retry sound: the
    second invocation starts from nothing on disk, so a document it comes back
    with is a document it wrote.

    The allowance handed down is the ceiling as the bounds resolved it, not a
    remainder of it. The ceiling is declared per invocation, so a second
    invocation gets its own — the shape a self-route already has, where a
    stage that runs again in place is handed its stage ceiling rather than
    what the failed invocation left of it.
    """
    artifact.unlink(missing_ok=True)
    try:
        granted = list(config.get("allowed_tools") or ())
        if DELIVERY_TOOL not in granted:
            granted.append(DELIVERY_TOOL)
        invoked = runner(
            _render(harness_root, target_root, config, scope, paths, answer,
                    artifact, bound.min_severity),
            stage=f"inspector:{scope.label}",
            cwd=target_root,
            log_path=log_path,
            permission_mode=config.get("permission_mode", "acceptEdits"),
            model=config.get("model"),
            allowed_tools=granted,
            # Handed to the invocation rather than checked after it, so the
            # invocation stops itself. The ceiling is per invocation, which is
            # per scope; the report says how many invocations were made.
            max_budget_usd=bound.max_cost_usd,
        )
        # The figure the invocation itself reported, kept rather than dropped
        # on the floor. It is read off the result the runner already returns —
        # the same event the result text is read off — so nothing anywhere
        # parses an agent log back to recover it. A runner that returns nothing
        # and a result carrying no cost both leave it None.
        cost = _reported_cost(invoked)
        document, problem = _read_findings(artifact, harness_root)
    finally:
        artifact.unlink(missing_ok=True)
    return document, problem, cost


def inspect_scope(scope: Scope, target_root: Path, config: dict,
                  harness_root: Path, bound: Bounds, blocked: tuple[str, ...],
                  runner, index: LocalIndex = LocalIndex(),
                  announce=None) -> _ScopeResult:
    """One scope: ask what is filed, invoke once, and read what was written.

    The order is the point. What is already filed against this scope's paths is
    asked for *before* the invocation and injected into the prompt as data, and
    a finding whose key matches an item the query reported is dropped without
    the model being asked about it a second time. A query that could not answer
    is recorded as dedupe not having run for this scope and the findings are
    filed anyway.

    `index` is the local queue read once for the whole inspection and handed
    down, because the queue is not scoped. It defaults to an empty index, so a
    caller that does not supply one gets exactly what it got before the local
    tier existed.

    A scope carrying an explicit path list is taken at its word; every other
    scope has its paths computed from its prefix exactly as it always did.

    **An invocation that produced no findings artifact is made once more.**
    Every other agent the harness runs is already treated that way — a stage
    whose process dies is simply run again — and the inspection was the one
    place with no equivalent, so a reviewer's process dying before it wrote
    anything was read as "looked, found nothing" and the findings it would have
    reported were lost. The trigger is the **absence of the artifact and
    nothing else**: not how many findings came back, not what the invocation
    reported spending, not anything it printed. An invocation that wrote a
    well-formed envelope carrying an empty findings array is a real answer —
    the Inspector looked and found nothing — and is never retried. Exactly one
    retry, fixed here rather than configured: a workflow key could not govern
    both modes, because a broad-mode inspection is made with no workflow
    loaded.

    `announce` is the seam by which a caller says the second invocation is
    being made, called once and immediately before it. A caller that supplies
    none — every broad-mode path — announces nothing, which is what every
    caller did before the retry existed. What the callable does, and whether a
    failure in it may escape, is its own: the narrow modes hand over a guarded
    one, so announcing cannot become the thing that stops an inspection, and
    guarding it a second time here would be a second answer to that question.
    """
    result = _ScopeResult()
    paths = scope.paths or scope_paths(target_root, scope, blocked)
    result.scope_files = len(paths)
    answer = filed_query.query(paths, config, target_root, harness_root)
    result.dedupe = Dedupe(
        scope=scope.label,
        ran=answer.answered,
        known=len(answer.items),
        reason=answer.reason,
        excluded=answer.excluded,
    )
    known = {item.key for item in answer.items}
    # The two sources are a union: a finding either of them knows is dropped,
    # and neither source's answer depends on the other having answered. `known`
    # is kept separately so a drop can name which source knew it.
    checked = known | index.landed

    artifact, log_path = findings_paths(target_root, config)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    invoke = (scope, target_root, config, harness_root, bound, paths, answer,
              artifact, log_path, runner)

    document, first_problem, first_cost = _one_invocation(*invoke)
    costs = [first_cost]
    result.invocations = 1
    second_problem = ""

    if document is None:
        # No findings artifact, which is the one trigger: the invocation did
        # not deliver, whatever it reported and whatever it printed. Said
        # before the second one is made, so a reader watching the run meets the
        # retry announced the way a stage being re-run is rather than a second
        # silence of the same length.
        if announce is not None:
            announce()
        document, second_problem, second_cost = _one_invocation(*invoke)
        costs.append(second_cost)
        result.invocations = 2

    # Summed on the terms the single figure was carried on: a cost neither
    # invocation reported stays None rather than becoming zero, because zero is
    # what an invocation that genuinely cost nothing would report, and averaging
    # the two together corrupts every figure taken from the corpus later.
    result.cost_usd = reported_total(costs)

    if document is None:
        # Recorded only once both invocations have produced nothing, and named
        # for both: a reader meeting this drop is meeting an inspection that
        # was tried twice, and a detail naming one attempt would read as an
        # inspection that was tried once.
        result.dropped.append(Drop(
            NO_ARTIFACT,
            f"{scope.label}: two invocations were made and neither produced a "
            f"findings artifact; the first: {first_problem}; "
            f"the second: {second_problem}",
        ))
        return result

    # A refused suggestion is a named drop beside the findings' own, so it
    # reaches Report.dropped on both paths through the same list.
    result.area_suggestions, refused = _area_suggestions(document)
    result.dropped.extend(refused)

    brief_schema = schema_validator.load_schema(BRIEF_SCHEMA, harness_root)
    defined = harness_config.workflow_names(harness_root)
    for position, finding in enumerate(document["findings"], start=1):
        named = _describe(finding, f"the finding at position {position}")
        problems = schema_validator.validate(finding, brief_schema)
        if problems:
            # One malformed finding costs only itself: it is named with the
            # field that failed, and the findings beside it in the same file
            # are filed exactly as they would have been.
            result.dropped.append(
                Drop(MALFORMED, f"{named}: {problems[0]}", _severity(finding))
            )
            continue
        if finding["workflow"] not in defined:
            # The acceptable names are the definitions the harness holds, so a
            # third workflow becomes selectable by shipping a definition and
            # with no edit here or to the brief schema.
            result.dropped.append(Drop(
                UNKNOWN_WORKFLOW,
                f"{named}: '{finding['workflow']}' is not a workflow the "
                f"harness defines; it defines: "
                f"{', '.join(defined) if defined else 'no workflow definitions'}",
                _severity(finding),
            ))
            continue
        key = outbox.identity_key(identity(finding))
        if key in checked:
            # The query first, then the local landed set. Both are in `checked`
            # and either alone is enough to drop; what this decides is only
            # which source the reader is told knew it.
            if key in known:
                result.dropped.append(Drop(
                    ALREADY_FILED,
                    f"{named}: the filed query already reported it",
                    _severity(finding),
                ))
            else:
                result.dropped.append(Drop(
                    ALREADY_FILED_LOCALLY,
                    f"{named}: the local queue holds it landed",
                    _severity(finding),
                ))
            continue
        if key in index.queued:
            # Written down here and seen by no tracker, so it is not the same
            # evidence a landed entry is: it is reported as already queued, and
            # the report does not count it as newly filed.
            result.dropped.append(Drop(
                ALREADY_QUEUED,
                f"{named}: the local queue holds it pending, not yet filed",
                _severity(finding),
            ))
            continue
        # A key the local queue holds *failed* reaches here deliberately and is
        # filed: see `local_index` for why a terminal entry must not suppress.
        result.found.append(_Found(finding=finding, scope=scope))
    return result


# --------------------------------------------------------------------------
# The whole inspection
# --------------------------------------------------------------------------


def capped(found: list, max_findings: int):
    """The findings kept and the ones the cap excluded.

    Applied across the whole inspection rather than per scope, because the
    bound is on what an inspection files. Sorted by severity so the ones kept
    are the highest-severity rather than the first written — a cap on writing
    order would be a cap on nothing worth bounding — and the sort is stable, so
    findings of one severity keep the order their scopes produced them in.
    """
    ordered = sorted(found, key=lambda one: -one.finding["severity"])
    return ordered[:max_findings], ordered[max_findings:]


def file_findings(target_root: Path, found: list, max_findings: int, *,
                  min_severity: int = 1, dry_run: bool = False,
                  failed: frozenset = frozenset()):
    """Apply the cap and file what survives it, reporting both.

    Returns `(filed, dropped)`. This is the whole of what a producer of
    findings does with them, and it is here rather than at a call site because
    there is more than one producer: an inspection of a part of the tree and an
    inspection of what one story changed file under the same cap, through the
    same enqueue, and drop what they drop for the same named reasons. A second
    copy of this would be a second answer to what filing a finding means.

    `min_severity` is the floor: a finding beneath it is dropped under a reason
    of its own, carrying its severity, and never reaches the cap. It is applied
    **before** the cap so that a finding the floor excluded is never reported as
    having been crowded out — the cap may not even have been reached — and so
    that the cap spends its places on findings that were going to be filed. It
    defaults to 1, which is no floor at all, so every construction that predates
    it files exactly what it filed before; the callers that have a resolved
    bound pass it, and both producers reach filing through here, so neither can
    diverge on it.

    `dry_run` reports exactly what an ordinary call would file and enqueues
    nothing: the floor and the cap are applied identically and only the call
    into the queue is not made.

    `failed` is the keys the local queue held in the terminal failed state when
    the inspection began, and it only marks what it files — nothing here drops
    a finding on it, and a caller that supplies none files exactly what it
    filed before the field existed. It is read from before any filing rather
    than as the queue now stands, so a key this call has just overwritten with
    a pending entry is still reported as a refile.
    """
    above = [one for one in found if one.finding["severity"] >= min_severity]
    beneath = [one for one in found if one.finding["severity"] < min_severity]
    dropped = [
        Drop(
            BENEATH_THE_FLOOR,
            f"{one.finding['slug']}: severity {one.finding['severity']}, "
            f"beneath the floor of {min_severity}",
            one.finding["severity"],
        )
        for one in beneath
    ]

    kept, excluded = capped(above, max_findings)
    dropped.extend(
        Drop(
            PAST_THE_CAP,
            f"{one.finding['slug']}: severity {one.finding['severity']}",
            one.finding["severity"],
        )
        for one in excluded
    )
    filed: list = []
    queue = outbox.queue_dir(target_root)
    for one in kept:
        key = ""
        if not dry_run:
            key = outbox.enqueue(queue, payload(one.finding, one.scope),
                                 identity(one.finding))
            if not key:
                # story-090's contract: the empty string is the item having
                # been lost rather than a key. Nothing is named after it and
                # nothing is reported as filed on it.
                dropped.append(Drop(
                    LOST_BY_THE_QUEUE,
                    f"{one.finding['slug']}: the queue dropped it",
                    one.finding["severity"],
                ))
                continue
        filed.append(Filed(
            key=key,
            slug=one.finding["slug"],
            title=one.finding["title"],
            severity=one.finding["severity"],
            scope=one.scope.label,
            # Carried as the brief gave it, and empty where the brief named
            # none. Both producers reach filing through here, so what a filed
            # brief's record says about its area is one fact rather than one
            # per producer.
            area=_area(one.finding),
            refiled_over_failure=_held_failed(one.finding, failed),
        ))
    return tuple(filed), dropped


def _held_failed(finding, failed: frozenset) -> bool:
    """Whether the local queue held this finding's key in the failed state.

    Derived rather than read off the key `enqueue` returned, because a dry run
    returns no key and reports exactly what an ordinary call would file.
    Guarded because this is a report and may not become the thing that stops a
    filing: an identity `identity_key` cannot render is refused by `enqueue`,
    which is where that decision belongs.
    """
    if not failed:
        return False
    try:
        return finding_key(finding) in failed
    except (TypeError, ValueError):
        return False


def reported_total(costs) -> float | None:
    """The sum of what a set of invocations reported, or None if none did.

    None rather than zero where nothing was reported, which is the distinction
    this whole record turns on: a zero is what an inspection that genuinely
    cost nothing would carry, and averaging one over the other corrupts every
    figure taken from the corpus later.
    """
    reported = [one for one in costs if one is not None]
    return sum(reported) if reported else None


#: What a record that reached no log is reported as, by both modes: the
#: broad mode's `record` says it where the append wrote nothing, and the
#: narrow mode's commit says it before staging, so the two cannot word the
#: one outcome two ways.
NO_LOG_DETAIL = ("the record could not be written: the history declaration "
                 "routes an inspection to no log")

#: What a commit that had no record path to stage is reported as. It says
#: nothing about tracking, because nothing about tracking was asked: it is
#: the answer to a caller that named no path at all.
NO_PATH_DETAIL = "nothing staged: no record path was named"

#: What a commit whose every record path git ignores is reported as. A
#: target whose ignore rules keep the history directory out of version
#: control gets this rather than a git add failure, because it is what that
#: target asked for and not a failure of anything.
NOT_TRACKED_DETAIL = "nothing staged: no record path is tracked"


def routed_logs() -> tuple[str, ...]:
    """The declared logs the inspection kind can reach, by filename.

    Asked of the same projection the append takes, the shape
    `plan_mandate._logs_holding` established: a declaration that stops routing
    this kind stops naming the file here, with no edit and no log filename
    written at any call site.

    The projection is asked with an entry carrying every field each log's
    declaration requires, so the answer is which logs this kind *can* reach
    rather than which it reached on one occurrence: since story-173 a log may
    require a field only some occurrences of a kind carry — the duration log
    requires the seconds an inspection that made its invocation took — and a
    probe carrying only what every occurrence carries would leave the file the
    real record went to unstaged.

    Exposed on its own so the narrow mode's commit can ask whether the
    declaration routes the kind anywhere before it looks for a record on disk,
    through this derivation rather than a copy of it.
    """
    import story_coordinator

    return tuple(
        log for log, declaration in
        story_coordinator.history_log_declarations().items()
        if story_coordinator.history_record(
            {"event": INSPECTION_EVENT, "timestamp": "",
             **{field: "" for field in declaration.get("required", [])}},
            [], "", declaration,
        ) is not None
    )


def record_paths(target_root: Path, config: dict, *, story_id: str = "",
                 segment: Path | None = None) -> tuple[str, ...]:
    """The repository-relative record paths an inspection's commit stages.

    The logs are `routed_logs` above. What is staged is the segment file the
    record was written to, resolved through the writer-side resolver the
    append resolved it through. A record a run wrote went to that story's own
    segment, so the run's producer names its `story_id`. A broad-mode record
    went to a segment named for the moment it was written, which only the
    append that wrote it knows, so `record` passes that `segment` in. A caller
    naming neither is refused rather than answered with a guess, because a
    path staged by a guess is a path nothing wrote.

    What is staged is narrowed to the files the segment holds, because an
    occurrence that carried less wrote to fewer of them and `git add` of a
    path that is not there refuses the whole set. So an empty answer where
    `routed_logs` names a log means no segment file holds the record — which
    the narrow mode's commit reads as a record that was not written.
    """
    if segment is None:
        if not story_id:
            raise ValueError(
                "no history segment to stage: neither a story nor the segment "
                "a record was written to was named"
            )
        segment = harness_config.history_segment(
            harness_config.history_dir(target_root, config), story_id
        )
    return tuple(sorted(
        str((segment / log).relative_to(target_root)) for log in routed_logs()
        if (segment / log).is_file()
    ))


def ignored_paths(target_root: Path, paths) -> frozenset:
    """Which of the named repository-relative paths git ignores.

    `git check-ignore` reports an untracked path an ignore rule matches and
    says nothing about a tracked one, so a path in the answer is one `git add`
    would refuse. The paths go in on stdin NUL-separated and come back the
    same way, so a path carrying an unusual byte arrives as itself. Asked
    with a fixed argument list, and a git that cannot be spawned or that
    errors answers nothing here rather than raising: the question is a
    courtesy before the add, and the add's own return code is still checked.
    """
    paths = tuple(paths)
    if not paths:
        return frozenset()
    try:
        completed = subprocess.run(  # noqa: S603 - a fixed argument list
            ["git", "-C", str(target_root), "check-ignore", "--stdin", "-z"],
            input="".join(f"{one}\0" for one in paths),
            capture_output=True, text=True,
        )
    except OSError:
        return frozenset()
    if completed.returncode not in (0, 1):
        return frozenset()
    return frozenset(one for one in completed.stdout.split("\0") if one)


def _git_said(completed) -> str:
    """What a git call said on stderr, or its exit status where it said nothing.
    """
    said = (completed.stderr or "").strip()
    return said if said else f"exit status {completed.returncode}"


def commit_record_paths(target_root: Path, paths, subject: str) -> Recorded:
    """Stage the named record paths and commit them, checking what git returned.

    The one commit both modes make, written once so the two cannot disagree
    about what a record commit does. The paths are staged **by name** and
    never with `git add -A`, so a file an inspection agent changed elsewhere
    is left in the working tree rather than folded into a commit this made.
    Both git return codes are checked. A commit that fails — a pre-commit
    hook that rejects it, a git with no identity to commit as — unstages
    exactly the paths that were staged, with `git restore --staged`, so the
    working tree is left as the commit found it: modified, with nothing
    staged. That is restoring what the commit found, not fixing the commit:
    nothing here retries, amends, resets past the staged paths or pushes.

    Returns a `Recorded` with `written` already true, since a caller reaches
    this only once it has established the record is on disk. Nothing staged
    is reported as not committed too, since a record that reached no commit
    is not durable whatever the reason. Two such outcomes are not failures
    and their details say so rather than calling them one: a caller that
    named no path at all, and a target whose ignore rules keep every named
    path out of version control — asked of git with `check-ignore` before
    the add, so that target gets "no record path is tracked" rather than the
    words `git add` would have refused it with. A caller that names no path
    is told that and nothing about tracking, because nothing about tracking
    was asked.

    This raises on nothing but a subprocess that cannot be spawned at all;
    each caller guards for that, because each producer may not raise.
    """
    paths = tuple(paths)
    if not paths:
        return Recorded(written=True, committed=False, detail=NO_PATH_DETAIL)
    if ignored_paths(target_root, paths) >= frozenset(paths):
        # Every record path is one git ignores: the target has kept its
        # history out of version control, which is a decision and not a
        # failure, and staging would only have git say so less clearly.
        return Recorded(written=True, committed=False,
                        detail=NOT_TRACKED_DETAIL)
    argv = ["git", "-C", str(target_root)]
    added = subprocess.run(  # noqa: S603 - a fixed argument list
        [*argv, "add", "--", *paths], capture_output=True, text=True
    )
    if added.returncode != 0:
        return Recorded(
            written=True, committed=False,
            detail=f"git add failed: {_git_said(added)}",
        )
    staged = subprocess.run(  # noqa: S603 - a fixed argument list
        [*argv, "diff", "--cached", "--quiet", "--", *paths],
        capture_output=True, text=True,
    )
    if staged.returncode == 0:
        # Nothing of ours is staged, so there is nothing to commit: an empty
        # commit here would be a commit about a record that is not there.
        return Recorded(
            written=True, committed=False,
            detail="nothing staged: the record paths are unchanged",
        )
    committed = subprocess.run(  # noqa: S603 - a fixed argument list
        [*argv, "commit", "-m", subject, "--", *paths],
        capture_output=True, text=True,
    )
    if committed.returncode == 0:
        return Recorded(written=True, committed=True)
    # Put the index back as the commit found it: the same paths, and only
    # those, unstaged. The working tree keeps the record; the next run's
    # pre-flight refuses on it and the detail says which inspection left it.
    subprocess.run(  # noqa: S603 - a fixed argument list
        [*argv, "restore", "--staged", "--", *paths],
        capture_output=True, text=True,
    )
    return Recorded(
        written=True, committed=False,
        detail=f"git commit failed: {_git_said(committed)}",
    )


def record(target_root: Path, config: dict, report: Report) -> Recorded:
    """Write one line for this inspection and commit it, and say what happened.

    The record goes through the coordinator's own per-log append with the
    history directory resolved exactly as a run resolves it and passed
    explicitly, because a broad-mode inspection has no run directory to resolve
    one from — the shape `plan_mandate.record` established. What reaches which
    log is read off the cross-run history declaration: this builds an entry
    carrying the kind that declaration names and hands it over, so no condition
    here decides where it goes. It names no work item, because a broad-mode
    inspection is not made by a run and has none.

    It is committed because the cross-run history is versioned, so a record left
    in the working tree is a dirty tree the next run's pre-flight refuses on.
    The commit is `commit_record_paths`: staged by name, both return codes
    checked, and unstaged again where the commit fails so the tree is left as
    the commit found it.

    Both halves are guarded, and separately: a history directory that cannot be
    written and a git call that fails each cost the record and nothing else.
    The inspection has already happened and has already filed what it found, and
    a failure to write down what that cost may not undo any of it. What each
    path returns is a `Recorded` saying what was done or why it was not — a
    raise on either half becomes a not-committed result carrying the exception
    — so the report a developer is reading can say it. Nothing here raises.

    The imports are inside the body, the idiom the queue module already uses for
    its own coordinator import: the coordinator reaches this module through
    `story_inspection`, and a module-scope import would close the cycle.
    """
    import story_coordinator

    try:
        entry = {
            "event": INSPECTION_EVENT,
            "timestamp": time.strftime(
                story_coordinator.HISTORY_TIMESTAMP_FORMAT
            ),
            "mode": MODE_BROAD,
            "findings": len(report.filed) + len(report.dropped),
            "filed": len(report.filed),
            "dropped": len(report.dropped),
            "scope_files": report.scope_files,
            "invocations": report.invocations,
            # Written on every line rather than only where dedupe failed, so a
            # false is distinguishable from a line written before the field
            # existed. It is the whole of what makes "which inspections ran
            # without dedupe" a question one read of a tracked file answers,
            # once the report this printed has scrolled away.
            "dedupe_ran": report.dedupe_ran,
            # Written on every record for the reason `dedupe_ran` beside it
            # is: a boolean has a false to say, and absence would mean either
            # that or a line written before the field existed. False here is
            # an inspection that was not made — every invocation for at least
            # one scope came back with no findings artifact, twice — which is
            # the thing this log could not previously be asked about, because
            # such an inspection recorded zero findings and read as one that
            # looked and found nothing.
            "inspection_made": was_made(report.dropped),
        }
        if report.cost_usd is not None:
            # Absent where nothing was reported, rather than zero: the same
            # distinction the schema states, made here by not writing the key.
            entry["cost_usd"] = report.cost_usd
        written = story_coordinator.append_history_records(
            harness_config.history_dir(target_root, config), entry, "", []
        )
    except Exception as error:  # noqa: BLE001 - a record that cannot be
        # written costs the record and nothing else
        return Recorded(detail=f"the record could not be written: {error}")

    if not written:
        return Recorded(detail=NO_LOG_DETAIL)
    try:
        # The segment is the one the append resolved for a record with no
        # story: named for the moment it was written, so it is read off what
        # was written rather than derived a second time.
        paths = record_paths(target_root, config, segment=written[0].parent)
        return commit_record_paths(target_root, paths, COMMIT_SUBJECT)
    except Exception as error:  # noqa: BLE001 - the commit is the record's
        # durability, never the inspection's
        return Recorded(written=True, committed=False,
                        detail=f"the record could not be committed: {error}")


def inspect(target_root: Path, config: dict, harness_root: Path, *,
            arguments=(), dry_run: bool = False,
            runner=agent_runner.run_agent) -> Report:
    """Inspect every scope, file what survives, and report what happened.

    The local queue is read once, then one invocation per scope, then one cap
    across all of them, then one `outbox.enqueue` per surviving brief. Returns
    what happened — including what the local index held, on every inspection
    and not only one where it held something — and prints nothing.

    `dry_run` reports exactly what an ordinary invocation would file and
    enqueues nothing: the scopes are inspected, the query is asked, the
    findings are validated and the cap is applied identically, and only the
    call into the queue is not made.

    The bounds are assumed already resolved by a caller that could refuse on
    them; `bounds` is that caller's half, and this one obeys whatever it is
    given.
    """
    bound, problem = bounds(config)
    if bound is None:
        # A caller that refuses on a bad bound never reaches here. One that did
        # not is given an inspection that made no invocation and filed nothing,
        # rather than one that quietly obeyed a default nobody wrote.
        return Report(dropped=(Drop(MALFORMED, problem),))

    covered = scopes(arguments, config)
    blocked = blocked_prefixes(harness_root)
    # Read once for the whole inspection rather than once per scope: the queue
    # is not scoped, and every scope asks it the same question.
    index = local_index(target_root, harness_root)

    found: list = []
    dropped: list = []
    dedupe: list = []
    costs: list = []
    suggestions: list = []
    scope_files = 0
    invocations = 0
    for scope in covered:
        result = inspect_scope(
            scope, target_root, config, harness_root, bound, blocked, runner,
            index,
        )
        # What the scope actually made, rather than one per scope: a scope
        # whose first invocation produced no findings artifact was invoked
        # again, and a count of scopes would report the two invocations' summed
        # cost as one invocation's.
        invocations += result.invocations
        found.extend(result.found)
        dropped.extend(result.dropped)
        dedupe.append(result.dedupe)
        costs.append(result.cost_usd)
        suggestions.extend(result.area_suggestions)
        scope_files += result.scope_files

    filed, over = file_findings(
        target_root, found, bound.max_findings,
        # The floor the bounds resolved, applied here rather than at each
        # producer, so this mode and the post-story one file on one set of
        # terms.
        min_severity=bound.min_severity, dry_run=dry_run,
        # The index read above every invocation, so a brief whose key this same
        # inspection is about to overwrite is still reported as a refile.
        failed=index.failed,
    )
    dropped.extend(over)

    report = Report(
        scopes=covered,
        invocations=invocations,
        filed=tuple(filed),
        dropped=tuple(dropped),
        dedupe=tuple(dedupe),
        local_index=index,
        dry_run=dry_run,
        cost_usd=reported_total(costs),
        scope_files=scope_files,
        min_severity=bound.min_severity,
        area_suggestions=tuple(suggestions),
    )
    # Written last, and not by a dry run. Two reasons, and the second is the
    # one that decides it. A dry run's filed count is zero because filing was
    # switched off rather than because nothing survived, so a line carrying it
    # describes an inspection that never existed and is indistinguishable in
    # the log from one that found nothing worth filing. And the record is
    # committed, which is a change to the repository a developer asking for a
    # dry run has not asked for. The cost of leaving it out is one real spend
    # missing from the corpus, which is the cheaper of the two.
    if not dry_run:
        # Attached rather than printed, so the report can say on every real
        # inspection whether the record landed; a dry run's report keeps the
        # default, which says no record was attempted.
        report = replace(report, record=record(target_root, config, report))
    return report
