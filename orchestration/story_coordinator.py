"""The Story Coordinator: deterministic execution of the story workflow.

The workflow definition says what should happen. The coordinator makes it
happen: it assembles context, invokes stage agents, saves state, and
routes execution from structured artifacts. It never reasons; every
decision here is a rule applied to a recorded fact.

The revert check defined below decides at one granularity, and it is worth
knowing before reading its verdict as more than it is: it reverts the whole
set of governed paths in a single run of the suite and decides on that one
result. So a set containing one forced repair is permitted *in full*, added
coverage in the other files of that set included, and a single file mixing a
forced repair with added coverage is not caught at all. The record names the
paths that were reverted, so a reader can see exactly what the decision
covered. Reading the diff remains the verifier's job; this check bounds a
class of edit, it does not audit one.

Its refusal does not stop the run, and that is not leniency: reaching a
refusal means the check built a clone with exactly those paths restored to
what the stage found and watched the suite pass in it, so undoing them in the
working tree reproduces a tree the harness has already proved good. The
coordinator restores them, keeps the stage's own version of each in the run
directory, records what it did, and the run continues. What still stops a run
is a check that reached no verdict — nothing was proved, so nothing may be
undone — and a file *created* under a governed path, against whose absence no
suite has ever been run.

A permission has a third disposition beside the two above, and it belongs to
the restriction rather than to the check. The check asks whether an edit was
needed; whether the stage was entitled to make it is a different question,
answered per sense by the table beside `_SENSES`. A stage confined by
`may_only_change` whose outside edit the suite needs has done another stage's
work: the edits are undone by the same restore the refusal uses, kept under
the same discarded directory, and recorded as permitted with a `routed` key
naming the confinement, under an event kind of their own. The tree then lacks
work it needs, which is what the next suite run turns red on and delivers to
the verifier to route. A create-restricted stage's necessary modification is
still permitted and left standing exactly as before.
"""
from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import json
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import agent_runner
import brief_key
import context_assembler
import harness_config
import item_update
import outbox_sweep
import story_inspection
import schema_validator
import story_parser
import worktrees


@dataclass
class RunState:
    story_id: str
    branch: str
    status: str = "running"
    current_stage: str = ""
    retry_count: int = 0
    verification_iterations: int = 0
    artifacts: list[str] = field(default_factory=list)
    # Everything below is defaulted so RunState(**json) still loads a state
    # file written before these fields existed, and empty means "not
    # established" everywhere it is read.
    #: The story artifact as this run first read it, so a refusal can say
    #: whether it has been amended since. It informs a message; it authorizes
    #: nothing.
    story_digest: str = ""
    #: The commit _escalate made on the story branch, empty when there was
    #: nothing to commit. Tells an escalation the harness committed from one a
    #: developer committed from one where nothing was committed.
    escalation_commit: str = ""
    #: The harness revision at the moment the run escalated.
    harness_revision: str = ""
    #: How many times the stage named by current_stage has re-run itself in
    #: place after a mechanical failure. The *live* count only, scoped
    #: implicitly by current_stage: it is reset every time a stage is entered
    #: other than by a self-route, so nothing here accumulates across stages
    #: and nothing reads it to learn what happened earlier. How many times a
    #: stage self-routed over the run is a query against the history, where
    #: one `self-routed` entry per self-route names its stage.
    self_route_count: int = 0
    #: How many of those re-entries were bookkeeping — a required artifact
    #: missing, or one the freshness check read as a previous attempt's. Live
    #: state scoped by current_stage exactly as self_route_count is, and
    #: zeroed wherever that one is, so the two agree by construction rather
    #: than by two resets that match today. It is a *subset* of
    #: self_route_count and never a second total: the failure budget is spent
    #: by the difference between them, so a reader of state.json can say how
    #: many times the stage has run without reading prose. A stage whose
    #: workflow declares no bookkeeping budget leaves this at zero however it
    #: re-enters, which is what makes such a stage spend every cause exactly
    #: as it spent them before the split existed.
    bookkeeping_self_route_count: int = 0
    #: The entries of the retry guidance directing the attempt now running:
    #: every current_focus focus and every preserve_behavior string of the
    #: guidance the attempt that just ended actually wrote. Set where a retry
    #: is routed with freshly written guidance and cleared where a retry is
    #: routed without any, so the input to the defective-guidance check is
    #: state rather than a read of retry-history.json or the attempts/
    #: archive — every routing decision must be reconstructable from
    #: state.json and events.log. Empty means no guidance is in force, and a
    #: verification then routes exactly as a first verification does.
    guidance_in_force: list[str] = field(default_factory=list)
    #: How many correction passes this run has taken. Live *and* cumulative,
    #: unlike self_route_count: the budget is one pass per run, so the count
    #: is what makes termination a property rather than a hope, and it is
    #: saved and restored with the rest of the state so a resumed run cannot
    #: spend the pass a second time. A correction pass spends nothing a retry
    #: spends, so this is a separate count and neither reads nor writes
    #: retry_count.
    correction_pass_count: int = 0
    #: How many repair passes this run has taken. Cumulative for the run like
    #: correction_pass_count, and for the same reason: the budget the verifier
    #: stage declares is per run, and the count is what makes a repair the
    #: verifier keeps rejecting terminate rather than cycle. Saved and restored
    #: with the rest of the state so a resumed run cannot spend the pass again,
    #: and defaulted so a state file written before it existed still loads. A
    #: repair pass spends nothing a retry spends and neither reads nor writes
    #: retry_count.
    repair_pass_count: int = 0
    #: Which entry of the run is now executing: zero for the first, one after
    #: the first resume, and so on. A run is re-entered rather than restarted,
    #: and each re-entry opens an entry whose counter-keyed artifacts are kept
    #: under their own directory, so retry_count and verification_iterations
    #: can be reset without an earlier entry's evidence being written over.
    #: Defaulted like every field beside it, so a state file written before it
    #: existed still loads and reads as the first entry.
    resume_count: int = 0
    #: What the current entry of this run has spent, in US dollars, summed
    #: from what each invocation reported. This is the *live allowance*: it is
    #: what the run ceiling is compared against, so it is policy rather than
    #: accounting, and a resume zeroes it exactly as it zeroes the other live
    #: counters — a human typing l5-run is the authorization to spend another
    #: allowance. What the run cost is the record's question, and cost.json
    #: answers it across every entry; one name does not serve both. One number
    #: rather than one per stage, because the run ceiling is the only
    #: cumulative comparison the harness makes and no per-stage total is
    #: accumulated anywhere. Defaulted, so a state file written before it
    #: existed still loads.
    entry_cost_usd: float = 0.0
    #: Whether this run stopped because a cost ceiling was reached. Recorded so
    #: the unchanged-since-escalation guard can be skipped on a resume: that
    #: guard refuses a resume when the story, the branch and the harness are
    #: all unchanged, which is exactly the state a run stopped on cost is in,
    #: and refusing it would refuse the resume this ceiling exists to allow.
    #: Cleared by the resume, like the allowance it accompanies.
    stopped_on_cost: bool = False
    #: The name of the workflow definition this run loaded. Recorded so a
    #: resume loads the same definition it was executing rather than
    #: re-resolving the choice: amending the story artifact to name another
    #: workflow must not change what an interrupted run is resumed under, and
    #: the run's own record is the only thing that can say so. Defaulted like
    #: every field beside it, so a state file written before it existed still
    #: loads and reads as no recorded workflow — which resolves exactly as a
    #: fresh run does.
    workflow: str = ""
    #: The declared suite run that failed and has not been superseded: the
    #: stage, the attempt, the try, the recorded scope, the exit code, and the
    #: retained result and output paths of that run — everything a refusal has
    #: to name, so the failure is reconstructable from state.json alone. Written
    #: where a declared suite run fails and the run advances, so the failure is
    #: outstanding from the moment it happens, and cleared only by a later green
    #: run — declared or clean-clone — whose recorded scope shadows it. A resume
    #: does not clear it: it is
    #: evidence that a failure is unrepaired rather than a live allowance, so it
    #: is restored with the rest of the state while the live counters beside it
    #: are zeroed. What a resume does do is repoint its two paths: the retained
    #: pair moves under the entry directory the resume opens, and the record
    #: follows it through repoint_outstanding_failure, so result_path and
    #: output_path name the pair where it now stands — still run-relative and
    #: absolute respectively — and every line composed from the record names a
    #: path that holds the file. That is the one way this field departs from
    #: retry-history.json, whose moved archive path is resolved by the log and
    #: never rewritten: this is a live pointer the coordinator routes on and
    #: reads back into prose, not append-only evidence. Defaulted like every
    #: field above, so a state file written before it existed loads and reads
    #: as no failure outstanding.
    unshadowed_suite_failure: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StoryReading:
    """The run's one reading of a story artifact: the parse and its problems."""

    parsed: dict | None
    problems: list[str]


def read_story(story_text: str, harness_root: Path | None = None) -> StoryReading:
    """Read a story artifact once, structurally, and report what is wrong.

    The story is parsed with schemas/story.schema.json steering the parse,
    then the parsed structure is validated against that same schema, so the
    shape the planner is asked to produce and the shape enforced here are one
    file. A parse failure is a single line-numbered message; a structural
    failure is one message per offending path.

    The parse is returned rather than discarded: it is the only reading of the
    artifact the run makes, and every structural value the coordinator derives
    from a story comes from it.
    """
    schema = schema_validator.load_schema("story", harness_root)
    try:
        parsed = story_parser.parse(story_text, schema)
    except story_parser.StoryParseError as error:
        return StoryReading(None, [str(error)])
    return StoryReading(parsed, schema_validator.validate(parsed, schema))


#: The longest story title this harness prints beside a story id. The bound
#: keeps an id and its title inside a conventional terminal line, so a message
#: naming both stays one line at the width a developer is most likely reading
#: at. It is bounded at the point it is printed rather than trusted because a
#: title is prose an agent wrote: nothing about the story contract constrains
#: its length or forbids it a newline, and a message assembled from it is a
#: message whose shape the planner would otherwise decide.
PRINTED_TITLE_MAX_LENGTH = 60


def story_title(story: dict | None) -> str:
    """The story's title, collapsed to one line and bounded for printing.

    The one reader of a story artifact's title for the messages that name a
    story beside its id. Whitespace runs collapse to single spaces, so a title
    carrying a newline prints as one line; a title longer than the bound is cut
    to it and ended with a single-character ellipsis.

    An absent parse, a parse carrying no story mapping and a story carrying no
    title all answer with the empty string, so a caller that could not read a
    title names the story by its id alone rather than by a placeholder.
    """
    if not isinstance(story, dict):
        return ""
    inner = story.get("story")
    if not isinstance(inner, dict):
        return ""
    title = inner.get("title")
    if not isinstance(title, str):
        return ""
    collapsed = " ".join(title.split())
    if len(collapsed) <= PRINTED_TITLE_MAX_LENGTH:
        return collapsed
    return collapsed[:PRINTED_TITLE_MAX_LENGTH] + "…"


def story_digest(story_text: str) -> str:
    """A digest of the story artifact exactly as the run was given it.

    Taken from the same text read_story was handed, so the digest and the
    reading describe one artifact. It is recorded on state.json at run start
    for one purpose: a refusal to resume can say whether the story has been
    amended since the run escalated. It never authorizes or triggers a resume —
    an incidental edit to a story must not silently restart a run.
    """
    return hashlib.sha256(story_text.encode("utf-8")).hexdigest()


#: The one kind the resolution walk recognises, and the one it stops at.
#: Everything else is a link to follow, and what any other kind means is
#: settled by the schema that declares it and by what a lookup can answer for
#: it, never here.
HUMAN = "human"

MANDATE_MAX_DEPTH_KEY = "mandate_max_depth"

#: How many links the walk follows when the target configures no bound. A
#: count of hops rather than a duration: the bound exists because a chain that
#: cannot terminate is refused rather than followed, and raising it only
#: decides how long the harness spends discovering that.
DEFAULT_MANDATE_MAX_DEPTH = "8"


def mandate_max_depth(config: dict) -> int:
    """The configured bound on the resolution walk, as a count of hops.

    Read as the string `load_config` produces, because the config parser
    coerces nothing, and parsed here. A value that is not a non-negative
    integer falls back to the default rather than bounding the walk by
    something that is not a count: the bound decides only how long a walk that
    is not going to terminate is followed, so a bad value must not be able to
    make an unresolvable chain resolve.
    """
    configured = config.get(MANDATE_MAX_DEPTH_KEY, DEFAULT_MANDATE_MAX_DEPTH)
    try:
        bound = int(str(configured))
    except (TypeError, ValueError):
        return int(DEFAULT_MANDATE_MAX_DEPTH)
    return bound if bound >= 0 else int(DEFAULT_MANDATE_MAX_DEPTH)


@dataclass(frozen=True)
class MandateResolution:
    """What a resolution walk reached, or the problems that stopped it.

    `kind` and `identity` describe the terminating source and are empty when
    the walk did not terminate; `problems` is empty exactly when it did. The
    walk composes no message about itself beyond the problems, so the caller
    decides the wording around them.
    """

    kind: str
    identity: str
    hops: int
    problems: list[str]

    @property
    def resolved(self) -> bool:
        return not self.problems


def resolve_mandate(mandate, lookup, maximum: int) -> MandateResolution:
    """Follow a mandate's source until a kind of human is reached.

    `lookup` is handed a source the walk cannot terminate on and answers with
    the mandate that source carries, or answers nothing at all. Answering
    nothing is an id that did not resolve rather than a permission, which is
    the whole of the bias here: the walk refuses on anything it cannot
    establish and permits only on positive evidence of a human.

    Four things stop it, and each is its own problem so a reader of the
    message can tell which happened: there is no block to resolve, an id that
    nothing answered for, a return to an id already visited, and a chain
    longer than `maximum`. A repeat visit is recognised before the bound is
    compared, so a chain that comes back to itself says it is a cycle rather
    than reporting whichever of the two a small bound happened to reach first.

    A kind of human terminates in one hop with the lookup never called, so a
    mandate conferred directly by a person reaches nothing at all.
    """
    if not isinstance(mandate, dict):
        return MandateResolution(
            "",
            "",
            0,
            [
                "there is no mandate to resolve: nothing recorded what "
                "conferred the right to run this work"
            ],
        )
    visited: list[str] = []
    current = mandate
    hops = 0
    while True:
        source = current.get("source")
        source = source if isinstance(source, dict) else {}
        kind = source.get("kind", "")
        if kind == HUMAN:
            return MandateResolution(kind, current.get("conferred_by", ""), hops, [])
        hops += 1
        identifier = source.get("id")
        if not isinstance(identifier, str) or not identifier:
            return MandateResolution(
                "",
                "",
                hops,
                [
                    f"the source of kind '{kind}' carries no id, so there is "
                    f"nothing to resolve it through and no way to reach a "
                    f"source whose kind is '{HUMAN}'"
                ],
            )
        if identifier in visited:
            return MandateResolution(
                "",
                "",
                hops,
                [
                    f"the chain of sources returns to id '{identifier}', which "
                    f"it has already visited: it is a cycle, and following it "
                    f"further cannot reach a source whose kind is '{HUMAN}'"
                ],
            )
        visited.append(identifier)
        if hops > maximum:
            return MandateResolution(
                "",
                "",
                hops,
                [
                    f"the chain of sources is longer than "
                    f"{MANDATE_MAX_DEPTH_KEY} ({maximum}) and has not reached "
                    f"a source whose kind is '{HUMAN}'"
                ],
            )
        answered = lookup(source)
        if not isinstance(answered, dict):
            return MandateResolution(
                "",
                "",
                hops,
                [
                    f"the id '{identifier}' named by the source of kind "
                    f"'{kind}' did not resolve: nothing answered for it"
                ],
            )
        current = answered


def resolves_nothing(source: dict) -> dict | None:
    """The lookup this deployment hands the walk: it answers for nothing.

    A fact about the deployment rather than about the walk. This harness has
    no store a non-human source could be fetched from, so every source the
    walk cannot terminate on is an id that did not resolve. A deployment that
    acquires such a store passes a lookup that answers, and the walk is
    unchanged by it.
    """
    return None


#: The workflow key declaring what a stage may not *add* to. A path at or
#: beneath one of its prefixes is governed; a path outside all of them is not.
CREATE_RESTRICTION = "may_not_create"

#: The workflow key confining a stage's repository writes to the prefixes it
#: names. It is the mirror image of the key above: a path *outside* every named
#: prefix is governed, and a path at or beneath one of them is not.
CONFINEMENT = "may_only_change"


def _inside(prefix: str, path: str) -> bool:
    """Whether `path` is at or beneath a confinement prefix.

    A prefix ending in a slash is a directory and covers everything beneath it;
    any other prefix is exactly one file and covers only the identical path.
    The same slash convention `grant_covers` gives a granted value, so a
    confinement and a grant agree about what a slash-less value means.
    """
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix


@dataclass(frozen=True)
class StageRestriction:
    """One declared restriction on where a stage may write, and what it means.

    A restriction carries its stage, the prefix it is named for, its sense, and
    the predicate deciding whether it governs a given path — so every reader
    asks `governs` rather than matching a prefix itself, and neither the
    coordinator's enforcement path nor the plan-time checks need a conditional
    on which sense they are looking at. Adding a sense is adding a member of
    `_SENSES` below; no reader changes.

    `prefixes` is the whole list the declaration this restriction came from
    named, because a confinement is a statement about that list rather than
    about any one of its members: a stage confined to two directories is
    governed only outside *both*, while the restriction is still reported once
    per declared prefix so a reader meets each one in its own wording.
    """

    stage: str
    prefix: str
    sense: str
    prefixes: tuple[str, ...]

    def governs(self, path: str) -> bool:
        """Whether this restriction has anything to say about this path.

        A create restriction speaks for its own prefix alone: a stage
        restricted from two directories is governed beneath each of them
        independently, and reading the group would make one prefix's
        restriction claim a path only its sibling covers. A confinement is the
        opposite and has to read the group, because being confined to two
        directories means being governed only outside *both*.

        A prefix not ending in a slash is exactly that file, not a directory
        whose name it happens to be: `.harness/docs/ARCHITECTURE.md` confines a
        stage to that one file, so `.harness/docs/ARCHITECTURE.md.bak` is
        governed rather than read as inside it. That is the meaning `grant_covers`
        already gives a slash-less value and the meaning the `:(exclude,top)`
        pathspec the baseline capture builds already has; a prefix ending in a
        slash keeps its directory meaning. A confinement decides `inside` per
        prefix rather than by `startswith`, so a slash-less prefix admits only
        the identical path.
        """
        if self.sense == CREATE_RESTRICTION:
            return path.startswith(self.prefix)
        return not any(_inside(prefix, path) for prefix in self.prefixes)

    @property
    def wording(self) -> str:
        """The restriction stated as the workflow declares it, in one sentence.

        Composed here so a prompt, a plan-time refusal and a run-time
        escalation state the same restriction identically rather than in three
        spellings that agree today.
        """
        return f"{self.stage} {_SENSES[self.sense]} {self.prefix}"

    @property
    def routes_necessary_edits(self) -> bool:
        """Whether a governed edit the revert check finds necessary is routed
        rather than permitted.

        The check asks whether an edit was needed; whether the stage was
        entitled to make it is a different question, and the answer depends on
        the sense. A confinement says the stage's work is what lies inside its
        prefixes, so a necessary edit outside them is another stage's work
        that this stage did — needed by the tree, and not this stage's to
        leave standing. A create restriction says only what the stage may not
        add; a modification it was forced into is maintenance of something it
        may own, and stands. Read off `_ROUTES_NECESSARY_EDITS` so no call
        site compares the sense; adding a sense is adding a row there.
        """
        return _ROUTES_NECESSARY_EDITS[self.sense]


#: How each sense reads in prose, keyed by the workflow key that declares it.
#: The one place a restriction's words are written; `StageRestriction.wording`
#: is its only reader.
_SENSES = {
    CREATE_RESTRICTION: "may not create files under",
    CONFINEMENT: "may only change files under",
}

#: Whether a necessary edit under each sense is routed rather than permitted,
#: keyed as `_SENSES` is. `StageRestriction.routes_necessary_edits` is its
#: only reader, and a sense declared in one table and not the other fails
#: loudly there rather than falling through to either disposition.
_ROUTES_NECESSARY_EDITS = {
    CREATE_RESTRICTION: False,
    CONFINEMENT: True,
}


def restrictions_on(stage: dict) -> list[StageRestriction]:
    """One stage's declared restrictions, in the order the senses are declared.

    The per-stage accessor the coordinator's enforcement path reads, so the
    ownership check, the revert check and the baseline capture are driven by
    the same derivation plan time reads rather than by a second reading of a
    declaration key at the call site.
    """
    return [
        StageRestriction(stage["name"], prefix, sense, tuple(prefixes))
        for sense in _SENSES
        for prefixes in [stage.get(sense, [])]
        for prefix in prefixes
    ]


def stage_restrictions(stages: list[dict]) -> list[StageRestriction]:
    """The workflow's declared path restrictions, in declared order.

    The mapping from a stage to what it may not write is derived here and
    nowhere else, so the exception cross-check below, the coordinator's
    enforcement path, the baseline capture, both plan-time checks in
    plan_validation and the planner injection read one derivation rather than
    several copies of it that can disagree. No stage name, no prefix and no
    sense conditional is written in any of them; all of it comes off the loaded
    workflow definition and travels in the restriction.
    """
    return [
        restriction for stage in stages for restriction in restrictions_on(stage)
    ]


def stage_exception_problems(story: dict, stages: list[dict]) -> list[str]:
    """Cross-check a story's stage exceptions against the loaded workflow.

    read_story answers whether a story conforms to its schema. This answers
    the separate question the schema cannot: whether an exception means
    anything against the workflow this run actually loaded. An exception
    naming a stage that does not exist, or granting a path its stage was
    never restricted on, grants nothing — a planning error rather than a
    harmless one, so both refuse the run. Stage names and prefixes come from
    the workflow definition; none is named here.

    A grant is accepted when one of that stage's restrictions *governs* the
    granted value, which is the same question the ownership check and the
    revert check ask of a path — so what a grant may name is decided by the
    restriction rather than by a prefix comparison written here, and each sense
    accepts what it governs: a create restriction accepts a value at or beneath
    a declared prefix, so a grant may name a single file, or a directory,
    beneath one rather than only the whole prefix; a confinement accepts a
    value outside every prefix it names, which is where that stage's writes are
    governed. Widening what is accepted narrows nothing: a whole-prefix grant
    keeps its meaning and its effect on both checks. A value no restriction
    governs is still refused with the message it prints today — a grant that
    lifts nothing grants nothing — as is a stage the loaded workflow does not
    define.
    """
    restricted: dict[str, list[StageRestriction]] = {
        stage["name"]: [] for stage in stages
    }
    for restriction in stage_restrictions(stages):
        restricted[restriction.stage].append(restriction)
    problems = []
    for index, exception in enumerate(story.get("stage_exceptions", [])):
        name, granted = exception["stage"], exception["create"]
        if name not in restricted:
            problems.append(
                f"$.stage_exceptions[{index}]: names stage '{name}', which the "
                f"loaded workflow does not define"
            )
        elif not any(
            restriction.governs(granted) for restriction in restricted[name]
        ):
            problems.append(
                f"$.stage_exceptions[{index}]: grants '{granted}' to stage "
                f"'{name}', which was never restricted from creating it"
            )
    return problems


def _clean_clone_route(stage: dict) -> context_assembler.RetryRoute | None:
    """The route a clean-clone failure on this stage takes, if it declares one.

    The clean-clone declaration names both artifacts of that check — the
    result it writes and the stage a failure routes to — so one key still
    turns the whole check on and its route is held to the same pre-flight as
    the categories the verifier chooses between. It is not one of those
    categories: nothing chooses it, so it carries no `when` and is never
    rendered into a prompt.
    """
    declaration = stage.get("clean_clone")
    if not isinstance(declaration, dict) or "retry_stage" not in declaration:
        return None
    return context_assembler.RetryRoute(
        stage["name"], "clean_clone", declaration["retry_stage"], ""
    )


def retry_routing_problems(stages: list[dict]) -> list[str]:
    """Check every route the workflow declares against the workflow itself.

    A route is validated when the workflow loads rather than when a retry
    happens, because a table naming a stage that does not exist is a defect
    in the definition and a run that discovers it three stages in has
    already spent an implementer, a tester and a verifier on it.

    Two problems, and they are the only two a definition can state without
    knowing what any run will do. A destination the workflow does not define
    cannot be routed to at all. A destination at or after the stage that
    declares the route routes *forward*, which would carry the run past the
    verification that sent it back — the retry would never be checked.

    Both the retry_routing entries and the clean-clone route are held to it.
    No category name and no destination is written here; both come off the
    loaded workflow definition.
    """
    names = [stage["name"] for stage in stages]
    declared = list(context_assembler.retry_routes(stages))
    declared += [
        route for route in map(_clean_clone_route, stages) if route is not None
    ]
    problems = []
    for route in declared:
        if route.stage not in names:
            problems.append(
                f"the '{route.category}' route on stage '{route.declared_by}' "
                f"names stage '{route.stage}', which the loaded workflow does "
                f"not define"
            )
        elif names.index(route.stage) >= names.index(route.declared_by):
            problems.append(
                f"the '{route.category}' route on stage '{route.declared_by}' "
                f"names stage '{route.stage}', which does not sit before it; "
                f"routing forward would skip verification"
            )
    return problems


def self_route_problems(stages: list[dict]) -> list[str]:
    """Check every declared self-route budget against what a budget can be.

    Both budgets, on the same terms and in the same loop: a stage may declare
    a bookkeeping budget beside its failure budget, and a bookkeeping budget
    that is not a count cannot be spent any more than a failure one can, so a
    definition declaring an unspendable one is refused at pre-flight rather
    than discovered mid-run.

    Beside `retry_routing_problems` and for its reason: a budget that is not a
    count is a defect in the definition, every run under that definition has
    it, and discovering it at the first mechanical failure has already spent
    the stages before it.

    A stage that declares nothing is not checked — declaring no budget is the
    normal case and means the stage escalates on a mechanical failure exactly
    as it always has. A declared value must be a non-negative integer: zero is
    a deliberate declaration of no budget and is accepted, a negative one is a
    count that cannot be spent, and `True` is not a budget however much Python
    is willing to treat it as one. No stage name and no budget value is written
    here; both come off the loaded workflow definition.
    """
    problems = []
    for stage in stages:
        for key in (SELF_ROUTE_BUDGET_KEY, BOOKKEEPING_SELF_ROUTE_BUDGET_KEY):
            if key not in stage:
                continue
            budget = stage[key]
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
                problems.append(
                    f"stage '{stage['name']}' declares {key} "
                    f"{budget!r}, which is not a non-negative integer"
                )
    return problems


def correction_pass_problems(stages: list[dict]) -> list[str]:
    """Check the correction-pass declaration's entry stage against the workflow.

    Beside `retry_routing_problems` and `self_route_problems`, and for their
    reason: a declaration naming a stage the workflow does not define is a
    defect every run under that definition carries, and a run that discovers
    it only when a passing verdict happens to carry a correctable finding has
    spent the whole workflow first — and discovers it at the one moment
    everything else went right.

    The cross-check is the one the coordinator already makes against a story's
    `stage_exceptions`: the named stage must be among the stages the loaded
    workflow defines. A declaration that names none at all is the same defect
    said differently, because the entry stage is what the declaration is for.
    A workflow carrying no declaration is not checked — that is how the whole
    mechanism is switched off. No stage name is written here; it comes off the
    loaded definition.
    """
    declaration = correction_pass_declaration(stages)
    if not declaration:
        return []
    names = [stage["name"] for stage in stages]
    entry = declaration.get("stage")
    if entry is None:
        return [
            "the correction_pass declaration names no stage to enter at; it "
            f"must name one of: {', '.join(names)}"
        ]
    if entry not in names:
        return [
            f"the correction_pass declaration names stage '{entry}', which "
            f"the loaded workflow does not define; it defines: "
            f"{', '.join(names)}"
        ]
    return []


def repair_pass_problems(stages: list[dict]) -> list[str]:
    """Check the repair-pass declaration against the workflow that carries it.

    Beside `correction_pass_problems`, and for its reason: a declaration every
    run under the definition would trip on is refused before any run state is
    created rather than at the one moment everything else went right. Two
    things a definition can be wrong about here. The budget must be a positive
    integer, because a budget that is not a count cannot be spent and a budget
    of zero is a declaration that should have been left out. And the stage
    declaring the pass must declare a retry_routing table, because a repair
    pass names no entry stage of its own: each finding's category is looked up
    in that table exactly as a failed verdict's retry_target is, so a stage
    with no table has nowhere to send a repair. A workflow carrying no
    declaration is not checked — that is how the mechanism is switched off.
    No stage name is written here; it comes off the loaded definition.
    """
    problems: list[str] = []
    for stage in stages:
        declaration = stage.get("repair_pass")
        if not declaration:
            continue
        budget = declaration.get("budget")
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
            problems.append(
                f"stage '{stage['name']}' declares a repair_pass budget of "
                f"{budget!r}, which is not a positive integer"
            )
        routes = (stage.get("on_failure") or {}).get("retry_routing") or {}
        if not routes:
            problems.append(
                f"stage '{stage['name']}' declares a repair_pass but no "
                f"retry_routing table, so a repairable finding's category "
                f"has nothing to resolve against"
            )
    return problems


def _is_a_ceiling(value) -> bool:
    """Whether a declared ceiling is a value a ceiling can take.

    A non-negative number, and not a bool: `isinstance(True, int)` holds, so a
    bare number test would accept a declaration of `true` as a ceiling of one
    dollar. Zero is accepted and means a deliberate refusal to run at all,
    which is a different declaration from making none.
    """
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value >= 0
    )


def cost_ceiling_problems(workflow: dict) -> list[str]:
    """Check every declared cost ceiling against what a ceiling can be.

    Beside `retry_routing_problems` and `self_route_problems`, and for their
    reason: a ceiling that is not a number cannot be compared or handed to an
    invocation, every run under that definition carries the defect, and
    discovering it at the first stage has already spent one.

    Two ceilings are checked, the workflow's own `max_run_cost_usd` and each
    stage's `max_execution_cost_usd`. A definition declaring neither is not
    checked at all — absent means unbounded, which is the behaviour every
    workflow had before ceilings existed. The sibling `_reason` keys are not
    read here or anywhere else; they record the judgement where a reader of
    the definition meets the number.
    """
    problems = []
    if "max_run_cost_usd" in workflow:
        ceiling = workflow["max_run_cost_usd"]
        if not _is_a_ceiling(ceiling):
            problems.append(
                f"workflow '{workflow.get('name')}' declares max_run_cost_usd "
                f"{ceiling!r}, which is not a non-negative number"
            )
    for stage in workflow.get("stages", []):
        if "max_execution_cost_usd" not in stage:
            continue
        ceiling = stage["max_execution_cost_usd"]
        if not _is_a_ceiling(ceiling):
            problems.append(
                f"stage '{stage['name']}' declares max_execution_cost_usd "
                f"{ceiling!r}, which is not a non-negative number"
            )
    return problems


#: What a target that configures no pause wait waits: nothing. Zero is the
#: default here because here zero means "do not wait". The other default
#: durations in harness source — `SUITE_TURN_WAIT_FALLBACK_SECONDS` below and
#: the sync timeout in `orchestration/outbox_sweep.py` — are non-zero because
#: for each of them zero means something else, and each says what.
NO_PAUSE_WAIT = "0"


def pause_wait_bound(config: dict) -> float:
    """The configured ceiling on an in-place wait, in seconds.

    Read as the string `load_config` produces, because the config parser
    coerces nothing, and parsed here. Only ever called after
    `pause_wait_problems` has passed, so the parse cannot fail: a value that is
    not a non-negative number refuses the run at pre-flight rather than
    reaching a comparison.
    """
    return float(config.get("max_pause_wait_seconds", NO_PAUSE_WAIT))


def pause_wait_problems(config: dict) -> list[str]:
    """Check the configured pause wait against what a bound can be.

    Shaped like `cost_ceiling_problems`, and for its reason: a bound that
    cannot be compared against a wait is a defect every run under that
    configuration carries, so it is refused before a stage is spent on it. An
    absent key is not checked at all — absent means zero, which is never
    waiting, and is what every run did before this key existed.
    """
    if "max_pause_wait_seconds" not in config:
        return []
    configured = config["max_pause_wait_seconds"]
    try:
        bound = float(configured)
    except (TypeError, ValueError):
        bound = -1.0
    if bound < 0 or bound != bound:  # NaN compares unequal to itself
        return [
            f"max_pause_wait_seconds is {configured!r}, which is not a "
            f"non-negative number of seconds"
        ]
    return []


#: How many suite runs' worth of time a run waits for its turn at the whole
#: suite when another run of the same repository holds it, unset. The number
#: counts suite runs as this machine has measured them — the bound is the
#: count times `duration_estimate(SUITE_RUN_OPERATION)` — because what the
#: wait is for is the holder's suite to end, and how long that takes is a fact
#: about this machine and not one to configure. Five, because a run that has
#: outwaited five suites is behind a suite that hung rather than one that is
#: merely slow, and a hung suite must not stall every run behind it, which is
#: why the wait is bounded at all.
DEFAULT_SUITE_TURN_WAIT_SUITE_RUNS = "5"

#: The bound where the harness has not yet measured enough suite runs to know
#: what one takes on this machine: a fresh target, or fewer than
#: `DURATION_ESTIMATE_MINIMUM` observations. Sixty minutes is several suite
#: runs on this repository, so a run that outlasts it is a suite hung in
#: another run rather than one that is merely slow. It is the reason the
#: minute default carried before the count replaced it.
SUITE_TURN_WAIT_FALLBACK_SECONDS = 60 * 60.0

#: How often a run waiting for its turn asks for the lock again. The poll is
#: what makes a killed holder's release visible: the kernel drops the lock the
#: moment the holder dies, and the next poll takes it.
SUITE_TURN_POLL_SECONDS = 0.5

#: The lock file's name under the worktree root. Beside the worktrees because
#: that directory is already the harness's own, already outside the repository,
#: and already shared by every worktree of one repository.
SUITE_LOCK_NAME = "suite.lock"

#: The second, momentary lock a waiter whose bound expired takes across the
#: swap that takes the turn over — see `_take_over_suite_turn`. Beside the
#: suite lock, and held only for the length of the swap.
SUITE_TAKEOVER_LOCK_NAME = "suite.takeover.lock"


def _suite_turn_wait_count(config: dict) -> float:
    """The configured count of suite runs, read as the string `load_config`
    produces and parsed here. Only ever called after
    `suite_turn_wait_problems` has passed, so the parse cannot fail.
    """
    return float(
        config.get("suite_turn_wait_suite_runs", DEFAULT_SUITE_TURN_WAIT_SUITE_RUNS)
    )


def suite_turn_wait_seconds(
    config: dict, *, fallback: float | None = SUITE_TURN_WAIT_FALLBACK_SECONDS,
) -> float | None:
    """The bound on waiting for a turn at the suite, in seconds.

    Shaped like `pause_wait_bound`: the key is read as the string `load_config`
    produces, parsed here, and only ever read after `suite_turn_wait_problems`
    has passed. The key counts suite runs, and the bound is that count times
    what a suite run takes on this machine, read through
    `duration_estimate(SUITE_RUN_OPERATION)` and nothing else. Where no
    estimate exists the answer is `fallback` — `SUITE_TURN_WAIT_FALLBACK_SECONDS`
    unless the caller says otherwise, and None where the caller wants to know
    that the fallback applied rather than a bound to wait for, which is how
    `suite_turn_wait_cost` learns where the bound came from without a read of
    its own. Zero is zero either way: take the turn only if it is free, and
    otherwise run at once.

    This is the one function outside the announcement renderers that reads an
    estimate, and `tests/test_an_operation_announces_what_it_has_taken_before.py`
    names it as such. History is evidence and never state: no verdict, check,
    retry or escalation may depend on what earlier runs took, because a
    decision that reads history is a decision `state.json` and `events.log`
    cannot reconstruct. A wait's length is not such a decision. The estimate
    decides how long a run waits before taking a turn the holder is no longer
    using, and nothing about which stage runs next, what a check concludes or
    what a run records — two identical runs over a seeded duration log and over
    none reach the same stages, the same verdicts and the same exit code, and
    differ only in how long one of them waited. That is why this bound may read
    the estimate where a verdict may not, and why it is the only bound that
    does.
    """
    count = _suite_turn_wait_count(config)
    if count == 0:
        return 0.0
    estimate = duration_estimate(SUITE_RUN_OPERATION)
    if estimate is None:
        return fallback
    return count * estimate


def suite_turn_wait_cost(config: dict) -> str:
    """What a contended wait is worth, said with where its bound came from.

    One of two wordings for a non-zero count — the count times the estimate,
    or the fallback because too few suite runs have been measured here — and a
    third for zero, whose bound comes from neither. Composed here so that
    `suite_turn` does not branch on the estimate inline. The estimate is read
    through `suite_turn_wait_seconds` alone — asked with no fallback, so that
    None is the answer where too few suite runs have been measured — and the
    estimate the wording names is the bound divided back by the count, so the
    clause and the bound cannot come from two reads.
    """
    count = _suite_turn_wait_count(config)
    if count == 0:
        return (
            "this waits for at most 0 minutes, because "
            "suite_turn_wait_suite_runs is 0: the turn is taken only if it "
            "is free, and otherwise the suite runs at once without one"
        )
    from_estimate = suite_turn_wait_seconds(config, fallback=None)
    if from_estimate is None:
        return (
            f"this waits until that run's suite ends, or for at most "
            f"{SUITE_TURN_WAIT_FALLBACK_SECONDS / 60:g} minutes, whichever "
            f"comes first; that is the fallback bound, because too few suite "
            f"runs have been measured here to know what one takes"
        )
    return (
        f"this waits until that run's suite ends, or for at most "
        f"{from_estimate / 60:g} minutes, whichever comes first; that is "
        f"{count:g} suite run(s) at the {from_estimate / count / 60:g}-minute "
        f"estimate measured here"
    )


def suite_turn_wait_problems(config: dict) -> list[str]:
    """Check the configured suite-turn wait against what a count can be.

    Shaped like `pause_wait_problems`, and for its reason: a count that cannot
    be multiplied into a bound is a defect every run under that configuration
    carries, so it is refused before a stage is spent on it. An absent key is
    not checked at all — absent means five suite runs, which is what
    `suite_turn_wait_seconds` answers for it.
    """
    if "suite_turn_wait_suite_runs" not in config:
        return []
    configured = config["suite_turn_wait_suite_runs"]
    try:
        bound = float(configured)
    except (TypeError, ValueError):
        bound = -1.0
    if bound < 0 or bound != bound:  # NaN compares unequal to itself
        return [
            f"suite_turn_wait_suite_runs is {configured!r}, which is not a "
            f"non-negative number of suite runs"
        ]
    return []


def applies_when_problems(workflow: dict) -> list[str]:
    """Check that the definition says when it is the workflow to plan under.

    Beside `cost_ceiling_problems`, `self_route_problems` and
    `retry_routing_problems`, and for their reason: a definition that cannot
    say what it is for is a defect every run under it carries, and discovering
    it at the first stage has already spent one.

    Unlike those three, `applies_when` is required rather than optional, and
    the required-ness is what the field is for: the developer chooses between
    the definitions the harness holds by reading what each says it is for, so a
    definition that says nothing is a definition that cannot be chosen between.
    A value that is not a string, or one that is only whitespace, is no more
    usable than an absent one and is reported the same way.
    """
    declared = workflow.get("applies_when")
    if isinstance(declared, str) and declared.strip():
        return []
    return [
        f"workflow '{workflow.get('name')}' declares applies_when "
        f"{declared!r}, which does not say when this workflow is the right "
        f"one to plan under"
    ]


def granted_paths(story: dict, stage_name: str) -> list[str]:
    """The values this story's stage_exceptions grant to one stage, in order.

    Public because plan_validation reads it: the plan-time check asks whether
    a grant already covers a file, which is the same question the run-time
    checks ask, and a second reader of the story's grants would be a second
    answer to it.
    """
    return [
        exception["create"]
        for exception in story.get("stage_exceptions", [])
        if exception["stage"] == stage_name
    ]


def grant_covers(granted: list[str], path: str) -> bool:
    """Whether any granted value covers this path.

    One function decides this, and the three readers that need it — the
    plan-time assignment check, the ownership check and the revert check —
    all call it, so a grant cannot mean one thing when a plan is written and
    another when it runs.

    A granted value ending in a slash is a directory and covers every path
    beneath it, which is what a whole-prefix grant has always meant. Any other
    granted value names one path and covers exactly and only that path — it is
    not a prefix match, so granting one file beneath a prefix leaves every
    other file beneath it governed.
    """
    for value in granted:
        if value.endswith("/"):
            if path.startswith(value):
                return True
        elif path == value:
            return True
    return False


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def save_state(run_dir: Path, state: RunState) -> None:
    _state_path(run_dir).write_text(
        json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8"
    )


def load_state(run_dir: Path) -> RunState | None:
    path = _state_path(run_dir)
    if not path.is_file():
        return None
    return RunState(**json.loads(path.read_text(encoding="utf-8")))


def _history_path(run_dir: Path) -> Path:
    return run_dir / "execution-history.json"


def load_history(run_dir: Path) -> list[dict]:
    """The run's structured history so far, empty before the first event."""
    path = _history_path(run_dir)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The cross-run history
#
# A run directory is execution state and is correctly not versioned, so
# everything the harness knew about an execution goes with the directory when
# it is deleted. The answer is a separate collection of append-only records
# that outlive the executions they describe: harness-scoped logs under
# history_dir, written through append_event as events occur rather than
# assembled as a terminal summary, summaries and never copies, and bounded by
# configuration.
#
# Which events are significant is read from
# schemas/cross-run-history.schema.json and from nowhere else. Each declared
# log names the execution-history kinds routed to it and the fields projected
# into a record there, so selection and record shape are one file and no
# condition in append_event decides either — the harness does not acquire a
# fourth place where "which events matter" is settled.
# --------------------------------------------------------------------------

#: The declaration everything below reads.
CROSS_RUN_HISTORY_SCHEMA = "cross-run-history"

#: The one declared property of a log that is a selection rather than a
#: projected field: its enum names the execution-history kinds routed there.
#: A record carries the other declared fields, so what routed it is read off
#: the enum that named its kind rather than repeated on every line.
HISTORY_EVENT_PROPERTY = "event"

#: How a record's timestamp is written, matching what append_event stamps an
#: events.log line and an execution-history entry with. It is what a retention
#: bound is compared against.
HISTORY_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

#: The configured retention bound. Absent keeps everything, so pruning is
#: opt-in.
HISTORY_RETENTION_KEY = "history_retention_days"

#: The resolved history directory for the run now executing, established once
#: at run-directory creation rather than passed to each append_event call. A
#: keyword argument with a default can be omitted at one call site and silently
#: drop a record, which is the one failure mode an append-only store cannot
#: have. None is "no run has established one", and then nothing is projected —
#: which is what a caller appending an event to a hand-built run directory,
#: outside any run, gets.
_history_dir: Path | None = None

#: How many records each history file holds past the configured retention
#: bound, as this run's startup read counted them, keyed by the file's path
#: relative to the history directory. Established once per run beside
#: `_history_dir` and in the same shape, so no terminal path can be handed a
#: count by a keyword argument a call site omits. Empty is "nothing past the
#: bound, or no bound at all", and then the run's end reports nothing. It
#: routes nothing: the run never rewrites the history, and what it holds here
#: is only what the end-of-run note says.
_history_past_bound: dict[str, int] = {}

#: The configured bound those counts were taken against, as the configuration
#: spells it, so the end-of-run note can name the value without a terminal
#: path having to be handed the configuration.
_history_retention_bound: object = None


def set_history_dir(directory: Path | None) -> None:
    """Establish where this run's cross-run records go, for the whole run.

    Establishing a directory is the start of a run's dealings with the
    history, so whatever an earlier run in this process counted past the
    bound is void here: the counts are cleared until this run's own read
    establishes them, and a caller establishing none — a hand-built run
    directory outside any run — owes no end-of-run note.
    """
    global _history_dir
    _history_dir = directory
    set_history_past_bound({})
    set_duration_estimates({})


def set_history_past_bound(counts: dict[str, int], bound: object = None) -> None:
    """Establish what this run's startup read counted past the bound, and the
    bound it counted against, for the whole run.
    """
    global _history_past_bound, _history_retention_bound
    _history_past_bound = dict(counts)
    _history_retention_bound = bound


# --------------------------------------------------------------------------
# How long a long step is expected to take
#
# Every long step announces itself before it starts and says what the wait is
# worth. What it said was self-referential — "as long as a suite run", "about
# as long as a stage" — which tells a reader the step is long and nothing
# about how long. The harness measured stage durations on every
# stage-completed entry and threw them away with the run directory. Now every
# timed operation leaves one record in the declared duration log, and a run
# reads an estimate off that log at the moment a step announces itself: per
# operation, the median of a bounded window of the most recent observations.
#
# The estimate is a report to a person and never a bound. No timeout reads it,
# no check compares against it, and a step that runs past it is a step,
# running. Where too few observations exist the step says exactly what it said
# before, so a fresh target announces nothing invented.

#: How many of an operation's most recent observations an estimate is the
#: median of. A module constant rather than configuration: the window decides
#: how quickly the figure follows a change in the machine or the suite, which
#: is a judgement about the estimate and not about any target.
DURATION_ESTIMATE_WINDOW = 10

#: How many observations an operation needs before a figure is announced at
#: all. Below it the step announces what it announced before this existed.
DURATION_ESTIMATE_MINIMUM = 3

#: The fixed operation names for the checks and the inspection. A stage's
#: operation is its own name, so it is not spelled here.
CLEAN_CLONE_OPERATION = "clean-clone"
SUITE_RUN_OPERATION = "suite-run"
REVERT_CHECK_OPERATION = "revert-check"
SUITE_CENSUS_OPERATION = "suite-census"
INSPECTION_OPERATION = "inspection"

#: The per-operation estimates this run announces with, in seconds, keyed by
#: operation. Established once per run from the startup read of the history,
#: in the shape `_history_past_bound` takes and for its reason: a keyword
#: argument a call site omits would silently drop the figure, and module state
#: established once cannot be omitted. Empty means no operation has enough
#: history, and then every announcement renders exactly what it rendered
#: before. Nothing routes on it.
_duration_estimates: dict[str, float] = {}


def set_duration_estimates(estimates: dict[str, float]) -> None:
    """Establish the per-operation estimates this run announces with."""
    global _duration_estimates
    _duration_estimates = dict(estimates)


def duration_estimate(operation: str | None) -> float | None:
    """The estimate this run holds for `operation`, in seconds, or None where
    it holds none — too few observations, or no history read at all.
    """
    if operation is None:
        return None
    return _duration_estimates.get(operation)


def duration_log_name(declarations: dict[str, dict]) -> str | None:
    """The declared log whose record shape carries an operation and a duration.

    Located through the declaration and never by filename: the log is the one
    whose `properties` declare both `operation` and `duration_seconds`, so a
    deployment that named the file differently would still be read.
    """
    for name, declaration in declarations.items():
        fields = declaration.get("properties", {})
        if "operation" in fields and "duration_seconds" in fields:
            return name
    return None


def _observation(record: object) -> tuple[float, str, float] | None:
    """One record read as an observation: (when, operation, seconds), or None.

    A record whose duration is not a number, whose operation is not a string,
    or whose timestamp cannot be read is not an observation and stops nothing —
    the same treatment `_record_is_older` gives a timestamp it cannot judge.
    """
    if not isinstance(record, dict):
        return None
    seconds = record.get("duration_seconds")
    operation = record.get("operation")
    stamp = record.get("timestamp")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    if not isinstance(operation, str) or not isinstance(stamp, str):
        return None
    try:
        when = time.mktime(time.strptime(stamp, HISTORY_TIMESTAMP_FORMAT))
    except (TypeError, ValueError, OverflowError):
        return None
    return when, operation, float(seconds)


def duration_estimates(records: Iterable[object]) -> dict[str, float]:
    """Per-operation estimates from the duration log's parsed records.

    Pure over its input. For each operation the observations are ordered by
    their record timestamp — never by file order, since a log is gathered from
    segments whose names do not sort in time order — the most recent
    `DURATION_ESTIMATE_WINDOW` are kept, and where at least
    `DURATION_ESTIMATE_MINIMUM` exist their median is the estimate. An
    operation with fewer has no entry, and a record that is not an observation
    contributes nothing.
    """
    by_operation: dict[str, list[tuple[float, float]]] = {}
    for record in records:
        observed = _observation(record)
        if observed is None:
            continue
        when, operation, seconds = observed
        by_operation.setdefault(operation, []).append((when, seconds))
    estimates: dict[str, float] = {}
    for operation, observations in by_operation.items():
        recent = sorted(observations)[-DURATION_ESTIMATE_WINDOW:]
        if len(recent) < DURATION_ESTIMATE_MINIMUM:
            continue
        estimates[operation] = float(statistics.median(seconds for _, seconds in recent))
    return estimates


def history_duration_records(reading: "HistoryReading") -> list[object]:
    """Every parsed record of the duration log the startup read found.

    The log is located through `duration_log_name` over the declarations the
    read itself walked; a declaration set carrying no such log yields nothing,
    and then no estimate is established.
    """
    log = duration_log_name(history_log_declarations())
    if log is None:
        return []
    return [record for path, lines in reading.files.items()
            if path.name == log for _, record in lines]


def estimated_run_time(seconds: float) -> str:
    """The parenthesised clause an estimate is announced in.

    Whole minutes, or whole seconds when the estimate is under a minute, each
    singular at one — exactly the form "(Estimated run time: 3 minutes)". No
    completion time is printed: every line is already stamped with its write
    time. This is the one spelling of the form; `_announcement` renders it for
    the steps that announce and the stage-started line renders it for a stage.
    """
    if seconds < 60:
        count, unit = max(0, round(seconds)), "second"
    else:
        count, unit = max(1, round(seconds / 60)), "minute"
    plural = "" if count == 1 else "s"
    return f"(Estimated run time: {count} {unit}{plural})"


def _with_estimate(message: str, estimate: float | None) -> str:
    """`message`, followed by the estimate's clause where there is one."""
    if estimate is None:
        return message
    return f"{message} {estimated_run_time(estimate)}"


def history_log_declarations(harness_root: Path | None = None) -> dict[str, dict]:
    """Each declared log's filename mapped to the shape of a record in it.

    The schema ships with the harness code, so it is resolved by
    schema_validator relative to its own module exactly as every other schema
    is. A log declares itself as that log read whole — an array of parsed
    lines — so the record shape is its `items`.
    """
    schema = schema_validator.load_schema(CROSS_RUN_HISTORY_SCHEMA, harness_root)
    return {name: log["items"] for name, log in schema["properties"].items()}


def history_record(
    entry: dict, history: list[dict], story_id: str, declaration: dict
) -> dict | None:
    """The record a history entry projects into one log, or None for no record.

    The entry's kind is checked against the log's declared enum and against
    nothing else: an entry whose kind appears in no log's enum is projected
    nowhere, so the selection is decided by the declaration alone.

    Three values are derived rather than threaded through the coordinator to
    serve this projection: the story id from the run directory's own name, the
    status from the event kind that produced the record, and the retry count
    from the retry decisions already present in the run's own execution
    history, which append_event has loaded to number the entry it is
    appending. Everything else comes off the entry, and a field the entry does
    not carry is omitted rather than written null — the rule the run's own
    structured history already follows. A caller with no work item to name is
    held to that same rule: an empty story id is not set at all, so the field is
    absent rather than present and empty. A broad-mode inspection is such a
    caller — it is not made by a run and has no story to name — and an empty
    string there would be a value every reader of the log would have to know to
    discount.

    The declaration's `required` list is the second half of the selection. A
    record that would lack a field the log requires is not written there at
    all, because a line missing what every line must carry is a line every
    reader has to know to discount. That is what lets one kind be named by two
    logs recording different facts about it: the duration log names the
    completion kinds of every timed operation and requires the duration, so an
    occurrence of such a kind that was not timed — a revert check decided by its
    nomination selector, an inspection that could not run — reaches the log
    that records what happened and not the one that records how long it took.
    No kind is named here; the required list is read off the declaration.
    """
    kinds = declaration["properties"][HISTORY_EVENT_PROPERTY]["enum"]
    kind = entry.get("event")
    if kind not in kinds:
        return None
    values = dict(entry)
    if story_id:
        values["story_id"] = story_id
    values["status"] = kind.removeprefix("story-")
    values["retry_count"] = sum(
        1 for recorded in history if recorded.get("retry_decision") == "retry"
    )
    record = {
        name: values[name]
        for name in declaration["properties"]
        if name != HISTORY_EVENT_PROPERTY and name in values
    }
    if any(name not in record for name in declaration.get("required", [])):
        return None
    return record


def append_history_records(
    history_directory: Path, entry: dict, story_id: str, history: list[dict]
) -> tuple[Path, ...]:
    """Project one entry into every log whose declaration names its kind.

    The history directory is explicit, so a caller outside a run — a process
    that observes something the harness records and has no run directory to
    resolve one from — appends a declared record through exactly this path
    rather than through a second way of writing a declared log. Nothing about
    which records reach which log lives here: the declaration decides it, and
    an entry whose kind appears in no log's enum writes nothing at all.

    Where a record goes within the directory is the writer-side resolver's
    answer, `harness_config.history_segment`: a record for a story is appended
    under that story's own segment, and one with no story under a segment
    named for the moment it is written. The files written are returned, so a
    caller staging what it wrote stages exactly that rather than re-deriving
    a moment it cannot know.

    Opened for appending and never for writing: a record already written is
    never rewritten by an append, and the single point at which a log is
    rewritten is `prune_history` below, invoked only by `l5-prune` on the
    default branch, which prints each file it dropped from. A run itself only
    reads and, at its end, reports what a set bound would drop.
    """
    segment: Path | None = None
    written: list[Path] = []
    for log, declaration in history_log_declarations().items():
        record = history_record(entry, history, story_id, declaration)
        if record is None:
            continue
        if segment is None:
            segment = harness_config.history_segment(history_directory, story_id)
            segment.mkdir(parents=True, exist_ok=True)
        with open(segment / log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        written.append(segment / log)
    return tuple(written)


def history_log_files(history_directory: Path, log: str) -> list[Path]:
    """Every file holding one declared log: the reader-side resolver.

    The root-level file of that name, if present — every record written
    before the segments existed, left exactly where it is — followed by each
    segment's file of that name in sorted segment order. Only files that exist
    are named, so a log nothing has written to yet resolves to nothing.
    """
    files: list[Path] = []
    if (history_directory / log).is_file():
        files.append(history_directory / log)
    if history_directory.is_dir():
        for segment in sorted(history_directory.iterdir()):
            if segment.is_dir() and (segment / log).is_file():
                files.append(segment / log)
    return files


def _append_history_records(run_dir: Path, entry: dict, history: list[dict]) -> None:
    """The projection a run takes, through the module-global directory.

    Unchanged in what it writes and where: it keeps its signature and its
    resolution of the directory, and is now a caller of the function above
    rather than a second copy of it.
    """
    if _history_dir is None:
        return
    append_history_records(_history_dir, entry, run_dir.name, history)


@dataclass(frozen=True)
class HistoryPrune:
    """What one prune of the whole store did: what stopped it, and what it
    dropped, keyed by the file's path relative to the history directory.
    """

    problems: list[str]
    dropped: dict[str, int]


def _record_is_older(record: object, cutoff: float) -> bool:
    """Whether a parsed record's own timestamp puts it before the bound.

    A record carrying no readable timestamp is *kept*. This store never
    discards what it cannot judge, and dropping a line because its timestamp
    could not be read would be the silent repair the prune exists to refuse.
    """
    stamp = record.get("timestamp") if isinstance(record, dict) else None
    if not isinstance(stamp, str):
        return False
    try:
        when = time.mktime(time.strptime(stamp, HISTORY_TIMESTAMP_FORMAT))
    except (TypeError, ValueError, OverflowError):
        return False
    return when < cutoff


@dataclass(frozen=True)
class HistoryReading:
    """What one read of the whole history found: what stopped it, what it
    counted past the bound, and the parsed lines the prune rewrites from.

    `past_bound` is keyed by the file's path relative to the history directory,
    exactly as `HistoryPrune.dropped` keys what the prune dropped, so the
    end-of-run note and the prune's announcement name a file the same way.
    `cutoff` is None where the bound is unset, and then `past_bound` is empty.
    """

    problems: list[str]
    past_bound: dict[str, int]
    cutoff: float | None
    files: dict[Path, list[tuple[str, object]]]


def read_history(directory: Path, config: dict) -> HistoryReading:
    """Read and judge every file of every declared log, rewriting nothing.

    This is the read that both a run's startup and the prune make, and it is
    the only reader of a log: the run calls it to refuse on a history it cannot
    parse and to count what a configured bound would drop, and `prune_history`
    calls it to know what to rewrite. One parser rather than two, so the run's
    refusal and the prune's refusal are one fact.

    It refuses rather than repairs. A line that is not valid JSON stops the
    read naming the file and the line number: a store that discards what it
    cannot parse and carries on is not append-only. Every file is read and
    parsed before anything is judged, so a caller that goes on to rewrite has
    parsed every file first. A `history_retention_days` that is not a
    non-negative number is refused the same way rather than ignored.

    An unset bound counts nothing, and the files are still read, because a
    corrupted record is worth refusing on whether or not this deployment
    prunes. A record whose timestamp cannot be read is not counted as past the
    bound, by the same judgement (`_record_is_older`) the prune drops by.
    """
    parsed: dict[Path, list[tuple[str, object]]] = {}
    for name in history_log_declarations():
        for path in history_log_files(directory, name):
            lines: list[tuple[str, object]] = []
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                try:
                    lines.append((line, json.loads(line)))
                except json.JSONDecodeError as error:
                    return HistoryReading(
                        [f"{path}, line {number}: is not valid JSON ({error.msg})"],
                        {}, None, {},
                    )
            parsed[path] = lines

    configured = config.get(HISTORY_RETENTION_KEY)
    if configured is None:
        return HistoryReading([], {}, None, parsed)
    try:
        days = float(configured)
    except (TypeError, ValueError):
        days = -1.0
    if days < 0 or days != days:  # NaN compares unequal to itself
        return HistoryReading(
            [
                f"{HISTORY_RETENTION_KEY} is {configured!r}, which is not a "
                f"non-negative number of days"
            ],
            {}, None, {},
        )

    cutoff = time.time() - days * 86400
    past_bound: dict[str, int] = {}
    for path, lines in parsed.items():
        older = sum(1 for _, record in lines if _record_is_older(record, cutoff))
        if older:
            past_bound[path.relative_to(directory).as_posix()] = older
    return HistoryReading([], past_bound, cutoff, parsed)


def prune_history(directory: Path, config: dict) -> HistoryPrune:
    """Bound the cross-run history by history_retention_days, or refuse.

    This is the only thing that rewrites a log, and it reads through the one
    read above, which is what confines the rewrite of a committed record to a
    single point and leaves the append path itself append-only. Since story-168
    that point is outside any run: a story run reads and reports, and the
    rewrite happens only through `l5-prune` on the repository's default branch,
    so two story branches never both rewrite the same file and their merge has
    nothing to reconcile.

    It refuses rather than repairs, on the read's terms: a line that is not
    valid JSON stops it naming the file and the line number, and every file is
    parsed *before* any is rewritten, so a malformed line in a later log cannot
    leave an earlier one already rewritten, and the malformed line and every
    other line are still there afterwards.

    An unset bound keeps everything: nothing is dropped and no log is
    rewritten. A log is every file the reader-side resolver names for it — the
    root-level file and each segment's — judged alike, and a record is dropped
    from whichever file holds it. What was dropped is keyed by the file
    relative to the history directory, so the announcement names the file that
    was rewritten.
    """
    reading = read_history(directory, config)
    if reading.problems:
        return HistoryPrune(list(reading.problems), {})
    if reading.cutoff is None:
        return HistoryPrune([], {})

    dropped: dict[str, int] = {}
    for path, lines in reading.files.items():
        kept = [line for line, record in lines
                if not _record_is_older(record, reading.cutoff)]
        if len(kept) == len(lines):
            continue
        dropped[path.relative_to(directory).as_posix()] = len(lines) - len(kept)
        path.write_text(
            "".join(f"{line}\n" for line in kept), encoding="utf-8"
        )
    return HistoryPrune([], dropped)


#: The command that rewrites the history, named in the end-of-run note and in
#: the completion report so a reader is told what to run rather than only that
#: something is past the bound.
PRUNE_COMMAND = "l5-prune"


def history_past_bound_sentence(bound: object, counts: dict[str, int]) -> str:
    """The one sentence a run reports a history past the bound with.

    Composed once so the end-of-run note and the completion report's Next Step
    say the same thing: the bound and its value, each file relative to the
    history directory with the count of records past the bound in it, and the
    command to run. Repeated by every run until the prune happens, because a
    command nobody is reminded to run never runs.
    """
    named = ", ".join(
        f"{name}: {count}" for name, count in sorted(counts.items())
    )
    return (
        f"the cross-run history holds records past "
        f"{HISTORY_RETENTION_KEY}={bound} ({named}); run {PRUNE_COMMAND} on "
        f"the default branch to prune them"
    )


def _history_past_bound_report() -> str:
    """The sentence this run owes at its end, or "" when it owes none."""
    if not _history_past_bound:
        return ""
    return history_past_bound_sentence(_history_retention_bound, _history_past_bound)


def _report_history_past_bound(run_dir: Path) -> None:
    """Append the end-of-run note, when this run counted anything past the
    bound; nothing otherwise, so a deployment leaving the bound unset appends
    no event here. Called by each terminal path before the commit it leaves —
    completion and escalation, and not a pause, which is not an end.
    """
    sentence = _history_past_bound_report()
    if sentence:
        # A note, which is append_event's default kind: one kind is spelled
        # in one function, and `note` is already run_story's to spell.
        append_event(run_dir, sentence)


def _refuse_bad_history(
    directory: Path, problems: list[str], *, then: str = "run the story again"
) -> int:
    """Refuse a run — or a prune — whose cross-run history could not be read.

    `then` is what the caller asks the person to do once the line is repaired:
    a run says to run the story again, and l5-prune says to run it again.
    """
    return refuse(
        f"The cross-run history under {directory} could not be read:",
        problems,
        "Nothing was discarded, rewritten or dropped around: a store that "
        "repairs what it cannot parse is not append-only. Repair the named "
        f"line by hand, then {then}.",
    )


def prune_store(target_root: Path) -> int:
    """The orchestration side of l5-prune: rewrite the history on the default
    branch, and commit what was rewritten.

    The prune is store-wide maintenance and belongs on the base branch outside
    any run: a story run reads and reports and rewrites nothing, because two
    story branches that both rewrote the same file would conflict on merge in
    exactly the shape per-writer segments removed for the append. So this
    refuses when the tree is not standing on the repository's default branch —
    resolved the way the run's own --base check resolves it — refuses through
    the same bad-history refusal a run makes on a malformed line or an unusable
    bound, says so and exits 0 when the bound is unset or nothing is past it,
    and otherwise prunes, prints each rewritten file with the count dropped
    from it, and commits exactly the rewritten files with a message naming the
    bound. It does not push.
    """
    config = harness_config.load_config(target_root)
    default = resolve_base(target_root, config, None)
    standing = worktrees.standing_branch(target_root)
    if standing != default:
        where = f"branch {standing}" if standing else "a detached HEAD"
        return refuse(
            f"{PRUNE_COMMAND} rewrites the cross-run history and belongs on "
            f"the repository's default branch:",
            [f"this tree is standing on {where}, and the default branch is "
             f"{default}"],
            "Nothing was rewritten and nothing was committed. Check out "
            f"{default} and run {PRUNE_COMMAND} again.",
        )

    directory = harness_config.history_dir(target_root, config)
    pruned = prune_history(directory, config)
    if pruned.problems:
        return _refuse_bad_history(
            directory, pruned.problems, then=f"run {PRUNE_COMMAND} again")
    configured = config.get(HISTORY_RETENTION_KEY)
    if configured is None:
        print(f"{HISTORY_RETENTION_KEY} is unset; nothing to prune.")
        return 0
    if not pruned.dropped:
        print(f"Nothing in the cross-run history is past "
              f"{HISTORY_RETENTION_KEY}={configured}; nothing to prune.")
        return 0

    for name, count in sorted(pruned.dropped.items()):
        print(f"{name}: dropped {count} record(s)")
    relative = directory.relative_to(target_root)
    paths = [str(relative / name) for name in sorted(pruned.dropped)]
    added = _git(target_root, "add", "--", *paths)
    if added.returncode != 0:
        print(f"The rewritten files could not be staged: "
              f"{added.stderr.strip()}", file=sys.stderr)
        return 1
    committed = _git(
        target_root, "commit", "-m",
        f"Prune the cross-run history to {HISTORY_RETENTION_KEY}={configured}",
        "--", *paths,
    )
    if committed.returncode != 0:
        print(f"The rewritten files could not be committed: "
              f"{committed.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Committed the rewritten files on {default}; not pushed.")
    return 0


def append_event(
    run_dir: Path,
    message: str,
    *,
    kind: str = "note",
    stage: str | None = None,
    artifacts: list[str] | None = None,
    duration_seconds: float | None = None,
    verifier_outcome: str | None = None,
    retry_decision: str | None = None,
    retry_reason: str | None = None,
    retry_category: str | None = None,
    retry_stage: str | None = None,
    findings: int | None = None,
    filed: int | None = None,
    dropped: int | None = None,
    mode: str | None = None,
    cost_usd: float | None = None,
    scope_files: int | None = None,
    invocations: int | None = None,
    dedupe_ran: bool | None = None,
    routed: int | None = None,
    acted_on: int | None = None,
    operation: str | None = None,
) -> None:
    """Append one event in both renderings, from one call.

    events.log stays exactly what it was — one `[timestamp] message` line,
    written from the prose message alone — because it is the format l5-status
    reads and the appendix documents. The structured keyword fields feed
    execution-history.json, the same events rendered for a reader that wants
    to route a query rather than read a stream. One write path is the point:
    a second one, however correct, is drift waiting to happen.

    A third output rides on the same call and is not a second write path: the
    entry this builds is projected into every cross-run log whose declaration
    names its kind, so a record outlives the run directory it describes. What
    reaches which log is read from schemas/cross-run-history.schema.json; no
    condition here decides it, and events.log and execution-history.json keep
    exactly the format and content they have.

    History is evidence, never state. Nothing here is read back to make a
    routing decision; state.json remains the coordinator's only routing
    source.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(run_dir / "events.log", "a", encoding="utf-8") as log:
        log.write(f"[{stamp}] {message}\n")

    history = load_history(run_dir)
    entry: dict = {
        "sequence": len(history) + 1,
        "timestamp": stamp,
        "event": kind,
        "message": message,
    }
    optional = {
        "stage": stage,
        "artifacts": artifacts,
        "duration_seconds": duration_seconds,
        "verifier_outcome": verifier_outcome,
        "retry_decision": retry_decision,
        "retry_reason": retry_reason,
        "retry_category": retry_category,
        "retry_stage": retry_stage,
        # An inspection's three counts, flat keyword fields in the idiom
        # verifier_outcome and retry_decision already use: absent on every
        # entry that is not one, and present on one that is even where the
        # count is zero, because zero findings is an answer.
        "findings": findings,
        "filed": filed,
        "dropped": dropped,
        # What that inspection was and what it cost, on exactly the terms the
        # three counts above are on: omitted when absent, and absent is how an
        # invocation that reported no cost is recorded — a zero there would be
        # indistinguishable from an inspection that genuinely cost nothing.
        "mode": mode,
        "cost_usd": cost_usd,
        "scope_files": scope_files,
        "invocations": invocations,
        # Whether that inspection's filed query answered. Written on every
        # inspection entry including one where it did, rather than only where it
        # did not, because a boolean has a false to say and absence would then
        # mean either that or an entry written before this field existed — the
        # treatment `discarded_session_block` already gets one log over. The
        # `is not None` filter below is what lets a false through.
        "dedupe_ran": dedupe_ran,
        # The accounting after a verdict: how many findings the pre-stage
        # inspection routed to the story, and how many of those the verdict
        # acted on. On the same omitted-when-None terms as the counts above;
        # present on an accounting entry even where a count is zero, because
        # none acted on is an answer.
        "routed": routed,
        "acted_on": acted_on,
        # Which timed operation the entry completes, beside its
        # duration_seconds: the stage's own name for a stage, a fixed name for
        # a check or the inspection. It is the key the cross-run duration log
        # records an observation under and an announcement reads an estimate
        # under, so the two are one string. Absent on every entry that
        # completes nothing timed.
        "operation": operation,
    }
    entry.update({key: value for key, value in optional.items() if value is not None})
    history.append(entry)
    _history_path(run_dir).write_text(
        json.dumps(history, indent=2) + "\n", encoding="utf-8"
    )
    _append_history_records(run_dir, entry, history)

    print(f"[{stamp}] {message}")


def _git(target_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(target_root), *args],
        capture_output=True,
        text=True,
    )


def _revision(root: Path, revision: str = "HEAD") -> str:
    """The revision `root` is at, or "" when that cannot be established.

    Empty is the honest answer for a directory that is not a git repository,
    and every reader treats it as not-established rather than as a value to
    compare — which is what keeps the resume guard from refusing on evidence
    it does not have.
    """
    result = _git(root, "rev-parse", revision)
    return result.stdout.strip() if result.returncode == 0 else ""


def same_repository(target_root: Path, harness_root: Path) -> bool:
    """Whether both roots are one git repository.

    Decided from `git rev-parse --show-toplevel` in each root: shared only when
    both invocations succeed and report the same path. A failing invocation on
    either side is the not-established answer and returns False, which is the
    same one-directional bias `_revision`, `dirty_paths`, `completion_commits`
    and `base_problems` already take — a root that is not a git repository
    reports not-shared rather than a false shared.
    """
    target = _git(target_root, "rev-parse", "--show-toplevel")
    harness = _git(harness_root, "rev-parse", "--show-toplevel")
    if target.returncode != 0 or harness.returncode != 0:
        return False
    return target.stdout.strip() == harness.stdout.strip()


def dirty_paths(target_root: Path) -> list[str]:
    """The paths `git status --porcelain` reports as uncommitted, sorted.

    This is the whole of the clean-tree pre-flight's evidence, and it only
    *reads* the target repository: no commit, no branch, no stash, no index
    change. A root that is not a git repository, or a git that fails for any
    other reason, reports nothing dirty — the run is refused for what can be
    established, never for what cannot, which is the same bias the resume
    guard takes.

    Untracked files count, because a file no stage produced is exactly what
    `git add -A` would absorb into the run's commit. Ignored files do not,
    which is why a gitignored run directory is not what this is about.
    """
    result = _git(target_root, "status", "--porcelain")
    if result.returncode != 0:
        return []
    paths = []
    for line in result.stdout.splitlines():
        entry = line[3:]
        # A rename is reported as "old -> new"; the new path is the one a
        # developer would look for, and the old one no longer exists.
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        if entry:
            paths.append(entry)
    return sorted(set(paths))


def story_branch(config: dict, story_id: str) -> str:
    """The branch a story's run works on, derived in one place.

    The prefix comes from the target repository's config; no branch name is
    written into orchestration. It exists because two callers now need the
    name — the fresh run's RunState, and the pre-flight that asks whether that
    branch already holds the story's finished work — and two derivations would
    be one fact in two places.

    This promise used to read "no branch name and no default base branch is
    written into orchestration", and story-030 revised it rather than leaving
    it standing beside code that contradicts it: `resolve_base` below ends in
    the literal "main" when a repository states no base and publishes no
    origin/HEAD to read one from. The revision is deliberate — a fallback that
    is *only* documented is a fallback nobody has exercised — and the literal
    is confined to that one line, which is the half of the sentence that still
    holds and is the half worth keeping true.
    """
    return config.get("branch_prefix", "story/") + story_id


def resolve_base(target_root: Path, config: dict, base: str | None) -> str:
    """The branch a story's branch is cut from, settled in one place.

    Four steps, first answer winning: the `base` argument when the developer
    declared one, the target repository's optional `base_branch` config key,
    the branch `refs/remotes/origin/HEAD` names, and the literal "main". The
    normal case is the third — a developer standing on the repository's own
    default branch needs no flag and no configuration — and the fourth exists
    only so a repository that publishes no origin/HEAD still has an answer.

    Both entry points read this; the base is derived here and nowhere else, so
    `l5-run` and `l5-plan` cannot disagree about what a story branches from.
    """
    if base is not None:
        return base
    configured = config.get("base_branch")
    if configured:
        return configured
    # A ref that is unset returns non-zero, which is the same as having no
    # answer here: fall through rather than reporting a failure.
    result = _git(target_root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if result.returncode == 0:
        prefix = "refs/remotes/origin/"
        ref = result.stdout.strip()
        if ref.startswith(prefix) and ref[len(prefix):]:
            return ref[len(prefix):]
    return "main"


def _base_remote(target_root: Path, base: str) -> str:
    """The remote the base is pushed to, or "" when there is none to name.

    Git's own record first — `branch.<base>.remote`, which a branch that tracks
    something has stated — and the repository's remotes otherwise, preferring
    `origin` and taking the first one named when there is no `origin`. A
    repository with no remote has nothing to name and answers "".

    It is a function rather than two copies because two readers need it: the
    tracking ref below, and the refresh that brings that ref up to date. Two
    derivations would be two answers to which remote the base belongs to.
    """
    configured = _git(target_root, "config", f"branch.{base}.remote")
    remote = configured.stdout.strip() if configured.returncode == 0 else ""
    if remote:
        return remote
    remotes = _git(target_root, "remote")
    if remotes.returncode != 0 or not remotes.stdout.split():
        return ""
    names = remotes.stdout.split()
    return "origin" if "origin" in names else names[0]


#: The prefix that runs the refresh's fetch with git's terminal prompting
#: disabled. `GIT_TERMINAL_PROMPT=0` is the only thing git reads for this --
#: there is no configuration equivalent -- so it has to reach the child's
#: environment, and it reaches it through `env` rather than through a copy of
#: this process's own. That is not a detour around the standing scan in
#: `tests/test_foreign_work_refusal.py` but the thing that scan is about: this
#: module reads no environment at all, so no flag or variable anywhere can tell
#: a check here to stand down, and setting one variable for one child does not
#: need a read to do it. A platform with no `env` on its path raises `OSError`
#: at the spawn, which the caller reports as a refresh it could not make.
NO_TERMINAL_PROMPT = ("env", "GIT_TERMINAL_PROMPT=0")

#: The bound in seconds on every fetch this module spawns, and deliberately one
#: bound rather than one per fetch: the refresh below and `fetch_story_branch`
#: are the same act against the same remote, so a second constant would be a
#: second answer that can disagree with this one. It is a real duration rather
#: than zero, because zero here would mean no bound at all -- which is the
#: failure a bound exists to prevent -- and twenty seconds is chosen because
#: every fetch here is one ref into one ref, which is fast on any healthy
#: remote: twenty seconds reads as slow rather than as hung.
#:
#: It is a constant in harness source rather than a configured key, unlike
#: `sync_timeout_seconds` and `filed_query_timeout_seconds`, which bound
#: commands a *target* supplies and whose cost is therefore the target's to
#: state. Nothing about a pre-flight fetch of one ref is a target's choice, and
#: a configured key is permanent surface for a knob nobody is expected to turn.
#: Adding the key later, if a real remote proves this wrong, is a small change.
FETCH_TIMEOUT_SECONDS = 20


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the process group the fetch leads, not merely the fetch.

    The fetch is spawned in its own session, so a kill delivered to its group
    reaches the children it spawned -- the ssh the fetch runs for an ssh
    remote, above all. Killing the process alone would leave that child running
    past the bound, which is the whole reason the session is new.

    Three near-identical copies of this live in `orchestration/filed_query.py`,
    `orchestration/command_transport.py` and `orchestration/item_update.py`.
    That is known; extracting them into one home is a separate story and not
    this one, and a fourth copy here is cheaper than widening this story's
    scope to three modules it otherwise does not touch.
    """
    # `getpgid` and `killpg` are imported by name rather than reached through
    # `os`, so this module still names no environment reader at all -- see
    # NO_TERMINAL_PROMPT above for what that property is for. A process-group
    # kill reads no environment; being able to see at a glance that nothing
    # here does is worth the import.
    from os import getpgid, killpg
    from signal import SIGKILL

    try:
        killpg(getpgid(process.pid), SIGKILL)
    except OSError:
        # The group is already gone, or this platform will not answer for it.
        # Either way the process itself must not be left behind.
        try:
            process.kill()
        except OSError:
            pass


@dataclass(frozen=True)
class _FetchOutcome:
    """What a bounded fetch did, in a shape both fetch sites can consume.

    The three ways a fetch can fail to succeed are reported distinctly, because
    they are three different things to say to a developer: `spawn_problem` is a
    fetch the platform would not start at all, `timed_out` is one killed at the
    bound, and a non-zero `returncode` is a remote that answered with an error.
    `returncode` is None for the first two, since neither produced one.
    """

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    spawn_problem: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


def _bounded_fetch(argv: list[str]) -> _FetchOutcome:
    """Spawn a git fetch, wait on it under FETCH_TIMEOUT_SECONDS, report.

    It raises on nothing, so a caller can be total: a platform that refuses the
    spawn, a fetch that exits non-zero and a fetch that never comes back all
    come back as an outcome. On expiry the process group is killed and the
    process reaped, so no child of the fetch outlives the bound and no zombie
    is left behind.

    The bound is what `refresh_base`'s docstring promises and what
    `fetch_story_branch` needs for the same reason: the ways a fetch stalls
    rather than answering -- a host that drops packets instead of refusing
    them, an unknown ssh host key, a passphrase with no agent -- are not
    reached by `GIT_TERMINAL_PROMPT`, because the last two ask on `/dev/tty`.

    What is bounded is the fetch *answering*, and that is why the output goes
    to files rather than to pipes. `communicate` waits for the pipes to reach
    end of file, which is a different event: a fetch that exited zero while
    something it spawned still holds the inherited descriptors -- git's own
    background maintenance is the obvious candidate -- would be killed and
    reported as a fetch that never answered, which is exactly backwards. It
    would also make the reap after the kill unbounded, since a descendant that
    escaped the group by starting a session of its own holds those pipes open
    for as long as it lives. Waiting on the process itself has neither
    property, and the reap is bounded by the same constant so the kill and its
    aftermath cannot between them outlast twice the bound.
    """
    with tempfile.TemporaryDirectory(prefix="l5-fetch-") as scratch:
        out_path = Path(scratch) / "stdout"
        err_path = Path(scratch) / "stderr"
        try:
            with open(out_path, "w") as out, open(err_path, "w") as err:
                process = subprocess.Popen(
                    argv,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
        except OSError as problem:
            return _FetchOutcome(spawn_problem=" ".join(str(problem).split()))
        timed_out = False
        try:
            process.wait(timeout=FETCH_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process)
            # Reaped after the kill so the fetch does not linger as a zombie,
            # and reaped under a bound because a reap that could wait forever
            # would give back the very guarantee the kill just established.
            try:
                process.wait(timeout=FETCH_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        # What the fetch managed to say before it was killed is kept, though a
        # stalled fetch has usually said nothing.
        stdout = _read_fetch_output(out_path)
        stderr = _read_fetch_output(err_path)
    if timed_out:
        return _FetchOutcome(stdout=stdout, stderr=stderr, timed_out=True)
    return _FetchOutcome(
        returncode=process.returncode, stdout=stdout, stderr=stderr
    )


def _read_fetch_output(path: Path) -> str:
    """What a bounded fetch wrote to one of its output files.

    A file that was never written, or that carries bytes this platform cannot
    decode, reports nothing rather than raising: `_bounded_fetch` raises on
    nothing, and what a fetch said is a detail in a sentence rather than
    something anything decides from.
    """
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _fetch_detail(fetched: _FetchOutcome) -> str:
    """How this module words each way a fetch failed to succeed.

    One wording for both fetches this module spawns, so the refresh and
    `fetch_story_branch` cannot describe the same outcome two ways: a spawn the
    platform refused carries what the platform said, an expiry names the bound
    it was killed at, and a non-zero exit carries what git wrote, or the exit
    status when git wrote nothing.

    It is only asked of an outcome that did not succeed; a successful fetch has
    no detail to give and its callers do not ask.
    """
    if fetched.spawn_problem:
        return fetched.spawn_problem
    if fetched.timed_out:
        return (
            f"the fetch did not answer within {FETCH_TIMEOUT_SECONDS}s and was "
            "killed at that bound"
        )
    said = fetched.stderr.strip() or fetched.stdout.strip()
    if said:
        return " ".join(said.split())
    return f"git fetch exited {fetched.returncode}"


def refresh_base(target_root: Path, base: str) -> str:
    """Bring the base's remote-tracking ref up to date, reporting why not.

    The empty string means the check below may go ahead on current
    information: either the ref was refreshed, or there was nothing to refresh
    — no remote, no remote-tracking counterpart, or a counterpart that is not a
    remote-tracking ref at all. A single sentence means a refresh was attempted
    and could not be made, and it is the caller's to print and to record.

    This exists because `base_problems`' second leg compares the base against
    its *local* remote-tracking ref, and nothing refreshed that ref first: a
    checkout that has not fetched since the base moved has the two in
    agreement, both behind, and the check passes cleanly while the branch is
    cut from a tree the shared base left behind. Refreshing first is the whole
    of what this changes; what the check *decides* is untouched, and nothing
    here rebases, pulls or moves anything on the developer's behalf.

    What is fetched is one ref into one ref, named explicitly rather than left
    to the remote's configured refspec, so a refresh cannot touch a ref the
    check is not about — and nothing else: not the working tree, not the index,
    not HEAD. It runs under `NO_TERMINAL_PROMPT` above, so a checkout with no
    credentials reports a reason rather than hanging a pre-flight on a password
    prompt.

    The fetch is also bounded at `FETCH_TIMEOUT_SECONDS`, and the process group
    it leads is killed at that bound. Prompt suppression does not reach every
    way a remote can decline to answer: a TCP connection to a host that drops
    packets rather than refusing them simply waits, and an unknown ssh host key
    or a passphrase with no agent asks on `/dev/tty`, which
    `GIT_TERMINAL_PROMPT` does not govern. So the bound is what makes this
    promise cover a remote that answers with *nothing* as well as one that
    answers with an error.

    A refresh that cannot be made is reported and never refused. This is
    network work in a pre-flight, and this harness's standing bias is that
    reporting may not become the failure: offline, an unreachable remote,
    absent credentials, and a remote that never comes back all leave the check
    exactly where it was, answering from what is known and saying that is what
    it did. A fetch killed at the bound is reported by the same one sentence a
    fetch that failed is reported by, with the bound named in its detail.
    """
    tracking = _base_tracking_ref(target_root, base)
    if tracking is None or not tracking.startswith("refs/remotes/"):
        return ""
    remote = _base_remote(target_root, base)
    if not remote:
        return ""
    merge = _git(target_root, "config", f"branch.{base}.merge")
    remote_branch = merge.stdout.strip() if merge.returncode == 0 else ""
    if not remote_branch:
        remote_branch = f"refs/heads/{base}"
    fetched = _bounded_fetch(
        [
            *NO_TERMINAL_PROMPT,
            "git",
            "-C",
            str(target_root),
            "fetch",
            "--quiet",
            remote,
            f"+{remote_branch}:{tracking}",
        ]
    )
    if fetched.succeeded:
        return ""
    detail = _fetch_detail(fetched)
    short = tracking.removeprefix("refs/remotes/")
    return (
        f"the base {base} could not be refreshed from {remote} ({detail}), so "
        f"the base check answered from {short} as the last fetch left it"
    )


def _base_tracking_ref(target_root: Path, base: str) -> str | None:
    """The base's remote-tracking counterpart, or None when there is none.

    The configured upstream first, because a branch that states one has stated
    the answer; the base's own name under the resolved remote otherwise, which
    is what a branch pushed without tracking configuration has. Anything that
    does not resolve is no counterpart at all, and the caller then says
    nothing rather than something false.
    """
    upstream = _git(
        target_root, "rev-parse", "--verify", "--symbolic-full-name", f"{base}@{{upstream}}"
    )
    if upstream.returncode == 0 and upstream.stdout.strip():
        return upstream.stdout.strip()
    remote = _base_remote(target_root, base)
    if not remote:
        return None
    candidate = f"refs/remotes/{remote}/{base}"
    if _git(target_root, "rev-parse", "--verify", candidate).returncode != 0:
        return None
    return candidate


def base_problems(
    target_root: Path, base: str, declared: bool, *, from_head: bool = True
) -> list[str]:
    """What refuses a run or a plan that would cut a branch from `base`.

    The empty list is the whole of "go ahead". A declared base is checked for
    one thing only — that the ref resolves — because stating a base is stating
    a deliberate departure: branching one story from another's branch is the
    case it exists for, so neither leg below applies to it.

    Otherwise two legs, in this order, returning after the first that has
    something to say. Leg one is that HEAD is standing on the base, because a
    branch cut from HEAD is cut from what is checked out. Leg two is that the
    base matches its remote-tracking counterpart in *either* direction —
    behind, ahead or diverged — because a branch cut from a local base that is
    not the shared one is a branch nobody else can see the history of. Only the
    first is printed when both hold: the second is not actionable until the
    first is fixed, and two refusals for one act read as two problems.

    `from_head` says whether the caller cuts the branch from HEAD, and it is the
    whole of what leg one is about. A caller that cuts from the base *by name* —
    which since story-117 is every caller that creates a worktree — passes
    False, because where the developer happens to be standing decides nothing
    for it, and asking anyway would refuse a plan or a run for a fact about a
    tree neither is going to touch. It defaults to True so that every caller
    that cut from HEAD before this parameter existed is unchanged by it, and so
    that this stays one definition of the check with one refusal that prints
    it rather than a second copy with one leg removed. The ref-resolves
    question and leg two are asked wherever a branch is cut.

    It carries the one-directional bias `unchanged_since_escalation` and
    `dirty_paths` already take: a root that is not a git repository, a base
    with no remote-tracking counterpart, a repository with no remote and any
    git invocation that fails all report *no* problem rather than a false one.
    A detached HEAD is the exception, and it is not an inconsistency — it is
    establishably not on the base, so it refuses like any other branch would.
    """
    if _git(target_root, "rev-parse", "--git-dir").returncode != 0:
        return []
    if _git(target_root, "rev-parse", "--verify", f"{base}^{{commit}}").returncode != 0:
        # A declared base that does not resolve is the developer naming
        # something that is not there. An undeclared one that does not resolve
        # is the harness having guessed, which establishes nothing.
        if declared:
            return [f"the base {base} does not resolve to a commit in {target_root}"]
        return []
    if declared:
        return []

    if from_head:
        head = _git(target_root, "rev-parse", "--abbrev-ref", "HEAD")
        if head.returncode == 0 and head.stdout.strip():
            current = head.stdout.strip()
            if current != base:
                where = "a detached HEAD" if current == "HEAD" else f"branch {current}"
                return [
                    f"HEAD is on {where}, not on the base {base}, so a new story "
                    f"branch would be cut from there instead"
                ]

    tracking = _base_tracking_ref(target_root, base)
    if tracking is None:
        return []
    counts = _git(target_root, "rev-list", "--left-right", "--count", f"{tracking}...{base}")
    if counts.returncode != 0:
        return []
    fields = counts.stdout.split()
    if len(fields) != 2:
        return []
    try:
        behind, ahead = int(fields[0]), int(fields[1])
    except ValueError:
        return []
    if not behind and not ahead:
        return []
    short = tracking.removeprefix("refs/remotes/")
    differences = []
    if ahead:
        differences.append(f"{ahead} commit(s) ahead of")
    if behind:
        differences.append(f"{behind} commit(s) behind")
    return [
        f"the base {base} is {' and '.join(differences)} {short}, so a new "
        f"story branch would be cut from a base that is not the shared one"
    ]


def branch_behind(target_root: Path, branch: str, base: str) -> int | None:
    """How many commits of `base` are not reachable from `branch`.

    None when that cannot be established, on the same bias as everything else
    reading the target repository here. It feeds a note on an existing story
    branch and no refusal: an existing branch is reported, never refused,
    because resume must keep working whatever the base has done since.
    """
    result = _git(target_root, "rev-list", "--count", f"{branch}..{base}")
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _checkout_story_branch(
    target_root: Path, branch: str, start_point: str | None = None
) -> list[str]:
    """Stand the run on its branch, reporting what git refused rather than raising.

    Returns the problems git named, one per line of its own message, and the
    empty list when the checkout was made. That shape is the one
    `base_problems` and `prune_history`'s `problems` already use, so the caller
    refuses through the shared `refuse()` like every neighbouring pre-flight
    rather than letting a `RuntimeError` out of a run that has already begun.
    """
    exists = _git(target_root, "rev-parse", "--verify", branch).returncode == 0
    if exists:
        args = ["checkout", branch]
    else:
        # A declared base is branched from explicitly. With no declaration the
        # start point is HEAD, exactly as before, and the pre-flight above is
        # what establishes that HEAD is the base.
        args = ["checkout", "-b", branch] + ([start_point] if start_point else [])
    result = _git(target_root, *args)
    if result.returncode == 0:
        return []
    # Git's own words, split into one problem per line so the refusal lists the
    # paths it named the way every other refusal lists what it found. A git
    # that said nothing still reports something, because a refusal with no
    # problems under it would say only that something went wrong.
    problems = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    return problems or ["git refused the checkout without saying why"]


@dataclass(frozen=True)
class RunRoot:
    """Where a run works, and how that was decided.

    `path` may not exist yet: resolving where a run works is a *decision* and
    creating the worktree is an *act*, and this repository keeps every refusal
    above every act. So the decision is made early — above the run directory,
    the state read and the dirty-tree check, all of which have to read the tree
    the run will actually work in — and `create` says whether the act is still
    owed when the run reaches the point its branch checkout used to sit at.
    """

    path: Path
    branch: str
    reason: str
    create: bool


def resolve_run_root(target_root: Path, config: dict, branch: str) -> RunRoot:
    """Which tree this run works in: the invoked one, an existing worktree, or a new one.

    Three answers, first winning. The invoked tree when it already stands on
    the story branch — which is what an accepted plan-time run offer is, since
    planning left the developer's session standing there, and there is nothing
    to create. An existing worktree for that branch when there is one, so a
    resume returns to the tree its earlier entry worked in and two runs of one
    story never stand in two trees on one branch. Otherwise a new worktree at
    the path `worktrees.worktree_path` derives, which is outside the repository
    and is created below, once every refusal has been made.

    The developer's own checkout is therefore left where it is unless it is
    already standing on the story branch: nothing checks it out, nothing
    commits to it, and its uncommitted work is neither read nor absorbed.
    """
    if worktrees.stands_on(target_root, branch):
        return RunRoot(target_root, branch, "the invoked tree stands on it", False)
    found = worktrees.find(target_root, branch)
    if found is not None:
        return RunRoot(found, branch, "an existing worktree stands on it", False)
    return RunRoot(
        worktrees.worktree_path(target_root, config, branch),
        branch,
        "a new worktree for it",
        True,
    )


def link_configured_build_state(
    source_root: Path, destination: Path, config: dict
) -> worktrees.BuildState:
    """Give `destination` the build state the target's configured commands name.

    The directories come from the commands the target already configures — the
    first word of `test_command`, and `verification_runner` — so no directory
    name is written into the harness and a target naming its environments
    differently needs no change. This is the whole of the configuration reading;
    `worktrees.interpreter_roots` decides which words have a root to link and
    `worktrees.link_build_state` does the linking, so the clean-clone path and
    this one share both.

    Reported, never raised, and no caller refuses on the result: a command word
    that is absolute, that is a bare name looked up on PATH, or whose root is
    absent from the source leaves nothing linked and every existing report for a
    command that could not be run exactly as it was.
    """
    command = config.get("test_command")
    words: list[str | None] = []
    if command:
        try:
            argv = shlex.split(command)
        except ValueError:
            # A command the harness cannot split is a command it cannot derive
            # a root from. Whatever else that breaks is the existing reports'
            # business; nothing new is refused here.
            argv = []
        if argv:
            words.append(argv[0])
    words.append(config.get("verification_runner"))
    return worktrees.link_build_state(
        source_root, destination, worktrees.interpreter_roots(words)
    )


def branch_exists(target_root: Path, branch: str) -> bool:
    """Whether `branch` resolves to a commit in this repository."""
    return _git(target_root, "rev-parse", "--verify", branch).returncode == 0


@dataclass(frozen=True)
class StoryBranchFetch:
    """Whether a story branch resolves here, and why this clone could not ask.

    `resolved` is what `fetch_story_branch` has always answered: whether the
    branch resolves in this clone once the fetch, if there was one, is done.

    `problem` follows `refresh_base`'s convention — the empty string means the
    caller may go ahead on what it knows, so a caller that would otherwise say
    the remote does not have the branch may say it. A non-empty problem is one
    sentence saying why this clone could not ask, and a caller that has one may
    not say anything about what the remote holds, because nothing established
    it.
    """

    resolved: bool
    problem: str = ""


def fetch_story_branch(
    target_root: Path, config: dict, branch: str
) -> StoryBranchFetch:
    """Bring a story branch that exists only on the remote into this clone.

    A plan is pushed from the worktree it was written in and that worktree is
    removed when its run offer is declined, so a story's artifact is routinely
    on its branch on the remote and in no tree here. Fetching it is what makes
    `l5-run <story-id>` work from a clone that has never seen the story.

    It reports whether the branch resolves afterwards *and* why it could not
    ask, in the shape `refresh_base` uses: a spawn the platform refused, a
    fetch killed at the `FETCH_TIMEOUT_SECONDS` bound together with the process
    group it leads — being bounded changes nothing about what this answers, a
    fetch killed at the bound leaving the branch unfetched — a non-zero
    exit carrying what git said, and a clone with no remote configured at all
    each come back as their own sentence. The problem is empty when the fetch
    succeeded, when the branch already resolved without a fetch, and when the
    fetch exited zero without producing the branch — the last because a remote
    that answered and did not have the branch is a remote that does not have
    it, which is what the caller's existing sentence correctly says.

    It runs under `NO_TERMINAL_PROMPT`, as its sibling does, so a checkout with
    no credentials reports a reason rather than blocking on a prompt whose
    output is redirected to a file the developer never sees.

    The one-directional bias still holds and is what the reported reason is
    for: this answers rather than raises, so a clone that could not ask answers
    that the branch does not resolve here — and now says why, so the caller
    refuses with something it established rather than with a statement about a
    remote nobody reached.
    """
    if branch_exists(target_root, branch):
        return StoryBranchFetch(resolved=True)
    remotes = _git(target_root, "remote")
    names = remotes.stdout.split() if remotes.returncode == 0 else []
    if not names:
        # Not silence: a clone with no remote is one that could not ask, and a
        # caller told nothing here would say the branch is absent from a remote
        # that does not exist.
        return StoryBranchFetch(
            resolved=False,
            problem=(
                f"there is no remote configured in {target_root} to ask for "
                f"branch {branch}"
            ),
        )
    remote = "origin" if "origin" in names else names[0]
    fetched = _bounded_fetch(
        [
            *NO_TERMINAL_PROMPT,
            "git",
            "-C",
            str(target_root),
            "fetch",
            remote,
            f"{branch}:{branch}",
        ]
    )
    problem = ""
    if not fetched.succeeded:
        problem = (
            f"branch {branch} could not be fetched from {remote} "
            f"({_fetch_detail(fetched)})"
        )
    return StoryBranchFetch(
        resolved=branch_exists(target_root, branch), problem=problem
    )


def story_on_branch(target_root: Path, branch: str, relative: str) -> str | None:
    """The story artifact as `branch` carries it, or None when it carries none."""
    result = _git(target_root, "show", f"{branch}:{relative}")
    if result.returncode != 0:
        return None
    return result.stdout


def is_blocked(path: str, blocked: list[str]) -> bool:
    """Whether a repository path falls under a repository-wide blocked prefix.

    The one reading of what a blocked prefix matches, so the check that
    escalates on a blocked path and the completeness check that subtracts them
    before reporting an omission cannot disagree about which paths are the
    coordinator's own to write.
    """
    return any(path.startswith(prefix) for prefix in blocked)


def _blocked_violation(run_dir: Path, record_name: str, blocked: list[str]) -> str | None:
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    for group in ("modified", "created", "deleted"):
        for path in changed.get(group, []):
            if is_blocked(path, blocked):
                return path
    return None


@dataclass(frozen=True)
class OwnershipViolation:
    """A path a stage created that one of its own restrictions governs.

    The restriction travels with the path rather than only its prefix, so the
    escalation states the rule in the workflow's own terms and a confinement
    does not have to be reported under a creation label.
    """

    path: str
    restriction: StageRestriction

    @property
    def prefix(self) -> str:
        """The prefix the restriction is named for."""
        return self.restriction.prefix


def _ownership_violation(
    run_dir: Path,
    record_name: str,
    restrictions: list[StageRestriction],
    granted: list[str],
) -> OwnershipViolation | None:
    """Hold a stage to the outputs it declared it does not own.

    Only the created array is checked. The line falls between creating and
    modifying because the rule is about independence, not directories: an
    implementer must be able to update an existing test whose call site its
    own signature change broke, but validation it authors itself checks what
    it built rather than what was asked.

    The stage's restrictions arrive whole; a path the story's grants cover is
    exempted rather than a restriction being dropped, so a grant naming one
    path leaves every other path the same restriction governs governed.
    Whether a grant covers a path is grant_covers's decision and no other's,
    and whether a restriction governs a path is the restriction's — so this
    check reads neither a declaration key nor a sense, and a creation outside a
    confinement escalates here for the reason a creation beneath a
    may_not_create prefix does.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    for path in changed.get("created", []):
        if grant_covers(granted, path):
            continue
        for restriction in restrictions:
            if restriction.governs(path):
                return OwnershipViolation(path, restriction)
    return None


def _schema_violation(run_dir: Path, stage: dict) -> str | None:
    """Check the stage's declared artifacts against their schemas.

    The artifact-to-schema mapping lives in the workflow definition, so no
    artifact or schema name is named here. An artifact the stage did not
    write is skipped: whether it is required is the outputs list's job, and
    a conditional artifact like retry-guidance.json is legitimately absent.
    """
    for artifact, schema_name in sorted(stage.get("schemas", {}).items()):
        path = run_dir / artifact
        if not path.is_file():
            continue
        try:
            instance = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            return f"{artifact} is not parseable as JSON: {error}"
        errors = schema_validator.validate(
            instance, schema_validator.load_schema(schema_name)
        )
        if errors:
            return (
                f"{artifact} does not match the {schema_name} schema: "
                + "; ".join(errors)
            )
    return None


def archivable_artifacts(stages: list[dict]) -> list[str]:
    """Collect every artifact name the loaded workflow declares under a stage.

    The union of four sources: each stage's outputs, its changed_files record,
    the keys of its schemas map, and the result artifact of every check
    declaration that stage carries. The first three are the places the
    coordinator already reads *stage* artifact names from; the fourth is where
    it reads the names of the records the *checks* write, which are declared
    nowhere else and so were never archived — a retry overwrote each in place
    and what the harness decided about a superseded attempt was unrecoverable.

    A check declaration is recognised by its shape rather than by its key: a
    value of the stage dict that is a mapping carrying a string `result`. That
    is what keeps this name-agnostic — no check name, no declaration key spelled
    as an artifact name and no stage name is written here, so a check declared
    later archives its result with no change to this function. Sorted, so the
    archive is deterministic.

    interrupted_attempt_artifacts is built on this, so a resume's archive
    carries the check results for the same reason a retry's does, with no
    second collection to keep in step.
    """
    names: set[str] = set()
    for stage in stages:
        names.update(stage.get("outputs", []))
        record = stage.get("changed_files")
        if record:
            names.add(record)
        names.update(stage.get("schemas", {}))
        for value in stage.values():
            if isinstance(value, dict) and isinstance(value.get("result"), str):
                names.add(value["result"])
    return sorted(names)


def attempt_dir(run_dir: Path, attempt: int) -> Path:
    """Where one attempt's superseded artifacts are kept.

    The one place the archive directory is named. Both readers derive it from
    here: the archive that writes it, and the resume that refuses when it
    already exists rather than writing over the evidence in it.
    """
    return run_dir / "attempts" / f"attempt-{attempt}"


def entry_dir(run_dir: Path, entry: int) -> Path:
    """Where one entry of the run keeps its counter-keyed artifacts.

    A run is re-entered rather than restarted, and re-entry K opens
    resumes/resume-K/ holding everything the entry it ends had written under a
    name keyed by a counter the re-entry resets. The one place that directory
    is named, in attempt_dir's shape and for attempt_dir's reason: the move
    that writes it and the refusal that guards it both derive it from here, so
    the two cannot disagree about where an entry's evidence went.
    """
    return run_dir / "resumes" / f"resume-{entry}"


def archive_attempt(run_dir: Path, artifacts: list[str], attempt: int) -> list[str]:
    """Copy the superseded attempt's artifacts into attempts/attempt-N/.

    Evidence is not silently overwritten. The live copies stay at the
    run-directory root under their canonical names, where every reader
    expects them; the archive keeps those same names one directory down.
    An artifact the attempt did not write is skipped, the way an absent
    conditional artifact is skipped by _schema_violation. Returns the names
    actually archived.

    A copied record that points at a companion file of its own brings that file
    with it. The pointer is followed off the record's own `output_path` field
    rather than off any filename, which is what keeps this name-agnostic: no
    artifact name and no naming convention is written here, so a record of a
    shape declared later is followed for free. The companion is copied into the
    archive under its basename and the pointer is rewritten *in the archived
    copy alone* — the live record and the live output file keep their contents
    and their canonical names — so reading an archived record leads to the
    output of the attempt that record belongs to rather than to whatever the
    next attempt wrote over it. A record that is not JSON, that is not an
    object, that carries no `output_path`, or whose `output_path` names nothing
    that exists inside the run directory, is copied exactly as before.
    """
    destination = attempt_dir(run_dir, attempt)
    destination.mkdir(parents=True, exist_ok=True)
    archived = []
    for artifact in artifacts:
        source = run_dir / artifact
        if not source.is_file():
            continue
        archived_copy = destination / artifact
        shutil.copy2(source, archived_copy)
        archived.append(artifact)
        companion = _archive_companion_output(run_dir, archived_copy, destination)
        if companion is not None:
            archived.append(companion)
    return archived


def _archive_companion_output(
    run_dir: Path, archived_copy: Path, destination: Path
) -> str | None:
    """Bring an archived record's own output file with it, and repoint the copy.

    Returns the name the companion was archived under, or None when the record
    points at nothing archivable. The pointed-at file must live inside the run
    directory: a record naming something outside it is naming a file this
    archive has no claim on, and is left pointing where it points.
    """
    try:
        record = json.loads(archived_copy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(record, dict):
        return None
    pointer = record.get("output_path")
    if not isinstance(pointer, str) or not pointer:
        return None
    output = Path(pointer)
    if not output.is_absolute():
        output = run_dir / output
    try:
        inside = output.resolve().is_relative_to(run_dir.resolve())
    except OSError:
        return None
    if not inside or not output.is_file():
        return None
    companion = destination / output.name
    shutil.copy2(output, companion)
    record["output_path"] = str(companion)
    archived_copy.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return output.name


def interrupted_attempt_artifacts(
    stages: list[dict], attempt: int, *, run_dir: Path | None = None
) -> list[str]:
    """What a resumed run would write over if the interrupted attempt stayed.

    The stage artifacts archive_attempt already derives from the workflow, plus
    that attempt's rendered prompts. The prompts are the addition a resume
    needs: a resumed run carries retry_count forward — it must, since resetting
    it would overwrite the escalated attempt's verification iteration — so it
    re-renders under the same attempt number and would write over the prompt
    the interrupted stage was actually given. No stage name and no artifact
    name is written here; both come off the loaded workflow.

    A resume also zeroes self_route_count, so a resumed stage's first mechanical
    failure is try 1 of that same attempt again and lands on exactly the names
    the interrupted attempt's own self-routes wrote. Those names cannot be
    derived from the workflow alone — the count that produced them is live state
    the resume has already discarded — so when the run directory is given they
    are read off it by self_route_artifacts. The retained suite-run pairs that
    attempt wrote are keyed by the same discarded count and are read off the
    directory beside them, by retained_suite_artifacts. The run directory is
    optional so a caller asking only what the workflow declares gets exactly
    what it got before.
    """
    names = archivable_artifacts(stages) + [
        prompt_file(stage["name"], attempt) for stage in stages
    ]
    if run_dir is not None:
        names += self_route_artifacts(run_dir, stages, attempt)
        names += retained_suite_artifacts(run_dir, stages, attempt)
    return names


def retained_suite_artifacts(
    run_dir: Path, stages: list[dict], attempt: int | str
) -> list[str]:
    """The retained suite-run pairs one attempt left behind.

    Read off the run directory for the reason self_route_artifacts is: the try
    number keying them is the self-route count, which is live state a resume has
    already zeroed, so nothing records how high it reached. The globs come from
    retained_suite_result_file and suite_output_file — the same functions that
    write the names, with the try wildcarded — so what is found cannot drift
    from what was written, and the artifact comes off the workflow's own
    suite-run declaration rather than from a literal. A workflow declaring no
    suite run has no such names, and this reports none.
    """
    declared = suite_run_declaration(stages).get("result")
    if not declared:
        return []
    names: list[str] = []
    for stage in stages:
        result = retained_suite_result_file(declared, stage["name"], attempt, "*")
        for pattern in (result, suite_output_file(result)):
            names.extend(sorted(path.name for path in run_dir.glob(pattern)))
    return names


def inspection_artifacts(
    run_dir: Path, stages: list[dict], attempt: int | str
) -> list[str]:
    """The pre-stage inspection records one attempt left behind.

    Keyed by the attempt, because an attempt is inspected once and a retry —
    which means the code changed — inspects again. A resume zeroes the retry
    count, so the next entry's attempt 1 would land on the ending entry's
    attempt 1 and, worse, would *find* it and skip inspecting: the once-per-
    attempt rule is decided by the file being there, so leaving it behind would
    hand a resumed run the previous entry's reading of a tree it no longer has.

    The glob comes from `story_inspection.findings_artifact_file` — the same
    function that writes the name — so what is found cannot drift from what was
    written, and the artifact comes off the workflow's own inspection
    declaration rather than from a literal. A workflow declaring none has no
    such names and this reports none.
    """
    declared = inspection_declaration(stages).get("result")
    if not declared:
        return []
    names: list[str] = []
    # The accounting artifact travels with the findings record it accounts
    # for, and for the same reason: its presence is what says an attempt was
    # accounted, so one left behind would have the next entry's attempt 1
    # account nothing for findings it routed. The held artifact travels with
    # both, because the filing at completion files the highest attempt that
    # holds one, and one left behind would have a resumed run file the
    # previous entry's reading of a tree it no longer has.
    for pattern in (
        story_inspection.findings_artifact_file(declared, attempt),
        story_inspection.accounting_artifact_file(declared, attempt),
        story_inspection.held_artifact_file(declared, attempt),
    ):
        names.extend(path.name for path in run_dir.glob(pattern))
    return sorted(names)


def entry_artifacts(run_dir: Path, stages: list[dict]) -> list[str]:
    """The run-root names a reset of the counters would re-land on.

    Everything at the run root whose name is keyed by retry_count or by
    verification_iterations: the attempt-numbered prompts, the try-suffixed
    prompts and self-route records those attempts wrote, the retained suite-run
    pairs those attempts wrote, the pre-stage inspection records those attempts
    wrote, the verification iteration files, and the
    attempts/ directory. Zeroing the counters puts the next attempt back on
    attempt 1 and iteration 1, so these are exactly the names an entry has to
    take with it.

    The prompt and self-route names come through prompt_file and
    self_route_result_file with the attempt and the try number wildcarded, the
    way self_route_artifacts already discovers what a discarded count wrote, so
    discovery cannot drift from writing; the stage names come off the loaded
    workflow, and the retained pairs through retained_suite_artifacts, which
    reads the artifact off the workflow's own suite-run declaration. The
    attempts directory is derived from attempt_dir for the same reason. Names
    are run-relative, so a move keeps each one's position beneath the run
    directory rather than flattening it.

    The record is not here and must not be: the run's retry record, its cost
    record, its two renderings of the event stream, its state file and its
    escalation summary account for the whole run, are never keyed by a
    counter, and stay at the root. Nor is any stage artifact a resumed stage
    reads as input — those keep canonical names a reset does not touch.
    """
    names: list[str] = []
    for stage in stages:
        for pattern in (
            prompt_file(stage["name"], "*"),
            prompt_file(stage["name"], "*", "*"),
            self_route_result_file(stage["name"], "*", "*"),
        ):
            names.extend(path.name for path in run_dir.glob(pattern))
    names.extend(retained_suite_artifacts(run_dir, stages, "*"))
    names.extend(inspection_artifacts(run_dir, stages, "*"))
    names.extend(
        str(path.relative_to(run_dir))
        for path in run_dir.glob("verification/iteration-*.json")
    )
    attempts = attempt_dir(run_dir, 1).parent
    if attempts.is_dir():
        names.append(attempts.name)
    return sorted(set(names))


def move_entry_artifacts(
    run_dir: Path, names: list[str], destination: Path
) -> list[str]:
    """Move one entry's counter-keyed artifacts under the entry directory.

    Each name keeps its position relative to the run directory, so
    verification/iteration-1.json lands at verification/iteration-1.json inside
    the destination rather than beside it. Nothing here parses, validates or
    repairs what it moves: a truncated file a dying stage left is moved exactly
    as it is, which is the same claim archive_attempt declines to make about
    what it copies. Returns the names actually moved.
    """
    moved = []
    for name in names:
        source = run_dir / name
        if not source.exists():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(name)
    return moved


def repoint_outstanding_failure(
    outstanding: dict, moved: list[str], opened: Path, run_dir: Path
) -> dict:
    """The outstanding-failure record, following the move a resume just made.

    The record names the failure's retained result and output — result_path
    run-relative, output_path absolute, as the suite-run check writes them —
    and every line composed from it afterwards names those paths: the
    carry-forward note, the note a clean-clone pass clears it with, and the
    end-of-run refusal. A resume moves the retained pair under the entry
    directory it opens, so a record left as it was would point at paths that
    no longer hold the files. This returns the record with both paths
    repointed to where the pair now stands, in the forms they already take,
    so what those lines read moves with the evidence and none of them has to
    change what it says.

    Pure over its inputs. The record is returned exactly as it was when it is
    empty or its result_path is not among the names the resume moved — a pair
    already under an earlier entry directory, or never retained, did not move
    in this resume, so a second resume does not repoint it twice. output_path
    is repointed only where it is non-empty and its run-relative form is among
    the moved names, and is left as it was otherwise. Nothing here matches the
    resumes/ spelling: the destination is `opened`, the entry directory the
    caller derived from entry_dir, which stays the one place that directory
    is named.

    This is a deliberate exception to the rule that a record is never
    rewritten and the log resolves a moved path, which is how the run's retry
    record is treated. That record is append-only evidence; this field is a
    live pointer the coordinator routes on and reads back into prose, and what
    it points at is the retained pair itself, which the resume already moves.
    The retry record and the resume's own archive note are untouched.
    """
    if not outstanding:
        return outstanding
    result_path = outstanding.get("result_path") or ""
    if result_path not in moved:
        return outstanding
    below = opened.relative_to(run_dir)
    repointed = dict(outstanding)
    repointed["result_path"] = str(below / result_path)
    output_path = outstanding.get("output_path") or ""
    if output_path:
        try:
            output_name = str(Path(output_path).relative_to(run_dir))
        except ValueError:
            output_name = ""
        if output_name in moved:
            repointed["output_path"] = str(opened / output_name)
    return repointed


def accumulated_attempts(run_dir: Path, state: RunState) -> int:
    """How many attempts this run has taken, across every entry of it.

    retry_count is entry-scoped — a resume restores the allowance by zeroing it
    — so the run's total cannot be read off the live counter and is composed
    from the record instead. One attempt opens each entry, and
    retry-history.json holds one entry per retry actually taken, on both sides
    of every resume. Records are never reset, which is what makes the addition
    sound.
    """
    return state.resume_count + 1 + len(load_retry_records(run_dir))


def conditional_artifacts(stage: dict) -> list[str]:
    """The artifacts a stage may write but is not required to.

    Read off the loaded workflow the way every other artifact name the
    coordinator handles is: a stage's schemas map names everything it may
    write, its outputs name what it must write, and its changed_files record
    is required too, so what remains is exactly the conditional set —
    the verifier's retry guidance, written only on a failing verdict. Naming
    it here instead would put an artifact name in orchestration code and give
    the harness a second place to change when the workflow changes.
    """
    required = set(stage.get("outputs", []))
    record = stage.get("changed_files")
    if record:
        required.add(record)
    return sorted(set(stage.get("schemas", {})) - required)


def required_artifacts(stage: dict) -> list[str]:
    """The artifacts a stage must write, read off the loaded workflow.

    The mirror of conditional_artifacts and read from the same places: a
    stage's outputs plus its changed_files record. No artifact name is
    written here, so a workflow declaring an output the shipped one does not
    is covered with no change to orchestration code. Sorted, for determinism.
    """
    names = set(stage.get("outputs", []))
    record = stage.get("changed_files")
    if record:
        names.add(record)
    return sorted(names)


def artifact_signatures(run_dir: Path, artifacts: list[str]) -> dict[str, tuple]:
    """What the named artifacts looked like at this moment, for comparison.

    A conditional artifact is not cleared between attempts: the retry guidance
    an attempt writes stays at the run-directory root, where the next
    implementer's context reads it. That makes "the file is present" a useless
    test of whether *this* attempt wrote it. Taking a signature before the
    stage runs and comparing after is the test that holds, and it names no
    artifact — the caller passes whichever the loaded workflow declares.

    An artifact that does not exist has no entry, so its later presence is
    itself a change.
    """
    signatures = {}
    for artifact in artifacts:
        path = run_dir / artifact
        if path.is_file():
            stat = path.stat()
            signatures[artifact] = (stat.st_mtime_ns, stat.st_size)
    return signatures


def artifacts_written_since(
    run_dir: Path, artifacts: list[str], before: dict[str, tuple]
) -> list[str]:
    """Which of the named artifacts were written after `before` was taken."""
    now = artifact_signatures(run_dir, artifacts)
    return [name for name, signature in now.items() if before.get(name) != signature]


def stale_artifacts(
    run_dir: Path, artifacts: list[str], before: dict[str, tuple]
) -> list[str]:
    """Which of the named artifacts are present but a previous attempt's.

    The reader that turns the pre-stage snapshot into a verdict about
    required outputs. An artifact present now and unchanged since `before`
    was taken was not written by the attempt that just ran. This adds no
    second comparison: it is the same snapshot and the same freshness test
    artifacts_written_since applies to conditional artifacts, read the other
    way round. Absent artifacts are not stale — they are missing, which is a
    distinct condition with its own reason.

    Detection only. Where a stale artifact routes is the caller's decision,
    so changing that destination is a routing change rather than a rewrite.
    """
    present = artifact_signatures(run_dir, artifacts)
    return sorted(
        name for name, signature in present.items() if before.get(name) == signature
    )


# --------------------------------------------------------------------------
# The pre-stage output baseline
#
# The snapshot above is taken in memory and the coordinator decides its own
# missing, stale and schema checks from it. A stage inside its own turn cannot
# reach it, so it finds out it left a required output unwritten only after the
# turn has ended and the coordinator re-enters it — a whole agent invocation
# spent writing one JSON file.
#
# So the same snapshot is written to the run directory, where a stage may read
# it. The written copy is a *report*, not a second source of truth: every
# check the coordinator makes still reads the in-memory dict it already holds,
# so a stage that rewrites this file changes what it is told about itself and
# nothing about what the coordinator decides.

OUTPUT_BASELINE = "output-baseline.json"


def output_baseline_file(run_dir: Path) -> Path:
    """Where the pre-stage snapshot is written for a stage to read."""
    return run_dir / OUTPUT_BASELINE


@dataclass(frozen=True)
class OutputBaseline:
    """What a stage's outputs looked like when its turn began."""

    stage: str
    signatures: dict[str, tuple]


def write_output_baseline(
    run_dir: Path, stage_name: str, signatures: dict[str, tuple]
) -> None:
    """Persist the snapshot the coordinator has just taken, for the stage to read."""
    output_baseline_file(run_dir).write_text(
        json.dumps(
            {
                "stage": stage_name,
                "signatures": {
                    name: list(signature)
                    for name, signature in sorted(signatures.items())
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def read_output_baseline(run_dir: Path) -> OutputBaseline | None:
    """The persisted snapshot, or None where there is none to read.

    The signatures come back in the tuple form artifact_signatures produces,
    because that is the form stale_artifacts compares against. JSON renders a
    tuple as a list, and a reader that returned lists would find every
    artifact changed, report no stale artifact at all, and tell a stage it was
    complete in precisely the case this exists to catch.

    An absent or unreadable file is None rather than an exception: the two
    readers of this — a command a stage may run on itself and a hook that runs
    at the end of its turn — each answer for that themselves, one by saying so
    and one by deciding nothing.
    """
    try:
        held = json.loads(
            output_baseline_file(run_dir).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict):
        return None
    signatures = held.get("signatures")
    if not isinstance(signatures, dict):
        return None
    return OutputBaseline(
        stage=str(held.get("stage") or ""),
        signatures={
            name: tuple(signature) for name, signature in signatures.items()
        },
    )


def stage_allowed_tools(
    *, allowed_tools: list[str] | None, harness_root: Path
) -> list[str]:
    """The target's grants, plus the grant for the stage's own output check.

    The check is invited in every stage's prompt, so it has to be runnable
    without a target repository editing its .harness/config.yaml or the
    template every new target is created from. It is appended rather than
    substituted: the configured grants arrive whole and unchanged, and this
    adds one.

    The module is imported here rather than at module scope because it imports
    this one.
    """
    import output_check

    return [*(allowed_tools or []), output_check.grant(harness_root)]


def _retry_record_file(run_dir: Path) -> Path:
    return run_dir / "retry-history.json"


def load_retry_records(run_dir: Path) -> list[dict]:
    """The retries recorded so far, empty before the first one is taken."""
    path = _retry_record_file(run_dir)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def append_retry_record(
    run_dir: Path,
    attempt: int,
    retry_stage: str,
    verdict: dict,
    guidance_artifacts: list[str],
) -> dict:
    """Record one retry that was taken, and return the entry appended.

    The verifier's guidance looks forward — it tells the next attempt what to
    fix. This looks backward: what the attempt that just ended tried, why it
    failed, where execution went, and where that attempt's own artifacts were
    archived. Neither absorbs the other, and neither replaces
    attempts/attempt-N/, which holds the evidence itself; an entry references
    that directory rather than copying its contents.

    Everything an entry needs is in hand at the retry decision point. The
    blocking issues are carried as the verifier recorded them, field for
    field, rather than summarized. The guidance comes from `guidance_artifacts`,
    which the caller has already narrowed to what *this* attempt wrote — a
    conditional artifact is never cleared from the run root, so an attempt that
    wrote none would otherwise inherit an earlier attempt's. It is omitted when
    the attempt produced none — a clean-clone failure reroutes after a passing
    verdict, which writes no guidance at all — following the
    optional-by-absence convention the history schema already uses.

    The file is created here, at the first retry, and never in advance: a run
    that never retries has no such file, so the absence is itself evidence.
    History is evidence, never state — nothing read back here routes anything.
    """
    entry: dict = {
        "attempt": attempt,
        "blocking_issues": verdict.get("blocking_issues", []),
        "retry_stage": retry_stage,
        "archive_directory": f"attempts/attempt-{attempt}",
    }
    for artifact in guidance_artifacts:
        path = run_dir / artifact
        if path.is_file():
            entry["guidance"] = json.loads(path.read_text(encoding="utf-8"))
            break

    records = load_retry_records(run_dir)
    records.append(entry)
    _retry_record_file(run_dir).write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    return entry


def _cost_record_file(run_dir: Path) -> Path:
    return run_dir / "cost.json"


def load_cost_record(run_dir: Path) -> dict:
    """What the run has recorded spending so far.

    An empty record before the first invocation reports a cost, so a reader
    need not distinguish "no file yet" from "nothing spent yet" — the total is
    zero either way, which is the true answer to what has been recorded.
    """
    path = _cost_record_file(run_dir)
    if not path.is_file():
        return {"total_usd": 0.0, "invocations": []}
    return json.loads(path.read_text(encoding="utf-8"))


def append_cost_record(
    run_dir: Path, stage: str, entry: int, attempt: int, cost: float
) -> dict:
    """Record one invocation's cost and rewrite the total. Returns the record.

    The record accounts for the whole run and is never reset: a re-entry
    leaves it at the run root and it continues from where it stood rather than
    restarting at zero, which is the half of the split the live allowance on
    state.json is the other side of.

    The total is the plain sum of the invocations beside it, computed in their
    recorded order and rounded not at all, so a reader summing the list gets
    the number the record reports rather than one differing in its last bits.
    Nothing here rewrites, repairs or prunes what is already recorded.
    """
    record = load_cost_record(run_dir)
    record["invocations"].append(
        {"stage": stage, "entry": entry, "attempt": attempt, "cost_usd": cost}
    )
    record["total_usd"] = sum(
        invocation["cost_usd"] for invocation in record["invocations"]
    )
    _cost_record_file(run_dir).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def recorded_cost(run_dir: Path) -> float:
    """What this run has cost across every entry of it, from the record.

    The live allowance is entry-scoped and a resume zeroes it, so the run's
    total cannot be read off state.json and is read off cost.json instead —
    the same division `accumulated_attempts` makes between the counter that
    routes and the record that accounts.
    """
    return load_cost_record(run_dir)["total_usd"]


def format_usd(amount: float) -> str:
    """One spelling of a dollar figure, for every message that states one."""
    return f"${amount:.2f}"


# --------------------------------------------------------------------------
# Guidance that may not sanction the outcome it fails
#
# The verifier writes retry-guidance.json to direct a retry, and until now
# nothing stood between two checks: the guidance's shape, and the verdict's
# route. Between them, guidance could instruct a stage to deliver a partial
# result and the same verifier could then fail the run for delivering it.
#
# The check is behavioural and reads no prose. Guidance declares its own
# success condition when it is written, in each current_focus entry's
# satisfied_when; the next verdict answers the guidance entry by entry in
# guidance_outcomes, echoing each entry verbatim and marking the ones it did
# not meet. The coordinator compares two sets of strings and inspects what no
# string says. A failed verdict whose echoed set does not match the guidance
# in force escalates naming the mismatch; a failed verdict that accounts for
# every entry and reports none unmet is a contradiction — the guidance
# authorized the outcome it is failing — and the verifier runs again in place
# on the budget its own stage declares, spending no retry.
# --------------------------------------------------------------------------

#: The fourth self-route failure, beside the three mechanical ones. It belongs
#: there for the same reason they do: it is a fact computed from what the
#: stage produced, not a judgement about the work.
DEFECTIVE_RETRY_GUIDANCE = "defective-retry-guidance"


def guidance_entries(guidance: dict | None) -> list[str]:
    """Every string of a guidance that the next verdict must account for.

    One flat list of the strings themselves: each current_focus entry's focus
    and every preserve_behavior string. The preserved behaviour is in it so a
    retry that regressed something it was told to leave alone fails normally
    rather than reading as defective guidance, and satisfied_when is not,
    because the verdict answers the entry rather than restating its condition.

    The guidance handed here is the one the record of the retry just taken was
    built from, already narrowed to what the attempt that just ended actually
    wrote, so a stale guidance left at the run root can never be read as in
    force. Nothing is read back off disk here.
    """
    if not guidance:
        return []
    entries = [
        item["focus"]
        for item in guidance.get("current_focus", [])
        if isinstance(item, dict) and isinstance(item.get("focus"), str)
    ]
    entries += [
        text for text in guidance.get("preserve_behavior", []) if isinstance(text, str)
    ]
    return entries


@dataclass(frozen=True)
class GuidanceComparison:
    """What one verdict's guidance_outcomes said about the guidance in force.

    `missing` and `extra` are the two directions of set difference — a
    misquoted entry appears as one of each — and `matched` is the whole of
    whether the verdict accounted for the guidance. `unmet` names the entries
    the verdict reported it did not meet; when the set matched and nothing is
    unmet, the guidance sanctioned the outcome it failed.
    """

    missing: tuple[str, ...]
    extra: tuple[str, ...]
    unmet: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return not self.missing and not self.extra


def compare_guidance_outcomes(
    in_force: list[str], verdict: dict
) -> GuidanceComparison:
    """Compare what a verdict echoed against the guidance in force.

    Set equality over strings and nothing else: no branch here reads what a
    guidance entry or an unmet reason says, so varying the wording of either
    while holding the sets equal cannot change what this returns. An absent
    guidance_outcomes echoes nothing and so reports every entry missing,
    which is what stops silence being read as everything met or as nothing
    met.
    """
    echoed = [
        outcome["guidance"]
        for outcome in verdict.get("guidance_outcomes", [])
        if isinstance(outcome, dict) and isinstance(outcome.get("guidance"), str)
    ]
    unmet = [
        outcome["guidance"]
        for outcome in verdict.get("guidance_outcomes", [])
        if isinstance(outcome, dict)
        and isinstance(outcome.get("guidance"), str)
        and "unmet" in outcome
    ]
    return GuidanceComparison(
        missing=tuple(sorted(set(in_force) - set(echoed))),
        extra=tuple(sorted(set(echoed) - set(in_force))),
        unmet=tuple(sorted(set(unmet))),
    )


def guidance_mismatch_reason(
    comparison: GuidanceComparison, verdict: dict
) -> str:
    """Say what did not match, so the escalation is actionable as it stands.

    Named rather than counted, and the absent case is said outright: a verdict
    carrying no guidance_outcomes at all and one carrying the wrong entries
    both escalate here, and a developer should be able to tell them apart from
    the recorded reason alone.
    """
    parts = []
    if "guidance_outcomes" not in verdict:
        parts.append("it carried no guidance_outcomes at all")
    if comparison.missing:
        parts.append(
            f"it does not account for: {', '.join(comparison.missing)}"
        )
    if comparison.extra:
        parts.append(
            f"it accounts for entries the guidance in force does not carry: "
            f"{', '.join(comparison.extra)}"
        )
    return (
        f"the verifier's guidance_outcomes does not match the retry guidance "
        f"in force; {'; '.join(parts)}"
    )


# --------------------------------------------------------------------------
# The clean-clone check
#
# The verifier runs the suite in the working tree, which is the one
# environment where the story's own commit does not exist yet: _complete
# commits the tree after every check the workflow performs. Everything below
# runs the same suite once more where the code actually ships — a fresh clone
# of the repository with the story committed into it — after the verifier
# passes. Since story-045 the verifier is the workflow's last stage, so the
# tree it clones already holds the documenter's edits.
# --------------------------------------------------------------------------

#: How much of the run's combined output the record keeps. Enough to identify
#: what failed; not the whole log, which the run directory is not a home for.
CLEAN_CLONE_OUTPUT_TAIL = 8000


@dataclass(frozen=True)
class CleanCloneResult:
    """What the clean-clone check did, as it is recorded in the run directory.

    Optional fields are expressed by absence in the written record rather than
    by null, matching the execution-history convention: a check that refused to
    run has no exit code and no output to report, only a reason.

    `scope` is not one of those. It carries the selections the coordinator
    substituted into the configured command, and it is required and always
    written — an empty scope is the claim that nothing narrowed the configured
    command, which is a different claim from an absent one and must not
    serialize alike. It carries no default for the same reason: a construction
    site added later cannot omit it and have an unnarrowed run recorded by
    accident.

    `duration_seconds` is the one field the written record does not carry. It
    is how long the run took, measured by the check around the work its
    announcement precedes, and it travels on the result to the completion
    entry the caller appends — where the cross-run duration log reads it —
    rather than into the artifact, which records what the run decided and not
    how long it took to decide it. None where the run was not made or not
    timed.
    """

    ran: bool
    command: str
    runner: str
    scope: tuple[str, ...]
    clone_path: str | None = None
    exit_code: int | None = None
    output_tail: str | None = None
    output_path: str | None = None
    reason: str | None = None
    duration_seconds: float | None = None

    def as_record(self) -> dict:
        record: dict = {
            "ran": self.ran,
            "command": self.command,
            "runner": self.runner,
            "scope": list(self.scope),
        }
        optional = {
            "clone_path": self.clone_path,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
            "output_path": self.output_path,
            "reason": self.reason,
        }
        record.update({key: value for key, value in optional.items() if value is not None})
        return record


def suite_run_shadows(
    passing_scope: Iterable[str], failing_scope: Iterable[str]
) -> bool:
    """Whether a passing suite run supersedes an earlier failing one.

    The rule: a later pass supersedes an earlier failure only when the pass
    ran at least as much as the failure did, which is recorded scope by set
    containment — the passing run's scope must be a subset of the failing
    run's. A rerun narrowed by nothing shadows whatever failed; a rerun at the
    same scope shadows, so an ordinary fix-and-rerun is unaffected; a rerun
    carrying a selection the failure did not carry filtered *more* than the
    failure did and establishes nothing about what it stopped running, so it
    shadows nothing.

    The limit, which is why this is a comparison and not an understanding: a
    scope entry is compared as an opaque string. The harness does not parse,
    normalise or interpret the target's selector syntax here or anywhere, so
    two spellings of one selection are two strings, and a run made broader by
    *rewriting* a selection rather than by dropping one is not recognised as
    broader. Such a rerun is treated as narrowing and refused; the way out is
    to rerun at a scope this comparison can see is no larger, not to teach
    this function a syntax.
    """
    return set(passing_scope) <= set(failing_scope)


def suite_output_file(artifact: str) -> str:
    """Where a suite run's full combined output is written, from its result name.

    The one place a full-output filename is shaped, so no call site spells one:
    each of the coordinator's suite runs already knows the artifact it writes,
    and the output beside it is named from that. The tail stays in the record
    whatever this holds, so each artifact remains readable on its own; what the
    file adds is what a tail loses, which is everything that happened early in
    a long run.
    """
    stem = artifact[: -len(".json")] if artifact.endswith(".json") else artifact
    return f"{stem}-output.txt"


@dataclass(frozen=True)
class Restoration:
    """Which of the restored-or-removed halves each path fell to.

    Returned by the one function that makes the decision rather than recomputed
    beside it, so what a revert *reports* it did and what it *did* cannot
    disagree.
    """

    restored: tuple[str, ...]
    removed: tuple[str, ...]


def restore_from_baseline(
    baseline: Path, tree: Path, paths: list[str] | tuple[str, ...]
) -> Restoration:
    """Put the named paths of a tree back to what the stage baseline holds.

    The rule, written once: a path the baseline holds is restored to that
    content, and a governed path the baseline does not hold is removed, because
    a path absent from the baseline did not exist when the stage started.
    Nothing here reverts to HEAD — a file an earlier stage of the same run
    created has no HEAD version and its pre-stage state is knowable all the
    same.

    Two callers: the clone the revert check takes its proof on, and the working
    tree that proof licenses reverting. They must decide restore-or-remove
    identically, because reproducing the content the proof was taken on is the
    whole of why reverting the working tree is safe — two copies of this rule
    could leave a tree the proof does not cover.

    The baseline is asserted to exist by the caller: this reports nothing and
    decides nothing about a baseline that is not there.
    """
    restored, removed = [], []
    for rel in paths:
        source, destination = baseline / rel, tree / rel
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            restored.append(rel)
        else:
            if destination.is_file():
                destination.unlink()
            removed.append(rel)
    return Restoration(tuple(restored), tuple(removed))


def _write_suite_output(destination: Path | None, output: str) -> str | None:
    """Write a suite run's whole combined output, and say where it went.

    Returns None when the caller named no destination, which is the optional-
    by-absence convention the surrounding records use: a run whose output was
    not kept records no path rather than a null one.
    """
    if destination is None:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(output, encoding="utf-8")
    return str(destination)


def _resolve_interpreter(target_root: Path, interpreter: str) -> Path | None:
    """The interpreter as something runnable, or None when it does not exist.

    A path is read relative to the target repository, the way the configured
    test command already reads it; a bare name is looked up on PATH.
    """
    candidate = Path(interpreter)
    if not candidate.is_absolute():
        candidate = target_root / candidate
    if candidate.is_file():
        return candidate
    found = shutil.which(interpreter)
    return Path(found) if found else None


def _build_clone(
    target_root: Path,
    clone: Path,
    *,
    revert: list[str] | tuple[str, ...] = (),
    baseline: Path | None = None,
) -> None:
    """Clone the target locally and commit its working tree into the clone.

    A clone, not a tree copy: the point of the check is that the story is
    present as a *commit*, which is the state a test resolving a baseline out
    of git history actually sees. The source is a filesystem path, so no
    network access is possible by construction.

    The clone is built over git's normal transport (`--no-local`): git
    negotiates a pack over a pipe rather than walking the source's
    `.git/objects` as a directory tree. It replaces `--no-hardlinks`, which
    carried exactly one property — the clone's object files are its own copies
    rather than hardlinks into the source, so nothing the source later does to
    its objects can reach the clone. The normal transport gives that property
    inherently: a packed object stream never references the source's object
    files at all, so there is nothing left to unlink from.

    What `--no-hardlinks` did *not* give is why it is gone. A directory copy
    enumerates the source's object store and then copies what it enumerated,
    and any file that disappears in between is a hard failure. Three CI runs
    failed exactly that way; one of the files git could not copy was
    `.git/objects/pack/multi-pack-index.lock`, which exists only while
    something is writing a multi-pack index in the source. The transport was
    chosen over disabling that writer because it makes the failure impossible
    rather than unlikely: the copy path is fragile against *any* concurrent
    writer, not only the one that was observed, and the normal transport never
    reads the source's object files whatever is writing them. It costs
    wall-clock on every clean-clone and revert check; the measurement is
    recorded in `.harness/docs/ARCHITECTURE.md`.

    Files .gitignore excludes reach the clone by neither route — the clone
    carries committed files and the two applications below carry tracked edits
    and untracked-but-not-ignored files — so the clone holds the same set of
    files _complete's `git add -A` would commit. The target repository is only
    read: every write happens inside the clone.

    `revert` names repository-relative paths to restore from `baseline`
    *inside the clone*, after the working tree has been applied and before the
    commit, so the clone holds every change the working tree carries except
    those. It defaults to reverting nothing, which is the clean-clone check's
    behavior and is unchanged by its existence.

    The baseline is a directory of file copies taken before the stage ran, not
    a revision: a path the baseline holds is restored to the content it held
    then, and a governed path the baseline does not hold is *removed* from the
    clone, because a path absent from the baseline did not exist when the
    stage started. Nothing here reverts to HEAD — a file an earlier stage of
    the same run created has no HEAD version, and its pre-stage state is
    knowable all the same.
    """
    result = subprocess.run(
        ["git", "clone", "--quiet", "--no-local", str(target_root), str(clone)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not clone {target_root}: {result.stderr.strip()}")

    diff = _git(target_root, "diff", "--binary", "HEAD").stdout
    if diff.strip():
        applied = subprocess.run(
            ["git", "-C", str(clone), "apply", "--whitespace=nowarn", "-"],
            input=diff,
            capture_output=True,
            text=True,
        )
        if applied.returncode != 0:
            raise RuntimeError(
                f"Could not apply the working tree to {clone}: {applied.stderr.strip()}"
            )

    untracked = _git(
        target_root, "ls-files", "--others", "--exclude-standard", "-z"
    ).stdout.split("\0")
    for rel in filter(None, untracked):
        source = target_root / rel
        if not source.is_file():
            continue
        destination = clone / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    if revert:
        if baseline is None or not baseline.is_dir():
            raise RuntimeError(
                f"Could not revert {', '.join(revert)} in {clone}: no captured "
                f"baseline to restore them from ({baseline})"
            )
        restore_from_baseline(baseline, clone, revert)

    _git(clone, "add", "-A")
    commit = _git(
        clone,
        "-c",
        "user.email=harness@l5.local",
        "-c",
        "user.name=l5 harness",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "the story's working tree, committed for the clean-clone check",
    )
    if commit.returncode != 0:
        raise RuntimeError(
            f"Could not commit the working tree in {clone}: {commit.stderr.strip()}"
        )


def _link_interpreter_roots(target_root: Path, clone: Path, interpreters: list[str]) -> None:
    """Link the directories the configured interpreters live in into the clone.

    A virtualenv is gitignored and therefore absent from the clone, so the
    configured path would not resolve there. The directory names come from the
    configured paths rather than being written here, so a repository that names
    its environments differently needs no change.

    The links are made after the commit and excluded inside the clone, so the
    environment the suite needs is present without the clone's working tree
    reporting anything the target's does not. A `.gitignore` entry for a
    directory does not cover a symlink standing in its place.

    Since story-119 the derivation and the linking are `worktrees`', not this
    function's: a worktree has the same absence for the same reason, and one
    fact with one home is what stops a clone and a worktree disagreeing about
    what an interpreter's root is. This keeps its signature, its callers and its
    effect on the clone; what it lost is a second copy of the reasoning.
    """
    worktrees.link_build_state(
        target_root, clone, worktrees.interpreter_roots(interpreters)
    )


def run_clean_clone(
    target_root: Path,
    command_to_run: str,
    verification_runner: str | None,
    destination: Path,
    revert: list[str] | tuple[str, ...] = (),
    baseline: Path | None = None,
    output_path: Path | None = None,
    *,
    scope: tuple[str, ...] = (),
) -> CleanCloneResult:
    """Run a command in a fresh clone with the story committed.

    The command is the caller's, and every caller takes it from the target
    repository's own configuration: the configured `test_command` for the
    checks that run the whole suite, and the configured `test_selection_command`
    with a nominated test substituted into it for the revert check's
    short-circuit. Nothing about either is written here — which is what lets
    the selector run through the same builder the suite does, so the applied
    and reverted runs differ only in what is reverted.

    Only its *first word* is substituted, and only when the
    configuration names a `verification_runner`, so the check can exercise an
    environment other than the one the developer works in and find an
    incompatibility before CI rather than by it. The caller owns `destination`
    and removes it whatever the result.

    That substitution is a first-word swap and nothing more: every remaining
    argument is the configured command's own. A target whose environment
    difference cannot be expressed by replacing the first word expresses it in
    `test_command` instead.

    This is the single build-a-clone-and-run-the-suite path. `revert` and the
    `baseline` it is restored from are passed through to the clone builder and
    default to reverting nothing, so the clean-clone check runs exactly as it
    did; the revert check is this same operation with the governed paths
    restored to the state the stage found them in rather than applied.

    `output_path` is where the run's whole combined output is written. The
    caller names it, because the caller is the one that knows which result
    artifact this run is recorded under and the name is derived from that. It
    defaults to writing nothing, so a caller that names none records none.

    `scope` is what the caller narrowed the configured command by, recorded on
    the result and interpreted nowhere: the harness neither parses nor
    understands the target's selector syntax. It is passed through rather than
    derived from `command_to_run`, because only the caller knows what it
    substituted. An empty scope says the configured command ran unnarrowed,
    which is what every whole-suite caller here means.
    """
    argv = shlex.split(command_to_run)
    runner = verification_runner or argv[0]
    command = shlex.join([runner, *argv[1:]])

    resolved = _resolve_interpreter(target_root, runner)
    if verification_runner and resolved is None:
        return CleanCloneResult(
            ran=False,
            command=command,
            runner=runner,
            scope=scope,
            reason=(
                f"verification_runner names {verification_runner}, which is not "
                f"an executable that exists under {target_root}"
            ),
        )

    clone = destination / "clone"
    _build_clone(target_root, clone, revert=revert, baseline=baseline)
    _link_interpreter_roots(target_root, clone, [argv[0], runner])

    result = subprocess.run(
        [runner, *argv[1:]],
        cwd=clone,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return CleanCloneResult(
        ran=True,
        command=command,
        runner=runner,
        scope=scope,
        clone_path=str(clone),
        exit_code=result.returncode,
        output_tail=output[-CLEAN_CLONE_OUTPUT_TAIL:],
        output_path=_write_suite_output(output_path, output),
    )


#: What the post-story inspection's wait is worth. An agent invocation, so a
#: stage's rather than a suite run's.
INSPECTION_COST = "this takes about as long as a stage"

#: What the pre-flight outbox sweep's wait is worth. One invocation of the
#: configured sync command per queued entry, each under its own timeout, so
#: what the wait is worth is stated in the terms the bound is stated in.
OUTBOX_SWEEP_COST = (
    "this runs the configured sync command once per entry, under its timeout"
)


def _announcement(message: str, cost: str, *, estimate: float | None = None) -> str:
    """One line saying a long step has started and what the wait is worth.

    This is the single home of the announcement convention. Every long step
    inside a run says so *before* it starts, and says what the wait is worth
    beside it, so a reader watching a console does not read a wait as a fault —
    which is the one reading a silence always admits. The two halves are
    rendered here rather than at each announcing site, so a step added later
    carries the convention by calling this rather than by imitating a
    neighbour's f-string.

    What the wait is worth is said in one of two ways. With an `estimate` — the
    seconds the operation has taken before, read off the cross-run duration log
    at run start — the line is the message followed by the parenthesised
    figure, "(Estimated run time: 4 minutes)", because a figure says how long
    where the cost clause could only say that the step is long. Without one the
    line is the message, the separator and the cost clause, exactly what it
    rendered before estimates existed, so a fresh target announces nothing
    invented. The estimate is a report and never a bound.

    An empty cost clause raises whether or not an estimate is given, on the
    fail-loudly standard: a line that says a step started and not what the wait
    is worth is the line this convention exists against, and rendering it
    silently would make the omission indistinguishable from a step whose wait
    genuinely costs nothing. An estimate does not excuse the clause, because
    the estimate is present only where the history happens to hold enough.
    """
    if not cost.strip():
        raise ValueError(
            "an announcement with no cost clause says a step started and not "
            f"what the wait is worth: {message!r}"
        )
    if estimate is not None:
        return _with_estimate(message, estimate)
    return f"{message}; {cost}"


def _timing(operation: str, duration_seconds: float | None) -> dict:
    """The two fields a timed operation's completion entry carries.

    A completion entry built through this helper carries both fields or
    neither: a duration with no operation could not be keyed, and an
    operation with no duration is an occurrence that was not timed — a revert
    check decided by its nomination selector — which must leave no duration
    record. That is a rule for the entries built here, not for every entry:
    an escalated entry appended in `run_story` carries the stage's elapsed
    time without an operation, because it completes no timed operation and
    keys no observation.
    Splatted into `append_event` beside the entry's other fields.
    """
    if duration_seconds is None:
        return {}
    return {"duration_seconds": duration_seconds, "operation": operation}


def _measured(started: float) -> float:
    """Seconds elapsed since `started` on the monotonic clock, rounded as the
    stage duration is.
    """
    return round(time.monotonic() - started, 3)


def _suite_rerun_started(
    run_dir: Path,
    stage_name: str,
    artifact: str,
    phrase: str,
    *,
    cost: str = "this takes as long as a suite run",
    operation: str | None = None,
) -> None:
    """Announce a check that is about to re-run the configured test command.

    `operation` names the timed operation the check is, when it is one, and is
    what the estimate is read under: a check with enough history announces the
    figure in place of its cost clause, and one without announces the clause
    exactly as before. The nomination selector names none, because it states a
    bound rather than a guess and is not timed.

    Every stage says it has started before it works. A check that re-runs the
    whole suite said nothing at all, so the console sat silent for the length
    of a suite run with the previous line still on screen, which reads as a
    hang. This is the general rule rather than one missing event: a check that
    re-runs the configured test command says so before doing so.

    It is an event rather than a bare print because `append_event` is the
    coordinator's one write path — a second output path, however correct, is
    drift waiting to happen — and because an event is what makes the wait
    visible in `l5-status` and in the run's structured history rather than only
    on the console of whoever started the run.

    No stage name and no artifact name is written here. Both come from the
    declaration the caller already read, and `phrase` says what is being re-run
    and why, so every announcement comes from this one function rather than
    being spelled per check. `cost` is what the wait is worth defaulted to a
    suite run, because a check that re-runs the whole suite is what this was
    written for; a check whose wait is a different size says so rather than
    claiming a suite run's.

    The line is rendered through `_announcement`, which is where the convention
    lives; the kind stays a literal in this function's own body, because a
    shared writer taking the kind as a parameter would forward a variable into
    `append_event` — which the standing scan over this module's source refuses
    outright — and would collapse the one-kind-one-emitter rule beside it. The
    composer is therefore the shared part and the event write is not.
    """
    append_event(
        run_dir,
        _announcement(
            f"re-running the suite {phrase}", cost,
            estimate=duration_estimate(operation),
        ),
        kind="suite-rerun-started",
        stage=stage_name,
        artifacts=[artifact],
    )


# --------------------------------------------------------------------------
# Suite runs take turns
#
# A story's time divides sharply. Its agent turns are network-bound and take
# tens of minutes while using almost no local CPU; its whole-suite runs take
# every core, because the configured test command carries the parallel flag.
# Two runs at once overlap the agent time happily and collide only in the
# suite time, and the collision is worse than arithmetic: load makes
# timing-dependent tests fail or go inconclusive, a spurious failure costs a
# retry, and the retry adds another suite run to the load.
#
# So the three whole-suite runs — the declared suite run, the clean-clone
# check and the revert check's whole-suite fallthrough — take an exclusive
# turn before running and release it after. The command is not touched: the
# worker count stays as configured and the coordinator knows nothing about the
# flag. What changes is that two coordinators of one repository queue for the
# suite instead of sharing the machine.
#
# The lock is scoped to the repository rather than to the machine, and that is
# a decision rather than a shortcut. A machine-wide lock deadlocks the moment
# this harness is its own target: the suite running under the lock drives the
# coordinator on constructed targets thousands of times, and each of those
# would wait on the lock the outer run holds. The coordinator may not read the
# environment, so nesting cannot be detected that way. The lock is keyed on
# the primary working tree instead, which every worktree of a repository shares
# and which `worktrees.worktree_root` already derives, and lives beside the
# worktrees the harness already owns. Two runs of one repository serialise; a
# run of another repository does not.
#
# The turn fails toward running, in three ways. It is a kernel file lock,
# released when its holder dies, so a killed run never holds it. A lock file
# that cannot be created or opened costs the turn and never the run. And a wait
# that outlasts the configured bound does not stall: a suite hung in another
# run would otherwise stall every run behind it and the harness puts no
# timeout on the suite itself. What an expired wait does depends on the bound.
# At zero the suite runs at once without a turn. At any other bound the waiter
# takes the turn *from* the holder — a live process's flock cannot be taken,
# so the takeover is a fresh file renamed over the lock path, which the hung
# holder does not hold; the holder keeps its lock on an orphaned inode nobody
# opens again. Every other waiter notices the inode at the path change, says
# so, reopens the path and starts its wait over, so behind a hung run the queue
# drains one run per wait instead of all giving up within minutes of each
# other and sharing the machine with the hung one. Every path that runs
# without a turn, takes one over, or restarts its wait says so in events.log.
#
# The bound is a configured count of suite runs times what a suite run has
# taken on this machine, or a fallback where too few have been measured — see
# `suite_turn_wait_seconds`.


def suite_lock_path(target_root: Path, config: dict) -> Path:
    """Where the turn is taken for the repository `target_root` belongs to.

    Derived through `worktrees.worktree_root` and nowhere else, so the lock
    follows a configured `worktree_dir` and is otherwise beside the repository
    rather than inside any one worktree of it — which is what makes it one lock
    per repository rather than one per tree.
    """
    return worktrees.worktree_root(target_root, config) / SUITE_LOCK_NAME


def _try_suite_lock(handle) -> bool:
    """One non-blocking attempt at the lock: True when taken, False when held.

    Any failure other than the lock being held propagates, so the caller can
    tell a lock file it cannot use from a turn it has to wait for.
    """
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _inode_held(handle) -> int:
    """The inode of the file `handle` is open on."""
    # `fstat` is imported by name rather than reached through `os`, for the
    # reason `_kill_group` gives: this module names no environment reader.
    from os import fstat

    return fstat(handle.fileno()).st_ino


def _inode_at(lock_path: Path) -> int:
    """The inode at the lock path now. The path is only ever renamed over,
    never unlinked, so it always resolves; a stat that fails is an `OSError`
    the caller treats as the could-not-open path.
    """
    return lock_path.stat().st_ino


def _takeover_lock_path(lock_path: Path) -> Path:
    """The second, momentary lock beside the suite lock, under which the turn
    is swapped and a granted turn is confirmed.
    """
    return lock_path.with_name(SUITE_TAKEOVER_LOCK_NAME)


def _confirm_suite_turn(lock_path: Path, handle) -> bool:
    """Whether a flock just granted on `handle` is the turn: True when the
    inode `handle` holds is still the one at `lock_path`, False when the path
    has moved on to a fresh inode a taker renamed over it.

    Asked under the takeover lock, taken blocking because a swap is momentary,
    so the answer cannot be read in the middle of one: a taker mid-swap
    finishes first and this reports the new inode, or this reports first and
    the taker's own flock retry under the same lock then meets the grant. A
    holder that let go inside the one poll between a taker's inode check and
    its rename would otherwise leave this run granted the old inode while the
    taker runs on the new one — the overlap the turn exists to prevent.
    """
    with open(_takeover_lock_path(lock_path), "a", encoding="utf-8") as takeover:
        fcntl.flock(takeover, fcntl.LOCK_EX)
        return _inode_at(lock_path) == _inode_held(handle)


def _take_over_suite_turn(lock_path: Path, handle):
    """Take the turn from a holder whose flock cannot be taken: a fresh file,
    locked, renamed over `lock_path`. `handle` is the handle this run has
    been waiting on. Returns the handle holding the turn — `handle` itself
    where the holder let go in the meantime and its lock was granted, a fresh
    handle where the turn was swapped — or None where another waiter took
    over first.

    The swap happens under a second, momentary lock at
    `SUITE_TAKEOVER_LOCK_NAME` beside the suite lock, so two waiters expiring
    in the same poll cannot each rename over the other: the one that takes the
    takeover lock confirms the inode at the path is still the one it waited on
    and swaps; the other finds the takeover lock held, or the inode already
    changed, takes nothing over, and sees the new inode on its next poll. The
    rename is what makes the swap atomic for anyone opening the path: a reader
    finds either the old inode or the new one and never a half. The lock path
    is never unlinked, and the file's existence still means nothing.

    Before swapping, and still under the takeover lock, the flock on `handle`
    is tried once more. A holder that finished in the window between this
    run's last poll and now has released it, and taking the turn on that inode
    is the ordinary grant; swapping over it would leave the next waiter to be
    granted the old inode while this run ran on the new one, which is the
    overlap the turn exists to prevent. Only a lock still refused here — the
    holder still there — is taken over. The waiter's side of the same race is
    `_confirm_suite_turn`.

    Any `OSError` other than the takeover lock being held propagates, so the
    caller treats it as the could-not-open path; a temporary file left by such
    a failure is removed on the way out.
    """
    waited_on = _inode_held(handle)
    with open(_takeover_lock_path(lock_path), "a", encoding="utf-8") as takeover:
        if not _try_suite_lock(takeover):
            return None
        if _inode_at(lock_path) != waited_on:
            return None
        if _try_suite_lock(handle):
            return handle
        descriptor, fresh = tempfile.mkstemp(
            dir=lock_path.parent, prefix=f"{SUITE_LOCK_NAME}.")
        handle = open(descriptor, "a", encoding="utf-8")
        try:
            # A file nobody else has opened: the lock is granted, and it is
            # the lock the handle keeps once the file is at the lock path.
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            Path(fresh).replace(lock_path)
        except BaseException:
            handle.close()
            Path(fresh).unlink(missing_ok=True)
            raise
        # Returning leaves the block, which closes the takeover handle and
        # releases the takeover lock; the returned handle keeps the suite lock.
        return handle


@contextlib.contextmanager
def suite_turn(
    run_dir: Path,
    target_root: Path,
    config: dict,
    stage_name: str,
    artifact: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
):
    """Hold the repository's turn at the whole suite for the body's duration.

    The lock is tried without blocking first. When it is held, a
    `suite-turn-waited` event announces the wait — through `_announcement`,
    naming what the wait is worth and where its bound came from — and the
    path is then polled every `SUITE_TURN_POLL_SECONDS`. Each poll makes two
    observations, in this order. First the inode at the path is compared
    against the inode of the handle being waited on: a different inode is the
    turn having changed hands (a waiter ahead of this one took it over), at
    which point the handle is reopened on the path, a second
    `suite-turn-waited` event announces the restart, and the bound starts over
    from that moment. Then, with the same inode, the flock is tried: refused
    is the same holder still there, granted is the holder finishing normally
    and the turn taken on the same inode. The announcement is written before
    the wait and the suite's own announcement after it, so the wait is
    readable off the events log as the gap between the two lines; an
    uncontended turn writes nothing at all.

    A bound that expires with the lock still held takes the turn from the
    holder, through `_take_over_suite_turn`, and a note says the bound
    expired, that this run took the turn, and that the previous holder was
    left running. A takeover another waiter got to first takes nothing over
    here: the next poll sees the new inode and restarts, so behind a hung
    holder the queue drains one run per wait. A bound of zero takes nothing
    over — it writes the wait and the expiry note and runs at once without a
    turn, exactly as it did before takeovers existed.

    A takeover and a normal release can meet, and the two sides of the race
    are closed under the takeover lock. A holder that lets go in the window
    between a taker's inode check and its rename would leave an ordinary
    waiter granted the old inode while the taker renamed a fresh one over the
    path, and both would run. So the taker retries the flock on the handle it
    waited on under the takeover lock and, where it is granted, takes the turn
    on that inode and swaps nothing (`_take_over_suite_turn`); and a waiter
    whose ordinary flock is granted confirms, under the same lock, that the
    inode it holds is still the one at the path (`_confirm_suite_turn`), and
    where it is not, lets the orphan go, reopens the path and keeps waiting —
    announcing the restart, since the turn did change hands. What the lock
    serialises is the swap against the confirmation. Two windows are left,
    and neither is closed here. The first: a lock refused by the hung holder
    and one refused by a waiter granted and confirmed in the instant between
    this run's expiry and its taking the takeover lock look the same to the
    taker. The second: the first, uncontended `_try_suite_lock` at the top of
    this function is the one grant not confirmed under the takeover lock, so
    a fresh entrant granted the old inode after the holder lets go — while a
    taker already holds the takeover lock with its retry refused and has not
    yet renamed the fresh file over the path — runs on the orphaned inode
    beside the taker. Each is a fraction of a poll.

    A lock file that cannot be created or opened, and any stat or open that
    fails mid-wait other than the lock being held, runs the body anyway and
    says why as a note naming the path and the error. Nothing here refuses or
    stops a run.

    `sleep` and `clock` are parameters for the reason `run_story`'s `sleep`
    is: a test observes the wait without taking it. The kind is a literal in
    this one function body, for the reason `_suite_rerun_started` gives, and
    the restart is the same kind written from the same call.
    """
    lock_path = suite_lock_path(target_root, config)
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a", encoding="utf-8")
        taken = _try_suite_lock(handle)
        if not taken:
            bound = suite_turn_wait_seconds(config)
            cost = suite_turn_wait_cost(config)
            waited_on = _inode_held(handle)
            message = (
                f"waiting for the repository's turn at the suite, which "
                f"another run holds at {lock_path}"
            )
            announce = True
            while not taken:
                if announce:
                    append_event(
                        run_dir,
                        _announcement(message, cost),
                        kind="suite-turn-waited",
                        stage=stage_name,
                        artifacts=[artifact],
                    )
                    started = clock()
                    announce = False
                changed_hands = False
                while clock() - started < bound:
                    sleep(min(SUITE_TURN_POLL_SECONDS,
                              max(0.0, bound - (clock() - started))))
                    if _inode_at(lock_path) != waited_on:
                        changed_hands = True
                        break
                    taken = _try_suite_lock(handle)
                    if taken:
                        # Granted — but on the inode still at the path? A
                        # taker may have renamed a fresh file over it in the
                        # same instant the holder let go, in which case this
                        # run holds an orphan and the turn is the taker's:
                        # let the orphan go and keep waiting on the path.
                        if not _confirm_suite_turn(lock_path, handle):
                            fcntl.flock(handle, fcntl.LOCK_UN)
                            taken = False
                            changed_hands = True
                        break
                if taken:
                    break
                if not changed_hands and bound > 0:
                    # The bound expired on a holder that is still there. Take
                    # the turn from it, unless another waiter got there first
                    # -- then the next poll sees the new inode and restarts.
                    handle_after = _take_over_suite_turn(lock_path, handle)
                    if handle_after is handle:
                        # The holder let go as this run went to take the
                        # turn: it is taken on the same inode, exactly as a
                        # grant inside the bound is, and nothing is swapped.
                        taken = True
                        break
                    if handle_after is not None:
                        handle.close()
                        handle = handle_after
                        taken = True
                        append_event(
                            run_dir,
                            f"the turn at the suite was still held after "
                            f"{bound / 60:g} minutes, so this run took the "
                            f"turn and left the previous holder running; a "
                            f"suite hung in another run must not stall this "
                            f"one",
                            stage=stage_name,
                            artifacts=[artifact],
                        )
                        break
                    sleep(SUITE_TURN_POLL_SECONDS)
                    changed_hands = _inode_at(lock_path) != waited_on
                    if not changed_hands:
                        # Still the inode this run waited on: whoever holds
                        # the takeover lock has not swapped yet. Try again
                        # rather than re-announce a wait that has not ended.
                        continue
                if changed_hands:
                    # The turn changed hands: reopen the path, say so, and
                    # start the bound over from this moment.
                    handle.close()
                    handle = open(lock_path, "a", encoding="utf-8")
                    waited_on = _inode_held(handle)
                    message = (
                        f"the turn at the suite changed hands, so the wait "
                        f"restarts: another run took the turn at {lock_path} "
                        f"from the holder this run was waiting on"
                    )
                    announce = True
                    continue
                append_event(
                    run_dir,
                    f"the turn at the suite was still held after {bound / 60:g} "
                    f"minutes, so the suite runs without one; a suite hung in "
                    f"another run must not stall this one",
                    stage=stage_name,
                    artifacts=[artifact],
                )
                break
    except OSError as error:
        append_event(
            run_dir,
            f"no turn at the suite was taken because the lock file "
            f"{lock_path} could not be created or opened ({error}); the "
            f"suite runs without one",
            stage=stage_name,
            artifacts=[artifact],
        )
    try:
        yield
    finally:
        if handle is not None:
            # Closing releases the lock the kernel granted, if any; a handle
            # that never took it releases nothing.
            handle.close()


def inspection_started(run_dir: Path, story_id: str, scope_files: int) -> None:
    """Announce the post-story inspection, before the invocation is made.

    A run says "story completed" and the inspection then takes about as long as
    a stage while saying nothing, so the only reasonable reading of that
    terminal is that the run has hung after finishing. It is the one agent
    invocation in a run that announced nothing first, and it is also the step
    that most needs the cost clause, because it begins after the run has
    declared itself complete — the one moment a developer has been given
    permission to stop watching.

    Public because `orchestration/story_inspection.py` calls it. It names the
    story and the size of the scope, so a reader knows what is being inspected
    and roughly how much of it, and renders through the shared composer with
    the kind spelled as a literal here, on the terms `_suite_rerun_started`
    states.
    """
    append_event(
        run_dir,
        _announcement(
            f"inspecting what {story_id} changed, over {scope_files} file(s)",
            INSPECTION_COST,
            estimate=duration_estimate(INSPECTION_OPERATION),
        ),
        kind="inspection-started",
    )


def outbox_sweep_started(run_dir: Path, queued: int) -> None:
    """Announce the pre-flight outbox sweep, before the queue is drained.

    The sweep sits at the top of a run, above the first stage line, and runs
    the configured sync command once per queued entry under a per-entry
    timeout — so a run over a full queue opened with the same silence the
    inspection opened with, in the place a developer is most likely to be
    watching for the run to start.

    Public because `orchestration/outbox_sweep.py` calls it. Nothing about the
    count it carries is read by either call site: it says how much there is to
    drain and decides nothing.
    """
    append_event(
        run_dir,
        _announcement(
            f"draining the outbox: {queued} entry(s) queued", OUTBOX_SWEEP_COST
        ),
        kind="outbox-sweep-started",
    )


def suite_failure_carried_forward_note(outstanding: dict) -> str:
    """What the events log says where a red suite advances instead of routing back.

    A red suite says the tree is broken and says nothing about which stage
    broke it, so the coordinator records the failure and advances; the verifier
    is where the failure is judged, from the routing table it already reads.
    Without this line the run would appear to advance on a passing suite, which
    is the one thing a reader of events.log must not conclude here — so the
    message names the declaring stage, the exit code, and the retained record
    and output the failure left behind, all off the outstanding-failure record
    rather than off a second reading of the run. The paths it names are
    current: a resume that moves the retained pair repoints the record to
    follow it (repoint_outstanding_failure), so this line, composed on a
    resumed run, still names paths that hold the files.

    This composes the line and does not append it. Both routes that carry a
    failure forward append it with the generic `note` kind, and the kind is
    spelled in `run_story` alone, where every other note in this run's stream
    is spelled: one kind, one emitting function. `note` rather than a kind of
    its own because a kind has to be declared in the history schema, which a
    run's history is validated against and which this story does not own; and
    the reader that a shared kind could collide with asserts the *absence* of
    notes on a run whose suite never failed, while this line is written only
    where one did.
    """
    return (
        f"the suite the coordinator ran after {outstanding.get('stage')} exited "
        f"{outstanding.get('exit_code')}; carrying the failure forward to the "
        f"next stage rather than re-entering {outstanding.get('stage')}, for "
        f"the verifier to judge. Its record is "
        f"{outstanding.get('result_path')} and its output is "
        f"{outstanding.get('output_path')}"
    )


def suite_failure_shadowed_by_clean_clone_note(outstanding: dict) -> str:
    """What the events log says where a clean-clone pass clears an outstanding failure.

    The clean-clone check runs the configured command in a fresh clone with the
    story committed, and where it exits zero at a scope no narrower than the
    outstanding failure's it shadows that failure on the same terms a green
    declared run does. Whichever path clears the field, the clear is a routing
    decision — it is what lets the run complete — so it is recorded on both
    paths and is reconstructable from events.log as well as from state.json:
    the line names the failure that was cleared, off the outstanding-failure
    record rather than off a second reading of the run, in the shape the
    carry-forward line takes. `suite_failure_shadowed_by_declared_rerun_note`
    is this composer's sibling for the other path, a declared rerun that
    passes at a scope no narrower than the failure's; the two differ only in
    which run they say did the clearing. The route this line was written for
    is a run resumed at the verifier, and the resume moves the retained pair
    under its entry directory before the verifier runs; the record's paths
    follow that move (repoint_outstanding_failure), so the pair this line
    names is where a reader finds it.

    This composes the line and does not append it; `run_story` appends it with
    the generic `note` kind, where every other note in a run's stream is
    spelled, and for the reason the carry-forward line is a note rather than a
    kind of its own.
    """
    return (
        f"the clean-clone check passed at a scope no narrower than the suite "
        f"failure outstanding since {outstanding.get('stage')} exited "
        f"{outstanding.get('exit_code')}; that failure is cleared. Its record "
        f"was {outstanding.get('result_path')} and its output was "
        f"{outstanding.get('output_path')}"
    )


def suite_failure_shadowed_by_declared_rerun_note(outstanding: dict) -> str:
    """What the events log says where a declared rerun clears an outstanding failure.

    The suite the coordinator runs after the declaring stage's turn, where it
    exits zero at a scope no narrower than the outstanding failure's, shadows
    that failure and clears the field. The reason the clear is written down is
    the one `suite_failure_shadowed_by_clean_clone_note` gives, and it is not
    specific to either path: the clear is a routing decision, it lets the run
    complete, and so it is reconstructable from events.log as well as from
    state.json. Without this line the only trace of a declared clear was the
    absence of a carry-forward line after the rerun's announcement, and a
    reader had to infer the clear from that absence and from state.json no
    longer carrying the field. The line names the failure that was cleared —
    its stage, its exit code, and the retained record and output it left
    behind — off the outstanding-failure record rather than off a second
    reading of the run, in the shape its sibling and the carry-forward line
    take; the paths it names are current for the reason theirs are.

    This composes the line and does not append it; `run_story` appends it with
    the generic `note` kind and the declaring stage's name, where every other
    note in a run's stream is spelled, and for the reason the carry-forward
    line is a note rather than a kind of its own.
    """
    return (
        f"a declared suite rerun passed at a scope no narrower than the suite "
        f"failure outstanding since {outstanding.get('stage')} exited "
        f"{outstanding.get('exit_code')}; that failure is cleared. Its record "
        f"was {outstanding.get('result_path')} and its output was "
        f"{outstanding.get('output_path')}"
    )


def clean_clone_check(
    run_dir: Path,
    target_root: Path,
    config: dict,
    artifact: str,
    *,
    stage_name: str,
) -> CleanCloneResult:
    """Run the check in a scratch directory and record what it did.

    The clone is built under a temporary directory outside the target
    repository and removed once the run of the suite completes, whatever its
    result. The record stays: a reader can tell the check ran rather than
    inferring it from a pass.

    The announcement is made here rather than at the call site, so it cannot
    happen without the check running: removing the `clean_clone` declaration
    silences the announcement and the result together. `stage_name` is required
    and keyword-only for the reason `capture_stage_baseline`'s `accounted_for`
    is — a defaulted argument would let a later call site silence the
    announcement by omission.

    The whole suite runs under the repository's turn, taken before the
    announcement so a contended wait is announced ahead of the run and
    released when the run returns.
    """
    with suite_turn(run_dir, target_root, config, stage_name, artifact):
        _suite_rerun_started(
            run_dir,
            stage_name,
            artifact,
            "in a fresh clone with the story committed, to check that it still "
            "passes where the code ships",
            operation=CLEAN_CLONE_OPERATION,
        )
        # Timed from here — after the turn and the announcement, around the
        # work the announcement precedes — so the figure is the check's own.
        started = time.monotonic()
        scratch = Path(tempfile.mkdtemp(prefix="l5-clean-clone-"))
        try:
            result = run_clean_clone(
                target_root,
                config["test_command"],
                config.get("verification_runner"),
                scratch,
                output_path=run_dir / suite_output_file(artifact),
                # The configured test command as configured: the coordinator
                # narrows it by nothing here.
                scope=(),
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        if result.ran:
            result = replace(result, duration_seconds=_measured(started))
    (run_dir / artifact).write_text(
        json.dumps(result.as_record(), indent=2) + "\n", encoding="utf-8"
    )
    return result


def _clean_clone_passed(
    run_dir: Path, stage_name: str, artifact: str,
    duration_seconds: float | None = None,
) -> None:
    """The pass event. `duration_seconds` is the check's own, carried with the
    operation name so the cross-run duration log records the observation.
    """
    append_event(
        run_dir,
        "clean-clone suite passed with the story committed",
        kind="clean-clone-passed",
        stage=stage_name,
        artifacts=[artifact],
        **_timing(CLEAN_CLONE_OPERATION, duration_seconds),
    )


def _clean_clone_failed(
    run_dir: Path,
    stage: dict,
    artifact: str,
    destination: str,
    retry_count: int,
    max_retries: int,
    duration_seconds: float | None,
) -> None:
    """The reroute event, at module level rather than inline.

    Not only for reading length. tests/test_execution_history.py proves its
    own non-vacuity by deleting the first `retry_decision="retry",` line at the
    verification-failed branch's indentation; an inline clean-clone branch
    nests deeper, its own line contains that same indented text, and it sits
    earlier in the file, so the mutation would silently land here instead of
    where it was aimed.

    `duration_seconds` is the check's own — how long the suite took in the
    clone — not the stage's, so the entry is an observation of the clean-clone
    operation the cross-run duration log can record.
    """
    append_event(
        run_dir,
        f"clean-clone suite failed; retry {retry_count} of {max_retries} "
        f"rerouted to {destination}",
        kind="clean-clone-failed",
        stage=stage["name"],
        artifacts=[artifact],
        **_timing(CLEAN_CLONE_OPERATION, duration_seconds),
        retry_decision="retry",
        retry_stage=destination,
        retry_reason=(
            "the suite failed in a clean clone with the story committed and "
            "the retry ceiling was not reached"
        ),
    )


def _clean_clone_failures(output: str) -> str:
    """The failing tests named in the run's output, collapsed to one line.

    events.log's line format is frozen at one line, so the evidence is
    summarized rather than pasted. `FAILED` is the marker the output carries,
    not the command that produced it; when nothing in the output is
    recognizable the last non-empty line stands in, so the reason is never
    empty.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    failures = [line for line in lines if line.startswith("FAILED")]
    if not failures:
        return lines[-1] if lines else "the run produced no output"
    if len(failures) > 5:
        return "; ".join(failures[:5]) + f"; and {len(failures) - 5} more"
    return "; ".join(failures)


# --------------------------------------------------------------------------
# The suite run a stage declares
#
# A stage that authors validation used to run the target's whole suite inside
# its own turn. On a target whose suite outlasts the agent tool's per-call
# ceiling that cannot work: the command is forced into the background, the
# stage polls, and the turn ends on a result that only arrives if the turn
# continues. It is arithmetic rather than misbehaviour — the command cannot
# complete inside one call.
#
# The other whole-suite runs in a cycle are coordinator subprocesses, and
# neither has ever failed that way, because a subprocess cannot end its turn.
# So this is a third one: the stage authors and records, and the coordinator
# runs the configured test command after the turn ends and reads its exit
# status. Zero advances; non-zero brings the stage back in place.
#
# It is declared on a stage rather than written into the orchestration, the
# way revert_check, clean_clone, claim_support and correction_pass are, so a
# target whose stage should keep running its own suite declares nothing and is
# left exactly as it is.
#
# The exit status is the whole of what the coordinator reads. Nothing here
# parses the output for a count of tests, a framework's summary line or a list
# of failing names: that would be a contract on the target's test framework,
# which is the assumption the harness spent two stories removing.


def suite_run_declaration(stages: list[dict]) -> dict:
    """The workflow's suite-run declaration, wherever a stage carries it.

    Read off the loaded definition rather than looked up by stage name, in the
    shape `correction_pass_declaration` established, so a workflow that moves
    the declaration is followed and one that declares it nowhere disables the
    mechanism entirely. The first declaration wins; a definition carrying two
    would be declaring one mechanism twice.
    """
    for stage in stages:
        declaration = stage.get("suite_run")
        if declaration:
            return declaration
    return {}


def inspection_declaration(stages: list[dict]) -> dict:
    """The workflow's pre-stage inspection declaration, wherever it is carried.

    Read off the loaded definition in the shape `suite_run_declaration` and
    `correction_pass_declaration` established, so the trigger for inspecting a
    story's own change is a position in the *workflow* rather than a position
    in the coordinator: a workflow that declares none behaves exactly as it did
    before the key existed, and one that moves the declaration to another stage
    inspects before that stage instead, with no edit here.

    Its one reader outside the stage loop is `_complete`, which invokes the
    post-story inspection **only** where this returns nothing — so the two call
    sites cannot both fire and no run inspects the same diff twice. The stage
    loop reads each stage's own declaration rather than this, because which
    stage is about to be entered is exactly what it is deciding about.
    """
    for stage in stages:
        declaration = stage.get("inspection")
        if declaration:
            return declaration
    return {}


def suite_run_check(
    run_dir: Path,
    target_root: Path,
    config: dict,
    artifact: str,
    *,
    stage_name: str,
    attempt: int,
    try_number: int,
) -> CleanCloneResult:
    """Run the target's configured test command in its own working tree.

    No clone: the question this asks is whether the suite passes where the
    stage left the tree, which is exactly the run the stage used to make
    itself. The record it writes is shaped like the other two suite runs'
    because it is the same fact — a command, the executable it ran under, an
    exit status, the tail and the path to the whole output.

    The announcement is made here rather than at the call site, so it cannot
    happen without the check running: removing the `suite_run` declaration
    silences the announcement and the result together. `stage_name` is
    required and keyword-only for the reason `clean_clone_check`'s is — a
    defaulted argument would let a later call site silence the announcement by
    omission.

    The configured command is run exactly as written, with no substitution of
    its first word. `verification_runner` is deliberately not read here: that
    key exists so the clean-clone check can exercise an environment other than
    the one the developer works in and find an incompatibility before CI does,
    and this run is *in* the tree the developer works in — it is the run the
    stage used to make for itself, made where the stage would have made it.
    The record's `runner` is therefore the command's own first word.

    A target that configures no test command has nothing to run, which is
    reported as a check that did not run rather than as a pass; nothing is
    announced on that path, because nothing is about to take a suite run's
    worth of time.

    Every path here records an empty scope: this run is the configured test
    command as configured, and the coordinator narrows it by nothing. That
    says nothing about what the configured command covers.

    Two pairs are written, not one. The canonical pair at the run-directory
    root keeps its present meaning exactly — the most recent run — so every
    reader that already knows those names reads what it read before. Beside it
    a pair keyed by the stage, the attempt and the try is written through
    `retained_suite_result_file`, so no run's evidence is written over by the
    run after it: a self-route caused by a red suite points its reader at a
    path that still holds what that self-route saw. Each record's `output_path`
    names the output file beside it rather than the other pair's, and the
    result returned names the retained output, so the self-route record and the
    statement it hands the re-running stage cite what survives.

    `attempt` and `try_number` are required and keyword-only for the reason
    `stage_name` is: a defaulted argument would let a call site write an unkeyed
    run by omission, which is the overwrite this keying exists to remove.
    """
    command = config.get("test_command")
    if not command:
        return CleanCloneResult(
            ran=False,
            command="",
            runner="",
            scope=(),
            reason=(
                "the target configures no test_command, so there is no suite "
                "for the coordinator to run"
            ),
        )

    retained = retained_suite_result_file(artifact, stage_name, attempt, try_number)
    argv = shlex.split(command)
    runner = argv[0]
    named = shlex.join(argv)

    # The whole suite runs under the repository's turn, taken before the
    # announcement so a contended wait is announced ahead of the run and
    # released when the run returns.
    with suite_turn(run_dir, target_root, config, stage_name, artifact):
        _suite_rerun_started(
            run_dir,
            stage_name,
            artifact,
            "in the target's own working tree, because the stage that authors "
            "the validation no longer runs it",
            operation=SUITE_RUN_OPERATION,
        )
        # Timed around the command alone — after the turn and the
        # announcement — so the figure is the suite's own.
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                cwd=target_root,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            decided = CleanCloneResult(
                ran=False,
                command=named,
                runner=runner,
                scope=(),
                reason=f"the configured test command could not be run: {error}",
            )
            canonical_output = None
        else:
            output = result.stdout + result.stderr
            # The whole output is written twice, beside each record that
            # points at it, so following either record's own pointer reaches
            # that record's run rather than the other pair's file.
            canonical_output = _write_suite_output(
                run_dir / suite_output_file(artifact), output
            )
            decided = CleanCloneResult(
                ran=True,
                command=named,
                runner=runner,
                scope=(),
                exit_code=result.returncode,
                output_tail=output[-CLEAN_CLONE_OUTPUT_TAIL:],
                output_path=_write_suite_output(
                    run_dir / suite_output_file(retained), output
                ),
                duration_seconds=_measured(started),
            )
    # One record, written twice with each copy's output_path repointed at the
    # output file beside it, in the shape _archive_companion_output already uses
    # for an archived record. The returned result carries the retained pointer,
    # so a caller citing it cites the path the next run will not write over.
    retained_record = decided.as_record()
    canonical_record = dict(retained_record)
    if canonical_output is None:
        canonical_record.pop("output_path", None)
    else:
        canonical_record["output_path"] = canonical_output
    (run_dir / artifact).write_text(
        json.dumps(canonical_record, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / retained).write_text(
        json.dumps(retained_record, indent=2) + "\n", encoding="utf-8"
    )
    return decided


def _suite_run_completed(
    run_dir: Path, stage_name: str, artifact: str, ran: CleanCloneResult
) -> None:
    """The declared suite run's completion event, whatever it exited with.

    The run had no completion kind of its own: a green suite fell through to
    the advance and a red one to the carry-forward note, so the only thing that
    said how long it took was the gap between two stamps. This entry carries the
    exit code in its message and the run's own duration beside the operation
    name, which is what the cross-run duration log records the observation
    from. It decides nothing — the exit code is read where it always was — and
    it is spelled by this one function on the terms `_suite_rerun_started`'s
    docstring states.
    """
    append_event(
        run_dir,
        f"the suite the {stage_name} stage declares finished with exit code "
        f"{ran.exit_code}",
        kind="suite-run-completed",
        stage=stage_name,
        artifacts=[artifact],
        **_timing(SUITE_RUN_OPERATION, ran.duration_seconds),
    )


# --------------------------------------------------------------------------
# The revert check
#
# A stage's may_not_create declaration says what it must not *add*. It says
# nothing about modifying or deleting, deliberately: a legitimate change can
# break an existing test, and the suite has to stay green. What must not
# happen is the stage authoring its own validation. The two acts are separated
# exactly, with no judgement, by reverting: maintenance is by definition the
# edit without which the suite fails.
#
# So an edit under a prefix the stage declared it may not create is permitted
# iff reverting it makes the suite fail. This is the clean-clone operation with
# the governed paths restored to the state the stage found them in rather than
# applied.
#
# The state the stage found them in, not HEAD. HEAD is the right baseline only
# for files that existed before the story: on a retry the implementer routinely
# repairs a test the tester wrote earlier in the same run, a file with no
# version at HEAD because the coordinator commits once, at _complete. The
# pre-stage content of every governed prefix is therefore captured before the
# stage agent is invoked, and the check restores from that.
#
# Granularity. The check reverts every governed path in one run and decides on
# that one result — see the module docstring, which states plainly what that
# does not catch.
# --------------------------------------------------------------------------


def stage_baseline_dir(run_dir: Path, baseline: str, stage_name: str) -> Path:
    """Where one stage's pre-stage content is kept.

    The directory name comes off the loaded workflow declaration; only the
    keying is written here, and it is by stage alone because the baseline is
    what that stage first found. An attempt-keyed directory made the second
    attempt of a stage decide against the first attempt's own edits, which is
    not the question the revert check asks.
    """
    return run_dir / baseline / stage_name


def recorded_by_all_stages(run_dir: Path, stages: list[dict]) -> set[str]:
    """Every repository path any stage's changed-files record names.

    The one derivation of what a run touched, read off the records the writing
    stages declare in the workflow. Two questions are asked of it and both are
    asked here rather than each reading the records for itself: what *another*
    stage touched, which the baseline merge turns on, and what the *run*
    touched, which is what a post-story inspection takes into scope.

    A record that is absent or unreadable contributes nothing rather than
    raising — the question is what a stage is *known* to have touched, and an
    answer that cannot be established is not one. It names no stage and no
    artifact; both come off the loaded workflow.
    """
    paths: set[str] = set()
    for stage in stages:
        record = stage.get("changed_files")
        if not record:
            continue
        try:
            changed = json.loads((run_dir / record).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for group in ("modified", "created", "deleted"):
            paths.update(changed.get(group, []))
    return paths


def recorded_by_other_stages(
    run_dir: Path, stages: list[dict], stage_name: str
) -> set[str]:
    """Every repository path some *other* stage's changed-files record names.

    Whoever created a governed path is the fact the baseline merge turns on,
    and this is where the harness already records it: the records of every
    stage but this one are the account of what this stage did not write. It is
    the derivation above filtered by stage name, so the two share one reading
    of the records rather than reading them twice and being able to disagree.

    The route a stage was entered by is not consulted and must not be: a resume
    can change several things between two entries, so it is a proxy for
    authorship rather than the fact itself.
    """
    return recorded_by_all_stages(
        run_dir, [stage for stage in stages if stage["name"] != stage_name]
    )


def capture_stage_baseline(
    run_dir: Path,
    target_root: Path,
    baseline: str,
    stage_name: str,
    prefixes: list[str],
    *,
    accounted_for: set[str],
) -> Path:
    """Record what the tree held under a stage's governed prefixes before it ran.

    The file set is `git ls-files --cached --others --exclude-standard` under
    each prefix — the same tracked-plus-untracked set `_build_clone` carries
    into a clone — so a file an earlier stage of this run created and never
    committed is captured. Tracked files alone would miss exactly that file,
    which is the whole reason this exists.

    First seen wins, per path rather than per directory: a path the baseline
    already holds keeps the content it was first captured with, and a path new
    since the last capture is added at its current content. So a re-entered
    stage is decided against what it originally found rather than against its
    own completed edits.

    The merge is per path because reusing the earlier capture's directory whole
    would get the second half wrong. A governed path that first appears between
    two invocations of this stage — a test file another stage created in the
    meantime — would be absent from the baseline, and a path absent from the
    baseline is deleted in the clone rather than restored, because absent means
    it did not exist when the stage started.

    What a re-capture may add is narrowed by `accounted_for`, the paths another
    stage's changed-files record names. A path that appeared since the last
    capture and that no other stage's record accounts for is this stage's own
    partial work — what a crashed invocation left in the tree — and admitting
    it would decide the stage's next invocation against itself, which is the
    failure the first-seen rule exists to prevent. The two cases are one rule:
    the tester's file across a backward retry is accounted for and is merged in
    at its current content; the crash leftover across a self-route is not, and
    is left out. It is narrowed by who created the path rather than by the
    route the stage was entered on, because the harness records the first and a
    resume can defeat the second.

    A first capture admits everything, having nothing of this stage's to
    mistake for the tree's: the capture is taken before the stage is invoked.

    The directory is created even when it captures nothing, so its existence
    answers "was a baseline taken" and its absence is a distinct, reportable
    condition rather than an empty capture.

    It names no stage and no prefix: both come from the loaded workflow. The
    result is evidence — nothing routes on it, and it is not in state.json.
    """
    directory = stage_baseline_dir(run_dir, baseline, stage_name)
    recapture = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if not prefixes:
        return directory
    # One listing over every pathspec asked for, rather than one per pathspec
    # whose results are unioned. For a set of plain prefixes the two are the
    # same answer; for a confinement they are not, because its pathspecs are
    # the whole tree and a subtraction from it, and a union would add back
    # exactly what the subtraction removed.
    listed = _git(
        target_root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *prefixes,
    ).stdout
    for rel in filter(None, listed.split("\0")):
        source = target_root / rel
        if not source.is_file():
            continue
        destination = directory / rel
        if destination.exists():
            continue
        if recapture and rel not in accounted_for:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return directory


@dataclass(frozen=True)
class GovernedEdits:
    """The stage's own modifications and deletions its restrictions govern.

    `created` is not collected: the ownership check has already escalated on
    it, so anything reaching here is an edit to something that already existed.

    `wordings` states each restriction that matched in the workflow's own
    terms, so what the check reports about a confinement does not have to be
    read off a bare prefix. It carries a default because a caller with nothing
    to report constructs an empty set of edits, which has no restriction to
    word.

    `routed` is the wording of each matched restriction whose necessary edits
    are routed rather than permitted, at the same whole-set granularity the
    check decides at: the set is routed when any restriction that matched
    routes, because the check reverts and decides on the whole set and cannot
    tell which path a permission belonged to. Empty when nothing that matched
    routes, which is what every create-restricted stage's edits carry.
    """

    paths: tuple[str, ...]
    prefixes: tuple[str, ...]
    wordings: tuple[str, ...] = ()
    routed: tuple[str, ...] = ()

    @property
    def routes(self) -> bool:
        """Whether a permission over this set is routed rather than left."""
        return bool(self.routed)


def governed_edits(
    run_dir: Path,
    record_name: str,
    restrictions: list[StageRestriction],
    granted: list[str],
) -> GovernedEdits:
    """Read a stage's record for the edits the revert check decides on.

    Names no stage, no prefix and no sense; the caller passes the stage's
    restrictions whole and, beside them, the values the story grants that
    stage. Whether a restriction governs a path is the restriction's decision,
    so a confinement's edits reach this check by the same route a create
    restriction's do. A path a grant covers is skipped, so it is exempt from
    this check exactly as it is exempt from the ownership check, and by the
    same decision — grant_covers, which both read. Sorted, so the record and
    what the check reports are deterministic. Whether a permission over the
    set is routed is the matched restrictions' answer, carried on the set, so
    the call site asks the edits rather than any restriction's sense.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    paths, matched = set(), set()
    for group in ("modified", "deleted"):
        for path in changed.get(group, []):
            if grant_covers(granted, path):
                continue
            for restriction in restrictions:
                if restriction.governs(path):
                    paths.add(path)
                    matched.add(restriction)
    return GovernedEdits(
        tuple(sorted(paths)),
        tuple(sorted(restriction.prefix for restriction in matched)),
        tuple(sorted(restriction.wording for restriction in matched)),
        tuple(sorted(restriction.wording for restriction in matched
                     if restriction.routes_necessary_edits)),
    )


# --------------------------------------------------------------------------
# A stage records every file it touched
#
# A changed-files record is what the whole governance layer reads:
# recorded_by_all_stages derives what a run touched from the records, and
# recorded_by_other_stages derives from the same reading which stage created a
# governed path — which is what the baseline merge and the revert check turn
# on. A file missing from a record is therefore a file the revert check does
# not consider governed, a file the post-story inspection never takes into
# scope, and a file no reader of the run can see was touched at all.
#
# So the coordinator takes a signature of the target tree before each stage
# that declares a record is invoked, compares it after that stage's turn, and
# reports any path the stage changed that its record does not name. It amends
# no record: a record is the writing stage's own account of its own work, and
# the only stage that may correct one is the stage that wrote it. An omission
# therefore re-enters that stage as a self-route on a bookkeeping cause — the
# stage did the work and did not record it, which is the same fact the two
# artifact causes state.
#
# Detection is placed before the blocked-path, ownership and revert checks
# deliberately: all three read the record, and an omitted governed path is
# exactly how an edit escapes the check that decides whether it was forced.


def stage_signature_file(stage_name: str) -> str:
    """Where one stage's pre-stage tree signature is written.

    The one place the name is shaped, in the idiom self_route_result_file and
    prompt_file already establish, and keyed by stage so a coordinator process
    that exits and resumes compares against the signature the first invocation
    took. It lives in the run directory rather than in memory for that reason.
    """
    return f"tree-signature-{stage_name}.json"


def _tracked_and_untracked(target_root: Path) -> list[str]:
    """The repository-relative paths git reports as tracked or untracked-not-ignored.

    The same set capture_stage_baseline and _build_clone already use, so a
    path git excludes as ignored is out before anything else looks at it —
    which is what keeps a suite run inside a stage's turn from reading as that
    stage's omission. `-z` so a path containing a newline cannot corrupt the
    listing.
    """
    listed = _git(
        target_root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    ).stdout
    return [rel for rel in listed.split("\0") if rel]


def _content_hash(path: Path) -> str | None:
    """A digest of one file's content, or None where there is nothing to read.

    Hashed here rather than by shelling to `git hash-object`, so a path
    containing a newline cannot corrupt a listing on the way back. A path that
    is not a readable regular file — a directory, a dangling symlink, a file
    removed between the listing and the read — has no content to compare, and
    answering None is what keeps it out of the signature rather than raising.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def tree_signature(target_root: Path) -> dict[str, str]:
    """What the target tree holds now: every readable path mapped to its content.

    Names no stage and no prefix. Sorted by construction on the way out, so a
    signature written twice over one tree is byte-identical.
    """
    signature: dict[str, str] = {}
    for rel in _tracked_and_untracked(target_root):
        digest = _content_hash(target_root / rel)
        if digest is not None:
            signature[rel] = digest
    return dict(sorted(signature.items()))


def capture_tree_signature(
    run_dir: Path,
    target_root: Path,
    stage_name: str,
    attempt: int,
    entry: int,
    *,
    accounted_for: set[str],
) -> dict[str, str]:
    """Record what the tree held before a stage ran, first-seen-wins.

    First seen wins within one attempt, mirroring capture_stage_baseline's
    rule and for its reason: a signature re-taken at a self-route would hold
    the stage's own uncorrected edits, and the omission this exists to find
    would become invisible — a re-entered stage that ignores the report would
    pass rather than being reported again.

    A new attempt takes a fresh one, and that is where this parts company with
    the baseline beside it, because the two answer different questions. The
    baseline asks what the stage found, once, so the revert check decides an
    attempt's edits against the tree before any of them; this asks whether the
    record at the run directory root accounts for the attempt it describes,
    and each attempt writes its own record and is decided on it. Carrying one
    signature across a retry would make a retried stage re-declare edits an
    earlier attempt already recorded and had adjudicated, which would widen
    what the revert check governs on the later attempt — an edit already
    decided being re-decided in company, and this check does not decide
    whether an edit was permitted. A new entry of the run takes a fresh one for
    that reason and one of its own: a resume restores the attempt allowance, so
    the attempt number alone repeats, and the tree a resumed stage starts from
    is the tree the developer committed before deciding to resume — work nobody
    may attribute to a stage. Both are stored beside the paths rather than
    written into the filename, so nothing here is keyed by a counter a resume
    zeroes and no name has to be moved when one is.

    What a re-capture within an attempt may admit is narrowed by
    `accounted_for`, the paths some other stage's changed-files record names,
    so a file another stage created or removed between two invocations of this
    one is taken at its current state and is not attributed here, while this
    stage's own crash leftovers are not admitted and stay reportable. It is
    narrowed by who changed the path rather than by the route the stage was
    entered on, because the harness records the first and a resume can defeat
    the second.

    A first capture admits everything, having nothing of this stage's to
    mistake for the tree's: it is taken before the stage is invoked.

    The result is written into the run directory under a name one function
    shapes, so a coordinator process that exits and resumes compares against
    what the first invocation took rather than against a fresh reading.
    """
    destination = run_dir / stage_signature_file(stage_name)
    now = tree_signature(target_root)
    signature = now
    if destination.exists():
        held = json.loads(destination.read_text(encoding="utf-8"))
        if (held.get("attempt"), held.get("entry")) == (attempt, entry):
            signature = held.get("paths", {})
            for path in accounted_for:
                if path in now:
                    signature[path] = now[path]
                else:
                    signature.pop(path, None)
            signature = dict(sorted(signature.items()))
    destination.write_text(
        json.dumps(
            {"attempt": attempt, "entry": entry, "paths": signature}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return signature


#: The groups a changed-files record uses, and therefore the groups a
#: derivation of what a stage actually changed is expressed in. Written once
#: so the derivation, the comparison against a record and the report all read
#: one list.
CHANGED_FILE_GROUPS = ("modified", "created", "deleted")


def coordinator_written_prefixes(config: dict) -> list[str]:
    """The directories the coordinator writes into during a stage's turn.

    The run directory, the raw agent log and the cross-run history: each is the
    coordinator's own write, made while a stage is running and named by that
    stage's turn rather than by anything the stage did, so none of them is that
    stage's to record. They are subtracted from what the completeness check
    reports for the reason the repository-wide blocked paths are.

    Two of the three are gitignored here and the third is blocked by this
    repository's rules, so this deployment would meet none of them — but
    nothing in the harness establishes any of that for a target. `l5-init`
    writes no ignore rule, and a target's blocked paths are the target's to
    declare, so a target that tracks its logs or does not block its history
    would otherwise have the coordinator's own writes reported against every
    stage of every run. Blocking them is a statement about who may write them;
    this is a statement about who did, and the check needs the second whether
    or not a target has made the first. The locations come off the target's own
    configuration, spelled as the sites that write them spell it, so a target
    that relocates any of the three is covered by the same subtraction.
    """
    return [
        f"{str(config.get(key, default)).rstrip('/')}/"
        for key, default in (
            ("runs_dir", ".harness/runs"),
            ("logs_dir", ".harness/logs"),
            ("history_dir", harness_config.DEFAULT_HISTORY_DIR),
        )
    ]


def tree_changes(
    signature: dict[str, str], target_root: Path, blocked: list[str]
) -> dict[str, list[str]]:
    """What changed in the target tree since a signature was taken.

    Expressed in the groups the changed-files schema already uses, so what is
    derived and what a record declares are the same vocabulary. `blocked` is
    subtracted through the same reading the blocked-path check makes, so the
    coordinator's own writes during a stage's turn do not read as that stage's
    work. A path git excludes as ignored is already out, because the listing
    this compares against is the tracked-plus-untracked set.

    Names no stage and no prefix: the caller passes the signature it captured
    for one stage and the prefixes it read off the rules and the config.
    """
    now = tree_signature(target_root)
    groups: dict[str, list[str]] = {
        "modified": [
            path
            for path, digest in now.items()
            if path in signature and signature[path] != digest
        ],
        "created": [path for path in now if path not in signature],
        "deleted": [path for path in signature if path not in now],
    }
    return {
        group: sorted(path for path in paths if not is_blocked(path, blocked))
        for group, paths in groups.items()
    }


def unrecorded_changes(
    run_dir: Path, record_name: str, changes: dict[str, list[str]]
) -> dict[str, list[str]]:
    """The paths a stage changed that its own changed-files record does not name.

    Omissions only. A record naming a path the tree does not show as changed is
    not reported: the question is whether the record accounts for what the
    stage did, and a stage that recorded more than the tree can confirm has not
    hidden anything from the readers this protects.

    Membership is tested against everything the record names, across all three
    groups rather than group by group, because a path recorded under a
    different heading is a classification quibble and not an omission — the
    readers this exists for take the union.

    A record that cannot be read names nothing, so everything the tree shows is
    reported; the schema check above this has already escalated on a record
    that is not well-formed, and this reports rather than raising for the
    reason every other reader of these records does.
    """
    try:
        changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        changed = {}
    recorded = {
        path for group in CHANGED_FILE_GROUPS for path in changed.get(group, [])
    }
    return {
        group: [path for path in changes.get(group, []) if path not in recorded]
        for group in CHANGED_FILE_GROUPS
    }


def describe_unrecorded(omitted: dict[str, list[str]]) -> str:
    """The omitted paths, grouped, for a reason a human reads.

    Only the groups that have something in them appear, so a report never
    names a heading with nothing under it.
    """
    return "; ".join(
        f"{group} {', '.join(paths)}"
        for group in CHANGED_FILE_GROUPS
        if (paths := omitted.get(group))
    )


#: Where a nominated test is substituted into the target's configured
#: selection command. A literal the harness looks for and replaces, and the
#: whole of what it knows about that command: the selector syntax on either
#: side of it belongs to the target.
TEST_SUBSTITUTION = "{test}"


def nominated_test(run_dir: Path, record_name: str) -> str | None:
    """The test a stage's record nominates as failing without its change.

    The field is optional, so a record that carries none, carries a
    non-string, or carries only whitespace nominates nothing — none of which
    is an error, because a nomination that is not there costs nothing and
    decides nothing. Names no stage: the caller passes the record it already
    read for the governed edits.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    nomination = changed.get("test_that_fails_without_this_change")
    if not isinstance(nomination, str) or not nomination.strip():
        return None
    return nomination.strip()


@dataclass(frozen=True)
class Nomination:
    """What the nominated test decided, and when it decided nothing, why.

    `short_circuited` is the only permission a nomination can establish, and
    it takes both exit codes to establish it: the selector must pass on the
    tree the stage left and fail with the governed edits reverted. Every other
    outcome sets `fell_through_because` and leaves the whole suite to decide,
    which is why a stage nominating the test that judges its own edit can buy
    nothing by nominating badly — a bad nomination costs one extra selector
    run over today's runtime and changes no verdict.
    """

    short_circuited: bool
    test: str | None = None
    command: str | None = None
    applied_exit_code: int | None = None
    reverted_exit_code: int | None = None
    fell_through_because: str | None = None

    def as_record(self) -> dict:
        record: dict = {"short_circuited": self.short_circuited}
        optional = {
            "test": self.test,
            "command": self.command,
            "applied_exit_code": self.applied_exit_code,
            "reverted_exit_code": self.reverted_exit_code,
            "fell_through_because": self.fell_through_because,
        }
        record.update({key: value for key, value in optional.items() if value is not None})
        return record


@dataclass(frozen=True)
class RevertedEdits:
    """What a refused verdict undid in the target working tree, and what it kept.

    Present on the record only when a refusal was acted on, so its absence is
    the ordinary case — a permitted check undoes nothing and has nothing to
    say here. It is the correction *beside* the stage's changed-files record
    rather than an edit to it: an attestation is not rewritten behind its
    author, so what the stage said it changed still says exactly that and this
    is where a reader learns which of those edits no longer stand.
    """

    restored: tuple[str, ...]
    removed: tuple[str, ...]
    discarded: str | None = None
    kept: tuple[str, ...] = ()

    def as_record(self) -> dict:
        record: dict = {
            "restored": list(self.restored),
            "removed": list(self.removed),
            "kept": list(self.kept),
        }
        if self.discarded is not None:
            record["discarded"] = self.discarded
        return record


@dataclass(frozen=True)
class RevertCheckResult:
    """What the revert check did, as it is recorded in the run directory.

    `permitted` is absent from the record when the check reached no verdict,
    following the optional-by-absence convention the other coordinator-written
    records use: a check that could not run decided nothing, and null would
    claim it decided something.

    `result.ran` keeps meaning what it has always meant — the configured test
    command ran in the clone with the edits reverted — so a check the
    nomination decided records it false while carrying `permitted` true. The
    two are therefore no longer the same question, which is why the call site
    escalates on a check that reached no verdict rather than on one that did
    not run.

    The record carries one of three dispositions. `permitted` true alone: the
    suite failed with the edits reverted, so they were needed and they stand.
    `permitted` false with a `reverted` block: the suite passed without them,
    so they were undone and the block names what was restored, what was
    removed and where the stage's own versions were kept. `permitted` true
    with a `reverted` block *and* a `routed` key: the edits were needed, and
    were undone anyway because the restriction that governs them says they
    are another stage's work — `routed` carries that restriction's wording as
    `StageRestriction.wording` composes it. The tree the run carries on with
    then lacks work it needs, which is what delivers the edit to the stage
    that owns it rather than leaving it where it was written. The two
    undoings are told apart by `permitted`, and the routed permission from
    the standing one by `routed`.
    """

    result: CleanCloneResult
    paths: tuple[str, ...]
    permitted: bool | None
    baseline: str | None = None
    nomination: Nomination | None = None
    reverted: RevertedEdits | None = None
    routed: str | None = None

    def as_record(self) -> dict:
        record = {"ran": self.result.ran, "paths": list(self.paths)}
        record.update(
            {key: value for key, value in self.result.as_record().items() if key != "ran"}
        )
        if self.permitted is not None:
            record["permitted"] = self.permitted
        if self.baseline is not None:
            record["baseline"] = self.baseline
        if self.nomination is not None:
            record["nomination"] = self.nomination.as_record()
        if self.reverted is not None:
            record["reverted"] = self.reverted.as_record()
        if self.routed is not None:
            record["routed"] = self.routed
        return record


def _run_selection(
    target_root: Path,
    config: dict,
    command: str,
    *,
    scope: tuple[str, ...],
    revert: tuple[str, ...] = (),
    baseline: Path | None = None,
) -> CleanCloneResult:
    """Run one selection command in a clone, through the suite's own builder.

    The selector and the suite differ in the command and in nothing else: the
    same clone builder, the same first-word swap for the configured
    verification_runner, the same environment linked into the clone. That is
    what makes the applied and reverted runs differ only in what is reverted,
    which is the whole of what the nomination is asked to establish.

    A clone that cannot be built is reported as a run that did not happen,
    with the reason, rather than raising: the caller falls through to the
    whole suite on it, and a selector that cannot be run must never be read as
    having decided anything.

    `scope` is what this run was narrowed to — the nomination the caller
    substituted — carried on the result so a filtered invocation and an
    unfiltered one are distinguishable from the record alone. It is required
    and keyword-only: a selector run that recorded an empty scope would claim
    it ran the configured command unnarrowed, which is the one thing it never
    does.
    """
    scratch = Path(tempfile.mkdtemp(prefix="l5-selection-"))
    try:
        return run_clean_clone(
            target_root,
            command,
            config.get("verification_runner"),
            scratch,
            revert=list(revert),
            baseline=baseline,
            scope=scope,
        )
    except (RuntimeError, OSError) as error:
        return CleanCloneResult(
            ran=False,
            command=command,
            runner=config.get("verification_runner") or command,
            scope=scope,
            reason=f"the clone could not be built: {error}",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_nomination(
    target_root: Path,
    config: dict,
    nomination: str | None,
    paths: tuple[str, ...],
    baseline: Path,
    announce: Callable[[], None] | None = None,
) -> Nomination:
    """Decide whether the nominated test may stand in for the whole suite.

    The check establishes a *failure*, and one failing test proves a failing
    suite while no number of passing tests proves a passing suite. That
    asymmetry is what lets a subset stand in for the whole here and nowhere
    the verdict runs the other way.

    The nomination must be shown to discriminate before it is believed. A
    selector that fails to collect exits non-zero exactly as a failing test
    does, and telling those apart means reading *which* non-zero, which is
    framework-specific and forbidden here — the harness reads exit statuses
    and nothing else. So the selector runs twice: once in a clone with the
    working tree applied, where it must pass, and once in the clone with the
    governed paths reverted, where it must fail. Pass-then-fail is the
    permission; every other outcome falls through to the whole suite with the
    reason recorded, and none of them is a permission.

    The reverted run is reached only from a passing applied run, so a bad
    nomination costs one extra selector run over today's runtime and buys
    nothing — which is what makes it harmless for the stage judging its own
    edit to be the one nominating.

    RESIDUAL RISK, stated here because this is the check and this is where a
    reader would otherwise have to discover it: a test that fails in isolation
    and would have passed inside the full suite is a **false permit** — the
    edits are reported forced when the suite standing in for them would have
    passed. The applied run narrows it, since a test that also fails in
    isolation on the tree the stage left establishes nothing and falls
    through, and it does not close it: a test whose isolated failure depends
    on the reverted content alone survives both runs. Nothing here detects
    that, and widening the check to detect it would mean running the suite,
    which is the cost the short-circuit exists to avoid.

    This is the executable counterpart of a plan's reverting_breaks_the_suite
    declaration and **not** the same thing. That one is written at plan time,
    before the edit exists, as prose a human reviewer weighs, and it leaves
    this check governing the path. This one is named after the edit exists and
    is decided by running it.
    """
    if nomination is None:
        return Nomination(
            False,
            fell_through_because=(
                "the stage's changed-files record nominated no test that fails "
                "without this change"
            ),
        )

    selection = config.get("test_selection_command")
    if not selection:
        return Nomination(
            False,
            test=nomination,
            fell_through_because=(
                "the target configures no test_selection_command, so there is "
                "no command to substitute the nomination into"
            ),
        )
    if TEST_SUBSTITUTION not in selection:
        return Nomination(
            False,
            test=nomination,
            fell_through_because=(
                f"the configured test_selection_command carries no "
                f"{TEST_SUBSTITUTION} substitution point, so the nomination "
                f"could not be substituted into it"
            ),
        )

    substituted = selection.replace(TEST_SUBSTITUTION, nomination)
    try:
        argv = shlex.split(substituted)
    except ValueError as error:
        argv = []
        problem = str(error)
    else:
        problem = "" if argv else "it is empty"
    if problem:
        return Nomination(
            False,
            test=nomination,
            command=substituted,
            fell_through_because=(
                f"the nomination substituted into the configured "
                f"test_selection_command could not be run at all: {problem}"
            ),
        )

    # Announced here rather than at the top: every fall-through above this
    # line runs nothing, and a wait announced before a check that turns out to
    # have nothing to run is the announcement claiming a cost it did not take.
    if announce is not None:
        announce()

    applied = _run_selection(target_root, config, substituted, scope=(nomination,))
    if not applied.ran:
        return Nomination(
            False,
            test=nomination,
            command=applied.command,
            fell_through_because=(
                f"the nomination could not be run on the tree the stage left: "
                f"{applied.reason}"
            ),
        )
    if applied.exit_code != 0:
        return Nomination(
            False,
            test=nomination,
            command=applied.command,
            applied_exit_code=applied.exit_code,
            fell_through_because=(
                f"the nomination did not pass on the tree the stage left "
                f"(exit {applied.exit_code}), so it discriminates nothing — a "
                f"test present only in the new version of a reverted file "
                f"fails here for the same reason a broken selector does"
            ),
        )

    reverted = _run_selection(
        target_root,
        config,
        substituted,
        scope=(nomination,),
        revert=paths,
        baseline=baseline,
    )
    if not reverted.ran:
        return Nomination(
            False,
            test=nomination,
            command=reverted.command,
            applied_exit_code=applied.exit_code,
            fell_through_because=(
                f"the nomination could not be run with the edits reverted: "
                f"{reverted.reason}"
            ),
        )
    if reverted.exit_code == 0:
        return Nomination(
            False,
            test=nomination,
            command=reverted.command,
            applied_exit_code=applied.exit_code,
            reverted_exit_code=reverted.exit_code,
            fell_through_because=(
                "the nomination passed with the edits reverted, so it does not "
                "show they were needed"
            ),
        )

    return Nomination(
        True,
        test=nomination,
        command=reverted.command,
        applied_exit_code=applied.exit_code,
        reverted_exit_code=reverted.exit_code,
    )


def revert_check(
    run_dir: Path,
    target_root: Path,
    config: dict,
    artifact: str,
    paths: tuple[str, ...],
    baseline: Path | None = None,
    *,
    stage_name: str,
    nomination: str | None = None,
) -> RevertCheckResult:
    """Run the suite once with every governed path reverted, and record it.

    Shaped like clean_clone_check: a scratch directory outside the target
    repository, the shared runner, and removal in a finally whatever the
    result. The decision is the suite's exit status — a non-zero exit means
    the edits were needed, which is what makes them maintenance rather than
    authorship.

    The paths are restored to the content `baseline` holds for them, and a
    governed path the baseline does not hold is deleted in the clone rather
    than skipped: skipping decides nothing and would report a permission the
    check never established.

    Two things stop the check from running, and both are reported as a check
    that reached no verdict, with the reason, rather than as a permission: a
    stage that declares the check with no baseline captured, and a clone that
    cannot be built at all.

    A `nomination` is asked first, when there is one. Passing on the tree the
    stage left and failing with the governed paths reverted permits the edits
    with the configured test command never run; every other outcome falls
    through to the whole suite below and today's verdict, unchanged, with the
    reason recorded. What is asked, what is reverted and what a verdict means
    are the same as they were — only how many tests a permission can be
    established from moved.

    The announcement is made inside the branch that builds the clone, not at
    the top: the no-baseline path runs no suite and so has nothing to announce.
    Its quiet used to look fine only because the check sits between the
    implementer's started and completed lines, so a reader already had a line
    on screen saying work was in progress — hidden by position rather than by
    design, and the silence would reappear the moment it moved.
    """
    command = config["test_command"]
    runner = config.get("verification_runner") or shlex.split(command)[0]
    resolved = baseline if baseline is not None and baseline.is_dir() else None
    decided_by_nomination: Nomination | None = None

    if resolved is None:
        result = CleanCloneResult(
            ran=False,
            command=command,
            runner=runner,
            scope=(),
            reason=(
                "no baseline was captured for the stage, so there is no state "
                f"to revert the edits to: {baseline}"
            ),
        )
    else:
        decided_by_nomination = run_nomination(
            target_root,
            config,
            nomination,
            paths,
            resolved,
            announce=lambda: _suite_rerun_started(
                run_dir,
                stage_name,
                artifact,
                f"restricted to the nominated {nomination}, twice — once on "
                f"the tree {stage_name} left and once with "
                f"{', '.join(paths)} reverted",
                cost="this takes seconds rather than a suite run",
            ),
        )
        if decided_by_nomination.short_circuited:
            decided = RevertCheckResult(
                result=CleanCloneResult(
                    ran=False,
                    command=command,
                    runner=runner,
                    # The coordinator narrowed the configured command by
                    # nothing on this path — it never ran it at all. What the
                    # selector runs were narrowed to is nomination.test, which
                    # is where a reader finds it.
                    scope=(),
                    reason=None,
                ),
                paths=paths,
                permitted=True,
                baseline=str(resolved),
                nomination=decided_by_nomination,
            )
            return _record_revert_check(run_dir, artifact, decided)

        # Only the whole-suite fallthrough takes the repository's turn: the
        # selector runs above take seconds and run no suite. The turn is taken
        # before the announcement, so a contended wait is announced ahead of
        # the run, and released when the run returns.
        with suite_turn(run_dir, target_root, config, stage_name, artifact):
            _suite_rerun_started(
                run_dir,
                stage_name,
                artifact,
                f"with {', '.join(paths)} reverted, to decide whether those "
                "edits were needed",
                operation=REVERT_CHECK_OPERATION,
            )
            # Timed from here, so the figure is the whole-suite fallthrough's
            # own: the nomination's selector runs above are not timed.
            started = time.monotonic()
            scratch = Path(tempfile.mkdtemp(prefix="l5-revert-check-"))
            try:
                result = run_clean_clone(
                    target_root,
                    command,
                    config.get("verification_runner"),
                    scratch,
                    revert=list(paths),
                    baseline=resolved,
                    output_path=run_dir / suite_output_file(artifact),
                    # The whole suite, narrowed by nothing. What was reverted
                    # is `paths`, which is a different fact and has its own
                    # field.
                    scope=(),
                )
            except (RuntimeError, OSError) as error:
                result = CleanCloneResult(
                    ran=False,
                    command=command,
                    runner=runner,
                    scope=(),
                    reason=(
                        "the clone with the edits reverted could not be built: "
                        f"{error}"
                    ),
                )
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            if result.ran:
                result = replace(result, duration_seconds=_measured(started))

    decided = RevertCheckResult(
        result=result,
        paths=paths,
        permitted=(result.exit_code != 0) if result.ran else None,
        baseline=str(resolved) if resolved is not None else None,
        nomination=decided_by_nomination,
    )
    return _record_revert_check(run_dir, artifact, decided)


def _record_revert_check(
    run_dir: Path, artifact: str, decided: RevertCheckResult
) -> RevertCheckResult:
    """Write the revert check's record, and say what was written.

    One writer, so the record the check leaves and the record a refusal
    rewrites with what it undid are the same serialization rather than two.
    """
    (run_dir / artifact).write_text(
        json.dumps(decided.as_record(), indent=2) + "\n", encoding="utf-8"
    )
    return decided


def revert_refused_edits(
    run_dir: Path,
    target_root: Path,
    paths: tuple[str, ...],
    baseline: Path,
    discarded: str | None,
) -> RevertedEdits:
    """Undo a refused stage's governed edits in the target working tree.

    Safe by construction rather than by judgement. A refusal means the check
    built a clone with exactly these paths restored to what the stage found and
    watched the suite pass there, so reproducing that content in the working
    tree reproduces the tree the proof was taken on — which is why the restore
    goes through restore_from_baseline, the same function the clone build
    calls, rather than through a second copy of the rule that could leave a
    tree the proof does not cover.

    The stage's own version of each path is written under the run-directory
    directory the declaration names before anything is undone, so the work a
    refusal discards survives in the run's evidence and a reviewer can read
    what the stage said. A path the stage deleted has no version in the tree to
    keep, and is recorded as reverted without one.
    """
    kept: list[str] = []
    if discarded is not None:
        for rel in paths:
            source = target_root / rel
            if not source.is_file():
                continue
            destination = run_dir / discarded / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            kept.append(rel)
    restoration = restore_from_baseline(baseline, target_root, paths)
    return RevertedEdits(
        restored=restoration.restored,
        removed=restoration.removed,
        discarded=discarded,
        kept=tuple(kept),
    )


def _revert_check_reverted(
    run_dir: Path,
    stage_name: str,
    artifact: str,
    edits: GovernedEdits,
    reverted: RevertedEdits,
    duration_seconds: float | None = None,
) -> None:
    """The refusal event. `duration_seconds` is the whole-suite fallthrough's
    own, so the entry is an observation of the revert-check operation.
    """
    undone = ", ".join(edits.paths)
    where = (
        f", the stage's own versions kept under {reverted.discarded}"
        if reverted.discarded
        else ""
    )
    append_event(
        run_dir,
        f"{stage_name} edits governed by {'; '.join(edits.wordings)} were not "
        f"needed: the suite passes with {undone} reverted, so they were "
        f"reverted in the working tree{where}",
        kind="revert-check-reverted",
        stage=stage_name,
        artifacts=[artifact],
        **_timing(REVERT_CHECK_OPERATION, duration_seconds),
    )


def _revert_check_routed(
    run_dir: Path,
    stage_name: str,
    artifact: str,
    edits: GovernedEdits,
    reverted: RevertedEdits,
) -> None:
    """Say that a confined stage's necessary outside edits were undone.

    Shaped like `_revert_check_reverted` and distinct from it on purpose: a
    reader of the log alone has to be able to see that a confined stage wrote
    outside its confinement, that the tree needed what it wrote, and that the
    work was undone to reach the stage that owns it rather than because it
    was unneeded.
    """
    undone = ", ".join(edits.paths)
    where = (
        f", the stage's own versions kept under {reverted.discarded}"
        if reverted.discarded
        else ""
    )
    append_event(
        run_dir,
        f"{stage_name} edits governed by {'; '.join(edits.routed)} were needed "
        f"— the suite fails with {undone} reverted — but are another stage's "
        f"work, so they were reverted in the working tree{where} and routed "
        f"rather than permitted",
        kind="revert-check-routed",
        stage=stage_name,
        artifacts=[artifact],
    )


def _revert_check_permitted(
    run_dir: Path, stage_name: str, artifact: str, edits: GovernedEdits,
    duration_seconds: float | None = None,
) -> None:
    """The permission event. `duration_seconds` is the whole-suite
    fallthrough's own and is None where the nomination decided it, so a
    permission the selector established leaves no duration record.
    """
    append_event(
        run_dir,
        f"{stage_name} edits governed by {'; '.join(edits.wordings)} permitted: "
        f"the suite fails with {', '.join(edits.paths)} reverted",
        kind="revert-check-permitted",
        stage=stage_name,
        artifacts=[artifact],
        **_timing(REVERT_CHECK_OPERATION, duration_seconds),
    )


# --------------------------------------------------------------------------
# The suite census
#
# The ownership check and the revert check between them make one assumption:
# that every legitimate edit to a stage's validation is forced by a change
# elsewhere. That holds over a workflow whose stages implement a story and
# fails over one whose whole correctness claim is that behaviour is unchanged,
# because there the edit to the validation *is* the work. A workflow for such
# work drops both declarations — and dropping them removes enforcement rather
# than replacing it, leaving no guard at all against the threat that kind of
# work actually carries: not authoring the validation that judges your own
# work, but weakening the validation that already exists.
#
# So a third proxy stands in their place. The target supplies a command that
# prints a JSON object of counter names to integers; the coordinator runs it
# in a clone at the stage's baseline and again in the tree the stage left, and
# refuses the run when a counter present at the baseline is missing afterwards
# or has decreased. The coordinator never learns what a counter means. The
# target chooses the counters and phrases them so that a larger number is a
# stronger suite, which is the whole of what keeps the harness free of any
# knowledge of the target's test framework: no framework summary line is read,
# no framework command is run, and no counter name is interpreted.
#
# The proxy is deterministic, and it is narrow. It assumes weakening shows up
# as a smaller count, so a change that leaves every counter where it is and
# loosens an assertion in place passes. That is stated here, in the census
# result's own schema description, and in the target's census command, rather
# than left for a reader to infer coverage the check does not have.

#: How much of a failing census command's output a refusal carries.
CENSUS_OUTPUT_TAIL = 2000


@dataclass(frozen=True)
class CensusRegression:
    """One counter the baseline carried that the later census did not keep.

    `after` is None when the later census does not carry the counter at all,
    which is its own kind of regression and is reported as one: a target that
    stops counting something has stopped watching it.
    """

    counter: str
    baseline: int
    after: int | None

    def as_record(self) -> dict:
        record: dict = {"counter": self.counter, "baseline": self.baseline}
        if self.after is not None:
            record["after"] = self.after
        return record

    def describe(self) -> str:
        if self.after is None:
            return (
                f"{self.counter} was {self.baseline} at the baseline and the "
                f"later census does not carry it"
            )
        return (
            f"{self.counter} fell from {self.baseline} at the baseline to "
            f"{self.after}"
        )


@dataclass(frozen=True)
class CensusResult:
    """What the suite census did, as it is recorded in the run directory.

    Optional fields are expressed by absence in the written record rather than
    by null, matching the other coordinator-written records: a census that
    could not be taken has no counts and no verdict, only a reason.

    `duration_seconds` is not written into the record, on the terms
    `CleanCloneResult`'s is not: it travels to the completion entry, where the
    cross-run duration log reads it, and is None where no census was taken.
    """

    ran: bool
    command: str
    baseline: dict[str, int] | None = None
    after: dict[str, int] | None = None
    baseline_path: str | None = None
    permitted: bool | None = None
    regressions: tuple[CensusRegression, ...] = ()
    reason: str | None = None
    duration_seconds: float | None = None

    def as_record(self) -> dict:
        record: dict = {"ran": self.ran, "command": self.command}
        optional = {
            "baseline": self.baseline,
            "after": self.after,
            "baseline_path": self.baseline_path,
            "permitted": self.permitted,
            "reason": self.reason,
        }
        record.update({key: value for key, value in optional.items() if value is not None})
        if self.ran:
            record["regressions"] = [item.as_record() for item in self.regressions]
        return record


def census_regressions(
    baseline: dict[str, int], after: dict[str, int]
) -> tuple[CensusRegression, ...]:
    """Compare two censuses over the counters the *baseline* carried.

    Every counter the baseline carries must still be there and must not have
    fallen. A counter the later census reports and the baseline did not is no
    violation and is not looked at, so a target may add a counter without the
    addition reading as one.

    This is the one place the comparison is made, and it reads no counter name:
    the direction is the target's to establish by naming its counters so that
    larger is stronger.
    """
    regressions = []
    for counter, before in sorted(baseline.items()):
        if counter not in after:
            regressions.append(CensusRegression(counter, before, None))
        elif after[counter] < before:
            regressions.append(CensusRegression(counter, before, after[counter]))
    return tuple(regressions)


def census_paths(target_root: Path, baseline: Path, prefixes: list[str]) -> list[str]:
    """The files a census clone restores, from a stage's declared paths.

    The clone builder restores and deletes *files*, because the revert check
    hands it the file paths a stage's own record named. A census declares
    directories instead — the suite is the thing being counted, not one edit
    to it — so the two sides are enumerated here and handed over as one set:
    every file the tree now holds under a declared path, so one the stage
    added is deleted in the clone, and every file the baseline captured under
    it, so one the stage deleted is restored. The union is what makes the
    clone hold the declared paths exactly as the stage first found them.

    A declared path that names a single file rather than a directory works
    unchanged, which is why the capture is asked whether it is a file before
    it is walked as a directory.
    """
    paths: set[str] = set()
    for prefix in prefixes:
        listed = _git(
            target_root,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            prefix,
        ).stdout
        paths.update(filter(None, listed.split("\0")))
        captured = baseline / prefix
        if captured.is_file():
            paths.add(prefix)
        elif captured.is_dir():
            for path in captured.rglob("*"):
                if path.is_file():
                    paths.add(path.relative_to(baseline).as_posix())
    return sorted(paths)


def _take_census(command: str, cwd: Path, where: str) -> tuple[dict[str, int] | None, str]:
    """Run the target's census command in one place and read what it printed.

    Returns the object and an empty reason, or None and the reason it could
    not be read. Each of the three ways this fails — a non-zero exit, output
    that is not a JSON object, a value that is not an integer — is reported as
    itself, because a refusal saying only that the census failed leaves the
    developer to run the command by hand to find out which.

    A bool is refused along with every other non-integer: `isinstance(True,
    int)` holds, so a bare integer test would read a printed `true` as the
    count 1.
    """
    try:
        result = subprocess.run(
            shlex.split(command), cwd=cwd, capture_output=True, text=True
        )
    except OSError as error:
        return None, f"the census command could not be run {where}: {error}"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip()[-CENSUS_OUTPUT_TAIL:]
        return None, (
            f"the census command exited {result.returncode} {where}: {tail}"
        )
    try:
        printed = json.loads(result.stdout)
    except ValueError as error:
        return None, (
            f"the census command printed output that is not JSON {where}: {error}"
        )
    if not isinstance(printed, dict):
        return None, (
            f"the census command printed a JSON value that is not an object "
            f"{where}: {result.stdout.strip()[:CENSUS_OUTPUT_TAIL]}"
        )
    for counter, value in sorted(printed.items()):
        if isinstance(value, bool) or not isinstance(value, int):
            return None, (
                f"the census command printed a value that is not an integer "
                f"{where}: {counter} is {json.dumps(value)}"
            )
    return printed, ""


def suite_census_check(
    run_dir: Path,
    target_root: Path,
    config: dict,
    artifact: str,
    paths: list[str],
    baseline: Path | None = None,
    *,
    stage_name: str,
) -> CensusResult:
    """Take the census twice and record both, the command, and the verdict.

    Shaped like the revert check beside it: the baseline comes from the shared
    stage-baseline resolution, the clone is built by the existing clone builder
    with the declared paths restored to what the stage first found, and the
    scratch directory goes whatever the result.

    Three conditions stop it, and none of them permits. A target that
    configures no census command has nothing to take, which is recorded as a
    check that did not run and announced nowhere, because nothing is about to
    take a census's worth of time. A stage that declares the census with no
    baseline captured has nothing to compare against. And a command that fails
    or prints something the coordinator cannot read as an object of names to
    integers is reported as the failure it was rather than read as a pass.

    The announcement is made inside the branch that builds the clone, for the
    reason the revert check's is: a path that runs nothing has nothing to
    announce.
    """
    command = config.get("census_command")
    if not command:
        decided = CensusResult(
            ran=False,
            command="",
            reason=(
                "the target configures no census_command, so there is no "
                "census for the coordinator to take"
            ),
        )
        return _record_census(run_dir, artifact, decided)

    resolved = baseline if baseline is not None and baseline.is_dir() else None
    if resolved is None:
        decided = CensusResult(
            ran=False,
            command=command,
            reason=(
                "no baseline was captured for the stage, so there is no state "
                f"to take the earlier census at: {baseline}"
            ),
        )
        return _record_census(run_dir, artifact, decided)

    _suite_rerun_started(
        run_dir,
        stage_name,
        artifact,
        "in a clone at this stage's baseline and again in the tree as the "
        "stage left it, to compare the suite census",
        cost="this takes a census of the suite twice",
        operation=SUITE_CENSUS_OPERATION,
    )

    # Timed around both censuses, from here to the verdict.
    started = time.monotonic()
    scratch = Path(tempfile.mkdtemp(prefix="l5-suite-census-"))
    try:
        clone = scratch / "clone"
        _build_clone(
            target_root,
            clone,
            revert=census_paths(target_root, resolved, list(paths)),
            baseline=resolved,
        )
        before, reason = _take_census(command, clone, "in a clone at the stage's baseline")
    except (RuntimeError, OSError) as error:
        before, reason = None, (
            f"the clone at the stage's baseline could not be built: {error}"
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    after: dict[str, int] | None = None
    if before is not None:
        after, reason = _take_census(
            command, target_root, "in the tree as the stage left it"
        )

    if before is None or after is None:
        decided = CensusResult(
            ran=False,
            command=command,
            baseline=before,
            baseline_path=str(resolved),
            reason=reason,
        )
        return _record_census(run_dir, artifact, decided)

    regressions = census_regressions(before, after)
    decided = CensusResult(
        ran=True,
        command=command,
        baseline=before,
        after=after,
        baseline_path=str(resolved),
        permitted=not regressions,
        regressions=regressions,
        duration_seconds=_measured(started),
    )
    return _record_census(run_dir, artifact, decided)


def _record_census(run_dir: Path, artifact: str, decided: CensusResult) -> CensusResult:
    (run_dir / artifact).write_text(
        json.dumps(decided.as_record(), indent=2) + "\n", encoding="utf-8"
    )
    return decided


def _suite_census_completed(
    run_dir: Path, stage_name: str, artifact: str, census: CensusResult
) -> None:
    """The census's completion event, on the terms `_suite_run_completed`
    states: the census had no completion kind of its own, this one carries the
    verdict in its message and the census's own duration beside the operation
    name, and it decides nothing.
    """
    verdict = "no regression" if census.permitted else "a regression"
    append_event(
        run_dir,
        f"the suite census declared on {stage_name} found {verdict}",
        kind="suite-census-completed",
        stage=stage_name,
        artifacts=[artifact],
        **_timing(SUITE_CENSUS_OPERATION, census.duration_seconds),
    )


#: The pathspec naming the whole repository from its root, whatever directory
#: the listing is run from. A confinement's capture opens with it and then
#: subtracts, because git resolves a set of exclusions against a positive
#: pathspec and answers nothing without one.
_WHOLE_TREE = ":(top)"


def baseline_pathspecs(restrictions: list[StageRestriction]) -> list[str]:
    """The git pathspecs covering everything a stage's restrictions govern.

    One list, so the capture makes one listing and the senses compose rather
    than each getting a listing whose results would be unioned — a union is the
    wrong operation for a confinement, whose whole content is a subtraction.
    A create restriction contributes its prefix; a confinement contributes the
    whole tree once and an exclusion per prefix it names, which is the same set
    its `governs` reports and is why neither is written twice.
    """
    pathspecs: list[str] = []
    for restriction in restrictions:
        if restriction.sense == CREATE_RESTRICTION:
            asked = [restriction.prefix]
        else:
            asked = [_WHOLE_TREE, f":(exclude,top){restriction.prefix}"]
        for pathspec in asked:
            if pathspec not in pathspecs:
                pathspecs.append(pathspec)
    return pathspecs


def stage_baseline_requests(stage: dict) -> dict[str, list[str]]:
    """The baseline captures a stage's declarations between them ask for.

    Two declarations decide against what the stage first found — the revert
    check, over whatever that stage's restrictions govern, and the suite
    census, over the paths it declares. Both name the run-directory directory
    their baseline is kept in. They are collected into one capture per named
    directory, with the union of the pathspecs asked for it, because capturing
    twice would make the second call a re-capture of the first's directory and
    narrow what it may admit for a reason that has nothing to do with the stage
    running again.

    What the revert check asks for is the restriction's own answer rather than
    its prefixes: a create restriction is governed at or beneath each prefix
    and asks for that, while a confinement is governed everywhere *else* and
    asks for the repository minus the prefixes it names. That is what brings
    the baseline with the confinement — a stage confined to one directory has a
    baseline covering the tree it may not freely change, which is the evidence
    a revert needs and the half a create restriction's baseline never had to
    cover.

    It names no stage, no prefix and no directory; all three come off the
    loaded workflow, and a stage carrying neither declaration asks for nothing.
    """
    requests: dict[str, list[str]] = {}
    asked = (
        (stage.get("revert_check"), baseline_pathspecs(restrictions_on(stage))),
        (stage.get("suite_census"), (stage.get("suite_census") or {}).get("paths", [])),
    )
    for declaration, prefixes in asked:
        if not declaration:
            continue
        collected = requests.setdefault(declaration["baseline"], [])
        for prefix in prefixes:
            if prefix not in collected:
                collected.append(prefix)
    return requests


# --------------------------------------------------------------------------
# Whether a documented claim is something the repository could support
# --------------------------------------------------------------------------
#
# An architecture document is tracked and permanent; the run directories,
# logs and requests a stage writes it from are not. So a stage can write a
# factual claim into the repository whose only support is a file local to one
# machine, invisible to whoever judges it, and absent from any clone.
#
# Nothing here reads prose to decide what a factual claim is: that is not
# tractable, and a check that half-works is worse than none. The tractable
# seam is the reference. A claim about another story names it in a fixed
# shape, and whether that story's work is in this repository's history is a
# question git answers. So the whole decision is: text this run added to a
# configured document, naming a story with no completion commit reachable
# from the run's base, in a block that also carries a quantity.
#
# The forward-reference case survives structurally rather than by judgement.
# A document legitimately describes the story currently landing, which is by
# definition unmerged when it is written — but a phrase-shaped forward
# reference ("the next story in this line") carries no story number at all,
# because a story number does not exist until the story is planned, and the
# run's own story id is exempt outright.

#: The shape a claim about another story wears. The reference is the seam the
#: whole check is keyed on, so it is one pattern read in three places: the
#: references a block names, the run's own id being subtracted from them, and
#: the quantity test below, which looks outside the references it removes.
STORY_REFERENCE = re.compile(r"story-\d+")

#: What counts as a quantity: a digit, or one word of the bounded set below.
#: The boundary is stated rather than papered over. Matched: any digit, and
#: any of NUMBER_WORDS — the cardinals two through nineteen, the tens twenty
#: through ninety, hundred, thousand, million and dozen, and the ordinals
#: second through nineteenth plus the -ieth forms — case-insensitively and
#: only as a whole word. Deliberately not matched: `one`, `a`, `an` and
#: `first`, which the set excludes on purpose (see NUMBER_WORDS); a number
#: word inside a longer word, so "often" is not "ten" and "someone" is not
#: "one"; any number word outside the set, so "quadrillion" and "twentieth-
#: century" prose beyond the -ieth forms are quantities this does not see;
#: and anything that would need the text read as language. The set is a
#: bounded vocabulary match, decidable without parsing prose, which is the
#: property the whole check is built on.
QUANTITY = re.compile(r"\d")

#: The bounded set of words that count as a quantity, written as a list so a
#: reader meets the set rather than a regex. `one`, `a`, `an` and `first` are
#: excluded on purpose rather than overlooked: they appear in ordinary prose
#: constantly ("one of the two", "a paragraph", "the first sentence") and
#: carry no enumeration, so admitting them would report almost every added
#: block naming an unmerged story. Everything here enumerates something.
NUMBER_WORDS = (
    # cardinals two through nineteen
    "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
    # the tens
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
    "ninety",
    # the multipliers, and the informal one
    "hundred", "thousand", "million", "dozen",
    # the matching ordinals, second through nineteenth
    "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
    "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
    # and the -ieth forms of the tens
    "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth",
    "seventieth", "eightieth", "ninetieth",
)

#: NUMBER_WORDS as one case-insensitive alternation, longest token first so
#: "seventeen" claims its own match rather than being read as "seven"
#: followed by "teen". The boundaries are non-letter lookarounds rather than
#: `\b`, so a hyphenated compound reports on the strength of the set alone —
#: "twenty-two" is `twenty` followed by a non-letter — while a number word
#: inside a longer word is not a quantity, because "often" has a letter
#: before "ten" and "someone" has one before "one".
NUMBER_WORD = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def added_blocks(diff: str) -> list[str]:
    """The contiguous blocks of text a unified diff adds.

    Added lines are grouped into blocks, and a blank added line ends a block
    the way an unchanged line does, so a block is a paragraph of added prose
    rather than a whole hunk. That is the granularity the report is about: a
    reference and a quantity in one paragraph are one claim, while the same
    two in paragraphs a page apart are not.

    Reads a diff and nothing else — no git invocation, no filesystem — so the
    grouping can be exercised against a diff a caller composed.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            text = line[1:]
            if text.strip():
                current.append(text)
                continue
        if current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def story_references(text: str, story_id: str) -> list[str]:
    """The stories `text` names, sorted, less the run's own.

    The run's own story is exempt outright: its work is by definition
    unmerged while it is landing, so a document describing the story it is
    part of would otherwise be reported on every run.
    """
    return sorted(set(STORY_REFERENCE.findall(text)) - {story_id})


def carries_a_quantity(text: str) -> bool:
    """Whether `text` carries a quantity outside the story references in it.

    A quantity is a digit or one word of the bounded NUMBER_WORDS set: the
    two are alternatives of one decision rather than two decisions, so the
    reference stripping and the block granularity are shared by construction
    and no caller changes.

    The references are removed before looking, because `story-049` is a name
    and the digits in it are part of that name rather than an assertion about
    how many of anything there were.
    """
    stripped = STORY_REFERENCE.sub(" ", text)
    return bool(QUANTITY.search(stripped) or NUMBER_WORD.search(stripped))


@dataclass(frozen=True)
class ClaimSupportResult:
    """What one claim-support check found, as the run directory records it.

    `reports` is absent from the record when the check did not run, and
    `reason` absent when it did, following the optional-by-absence convention
    the other coordinator-written records use: a check that could not run
    reported nothing and cleared nothing, and an empty list would claim it
    had looked.
    """

    ran: bool
    base: str | None = None
    story_id: str | None = None
    documents: tuple[str, ...] = ()
    reports: tuple[dict, ...] = ()
    reason: str | None = None

    def as_record(self) -> dict:
        record: dict = {"ran": self.ran}
        if self.base is not None:
            record["base"] = self.base
        if self.story_id is not None:
            record["story_id"] = self.story_id
        if self.documents:
            record["documents"] = list(self.documents)
        if self.ran:
            record["reports"] = [dict(report) for report in self.reports]
        if self.reason is not None:
            record["reason"] = self.reason
        return record


def unsupported_claims(
    document: str,
    diff: str,
    story_id: str,
    has_merged_work,
) -> list[dict]:
    """The added blocks of one document's diff that assert what nothing checks.

    One report per block naming a story with no merged work and carrying a
    quantity. `has_merged_work` is the merged decision, passed in rather than
    made here, so the question "has this story's work landed" has one answer
    in the coordinator rather than a second spelling of it.

    No branch reads what a block *says* beyond the references in it and
    whether a quantity — a digit or a word of the bounded NUMBER_WORDS set —
    appears outside them, so varying a reported figure — to another wrong
    value or to the right one — cannot change what is reported.
    """
    reports = []
    for block in added_blocks(diff):
        referenced = story_references(block, story_id)
        if not referenced:
            continue
        unmerged = [ref for ref in referenced if not has_merged_work(ref)]
        if not unmerged:
            continue
        if not carries_a_quantity(block):
            continue
        reports.append(
            {"document": document, "stories": unmerged, "text": block}
        )
    return reports


def claim_support_check(
    run_dir: Path,
    target_root: Path,
    config: dict,
    artifact: str,
    base: str,
    story_id: str,
) -> ClaimSupportResult:
    """Report the added claims about another story that nothing tracked can hold.

    Scans the text this run added to each of the target's configured
    architecture documents, against the run's resolved base. The merged
    question is put to `completion_commits`, the same reader the finished-branch
    pre-flight uses, so what counts as a story's merged work is one fact.

    It routes nothing and escalates nothing: the record is written, injected
    into the verifier, and read by the stage whose job is judging.

    Three conditions stop it, and each is recorded as a check that could not
    run *with the reason* rather than as a check that found nothing — a target
    configuring no architecture documents must not read as a document whose
    added claims are all supportable.
    """
    documents = tuple(config.get("architecture_docs", []) or ())
    if not documents:
        return _record_claim_support(
            run_dir,
            artifact,
            ClaimSupportResult(
                ran=False,
                story_id=story_id,
                reason=(
                    "the target configures no architecture documents, so there "
                    "is nothing to scan; this is not a document whose added "
                    "claims are all supportable"
                ),
            ),
        )

    if _git(target_root, "rev-parse", "--verify", base).returncode != 0:
        return _record_claim_support(
            run_dir,
            artifact,
            ClaimSupportResult(
                ran=False,
                story_id=story_id,
                documents=documents,
                reason=(
                    f"the run's base '{base}' does not resolve, so there is no "
                    f"horizon to ask what this run added or what has merged"
                ),
            ),
        )

    merged: dict[str, bool] = {}

    def has_merged_work(story: str) -> bool:
        if story not in merged:
            merged[story] = bool(completion_commits(target_root, base, story))
        return merged[story]

    reports: list[dict] = []
    for document in documents:
        diff = _git(target_root, "diff", base, "--", document)
        if diff.returncode != 0:
            return _record_claim_support(
                run_dir,
                artifact,
                ClaimSupportResult(
                    ran=False,
                    base=base,
                    story_id=story_id,
                    documents=documents,
                    reason=(
                        f"the text this run added to {document} could not be "
                        f"read: git diff against {base} failed: "
                        f"{diff.stderr.strip()}"
                    ),
                ),
            )
        reports.extend(
            unsupported_claims(document, diff.stdout, story_id, has_merged_work)
        )

    return _record_claim_support(
        run_dir,
        artifact,
        ClaimSupportResult(
            ran=True,
            base=base,
            story_id=story_id,
            documents=documents,
            reports=tuple(reports),
        ),
    )


def _record_claim_support(
    run_dir: Path, artifact: str, result: ClaimSupportResult
) -> ClaimSupportResult:
    (run_dir / artifact).write_text(
        json.dumps(result.as_record(), indent=2) + "\n", encoding="utf-8"
    )
    return result


def _claim_support_recorded(
    run_dir: Path, stage_name: str, artifact: str, result: ClaimSupportResult
) -> None:
    """Say what the check found. Nothing routes on it.

    Its own event kind rather than the generic `note`, because a note is the
    kind an existing reader uses to *find* the stale-base note and to assert
    its absence; sharing it would make that reader report this instead. The
    kind says only that the check ran and what it saw — the coordinator
    computes the fact and the verifier decides what to do with it, so no
    route, no retry and no escalation hangs off this line.
    """
    if not result.ran:
        message = (
            f"claim support check on {stage_name}'s documents did not run: "
            f"{result.reason}"
        )
    elif result.reports:
        listed = "; ".join(
            f"{report['document']} names {', '.join(report['stories'])}"
            for report in result.reports
        )
        message = (
            f"claim support check on {stage_name}'s documents reports "
            f"{len(result.reports)} added claim(s) nothing tracked can "
            f"support: {listed}"
        )
    else:
        message = (
            f"claim support check on {stage_name}'s documents reports nothing"
        )
    append_event(
        run_dir,
        message,
        kind="claim-support-checked",
        stage=stage_name,
        artifacts=[artifact],
    )


# --------------------------------------------------------------------------
# The self-route
#
# Mechanical failures route differently from verdicts. There is no defect to
# categorize and no other stage to send the work to, so the failed stage runs
# again: a self-route. A stage that failed to produce something may plausibly
# produce it next time. That is exactly the reasoning that does not apply to a
# boundary violation, where retrying the same instructions would produce the
# same violation again, so boundary violations still escalate — as do schema
# violations, stage output ownership, the revert check's no-verdict escalation
# and the clean-clone check's two. The revert check's *refusal* is not on that
# list and never was a boundary violation of this kind: it is a verdict the
# check has already proved harmless, so the edits are undone and the run
# carries on rather than being routed anywhere.
#
# The global ceiling survives the exception, because a self-route cannot
# alternate: when it succeeds the workflow advances, and the only way it
# repeats is by failing again in the same place. A self-route therefore spends
# neither retry_count nor max_retries and reads neither; it archives nothing
# under attempts/attempt-N/, appends nothing to retry-history.json, and does
# not move the attempt number in any rendered prompt filename.
#
# A self-routed stage has no agent-authored guidance — no verifier looked at
# the work — so the coordinator states why the stage is re-running itself,
# naming the output that was missing or stale. It writes that statement as an
# artifact on disk as well as injecting it, so a run directory can say why a
# stage ran twice. A routable failure without injected evidence replays the
# conditions that produced it.
# --------------------------------------------------------------------------

#: The three mechanical failures. Each is a fact about what the stage did not
#: produce rather than a judgement about the work, which is what makes running
#: the stage again a plausible answer to all three.
AGENT_PROCESS_FAILED = "agent-process-failed"
MISSING_REQUIRED_ARTIFACTS = "missing-required-artifacts"
STALE_REQUIRED_ARTIFACTS = "stale-required-artifacts"

#: The stage changed a repository file its own changed-files record does not
#: name. Like the two artifact causes it is a fact about what the stage did
#: not write down rather than a judgement about what it did, and the only
#: stage that may amend a record is the stage that wrote it — so the omission
#: re-enters that stage rather than being repaired anywhere else.
INCOMPLETE_CHANGED_FILES = "incomplete-changed-files"

#: Every cause a self-route can be taken for. Declared so the classification
#: below is held *total* against it rather than against a list written at a
#: call site or in a test: a cause added later and left unclassified is a
#: cause on neither side of the split, which is a question the suite can ask
#: and answer.
SELF_ROUTE_FAILURES = (
    AGENT_PROCESS_FAILED,
    MISSING_REQUIRED_ARTIFACTS,
    STALE_REQUIRED_ARTIFACTS,
    DEFECTIVE_RETRY_GUIDANCE,
    INCOMPLETE_CHANGED_FILES,
)

#: The bookkeeping subset: the stage did the work and did not record it, or
#: recorded it somewhere the freshness check reads as a previous attempt's, or
#: recorded it incompletely. Nothing about the work is in question in any of
#: the three, which is what makes spending a budget of its own the right price
#: for them. The incomplete record is the sharpest instance of the fact the
#: other two state: the tree already holds the change and the account of it is
#: what is missing.
#:
#: agent-process-failed is deliberately not here. A process that exited
#: without completing produced no account of what it did, so what is missing
#: is not the bookkeeping but the whole turn: nobody can say whether the work
#: was done, and re-entering it is not a correction pass over a known-good
#: tree. defective-retry-guidance is not here for the plainer reason that it
#: is a fact about the judgement of the work rather than about the record of
#: it.
BOOKKEEPING_SELF_ROUTE_FAILURES = (
    MISSING_REQUIRED_ARTIFACTS,
    STALE_REQUIRED_ARTIFACTS,
    INCOMPLETE_CHANGED_FILES,
)

#: The declaration each budget is read off, and the name a stop is reported
#: under. Written once so the key the workflow declares, the key the
#: pre-flight refuses on and the name the escalation reason carries are one
#: string rather than three that agree today.
SELF_ROUTE_BUDGET_KEY = "max_self_routes"
BOOKKEEPING_SELF_ROUTE_BUDGET_KEY = "max_bookkeeping_self_routes"


def self_route_result_file(
    stage_name: str, attempt: int, try_number: int | str
) -> str:
    """Where one self-route's evidence is written, keyed so none overwrites another.

    The stage, the attempt and the try together, because a stage can self-route
    more than once within one attempt and more than one stage can self-route in
    one run. The try number is the self-route count the state carries, which is
    the same number the re-run prompt's filename uses, so the two agree by
    construction rather than by two derivations that match today.

    A caller may pass `"*"` for the try number to build the glob that finds
    every such name already written for one stage and attempt. That is why the
    number is not an int alone: the discovery and the writing then share one
    spelling of the name rather than two that agree today.
    """
    return f"self-route-{stage_name}-attempt-{attempt}-try-{try_number}.json"


def retained_suite_result_file(
    artifact: str, stage_name: str, attempt: int, try_number: int | str
) -> str:
    """Where one suite run's result is kept, keyed so no later run overwrites it.

    The declared artifact's stem, then the stage, the attempt and the try, in
    the shape `self_route_result_file` and `prompt_file` already establish. The
    stem is stripped the way `suite_output_file` strips it, so the artifact name
    is the workflow's and nothing here spells one. The retained output filename
    comes from passing this name to `suite_output_file`, so the output is
    derived from the result rather than shaped a second time.

    The try number is the self-route count the state carries — the same number
    the prompt file for the invocation this run judges is keyed by — so the
    prompt a stage was given and the suite run judging that turn share a key
    rather than two derivations that happen to agree.

    A caller may pass `"*"` for the attempt or the try to build the glob over
    what is already written, so discovery and writing share one spelling.
    """
    stem = artifact[: -len(".json")] if artifact.endswith(".json") else artifact
    return f"{stem}-{stage_name}-attempt-{attempt}-try-{try_number}.json"


def prompt_file(stage_name: str, attempt: int, try_number: int | str = 0) -> str:
    """Where one invocation's rendered prompt is written.

    The one place the prompt filename is shaped. A first invocation writes the
    name it has always written, with no try suffix; a self-route adds the
    suffix, keyed by the same count self_route_result_file uses, so a re-run
    does not write over the prompt the failed invocation was actually given.
    As there, `"*"` builds the glob over what is already written.
    """
    suffix = f"-try-{try_number}" if try_number else ""
    return f"prompt-{stage_name}-attempt-{attempt}{suffix}.md"


def self_route_artifacts(run_dir: Path, stages: list[dict], attempt: int) -> list[str]:
    """The self-route evidence and try-suffixed prompts one attempt left behind.

    Read off the run directory rather than derived, because a self-route's
    names are keyed by a count that is live state: by the time a resume asks,
    the count has been zeroed and nothing records how high it reached. The two
    globs come from the same functions that write the names, with the try
    number wildcarded, so what is found cannot drift from what was written, and
    the stage names come off the loaded workflow as everywhere else.
    """
    names: list[str] = []
    for stage in stages:
        for pattern in (
            self_route_result_file(stage["name"], attempt, "*"),
            prompt_file(stage["name"], attempt, "*"),
        ):
            names.extend(sorted(path.name for path in run_dir.glob(pattern)))
    return names


def self_route_statement(
    failure: str, artifacts: list[str], entries: list[str] | None = None
) -> str:
    """What the coordinator tells a stage it is re-running, in its own words.

    Nothing here is an agent's judgement and the text says so. The failed
    process gets a different statement from the other two deliberately: there
    is no output to name, because the invocation did not reach the point of
    declaring what it had done, and what the stage most needs to know is that
    the tree already holds whatever that invocation wrote before it exited.

    The defective-guidance statement names the guidance entries that were met
    and the verdict that failed anyway, because the contradiction is between
    those two things and a re-running verifier told only that it contradicted
    itself has nothing to act on. `entries` carries them, and they are written
    out verbatim rather than counted; the incomplete-record statement carries
    its omitted paths through the same argument and on the same terms.

    Every cause reaches its text by its own condition and none by falling off
    the end, so a cause added to SELF_ROUTE_FAILURES later has to be given
    words rather than silently inheriting another cause's. An unknown cause
    raises, which is the loud failure this repository prefers to a degraded
    one.
    """
    named = ", ".join(artifacts)
    if failure == DEFECTIVE_RETRY_GUIDANCE:
        listed = "; ".join(entries or [])
        return (
            f"Every entry of the retry guidance in force was met, and your "
            f"verdict failed the work anyway. Your own guidance_outcomes "
            f"accounts for all of these and reports none of them unmet: "
            f"{listed}. That contradiction indicts the guidance rather than "
            f"the stage: guidance may not sanction the outcome it fails. "
            f"This is the coordinator's own statement, not an agent's, and no "
            f"verifier judged it — it is computed from the guidance in force "
            f"and the verdict you wrote, by comparing strings alone. The "
            f"artifacts it is between are {named}. Either fail the work on "
            f"the criterion it actually failed, reporting the entry whose "
            f"satisfied_when did not hold as unmet and saying why, or write "
            f"guidance that does not authorize the outcome you will fail. No "
            f"retry has been spent and no attempt archived; this is the same "
            f"attempt, running the verification again."
        )
    if failure == AGENT_PROCESS_FAILED:
        return (
            "The previous invocation of this stage exited without completing, "
            "so there is no output to name: it did not reach the point of "
            "declaring what it had done. This is not a judgement about the "
            "work — no verifier saw it. The working tree already holds "
            "whatever that invocation wrote before it exited, so read what is "
            "there before repeating it."
        )
    if failure == MISSING_REQUIRED_ARTIFACTS:
        return (
            f"The previous invocation of this stage ended without writing "
            f"required output it declared: {named}. This is not a judgement "
            f"about the work — no verifier saw it. Produce the named output "
            f"this time."
        )
    if failure == STALE_REQUIRED_ARTIFACTS:
        return (
            f"The previous invocation of this stage left required output "
            f"unwritten: {named}. What sits at the run directory root under "
            f"those names is a previous attempt's, not that invocation's. "
            f"This is not a judgement about the work — no verifier saw it. "
            f"Write the named output afresh this time."
        )
    if failure == INCOMPLETE_CHANGED_FILES:
        listed = "; ".join(entries or [])
        return (
            f"You changed repository files your changed-files record does not "
            f"name. The record is {named}, and the paths missing from it are: "
            f"{listed}. This is not a judgement about the work — no verifier "
            f"saw it, and it is not an assertion by any agent: the coordinator "
            f"took a signature of the tree before your turn began and compared "
            f"it after, subtracting the repository-wide blocked paths and "
            f"whatever git excludes as ignored. Your record is the only "
            f"account of what you touched, and every check that decides "
            f"whether an edit was permitted reads it, so a path missing from "
            f"it is a path nothing can adjudicate. Amend your own record so it "
            f"names every path above, in the group that describes what you did "
            f"to it; no other stage may write it for you. The signature is not "
            f"re-taken, so an omission left uncorrected is reported again. No "
            f"retry has been spent and no attempt archived; this is the same "
            f"attempt, running again."
        )
    raise ValueError(f"no self-route statement for failure {failure!r}")


@dataclass(frozen=True)
class SelfRouteDecision:
    """What one mechanical failure decided: run the stage again, or escalate.

    `reason` is the escalation reason when the stage did not self-route, and it
    names the mechanical failure in the words that site already used. When the
    budget was declared and is spent it names the exhausted budget too, so a
    developer reading the escalation learns both what failed and why the stage
    stopped trying.
    """

    taken: bool
    reason: str


def self_route(
    run_dir: Path,
    state: RunState,
    stage: dict,
    *,
    failure: str,
    reason: str,
    artifacts: list[str],
    attempt: int,
    entries: list[str] | None = None,
) -> SelfRouteDecision:
    """Decide one mechanical failure, and record it when the stage runs again.

    The one decision behind every self-routing failure site — the three
    mechanical ones and the defective-guidance one that joined them — so they
    share a rule rather than repeating it per site. A stage that declares no
    budget
    escalates with exactly the reason it escalated with before this existed,
    which is what makes landing this change nothing until a workflow opts in.

    Two budgets are spent rather than one, and which of them a failure spends
    is decided by the declared classification above rather than by a condition
    written here. A stage declaring a bookkeeping budget spends it on the
    bookkeeping causes and spends max_self_routes on the rest; a stage
    declaring none spends max_self_routes on everything, which is what the
    stage did before the split and is why landing it is nothing until a
    definition opts in.

    When there is budget left the count is incremented, the coordinator's own
    evidence is written under a name keyed by stage, attempt and try, one
    `self-routed` event is appended, and the state is saved so a crash mid-stage
    leaves a count a reader can trust. The budget comes off the loaded stage
    dict exactly as may_not_create and clean_clone do; no stage name and no
    budget value is written here.
    """
    name = stage["name"]
    budget = stage.get(SELF_ROUTE_BUDGET_KEY, 0)
    bookkeeping_budget = stage.get(BOOKKEEPING_SELF_ROUTE_BUDGET_KEY)
    # An absent bookkeeping budget means *unsplit*, not zero: the stage has
    # not opted in, so every cause spends max_self_routes and is reported the
    # way it was reported before the split existed. Only a stage that declares
    # one has its bookkeeping causes taken out of the failure budget.
    bookkeeping = (
        bookkeeping_budget is not None
        and failure in BOOKKEEPING_SELF_ROUTE_FAILURES
    )
    if bookkeeping:
        spent, governing = state.bookkeeping_self_route_count, bookkeeping_budget
        governing_key = BOOKKEEPING_SELF_ROUTE_BUDGET_KEY
    else:
        # The failure budget is spent by the re-entries that were not
        # bookkeeping, which is the difference between the total and the
        # subset — so a stage re-entered for a forgotten file reaches a later
        # suite failure with the failure budget it would have had.
        spent = state.self_route_count - state.bookkeeping_self_route_count
        governing, governing_key = budget, SELF_ROUTE_BUDGET_KEY
    if spent >= governing:
        if governing:
            # An unsplit stage's escalation is worded exactly as it was, so
            # landing the split changes nothing until a definition opts in. A
            # split one names the budget that stopped it, so a bookkeeping
            # exhaustion and a failure exhaustion do not read as one stop.
            named_budget = (
                "self-route"
                if bookkeeping_budget is None
                else governing_key
            )
            return SelfRouteDecision(
                False,
                f"{reason}; {name} has exhausted its {named_budget} budget of "
                f"{governing}",
            )
        return SelfRouteDecision(False, reason)

    # The total is incremented for every cause, so the try number stays the
    # count of this stage entry's re-entries and every self-route record and
    # re-run prompt keyed by it is written and discovered exactly as it is.
    state.self_route_count += 1
    if bookkeeping:
        state.bookkeeping_self_route_count += 1
        spent = state.bookkeeping_self_route_count
    else:
        spent = state.self_route_count - state.bookkeeping_self_route_count
    record: dict = {
        "stage": name,
        "attempt": attempt,
        "try": state.self_route_count,
        "failure": failure,
        "reason": reason,
        "statement": self_route_statement(failure, artifacts, entries),
    }
    # Optional by absence, as clean-clone-result and execution-history already
    # are: a failed process names no artifact, and null would claim it named
    # none rather than that naming one does not apply.
    if artifacts:
        record["artifacts"] = list(artifacts)
    (run_dir / self_route_result_file(name, attempt, state.self_route_count)).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    # The same rule the escalation reason follows: an unsplit stage's event
    # reads as it always did, and a split one says which budget it spent.
    spend = (
        f"self-route {state.self_route_count} of {budget}"
        if bookkeeping_budget is None
        else f"{governing_key} {spent} of {governing}"
    )
    append_event(
        run_dir,
        f"self-routed: {name} runs again in place ({reason}); {spend}",
        kind="self-routed",
        stage=name,
        # A self-route names its own stage: there is no other destination, and
        # recording it keeps the route reconstructable from the history alone.
        retry_stage=name,
        retry_reason=reason,
    )
    save_state(run_dir, state)
    return SelfRouteDecision(True, reason)


# --------------------------------------------------------------------------
# A minor, correct finding has somewhere to go
#
# A verifier that notices something real but too small to fail a run used to
# record it in `unverified`, where nothing read it: no routing decision touched
# it, no stage received it, and the observation shipped uncorrected. A verdict
# may now carry `correctable_findings` instead — findings the verifier judges
# correct, mechanically fixable, and a correction to words rather than to
# behaviour — and on a passing verdict the workflow re-enters at the stage the
# correction-pass declaration names and runs through to verification again.
#
# The entry stage is declared rather than derived from a finding's category,
# and the clean-clone check now runs after the pass rather than before it. Both
# are story-067's, and the reasoning for each sits where the behaviour is: the
# declaration at `correction_destination`, the ordering at the verifier branch
# that reads the two blocks.
#
# That is a retry's shape minus everything a retry spends: no retry_count, no
# attempts/attempt-N/ archive, no retry-history.json entry, and the verdict
# that routed it is unchanged. The budget is declared in the workflow the way
# clean_clone is, so removing the key disables the whole mechanism with no
# change here, and nothing below writes a stage name or a category: both come
# off the loaded definition, as every other route does.


def correction_pass_declaration(stages: list[dict]) -> dict:
    """The workflow's correction-pass declaration, wherever a stage carries it.

    Read off the loaded definition rather than looked up by stage name, so a
    workflow that declares the pass on a different stage is found here and one
    that declares it nowhere disables the mechanism entirely. The first
    declaration wins; a definition carrying two would be declaring one
    mechanism twice.
    """
    for stage in stages:
        declaration = stage.get("correction_pass")
        if declaration:
            return declaration
    return {}


def correction_pass_result_file(artifact: str, number: int) -> str:
    """Where one correction pass's record is written, keyed so none overwrites another.

    The declared artifact name carries the pass number before its extension.
    The budget is one pass per run today, so no run reaches a second name; the
    key exists because the alternative is a name that silently becomes wrong
    the moment a workflow declares a larger budget, which is the failure the
    self-route records already key against.
    """
    stem, dot, extension = artifact.rpartition(".")
    if not dot:
        return f"{artifact}-{number}"
    return f"{stem}-{number}{dot}{extension}"


def correction_pass_grants(
    run_dir: Path, stages: list[dict], state: RunState, stage_name: str, attempt: int
) -> list[str]:
    """The files this pass's findings grant one stage to change for one attempt.

    A correctable finding names a `path`, and naming it is the grant: at the
    stage the pass enters, the exempt list the ownership and revert checks read
    gains that path, so a finding grants its stage the one file it names for
    this pass. The record is read off the run directory rather than passed in
    memory, exactly as the prompt injection of the same record is, so what the
    checks exempt and what the prompt reports are one thing.

    The grant is bounded to the stage and the attempt the record names: a
    documenter re-entered by a later retry in the same run runs at a later
    attempt, so the attempt no longer matches and the grant is not inherited.
    A record for another stage grants nothing here either. A run that has taken
    no pass, a workflow that declares none, and a record that is absent or
    unreadable each grant nothing.
    """
    if not state.correction_pass_count:
        return []
    declared = correction_pass_declaration(stages).get("result")
    if not declared:
        return []
    record = run_dir / correction_pass_result_file(
        declared, state.correction_pass_count
    )
    if not record.is_file():
        return []
    try:
        parsed = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if parsed.get("stage") != stage_name or parsed.get("attempt") != attempt:
        return []
    return [
        finding["path"]
        for finding in parsed.get("findings", [])
        if finding.get("path")
    ]


def correction_pass_statement(stage_name: str) -> str:
    """What the coordinator tells the stage a correction pass routes to.

    A passing verdict writes no retry guidance, so there is no agent-authored
    guidance behind this and the statement says so, exactly as the self-route
    statement does. What the stage most needs to know is the boundary: this
    corrects words and never behaviour.

    It does not ask the stage to prove that. The instruction that the suite
    must pass unchanged across the pass used to be here, and what it bought
    was a stage starting a whole-suite run it could not finish inside its
    turn — story-063's implementer, which spent an invocation and two
    self-routes on a line wrap without ever writing the edit's artifacts. The
    constraint is unchanged and is still stated where the verifier reads what
    may go in a correctable finding; what moved is who is asked to establish
    it, and the answer is the clean-clone check the coordinator runs after the
    pass, which runs the whole suite in a fresh clone and needs nothing of the
    stage.
    """
    return (
        f"The verification passed. It also recorded findings it judges correct, "
        f"too small to fail the run, and fixable in the words alone, and the "
        f"coordinator has re-entered the workflow at {stage_name} so they have "
        f"somewhere to go. This is the coordinator's own statement, not an "
        f"agent's: a passing verdict writes no retry guidance, so nothing here "
        f"was authored by a verifier beyond the findings themselves. This is "
        f"not a retry and no retry budget was spent — the retry count is "
        f"unchanged, no attempt was archived, and the passing verdict stands. "
        f"Correct the words each finding names and nothing else: a correction "
        f"pass changes prose — a comment, a docstring, a schema description, a "
        f"document — and never behaviour, so nothing you change here may alter "
        f"what any test asserts about the system. You are not asked to "
        f"establish that: the clean-clone check the coordinator runs after "
        f"this pass confirms the correction changed no behaviour. The approved "
        f"story artifact is never edited by a correction pass — if a finding "
        f"names it, correct nothing and say so in your own output. Then let "
        f"the workflow run through to verification again as it normally would."
    )


@dataclass(frozen=True)
class CorrectionRouting:
    """Where a verdict's correctable findings send execution, or why they cannot.

    `stage` is the entry stage the workflow's correction-pass declaration
    names. It is a property of the definition rather than something derived
    from the findings: whatever categories they carry, a pass enters where the
    declaration says and runs forward from there.

    `unknown` holds the categories no route in the table defines, in the order
    the findings named them and without repeats. It is non-empty exactly when
    the run must escalate instead of routing, which is the same strictness an
    unrecognised `retry_target` already gets: absorbing an unknown category
    into a default destination would be the drift the routing table exists to
    remove. That the category no longer *selects* a destination does not make
    it optional — it is still a required, validated field, and one naming a
    category the workflow does not declare is still a bug in what the verifier
    produced.
    """

    stage: str | None
    unknown: list[str]


def correction_destination(
    declaration: dict, findings: list[dict], routes: dict
) -> CorrectionRouting:
    """The entry stage the correction-pass declaration names, or why none applies.

    Declared rather than derived. It used to be the earliest stage in workflow
    order among the categories the findings named, which priced a one-word fix
    at every stage after that entry point: story-063 categorised a line wrap
    as an implementation defect, re-entered at the implementer, and spent an
    invocation and two self-routes on it. A pass now costs the stages after
    the declared entry and no more, whatever category a finding carries.

    The routes table is still read, and still by walking the findings, because
    an unknown category still escalates the run. No category name and no stage
    name is written here: the destination comes off the declaration and the
    vocabulary off the table, both read from the loaded definition.
    """
    unknown: list[str] = []
    for finding in findings:
        category = finding.get("category")
        if category not in routes and category not in unknown:
            unknown.append(category)
    if unknown:
        return CorrectionRouting(None, unknown)
    return CorrectionRouting(declaration.get("stage"), [])


def _correction_pass_routed(
    run_dir: Path,
    stage_name: str,
    destination: str,
    findings: list[dict],
    number: int,
    budget: int,
    routed_slugs=(),
) -> None:
    """Record a correction pass, and where its findings came from.

    `routed_slugs` is what the pre-stage inspection routed to this story on
    this attempt, read back off the accounting artifact. When any were, the
    line says how many of the findings the pass carries are among them — by
    the accounting's own slug rule — so a reader of events.log can tell a
    re-entry spent on the Inspector's reading from one spent on the verifier's
    own. A workflow declaring no inspection routes none and writes the line
    exactly as before.
    """
    append_event(
        run_dir,
        f"correction pass {number} of {budget}: verification passed carrying "
        f"{len(findings)} correctable finding(s); re-entering at {destination}"
        + routed_among(findings, routed_slugs),
        kind="correction-pass-routed",
        stage=stage_name,
        retry_stage=destination,
    )


def routed_among(entries: list[dict], routed_slugs) -> str:
    """The clause a re-entry event carries when findings were routed.

    Empty when none were, so an event under a workflow declaring no inspection
    is byte-for-byte what it was. Otherwise it says how many of the findings
    routed to this story the entries name — the accounting's rule, reached
    through `story_inspection` rather than respelled here, so the event and the
    accounting cannot count differently.

    Both halves of the clause count findings. A verdict raises an entry per
    location a repair has to reach, so the entries it carries are the wrong
    unit to put against a count of findings: story-156's first repair pass
    carried seven entries naming three routed findings, and the clause read
    "6 of them are among the 3 findings routed to this story", which no reading
    makes true. "They name" rather than "of them" for the same reason — what is
    counted is no longer a subset of the entries.
    """
    slugs = tuple(routed_slugs)
    if not slugs:
        return ""
    locations = [entry.get("location") for entry in entries
                 if isinstance(entry, dict)]
    named = story_inspection.routed_findings_named(locations, slugs)
    return (f"; they name {named} of the {len(slugs)} findings "
            f"routed to this story")


def _correction_pass_recorded(
    run_dir: Path, stage_name: str, findings: list[dict], budget: int
) -> None:
    """Record findings a spent budget leaves uncorrected, and complete the run.

    The bound is one pass per run and it is what makes termination a property
    rather than a hope: a fix the verifier keeps rejecting cannot cycle. A
    second verdict still carrying findings is not an error and routes nowhere,
    so the findings are named in the event stream, where a developer meets
    them, and the run completes.
    """
    named = "; ".join(
        f"{finding.get('location')}: {finding.get('finding')}"
        for finding in findings
    )
    append_event(
        run_dir,
        f"correction pass budget of {budget} is spent; verification passed "
        f"still carrying correctable findings, which are recorded here and "
        f"left uncorrected: {named}",
        kind="correction-pass-recorded",
        stage=stage_name,
    )


# --------------------------------------------------------------------------
# A finding about the story's own change is fixed by the stage that owns it
#
# A verifier that wants a finding fixed had two routes, and most findings fit
# neither: a correctable finding enters at the declared correction stage and
# is bounded to the words alone, and a blocking issue fails the verdict and
# spends a retry. A finding whose fix is one line in a file the correction
# stage does not own could be had only by failing the whole run, so story-154
# filed five such findings as briefs instead.
#
# The repair pass is the third route. A passing verdict may carry
# `repairable_findings`, each naming a path, a location, the finding, the
# correction and a retry category. The category is looked up in the verifier
# stage's retry_routing table exactly as a failed verdict's retry_target is,
# the workflow re-enters at the earliest stage in workflow order among the
# resolved stages, and runs forward to verification again. It has the
# correction pass's cost and the retry routing's reach: nothing a retry spends
# is spent — no retry_count increment, no attempts/attempt-N/ archive, no
# retry-history entry, and the passing verdict that routed it stands — and
# each stage entered is granted the paths of the findings whose category
# resolves to it, for that attempt alone.
#
# Because a repair may change behaviour, it is built on a verified tree: it
# routes only after the clean-clone check on the routing verdict has passed,
# and the verification it returns to runs the check again. The budget is
# declared on the verifier stage beside correction_pass, and the declaration
# carries no stage key because the stage comes off the routing table. Nothing
# below writes a stage name, a category or an artifact name.


def repair_pass_declaration(stages: list[dict]) -> dict:
    """The workflow's repair-pass declaration, wherever a stage carries it.

    In `correction_pass_declaration`'s shape and for its reason: read off the
    loaded definition rather than looked up by stage name, and a workflow that
    declares it nowhere disables the mechanism entirely.
    """
    for stage in stages:
        declaration = stage.get("repair_pass")
        if declaration:
            return declaration
    return {}


def repair_pass_result_file(artifact: str, number: int) -> str:
    """Where one repair pass's record is written, keyed by the pass number.

    Keyed for the reason `correction_pass_result_file` keys: the budget is
    more than one, so a second pass would otherwise write over the first's
    record.
    """
    return correction_pass_result_file(artifact, number)


def repair_pass_grants(
    run_dir: Path, stages: list[dict], state: RunState, stage_name: str, attempt: int
) -> list[str]:
    """The files this pass's findings grant one stage to change for one attempt.

    Read off the current pass record exactly as `correction_pass_grants` reads
    the correction record, so what the checks exempt and what the prompt
    reports are one thing. The difference is that a repair pass enters more
    than one stage: the record carries each finding with the stage its
    category resolved to, and a stage is granted only the paths of the
    findings that resolved to it. Bounded to the attempt the record names, so
    a stage re-entered by a later retry in the same run inherits nothing. A
    run that has taken no pass, a workflow that declares none, and a record
    that is absent or unreadable each grant nothing. A finding carrying a
    scope_extension is granted here exactly as any other: the extension was
    decided when the pass was routed, and a finding the bounds refused is not
    in the record.
    """
    return [
        finding["path"]
        for finding in _repair_pass_findings(run_dir, stages, state, stage_name, attempt)
    ]


def _repair_pass_findings(
    run_dir: Path, stages: list[dict], state: RunState, stage_name: str, attempt: int
) -> list[dict]:
    """The current pass record's findings that resolved to `stage_name` on
    `attempt`, each naming a path — the one reading behind the grants and
    the extension announcements.
    """
    if not state.repair_pass_count:
        return []
    declared = repair_pass_declaration(stages).get("result")
    if not declared:
        return []
    record = run_dir / repair_pass_result_file(declared, state.repair_pass_count)
    if not record.is_file():
        return []
    try:
        parsed = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if parsed.get("attempt") != attempt:
        return []
    return [
        finding
        for finding in parsed.get("findings", [])
        if isinstance(finding, dict)
        and finding.get("stage") == stage_name
        and finding.get("path")
    ]


def repair_pass_statement(entry_stage: str) -> str:
    """What the coordinator tells the stages a repair pass re-enters.

    A passing verdict writes no retry guidance, so there is no agent-authored
    guidance behind this and the statement says so, as the correction-pass
    statement does. What the stage most needs to know is that it is told the
    findings and the corrections and nothing else, that only the files those
    findings name are its to change on this pass, and that this is not a
    licence to revisit the work.
    """
    return (
        f"The verification passed, and the clean-clone check on that verdict "
        f"passed too. It also recorded findings it judges correct, too small "
        f"to fail the run, and fixable by one stage in the file each names, "
        f"and the coordinator has re-entered the workflow at {entry_stage} so "
        f"they are fixed by the stage that owns them rather than filed for a "
        f"later story. This is the coordinator's own statement, not an "
        f"agent's: a passing verdict writes no retry guidance, so nothing here "
        f"was authored by a verifier beyond the findings themselves. This is "
        f"not a retry and no retry budget was spent — the retry count is "
        f"unchanged, no attempt was archived, and the passing verdict stands. "
        f"Each finding below names the stage its category resolved to; if "
        f"none names yours, you are running because the workflow runs forward "
        f"from the entry stage, and you have nothing to repair. Make the "
        f"correction each of your findings names, in the file it names, and "
        f"nothing else: those files are yours to change for this pass, and an "
        f"edit to any other governed file is put to the revert check after "
        f"your turn. A finding carrying scope_extension names a file outside "
        f"the story's declared scope; the coordinator has granted it for this "
        f"pass, and the reason recorded on the finding is why — the rule the "
        f"story changed and the line in that file that states it. A repair "
        f"pass is not a licence to revisit the work. The verification it "
        f"returns to runs the clean-clone check again on the repaired tree."
    )


@dataclass(frozen=True)
class RepairRouting:
    """Where a verdict's repairable findings send execution, or why they cannot.

    `stage` is the entry stage: the earliest in workflow order among the
    stages the findings' categories resolved to, so a verdict naming two
    categories costs one pass that runs forward through both. `resolved` is
    each finding with its resolved stage added, in the order the verdict
    carried them, so the pass record can say which stage owns which fix.
    `unknown` holds the categories the table does not define, and it is
    non-empty exactly when the run must escalate instead of routing — the
    strictness an unrecognised retry_target already gets.
    """

    stage: str | None
    resolved: list[dict]
    unknown: list[str]


def repair_destination(
    findings: list[dict], routes: dict, stage_names: list[str]
) -> RepairRouting:
    """Resolve each finding's category through the routing table.

    The table is the same one a failed verdict's retry_target is looked up
    in, so the categories a repair pass may name and the stages it may enter
    are exactly the ones the workflow already declares for a retry. No
    category name and no stage name is written here.
    """
    unknown: list[str] = []
    resolved: list[dict] = []
    for finding in findings:
        category = finding.get("category")
        route = routes.get(category) if isinstance(category, str) else None
        if route is None:
            if category not in unknown:
                unknown.append(category)
            continue
        resolved.append({**finding, "stage": route.get("stage")})
    if unknown:
        return RepairRouting(None, [], unknown)
    stages = {one["stage"] for one in resolved}
    entry = next((name for name in stage_names if name in stages), None)
    return RepairRouting(entry, resolved, [])


def _repair_pass_routed(
    run_dir: Path,
    stage_name: str,
    destination: str,
    findings: list[dict],
    number: int,
    budget: int,
    routed_slugs=(),
) -> None:
    """Record a repair pass, and where its findings came from.

    Worded with `routed_among` as the correction-pass event is, so a reader of
    events.log can tell a re-entry spent on the Inspector's reading from one
    spent on the verifier's own.
    """
    append_event(
        run_dir,
        f"repair pass {number} of {budget}: verification passed and the "
        f"clean-clone check passed, carrying {len(findings)} repairable "
        f"finding(s); re-entering at {destination}"
        + routed_among(findings, routed_slugs),
        kind="repair-pass-routed",
        stage=stage_name,
        retry_stage=destination,
    )


def _repair_pass_recorded(
    run_dir: Path, stage_name: str, findings: list[dict], budget: int
) -> None:
    """Record findings a spent budget leaves unrepaired, and complete the run.

    The bound is what makes termination a property rather than a hope: a fix
    the verifier keeps rejecting cannot cycle. A later verdict still carrying
    findings is not an error and routes nowhere, so the findings are named in
    the event stream, where a developer meets them, and the run completes.
    """
    named = "; ".join(
        f"{finding.get('location')}: {finding.get('finding')}"
        for finding in findings
    )
    append_event(
        run_dir,
        f"repair pass budget of {budget} is spent; verification passed "
        f"still carrying repairable findings, which are recorded here and "
        f"left unrepaired: {named}",
        kind="repair-pass-recorded",
        stage=stage_name,
    )


# --------------------------------------------------------------------------
# A repair may reach a file outside the story's declared scope
#
# A repairable finding may name a file the story did not declare when that
# file states or consumes the rule the story changed, and says so in
# scope_extension. The coordinator decides each such reach against three
# bounds it holds itself — the execution rules' blocked paths, the resolved
# stage's own restrictions, and a reasoned scope.do_not_modify entry — and
# refuses one that crosses any, naming the bound. A refusal drops that
# finding from the pass and never stops the run: the verdict the accounting
# is handed records it as declined with the bound as its reason, so it is
# filed as a brief at completion rather than lost. A finding carrying no
# scope_extension is untouched by all of this. Nothing below writes a stage
# name, a category or an artifact name; no model is called.


#: The field a repairable finding carries to ask for an extension.
SCOPE_EXTENSION = "scope_extension"


def carries_a_reason(entry: str) -> bool:
    """Whether a scope.do_not_modify entry carries the developer's reason.

    True when non-empty text — the path or area — precedes a closing
    parenthetical holding at least one word character. The parenthetical is
    the one the entry ends with, found by walking back from its final ")" to
    the "(" that balances it, so a reason may itself carry parentheses. Shape
    alone is read: the reason's text is neither parsed nor scored, so a bare
    path and an empty parenthetical are the two things refused and any reason
    at all passes.

    Defined here rather than in plan_validation, which imports it back under
    the same name, because the plan-time reason check and the run-time
    do_not_modify bound below must read one definition and plan_validation
    already imports this module.
    """
    text = entry.rstrip()
    if not text.endswith(")"):
        return False
    depth = 0
    for position in range(len(text) - 1, -1, -1):
        character = text[position]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                reason, before = text[position + 1:-1], text[:position]
                return before.strip() != "" and re.search(r"\w", reason) is not None
    return False


#: The boundary between the paths one do_not_modify entry lists before its
#: parenthetical: a comma, a semicolon or a standalone "and" — the clause
#: boundary plan_validation's strictness check already splits an entry on.
_ENTRY_PATH_BOUNDARY = re.compile(r"[,;]|\band\b")


def _entry_paths(entry: str) -> list[str]:
    """The paths a reasoned do_not_modify entry lists before its closing
    parenthetical, each stripped — the path or area the reason is about.

    Planners write one entry for several files ("prompts/a.md, prompts/b.md
    and prompts/c.md (the reason)"), so the text is split on the clause
    boundary and each piece is a path in its own right. An entry naming one
    path yields that one piece.
    """
    text = entry.rstrip()
    depth = 0
    before = text
    for position in range(len(text) - 1, -1, -1):
        character = text[position]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                before = text[:position]
                break
    return [piece.strip() for piece in _ENTRY_PATH_BOUNDARY.split(before)
            if piece.strip()]


def _names_path(entry_path: str, path: str) -> bool:
    """Whether a do_not_modify entry's path equals `path` or is a directory
    prefix of it: `workflows/` and `workflows` both cover
    `workflows/story-workflow.json`, and `orchestration/story_coordinator.py`
    covers itself alone.
    """
    if not entry_path:
        return False
    if entry_path == path:
        return True
    directory = entry_path if entry_path.endswith("/") else entry_path + "/"
    return path.startswith(directory)


def do_not_modify_bound(story: dict | None, path: str) -> str | None:
    """The reasoned scope.do_not_modify entry that bars `path`, if any.

    An entry bars a path when it carries a reason — decided by
    `carries_a_reason`, the plan-time predicate — and any path its text
    before the parenthetical lists equals the path or is a directory prefix
    of it. A bare entry carrying no reason bars nothing: it is a planner's
    default rather than a developer's decision, which is the distinction the
    reason exists to draw. An area-style entry naming no path bars nothing
    either, since no piece of it equals or prefixes a repository path.
    """
    # Bound to `declared` rather than `scope`: the standing scan in
    # tests/test_suite_run_scope.py reports a decision under orchestration/
    # taken on a name called `scope`, which is the suite-run record's field
    # and not the story's.
    declared = (story or {}).get("scope") or {}
    for entry in declared.get("do_not_modify") or []:
        if not isinstance(entry, str) or not carries_a_reason(entry):
            continue
        if any(_names_path(one, path) for one in _entry_paths(entry)):
            return entry
    return None


@dataclass(frozen=True)
class RefusedExtension:
    """One extension the coordinator refused: the finding as the verdict
    carried it, the stage its category resolved to, and the bound crossed in
    the bound's own words.
    """

    finding: dict
    stage: str
    bound: str


@dataclass(frozen=True)
class ScopeExtensionCheck:
    """What the bounds decided about a verdict's repairable findings.

    `accepted` holds every finding the pass may carry, as the verdict carried
    it — a finding with no scope_extension is accepted without being asked,
    and one with an extension is accepted where no bound applies. `refused`
    holds the rest with the bound each crossed.
    """

    accepted: list[dict]
    refused: list[RefusedExtension]


def scope_extension_check(
    findings: list[dict],
    routes: dict,
    stage_names: list[str],
    stages: list[dict],
    story: dict | None,
    rules: dict,
) -> ScopeExtensionCheck:
    """Decide each finding carrying scope_extension against the three bounds.

    Pure over its inputs. A finding is refused when its path is under a
    repository-wide blocked path from the execution rules; when the stage its
    category resolves to declares a restriction that governs the path, so a
    tester's extension cannot reach the implementation — unlike an in-scope
    repairable finding, whose grant lifts the stage's confinement for that
    file; or when a reasoned scope.do_not_modify entry names the path or a
    directory it is under. The bounds are asked in that order and the first
    that applies is the one named. A finding whose category the routing table
    does not define is left in `accepted` before any bound is asked, whatever
    its path, because the routing that follows escalates naming the unknown
    category and the bounds have no stage to ask about — a refusal there
    would name an empty stage and hide the category behind it.
    """
    by_name = {stage["name"]: stage for stage in stages}
    blocked = list(rules.get("blocked_paths") or [])
    accepted: list[dict] = []
    refused: list[RefusedExtension] = []
    for finding in findings:
        extension = finding.get(SCOPE_EXTENSION) if isinstance(finding, dict) else None
        if not extension:
            accepted.append(finding)
            continue
        route = routes.get(finding.get("category"))
        stage_name = route.get("stage") if isinstance(route, dict) else None
        if stage_name not in by_name:
            accepted.append(finding)
            continue
        path = finding.get("path") or ""
        bound: str | None = None
        if is_blocked(path, blocked):
            prefix = next(one for one in blocked if path.startswith(one))
            bound = f"{path} is under {prefix}, a path the execution rules block for every stage"
        else:
            governing = next(
                (one for one in restrictions_on(by_name[stage_name]) if one.governs(path)),
                None,
            )
            if governing is not None:
                bound = f"{governing.wording}, and {path} is governed by it"
        if bound is None:
            entry = do_not_modify_bound(story, path)
            if entry is not None:
                bound = f"the story's scope.do_not_modify names it with a reason: {entry}"
        if bound is None:
            accepted.append(finding)
        else:
            refused.append(RefusedExtension(finding, stage_name, bound))
    return ScopeExtensionCheck(accepted, refused)


def refusal_reason(refused: RefusedExtension) -> str:
    """The one wording of a refusal: what the event says and what the
    accounting records as the declined finding's reason.
    """
    finding = refused.finding
    return (
        f"scope extension refused for {finding.get('location')}: {refused.stage} "
        f"may not change {finding.get('path')} for this pass because {refused.bound}"
    )


def refused_routed_slugs(
    check: ScopeExtensionCheck, routed_slugs=()
) -> tuple[str, ...]:
    """The routed slugs the refused findings' locations name, in the order
    the refusals were made and each slug once.

    This is the one match between a refusal and a routed finding:
    `verdict_after_refusals` keys its declined entries by it, and the
    accounting is handed it as `refused_slugs`, so the two cannot disagree
    about which routed finding a refusal was of. A check that refused nothing
    names nothing.
    """
    named: list[str] = []
    for one in check.refused:
        location = one.finding.get("location")
        for slug in routed_slugs:
            if (isinstance(slug, str) and slug and slug not in named
                    and story_inspection.names_a_slug(location, (slug,))):
                named.append(slug)
    return tuple(named)


def verdict_after_refusals(
    verdict: dict, check: ScopeExtensionCheck, routed_slugs=()
) -> dict:
    """The verdict handed to the accounting once the bounds have decided.

    Each refused finding is removed from repairable_findings, so the
    accounting does not count it as acted on, and a declined_findings entry
    is added for every routed slug its location names, carrying the refusal
    as its reason, so the accounting counts it as left with a reason and the
    completion filing files it as a brief. Where its location names no routed
    slug, the entry is keyed by the location itself — a record of the refusal
    in the shape the schema takes, matching no routed finding. A check that
    refused nothing returns the verdict it was given.

    Which routed slug a refusal names is decided by `refused_routed_slugs`,
    the same match the accounting is handed, so a slug this records as
    declined is a slug the accounting leaves whatever its accepted sibling
    did.
    """
    if not check.refused:
        return verdict
    removed = [id(one.finding) for one in check.refused]
    declined = list(verdict.get("declined_findings") or [])
    for one in check.refused:
        location = one.finding.get("location")
        named = refused_routed_slugs(
            ScopeExtensionCheck(accepted=(), refused=(one,)), routed_slugs
        )
        for slug in named or (str(location),):
            declined.append({"slug": slug, "reason": refusal_reason(one)})
    return {
        **verdict,
        "repairable_findings": [
            one for one in verdict.get("repairable_findings") or []
            if id(one) not in removed
        ],
        "declined_findings": declined,
    }


def _scope_extension_refused(
    run_dir: Path, stage_name: str, refused: RefusedExtension
) -> None:
    """One event per refusal, naming the location, the file, the stage and
    the bound, so a refusal is reconstructable from events.log alone.
    """
    append_event(
        run_dir,
        refusal_reason(refused),
        kind="scope-extension-refused",
        stage=stage_name,
    )


def repair_pass_extensions(
    run_dir: Path, stages: list[dict], state: RunState, stage_name: str, attempt: int
) -> list[tuple[str, str]]:
    """The (path, reason) of each granted extension this stage carries on
    this attempt, read off the pass record exactly as `repair_pass_grants`
    reads the paths, so the announcement and the grant cannot disagree.
    """
    return [
        (finding["path"], finding[SCOPE_EXTENSION])
        for finding in _repair_pass_findings(run_dir, stages, state, stage_name, attempt)
        if finding.get(SCOPE_EXTENSION)
    ]


# --------------------------------------------------------------------------
# Ending a run so it can be resumed
#
# A successful run's work is durable: _complete commits it. An escalated run's
# was not, so it lived in the working tree and survived exactly until someone
# checked out another branch — a normal thing to do while deciding what to do
# about an escalation. Resume cannot recover what the harness did not preserve,
# so the escalation commits too.
#
# It commits the same way _complete does — `git add -A` on the working tree —
# and since story-021 that is an accurate statement about what the run
# produced rather than a loose one. Neither commit was changed to make it so:
# the clean-tree pre-flight above establishes that the tree held nothing the
# run did not produce *before* any stage ran, so staging everything and
# staging what the run produced are the same set. Staging less would be the
# regression — it would commit a tree no stage validated and silently drop a
# real change no record happened to name.
#
# The guarantee has exactly one gap, and it is the pre-flight's one exclusion
# rather than a looseness here: a resume of a *crashed* run is not held to a
# clean tree, because nothing commits when a process dies and that working
# tree holds the run's own unfinished work. So a resumed crashed run is the
# one case where a terminal commit can stage more than the run produced.
#
# The escalation ends in *two* commits, and the second one is not bookkeeping
# for its own sake. Everything the escalation writes — state.json, both
# renderings of the event stream, the summary — has to be inside the commit,
# because a repository that tracks its run directory is otherwise left dirty by
# the very writes that record the escalation, and the checkout this exists to
# make safe is refused. But state.json records the sha of the commit it is
# committed in, and no commit can contain its own sha: the content is hashed
# into the identity being recorded. So the work is committed first, the sha of
# that commit is written to state.json, and a second commit carries the record
# on top. It is made even when it has nothing to add — a repository that
# ignores its run directory has nothing to record — so the branch an escalation
# leaves always has the same shape and the undo command named in the message
# can name one revision.
# --------------------------------------------------------------------------

#: Leads the escalation commit's subject. _complete's subject is
#: "<story-id>: <title>", so a subject beginning with a marker naming what the
#: commit is cannot be read as a completion in `git log --oneline`, in a PR
#: title, or by anyone scanning the branch.
ESCALATION_COMMIT_MARKER = "l5 escalated:"

#: How the escalation commit's changes are put back in the working tree. Named
#: in the body, because a developer deciding what to do about an escalation
#: should not have to work it out. Two revisions, because an escalation that
#: commits makes two commits — the work and the record of it — and both belong
#: back in the tree.
ESCALATION_UNDO_COMMAND = "git reset --mixed HEAD~2"

#: Leads the completion commit's body, and is what identifies a completion
#: commit on a branch. The subject alone cannot: "<story-id>: <title>" is a
#: shape any hand-written commit about the story can wear, and a pre-flight
#: that refused on it would refuse work the harness never did. The marker is
#: written by _complete and by nothing else, so a commit carrying both the
#: subject and this sentence is evidence that a run of this story finished.
COMPLETION_COMMIT_MARKER = "Implemented by the l5 harness story workflow."


def completion_commit_subject(story_id: str, title: str) -> str:
    """The completion commit's subject line.

    Extracted so the composition and the pre-flight that recognizes it are one
    fact: `completion_commits` matches on the prefix this builds, rather than
    on a second spelling of the same shape.
    """
    return f"{story_id}: {title}"


#: The paragraph the amended completion commit carries in addition to the
#: marker. Amending this story's escalation commit rewrites the only
#: trunk-visible record of the escalation, so what that record said has to
#: survive into the message replacing it: that the run escalated, that this
#: commit carries the whole working tree the escalation committed, and that
#: the escalation's undo command no longer describes the branch.
AMENDED_COMPLETION_NOTE = (
    "This run escalated before it finished, and this commit is that "
    "escalation's, amended rather than added to. It carries the whole "
    "working tree the escalation committed, and the escalation's undo "
    f"command ({ESCALATION_UNDO_COMMAND}) no longer applies to it."
)


def completion_commit_message(
    state: RunState, title: str, *, amended: bool = False
) -> str:
    """The completion commit's message: the story it finished, and by what.

    Without `amended` this is byte for byte what `_complete` composed inline
    before it was extracted. The subject names the story as a reader scanning
    the branch would want it named; the body is the single marker sentence,
    which is what makes the commit recognizable as a finished run's rather
    than as anyone's commit about the story.

    `amended` is for the one case in which `_complete` rewrites a commit
    instead of adding one — a clean tree standing on this story's own
    escalation commit, see `_complete`. It adds a paragraph *below* the
    marker: the subject and the marker sentence are unchanged, so the amended
    commit is recognised by exactly what recognises every other completion,
    and there is still one composition of both.
    """
    body = COMPLETION_COMMIT_MARKER
    if amended:
        body = f"{body}\n\n{AMENDED_COMPLETION_NOTE}"
    return (
        f"{completion_commit_subject(state.story_id, title)}\n"
        f"\n"
        f"{body}"
    )


#: What a squash merge writes before each folded commit's subject when it
#: composes the merge commit's body. Stripped before a line is compared
#: against the completion subject shape, so a completion that reaches the
#: trunk as one bullet of a squashed body is still recognised.
MERGE_BULLETS = ("* ", "- ")


def _carries_completion_subject(message: str, story_id: str) -> bool:
    """Whether any line of `message` names `story_id` in the completion shape.

    Any line rather than the subject line, because a merge method decides
    where a commit's subject ends up: a squash merge writes the pull request's
    title as the subject and the folded commits' messages into the body, so
    the completion subject arrives as a bulleted line partway down. The bullet
    is stripped before the comparison; nothing else about the line is
    tolerated, so a commit whose message merely mentions the story is not a
    completion.
    """
    prefix = completion_commit_subject(story_id, "")
    for line in message.splitlines():
        text = line.strip()
        for bullet in MERGE_BULLETS:
            if text.startswith(bullet):
                text = text[len(bullet):].strip()
                break
        if text.startswith(prefix) and text != prefix:
            return True
    return False


def completion_commits(target_root: Path, branch: str, story_id: str) -> list[str]:
    """Commits reachable from `branch` that a finished run of `story_id` made.

    One "<abbrev sha> <subject>" line per match, newest first, and an empty
    list when there is nothing to say. A commit qualifies on two pieces of
    evidence together: a line of the completion shape for *this* story, and
    `COMPLETION_COMMIT_MARKER`. Neither alone is enough — the subject shape is
    one a hand-written commit can wear, and the marker without a line naming
    this story would report another story's run.

    Both are required *anywhere in the message*, subject and body together,
    rather than in particular fields of it, and that is what makes a
    squash-merged completion recognisable: a squash writes the pull request's
    title as the subject and the folded commits' messages into the body, so
    matching the completion subject against `%s` alone found nothing however
    the run had ended. Requiring both together still holds, so a commit
    carrying only the subject shape and one carrying only the marker are both
    rejected. Together with `_complete` amending this story's escalation
    commit rather than adding an empty one on top of it, no merge method can
    drop the completion any longer: a merge commit keeps it, a rebase has a
    non-empty commit to replay, and a squash folds its message into the body
    where this reads it.

    Reachability from the branch is the whole test, deliberately rather than
    `<base>..<branch>`. Being ahead of a base is no longer the same thing as
    having finished: since story-020 an escalation leaves two commits, so an
    ahead-of-base test would refuse every escalated resume. It is also base-
    free, which is what makes it still hold when the developer is standing on
    the story branch a completed run left them on.

    The target repository is only read: no commit, no branch, no checkout, no
    index change, no stash. A branch that does not exist, a root that is not a
    git repository, and a git invocation that fails all return an empty list —
    the check refuses only on positive evidence of finished work, the same
    one-directional bias `dirty_paths` and `unchanged_since_escalation` take.
    """
    if _git(target_root, "rev-parse", "--verify", branch).returncode != 0:
        return []
    result = _git(
        target_root, "log", "--format=%h%x1f%s%x1f%b%x1e", branch
    )
    if result.returncode != 0:
        return []
    found = []
    for record in result.stdout.split("\x1e"):
        fields = record.strip("\n").split("\x1f")
        if len(fields) != 3:
            continue
        sha, subject, body = fields
        message = f"{subject}\n{body}"
        if COMPLETION_COMMIT_MARKER not in message:
            continue
        if not _carries_completion_subject(message, story_id):
            continue
        # The commit's real subject, not the line that matched: a squashed
        # completion's subject is the pull request's title, and that is what a
        # reader scanning the trunk sees.
        found.append(f"{sha} {subject}")
    return found


#: The escalation commit's subject, written once as a template so that the
#: composition below and the reader below it are one fact rather than two
#: spellings — the pairing `completion_commit_subject` already gives
#: `completion_commits`. `escalated_story` derives its own pattern from this
#: string, so a change to the subject's shape moves both halves together.
ESCALATION_SUBJECT_TEMPLATE = (
    f"{ESCALATION_COMMIT_MARKER} {{story_id}} stopped at {{stage}}"
)


def escalation_commit_subject(story_id: str, stage: str) -> str:
    """The escalation commit's subject line."""
    return ESCALATION_SUBJECT_TEMPLATE.format(story_id=story_id, stage=stage)


def escalated_story(subject: str) -> str | None:
    """The story id an escalation commit's subject names, or None.

    The pattern is built from `ESCALATION_SUBJECT_TEMPLATE` rather than written
    beside it, so what this reads is exactly what `escalation_commit_subject`
    writes. A subject of any other shape — a completion, a planning commit,
    anyone's commit about the story — is not an escalation and yields None.
    """
    pattern = re.escape(ESCALATION_SUBJECT_TEMPLATE)
    pattern = pattern.replace(re.escape("{story_id}"), r"(?P<story_id>\S+)")
    pattern = pattern.replace(re.escape("{stage}"), r"\S.*")
    match = re.fullmatch(pattern, subject.strip())
    return match.group("story_id") if match else None


def _head_escalated(target_root: Path) -> str | None:
    """The story id HEAD's subject names as an escalation, or None.

    Reads only, and answers None whenever it cannot establish otherwise — an
    unreadable HEAD, a root that is not a git repository, a subject of any
    other shape. `_complete` amends only on a positive answer, the same
    one-directional bias `completion_commits` and `dirty_paths` take: the cost
    of answering None is the empty commit the harness already wrote, and the
    cost of a false yes is a rewritten commit that was not this run's.
    """
    result = _git(target_root, "log", "-1", "--format=%s")
    if result.returncode != 0:
        return None
    return escalated_story(result.stdout.strip())


#: Leads a pause commit's subject, and is deliberately not
#: `ESCALATION_COMMIT_MARKER`: `escalated_story` builds its pattern from the
#: escalation's template, so a pause commit reusing that subject would be
#: reported as an escalation by `_head_escalated` and amended over by
#: `_complete`. A pause is not an escalation and its commit says so in the
#: first thing a reader of `git log --oneline` meets.
PAUSE_COMMIT_MARKER = "l5 paused:"

#: How a pause commit's changes are put back in the working tree. Two
#: revisions, for the reason the escalation's undo command names two: a pause
#: that commits makes the same two commits — the run's own record of the pause,
#: and the work on top of it.
PAUSE_UNDO_COMMAND = "git reset --mixed HEAD~2"

#: The pause commit's subject, written once so the composition below and
#: `paused_story` are one fact rather than two spellings — the pairing
#: `ESCALATION_SUBJECT_TEMPLATE` already gives the escalation.
PAUSE_SUBJECT_TEMPLATE = (
    f"{PAUSE_COMMIT_MARKER} {{story_id}} waiting for capacity at {{stage}}"
)


def pause_commit_subject(story_id: str, stage: str) -> str:
    """The pause commit's subject line."""
    return PAUSE_SUBJECT_TEMPLATE.format(story_id=story_id, stage=stage)


def paused_story(subject: str) -> str | None:
    """The story id a pause commit's subject names, or None.

    The counterpart of `escalated_story`, and the reason there are two readers
    rather than one: each answers for its own kind only, so a pause commit is
    never read as an escalation and an escalation commit is never read as a
    pause. The pattern is built from `PAUSE_SUBJECT_TEMPLATE`, so what this
    reads is exactly what `pause_commit_subject` writes.
    """
    pattern = re.escape(PAUSE_SUBJECT_TEMPLATE)
    pattern = pattern.replace(re.escape("{story_id}"), r"(?P<story_id>\S+)")
    pattern = pattern.replace(re.escape("{stage}"), r"\S.*")
    match = re.fullmatch(pattern, subject.strip())
    return match.group("story_id") if match else None


def pause_commit_message(state: RunState, reason: str) -> str:
    """The pause commit's message: what it is, why, and how to undo it.

    It says outright that nothing about the work has been rejected and that a
    resume continues it, because the two commits a pause leaves are otherwise
    indistinguishable at a glance from the two an escalation leaves, and the
    difference between them is exactly whether a developer has anything to
    look at.
    """
    stage = state.current_stage or "no stage"
    return (
        f"{pause_commit_subject(state.story_id, stage)}\n"
        f"\n"
        f"The run paused because capacity ran out, and this commit is a "
        f"holding place for what it left in the working tree, so the work "
        f"survives a checkout of another branch or a wait of several days.\n"
        f"\n"
        f"Nothing about the work has been rejected. No stage failed, no "
        f"verifier judged it, and no attempt was spent: the invocation did "
        f"not run. Running `l5-run {state.story_id}` again resumes this run "
        f"at stage {stage}, with every counter exactly where it is now.\n"
        f"\n"
        f"Paused because: {reason}\n"
        f"\n"
        f"To put these changes back in the working tree:\n"
        f"    {PAUSE_UNDO_COMMAND}\n"
    )


def escalation_commit_message(state: RunState, reason: str) -> str:
    """The escalation commit's message: what it is, why, and how to undo it.

    The subject names the stage execution stopped at. The body says outright
    that this is a holding place rather than a decision about the work, carries
    the escalation reason, and names the command that returns the changes to
    the working tree.
    """
    stage = state.current_stage or "no stage"
    return (
        f"{escalation_commit_subject(state.story_id, stage)}\n"
        f"\n"
        f"The run escalated and this commit is a holding place for what it "
        f"left in the working tree, so the work survives a checkout of another "
        f"branch. It is not a decision about that work: the story did not "
        f"finish and nothing here has been accepted or reviewed.\n"
        f"\n"
        f"Escalation reason: {reason}\n"
        f"\n"
        f"To put these changes back in the working tree:\n"
        f"    {ESCALATION_UNDO_COMMAND}\n"
    )


def commit_escalated_work(
    target_root: Path,
    state: RunState,
    reason: str,
    *,
    run_dir: Path | None = None,
    message: str | None = None,
) -> str:
    """Open the escalation's commit of what the run left, and name it.

    `message` is what the commit carries, defaulting to the escalation's own.
    It is a parameter because a capacity pause makes the same two commits for
    the same reason — work left uncommitted for days is what this exists to
    protect, and the clean-tree pre-flight refuses a resume of a dirty tree —
    and differs from an escalation only in what it says. What it must not
    share is the *subject*: `escalated_story` and `_head_escalated` identify
    an escalation commit by its subject, and a pause is not one.

    This is the first of the two commits an escalation makes: the run's own
    record of the escalation — state.json, both renderings of the event stream
    — so that the sha it returns can be written into state.json and committed,
    with the work, by `commit_escalated_tree` on top of it. A commit cannot
    carry its own sha, and that is the whole reason the record and the work are
    two commits rather than one.

    Returns the commit, or "" when the escalated run left nothing to commit at
    all — an escalation with a clean tree records no commit, commits nothing
    further, and is not an error. It establishes nothing about the tree it
    commits, exactly as _complete does not: both stage whatever the working
    tree holds.

    `--allow-empty`, because a repository that ignores its run directory has no
    record to commit here and must still leave the same two-commit shape: the
    undo command named in the message names one revision and has to be right in
    both shapes.
    """
    if not _git(target_root, "status", "--porcelain").stdout.strip():
        return ""
    if run_dir is not None:
        _git(target_root, "add", "-A", "--", str(run_dir))
    committed = _git(
        target_root,
        "commit",
        "--allow-empty",
        "-m",
        message if message is not None else escalation_commit_message(state, reason),
    )
    return _revision(target_root) if committed.returncode == 0 else ""


def commit_escalated_tree(
    target_root: Path,
    state: RunState,
    reason: str,
    *,
    message: str | None = None,
) -> None:
    """Commit the work the escalated run left, on top of its record.

    The second of the two commits, and the branch tip an escalation leaves. It
    carries the same message as the commit it sits on, because it is the same
    escalation: a reader scanning the branch should meet the escalation rather
    than a bookkeeping entry. `message` is that message, defaulted to the
    escalation's own for the reason it is on `commit_escalated_work`.
    """
    _git(target_root, "add", "-A")
    _git(
        target_root,
        "commit",
        "--allow-empty",
        "-m",
        message if message is not None else escalation_commit_message(state, reason),
    )


def _readable_json(path: Path):
    """An artifact's contents, or None when it is absent or unparseable.

    A report is not the place an unreadable artifact raises: the coordinator
    already skips an artifact it cannot read rather than failing out of one,
    and a summary that cannot be written is worse than a summary missing a
    section whose source is unreadable.
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _issue_line(issue: dict) -> str:
    """One blocking issue, carrying the four fields the verifier recorded.

    Rendered, never summarized: a reader must be able to act on the finding
    without opening the artifact it came from.
    """
    return (
        f"- [{issue.get('severity')}] {issue.get('issue')}\n"
        f"  Location: {issue.get('location')}\n"
        f"  Required behavior: {issue.get('required_behavior')}"
    )


def _outstanding_issues_section(run_dir: Path, state: RunState) -> str | None:
    """What the last verdict said still blocks acceptance.

    Rendered from `verification-result.json` and only when that artifact
    records a failure. A missing-artifact, blocked-path or clean-clone
    escalation has no failing verdict behind it, and an empty section under a
    heading is the failure mode this exists to remove — so nothing is emitted
    at all.

    The opening line names both the artifact read and the iteration file
    holding the same verdict, because the verdict may predate the stage the
    run escalated at and a reader has to be able to tell.
    """
    verdict = _readable_json(run_dir / "verification-result.json")
    if not isinstance(verdict, dict) or verdict.get("status") != "failed":
        return None
    blocks = [
        f"## Outstanding Issues\n"
        f"From verification-result.json, the same verdict recorded as "
        f"verification/iteration-{state.verification_iterations}.json. It may "
        f"predate the stage this run escalated at.",
        *(_issue_line(issue) for issue in verdict.get("blocking_issues", [])),
    ]
    return "\n\n".join(blocks)


def _retry_history_section(run_dir: Path) -> str | None:
    """Each retry this run took, as `retry-history.json` recorded it.

    The artifact is created at the first retry and never in advance, so its
    absence is itself evidence: a run that never retried gets no section
    rather than an empty one.
    """
    records = load_retry_records(run_dir)
    if not records:
        return None
    blocks = ["## Retry History"]
    for record in records:
        blocks.append(
            f"### Attempt {record.get('attempt')}, rerouted to "
            f"{record.get('retry_stage')}"
        )
        blocks.extend(_issue_line(issue) for issue in record.get("blocking_issues", []))
        blocks.append(f"Archived at {record.get('archive_directory')}")
    return "\n\n".join(blocks)


def _recommended_investigation_section(run_dir: Path, state: RunState) -> str:
    """Where to start, composed from facts already in hand.

    Every conditional here is over something recorded — which artifacts are on
    disk, and whether the escalation made a commit — never over the escalation
    reason's text. The coordinator renders what it knows; it does not classify
    why the run stopped.
    """
    # The summary itself is left off the list it renders. It exists on a
    # re-escalation and not on a first one, and a report naming itself as
    # something to open would be the only part of this section that differed
    # between the two for no reason a reader could use.
    artifacts = sorted(
        path.name
        for path in run_dir.iterdir()
        if path.is_file() and path.name != "escalation-summary.md"
    )
    blocks = ["## Recommended Investigation"]
    if artifacts:
        listing = "\n".join(f"- {name}" for name in artifacts)
        blocks.append(f"Artifacts this run left in {run_dir}:\n\n{listing}")
    if state.escalation_commit:
        blocks.append(
            f"The escalated work is committed on branch {state.branch} at "
            f"{state.escalation_commit}, so it survives a checkout of another "
            f"branch. To put those changes back in the working tree:\n\n"
            f"    {ESCALATION_UNDO_COMMAND}"
        )
    # A developer deciding whether to resume should decide with the run's whole
    # cost in front of them, and with the allowance the resume restores stated
    # rather than inferred. Both are facts every escalation has, so neither
    # branches on the reason's text. The total comes from the record and the
    # entry index rather than from retry_count, which this story made
    # entry-scoped.
    blocks.append(
        f"This run has taken {accumulated_attempts(run_dir, state)} attempt(s) "
        f"in all, across {state.resume_count + 1} entry(s) of it. Resuming "
        f"opens another entry and restores the run's attempt allowance: the "
        f"retry count goes back to zero, so a failing verdict after the resume "
        f"takes a retry rather than stopping the run again at the ceiling. "
        f"Nothing already taken is discarded — the run's records account for "
        f"every attempt on both sides of a resume, and each earlier entry's "
        f"artifacts move under resumes/resume-K/."
    )
    # What the run cost, on the same terms as the attempts above: a fact every
    # escalation has, read off the record rather than off the entry-scoped
    # allowance, and stated without branching on the escalation's reason.
    blocks.append(
        f"This run has cost {format_usd(recorded_cost(run_dir))} in all, as "
        f"cost.json records it; {format_usd(state.entry_cost_usd)} of that is "
        f"the current entry's spend, which is what a run ceiling is compared "
        f"against and which resuming returns to zero."
    )
    # The rule for every escalation, stated without branching on this one's
    # reason: which stops resume as they stand is decided at the guard's call
    # site in run_story from the recorded counters and the loaded rules, and
    # this block says the same split so the developer is not sent to change
    # something the resume itself is about to restore.
    blocks.append(
        f"`l5-run {state.story_id}` resumes this run at the stage it stopped "
        f"at ({state.current_stage}); `--stage <stage>` overrides that and "
        f"enters somewhere else. A run stopped because its retries were spent, "
        f"its self-route budget was spent, or a cost ceiling was reached "
        f"resumes with nothing changed, because the resume restores what "
        f"stopped it. Every other escalation is refused while the story "
        f"artifact, the branch and the harness are all unchanged, because it "
        f"would reach the same point the same way."
    )
    return "\n\n".join(blocks)


def escalation_summary(run_dir: Path, state: RunState, reason: str) -> str:
    """The escalation report, composed from the run's recorded facts.

    A developer meeting this file should not have to reconstruct the run to
    learn what went wrong. Every section renders an artifact that already
    exists — nothing new is written to disk — and a section whose source is
    absent is omitted entirely rather than emitted empty.

    The first four sections are what they have always been, and `## Reason`
    stays immediately after `## Status` so `escalation_reason`'s split reads
    the same text it has always read.
    """
    blocks = [
        f"# {state.story_id} Escalation Summary",
        f"## Status\nEscalated",
        f"## Reason\n{reason}",
        f"## Where Execution Stopped\nStage: {state.current_stage}, "
        f"retry count: {state.retry_count}",
        _outstanding_issues_section(run_dir, state),
        _retry_history_section(run_dir),
        f"## Where to Look\nSee events.log for the run history and the "
        f"verification/ directory for verifier findings.",
        _recommended_investigation_section(run_dir, state),
    ]
    return "\n\n".join(block for block in blocks if block) + "\n"


def escalation_reason(run_dir: Path) -> str | None:
    """The reason the escalation summary recorded, for a message only.

    Nothing routes on this. The resume guard decides entirely from state.json,
    and this is read afterwards so a refusal can say what the run escalated
    for; a missing or reshaped summary costs the message a sentence and changes
    no decision.
    """
    path = run_dir / "escalation-summary.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if "## Reason" not in text:
        return None
    return text.split("## Reason", 1)[1].split("##", 1)[0].strip() or None


def unchanged_since_escalation(
    state: RunState, story_text: str, target_root: Path, harness_root: Path
) -> list[str]:
    """Evidence that resuming would reach the same point the same way.

    Three comparisons, each of which must be *establishable* before it can say
    anything: the story artifact against the digest recorded at run start, the
    branch against the escalation commit the harness made, and the harness
    against the revision recorded when the run escalated.

    Returns the evidence only when all three are establishable and identical,
    and an empty list otherwise. Anything the guard cannot establish counts as
    not-the-same, so an absent digest, an escalation that committed nothing, a
    target whose HEAD cannot be read, or a harness root that is not a git
    repository produces no refusal rather than a false one.

    A run that stopped on an exhausted self-route budget is exempted ahead of
    all three, and for a different reason than they are: not that the guard
    cannot establish sameness, but that sameness is the wrong conclusion. The
    resume zeroes `self_route_count`, so the stage is entered with a budget it
    did not have when it stopped — the resource whose exhaustion stopped it.
    A run stopped at the retry ceiling and a run stopped on a cost ceiling are
    exempted on the same reasoning, but at the guard's call site in `run_story`
    rather than here, so this function's text and its three comparisons stay as
    they are.

    The third comparison has two forms, decided by whether the harness and the
    target are one checkout. When they are separate — the deployment the guard
    was written for — it is the recorded-revision comparison, unchanged. When
    they are one tree, what leg two established covers the harness source only
    while the working tree is standing on the branch it spoke about: leg two
    says that branch is exactly the escalation commit, and the `status
    --porcelain` leg beside it says the tree is clean, not that it is that
    branch's tree. So the shared-checkout form requires the tree to resolve to
    `state.branch`, and standing anywhere else it establishes nothing and the
    resume proceeds. The recorded revision cannot be reached for instead — it
    is recorded as the first act of `_escalate`, before the two escalation
    commits move HEAD, so a shared-root comparison of it always differs and the
    guard could never refuse.

    What that gives up is deliberate and chosen: under one checkout an
    off-branch resume with genuinely nothing changed is no longer refused,
    because the guard cannot tell that case from a harness fixed on the base
    without a second definition of "has the harness changed". Permitting a
    pointless resume is the direction the guard's one-directional bias already
    takes; refusing a needed one is not.

    The branch comparison resolves `state.branch~1` rather than `HEAD~1`,
    because the story branch is what it means and HEAD is only the same thing
    when the developer happens to be standing on it. The `status --porcelain`
    leg beside it needs no such repointing and keeps its current meaning: the
    working tree is one tree whatever branch is checked out, and the dirty-tree
    pre-flight above this guard has already refused for every status that could
    reach it, so what it establishes is unchanged by where the run is standing.

    A tree hash over the harness's own source directories was considered and
    not taken. It is a second definition of "has the harness changed" living
    beside the branch comparison that already answers it, and it would have to
    choose which directories count — a choice nothing else in the harness
    makes.
    """
    # A run stopped on an exhausted self-route budget is exempted before any
    # comparison, because for that run the guard's conclusion is false rather
    # than unestablishable. The three comparisons below would all hold — the
    # artifact, the branch and the harness are untouched — and the run would
    # still not reach the same point the same way, because the resume restores
    # the very resource whose exhaustion stopped it: the resume path sets
    # `self_route_count` back to zero, saying in as many words that a resumed
    # stage starts with its full budget. Refusing here tells a developer
    # nothing has changed while the harness is about to change it.
    #
    # Read off the live counter rather than the escalation's reason text, which
    # is prose the guard would have to match. The counter is what the budget
    # comparison in `self_route` reads, so this asks the same question that
    # stopped the run.
    if state.self_route_count:
        return []
    if not state.story_digest or state.story_digest != story_digest(story_text):
        return []
    evidence = [
        "the story artifact is byte for byte the one the escalated run read"
    ]

    # The escalation commit is the branch tip's parent, not the tip: the record
    # of its sha is committed on top of it, because a commit cannot carry its
    # own sha. A branch a developer has committed on since therefore fails this
    # comparison, which is what it is for.
    #
    # Resolved against `state.branch` rather than HEAD, because the story
    # branch is what the comparison means and HEAD is only the same thing when
    # the developer happens to be standing on it. The guard runs above the
    # checkout — it refuses, so it stays above every act — so an off-branch
    # resume asked about HEAD could never establish sameness and could never
    # refuse.
    if (
        not state.escalation_commit
        or _revision(target_root, f"{state.branch}~1") != state.escalation_commit
    ):
        return []
    porcelain = _git(target_root, "status", "--porcelain")
    if porcelain.returncode != 0 or porcelain.stdout.strip():
        return []
    evidence.append(
        f"branch {state.branch} is exactly the escalation commit "
        f"{state.escalation_commit[:12]}, with nothing uncommitted"
    )

    # Under one checkout the branch comparison above stands for the harness
    # comparison only while the working tree is standing on the branch it spoke
    # about. Leg two established that `state.branch` is exactly the escalation
    # commit, and the `status --porcelain` leg beside it established that the
    # tree is clean — neither says the tree is *that* branch's, so a developer
    # who checked out the base and committed a harness fix there is standing in
    # a tree the two legs above have said nothing about. Standing anywhere else
    # this leg therefore establishes nothing and the resume proceeds, which
    # permits a pointless resume where nothing changed rather than refusing a
    # needed one where the harness did.
    #
    # Both revisions go through `_revision`, so one that cannot be read is ""
    # and falls to the not-established answer the rest of the guard gives.
    # The recorded revision cannot serve here — it is recorded before the
    # escalation commits move HEAD, so it always differs — and a tree hash over
    # the harness's source directories was rejected rather than reached for: it
    # is a second definition of "has the harness changed" beside the one already
    # answering it, and it would have to choose which directories count.
    if same_repository(target_root, harness_root):
        standing = _revision(target_root)
        if not standing or standing != _revision(target_root, state.branch):
            return []
        evidence.append(
            f"the harness is the same checkout as the target and the working "
            f"tree is standing on {state.branch}, so it is covered by the "
            f"branch comparison above"
        )
        return evidence

    if not state.harness_revision or _revision(harness_root) != state.harness_revision:
        return []
    evidence.append(
        f"the harness is still at revision {state.harness_revision[:12]}"
    )
    return evidence


def _resume_refusal(
    story_path: Path, run_dir: Path, state: RunState, evidence: list[str]
) -> str:
    reason = escalation_reason(run_dir)
    lines = [
        f"{state.story_id} escalated at stage {state.current_stage} and nothing "
        f"establishable has changed since:",
        *(f"  - {item}" for item in evidence),
    ]
    if reason:
        lines.append(f"It escalated because: {reason}")
    lines.append(
        f"Resuming now would reach the same point the same way. Amend "
        f"{story_path}, change the code on branch {state.branch}, or update the "
        f"harness, then run the story again."
    )
    return "\n".join(lines)


def refuse(header: str, problems: list[str], guidance: str) -> int:
    """The one refusal path: exit 1, one message per problem.

    Every refusal that has problems to enumerate goes through here — the two
    story-artifact refusals, the clean-tree one, and l5-plan's plan-time
    validation — so the shape stays a single code path rather than a copy per
    reason. What differs between them is the sentence above the list and the
    sentence below it, which is what a caller supplies.

    It is public because plan time and pre-flight must print a given problem
    identically: the same defect in the same artifact produces the same text
    whether it is caught when the artifact is written or when it is run.
    """
    print(header, file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(guidance, file=sys.stderr)
    return 1


def refuse_bad_story(story_path: Path, problems: list[str]) -> int:
    return refuse(
        f"{story_path} is not a valid story artifact:",
        problems,
        "Fix the artifact or re-run planning before executing the story.",
    )


def _refuse_unresolved_mandate(problems: list[str]) -> int:
    """Refuse work whose mandate does not resolve to a human.

    One more thin caller of the shared refusal, so this reads like every other
    refusal the harness prints. What it adds around the walk's own problems is
    the one thing the walk deliberately does not say: where a mandate comes
    from, and therefore what the repair is. It names no kind it was not given.
    """
    return refuse(
        "This work has no mandate that resolves to a human:",
        problems,
        "A mandate is written by the harness process that observed the "
        "authorizing act and never by an agent, so the repair is to have the "
        "act observed again rather than to write the block by hand. Nothing "
        "was created and no agent was invoked.",
    )


def _refuse_bad_routing(workflow: dict, problems: list[str]) -> int:
    """Refuse a workflow whose retry routing cannot be followed.

    The definition is wrong, not the story and not the tree, so the guidance
    points at the file that has to change rather than at anything a developer
    could do to their repository.
    """
    return refuse(
        f"Workflow '{workflow['name']}' declares retry routes that cannot be "
        f"followed:",
        problems,
        "Fix the workflow definition's retry routing before running a story "
        "under it.",
    )


def _refuse_bad_self_routes(workflow: dict, problems: list[str]) -> int:
    """Refuse a workflow whose self-route budget is not a count.

    Thin, like every other caller of `refuse`. The definition is wrong, not the
    story and not the tree, so the guidance points at the file that has to
    change.
    """
    return refuse(
        f"Workflow '{workflow['name']}' declares a self-route budget that "
        f"cannot be spent:",
        problems,
        "Fix the workflow definition's self-route budgets before running a "
        "story under it.",
    )


def _refuse_bad_correction_pass(workflow: dict, problems: list[str]) -> int:
    """Refuse a workflow whose correction pass has nowhere to enter.

    Thin, like every other caller of `refuse`. The definition is wrong, not the
    story and not the tree, so the guidance points at the file that has to
    change.
    """
    return refuse(
        f"Workflow '{workflow['name']}' declares a correction pass that cannot "
        f"be entered:",
        problems,
        "Fix the workflow definition's correction_pass stage before running a "
        "story under it.",
    )


def _refuse_bad_repair_pass(workflow: dict, problems: list[str]) -> int:
    """Refuse a workflow whose repair pass cannot be spent or cannot route.

    Thin, like every other caller of `refuse`. The definition is wrong, not the
    story and not the tree, so the guidance points at the file that has to
    change.
    """
    return refuse(
        f"Workflow '{workflow['name']}' declares a repair pass that cannot "
        f"be taken:",
        problems,
        "Fix the workflow definition's repair_pass declaration before running "
        "a story under it.",
    )


def _refuse_bad_cost_ceilings(workflow: dict, problems: list[str]) -> int:
    """Refuse a workflow whose declared cost ceiling is not a number.

    Thin, like every other caller of `refuse`. The definition is wrong, not the
    story and not the tree, so the guidance points at the file that has to
    change.
    """
    return refuse(
        f"Workflow '{workflow['name']}' declares a cost ceiling that cannot be "
        f"applied:",
        problems,
        "Fix the workflow definition's cost ceilings before running a story "
        "under it.",
    )


def _refuse_bad_applies_when(workflow: dict, problems: list[str]) -> int:
    """Refuse a workflow that does not say when it is the one to plan under.

    Thin, like every other caller of `refuse`. The definition is wrong, not the
    story and not the tree, so the guidance points at the file that has to
    change.
    """
    return refuse(
        f"Workflow '{workflow['name']}' does not say when it applies:",
        problems,
        "Give the workflow definition an applies_when saying when it is the "
        "right workflow to plan under, before running a story under it.",
    )


def _budget_stopped(run_dir: Path, stage_name: str, reason: str) -> None:
    """Say that the run stopped on a cost ceiling, in its own event kind.

    Its own kind rather than the `escalated` one that follows it, so a reader
    of events.log can tell a budget stop from an escalation about the work.
    The stop itself goes through the existing escalation path — the commit, the
    summary and the resume path are exactly what they are for any other
    escalation — and this line is what distinguishes it.
    """
    append_event(
        run_dir,
        f"stopped on a cost ceiling: {reason}",
        kind="budget-stopped",
        stage=stage_name,
    )


def refuse_unknown_workflow(unknown: harness_config.UnknownWorkflow) -> int:
    """Refuse a workflow asked for by a name no definition under workflows/ has.

    Thin, like every other caller of `refuse`. The problem it enumerates is
    composed by harness_config and names the workflow asked for beside the
    definitions the harness holds, so the refusal is actionable without
    listing the directory.

    Public for the reason `refuse_bad_story` is: a story artifact naming an
    undefined workflow is refused when the planning session ends and again at
    pre-flight, and the two must report it identically rather than from two
    wordings that agree today.
    """
    return refuse(
        f"No workflow definition named '{unknown.workflow}':",
        unknown.problems,
        "Name a workflow the harness defines, or add its definition under "
        "workflows/, before running a story under it.",
    )


def _refuse_unresolved_workflow_token(
    unresolved: harness_config.UnresolvedWorkflowToken,
) -> int:
    """Refuse a workflow that references configuration the harness cannot answer.

    Thin, like every other caller of `refuse`. The header names the workflow
    and each problem names the token, so the pair a developer needs is in the
    message rather than one of them being left to inference.
    """
    return refuse(
        f"Workflow '{unresolved.workflow}' references configuration the harness "
        f"cannot resolve:",
        unresolved.problems,
        "Fix the workflow definition's configuration references before running "
        "a story under it.",
    )


def _refuse_undeclared_config_keys(target_root: Path, problems: list[str]) -> int:
    """Refuse a run whose configuration carries a key the harness does not read.

    Thin, like every other caller of `refuse`. The configuration is wrong, not
    the story and not the tree, so the guidance names the file to edit and the
    edit to make.
    """
    return refuse(
        f"{target_root / '.harness' / 'config.yaml'} carries configuration keys "
        f"the harness does not read:",
        problems,
        "Remove or correct each key before running a story.",
    )


def _refuse_bad_pause_wait(target_root: Path, problems: list[str]) -> int:
    """Refuse a run whose configured pause wait is not a duration.

    Thin, like every other caller of `refuse`. The configuration is wrong, not
    the story and not the tree, so the guidance names the file to edit and what
    the key may hold.
    """
    return refuse(
        f"{target_root / '.harness' / 'config.yaml'} configures a pause wait "
        f"that cannot be compared against a wait:",
        problems,
        "Set max_pause_wait_seconds to a non-negative number of seconds, or "
        "remove it — removed, the harness never waits and a capacity pause "
        "always exits.",
    )


def _refuse_bad_suite_turn_wait(target_root: Path, problems: list[str]) -> int:
    """Refuse a run whose configured suite-turn wait is not a count.

    Thin, like `_refuse_bad_pause_wait` beside it and for its reason: the
    configuration is wrong, not the story and not the tree, so the guidance
    names the file to edit and what the key may hold.
    """
    return refuse(
        f"{target_root / '.harness' / 'config.yaml'} configures a suite-turn "
        f"wait that cannot be compared against a wait:",
        problems,
        "Set suite_turn_wait_suite_runs to a non-negative number of suite "
        "runs, or remove it — removed, a run waits five suite runs' worth of "
        "time, as measured on this machine, for another run of the same "
        "repository to finish its suite, or sixty minutes where too few suite "
        "runs have been measured here; zero takes the turn only when it is "
        "free.",
    )


def _refuse_dirty_tree(target_root: Path, paths: list[str]) -> int:
    """Refuse a run whose target tree already holds work no stage produced.

    The friction here is the point of contact a developer meets most often, so
    the message names every dirty path and says what clears it.
    """
    return refuse(
        f"{target_root} has uncommitted changes, so a run starting here could "
        f"not establish that what it commits is what it produced:",
        paths,
        "Commit or stash them, then run the story again.",
    )


def _refuse_checkout(branch: str, problems: list[str]) -> int:
    """Refuse a run that cannot stand on its story branch.

    The checkout is the run's first act, so a checkout git will not make is
    refused the way every neighbouring pre-flight refuses rather than raising
    out of a run: exit 1, the branch named, one line per path git named, and
    guidance saying what clears them. Thin, like every other caller of
    `refuse`.
    """
    return refuse(
        f"Branch {branch} could not be checked out, so this run cannot stand "
        f"on the branch it would write to:",
        problems,
        f"Commit or stash the paths above, or check out {branch} by hand, then "
        f"run the story again. Nothing was created and no agent was invoked.",
    )


def _refuse_base(base: str, problems: list[str]) -> int:
    """Refuse a run or a plan that would branch or commit from the wrong base.

    Thin, like every other caller of `refuse`, and shared by both entry points
    on purpose: the same condition must read the same way whether l5-run met it
    at pre-flight or l5-plan met it before committing an artifact.
    """
    return refuse(
        f"The base for this story is {base}, and the repository is not standing "
        f"where a branch cut from it would be the branch you meant:",
        problems,
        f"Check out {base} and bring it level with its remote, then try again. "
        f"To branch deliberately from something else, pass --base <branch>; it "
        f"states a base, it does not skip the check.",
    )


def _refuse_finished_branch(branch: str, run_dir: Path, commits: list[str]) -> int:
    """Refuse a run whose branch already carries the story's finished work.

    The refusal names what it found and what to do about each of the two things
    a re-run would otherwise silently sit on top of: the branch, whose reset or
    deletion discards work a run finished, and the run directory, which is
    gitignored and therefore lost rather than merely uncommitted.
    """
    return refuse(
        f"Branch {branch} already carries a completion commit for this story, "
        f"so a run starting here would re-run a story that has already "
        f"finished:",
        commits,
        f"A completion commit reachable from the branch you plan to run from "
        f"means the story has already shipped; the right response is a new "
        f"story describing what is still wanted, not a reset. To genuinely run "
        f"this one again, reset or delete {branch} — which discards the "
        f"finished work — and delete {run_dir}, which is gitignored and will "
        f"not come back. Archive either to .harness/runs-archive/ first if it "
        f"is worth keeping.",
    )


def _commits_the_tree_it_ends_on(escalate):
    """Commit the escalated work after the escalation has finished writing.

    The work commit has to be the last thing that happens: every file the
    escalation writes — state.json, both renderings of the event stream, the
    escalation summary — belongs inside it, or a repository that tracks its run
    directory is left dirty by the very writes that record the escalation, and
    the checkout this story exists to make safe is refused. Wrapping is how the
    ordering is expressed without moving the escalation's own writing around,
    whose last act is the summary a separate request owns.
    """

    @functools.wraps(escalate)
    def escalate_and_commit(
        run_dir: Path,
        state: RunState,
        reason: str,
        *,
        target_root: Path,
        harness_root: Path,
        **event_fields,
    ) -> int:
        code = escalate(
            run_dir,
            state,
            reason,
            target_root=target_root,
            harness_root=harness_root,
            **event_fields,
        )
        if state.escalation_commit:
            commit_escalated_tree(target_root, state, reason)
        return code

    return escalate_and_commit


@_commits_the_tree_it_ends_on
def _escalate(
    run_dir: Path,
    state: RunState,
    reason: str,
    *,
    target_root: Path,
    harness_root: Path,
    **event_fields,
) -> int:
    """End the run, recording the escalation in both renderings.

    A run that failed must be as reconstructable as one that passed, so the
    history ends with this entry. Callers forward whatever structured fields
    the escalation has — the stage's elapsed time, the verifier's outcome,
    the decision that routed here — through event_fields.

    The run's record of the escalation is committed here, and the sha of that
    commit is written into state.json for the commit that follows to carry —
    the wrapper above commits the tree once this has written everything, so
    that the escalation's own evidence is inside the commit and the tree it
    leaves is clean. The harness revision is recorded here too, for the same
    reader: a resume can then tell whether the harness itself has changed
    since.
    """
    state.harness_revision = _revision(harness_root)
    state.status = "escalated"
    save_state(run_dir, state)
    # What the startup read counted past the retention bound, said before the
    # escalation's own entry so the history still ends with that entry, and
    # before the commit below so the note is inside it, as every record this
    # path writes is; nothing when nothing was counted.
    _report_history_past_bound(run_dir)
    append_event(
        run_dir,
        f"escalated: {reason}",
        kind="escalated",
        stage=state.current_stage or None,
        **event_fields,
    )
    state.escalation_commit = commit_escalated_work(
        target_root, state, reason, run_dir=run_dir
    )
    if state.escalation_commit:
        save_state(run_dir, state)
    summary = escalation_summary(run_dir, state, reason)
    (run_dir / "escalation-summary.md").write_text(summary, encoding="utf-8")
    return 2


#: What a paused process returns. Distinct from completion (0) and from
#: escalation (2), so a caller can tell a run that must be looked at from one
#: that may simply be run again once capacity is back.
PAUSE_EXIT_CODE = 3


def pause_wait_seconds(
    reset_at: float | None, now: float, bound: float
) -> float | None:
    """How long to wait in place before re-entering, or None to exit.

    Three cases, and the third is the one that keeps the mechanism honest. A
    known reset time within the configured bound is the wait. A known reset
    time beyond it is None, because a run that would sit longer than the
    developer authorized should exit and be resumed deliberately. And an
    *unknown* reset time is None whatever the bound says: the bound is a
    ceiling on a wait whose length the harness has been told, never a duration
    it sleeps in the absence of one, and a harness that guessed would hang a
    run on a signal it did not understand.

    A bound of zero — which is what a target configuring none has — never
    waits, so the reset time is not even consulted. A reset time already past
    is a wait of nothing rather than a negative one.
    """
    if reset_at is None or not bound:
        return None
    wait = max(0.0, reset_at - now)
    return wait if wait <= bound else None


def _capacity_paused(
    run_dir: Path,
    stage_name: str,
    capacity: agent_runner.CapacityStop,
    reason: str,
) -> None:
    """Say that the run paused on capacity, in its own event kind.

    What was detected and when capacity is expected back go here rather than
    on `state.json`, because they are a record of why and when rather than
    something routed on: the pause is expressed by the `status` value alone,
    and `append_event` stays the single write path for events.
    """
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(capacity.reset_at))
        if capacity.reset_at is not None
        else "an unstated time"
    )
    append_event(
        run_dir,
        f"paused on capacity: {reason}; capacity expected back at {when}",
        kind="capacity-paused",
        stage=stage_name,
    )


def _pause(
    run_dir: Path,
    state: RunState,
    capacity: agent_runner.CapacityStop,
    reason: str,
    *,
    target_root: Path,
) -> None:
    """Pause the run where it stands, durably, before anything waits.

    The state, the event and the commit all land here, in that order, so the
    run's own record of the pause is inside the commit — the ordering
    `_commits_the_tree_it_ends_on` already enforces for an escalation. That
    ordering is also the whole reason a sleep is safe afterwards: the
    durability is unconditional and the wait is opportunistic, so a process
    killed mid-sleep leaves a run a fresh process resumes with nothing lost.

    No counter is touched, no escalation summary is written and no escalation
    event is appended. The stage did not fail — it did not run — so there is
    nothing to spend and nothing to report a verdict on.

    The two commits are the escalation's own functions carrying a different
    message, because what a pause needs of them is exactly what an escalation
    needs: work left uncommitted for days is what they exist to protect, and
    the clean-tree pre-flight would otherwise refuse the very resume this
    pause exists to allow. What is not shared is the subject; see
    `pause_commit_subject`. The commit's sha is not recorded, because
    `state.json` gains no field for a pause: the status is the whole of it.
    """
    state.status = "paused"
    save_state(run_dir, state)
    _capacity_paused(run_dir, state.current_stage, capacity, reason)
    message = pause_commit_message(state, reason)
    committed = commit_escalated_work(
        target_root, state, reason, run_dir=run_dir, message=message
    )
    if committed:
        commit_escalated_tree(target_root, state, reason, message=message)


def _resumed(run_dir: Path, state: RunState, *, note: str = "") -> None:
    """Say that the run resumed, in the one place that kind is spelled.

    Two paths reach a resume — the escalated-or-crashed one, which re-enters
    the run, and the capacity one, which simply continues it — and one kind of
    event says so. `note` is what distinguishes them, in the shape
    `_suite_rerun_started`'s `phrase` already established: without it this
    writes byte for byte the line the resume wrote before a second path
    existed.
    """
    append_event(
        run_dir,
        f"resumed at stage {state.current_stage} under workflow "
        f"{state.workflow}{note}",
        kind="resumed",
        stage=state.current_stage,
    )


def resume_from_capacity(run_dir: Path, state: RunState) -> None:
    """The state transitions a capacity resume makes, and nothing else.

    Written once because two paths take it: the in-process wait, which sleeps
    and then re-enters the same stage, and the fresh-process resume, which
    reaches the same point having been started by a developer. Waiting in place
    is then the ordinary resume plus a sleep rather than a second recovery
    path, and the two cannot drift.

    A capacity resume archives nothing, moves nothing, opens no entry and
    resets nothing. That is where it differs from an escalated or crashed
    resume, and the difference follows from what each is: those follow a
    developer deciding, after investigating, to fund another attempt, while
    this is automatic, nothing was wrong, and the stage did not fail. The
    consequence worth naming is that a paused run cannot spend its way past
    the run ceiling by pausing — the allowance survives the pause exactly as
    the retry count does.
    """
    state.status = "running"
    save_state(run_dir, state)
    _resumed(run_dir, state, note=" after a capacity pause")


def announce_item_status(
    story: dict | None, config: dict, target_root: Path, status: str
) -> item_update.Published | None:
    """Move the item this story was planned from to `status`, if there is one.

    Returns `None` where nothing was invoked, and a result where something
    was — so a caller reports what happened without asking a second question
    about whether anything did. Two things make it `None`, and both are
    ordinary rather than failures: a story artifact carrying no brief key,
    which is every story planned from typed request text, and a target
    configuring no item-update command, which is every target that tracks its
    work somewhere the harness has not been told about. Neither is announced,
    because a run that invoked nothing has nothing to say.

    Nothing here raises, nothing here refuses, and nothing here prints. The
    caller decides where a result is reported, which differs between the two
    call sites: a run's start reports into its own events.log, and a run's
    completion reports on stderr and appends nothing at all, because a line
    written after the completion commit leaves the tree dirty for the next
    run's clean-tree pre-flight to refuse on.

    `status` is a token composed into the question and read by nothing here:
    no branch anywhere in the harness decides anything from which of the three
    was sent, and no status is ever read back out of the command.
    """
    key = brief_key.key_of(story)
    if not key or not config.get(item_update.COMMAND_KEY):
        return None
    return item_update.publish(
        key,
        state_story_id(story),
        # No document: there is nothing new to publish at either of these
        # moments, and the projection the planning session published is
        # already on the item.
        None,
        config,
        target_root,
        status=status,
    )


def state_story_id(story: dict | None) -> str:
    """The story id a parsed story names, or the empty string for none.

    Taken off the reading the caller already has rather than from a second
    parse, and answering emptily rather than raising, because what it feeds is
    a report about a tracker and never a routing decision.
    """
    value = (story or {}).get("story", {}).get("id", "")
    return value if isinstance(value, str) else ""


def _complete(run_dir: Path, state: RunState, story: dict, target_root: Path,
              *, config: dict | None = None,
              harness_root: Path | None = None,
              stages: list[dict] | None = None) -> int:
    state.status = "completed"
    state.current_stage = ""
    save_state(run_dir, state)
    # The schema marks title required and the run cannot reach here without
    # having passed validation, so a missing title is a loud failure rather
    # than a silently empty report.
    title = story["story"]["title"]
    # The same sentence the end-of-run note carries, under Next Step, when the
    # startup read counted records past the retention bound; the report is
    # byte-for-byte what it was otherwise.
    past_bound = _history_past_bound_report()
    next_step = (
        "Review the branch and merge it when you accept the story."
        + (f" Also: {past_bound}." if past_bound else "")
    )
    report = (
        f"# {state.story_id} Completion Report\n\n"
        f"## Story\n{title}\n\n"
        f"## Outcome\nCompleted on branch {state.branch} after "
        f"{state.retry_count} retr{'y' if state.retry_count == 1 else 'ies'}.\n\n"
        # From the record rather than the live allowance, which is
        # entry-scoped: what the run cost is the whole run's question.
        f"## Cost\nThis run cost {format_usd(recorded_cost(run_dir))}, summed "
        f"from what each stage invocation reported. cost.json records them "
        f"one by one.\n\n"
        f"## Evidence\n"
        f"- test-results.json\n"
        f"- verification/iteration-{state.verification_iterations}.json (passed)\n"
        f"- documentation-report.md\n\n"
        f"## Next Step\n{next_step}\n"
    )
    (run_dir / "completion-report.md").write_text(report, encoding="utf-8")
    _git(target_root, "add", "-A")
    # The staging question is asked about the *work* and answered before
    # anything is appended, then held. Appending first and looking at the index
    # afterwards would make the tree dirty at the three-way choice every time,
    # and the amend case below — a clean tree standing on this story's own
    # escalation commit — would never occur again: the staged history line
    # alone would route every completion to the ordinary commit. Asking first
    # and re-staging afterwards keeps the choice about what the stages left and
    # still sweeps the run's own completion record into the commit that
    # completion leaves.
    stages_left_work = _git(target_root, "diff", "--cached", "--quiet").returncode != 0
    # The record is appended *before* the commit, which is the rule every
    # terminal path follows: `_escalate`, the budget stop that goes through it,
    # and `_pause` all write their record and then commit it. The cost is that a
    # record states an outcome a moment before that outcome is demonstrably
    # committed; the alternative is the untracked record this ordering exists to
    # remove, so it is accepted. append_event is the only writer of the history
    # logs and nothing later in the run stages anything, so a completion
    # appended after the commit is a completion whose own record is untracked.
    #
    # The end-of-run note about records past the retention bound goes first,
    # for the same reason and so the completion entry stays the last thing the
    # history says; it appends nothing when the startup read counted nothing.
    _report_history_past_bound(run_dir)
    append_event(
        run_dir,
        f"story completed on branch {state.branch}",
        kind="story-completed",
    )
    _git(target_root, "add", "-A")
    # Three outcomes, decided by what staging left and by what the branch tip
    # is. `--allow-empty` in two of them, for the reason the escalation commits
    # carry it: the commit is how a finished run is recognised —
    # completion_commits reads it, and the pre-flight that refuses a re-run
    # onto a finished branch reads that — so a run whose last stage changed no
    # repository file must still leave one. Before story-045 the documenter ran
    # last and all but guaranteed a dirty tree here; with the verifier last, a
    # run that entered at it writes only run-directory artifacts, which a
    # repository ignoring its run directory has nothing to commit from.
    #
    # The middle case is the one story-065 adds. An empty commit exists on the
    # branch and says the story finished, but a rebase merge discards it and a
    # squash folds it, so a run that escalated and then resumed reached the
    # trunk with nothing saying it had ended. When the tree is clean *and* the
    # branch tip is an escalation commit of the story being run, the completion
    # message amends that commit instead: the trunk then sees one commit that
    # both carries the work the escalation committed and says the story
    # finished, which every merge method preserves. The amend is confined to
    # exactly that shape — the escalation is recognised through
    # `escalated_story`, by what the commit says about itself rather than by
    # its position or by counting — because rewriting anyone else's commit,
    # including another story's escalation, is not this run's to do.
    if stages_left_work:
        _git(target_root, "commit", "--allow-empty", "-m",
             completion_commit_message(state, title))
    elif _head_escalated(target_root) == state.story_id:
        _git(target_root, "commit", "--amend", "--allow-empty", "-m",
             completion_commit_message(state, title, amended=True))
    else:
        _git(target_root, "commit", "--allow-empty", "-m",
             completion_commit_message(state, title))
    # The post-story inspection, between the completion commit above and the
    # sweep below, and both halves of that position are load-bearing. It is
    # below the commit so that a slow inspection cannot delay the durability of
    # the work — an inspection is an agent invocation, and nothing about
    # whether the story's work survives may wait on one. It is above the sweep
    # so that a brief it enqueues is filed by that same sweep rather than
    # waiting for the next run to drain the queue. story-092's criterion that
    # `_complete` sweeps after the completion commit stays exactly true: this
    # inserts between the two and moves neither.
    #
    # It must not refuse, for the reason the sweep beside it must not. It
    # returns nothing, so there is no value to branch on here, and it raises on
    # no path, so nothing below it is conditional on it having worked.
    #
    # Since story-147 it happens here **only where no stage of the loaded
    # workflow declares an inspection of its own**. A workflow that declares one
    # has already inspected this story's change before that stage was entered,
    # and inspecting again from here would read the same diff a second time and
    # file against the next story what the last one has already answered. A
    # workflow declaring none is unchanged: same position, same scope, same
    # filing, same record.
    if not inspection_declaration(stages or []):
        story_inspection.inspect_after_story(
            run_dir, target_root, config or {}, harness_root, state.story_id,
            stages or [],
        )
    else:
        # A workflow declaring an inspection inspected each attempt's change
        # before its declaring stage and filed none of it: what those
        # inspections held, and what the final verdict left unanswered, is
        # filed here, once, from the final attempt — in the same position and
        # for the same two reasons, below the commit so the durability of the
        # work never waits on a queue and above the sweep so the same sweep
        # drains what this filed. It must not refuse, for the reason its
        # neighbour must not: it returns nothing and raises on no path, so
        # nothing below it is conditional on it having worked.
        story_inspection.file_at_completion(
            run_dir, target_root, config or {}, harness_root, state.story_id,
            stages or [],
        )
    # The second opportunistic sweep, *after* the completion commit and not
    # before it. A sweep talks to a provider that may be slow or unreachable,
    # and nothing about the durability of the work may wait on that: the work
    # is committed above, and only then is the queue offered to the network.
    #
    # This one must not refuse either, for the reason the pre-flight sweep must
    # not: making it consistent with the checks that refuse would let a queue
    # nobody can drain turn a finished story into a failed one. Its result is
    # read by nothing — the return below is 0 whatever the sweep found — and
    # `_escalate` and `_pause` sweep nothing at all, so a crashed, escalated or
    # paused run leaves its entries in the repository's one queue. That queue
    # is the repository's rather than this working tree's, which is what makes
    # leaving them safe: both readers named here reach the same directory
    # whichever tree they stand in — the next run's pre-flight sweep, whatever
    # story it is running and whatever worktree it works in, and an explicit
    # l5-sync, wherever in the repository it is invoked from.
    #
    # It is the one sweep that reports into no run directory, and that is the
    # same rule seen from the other side rather than an inconsistency. Every
    # line this run appends is inside the commit above; a line appended after
    # it leaves events.log and execution-history.json modified, and in a target
    # that tracks its run directory the *next* run's clean-tree pre-flight then
    # refuses — the sweep having become the thing that blocks a run, which is
    # the one outcome this whole design exists to prevent. So the sweep runs
    # and reports nowhere. What it did is observable where it can be observed
    # without writing: at the transport, which is called after the commit.
    outbox_sweep.sweep(target_root, config or {}, harness_root)
    # The third of the three moments, beside that sweep and reported the way it
    # is reported: on stderr, appending nothing to events.log, to
    # execution-history.json or to any other file. A line appended after the
    # completion commit leaves the tree dirty for the next run's clean-tree
    # pre-flight to refuse on, which is the rule the sweep above already obeys
    # and the one thing this report may not break — a run that finished is
    # finished whether or not its tracker heard, so a status update must not be
    # able to fail the thing it reported on.
    #
    # Nothing branches on it: the return below is 0 whatever it found, and a
    # story carrying no brief key or a target configuring no command invokes
    # nothing and says nothing.
    moved = announce_item_status(
        story, config or {}, target_root, item_update.READY_TO_MERGE
    )
    if moved is not None:
        print(
            f"item status {item_update.READY_TO_MERGE} sent for "
            f"{state.story_id}"
            if moved.published
            else f"item status {item_update.READY_TO_MERGE} was not sent for "
            f"{state.story_id}: {moved.reason}",
            file=sys.stderr,
        )
    _report_moved_item_command(run_dir, config or {}, target_root)
    return 0


def _report_moved_item_command(
    run_dir: Path, config: dict, target_root: Path
) -> None:
    """Say in the run's events.log where the item command moved under the run.

    A run holds the configuration it loaded before the run directory existed,
    so a story whose own work moves the item-update command sends its
    ready-to-merge status through the path it has just removed. The line sits
    beside the one reporting whether that status was sent, so a reader meeting
    "was not sent" learns whether the command is broken or merely the one the
    tree has moved on from.

    Nothing routes on it: the status that was sent is the status that was sent,
    the run's exit code is unchanged, and a completion may not fail on a report
    about itself — so every path here is guarded and every path returns.
    """
    try:
        moved = harness_config.moved_command(
            config, target_root, item_update.COMMAND_KEY
        )
        if moved is None:
            return
        append_event(run_dir, moved.describe())
    except Exception:  # noqa: BLE001 - noticing may not become a failure
        pass


def run_story(
    story_id: str,
    harness_root: Path,
    target_root: Path,
    runner=agent_runner.run_agent,
    start_stage: str | None = None,
    *,
    base: str | None = None,
    sleep=time.sleep,
) -> int:
    """Execute one story, from a fresh run or from where a run left off.

    `start_stage` overrides where execution enters — the recorded stage on a
    resume, the workflow's first stage on a fresh run. It is named
    `start_stage` rather than `stage` because `stage` is the loop's name for
    the stage being executed, and one name for two things is how this
    repository has repeatedly confused itself.

    `base` declares what a *new* story branch is cut from. None is the normal
    case and means the repository's own default branch; see `resolve_base`.

    `sleep` is how the run waits in place for capacity to come back, injected
    for the reason the agent runner is: a test observes the decision to wait
    without taking it. Every duration handed to it comes from configuration.
    """
    config = harness_config.load_config(target_root)

    # Pre-flight: a key the schema does not declare is refused rather than
    # ignored. Ignoring one lets the run fall back to a default and quietly
    # exercise something other than what the config asked for — a retired name
    # left after a rename, or a mistyping of a declared key. Above every other
    # pre-flight, because it is decidable the moment the config loads: a
    # refusal here leaves no run directory, no state.json, no log, no new
    # branch, and invokes no agent.
    undeclared = harness_config.undeclared_config_problems(config, harness_root)
    if undeclared:
        return _refuse_undeclared_config_keys(target_root, undeclared)

    # And the same pre-flight for the one configured value the harness has to
    # compare a duration against. A pause wait that is not a non-negative
    # number cannot bound anything, and every run under that configuration
    # carries the defect, so it is refused here rather than at the first
    # capacity failure — above the run directory, so a refusal leaves nothing
    # behind and invokes no agent.
    wait_problems = pause_wait_problems(config)
    if wait_problems:
        return _refuse_bad_pause_wait(target_root, wait_problems)

    # And for the other duration: the bound on waiting for the repository's
    # turn at the suite, refused here on the same terms so a run never reaches
    # its first suite run holding a bound it cannot compare against.
    turn_problems = suite_turn_wait_problems(config)
    if turn_problems:
        return _refuse_bad_suite_turn_wait(target_root, turn_problems)

    # The story artifact and any existing run state are read before the
    # workflow, because both can name the workflow this run loads. Reading
    # them here creates nothing — the run directory is derived from
    # configuration alone, and load_state reads a file rather than requiring a
    # directory — so every refusal below still leaves no run directory, no
    # state.json, no log, no branch and no agent invoked.
    stories_dir = config.get("stories_dir", ".harness/stories")
    relative_story = f"{stories_dir}/{story_id}.yaml"
    story_path = target_root / relative_story
    invoked_branch = story_branch(config, story_id)
    if story_path.is_file():
        story_text = story_path.read_text(encoding="utf-8")
    else:
        # The artifact is not in the invoked tree, which is the ordinary state
        # of a clone that did not plan the story: planning happens in a worktree
        # of its own, pushes the artifact on the story branch, and removes that
        # worktree when its run offer is declined. So a story id whose artifact
        # is not here is resolved through its branch — fetched from the remote
        # when this clone has never seen it — rather than refused, and the run
        # root below is cut from that branch.
        fetched = fetch_story_branch(target_root, config, invoked_branch)
        from_branch = (
            story_on_branch(target_root, invoked_branch, relative_story)
            if fetched.resolved
            else None
        )
        if from_branch is None:
            # Two refusals, one status, and which one is printed turns on
            # whether the fetch established anything. With nothing to report the
            # remote answered and did not have the branch, so the sentence this
            # has always printed is true. With a reason, nobody reached the
            # remote: saying the branch is absent from it would be a statement
            # the fetch did not establish, and telling the developer to plan a
            # story that may already be planned is how that statement misleads.
            if fetched.problem:
                print(
                    f"No story artifact at {story_path}, and this clone could "
                    f"not reach the remote to find out whether branch "
                    f"{invoked_branch} carries one: {fetched.problem}.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"No story artifact at {story_path}, and none on branch "
                    f"{invoked_branch} here or on the remote. Run l5-plan first.",
                    file=sys.stderr,
                )
            return 1
        story_text = from_branch

    # Pre-flight: refuse a bad story before any run state exists, so a
    # rejection leaves no run directory, no state.json, and no new branch.
    # The schema ships with the harness code, so it is resolved by
    # schema_validator relative to its own module, not from harness_root.
    reading = read_story(story_text)
    if reading.problems:
        return refuse_bad_story(story_path, reading.problems)

    # Pre-flight: what conferred the right to run this work has to resolve to
    # a human before anything is spent on it. Directly below the reading that
    # produced the block, because the walk needs a parse and has nothing to say
    # about an artifact that has none, and above everything a run creates: a
    # refusal here leaves no run directory, no state.json, no log, no new
    # branch, and invokes no agent.
    #
    # It is re-run on every entry, against the artifact as it is now, so a
    # resume is judged by the artifact it is resuming under rather than by what
    # the first entry read. The lookup this deployment passes answers for
    # nothing, which is a fact about the deployment and not about the walk.
    resolution = resolve_mandate(
        (reading.parsed or {}).get("mandate"),
        resolves_nothing,
        mandate_max_depth(config),
    )
    if not resolution.resolved:
        return _refuse_unresolved_mandate(resolution.problems)

    # Where this run works, decided before anything reads or writes a tree.
    # Resolving it is a decision and creating a worktree is an act, so the
    # decision is made here — above the run directory, the state read and the
    # dirty-tree check, every one of which has to be about the tree the run
    # will actually work in — and the act waits until the point the branch
    # checkout used to sit at, with every refusal still above it.
    #
    # `invoked_root` is the developer's own checkout and stays the repository
    # every git question about branches and bases is asked of. `target_root`
    # becomes the run root below the creation, so the rest of this function —
    # the run directory, the history, the stage baselines, the checks and both
    # terminal commits — is against the tree the run works in.
    invoked_root = target_root
    run_root = resolve_run_root(invoked_root, config, invoked_branch)
    run_dir = run_root.path / config.get("runs_dir", ".harness/runs") / story_id
    state = load_state(run_dir)
    # Whether this entry is a fresh run rather than a resume, read off the same
    # loaded state the resume decision below is made from — captured here
    # because that state is replaced by the fresh run's own RunState further
    # down, and one reading of it is what keeps the two decisions agreeing.
    fresh_run = state is None

    # Which workflow this run executes, in this order: what a resumed run
    # recorded loading, then what the story artifact names, then what the
    # target configuration names. The recorded run comes first because a
    # resume must load the definition it was executing — amending the artifact
    # between two entries of one run must not change what the second entry
    # runs under. The artifact comes next because the choice belongs to the
    # work item. The configured default is last and is spelled exactly as it
    # always was, so an artifact naming no workflow runs precisely what it ran
    # before this field existed.
    declared = (reading.parsed or {}).get("story", {}).get("workflow")
    workflow_name = (
        (state.workflow if state else "")
        or declared
        or config.get("workflow", "story-workflow")
    )

    # The definition may reference the target's configuration, so it is loaded
    # against the config that has just been read. A reference the config
    # cannot answer, and a name no definition answers, are both defects every
    # run under that choice carries, so both are refused here, beside the
    # other pre-flight refusals and above everything a run creates: no run
    # directory, no state.json, no log, no branch, and no agent invoked.
    try:
        workflow = harness_config.load_workflow(harness_root, workflow_name, config)
    except harness_config.UnknownWorkflow as unknown:
        return refuse_unknown_workflow(unknown)
    except harness_config.UnresolvedWorkflowToken as unresolved_token:
        return _refuse_unresolved_workflow_token(unresolved_token)
    rules = harness_config.load_rules(harness_root)
    stages = workflow["stages"]
    stage_names = [s["name"] for s in stages]

    # A stage the developer named overrides where execution enters, which is
    # what makes a resume useful: an escalation caused by an amended story is
    # re-entered at the implementer rather than at the verifier that recorded
    # it. Refused above everything else, in the shape the other pre-flight
    # refusals take — exit 1, one message, nothing created and no agent run.
    if start_stage is not None and start_stage not in stage_names:
        print(
            f"'{start_stage}' is not a stage the loaded workflow defines. "
            f"{workflow['name']} defines: {', '.join(stage_names)}.",
            file=sys.stderr,
        )
        return 1

    # Pre-flight: the routing table is checked when the workflow loads, not
    # when a retry happens. A route that cannot be followed is a defect in the
    # definition, and every run under that definition has it; discovering it at
    # the first retry spends three stages first. Above the run-directory
    # creation and the branch checkout with the other refusals, so a rejection
    # leaves no run directory, no state.json, no log, no new branch, and
    # invokes no agent.
    routing_problems = retry_routing_problems(stages)
    if routing_problems:
        return _refuse_bad_routing(workflow, routing_problems)

    # The same pre-flight for the other budget the definition declares. A
    # max_self_routes that is not a count cannot be spent, and every run under
    # that definition carries the defect.
    budget_problems = self_route_problems(stages)
    if budget_problems:
        return _refuse_bad_self_routes(workflow, budget_problems)

    # And the same pre-flight for where a correction pass enters. The entry
    # stage is a declared property of the workflow rather than something a
    # finding's category selects, so it is a declaration the definition can be
    # wrong about, and a run that discovers it only when a passing verdict
    # carries a correctable finding has already spent the whole workflow.
    entry_problems = correction_pass_problems(stages)
    if entry_problems:
        return _refuse_bad_correction_pass(workflow, entry_problems)

    # And the same pre-flight for the repair pass. Its budget has to be a
    # count that can be spent, and the stage declaring it has to declare the
    # retry_routing table a repairable finding's category is resolved through,
    # or a passing verdict carrying one would have nowhere to send it.
    repair_problems = repair_pass_problems(stages)
    if repair_problems:
        return _refuse_bad_repair_pass(workflow, repair_problems)

    # And the same pre-flight for the two cost ceilings. A ceiling that is not
    # a number can be neither compared against a spend nor handed to an
    # invocation, so a definition that cannot work is refused before a stage is
    # spent on it. A definition declaring no ceiling anywhere is not checked and
    # runs exactly as it did before ceilings existed.
    ceiling_problems = cost_ceiling_problems(workflow)
    if ceiling_problems:
        return _refuse_bad_cost_ceilings(workflow, ceiling_problems)

    # And the same pre-flight for what the definition says it is for. A
    # definition that cannot say when it applies cannot be chosen between at
    # planning time, and discovering that at the first stage has already spent
    # one.
    purpose_problems = applies_when_problems(workflow)
    if purpose_problems:
        return _refuse_bad_applies_when(workflow, purpose_problems)

    # Conformance is one question, agreement with this workflow another. A
    # stage exception is checked against the workflow that was selected, so it
    # sits below the load rather than beside read_story; both refusals stay
    # above run-directory creation.
    exception_problems = stage_exception_problems(reading.parsed, stages)
    if exception_problems:
        return refuse_bad_story(story_path, exception_problems)

    # Pre-flight: refuse a run onto a branch that already holds this story's
    # finished work. `_checkout_story_branch` reuses an existing branch rather
    # than resetting it, so deleting only the run directory puts the implementer
    # in a repository where the story is already done, the tester finds the
    # tests written and passing, the verifier verifies a tree that genuinely
    # satisfies the story, and the run reports success having changed nothing.
    #
    # The evidence is deliberately a *finished run* — `_complete`'s own commit,
    # recognized by its subject and marker — and not the existence of commits on
    # the branch. Those are different statements, and only the first is what
    # makes a re-run pointless: an escalation already leaves two commits, so an
    # ahead-of-base test would refuse every escalated resume, and a later design
    # that commits partial progress would have to unpick a check written that
    # way. It is base-free for the same reason, so it still holds when the
    # developer is standing on the branch a completed run left them on.
    #
    # A loaded state recording `completed` keeps its own refusal below: that one
    # has the run directory to point at and says more.
    if not (state and state.status == "completed"):
        branch = state.branch if state else story_branch(config, story_id)
        finished = completion_commits(target_root, branch, story_id)
        if finished:
            return _refuse_finished_branch(branch, run_dir, finished)

    # Pre-flight: a story branch is cut from something, and until this check
    # that something was whatever happened to be checked out. The base is
    # resolved once here and read again below for the stale-base note.
    #
    # The check is creation-time. It answers "what will this branch be cut
    # from", which is a question only a branch that does not exist yet has, so
    # an existing story branch is never refused for its base however far the
    # base has moved — resume must keep working, and a resume that met this
    # would be refused for a decision a previous run already made. It sits
    # above the clean-tree check and above the run-directory creation, so a
    # refusal leaves no run directory, no state.json, no log, no new branch,
    # and invokes no agent.
    story_branch_name = state.branch if state else story_branch(config, story_id)
    branch_existed = (
        _git(target_root, "rev-parse", "--verify", story_branch_name).returncode == 0
    )
    resolved_base = resolve_base(target_root, config, base)
    # Why a refresh could not be made, held for the note below once there is a
    # run directory to record it in. Empty is the ordinary case: the ref was
    # refreshed, or there was nothing to refresh, and neither says anything.
    refresh_reason = ""
    if not branch_existed:
        # The base's remote-tracking ref is brought up to date immediately above
        # the check that reads it, inside this guard so a resume — and a story
        # branch that already exists — does no network work. A refresh that
        # could not be made is printed and carried, never refused: the check
        # then reaches exactly the decision it would have reached before.
        refresh_reason = refresh_base(target_root, resolved_base)
        if refresh_reason:
            print(refresh_reason)
        # Asked without its HEAD-standing-on-the-base leg: since story-117 a run
        # cuts its branch in a worktree, from the base *by name*, so where the
        # developer happens to be standing decides nothing about what the branch
        # is cut from and refusing them for it would refuse a run over a tree it
        # never touches. The ref-resolves question and the base-agrees-with-its-
        # remote leg are asked here exactly as they were.
        problems = base_problems(
            target_root, resolved_base, base is not None, from_head=False
        )
        if problems:
            return _refuse_base(resolved_base, problems)

    # Pre-flight: a run commits the tree it ends on, so it has to start from a
    # tree it can account for. Which runs this applies to is decided by the
    # state the run is *starting from*, which is already loaded above for the
    # resume decision. No state is a fresh run; an escalated state is a resume
    # of a run that left the tree clean when it escalated, so anything
    # uncommitted now is the developer's own fix and committing it deliberately
    # is what this check is for. A `running` state is the one exclusion, and it
    # is not an exception to the rule so much as a different tree: nothing
    # commits when a process dies, so that working tree holds the run's own
    # unfinished work and refusing it would refuse the run its own state.
    #
    # It sits above the run-directory creation and the branch checkout, so a
    # refusal leaves no run directory, no state.json, no log, no new branch,
    # and invokes no agent. There is deliberately no flag, environment variable
    # or configuration key that skips it: a bypass on a correctness guard
    # becomes the default invocation.
    # A `paused` state joins `escalated` here on the same reasoning: a pause
    # commits what the run left, so the tree it leaves is clean and anything
    # uncommitted now is the developer's own. Leaving it out would have the
    # clean-tree check refuse the very resume the pause exists to allow.
    #
    # Read against the run root rather than against the invoked tree, because
    # the run root is the tree the run commits. A fresh run cutting a worktree
    # of its own is therefore not refused for a developer's uncommitted work —
    # that work is in a tree the run never touches and no file of it appears in
    # any commit the run makes — while a resume, whose run root is the worktree
    # its earlier entry left, is still refused for a dirty one.
    if state is None or state.status in ("escalated", "paused"):
        dirty = dirty_paths(run_root.path)
        if dirty:
            return _refuse_dirty_tree(run_root.path, dirty)

    if state and state.status == "completed":
        # Name the branch as well as the run directory. _checkout_story_branch
        # reuses an existing branch rather than resetting it, so deleting only
        # the run directory re-runs the story on top of the finished work — the
        # implementer opens a repository where the story is already done, and
        # the run reports success having changed nothing. This guard is about
        # finished work; an escalated run resumes below, because its work is
        # not finished and its evidence is what the resume exists to keep.
        print(
            f"{story_id} already ended with status '{state.status}'. "
            f"Inspect {run_dir} to review it.\n"
            f"To run the story again, delete {run_dir} *and* reset branch "
            f"{state.branch}, which still holds the finished work. Archive both "
            f"first if the run is worth keeping — {run_dir} is gitignored.",
            file=sys.stderr,
        )
        return 1
    # A resume is handled in two passes, and the checkout goes between them.
    # This is the first: the decisions, every one of which is a refusal, made
    # here so that the ordering the harness already keeps — every refusal above
    # every act — survives the checkout becoming the run's first act. It reads
    # `attempt_dir` and `entry_dir` for existence only, which needs no run
    # directory, and mutates nothing. The second pass, below the checkout,
    # performs what these decisions permitted.
    #
    # A paused run takes no decision here because it takes none of those acts:
    # it is continued rather than re-entered, so there is nothing for a
    # collision to collide with and no escalation for the guard to establish
    # sameness against.
    resume_attempt: int | None = None
    resume_entry: int | None = None
    if state and state.status != "paused":
        if (
            state.status == "escalated"
            and not state.stopped_on_cost
            and state.retry_count < rules["max_retries"]
        ):
            # Resuming is inferred from the recorded status and from nothing
            # else. What the guard adds is a refusal in the one case where a
            # resume is knowably pointless: the story, the tree and the harness
            # are all establishably what they were when the run escalated.
            #
            # A run stopped on a cost ceiling is exempted here, at the call
            # site, rather than inside the guard: the guard keeps saying only
            # what it establishes, and what it establishes is true of a budget
            # stop — nothing has changed, and nothing needs to have. Resuming
            # such a run is the decision to fund another allowance, which is
            # precisely the resume this ceiling exists to allow, so refusing it
            # on the guard's evidence would refuse the only useful response to
            # the stop.
            #
            # A run stopped at the retry ceiling is exempted here on the same
            # terms, and for the reason the self-route exemption inside
            # `unchanged_since_escalation` gives for its own budget: the resume
            # zeroes `retry_count`, so it restores the very resource whose
            # exhaustion stopped the run, and the run will take the retry it
            # could not take before rather than reach the same point the same
            # way. Refusing would tell the developer nothing has changed while
            # the harness is about to change it. The condition reads the
            # recorded counter against the ceiling the rules declare — the same
            # comparison the escalation sites make — and never the escalation's
            # reason text. Because only the counter is read, a run that spent
            # its retries and then stopped on the final attempt for any other
            # reason is exempted too; that is the imprecision the self-route
            # exemption accepts, in the guard's permit-over-refuse direction.
            # It is read as "at or past" rather than "equal to"
            # because a resume zeroes the counter and a state file written by an
            # older coordinator may hold a count the current ceiling no longer
            # admits; either way the allowance it stopped on is spent.
            evidence = unchanged_since_escalation(
                state, story_text, run_root.path, harness_root
            )
            if evidence:
                print(
                    _resume_refusal(story_path, run_dir, state, evidence),
                    file=sys.stderr,
                )
                return 1
        # The interrupted attempt is archived below under the attempt number it
        # was written with, and the entry directory this re-entry opens is
        # written there too. Both are refused rather than written over — the
        # archive and the entry are the evidence a resume exists to preserve —
        # and both are decided here, above every act, so a refused resume
        # archives nothing, moves nothing and resets nothing.
        resume_attempt = state.retry_count + 1
        destination = attempt_dir(run_dir, resume_attempt)
        if destination.exists():
            print(
                f"{destination} already holds an archived attempt, and "
                f"resuming {story_id} would write attempt {resume_attempt} over "
                f"it. Move or remove it if that attempt is not worth "
                f"keeping, then run the story again.",
                file=sys.stderr,
            )
            return 1
        resume_entry = state.resume_count + 1
        opened = entry_dir(run_dir, resume_entry)
        if opened.exists():
            print(
                f"{opened} already holds an archived entry, and resuming "
                f"{story_id} would open entry {resume_entry} over it. Move or "
                f"remove it if that entry is not worth keeping, then run the "
                f"story again.",
                file=sys.stderr,
            )
            return 1

    # The checkout, and it is the run's first act: nothing the run writes —
    # tracked or gitignored — happens above this line. It used to sit below the
    # run directory, the history prune and every re-entry mutation a resume
    # makes, so those writes landed on whatever branch the developer typed
    # l5-run from and a tracked cross-run history that differs between branches
    # made git refuse the checkout after the run had already archived an entry,
    # zeroed its counters and appended three history records.
    #
    # Every check that can be made without standing here stays above it, so a
    # checkout git will not make refuses the way its neighbours refuse rather
    # than raising. The branch name comes from the loaded state on a resume and
    # from the configured prefix on a fresh run, which is the same expression
    # the base check above already resolved.
    #
    # A branch that already exists is checked out in the worktree as it stands;
    # one that does not is cut from the resolved base, which the pre-flight
    # above has already checked. The developer's own checkout is not moved
    # either way: nothing here checks a branch out in the invoked tree unless
    # that tree was already standing on the story branch, in which case there is
    # nothing to create.
    if run_root.create:
        # A base that does not resolve establishes nothing — the harness
        # guessed, or a target names a branch it does not have — and the branch
        # is then cut where it was cut before this story: from the invoked
        # tree's HEAD. A declared base that does not resolve was refused above.
        start_point = (
            resolved_base
            if branch_exists(invoked_root, resolved_base)
            else "HEAD"
        )
        made = worktrees.add(
            invoked_root,
            run_root.path,
            story_branch_name,
            None if branch_existed else start_point,
        )
        if made.problems:
            return _refuse_checkout(story_branch_name, made.problems)

    # Every act below is against the tree the run works in.
    target_root = run_root.path

    if run_root.path != invoked_root:
        # The build state the configured commands name, linked from the tree the
        # harness was invoked from, for the reason the log directory below is
        # created: a fresh worktree holds tracked files alone, and an
        # interpreter the target configures inside its own tree is gitignored
        # and therefore not there. Without it the coordinator's own suite run
        # resolves a relative first word against a directory that has no such
        # file, and the clean-clone check's runner resolves to nothing.
        #
        # Guarded on the run root not being the invoked tree rather than on
        # run_root.create, so a resume into a worktree cut before story-119
        # gains the links it was missing; the linking skips a path the
        # destination already has, so a second run over the same worktree does
        # nothing. A failure to link is not a refusal — it leaves the existing
        # reports for a command that could not be run exactly as they are.
        link_configured_build_state(invoked_root, run_root.path, config)

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "verification").mkdir(exist_ok=True)
    # Derived rather than spelled here, so this site and the post-story
    # inspection that appends its detail to the same file cannot disagree about
    # which file that is.
    log_path = harness_config.run_log_path(target_root, config, story_id)
    # Created rather than assumed, for the reason the run directory above is.
    # Until story-117 the run worked in the tree it was invoked from, where the
    # log directory was whatever the developer's checkout already had; a run now
    # works in a worktree cut from the base, and a log directory that is ignored
    # rather than tracked — which is what this repository's own `.gitignore`
    # makes of `.harness/logs/` — is simply not in a fresh worktree. Without
    # this the first stage's log append raises FileNotFoundError before any
    # agent is invoked.
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # The cross-run history, established once for the whole run rather than
    # handed to each append_event call: a keyword argument with a default can
    # be omitted at one call site and silently drop a record, which is the one
    # failure mode an append-only store cannot have.
    #
    # The history is read here, at run-directory creation and above this run's
    # first append, and it is read only: every file of every declared log is
    # parsed, a line that cannot be parsed refuses the run with nothing
    # discarded and nothing rewritten around, and a configured bound counts
    # what is past it. The run rewrites nothing whatever the bound says. Until
    # story-168 the whole-store rewrite itself ran at this same point,
    # and two story branches that both ran under a bound then both rewrote
    # every file holding an old record, so merging them conflicted in exactly
    # the shape per-writer segments removed for the append. Pruning is
    # store-wide maintenance and belongs on the base branch outside any run,
    # which is `l5-prune`; what a run does with the count is report it at its
    # end, on every run until the prune happens.
    #
    # Below the checkout, so the files it reads are the story branch's own
    # rather than the base's.
    history_directory = harness_config.history_dir(target_root, config)
    set_history_dir(history_directory)
    history_read = read_history(history_directory, config)
    if history_read.problems:
        return _refuse_bad_history(history_directory, history_read.problems)
    set_history_past_bound(
        history_read.past_bound, config.get(HISTORY_RETENTION_KEY))
    # The per-operation estimates every announcement in this run reads, from
    # the one startup read above and never re-read: a report to a person that
    # no timeout, bound, check or routing decision consults.
    set_duration_estimates(
        duration_estimates(history_duration_records(history_read)))

    if state and state.status == "paused":
        # A resume of a run that paused because capacity ran out, which is the
        # third interruption beside a crash and an escalation and is unlike
        # both: nothing failed, nothing was judged, and the stage did not run.
        # So it takes none of the acts below — no archive, no entry directory,
        # no counter reset — and no unchanged-since-escalation guard, which
        # applies to an escalation and has nothing to establish about a pause.
        # It continues at the recorded stage with every counter carried
        # forward, through the one function the in-process wait also calls.
        if start_stage:
            state.current_stage = start_stage
        resume_from_capacity(run_dir, state)
    elif state:
        # A resume, of a crashed run or an escalated one. Chapter 18 treats
        # the two identically and so does this: restore nothing, because the
        # artifacts and the state are already here, and continue at the
        # recorded stage.
        #
        # What the resume does reinitialize is the run's attempt allowance. A
        # counter routes and a record accounts; a counter is reset by the
        # decision to continue and a record is never reset at all. Carrying
        # retry_count forward meant a run that escalated at the ceiling resumed
        # with its attempts already spent, so the first failing verdict
        # escalated it again — a second escalation rather than a resume. So
        # retry_count and verification_iterations go to zero here, and
        # self_route_count keeps the zeroing it already had: the three counters
        # agreeing is the point rather than a coincidence.
        #
        # What must not be reset is the record, and the reset would land the
        # next attempt straight back on names the earlier attempts occupy. So
        # the run is *re-entered* rather than restarted: re-entry K opens
        # resumes/resume-K/ and every counter-keyed name at the run root moves
        # into it, keeping its position. attempt-N therefore keeps meaning the
        # Nth attempt of one entry. What stays at the root is the half that
        # accounts for the whole run — retry-history.json,
        # execution-history.json, events.log, state.json, the escalation
        # summary — together with every stage artifact the resumed stage reads
        # as input, whose names no counter keys.
        #
        # This is the second pass. Every refusal a resume makes — the unchanged
        # guard, the attempt collision and the entry collision — was made above
        # the checkout; what is left here is what those decisions permitted.
        if start_stage:
            state.current_stage = start_stage
        # A resumed stage starts with its full self-route budget. The count is
        # the live count for one stage invocation and nothing carries it across
        # a resume: the stage is being entered afresh, not re-running itself.
        # The bookkeeping subset is zeroed with it, wherever it is zeroed, so
        # the two cannot disagree about the same stage entry.
        state.self_route_count = 0
        state.bookkeeping_self_route_count = 0
        # The interrupted attempt is archived before the resumed stage runs,
        # under the attempt number it was written with. This happens for both
        # resumed statuses: a crashed run stopped for a reason nobody recorded,
        # which is when a partial artifact is most likely to be the only
        # account of what happened, and a resume carries retry_count forward,
        # so the stage loop would re-render the prompt under the very number
        # the dead attempt used. Refuse rather than overwrite: the archive is
        # the evidence a resume exists to preserve, and story-010 recorded
        # exactly this case as open.
        #
        # Archiving makes no claim about what it copied. A stage that died
        # mid-write may leave a truncated or schema-invalid file, and
        # preserving that file exactly as it is is the point — nothing here
        # parses, validates or repairs an archived artifact.
        #
        # What does not generalize with it is the state transition below and
        # the unchanged guard above the checkout. A crashed run is already
        # running, so assigning that status would state a transition that did
        # not happen; and a crashed run has no escalation commit to compare
        # against, so unchanged_since_escalation could establish nothing and its
        # refusal would be meaningless.
        attempt = resume_attempt
        entry = resume_entry
        opened = entry_dir(run_dir, entry)
        archive_attempt(
            run_dir,
            interrupted_attempt_artifacts(stages, attempt, run_dir=run_dir),
            attempt,
        )
        # The move runs below the archive, so the attempt it archives is
        # numbered by the counters as they stood and travels into the entry
        # directory with the rest of attempts/, and above the reset, so the
        # counters still describe the entry being closed while it is closed.
        moved = move_entry_artifacts(run_dir, entry_artifacts(run_dir, stages), opened)
        state.resume_count = entry
        state.retry_count = 0
        state.verification_iterations = 0
        # The outstanding-failure record follows the move. Its retained pair is
        # among the counter-keyed names just moved, and every line composed
        # from the record afterwards — the carry-forward note, the clean-clone
        # shadowing note, the end-of-run refusal — names the paths it holds;
        # left as it was, a reader following any of them would find nothing
        # at the path named. Repointed by the moved names rather than by the
        # entry directory's spelling, above the save below and the resumed
        # event, so the state saved for the re-entry already carries the
        # current paths. A record whose pair did not move in this resume is
        # returned unchanged, so a second resume does not repoint it twice.
        # This is the one record a resume rewrites: retry-history.json and the
        # archive note below stay as written and are resolved by the log,
        # because they are evidence; this field is a live pointer the run
        # routes on, and the pair it points at is what the resume moved.
        state.unshadowed_suite_failure = repoint_outstanding_failure(
            state.unshadowed_suite_failure, moved, opened, run_dir
        )
        # The cost allowance is a live counter and resets where the other live
        # counters reset. The rule it conforms to is the one they already
        # follow: a counter is policy and is reset by the decision to continue,
        # a record accounts and is never reset at all. So the allowance goes to
        # zero and cost.json is left exactly where it is, continuing from where
        # it stood. The ceiling bounds what an *unattended* execution may spend;
        # a human typing l5-run is the authorization to spend another
        # allowance. Read before the reset, so the note below can say what the
        # entry that is ending spent.
        spent_before_resume = state.entry_cost_usd
        state.entry_cost_usd = 0.0
        stopped_on_cost = state.stopped_on_cost
        state.stopped_on_cost = False
        # Record what this entry loaded. For a run resumed from a state file
        # written before the field existed this is the first time the run says
        # so; for every other resume it is the name the resume resolved from,
        # written back unchanged.
        state.workflow = workflow_name
        if state.status == "escalated":
            state.status = "running"
            save_state(run_dir, state)
        _resumed(run_dir, state)
        # What moved and where, said once. A retry record written before the
        # move names its archive as a path relative to the run directory, and
        # that path is now one directory further down; the record is evidence
        # and evidence is never rewritten, so the log is what resolves it. Both
        # halves are run-relative for exactly that reason, and the names are
        # written out rather than counted so the resolution needs nothing else.
        append_event(
            run_dir,
            f"entry {entry} archived under {opened.relative_to(run_dir)}: "
            + ", ".join(moved),
            kind="note",
            stage=state.current_stage,
        )
        # What the resume decided about money, said where a reader of the log
        # meets the resume. The accumulated total comes from the record, which
        # accounts for every entry, and the entry's own spend from the
        # allowance that has just been zeroed — reporting both is what makes
        # the reset a decision rather than an accident. A run that stopped on a
        # ceiling says outright that resuming funds another allowance, which is
        # the whole of what the developer is authorizing.
        spend_note = (
            f"recorded spend so far: {format_usd(recorded_cost(run_dir))} "
            f"across the run, {format_usd(spent_before_resume)} in the entry "
            f"just ended; the allowance is back to zero and cost.json "
            f"continues from where it stood"
        )
        if stopped_on_cost:
            spend_note += (
                ", and resuming a run stopped on a cost ceiling is the "
                "decision to fund another allowance"
            )
        append_event(
            run_dir, spend_note, kind="note", stage=state.current_stage
        )
    else:
        state = RunState(
            story_id=story_id,
            branch=story_branch(config, story_id),
            current_stage=start_stage or stage_names[0],
            # Recorded from the same text read_story was given, so the digest
            # and the run's one reading describe one artifact.
            story_digest=story_digest(story_text),
            # Recorded from the name this run resolved and loaded, so a resume
            # loads the definition this entry executed rather than resolving
            # the choice again against an artifact that may since have been
            # amended.
            workflow=workflow_name,
        )
        save_state(run_dir, state)
        # The first line on the console and the first line of events.log both
        # say which definition is executing as well as which story, because two
        # definitions differing in their stage list make implementer-then-
        # documenter ambiguous between a correct refactor and a story that
        # skipped its tester. The name is already resolved, so this says what
        # the run already knows. The title comes from the reading this run
        # already made, for the reason the id does: a number is not what a
        # developer recognises, and an afternoon that planned several stories
        # is where it cannot be resolved from memory. Still one append_event
        # call under the kind the event already carried, so events.log and
        # execution-history.json stay two renderings of one write.
        title = story_title(reading.parsed)
        append_event(
            run_dir,
            f"workflow {workflow_name} started for {story_id}"
            + (f": {title}" if title else ""),
            kind="workflow-started",
        )

    # What the pre-flight walk resolved, recorded now that there is a run
    # directory to record it in. Evidence rather than state: nothing reads it
    # back, and the run was permitted above rather than here. It names the
    # source kind the walk terminated at and the identity it terminated on, so
    # a reader of the run's own record can say what this execution ran on
    # without opening the artifact it came from.
    #
    # Below the announcement rather than above it, because the first thing a
    # run says is still which workflow is executing for which story: this is
    # the second thing it says, on every entry, since the walk is re-run on
    # every entry against the artifact as it is now.
    append_event(
        run_dir,
        f"mandate resolved in {resolution.hops} hop(s): source kind "
        f"'{resolution.kind}', conferred by "
        f"{resolution.identity or 'an identity the mandate does not name'}",
        kind="note",
    )

    # The opportunistic sweep, below every refusing pre-flight and above the
    # first stage invocation. Every check above this point refuses — the
    # undeclared configuration key, the bad pause wait, the unreadable story,
    # the unresolved mandate, the unknown workflow, the finished branch, the
    # bad base, the dirty tree, the two resume collisions, the refused
    # checkout and the unparseable history — so a run refused for any of those
    # reasons leaves the queue exactly as it was. It sits below the workflow
    # announcement and the mandate note for story-072's reason: the first thing
    # a run says is still which workflow is executing for which story.
    #
    # **This one must not refuse, and that is the whole reason it is a
    # different function from the one l5-sync calls.** Restoring consistency
    # with the refusals above it would defeat the mechanism: a sweep is an
    # attempt to be helpful with a network that may be down, and a sweep that
    # could say no would make the outbox the blocker the queue exists to
    # prevent — an undrainable queue stopping the run that was going to drain
    # it. `outbox_sweep.sweep` returns a Summary on every path and raises on
    # none, and nothing about its result is read here: no branch, no early
    # return, no status. The run directory is passed so what it did reaches
    # this run's events.log, which is safe here and is not safe after the
    # completion commit — see the sweep at the end of `_complete`.
    outbox_sweep.sweep(target_root, config, harness_root, run_dir=run_dir)

    # The second of the three moments, beside that sweep and on a fresh run
    # only. A resumed run has already been announced, and nothing here knows
    # whether the tracker moved the item on since, so re-announcing on every
    # escalated, crashed and capacity resume would make the harness the author
    # of a status history nobody asked for. The cost is stated: a story that
    # escalated and was resumed shows no fresh movement in the tracker for the
    # second half of its life, and the moment that follows is the completion.
    #
    # Whether this entry is a fresh run is decided by the same loaded state
    # that decides the resume, captured before that state was replaced.
    #
    # It may not refuse, for the reason the sweep above it may not: an item
    # that cannot be moved is not a reason to refuse the run it was reporting
    # on. Nothing branches on the result — what it did is reported into this
    # run's events.log, which is safe here and is not safe after the
    # completion commit, and a run that invoked nothing says nothing.
    if fresh_run:
        moved = announce_item_status(
            reading.parsed, config, target_root, item_update.IN_PROGRESS
        )
        if moved is not None:
            append_event(
                run_dir,
                f"item status {item_update.IN_PROGRESS} sent for {story_id}"
                if moved.published
                else f"item status {item_update.IN_PROGRESS} was not sent for "
                f"{story_id}: {moved.reason}",
                kind="note",
            )

    # A branch that already existed was cut from the base as it stood then, and
    # the base has moved since or it has not. Say so once, as a note: what to
    # do about it is the developer's call, and nothing here rebases, resets or
    # routes on it. One append_event call, so events.log and
    # execution-history.json stay two renderings of one write.
    if branch_existed:
        behind = branch_behind(target_root, state.branch, resolved_base)
        if behind:
            append_event(
                run_dir,
                f"branch {state.branch} is {behind} commit(s) behind base "
                f"{resolved_base}",
                kind="note",
            )

    # A base check that answered from an unrefreshed tracking ref says so in the
    # run's own record, beside the note above, so a run that proceeded on old
    # information is not indistinguishable from one that proceeded on current
    # information. A refresh that was made appends nothing.
    if refresh_reason:
        append_event(run_dir, refresh_reason, kind="note")

    # Stage timing: started where the stage-started event is appended, read at
    # whichever event ends the stage, so a completed stage carries an elapsed
    # duration the log only made derivable.
    stage_started_at: float | None = None

    # What routed the attempt now running, for the stage receiving it to read.
    # Set where a retry is routed and read where the next stage's context is
    # assembled; the values themselves come off the loaded workflow.
    routed_category: str | None = None
    routed_stage: str | None = None

    def elapsed() -> float | None:
        if stage_started_at is None:
            return None
        return round(time.monotonic() - stage_started_at, 3)

    # Whether the iteration about to begin is a stage re-running itself. Every
    # other way of entering a stage — advancing, a retry's reroute, the first
    # iteration after a resume — starts that stage with its full budget, so the
    # count is zeroed unless this is set.
    self_routed = False

    index = stage_names.index(state.current_stage)
    while index < len(stages):
        stage = stages[index]
        name = stage["name"]
        state.current_stage = name
        if not self_routed:
            state.self_route_count = 0
            state.bookkeeping_self_route_count = 0
        self_routed = False
        save_state(run_dir, state)
        stage_started_at = time.monotonic()
        # Bare where the history holds too few observations of this stage, and
        # carrying the parenthesised estimate where it holds enough; the clause
        # is rendered by the one spelling `_announcement` also renders.
        append_event(
            run_dir,
            _with_estimate(f"{name} stage started", duration_estimate(name)),
            kind="stage-started",
            stage=name,
        )

        attempt = state.retry_count + 1

        # A stage that declares an inspection is preceded by one: the Inspector
        # reads what this attempt has produced, and its findings about the files
        # the story itself changed are handed to this stage as evidence while
        # the change can still be answered, instead of being filed as briefs
        # against whatever story comes next. Every other finding it makes is
        # held beside the findings record for the run's completion, where
        # _complete files the final attempt's once through file_at_completion.
        #
        # Once per attempt, decided by whether the attempt has already written
        # its artifact rather than by a counter: a self-route or a correction
        # pass re-entering this stage within the attempt finds the file and
        # renders it, and a retry — which means the code changed, and a stale
        # reading is worse than none — writes its own under the next attempt's
        # name. The declaration is read off the stage about to be entered, so no
        # stage name and no artifact name is written here, and a workflow
        # declaring none renders None and inspects from `_complete` as before.
        #
        # Nothing below branches on any of this. The inspection returns nothing,
        # raises on no path, and what it wrote is read only to be rendered: the
        # verifier decides and the inspection supplies.
        inspection_findings = None
        declared_inspection = (stage.get("inspection") or {}).get("result")
        if declared_inspection:
            record = run_dir / story_inspection.findings_artifact_file(
                declared_inspection, attempt
            )
            if not record.is_file():
                story_inspection.inspect_before_stage(
                    run_dir, target_root, config or {}, harness_root,
                    state.story_id, stages,
                    artifact=declared_inspection, attempt=attempt,
                )
            if record.is_file():
                inspection_findings = record.read_text(encoding="utf-8")

        # A stage running again after a mechanical failure carries the
        # coordinator's own statement of why. It is read back off the artifact
        # just written rather than passed along in memory, so what the prompt
        # says and what the run directory records are one thing. A stage that
        # did not self-route has a zero count and renders None — including a
        # stage running after another stage self-routed earlier, because the
        # count is reset at every entry that is not itself a self-route.
        self_route_result = None
        if state.self_route_count:
            evidence = run_dir / self_route_result_file(
                name, attempt, state.self_route_count
            )
            if evidence.is_file():
                self_route_result = evidence.read_text(encoding="utf-8")

        # A stage running because a passing verdict carried a correctable
        # finding carries the coordinator's own record of that pass. Read back
        # off the artifact just written rather than passed along in memory, so
        # what the prompt says and what the run directory records are one
        # thing, exactly as the self-route evidence above is. The artifact name
        # comes off the workflow's declaration wherever a stage carries it, so
        # no stage name and no artifact name is written here. It renders as
        # None for every stage of a run that took no pass, including the
        # stages that ran before one.
        correction_pass_result = None
        if state.correction_pass_count:
            declared = correction_pass_declaration(stages).get("result")
            if declared:
                record = run_dir / correction_pass_result_file(
                    declared, state.correction_pass_count
                )
                if record.is_file():
                    correction_pass_result = record.read_text(encoding="utf-8")

        # The same for a repair pass: the current pass record, read back off
        # the artifact the coordinator wrote, into every stage prompt, so the
        # stage a pass entered is told the findings and the corrections and
        # nothing else. None for every stage of a run that took none.
        repair_pass_result = None
        if state.repair_pass_count:
            declared = repair_pass_declaration(stages).get("result")
            if declared:
                record = run_dir / repair_pass_result_file(
                    declared, state.repair_pass_count
                )
                if record.is_file():
                    repair_pass_result = record.read_text(encoding="utf-8")

        # The coordinator's record of the suite it ran after some stage's turn
        # ended. Read back off the artifact rather than passed along in memory,
        # for the reason the two records above are, and named off the
        # workflow's own declaration wherever a stage carries it, so no stage
        # name and no artifact name is written here. It renders as None for
        # every stage of a run under a workflow that declares no suite run, and
        # for the stages that run before the first one.
        suite_run_result = None
        declared_suite = suite_run_declaration(stages).get("result")
        if declared_suite:
            record = run_dir / declared_suite
            if record.is_file():
                suite_run_result = record.read_text(encoding="utf-8")

        # What every revert check this run has reached decided, read back off
        # the artifacts rather than passed along in memory, and named off each
        # stage's own declaration wherever a stage carries one — so no stage
        # name and no artifact name is written here and a workflow declaring no
        # revert check renders None. A stage is named beside its record because
        # more than one stage can declare the check and a reader has to be able
        # to tell whose edits were undone.
        revert_records = [
            f"{stage_name}:\n{(run_dir / artifact).read_text(encoding='utf-8')}"
            for stage_name, artifact in (
                (declaring["name"], (declaring.get("revert_check") or {}).get("result"))
                for declaring in stages
            )
            if artifact and (run_dir / artifact).is_file()
        ]

        # The grants this stage is invoked with: the target's own, plus the
        # one that makes the output check it is invited to run permitted. One
        # read of the configured key feeds both sites that pass them, so the
        # rendered prompt lists exactly what the invocation permits.
        stage_tools = stage_allowed_tools(
            allowed_tools=config.get("allowed_tools"),
            harness_root=harness_root,
        )

        context = context_assembler.build_context(
            story_text=story_text,
            story=reading.parsed,
            run_dir=run_dir,
            target_root=target_root,
            harness_root=harness_root,
            config=config,
            rules=rules,
            workflow=workflow,
            retry_count=state.retry_count,
            retry_category=routed_category,
            retry_stage=routed_stage,
            allowed_tools=stage_tools,
            self_route_result=self_route_result,
            correction_pass_result=correction_pass_result,
            repair_pass_result=repair_pass_result,
            suite_run_result=suite_run_result,
            revert_check_result="\n".join(revert_records) or None,
            inspection_findings=inspection_findings,
            stage=name,
        )
        template = context_assembler.load_template(harness_root, stage["prompt"])
        prompt = context_assembler.render(template, context)
        # A self-route does not move the attempt number, so the re-run's prompt
        # would otherwise be written over the prompt the failed invocation was
        # actually given — the evidence a re-run exists to build on. The try
        # suffix appears only when a self-route occurred; a first invocation
        # writes exactly the filename it has always written.
        (run_dir / prompt_file(name, attempt, state.self_route_count)).write_text(
            prompt, encoding="utf-8"
        )

        # What this stage's artifacts looked like before it ran. One snapshot,
        # covering the conditional artifacts and the required ones together,
        # feeding one comparison function. The conditional readers ask which
        # of them this attempt wrote — nothing clears the retry guidance
        # between attempts — and the required reader below asks the same
        # question of the outputs, because a retry that leaves one untouched
        # satisfies a presence check with a superseded attempt's file.
        # artifacts_written_since narrows by the names its caller passes, so
        # widening what is covered here changes neither conditional reader.
        conditional = conditional_artifacts(stage)
        required = required_artifacts(stage)
        artifacts_before = artifact_signatures(run_dir, conditional + required)
        # The same snapshot, written where the stage can read it, so a stage
        # can ask about its own outputs while its turn is still open. Every
        # check below still reads the in-memory dict: this is what the stage
        # is told, never what the coordinator decides.
        write_output_baseline(run_dir, name, artifacts_before)

        # What the whole tree held before this stage ran, so the completeness
        # check below can say what the stage changed without asking the stage.
        # Taken beside the stage baselines below and from the same tree;
        # first-seen-wins within the attempt, so a stage re-entered in place is
        # compared against what it originally found rather than against its own
        # uncorrected edits. Driven off the changed_files declaration alone: a
        # stage that declares no record has nothing to be held to, and no stage
        # name is written here.
        # The same account the baseline capture below is narrowed by, bound to
        # a name rather than spelled a second time at the call: two identical
        # calls would be two call sites a reader — and anything that rewrites
        # one to show what it decides — could not tell apart.
        changed_elsewhere = recorded_by_other_stages(run_dir, stages, name)
        signature = (
            capture_tree_signature(
                run_dir, target_root, name, attempt, state.resume_count,
                accounted_for=changed_elsewhere,
            )
            if stage.get("changed_files")
            else {}
        )

        # What the tree held under this stage's governed prefixes before it
        # ran, which is the baseline the revert check below decides against.
        # Both names come off the stage's declaration, so removing that one
        # key disables the capture and the check together. Captured over the
        # declared prefixes rather than the grant-subtracted list: the
        # enforced list is computed after the stage, and capturing a superset
        # costs a few file copies while the restore set stays narrowed. What a
        # re-entry may add to it is narrowed the other way, to the paths
        # another stage's own record accounts for, so a re-run is never decided
        # against the partial work a crashed invocation left behind.
        # The suite census below decides against the same fact and resolves it
        # the same way, so the two declarations are collected into one capture
        # per baseline directory they name rather than each capturing for
        # itself — a second capture of the same directory would be read as a
        # re-capture and narrow what it may admit for a reason that has nothing
        # to do with the stage running again.
        declaration = stage.get("revert_check") or {}
        census_declaration = stage.get("suite_census") or {}
        baselines = {
            baseline: capture_stage_baseline(
                run_dir,
                target_root,
                baseline,
                name,
                prefixes,
                accounted_for=recorded_by_other_stages(run_dir, stages, name),
            )
            for baseline, prefixes in stage_baseline_requests(stage).items()
        }
        baseline_dir = baselines.get(declaration.get("baseline"))
        census_baseline = baselines.get(census_declaration.get("baseline"))

        # The run ceiling, compared immediately above the invocation it would
        # stop. It bounds *repetition* — how much a run may spend across every
        # stage and every attempt of its current entry — where the per-execution
        # allowance below bounds one call. An absent ceiling compares nothing:
        # absent is unbounded, which is what every run did before ceilings
        # existed. `is not None` rather than truthiness, because zero is a
        # deliberate refusal to run at all and must stop the first invocation.
        run_ceiling = workflow.get("max_run_cost_usd")
        if run_ceiling is not None and state.entry_cost_usd >= run_ceiling:
            reason = (
                f"this run has spent {format_usd(state.entry_cost_usd)}, at or "
                f"past the run ceiling of {format_usd(run_ceiling)}, so "
                f"{name} was not invoked"
            )
            state.stopped_on_cost = True
            _budget_stopped(run_dir, name, reason)
            return _escalate(
                run_dir,
                state,
                reason,
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        # The allowance this one invocation is handed: the stage's declared
        # ceiling, unmodified by what the stage or the run has already spent.
        # Flat rather than a remainder, deliberately — a self-route has to redo
        # the work the failed invocation was doing, and handing it what is left
        # invites it to fail again for a new reason. Repetition is bounded by
        # max_self_routes, max_retries and the run ceiling above; the size of
        # one call is bounded here. The consequence is that a run may finish
        # above the run ceiling by at most one execution allowance, which is
        # the price of not starving a legitimate retry.
        #
        # The keyword is passed only when the stage declares a ceiling, so a
        # stage under none calls the runner with exactly the arguments it
        # passed before this existed and every fake runner keeps its signature.
        execution_ceiling = stage.get("max_execution_cost_usd")
        budget = (
            {} if execution_ceiling is None else {"max_budget_usd": execution_ceiling}
        )

        # The configured test command, handed to the stage's Bash guard so a
        # run of it inside the turn is denied. Built the way the ceiling above
        # is: present only for a stage whose workflow entry declares that it
        # runs no suite, absent otherwise, so a stage under no declaration is
        # invoked with exactly the arguments it was invoked with before. A
        # target configuring no test command has nothing to compare against, so
        # the keyword is absent there too.
        #
        # Unlike the ceiling above, this does reach the fake runners: a suite
        # test driving either shipped definition drives a stage that declares
        # the restriction, so the keyword is passed and the runner must accept
        # it. The absent-when-undeclared shape bounds which runners that is, it
        # does not spare them all.
        test_command = config.get("test_command")
        no_suite = (
            {"suite_command": test_command}
            if stage.get("may_not_run_suite") and test_command
            else {}
        )

        result = runner(
            prompt,
            stage=name,
            cwd=target_root,
            log_path=log_path,
            permission_mode=config.get("permission_mode", "acceptEdits"),
            model=config.get("model"),
            allowed_tools=stage_tools,
            run_dir=run_dir,
            **budget,
            **no_suite,
        )

        # The cost the invocation reported, added to the live allowance and
        # appended to the record. An invocation reporting none adds nothing to
        # either: the harness carries what it was told and infers nothing.
        cost = result.cost_usd
        if cost is not None:
            state.entry_cost_usd += cost
            append_cost_record(
                run_dir,
                stage=name,
                entry=state.resume_count,
                attempt=attempt,
                cost=cost,
            )
            save_state(run_dir, state)

        # Then, before the mechanical-failure checks: did this invocation's own
        # reported cost reach the allowance it was handed? It did, and the run
        # stops on the ceiling; it did not, and the checks below decide as they
        # always have. Deciding it from the recorded cost rather than from the
        # wording of a result the harness does not own keeps this off a string
        # it cannot rely on, and keeps a budget stop out of the self-route path
        # — which would otherwise re-run the stage and spend the money again.
        if (
            execution_ceiling is not None
            and cost is not None
            and cost >= execution_ceiling
        ):
            reason = (
                f"{name} spent {format_usd(cost)} of its execution allowance "
                f"of {format_usd(execution_ceiling)}, so the invocation was "
                f"stopped on its budget rather than failing at its work"
            )
            state.stopped_on_cost = True
            _budget_stopped(run_dir, name, reason)
            return _escalate(
                run_dir,
                state,
                reason,
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        # Before any of that: did the invocation fail because capacity ran
        # out? A budget ceiling is a reason to stop and capacity exhaustion is
        # only a reason to wait, so this run pauses rather than ending. It is
        # decided from the field the agent runner set and from no string — the
        # coordinator never reasons about a capacity failure — and it sits
        # *above* the self-route decision deliberately: the stage did not fail
        # at its work, it did not run, so it must not spend a self-route.
        #
        # The pause is written and committed before anything sleeps. What
        # follows is opportunistic: a known reset time within the configured
        # bound is slept to and this same stage re-entered, and anything else
        # returns the pause exit code, leaving a run a fresh l5-run resumes at
        # exactly this stage.
        if not result.ok and result.capacity:
            reason = (
                f"{name} stopped because capacity ran out "
                f"({result.capacity.signal})"
            )
            _pause(
                run_dir, state, result.capacity, reason, target_root=target_root
            )
            wait = pause_wait_seconds(
                result.capacity.reset_at, time.time(), pause_wait_bound(config)
            )
            if wait is None:
                return PAUSE_EXIT_CODE
            append_event(
                run_dir,
                f"waiting {round(wait)}s in place for capacity, within the "
                f"configured bound",
                kind="note",
                stage=name,
            )
            sleep(wait)
            resume_from_capacity(run_dir, state)
            # Re-entered as the same attempt of the same stage: nothing was
            # spent, so nothing moves. `self_routed` is set for its one effect
            # here — it is what stops the loop's head zeroing self_route_count,
            # which a pause must leave exactly where it found it.
            self_routed = True
            continue

        # The first of the three mechanical failures. Each is routed through
        # one decision: re-enter the loop at this same index when the stage has
        # unspent budget, escalate with the same reason it always escalated
        # with when it has none.
        if not result.ok:
            decision = self_route(
                run_dir,
                state,
                stage,
                failure=AGENT_PROCESS_FAILED,
                reason=f"{name} agent process failed",
                artifacts=[],
                attempt=attempt,
            )
            if decision.taken:
                self_routed = True
                continue
            return _escalate(
                run_dir,
                state,
                decision.reason,
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        missing = [out for out in stage.get("outputs", []) if not (run_dir / out).is_file()]
        if missing:
            decision = self_route(
                run_dir,
                state,
                stage,
                failure=MISSING_REQUIRED_ARTIFACTS,
                reason=(
                    f"{name} did not produce required artifacts: "
                    f"{', '.join(missing)}"
                ),
                artifacts=missing,
                attempt=attempt,
            )
            if decision.taken:
                self_routed = True
                continue
            return _escalate(
                run_dir,
                state,
                decision.reason,
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        # Present is not written. On a retry an artifact a superseded attempt
        # left at the run root satisfies the check above, and the run finishes
        # carrying a record of an attempt that no longer describes it. Read
        # after the missing case so an absent artifact keeps its own reason;
        # the two are indistinguishable to a reader of the run directory
        # afterwards, which is what let one ship unnoticed. story-022 recorded
        # the escalation here as provisional, pending a route from a stage that
        # skipped its output back to that stage; this is that route, so a stage
        # with unspent budget now runs again in place and only a stage without
        # one escalates. Either way no attempt number is consumed.
        stale = stale_artifacts(run_dir, required, artifacts_before)
        if stale:
            decision = self_route(
                run_dir,
                state,
                stage,
                failure=STALE_REQUIRED_ARTIFACTS,
                reason=(
                    f"{name} left required artifacts unwritten; these are a "
                    f"previous attempt's, not this one's: {', '.join(stale)}"
                ),
                artifacts=stale,
                attempt=attempt,
            )
            if decision.taken:
                self_routed = True
                continue
            return _escalate(
                run_dir,
                state,
                decision.reason,
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        violation = _schema_violation(run_dir, stage)
        if violation:
            return _escalate(
                run_dir,
                state,
                f"{name} wrote an invalid artifact: {violation}",
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        record_name = stage.get("changed_files")
        if record_name:
            # Is the record complete? Asked first, and before the three checks
            # below that read it, because all three decide from what it names:
            # an omitted governed path is precisely how an edit escapes the
            # revert check that exists to decide whether it was forced, and a
            # path no record names is one the post-story inspection never takes
            # into scope.
            #
            # The coordinator does not amend a record. It reports the omission
            # and re-enters the stage that wrote it, which is the only stage
            # that may correct it — the block on .harness/runs/ stays, and
            # nothing here creates a route around it. The cause is a
            # bookkeeping one, so a stage with an unspent bookkeeping budget
            # runs again in place and a stage without one escalates with the
            # omitted paths named.
            omitted = unrecorded_changes(
                run_dir,
                record_name,
                tree_changes(
                    signature,
                    target_root,
                    rules.get("blocked_paths", [])
                    + coordinator_written_prefixes(config),
                ),
            )
            missing = describe_unrecorded(omitted)
            if missing:
                decision = self_route(
                    run_dir,
                    state,
                    stage,
                    failure=INCOMPLETE_CHANGED_FILES,
                    reason=(
                        f"{name} changed repository files {record_name} does "
                        f"not name: {missing}"
                    ),
                    artifacts=[record_name],
                    attempt=attempt,
                    entries=[
                        f"{group}: {path}"
                        for group in CHANGED_FILE_GROUPS
                        for path in omitted[group]
                    ],
                )
                if decision.taken:
                    self_routed = True
                    continue
                return _escalate(
                    run_dir,
                    state,
                    decision.reason,
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                )

            violation = _blocked_violation(run_dir, record_name, rules.get("blocked_paths", []))
            if violation:
                return _escalate(
                    run_dir,
                    state,
                    f"{name} modified blocked path: {violation}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                )

            # Stage output ownership, read from the workflow definition the
            # way blocked paths are read from the rules. A story may lift a
            # declared prefix for one stage; the grant is recorded so the
            # routing stays reconstructable from events.log.
            # The stage's restrictions arrive whole, off the one derivation
            # plan time reads, so no declaration key and no sense conditional
            # is read here: whether a restriction governs a path is the
            # restriction's decision. A grant is an exemption on the paths it
            # covers rather than the removal of a restriction, so a story
            # granting one file leaves the rest of what that restriction
            # governs governed. One event per grant whatever its granularity.
            enforced = restrictions_on(stage)
            exempt = granted_paths(reading.parsed, name)
            for granted in exempt:
                append_event(
                    run_dir,
                    f"stage exception applied: {name} may create {granted}",
                    kind="stage-exception-applied",
                    stage=name,
                )
            # A correction pass grants the stage it enters the files its
            # findings name, for that pass alone. The verifier's verdict copied
            # verbatim into the pass record is the grant, so what was granted is
            # recorded by the act that granted it; the exempt list here is the
            # story's own grants plus the path of every finding in the current
            # pass record, when that record names this stage and this attempt.
            # Each granted path is an event beside `stage exception applied` so
            # the two read alike in events.log.
            pass_grants = correction_pass_grants(
                run_dir, stages, state, name, attempt
            )
            for granted in pass_grants:
                append_event(
                    run_dir,
                    f"correction-pass grant applied: {name} may change {granted}",
                    kind="correction-pass-grant-applied",
                    stage=name,
                )
            # A repair pass grants each stage it enters the files of the
            # findings whose category resolved to that stage, for that attempt
            # alone, on the same exempt list and the same terms: the record
            # copies the findings with their resolved stages, so what was
            # granted is recorded by the act that granted it.
            repair_grants = repair_pass_grants(
                run_dir, stages, state, name, attempt
            )
            for granted in repair_grants:
                append_event(
                    run_dir,
                    f"repair-pass grant applied: {name} may change {granted}",
                    kind="repair-pass-grant-applied",
                    stage=name,
                )
            # A finding the pass carries into a file outside the story's
            # declared scope is granted above as any other finding is; what
            # is added is the announcement, one per extension, in the form
            # the grant lines take and through the same writer, so
            # events.log, execution-history.json and the console stay
            # renderings of one write.
            for granted, reason in repair_pass_extensions(
                run_dir, stages, state, name, attempt
            ):
                append_event(
                    run_dir,
                    f"scope-extension grant applied: {name} may change "
                    f"{granted}: {reason}",
                    kind="scope-extension-grant-applied",
                    stage=name,
                )
            exempt = exempt + pass_grants + repair_grants
            ownership = _ownership_violation(run_dir, record_name, enforced, exempt)
            if ownership:
                return _escalate(
                    run_dir,
                    state,
                    f"{name} created {ownership.path}, which the workflow "
                    f"does not permit: {ownership.restriction.wording}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                )

            # The revert check, on the same record, the same enforced
            # prefixes and the same exemption the ownership check just used —
            # so a granted path is exempt from both — the one record whose
            # edits under those prefixes are known to be this stage's alone.
            # The artifact name comes off the loaded workflow definition, so
            # removing that declaration disables the check with no change here.
            # It is the same declaration the baseline was captured under, read
            # here for the other name it carries.
            revert_artifact = declaration.get("result")
            edits = (
                governed_edits(run_dir, record_name, enforced, exempt)
                if revert_artifact
                else GovernedEdits((), ())
            )
            if edits.paths:
                governed = "; ".join(edits.wordings)
                listed = ", ".join(edits.paths)
                decided = revert_check(
                    run_dir,
                    target_root,
                    config,
                    revert_artifact,
                    edits.paths,
                    baseline_dir,
                    stage_name=name,
                    nomination=nominated_test(run_dir, record_name),
                )
                # Three dispositions, and they answer different questions.
                #
                # A check that reached no verdict escalates: nothing was
                # proved, so nothing may be undone. That is not the same
                # question as a check that did not *run* — since the nomination
                # can permit these edits without the configured test command
                # being run at all, `ran` false would escalate a permission.
                #
                # A refusal does not escalate, because it is the one verdict
                # the harness has already proved harmless: the check built a
                # clone with exactly these paths restored to what the stage
                # found and watched the suite pass there. Reproducing that
                # content in the working tree reproduces the tree the proof was
                # taken on, so the coordinator restores the offending paths
                # from the stage's baseline, records what it undid and what it
                # discarded, and the run continues. A stopped run over a case
                # the harness has proved harmless is the failure; the record
                # beside the stage's own attestation is the correction.
                #
                # A permission is left standing or routed, and the edits say
                # which: the check answers whether they were needed, and
                # whether the stage was entitled to make them is the matched
                # restriction's answer, carried on the set. Routed edits are
                # undone by the same restore the refusal uses, kept under the
                # same discarded directory, and recorded as permitted with a
                # `routed` key beside the reverted block, so the tree carries
                # on lacking work that belongs to another stage — which is what
                # the suite run after the authoring stage, and the clean-clone
                # check after the verifier, turn red on and deliver to the
                # verifier to route. No sense is compared here.
                if decided.permitted is None:
                    return _escalate(
                        run_dir,
                        state,
                        f"the revert check on {name}'s edits ({listed}), "
                        f"governed by {governed}, could not run: "
                        f"{decided.result.reason}",
                        target_root=target_root,
                        harness_root=harness_root,
                        duration_seconds=elapsed(),
                    )
                if not decided.permitted:
                    reverted = revert_refused_edits(
                        run_dir,
                        target_root,
                        edits.paths,
                        baseline_dir,
                        declaration.get("discarded"),
                    )
                    _record_revert_check(
                        run_dir,
                        revert_artifact,
                        replace(decided, reverted=reverted),
                    )
                    _revert_check_reverted(
                        run_dir, name, revert_artifact, edits, reverted,
                        decided.result.duration_seconds,
                    )
                elif edits.routes:
                    reverted = revert_refused_edits(
                        run_dir,
                        target_root,
                        edits.paths,
                        baseline_dir,
                        declaration.get("discarded"),
                    )
                    _record_revert_check(
                        run_dir,
                        revert_artifact,
                        replace(
                            decided,
                            reverted=reverted,
                            routed="; ".join(edits.routed),
                        ),
                    )
                    _revert_check_routed(
                        run_dir, name, revert_artifact, edits, reverted
                    )
                else:
                    _revert_check_permitted(
                        run_dir, name, revert_artifact, edits,
                        decided.result.duration_seconds,
                    )

        # The suite census, declared like the checks above it: one key names
        # the artifact and turns the check on, so no stage name appears here
        # and a workflow declaring it nowhere takes no census and announces
        # nothing. It stands in for the ownership and revert checks under a
        # workflow whose stages legitimately edit the validation, and it reads
        # the same stage baseline they do.
        census_artifact = census_declaration.get("result")
        if census_artifact:
            census = suite_census_check(
                run_dir,
                target_root,
                config,
                census_artifact,
                census_declaration.get("paths", []),
                census_baseline,
                stage_name=name,
            )
            # A census that could not be taken permits nothing, exactly as the
            # revert check's own could-not-run path decides — except where the
            # target configured no census command at all, which is a target
            # declining the check rather than a check that failed.
            if not census.ran and config.get("census_command"):
                return _escalate(
                    run_dir,
                    state,
                    f"the suite census declared on {name} could not be taken: "
                    f"{census.reason}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                )
            if census.ran:
                # Its completion, said before the verdict is acted on, so how
                # long it took is recorded whether or not it permits.
                _suite_census_completed(run_dir, name, census_artifact, census)
            if census.permitted is False:
                fell = "; ".join(item.describe() for item in census.regressions)
                return _escalate(
                    run_dir,
                    state,
                    f"the suite census taken after {name} is weaker than the "
                    f"one taken at its baseline: {fell}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                )

        # The claim-support check, declared like the two above: one key names
        # the artifact and turns the check on, so the stage it runs after is
        # read off the workflow and no stage name appears here. It reports and
        # routes nothing — the record is written, a note is appended, and the
        # verifier that receives the record decides what a report means. A run
        # whose record reports a claim is therefore routed exactly as the same
        # run with an empty record.
        claim_support = stage.get("claim_support") or {}
        claim_artifact = claim_support.get("result")
        if claim_artifact:
            supported = claim_support_check(
                run_dir,
                target_root,
                config,
                claim_artifact,
                resolved_base,
                story_id,
            )
            _claim_support_recorded(run_dir, name, claim_artifact, supported)

        # The suite run a stage declares, run here because the stage that
        # authors validation cannot run a command longer than its own turn. It
        # sits below the required-output, freshness and schema checks, so a
        # stage that produced nothing is not charged a suite run, and above the
        # advance, so the workflow moves on only over a green suite. The
        # declaration is read off the loaded stage dict, so no stage name
        # appears here and a workflow declaring it nowhere runs the stage
        # exactly as it ran before this existed.
        suite_run = stage.get("suite_run") or {}
        suite_artifact = suite_run.get("result")
        if suite_artifact:
            # The attempt and the try key the pair this run retains. The try is
            # the same self_route_count the prompt for the invocation this run
            # judges was keyed by, so the two share a key by construction.
            ran = suite_run_check(
                run_dir,
                target_root,
                config,
                suite_artifact,
                stage_name=name,
                attempt=attempt,
                try_number=state.self_route_count,
            )
            retained_suite = retained_suite_result_file(
                suite_artifact, name, attempt, state.self_route_count
            )
            if not ran.ran:
                # A check that could not run permits nothing, as the
                # clean-clone check's own could-not-run path already decides.
                return _escalate(
                    run_dir,
                    state,
                    f"the suite run declared on {name} could not run: "
                    f"{ran.reason}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                )
            # The run's completion, said before its exit code is acted on, so
            # how long it took is recorded whether it was green or red.
            _suite_run_completed(run_dir, name, suite_artifact, ran)
            if ran.exit_code != 0:
                # The failure is outstanding from the moment it happens, and it
                # is written here — where the failure is met — rather than in a
                # second place that could disagree with this one. Its shape and
                # its values are what this block wrote before the failure
                # started being carried forward rather than routed back: the
                # stage, the attempt, the try, the recorded scope, the exit
                # code, and the retained result and output paths.
                # Everything a later refusal has to name comes off the run
                # just made and the retained paths already computed above, so
                # the failure is reconstructable from state.json alone.
                state.unshadowed_suite_failure = {
                    "stage": name,
                    "attempt": attempt,
                    "try": state.self_route_count,
                    "scope": list(ran.scope),
                    "exit_code": ran.exit_code,
                    "result_path": retained_suite,
                    "output_path": ran.output_path or "",
                }
                # The failure is carried forward rather than attributed. A red
                # suite says the tree is broken and says nothing about which
                # stage broke it, which is a judgement the coordinator is not
                # able to make — and for the stage that authors validation it
                # is backwards by construction, since that stage may only
                # change files under the tests directory and is the one stage
                # forbidden to fix the usual cause. So the run advances to the
                # next stage, no self-route is taken and no retry is spent, and
                # the failure reaches the verifier, which already reads the
                # workflow's retry_routing table and already writes back a
                # category the coordinator routes on. The harness gains no
                # judgement source and no category: the judgement moves to the
                # agent that was always making judgements of this kind. What
                # stops a run that nothing superseded the failure for is the
                # refusal at the end of this function.
                append_event(
                    run_dir,
                    suite_failure_carried_forward_note(
                        state.unshadowed_suite_failure
                    ),
                    kind="note",
                    stage=name,
                )
                save_state(run_dir, state)
            elif state.unshadowed_suite_failure:
                # A green suite is the more recent result, which is not by
                # itself a reason to supersede the failure before it: a rerun
                # narrowed until the answer is convenient establishes nothing
                # about what it stopped running. The pass supersedes the
                # failure only when it ran at least as much, by the recorded
                # scopes alone.
                outstanding = state.unshadowed_suite_failure
                if suite_run_shadows(ran.scope, outstanding.get("scope", [])):
                    state.unshadowed_suite_failure = {}
                    # Saved where the decision is made rather than at the next
                    # stage's start, so a crash between the two cannot leave a
                    # failure recorded that a shadowing pass has superseded.
                    save_state(run_dir, state)
                    # Recorded after the save, as the clean-clone block records
                    # its clear: the clear is a routing decision on either
                    # path, so it is reconstructable from events.log and not
                    # only from the field's absence in state.json. The line
                    # names the failure that was cleared, off the record read
                    # before the field was emptied.
                    append_event(
                        run_dir,
                        suite_failure_shadowed_by_declared_rerun_note(
                            outstanding
                        ),
                        kind="note",
                        stage=name,
                    )
                else:
                    # What stands is the suite failure, and a standing red
                    # suite never re-enters the authoring stage by any path:
                    # this route advances too, leaving the outstanding failure
                    # in state for the verifier to judge and for the
                    # end-of-run refusal to stop the run on if nothing
                    # supersedes it. The event names the *original* failure's
                    # stage, exit code and retained pair rather than this
                    # rerun's, because that is the failure a reader has to be
                    # pointed at.
                    append_event(
                        run_dir,
                        suite_failure_carried_forward_note(outstanding),
                        kind="note",
                        stage=name,
                    )

        if name == "verifier":
            verdict = json.loads((run_dir / "verification-result.json").read_text(encoding="utf-8"))
            state.verification_iterations += 1
            archive = run_dir / "verification" / f"iteration-{state.verification_iterations}.json"
            archive.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
            # Every finding the pre-stage inspection routed to this story is
            # accounted for here, after the verdict is archived and before any
            # routing: what the verdict acted on is named by slug, and the rest
            # is recorded as the remainder in the accounting artifact and filed
            # at the run's completion, not here.
            # Only where the stage declares an inspection, and read off that
            # declaration, so no artifact name is written here. The call is a
            # bare statement and returns nothing; what it recorded is read back
            # off its artifact for wording the two re-entry events below and by
            # no routing decision.
            # Where a failed verification goes is read off the table this
            # stage declares, keyed on the category the verifier reported.
            # There is no default: a recommended retry naming no category, or
            # one the table does not define, escalates below rather than being
            # absorbed by a fallback route — a silent fallback is the drift the
            # table exists to remove. No category name appears here.
            routes = stage.get("on_failure", {}).get("retry_routing", {})
            # A repairable finding reaching outside the story's declared
            # scope is decided here, before the accounting and before any
            # pass record is written: each finding carrying scope_extension is
            # put to the three bounds, a refusal is one event naming the
            # bound, and the verdict the accounting is handed has the refused
            # finding removed and recorded as declined with the refusal as its
            # reason. The archived verdict above is what the verifier wrote;
            # this is what the run decided about it. Nothing here escalates.
            declared_inspection = (stage.get("inspection") or {}).get("result")
            extensions = scope_extension_check(
                verdict.get("repairable_findings") or [],
                routes, stage_names, stages, reading.parsed, rules,
            )
            for refused in extensions.refused:
                _scope_extension_refused(run_dir, name, refused)
            # The routed slugs the refusals named, computed once and handed
            # to the accounting beside the transformed verdict: a routed
            # finding fixed as two repairable findings sharing one slug is
            # otherwise counted as acted on through its accepted sibling when
            # the coordinator refused the other file, and the brief the
            # refusal promised is never filed. Empty when nothing was refused.
            refused_slugs: tuple = ()
            if extensions.refused:
                routed_named = (
                    story_inspection.findings_slugs(
                        run_dir, declared_inspection, attempt
                    ) if declared_inspection else ()
                )
                refused_slugs = refused_routed_slugs(extensions, routed_named)
                verdict = verdict_after_refusals(verdict, extensions, routed_named)
            routed_slugs: tuple = ()
            if declared_inspection:
                story_inspection.account_after_verdict(
                    run_dir, target_root, config or {}, harness_root,
                    state.story_id, artifact=declared_inspection,
                    attempt=attempt, verdict=verdict,
                    refused_slugs=refused_slugs,
                )
                routed_slugs = story_inspection.routed_slugs(
                    run_dir, declared_inspection, attempt
                )
            # The artifacts an entry names come off the stage's declared
            # outputs in the loaded workflow, never a list written here.
            outputs = stage.get("outputs", [])
            declared = ", ".join(routes) or "no retry categories"
            target = verdict.get("retry_target")
            if verdict.get("status") == "passed":
                append_event(
                    run_dir,
                    "verification passed",
                    kind="verification-passed",
                    stage=name,
                    artifacts=outputs,
                    duration_seconds=elapsed(),
                    # The verifier's completion is this entry rather than a
                    # stage-completed one, so its operation — its own name,
                    # as every stage's is — travels here for the duration log.
                    operation=name,
                    verifier_outcome=verdict["status"],
                )

                # The two blocks below are read in this order — correction
                # first, clean-clone second — and the order is story-067's,
                # reversing the one story-055 chose deliberately. What it buys
                # is one suite run instead of two: a pass that routes skips the
                # check here and meets it on the verification the pass returns
                # to, so a run taking a correction runs the whole suite in a
                # fresh clone once rather than on both sides of an edit that
                # changes only words. What it costs is that the correction is
                # performed on a tree not yet known clean-clone-good, so a
                # pre-existing clean-clone failure is discovered one stage
                # later than it used to be, with the pass already spent on it.
                #
                # That trade holds only while a correction changes prose and
                # never behaviour. If a pass is ever allowed to change what the
                # code does, the correction would be built on an unverified
                # tree and story-055's order — check first, then correct — has
                # to be restored rather than reasoned about again.
                #
                # A run that routes no pass reaches the check exactly where it
                # reached it before: the correction block falls through when
                # the findings are absent, when the declaration is absent, or
                # when the budget is spent.

                # A finding too small to fail the run has somewhere to go.
                # Read on a passing verdict, because a failed verdict already
                # routes and the correction travels in the retry guidance it
                # writes. Both names come off the one declaration, exactly as
                # clean_clone's do, so removing that key disables the whole
                # mechanism with no change here and the same verdict completes
                # the run as it did before this existed.
                correction = stage.get("correction_pass") or {}
                correction_artifact = correction.get("result")
                findings = verdict.get("correctable_findings") or []
                if correction_artifact and findings:
                    routing = correction_destination(correction, findings, routes)
                    if routing.unknown:
                        # Above the budget comparison for the reason the two
                        # unroutable-retry escalations sit above the ceiling: a
                        # finding naming a category the workflow does not
                        # define is a bug in what the verifier produced, and
                        # that is the reason a developer should read rather
                        # than the budget. Through _escalate, so retry_count is
                        # untouched, and above archive_attempt, so no
                        # attempts/attempt-N/ is written.
                        named = ", ".join(f"'{one}'" for one in routing.unknown)
                        return _escalate(
                            run_dir,
                            state,
                            f"a correctable finding named {named}, which is "
                            f"not a retry category {workflow['name']} defines; "
                            f"it defines: {declared}",
                            target_root=target_root,
                            harness_root=harness_root,
                            duration_seconds=elapsed(),
                            verifier_outcome=verdict.get("status"),
                            retry_decision="escalate",
                            retry_reason=(
                                f"a correctable finding named the unknown "
                                f"category {named}"
                            ),
                        )
                    budget = correction["budget"]
                    if state.correction_pass_count >= budget:
                        _correction_pass_recorded(run_dir, name, findings, budget)
                    else:
                        state.correction_pass_count += 1
                        record = {
                            "pass": state.correction_pass_count,
                            "attempt": attempt,
                            "stage": routing.stage,
                            "findings": findings,
                            "statement": correction_pass_statement(routing.stage),
                        }
                        (
                            run_dir
                            / correction_pass_result_file(
                                correction_artifact, state.correction_pass_count
                            )
                        ).write_text(
                            json.dumps(record, indent=2) + "\n", encoding="utf-8"
                        )
                        # The passing verdict that routed this wrote no
                        # guidance, so none is in force for the verification
                        # the pass runs back into. Cleared here for the same
                        # reason the clean-clone reroute clears it: a stale
                        # guidance read as in force would subject a
                        # verification to a check about an attempt it is not
                        # judging.
                        state.guidance_in_force = []
                        save_state(run_dir, state)
                        _correction_pass_routed(
                            run_dir,
                            name,
                            routing.stage,
                            findings,
                            state.correction_pass_count,
                            budget,
                            routed_slugs,
                        )
                        # Nothing a retry spends is spent: retry_count is
                        # untouched, archive_attempt is not reached, and no
                        # retry-history entry is appended. The pass is not a
                        # retry, so it names no retry category and no retry
                        # destination; what the stage is told comes off the
                        # record just written. The clean-clone check below is
                        # not run for this verdict — it runs on the one the
                        # pass returns to.
                        routed_category, routed_stage = None, None
                        index = stage_names.index(routing.stage)
                        continue

                # The verdict passed where the verifier stood; run the suite
                # once more where the code ships, and put the outcome to the
                # outstanding failure below, since the declared suite may
                # still be red here. The artifact name comes off the loaded
                # workflow definition, so removing that declaration disables
                # the check with no change here.
                # Both names come off the one declaration: the result the
                # check writes and the stage a failure routes to. One key
                # still turns the whole check on, so removing the declaration
                # disables it with no change here.
                clean_clone = stage.get("clean_clone") or {}
                artifact = clean_clone.get("result")
                if artifact:
                    clean = clean_clone_check(
                        run_dir, target_root, config, artifact, stage_name=name
                    )
                    if not clean.ran:
                        return _escalate(
                            run_dir,
                            state,
                            f"the clean-clone check could not run: {clean.reason}",
                            target_root=target_root,
                            harness_root=harness_root,
                            duration_seconds=elapsed(),
                        )
                    if clean.exit_code != 0:
                        failures = _clean_clone_failures(clean.output_tail or "")
                        if state.retry_count >= rules["max_retries"]:
                            return _escalate(
                                run_dir,
                                state,
                                f"the clean-clone check failed and retries are "
                                f"exhausted: {failures}",
                                target_root=target_root,
                                harness_root=harness_root,
                                duration_seconds=elapsed(),
                                retry_decision="escalate",
                                retry_reason=(
                                    f"the retry ceiling of {rules['max_retries']} "
                                    f"was reached"
                                ),
                            )
                        # The same retry path a failed verification takes:
                        # archive above the increment, so the attempt number
                        # names the attempt that just ended.
                        destination = clean_clone["retry_stage"]
                        archive_attempt(
                            run_dir, archivable_artifacts(stages), state.retry_count + 1
                        )
                        entry = append_retry_record(
                            run_dir,
                            state.retry_count + 1,
                            destination,
                            verdict,
                            artifacts_written_since(
                                run_dir, conditional, artifacts_before
                            ),
                        )
                        # A retry routed with no guidance this attempt wrote
                        # leaves none in force, so the verification that
                        # follows is not subjected to the defective-guidance
                        # check. This is that path: a clean-clone failure
                        # follows a passing verdict, which writes no guidance
                        # at all. Taken off the same freshness-checked view
                        # the retry-history entry was built from rather than
                        # read back out of the run root, so a stale guidance
                        # can never be read as in force.
                        state.guidance_in_force = guidance_entries(
                            entry.get("guidance")
                        )
                        state.retry_count += 1
                        save_state(run_dir, state)
                        _clean_clone_failed(
                            run_dir,
                            stage,
                            artifact,
                            destination,
                            state.retry_count,
                            rules["max_retries"],
                            clean.duration_seconds,
                        )
                        # A clean-clone failure carries no category: nothing
                        # chose it, the declaration names the route outright.
                        routed_category, routed_stage = None, destination
                        index = stage_names.index(destination)
                        continue
                    _clean_clone_passed(
                        run_dir, name, artifact, clean.duration_seconds
                    )
                    if state.unshadowed_suite_failure:
                        # A check that ran and exited zero is put to the same
                        # rule a green declared run is put to, on the same
                        # terms: it supersedes the outstanding failure only
                        # where its recorded scope is no narrower than the
                        # failure's, decided by suite_run_shadows over the
                        # recorded scopes and never by recency. A check that
                        # did not run escalated above and clears nothing.
                        # Without this a run resumed past the declaring stage
                        # could pass every check and still be refused at the
                        # end of the workflow, because the declared-run block
                        # is the only other place the field is cleared and a
                        # resume entering here never reaches it. Decided here,
                        # where the exit code is read, rather than inside
                        # clean_clone_check — mirroring the declared run, whose
                        # check does not touch the field either.
                        outstanding = state.unshadowed_suite_failure
                        if suite_run_shadows(
                            clean.scope, outstanding.get("scope", [])
                        ):
                            state.unshadowed_suite_failure = {}
                            # Saved at the decision, so a crash between here
                            # and the run's end cannot leave a failure recorded
                            # that a shadowing pass has superseded.
                            save_state(run_dir, state)
                            append_event(
                                run_dir,
                                suite_failure_shadowed_by_clean_clone_note(
                                    outstanding
                                ),
                                kind="note",
                                stage=name,
                            )
                        else:
                            # The same carry-forward the declared block
                            # appends: the failure stands, pointed at by its
                            # own retained pair, for the end-of-run refusal.
                            append_event(
                                run_dir,
                                suite_failure_carried_forward_note(outstanding),
                                kind="note",
                                stage=name,
                            )

                # A finding about the story's own change whose fix is not to
                # the words alone has somewhere to go, and it goes there only
                # once the tree is known clean-clone-good. This block sits
                # below the clean-clone check deliberately, where the
                # correction block sits above it: a repair may change
                # behaviour, so it is built on a verified tree and the
                # verification it returns to runs the check again on the
                # repaired one. A verdict whose clean-clone check failed has
                # already taken the retry route above and never reaches here.
                # Both names come off the declaration, so removing the key
                # disables the whole mechanism with no change here.
                repair = stage.get("repair_pass") or {}
                repair_artifact = repair.get("result")
                repairable = verdict.get("repairable_findings") or []
                if repair_artifact and repairable:
                    routing = repair_destination(repairable, routes, stage_names)
                    if routing.unknown:
                        # Above the budget comparison, for the reason the
                        # correction block's escalation is: a finding naming a
                        # category the workflow does not define is a bug in
                        # what the verifier produced. Through _escalate, so
                        # retry_count is untouched, and above archive_attempt,
                        # so no attempts/attempt-N/ is written.
                        named = ", ".join(f"'{one}'" for one in routing.unknown)
                        return _escalate(
                            run_dir,
                            state,
                            f"a repairable finding named {named}, which is "
                            f"not a retry category {workflow['name']} defines; "
                            f"it defines: {declared}",
                            target_root=target_root,
                            harness_root=harness_root,
                            duration_seconds=elapsed(),
                            verifier_outcome=verdict.get("status"),
                            retry_decision="escalate",
                            retry_reason=(
                                f"a repairable finding named the unknown "
                                f"category {named}"
                            ),
                        )
                    repair_budget = repair["budget"]
                    if state.repair_pass_count >= repair_budget:
                        _repair_pass_recorded(
                            run_dir, name, repairable, repair_budget
                        )
                    else:
                        state.repair_pass_count += 1
                        record = {
                            "pass": state.repair_pass_count,
                            "attempt": attempt,
                            "stage": routing.stage,
                            "findings": routing.resolved,
                            "statement": repair_pass_statement(routing.stage),
                        }
                        (
                            run_dir
                            / repair_pass_result_file(
                                repair_artifact, state.repair_pass_count
                            )
                        ).write_text(
                            json.dumps(record, indent=2) + "\n", encoding="utf-8"
                        )
                        # Cleared for the reason the correction block clears
                        # it: the passing verdict that routed this wrote no
                        # guidance, so none is in force for the verification
                        # the pass runs back into.
                        state.guidance_in_force = []
                        save_state(run_dir, state)
                        _repair_pass_routed(
                            run_dir,
                            name,
                            routing.stage,
                            repairable,
                            state.repair_pass_count,
                            repair_budget,
                            routed_slugs,
                        )
                        # Nothing a retry spends is spent: retry_count is
                        # untouched, archive_attempt is not reached, and no
                        # retry-history entry is appended. The pass names no
                        # retry category and no retry destination; what each
                        # stage is told comes off the record just written.
                        routed_category, routed_stage = None, None
                        index = stage_names.index(routing.stage)
                        continue
            elif verdict.get("retry_recommended") and not target:
                # Above the ceiling comparison deliberately: a verdict that
                # cannot be routed is a bug in what the verifier produced, and
                # that is the reason a developer should read, not the budget.
                # Through _escalate, so retry_count is untouched, and above
                # archive_attempt, so no attempts/attempt-N/ is written — the
                # artifacts at the run root already describe the attempt that
                # failed, and nothing is being superseded.
                return _escalate(
                    run_dir,
                    state,
                    f"the verifier recommended a retry without naming a "
                    f"retry_target, so there is no category to route it on; "
                    f"{workflow['name']} defines: {declared}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason="the recommended retry named no retry_target",
                )
            elif verdict.get("retry_recommended") and target not in routes:
                return _escalate(
                    run_dir,
                    state,
                    f"the verifier recommended a retry to '{target}', which is "
                    f"not a retry category {workflow['name']} defines; it "
                    f"defines: {declared}",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason=(
                        f"the recommended retry named the unknown retry_target "
                        f"'{target}'"
                    ),
                    retry_category=target,
                )
            elif state.guidance_in_force and not (
                comparison := compare_guidance_outcomes(
                    state.guidance_in_force, verdict
                )
            ).matched:
                # Placed after the two escalations for an unroutable
                # recommended retry, so how one of those is reported is
                # unchanged, and before the unfinishable_by_retry branch,
                # deliberately: a fast exit paired with guidance that still
                # sanctions partial results is worse than what came before it,
                # because the verifier then escalates confidently on runs that
                # were only stalling because it misdirected them. Misdirection
                # is ruled out first.
                #
                # There is no default and no assumption about what silence
                # meant — the same strictness retry_target is held to. Through
                # _escalate, so retry_count is untouched, and above
                # archive_attempt, so no attempts/attempt-N/ is written.
                reason = guidance_mismatch_reason(comparison, verdict)
                return _escalate(
                    run_dir,
                    state,
                    reason,
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason=reason,
                )
            elif state.guidance_in_force and not comparison.unmet:
                # Every entry of the guidance in force was accounted for and
                # none was reported unmet, and the verdict failed the work
                # anyway. The retry delivered what its guidance asked and
                # verification failed regardless, which indicts the guidance
                # rather than the stage. A defective-guidance finding spends
                # no retry budget: the verifier runs again in place on the
                # self-route budget its own stage already declares, told what
                # the contradiction is, so it can rewrite guidance that does
                # not authorize the outcome it will fail or fail the work on
                # the criterion it actually failed. Routed through the
                # existing self_route decision rather than a new path, so when
                # the budget is already spent the run escalates with the
                # reason self_route already returns — escalation is the
                # fallback without a second escalation path being written for
                # it.
                met = list(state.guidance_in_force)
                reason = (
                    f"the retry guidance in force was met in full and the "
                    f"verifier failed the work anyway, so the guidance "
                    f"sanctioned the outcome it failed; every entry was "
                    f"accounted for and none reported unmet: {'; '.join(met)}"
                )
                decision = self_route(
                    run_dir,
                    state,
                    stage,
                    failure=DEFECTIVE_RETRY_GUIDANCE,
                    reason=reason,
                    # The artifacts the contradiction is between, named off
                    # the stage's own declarations rather than written here.
                    artifacts=sorted(conditional + required),
                    attempt=attempt,
                    entries=met,
                )
                if decision.taken:
                    self_routed = True
                    continue
                return _escalate(
                    run_dir,
                    state,
                    decision.reason,
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason=decision.reason,
                )
            elif verdict.get("unfinishable_by_retry"):
                # The verifier's judgement that retrying cannot finish this,
                # read on the first sighting and above the ceiling comparison
                # deliberately: a verdict saying no number of retries closes
                # the gap must not be weighed against how much budget is left,
                # or the run spends a budget the verifier has just said cannot
                # cover the work. Through _escalate, so retry_count is
                # untouched and the budget is left unspent, and above
                # archive_attempt, so no attempts/attempt-N/ is written — the
                # artifacts at the run root already describe the attempt that
                # just ended, and nothing is being superseded. That is the same
                # shape as the two escalations above, for the same reasons.
                #
                # The recorded reason is the verifier's own text, so the
                # escalation summary and the history entry carry the judgement
                # rather than a coordinator paraphrase of it. The one exception
                # is a verdict that also recommends a retry: it says both that
                # retrying is the answer and that retrying cannot finish, and
                # neither half is silently preferred — the reason names the
                # contradiction and still carries the text.
                judgement = verdict["unfinishable_by_retry"]
                if verdict.get("retry_recommended"):
                    reason = (
                        f"the verifier's verdict contradicts itself: it "
                        f"recommended a retry and also reported that retrying "
                        f"cannot finish this work: {judgement}"
                    )
                else:
                    reason = judgement
                return _escalate(
                    run_dir,
                    state,
                    reason,
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason=reason,
                )
            elif verdict.get("retry_recommended") and state.retry_count < rules["max_retries"]:
                # Archive before the retry begins, while the root artifacts
                # still describe the attempt that just failed. The attempt
                # number is the one the rendered prompts already use, so
                # prompt-implementer-attempt-1.md and attempts/attempt-1/
                # describe one attempt.
                archive_attempt(
                    run_dir, archivable_artifacts(stages), state.retry_count + 1
                )
                # The backward-looking record of the attempt that just failed,
                # appended on the path that actually reroutes and on neither
                # escalation path, which take no retry. Above the increment for
                # the same reason the archive is: state.retry_count + 1 names
                # the attempt that ended, matching attempts/attempt-N/.
                destination = routes[target]["stage"]
                entry = append_retry_record(
                    run_dir,
                    state.retry_count + 1,
                    destination,
                    verdict,
                    artifacts_written_since(run_dir, conditional, artifacts_before),
                )
                # The guidance directing the attempt about to begin, recorded
                # on the state so the check on the next verdict reads a
                # routing input reconstructable from state.json and events.log
                # alone. Built from the same freshness-checked view the
                # retry-history entry was, so an attempt that wrote no
                # guidance clears it rather than inheriting the run root's.
                state.guidance_in_force = guidance_entries(entry.get("guidance"))
                state.retry_count += 1
                save_state(run_dir, state)
                append_event(
                    run_dir,
                    f"verification failed; retry {state.retry_count} of "
                    f"{rules['max_retries']} rerouted to {destination} "
                    f"for {target}"
                    + routed_among(verdict.get("blocking_issues") or [],
                                   routed_slugs),
                    kind="verification-failed",
                    stage=name,
                    artifacts=outputs,
                    duration_seconds=elapsed(),
                    # As on the passing entry: the verifier's own name, so a
                    # failed verification is an observation of the stage too.
                    operation=name,
                    verifier_outcome=verdict.get("status"),
                    retry_decision="retry",
                    retry_reason=(
                        "the verifier recommended a retry and the retry ceiling "
                        "was not reached"
                    ),
                    retry_category=target,
                    retry_stage=destination,
                )
                routed_category, routed_stage = target, destination
                index = stage_names.index(destination)
                continue
            elif verdict.get("retry_recommended"):
                return _escalate(
                    run_dir,
                    state,
                    "verification failed and retries are exhausted",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason=f"the retry ceiling of {rules['max_retries']} was reached",
                )
            else:
                return _escalate(
                    run_dir,
                    state,
                    "verification failed and the verifier did not recommend a retry",
                    target_root=target_root,
                    harness_root=harness_root,
                    duration_seconds=elapsed(),
                    verifier_outcome=verdict.get("status"),
                    retry_decision="escalate",
                    retry_reason="the verifier did not recommend a retry",
                )
        else:
            append_event(
                run_dir,
                f"{name} stage completed",
                kind="stage-completed",
                stage=name,
                artifacts=stage.get("outputs", []),
                duration_seconds=elapsed(),
                # A stage's operation is its own name, which is what the
                # duration log records the stage's observation under.
                operation=name,
            )

        index += 1

    if state.unshadowed_suite_failure:
        # A run must not reach completion carrying a declared suite failure
        # nothing has superseded. The suite-run block records a failure and
        # advances — it attributes nothing and refuses nothing — and the
        # clean-clone block above clears the failure where its check ran and
        # exited zero at a scope no narrower than the failure's. So what
        # arrives here with one outstanding is a run whose verifying stage
        # declares no clean-clone check, or whose check did not run, or whose
        # check passed at a scope narrower than the failure's. This refusal is
        # what stops such a run completing, escalating naming the failure
        # that still stands. It costs no suite run: the verdict is read off
        # state.json. The record and output the reason names are where they
        # stand now, on a resumed run too: the resume repoints the record to
        # follow the pair it moved, so the reason is composed from current
        # paths rather than from the ones the failure was first recorded
        # under.
        outstanding = state.unshadowed_suite_failure
        return _escalate(
            run_dir,
            state,
            (
                f"the workflow ended with a suite failure still outstanding: the "
                f"suite the coordinator ran after {outstanding.get('stage')} "
                f"exited {outstanding.get('exit_code')} and no later run shadowed "
                f"it; its record is {outstanding.get('result_path')} and its "
                f"output is {outstanding.get('output_path')}"
            ),
            target_root=target_root,
            harness_root=harness_root,
            duration_seconds=elapsed(),
        )

    return _complete(
        run_dir, state, reading.parsed, target_root,
        config=config, harness_root=harness_root, stages=stages,
    )
