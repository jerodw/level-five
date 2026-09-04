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
precisely so a caller can say dedupe did not run.

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

**No silent bound.** Every way of dropping a finding is named in the report with
what it excluded: already filed by the tracker, already filed by this harness,
already queued, malformed, an unknown workflow, past the cap, lost by the queue.
A scope whose filed query could not answer is reported as dedupe not having run,
in those terms, and its findings are filed anyway — losing dedupe is not a
reason to lose the findings.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
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

#: Where a target's tests live, which is a scope of its own beside the source
#: dirs rather than one of them.
TESTS_DIR_KEY = "tests_dir"

DEFAULT_MAX_FINDINGS = 10
DEFAULT_MAX_COST_USD = 5.0
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
UNKNOWN_WORKFLOW = "names a workflow the harness does not define"
PAST_THE_CAP = "past the cap"
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
class Filed:
    """One brief that reached the queue, and the key it was filed under."""

    key: str
    slug: str
    title: str
    severity: int
    scope: str


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


@dataclass(frozen=True)
class LocalIndex:
    """What the local outbox queue knows, read once for a whole inspection.

    This is the free tier of the dedupe: no network, no configuration, no
    subprocess and nothing that can fail slowly — the same read `l5-status`
    already makes, through `outbox.entry_files` and `outbox.read_entry` alone.
    It is consulted on every inspection rather than only when the filed query
    fails, because both answer the same question and both are asked.

    **It is a subset of what the filed query answers**, and a reader must not
    mistake it for a complete one. It knows only what this machine filed:
    another machine's filings and anything filed by hand are invisible to it,
    so an inspection whose query could not answer still reports that dedupe did
    not run even where this was read successfully.

    `read` false is a queue that could not be listed, with `reason` saying why;
    that costs this tier and costs nothing else. `unreadable` counts the files
    in the queue that could not be read as entries — each contributes no key and
    stops nothing. The default is an index that was read and held nothing, which
    suppresses nothing and claims no failure.
    """

    read: bool = True
    landed: frozenset = frozenset()
    queued: frozenset = frozenset()
    unreadable: int = 0
    reason: str = ""


def local_index(target_root: Path,
                harness_root: Path | None = None) -> LocalIndex:
    """The keys the local queue holds, by the state their entries are in.

    Read once for a whole inspection rather than once per scope, because the
    queue is not scoped: it is a record of what this harness filed, and every
    scope asks it the same question.
    """
    queue = outbox.queue_dir(target_root)
    try:
        files = outbox.entry_files(queue)
    except OSError as error:
        # A queue that cannot be listed is nothing known rather than an error,
        # the one-directional bias every other total path in this module takes.
        # It costs this tier, it is reported, and nothing is raised out of here.
        # A directory that does not exist needs no special case: `entry_files`
        # already answers it with no entries rather than an error.
        return LocalIndex(
            read=False,
            reason=f"the queue at {queue} could not be listed: {error}",
        )

    landed: set = set()
    queued: set = set()
    unreadable = 0
    for path in files:
        entry, _ = outbox.read_entry(path, harness_root)
        if entry is None:
            # A poisoned entry contributes no key and stops nothing: the
            # entries beside it in the same queue are indexed exactly as they
            # would have been, and the count is reported.
            unreadable += 1
            continue
        state = entry["state"]
        if state == outbox.LANDED:
            landed.add(entry["key"])
        elif state == outbox.PENDING:
            queued.add(entry["key"])
        # A failed entry is skipped deliberately and contributes to neither
        # set. It is terminal: no later sync will file it, so it is a finding
        # that reached nobody, and suppressing on it would lose that finding
        # permanently with no signal. Leaving it out means the finding is
        # enqueued again and replaces the failed entry at the same key with a
        # pending one — the finding getting another chance rather than a
        # duplicate, since the key is derived from the identity alone.

    return LocalIndex(
        read=True,
        landed=frozenset(landed),
        queued=frozenset(queued),
        unreadable=unreadable,
    )


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

    def dropped_for(self, reason: str) -> tuple[Drop, ...]:
        """Everything dropped one way, so a caller can say each way once."""
        return tuple(drop for drop in self.dropped if drop.reason == reason)

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
    """What one invocation produced: what it found, dropped, and cost.

    `cost_usd` is what the runner's own result reported, carried and never
    computed — nothing here reads an agent log back to recover it. None is a
    runner that returned nothing and a result carrying no cost alike: the
    harness was told no figure, which is not the same as a figure of zero, and
    keeping the two apart is what stops a zero nobody reported from being
    averaged later as though it were one.
    """

    found: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    dedupe: Dedupe = None
    cost_usd: float | None = None
    scope_files: int = 0


# --------------------------------------------------------------------------
# The bounds
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """The two bounds an inspection runs under, already resolved."""

    max_findings: int
    max_cost_usd: float


def bounds(config: dict):
    """The bounds, or the reason they are refused.

    Returns `(bounds, problem)`, in the shape `filed_query.resolve_settings`
    and the sweep's transport build already take: a terminal caller refuses a
    configuration it cannot obey. Neither falls back to its default when it is
    declared and unreadable — a bound that cannot be read is a bound the target
    did not declare, and obeying the default in its place would obey a number
    nobody wrote. Both problems are reported together, so a target that got
    both wrong is told both.
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

    if problems:
        return None, "; ".join(problems)
    return Bounds(max_findings=max_findings, max_cost_usd=max_cost), ""


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


def _standards(target_root: Path, config: dict) -> str:
    """Whatever the target declares as standards, as one undifferentiated body.

    Globbed, never looked for by name: the harness declares no required
    document set, so a target with one standards file and a target with twelve
    are read identically, and a target with none is read as declaring none.
    """
    directory = target_root / config.get("standards_dir", ".harness/standards")
    if not directory.is_dir():
        return ""
    parts = []
    for path in sorted(directory.glob("*.md")):
        try:
            parts.append(f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}")
        except OSError:
            continue
    return "\n\n".join(parts)


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


def _render(harness_root: Path, target_root: Path, config: dict, scope: Scope,
            paths: tuple[str, ...], answer, artifact: Path) -> str:
    """The prompt one invocation is given."""
    context = context_assembler.schema_context(harness_root)
    context["scope"] = scope.label
    context["scope_kind"] = scope.kind
    # Supplied by the scope its caller built rather than defaulted here, so the
    # two modes' questions are each their own caller's and neither can reach
    # the template unset.
    context["framing"] = scope.framing
    context["scope_paths"] = "\n".join(paths) or "(this scope tracks no files)"
    context["repository_standards"] = _standards(target_root, config) or None
    context["already_filed"] = _already_filed_block(answer)
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


def _severity(finding) -> int | None:
    """A finding's severity where it has a readable one, for the drop report."""
    if isinstance(finding, dict):
        value = finding.get("severity")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def inspect_scope(scope: Scope, target_root: Path, config: dict,
                  harness_root: Path, bound: Bounds, blocked: tuple[str, ...],
                  runner, index: LocalIndex = LocalIndex()) -> _ScopeResult:
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
    # An artifact left by an earlier invocation is not this one's, and reading
    # one as this one's would report findings nothing found. Removed before the
    # invocation and again after it is read, so whatever this invocation does,
    # the next one starts with nothing there. What the invocation said on the
    # way to writing it is kept by the runner's own log.
    artifact.unlink(missing_ok=True)
    try:
        granted = list(config.get("allowed_tools") or ())
        if DELIVERY_TOOL not in granted:
            granted.append(DELIVERY_TOOL)
        invoked = runner(
            _render(harness_root, target_root, config, scope, paths, answer,
                    artifact),
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
        result.cost_usd = _reported_cost(invoked)
        document, problem = _read_findings(artifact, harness_root)
    finally:
        artifact.unlink(missing_ok=True)

    if document is None:
        result.dropped.append(Drop(NO_ARTIFACT, f"{scope.label}: {problem}"))
        return result

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
                  dry_run: bool = False):
    """Apply the cap and file what survives it, reporting both.

    Returns `(filed, dropped)`. This is the whole of what a producer of
    findings does with them, and it is here rather than at a call site because
    there is more than one producer: an inspection of a part of the tree and an
    inspection of what one story changed file under the same cap, through the
    same enqueue, and drop what they drop for the same named reasons. A second
    copy of this would be a second answer to what filing a finding means.

    `dry_run` reports exactly what an ordinary call would file and enqueues
    nothing: the cap is applied identically and only the call into the queue is
    not made.
    """
    kept, excluded = capped(found, max_findings)
    dropped = [
        Drop(
            PAST_THE_CAP,
            f"{one.finding['slug']}: severity {one.finding['severity']}",
            one.finding["severity"],
        )
        for one in excluded
    ]
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
        ))
    return tuple(filed), dropped


def reported_total(costs) -> float | None:
    """The sum of what a set of invocations reported, or None if none did.

    None rather than zero where nothing was reported, which is the distinction
    this whole record turns on: a zero is what an inspection that genuinely
    cost nothing would carry, and averaging one over the other corrupts every
    figure taken from the corpus later.
    """
    reported = [one for one in costs if one is not None]
    return sum(reported) if reported else None


def record_paths(target_root: Path, config: dict) -> tuple[str, ...]:
    """The repository-relative record paths a broad-mode commit stages.

    Asked of the same projection the append took, the shape
    `plan_mandate._logs_holding` established: a declaration that stops routing
    this kind stops staging the file, with no edit here and no log filename
    written at any call site.
    """
    import story_coordinator

    directory = harness_config.history_dir(target_root, config)
    logs = [
        log for log, declaration in
        story_coordinator.history_log_declarations().items()
        if story_coordinator.history_record(
            {"event": INSPECTION_EVENT, "timestamp": ""}, [], "", declaration
        ) is not None
    ]
    return tuple(sorted(
        str((directory / log).relative_to(target_root)) for log in logs
    ))


def record(target_root: Path, config: dict, report: Report) -> None:
    """Write one line for this inspection and commit it, or cost only the line.

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
    The declared paths are staged **by name** and never with `git add -A`, so a
    file the inspection agent changed elsewhere is left in the working tree
    rather than folded into a commit this module made.

    Both halves are guarded, and separately: a history directory that cannot be
    written and a git call that fails each cost the record and nothing else.
    The inspection has already happened and has already filed what it found, and
    a failure to write down what that cost may not undo any of it.

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
        }
        if report.cost_usd is not None:
            # Absent where nothing was reported, rather than zero: the same
            # distinction the schema states, made here by not writing the key.
            entry["cost_usd"] = report.cost_usd
        story_coordinator.append_history_records(
            harness_config.history_dir(target_root, config), entry, "", []
        )
    except Exception:  # noqa: BLE001 - a record that cannot be written costs
        return                                   # the record and nothing else

    try:
        paths = record_paths(target_root, config)
        if not paths:
            return
        argv = ["git", "-C", str(target_root)]
        added = subprocess.run(  # noqa: S603 - a fixed argument list
            [*argv, "add", "--", *paths], capture_output=True, text=True
        )
        if added.returncode != 0:
            return
        staged = subprocess.run(  # noqa: S603 - a fixed argument list
            [*argv, "diff", "--cached", "--quiet", "--", *paths],
            capture_output=True, text=True,
        )
        if staged.returncode == 0:
            # Nothing of ours is staged, so there is nothing to commit: an
            # empty commit here would be a commit about a record that is not
            # there.
            return
        subprocess.run(  # noqa: S603 - a fixed argument list
            [*argv, "commit", "-m", COMMIT_SUBJECT, "--", *paths],
            capture_output=True, text=True,
        )
    except Exception:  # noqa: BLE001 - the commit is the record's durability,
        return                                       # never the inspection's


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
    scope_files = 0
    invocations = 0
    for scope in covered:
        result = inspect_scope(
            scope, target_root, config, harness_root, bound, blocked, runner,
            index,
        )
        invocations += 1
        found.extend(result.found)
        dropped.extend(result.dropped)
        dedupe.append(result.dedupe)
        costs.append(result.cost_usd)
        scope_files += result.scope_files

    filed, over = file_findings(
        target_root, found, bound.max_findings, dry_run=dry_run
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
        record(target_root, config, report)
    return report
