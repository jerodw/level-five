"""Inspecting what a story changed, once that story's run has completed.

The Inspector is a capability a developer invokes. This module makes it
standing: when a run completes, the coordinator inspects what that story
touched and files briefs for what it finds, so nobody has to remember to ask.

**It adds no judgement.** What a good finding is stays in `prompts/inspector.md`
and the dedupe, the validation, the identity, the brief cap and the filing stay
in `orchestration/inspection.py`. What is here is the run integration: which
files a completed story puts in scope, the file cap that bounds them, the one
invocation, the record and the commit that makes the record durable.

**It may never block, delay or refuse a run.** That is the queue sweep's rule,
inherited for the sweep's reason: a mechanism whose whole purpose is to be
helpful must not become the thing that stops the work. It is expressed the way
the queue's own sweep seam expresses it, as a total function — no return value, no
exception out of any path, and no parameter by which a caller could be told to
stop — so a later reader cannot make it consistent with the refusing pre-flights
around it and delete the guarantee by accident. A configuration it cannot obey
is *named in the record* rather than refused, because a total function has no
way to refuse and inventing one would be inventing the block.

**The scope is the files the run changed plus their siblings.** Changed files
alone cannot reveal duplication or a parity gap, because both live in the file
the story did not change; the containing directories in full re-inspect a
subsystem on every story that touches it, one level and never recursively. The
expansion is computed here, before any agent is invoked, so it is testable and
its cost is known in advance — which is also why the cap is applied to a list
this module built rather than to whatever an invocation happened to read.

**It says it has started, and then says what it did in two sizes.** The
announcement is written before the invocation is made, in the idiom the suite
reruns use, because the inspection begins after the run has declared itself
complete and a silence there reads as a hang. What it did is then one line a
person can take in — the counts, the drop reasons and the dedupe verdict — with
the paths it did not read appended to the run's own log under the configured
logs directory and named from the summary. Nothing bounds how many names go
into that log, because a bound there would be the silent drop the naming exists
against; what bounds them is the file cap the summary reports.

**What it cost is recorded twice, and neither recording enforces anything.**
The figure the invocation reported goes into the cross-run inspection log
beside the mode, the scope size and the three counts, so one read of one file
answers what inspection has cost; and it is appended to that run's own
cost.json beside the stage invocations, because the inspection is part of that
run's life. It is deliberately never added to `state.entry_cost_usd`, which is
the live allowance `max_run_cost_usd` is compared against: this spend happens
after the completion commit, when the run's work is done and committed, and
charging it there could push a completed run over a cap it had already
honoured. The figure is carried and never computed — nothing here reads an
agent log back — and an invocation that reported no cost records that it
reported none rather than recording a zero that would corrupt an average taken
over the corpus later.

**The record is committed.** The run directory is gitignored and reaches no
clone, and the tracked cross-run logs are deliberately summaries, so an
inspection reporting only into events.log would leave no evidence that any
inspection had ever run. The commit stages the record paths *by name* and never
with `git add -A`, so a file the inspection agent changed elsewhere in the
repository is left in the working tree rather than folded into a commit this
module made.

**A finding routed to the story is accounted for after the verdict.** The
pre-stage inspection hands the findings about the story's own change to the
stage that judges it, and the verdict names what it acted on by slug. Every
routed finding the verdict did not name is the remainder, recorded once per
attempt beside the routed and acted-on slugs, and an event says how many were
acted on and how many were held. Declining a finding stays correct; declining
no longer deletes it.

**Nothing an inspection finds during a run reaches the queue until the run
completes.** A run of several attempts inspects once per attempt, and a
defect re-found on a later attempt is the Inspector's second reading of it —
under a slug, a category and a path set rephrased the way a title is — so a
brief filed per attempt was filed once per attempt under keys nothing could
match. The pre-stage inspection therefore writes the findings that are not
about the change to a held artifact beside its findings record instead of
filing them, the accounting after the verdict files nothing and records the
remainder, and when the run completes `file_at_completion` files the final
attempt's held findings and its remainder through the one filing call, between
the completion commit and the sweep so the same sweep drains them. The final
attempt's inspection read the code that shipped, so a run of three attempts
files exactly what a run of one attempt over that same code would have filed.
Two consequences are accepted: a run that escalates and is abandoned files
none of what it held, and the verifier is handed a re-found finding on every
attempt rather than once, because nothing is queued mid-run for the retry's
inspection to drop it against.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import harness_config
import inspection

#: The maximum number of files one post-story inspection may take into scope
#: after the expansion below. Its absence disables the whole mechanism.
MAX_FILES_KEY = "inspect_after_story_max_files"

#: The kind the record is appended under, and which mode this producer is.
#: Which cross-run log the kind reaches — one, and only the one whose
#: declaration names it — is the cross-run history declaration's to say rather
#: than this module's, so no log filename is written here. Both names are
#: `inspection`'s, reached through here rather than respelled, so this mode's
#: record and the broad mode's are one vocabulary: a reader querying the log
#: for either is querying for the value both producers write.
INSPECTION_EVENT = inspection.INSPECTION_EVENT
MODE = inspection.MODE_NARROW

#: The kind the accounting after a verdict is appended under. It is a second
#: record about one attempt's inspection rather than a second inspection, so it
#: has a kind of its own: a reader counting `INSPECTION_EVENT` lines counts
#: inspections, and this line says what became of what one of them routed.
#: Which cross-run log it reaches is the declaration's to say, as above.
ACCOUNTING_EVENT = "inspection-accounted"

#: The kind the filing a run makes at completion is appended under: the one
#: moment anything an inspection found during the run reaches the queue. It is
#: neither an inspection nor an accounting of one, so a reader counting either
#: does not count it; which cross-run log it reaches is the declaration's to
#: say, as above.
FILED_EVENT = "inspection-filed"

#: What a post-story inspection's cost is recorded as in that run's cost.json.
#: It is not an attempt at a stage, so it carries no attempt number of its own
#: and is recorded at attempt 0; the stage name says what the invocation was
#: rather than naming a stage the workflow declares.
COST_STAGE = "inspection"
COST_ATTEMPT = 0

#: What a finding about a declared architecture document is, said once and
#: carried by both framings below. A document the story changed may be among
#: the files listed, and it is inspected as a document: the sentence says what
#: counts and, so a large document in scope does not become a brief per
#: paragraph, what does not.
DOCUMENT_FRAMING = (
    "An architecture document the target declares and the story changed may "
    "be among the files listed. A finding about it is a claim the document "
    "contradicts elsewhere in itself, a claim the code contradicts, or a fact "
    "the document states more than once; a paragraph you would have worded "
    "differently is not a finding."
)

#: The question this mode asks, supplied from here because it is this caller's
#: question. Broad mode's is `inspection.BROAD_FRAMING` and is supplied from
#: there for the same reason.
POST_STORY_FRAMING = (
    "A story has just completed and its work is committed. Ask whether that "
    "change left a defect: whether what it added is wrong, whether it agrees "
    "with the files beside it that it did not change, and whether anything it "
    "touched now contradicts something it did not. The files the story changed "
    "are listed first below and the files beside them follow; the change is "
    "your subject and its neighbours are the evidence you judge it against. "
    + DOCUMENT_FRAMING
)

#: What a brief filed from here carries as its provenance. Payload and never
#: identity, so a finding this mode files and the same finding an l5-inspect
#: run rediscovers land on one key.
ORIGIN = "the change made by {story_id}"

#: The question the pre-stage mode asks. It is the post-story question read
#: forward rather than back: the change is committed to the working tree rather
#: than to the branch, and the stage that reads the answer is still able to act
#: on it.
PRE_STAGE_FRAMING = (
    "A story's work is in the working tree and is about to be judged. Ask "
    "whether that change left a defect: whether what it added is wrong, "
    "whether it agrees with the files beside it that it did not change, and "
    "whether anything it touched now contradicts something it did not. The "
    "files the story changed are listed first below and the files beside them "
    "follow; the change is your subject and its neighbours are the evidence "
    "you judge it against. "
    + DOCUMENT_FRAMING
)

#: The subject the record commit carries. It leads with the harness rather
#: than with the story id and carries no COMPLETION_COMMIT_MARKER, so it
#: matches neither the completion shape `completion_commits` reads nor the
#: escalation shape `_head_escalated` reads nor the pause shape beside it.
COMMIT_SUBJECT = "l5 recorded a post-story inspection of {story_id}"

#: The subject the commit of the completion filing's record carries, on the
#: same terms: it is not the post-story inspection's subject, because a run
#: under a workflow declaring an inspection makes no post-story inspection and
#: a reader searching a branch for one must not find this instead.
FILED_COMMIT_SUBJECT = (
    "l5 recorded the briefs filed at the completion of {story_id}"
)

#: The drop reasons that name something other than a finding the invocation
#: produced, so a count of findings does not count them: an invocation that
#: wrote no artifact at all, and an area suggestion the reader refused — a
#: suggestion reaches no brief and is not a finding whether or not it was
#: usable.
NOT_A_FINDING = (inspection.NO_ARTIFACT, inspection.UNUSABLE_SUGGESTION)


# --------------------------------------------------------------------------
# The bound
# --------------------------------------------------------------------------


def max_files(config: dict):
    """How many files one inspection may take into scope, or why it will not.

    Returns `(cap, problem)`. An *absent* key is neither: the mechanism is off,
    and the caller does nothing at all rather than reporting that it did
    nothing. A value that is not a positive integer is a problem, and the
    problem is named in the record rather than refusing anything — a total
    function cannot refuse, and obeying a default in place of a bound the
    target got wrong would obey a number nobody wrote.
    """
    declared = config.get(MAX_FILES_KEY)
    if declared is None:
        return None, ""
    try:
        cap = int(str(declared))
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return None, (
            f"{MAX_FILES_KEY}: {declared!r} is not a positive integer, so the "
            "post-story inspection was not made"
        )
    return cap, ""


# --------------------------------------------------------------------------
# The expansion
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Expansion:
    """The files one inspection covers, and what was left out of them.

    `changed` is what the run's own stages recorded, in scope and still held by
    the target tree — a file the story created counts whether or not anything
    has staged it, and a path the tree no longer holds does not.
    `siblings` is what the tree holds directly beside those files. They are kept
    apart rather than merged because the cap trims the second before the first.
    `excluded` names every path that was dropped and why, so a scope that is
    smaller than a reader expects says so rather than reading as a repository
    with nothing beside the change.
    """

    changed: tuple[str, ...] = ()
    siblings: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        """Everything in scope, changed files before the files beside them."""
        return self.changed + self.siblings


def _held(target_root: Path) -> tuple[str, ...]:
    """Every path the target tree holds, as repository-relative paths.

    The listing is `--cached --others --exclude-standard`, which is the
    tracked-plus-untracked set the coordinator's own tree comparison already
    uses, filtered to the paths that are actually on disk. Asking the index
    instead — `git ls-files` alone — answers correctly only for a caller that
    runs below a commit of the work: `inspect_after_story` does, and
    `inspect_before_stage` runs in the opposite condition, from the stage loop
    with nothing having staged the tree, where every file the story created is
    untracked. One listing serves both, because after the completion commit the
    cached half already covers everything the post-story caller sees and the
    others half adds nothing to it.

    The existence filter is what keeps a deletion out of the scope: an index
    entry whose file has been removed from the working tree is a path there is
    nothing at to read, and it is excluded on exactly the terms a committed
    deletion is.

    Run through a module-local subprocess call with a fixed argument list, the
    idiom `inspection._tracked` already uses. A repository git cannot answer
    for holds nothing here rather than raising: this module may not raise, and
    an inspection is not the place to discover a broken checkout.
    """
    argv = [
        "git", "-C", str(target_root), "ls-files",
        "--cached", "--others", "--exclude-standard", "-z",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - a fixed argument list
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
    except OSError:
        return ()
    if completed.returncode != 0:
        return ()
    root = Path(target_root)
    held: list[str] = []
    for one in completed.stdout.split("\0"):
        if not one:
            continue
        try:
            if (root / one).exists():
                held.append(one)
        except OSError:
            continue
    return tuple(held)


def containing_directory(path: str) -> str:
    """The directory a repository-relative path sits directly in.

    "" for a path at the repository root, which is the same value the listing
    gives such a path, so the two compare without a special case.
    """
    head, separator, _ = path.rpartition("/")
    return head if separator else ""


def scope_prefixes(config: dict) -> tuple[str, ...]:
    """The directory prefixes an inspection's scope is bounded to.

    `source_dirs` plus `tests_dir`, which is the same pair broad mode covers.
    A target that declares neither is bounded to its whole working tree rather
    than to nothing — the reading `inspection.scopes` already takes, and the
    alternative would silently inspect nothing in every target that has not
    declared a source layout.

    These are the *prefix* half of the scope and not the whole of it: the
    documents the target declares under `architecture_docs` are in scope too,
    as exact paths rather than prefixes, through `declared_documents` beside
    this. They are kept apart because a prefix pulls a directory in and a
    document pulls in itself alone.
    """
    declared = list(config.get(inspection.SOURCE_DIRS_KEY) or [])
    tests_dir = config.get(inspection.TESTS_DIR_KEY)
    if tests_dir:
        declared.append(tests_dir)
    found = tuple(
        one for one in (_prefix(entry) for entry in declared) if one
    )
    return found or ("",)


def declared_documents(config: dict) -> tuple[str, ...]:
    """The architecture documents the target declares, as exact paths.

    Each entry of `architecture_docs` is in scope as itself and nothing more:
    a changed declared document is kept in the changed set, and a file beside
    it in the same directory is a sibling only where it is itself declared —
    the document is inspected as a declared document, not reclassified as
    source, so its directory is not pulled in wholesale. A target declaring
    none computes exactly the scope it computed before the documents were in
    it. The key is fetched under the same literal name every other reader and
    the declared-keys scan use; each entry is matched as one exact path, and
    no directory convention is applied to it here.
    """
    declared = config.get("architecture_docs") or []
    return tuple(
        one for one in (str(entry).strip() for entry in declared) if one
    )


def _prefix(path: str) -> str:
    """One scope key as a prefix, so a path is under it or is not."""
    path = str(path).strip()
    if not path or path.endswith("/"):
        return path
    return path + "/"


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    """Whether a path sits under any of a set of prefixes."""
    return any(prefix == "" or path.startswith(prefix) for prefix in prefixes)


def expansion(target_root: Path, config: dict, harness_root: Path,
              changed) -> Expansion:
    """The files in scope for one post-story inspection.

    The changed paths, plus for each of them the files the target tree holds
    *directly* in its containing directory — one level and not recursively, so
    a populated subdirectory beneath a changed file's directory is not pulled
    in. Bounded to the `source_dirs` and `tests_dir` prefixes plus the exact
    paths the target declares under `architecture_docs`, and with the
    execution rules' blocked prefixes excluded. A declared document is on the
    same terms as a changed source file: changed, it is in the changed set;
    unchanged, it joins the siblings only where it sits directly beside a
    changed file; and a file beside a changed document that is neither under
    a prefix nor itself declared stays out.

    The scope is what the tree *holds* rather than what the index tracks, and
    the difference is the whole of what makes this answer for both callers. A
    file the story created during the stage loop has nothing in the index yet,
    so an index listing would drop it from the scope of the very inspection
    whose subject it is; the tree holds it either way, and after a completion
    commit the tree and the index agree, so the post-story caller sees exactly
    what it saw before.

    A changed path outside both scope keys is dropped and named, and its
    directory is not pulled in: what the harness will not inspect it should not
    inspect the neighbours of either. A changed path the tree does not hold —
    deleted by the story, deleted in the working tree with its index entry
    still standing, or never there at all — contributes its containing
    directory but not itself, which falls out of taking the paths from the
    listing rather than from the record. No model is involved in any of this:
    it is two set operations over one `git ls-files`.
    """
    held = frozenset(_held(target_root))
    prefixes = scope_prefixes(config)
    documents = frozenset(declared_documents(config))
    blocked = tuple(
        _prefix(one) for one in inspection.blocked_prefixes(harness_root) if one
    )

    def in_scope(path: str) -> bool:
        # A declared document is an exact-path entry beside the prefixes, so
        # it holds for the document and not for its directory; the blocked
        # exclusion applies after it exactly as it applies to a prefix.
        return ((_under(path, prefixes) or path in documents)
                and not _under(path, blocked))

    kept: list[str] = []
    directories: list[str] = []
    excluded: list[str] = []
    for path in sorted(set(changed)):
        if not in_scope(path):
            excluded.append(f"{path}: outside the inspected scope")
            continue
        directory = containing_directory(path)
        if directory not in directories:
            directories.append(directory)
        if path in held:
            kept.append(path)
        else:
            # Deleted by the story, deleted in the working tree, ignored, or
            # never there at all. Its directory is in scope because what sits
            # beside a removal is exactly what a removal can have broken; the
            # path itself is not, because there is nothing there to read.
            # The wording says only that the listing does not hold the path,
            # because asserting a removal would be false of a path that never
            # existed and asserting that the repository stopped tracking it
            # would be false of one git ignores — which is on disk.
            excluded.append(
                f"{path}: the tree listing this scope is taken from "
                "does not hold it")

    beside = sorted(
        path for path in held
        if containing_directory(path) in directories
        and path not in kept
        and in_scope(path)
    )
    return Expansion(
        changed=tuple(kept),
        siblings=tuple(beside),
        excluded=tuple(excluded),
    )


def cap_paths(found: Expansion, cap: int):
    """The scope trimmed to the cap, and what the cap left out.

    Changed files are ordered before the files beside them, so the cap trims
    siblings before it trims what the story touched. Where the changed files
    alone exceed the cap they are trimmed too, on the same terms and named the
    same way: a cap that silently kept the whole change would be a cap on
    nothing in the case that most needs bounding.
    """
    paths = found.paths
    return paths[:cap], paths[cap:]


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------


def _say(run_dir: Path, message: str, *, findings: int | None = None,
         filed: int | None = None, dropped: int | None = None,
         cost_usd: float | None = None, scope_files: int | None = None,
         invocations: int | None = None,
         dedupe_ran: bool | None = None,
         inspection_made: bool | None = None,
         kind: str = INSPECTION_EVENT,
         routed: int | None = None,
         acted_on: int | None = None,
         duration_seconds: float | None = None) -> str:
    """Append the inspection's record through the coordinator's shared append.

    Returns "" where the append returned, and the exception's text where the
    guard below swallowed one. The append writes events.log and the run's
    history before the cross-run projection, so a history directory that
    cannot be written raises out of it with the line already in events.log
    and no segment file behind it; the text is what `commit_record` is handed
    as the cause when it finds no record to stage, so a record that was never
    written is reported as not written with the reason, rather than as
    written and untracked.

    Every value goes through that one call, so events.log, the run's structured
    history and the cross-run log stay one write path and not three. The cost,
    the scope size and the invocation count are passed beside the three counts
    and are omitted when absent on exactly the terms those three already are —
    which is how an invocation that reported no cost records that it reported
    none rather than recording a zero.

    `dedupe_ran` is not on those terms and is passed by every inspection call:
    it is a boolean, so absence would mean either false or a record written
    before the field existed, and a record that cannot say which is a record
    nobody can query for the runs that inspected without dedupe.

    `inspection_made` is on exactly those terms and for exactly that reason,
    and is likewise passed by every inspection call, including the ones that
    made no invocation at all and so pass false. What a false records is the
    case this record could not previously be asked about: an inspection whose
    invocations produced no findings artifact recorded zero findings, and zero
    findings is what an inspection that looked and found nothing records.
    Neither boolean is passed by the accounting or the filing at completion,
    which are not inspections and ask neither question.

    `kind` defaults to the inspection's own event and is overridden by the two
    other records this module writes: the accounting after the verdict, which
    carries `routed` and `acted_on` and no cost, no invocation count and no
    dedupe verdict — it made no invocation and asked no query — and the filing
    at completion under FILED_EVENT, which carries `filed` and `dropped` and
    nothing else.

    Imported inside the body, the idiom the queue module already uses for its
    own coordinator import: the coordinator imports this module, and a
    module-scope import would close the cycle. Guarded for the reason
    everything here is guarded — an inspection that could not report is still
    an inspection that must not stop a run.
    """
    try:
        from story_coordinator import INSPECTION_OPERATION, append_event

        # The duration and the operation name travel together, or not at all:
        # an inspection record carrying a duration is the observation the
        # cross-run duration log records, and one carrying none — a
        # configuration it could not obey, nothing in scope — is not.
        timing = ({"duration_seconds": duration_seconds,
                   "operation": INSPECTION_OPERATION}
                  if duration_seconds is not None else {})
        append_event(
            Path(run_dir), message, kind=kind,
            findings=findings, filed=filed, dropped=dropped,
            mode=MODE if kind == INSPECTION_EVENT else None,
            cost_usd=cost_usd, scope_files=scope_files,
            invocations=invocations, dedupe_ran=dedupe_ran,
            inspection_made=inspection_made,
            routed=routed, acted_on=acted_on,
            **timing,
        )
    except Exception as error:  # noqa: BLE001 - reporting may not become the failure
        return str(error) or error.__class__.__name__
    return ""


def _announce(run_dir: Path, story_id: str, scope_files: int, *,
              retrying: bool = False) -> None:
    """Say the inspection has started and what the wait is worth.

    A run says "story completed" and then goes quiet for about as long as a
    stage, which is the one moment a developer has been told they may stop
    watching — so the silence reads as a hang rather than as work. This is the
    idiom the suite reruns already use, reached through the coordinator's own
    announcer so the announcement convention has one home and this module
    spells no event kind of its own.

    `retrying` says this is the second invocation for one inspection, made
    because the first produced no findings artifact. A retry that said nothing
    would be a second silence of a stage's length where a reader has already
    been told the first one is the inspection — the same reading-as-a-hang this
    announcement exists against, met twice. It goes through the same announcer,
    which words it: a second announcer would be a second place to keep the
    wording in step with.

    Imported inside the body and guarded for the reasons `_say` and
    `record_cost` are: the coordinator imports this module, and an announcement
    that cannot be written costs the announcement and never the run. That guard
    is also what makes this safe to hand `inspect_scope` as its announcing
    seam: the seam calls what it is given and guards nothing itself, so the
    guarantee that announcing cannot stop an inspection is stated once, here.
    """
    try:
        from story_coordinator import inspection_started

        inspection_started(Path(run_dir), story_id, scope_files,
                           retrying=retrying)
    except Exception:  # noqa: BLE001 - announcing may not become the failure
        pass


def detail_log(target_root: Path, config: dict, story_id: str) -> Path:
    """The run's own log, where this inspection's detail is appended.

    The same derivation the run that created the file made, reached through
    `harness_config` rather than spelled here: two spellings of one path are
    two answers about which file that is, one of which would append somewhere
    nobody reads.
    """
    return harness_config.run_log_path(Path(target_root), config, story_id)


def _relative(path: Path, target_root: Path) -> str:
    """The log's path as a reader of the repository would name it."""
    try:
        return str(Path(path).relative_to(Path(target_root)))
    except ValueError:
        return str(path)


def write_detail(target_root: Path, config: dict, story_id: str,
                 trimmed, excluded) -> None:
    """Append the paths this inspection did not read to the run's own log.

    The naming exists so that a trimmed path stays recoverable rather than
    being silently dropped, and nothing bounds how many are written here,
    because a bound would be exactly that silent drop. What this changes is
    where the names live: the summary line says how many and which log holds
    them, and the log holds them in full.

    Written at the moment each fact is known rather than at the end of the run,
    which is the shape a watcher reads — a log being appended to while work
    happens — in the separator-and-lines form this file already carries from
    the agent stream, with the directory created the way the agent runner's own
    append creates it.

    Guarded whole: a log that cannot be written costs the detail and never the
    run, which is the rule every other call this module makes is held to.
    """
    if not trimmed and not excluded:
        return
    try:
        path = detail_log(target_root, config, story_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"\n===== post-story inspection: {story_id} =====\n"]
        for one in trimmed:
            lines.append(f"trimmed to the file cap: {one}\n")
        for one in excluded:
            lines.append(f"left out of scope: {one}\n")
        with open(path, "a", encoding="utf-8") as log:
            log.write("".join(lines))
    except Exception:  # noqa: BLE001 - the detail may not become the failure
        pass


def _note(run_dir: Path, message: str) -> None:
    """Say one thing in the run's events.log, and only there.

    It carries no kind of its own, so it is a note: it reaches events.log and
    the run's structured history, and it reaches the cross-run inspection log
    not at all — that log holds one record per inspection, and a second entry
    carrying this kind would be a second inspection as far as anything reading
    it is concerned. What the durable record says about dedupe is the
    `dedupe_ran` field on the one line the inspection writes.

    Guarded and imported inside the body for the reasons `_say` beside it is.
    """
    try:
        from story_coordinator import append_event

        append_event(Path(run_dir), message)
    except Exception:  # noqa: BLE001 - reporting may not become the failure
        pass


def record_cost(run_dir: Path, cost: float | None) -> None:
    """Append what the inspection cost to that run's cost.json, and only there.

    The inspection is part of that run's life, so its spend belongs in the run's
    own record beside the stage invocations. It is deliberately **not** added to
    `state.entry_cost_usd`: that is the live allowance the run ceiling is
    compared against, and this spend happened after the completion commit, when
    the run's work is done and committed. Charging it there could push a
    completed run over a cap it had already honoured, for money spent after the
    thing the cap protects. Recording and enforcing are different jobs, and this
    is the recording one.

    The entry index is the one the run records for the entry now running, read
    off state.json, so the entry a reader sees beside the stage invocations is
    the entry that made them. An invocation that reported no cost adds no entry
    at all, rather than an entry of zero.

    Guarded like everything here: a cost that cannot be recorded costs the
    record and nothing else, and never the run.
    """
    if cost is None:
        return
    try:
        from story_coordinator import append_cost_record, load_state

        state = load_state(Path(run_dir))
        append_cost_record(
            Path(run_dir),
            stage=COST_STAGE,
            entry=state.resume_count if state is not None else 0,
            attempt=COST_ATTEMPT,
            cost=cost,
        )
    except Exception:  # noqa: BLE001 - recording may not become the failure
        pass


def record_paths(target_root: Path, config: dict,
                 story_id: str) -> tuple[str, ...]:
    """The repository-relative record paths this inspection's commit stages.

    Asked of the same projection the append took, the shape
    `plan_mandate._logs_holding` established: a declaration that stops routing
    this kind stops staging the file, with no edit here.

    The derivation is `inspection.record_paths`, reached through here rather
    than copied, because both modes write a record of the same kind and two
    derivations of which logs that kind reaches are two answers that can
    disagree. The story is named because since story-165 a run's record goes
    to that story's own segment of the history, and what is staged is what
    was written. The story is required rather than defaulted, so an omission
    fails at the call site rather than staging nothing and saying nothing.

    It raises where the derivation raises: `commit_record` guards its whole
    body, and the exception's text then reaches the detail it notes, where a
    guard here would have turned the raise into no paths and no cause.
    """
    return inspection.record_paths(target_root, config, story_id=story_id)


def _segment_holds_record(target_root: Path, config: dict,
                          story_id: str) -> bool:
    """Whether a history segment file holds this inspection's record.

    The question `commit_record` decides by before it stages, asked by the
    pre-stage inspection before it notes a record as not written. Guarded,
    because `record_paths` raises and a raise here would fall to the entry
    point's guard, which overwrites the held artifact with an empty one: a
    raise counts as no path held, so the note is still written with the
    swallowed text as its cause.
    """
    try:
        return bool(record_paths(target_root, config, story_id))
    except Exception:  # noqa: BLE001 - a raise is no path held
        return False


#: What `commit_record` gives as the cause of a record it found no segment
#: file for, where the append that should have written it swallowed nothing.
NO_SEGMENT_DETAIL = "no history segment holds the record"


def commit_record(run_dir: Path, target_root: Path, config: dict,
                  story_id: str,
                  subject: str = COMMIT_SUBJECT, *,
                  write_problem: str = "") -> inspection.Recorded:
    """Commit the record this inspection wrote, and say when it did not.

    It establishes first whether there is a record to commit, and only then
    stages one. Where the history declaration routes the inspection kind to
    no log, the record was not written, in the words `inspection.record` uses
    for that. Where the declaration routes it somewhere and no segment file
    holds it, the record was not written either: the detail begins "the
    record could not be written" and carries the cause — `write_problem`,
    which is what `_say` swallowed when the append raised, or a fixed phrase
    saying no history segment holds the record where nothing was swallowed.
    The segment files decide and the swallowed text is only the cause they
    are reported with: the append projects the record before it prints, so
    a raise out of it after the projection leaves the record on disk, and a
    record on disk is staged whatever `_say` returned.

    Otherwise the commit is `inspection.commit_record_paths`, the one commit
    both modes make: the paths are staged **by name** and never with `git add
    -A`, so an edit the inspection agent made elsewhere in the repository is
    left in the working tree rather than folded into this commit; both git
    return codes are checked; and a commit that fails unstages exactly what
    it staged, so the tree is left as the commit found it. A target whose
    ignore rules keep the history out of version control is told no record
    path is tracked, which that function asks of git rather than inferring,
    and gains no commit. The subject carries no completion marker and matches
    neither the completion, escalation nor pause shape, so nothing that reads
    a branch for one of those reads this.

    Whenever the record was not committed, one note goes into the run's
    events.log through `_note`, in the two shapes `scripts/l5-inspect`
    prints: "written but NOT committed: <detail>" for a record that exists
    and did not land, and "NOT committed, because it was not written:
    <detail>" for one that never existed. events.log already carries the
    record line the inspection wrote, so a commit that landed adds nothing
    there.

    Guarded whole, because this producer may not raise: a raise anywhere in
    it becomes a not-committed result carrying the exception's text, noted
    the same way — as not written where the record had not yet been
    established, and as written but not committed where it had. Nothing
    retries, amends, resets past the staged paths or pushes.

    `subject` is the post-story inspection's by default; the completion
    filing passes its own, so the two records a branch can carry are told
    apart by what each commit says about itself.
    """
    established = False
    try:
        if not inspection.routed_logs():
            recorded = inspection.Recorded(detail=inspection.NO_LOG_DETAIL)
        else:
            paths = record_paths(target_root, config, story_id)
            if not paths:
                recorded = inspection.Recorded(
                    detail=f"the record could not be written: "
                           f"{write_problem or NO_SEGMENT_DETAIL}")
            else:
                established = True
                recorded = inspection.commit_record_paths(
                    target_root, paths, subject.format(story_id=story_id)
                )
    except Exception as error:  # noqa: BLE001 - the totality is the guarantee
        if established:
            recorded = inspection.Recorded(
                written=True, committed=False,
                detail=f"the record could not be committed: {error}",
            )
        else:
            recorded = inspection.Recorded(
                detail=f"whether the record was written could not be "
                       f"established: {error}",
            )
    if recorded.committed:
        return recorded
    if recorded.written:
        _note(run_dir, f"inspection record of {story_id} written but NOT "
                       f"committed: {recorded.detail}")
    else:
        _note(run_dir, f"inspection record of {story_id} NOT committed, "
                       f"because it was not written: {recorded.detail}")
    return recorded


# --------------------------------------------------------------------------
# The artifact the stage reads
# --------------------------------------------------------------------------


def findings_artifact_file(artifact: str, attempt: int) -> str:
    """The declared artifact name keyed by the attempt that wrote it.

    The idiom `correction_pass_result_file` and `self_route_result_file`
    already establish, and it is keyed for their reason turned to this
    mechanism's: an attempt is inspected once, and a retry means the code
    changed, so the two readings must not land on one name. The presence of
    this file is also the whole of the once-per-attempt rule — a re-entry into
    the declaring stage within one attempt finds it and renders it rather than
    inspecting again — so nothing counts invocations and no state field is
    added for it.
    """
    stem, _, suffix = str(artifact).rpartition(".")
    if not stem:
        return f"{artifact}-{attempt}"
    return f"{stem}-{attempt}.{suffix}"


def accounting_artifact_file(artifact: str, attempt: int) -> str:
    """The name the accounting of one attempt's routed findings is written under.

    Keyed by the attempt for the reason the findings artifact is, and the
    presence of this file is the whole of the accounted-once rule: the second
    verdict of one attempt — the one a correction pass returns to — finds it
    and accounts nothing, and a retry, whose findings artifact is its own,
    accounts under the next attempt's name.

    The declared name is *prefixed* rather than suffixed, so this file is not
    matched by the findings artifact's own attempt-wildcarded glob and a reader
    listing an attempt's findings records does not count its accounting among
    them.
    """
    return f"accounting-of-{findings_artifact_file(artifact, attempt)}"


def held_artifact_file(artifact: str, attempt: int) -> str:
    """The name one attempt's held findings are written under.

    The findings that are *not* about the story's own change — what the
    post-story mode filed as briefs — held beside the attempt's findings
    record rather than filed, because nothing an inspection finds during a run
    reaches the queue until the run completes. Keyed by the attempt for the
    reason the findings artifact is, and prefixed the way the accounting's
    name is, so the findings artifact's own attempt-wildcarded glob does not
    match it.
    """
    return f"held-from-{findings_artifact_file(artifact, attempt)}"


def write_held(run_dir: Path, artifact: str, attempt: int, story_id: str,
               findings=(), area_suggestions=(),
               refused_area_suggestions=()) -> None:
    """Write the findings the inspection held for the run's completion.

    As the Inspector wrote them, under the findings record's shape, so the
    filing at completion files exactly what a filing at inspection time would
    have filed from the same reading. Written on every path on which the
    inspection ran, empty or not: the highest attempt holding one is the
    final attempt whose inspection read the tree, and an empty one on attempt
    two is what supersedes a full one on attempt one.

    The Inspector's area suggestions travel with the findings, because the
    line saying a filed brief named no area is written where the brief is
    filed, and that is now the run's completion. So do the suggestions the
    reader refused, as the detail of each drop under UNUSABLE_SUGGESTION: a
    refused suggestion is still evidence that the target declares an area
    vocabulary, and the filing at completion can say which filed brief named
    none only if that evidence reaches it. They reach no brief, no payload
    and no queue entry from here, exactly as they reached none at inspection.

    Guarded like everything else here.
    """
    try:
        path = Path(run_dir) / held_artifact_file(artifact, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "story_id": story_id,
                    "attempt": attempt,
                    "findings": list(findings),
                    "area_suggestions": [
                        {"slug": one.slug, "concerns": one.concerns}
                        for one in area_suggestions
                    ],
                    "refused_area_suggestions": [
                        str(one) for one in refused_area_suggestions
                    ],
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - the totality is the guarantee
        pass


def write_findings(run_dir: Path, artifact: str, attempt: int, story_id: str,
                   *, ran: bool, reason: str = "", findings=()) -> None:
    """Write what the inspection found about the story's own change.

    It is written on **every** path the pre-stage inspection takes, including
    the paths on which no invocation was made, and that is the point: a stage
    handed nothing cannot tell an inspection that found no defect in the change
    from one that could not be made, and reading a silence as agreement is the
    failure this record exists against. `ran` says which happened and `reason`
    says why where it did not.

    Guarded like everything else here. A record that cannot be written costs
    the record: the stage is then rendered the absence, and its prompt says
    what an absence means.
    """
    try:
        path = Path(run_dir) / findings_artifact_file(artifact, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "story_id": story_id,
                    "attempt": attempt,
                    "ran": bool(ran),
                    "reason": reason,
                    "findings": list(findings),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - the totality is the guarantee
        pass


def about_the_change(found, changed) -> tuple[list, list]:
    """Split what an invocation found into the story's own and the backlog's.

    Returns `(own, others)`. A finding is the story's own when any of its bare
    paths names a file in the expansion's `changed` set — what the run's stages
    recorded they touched — and the backlog's when none of them does. A finding
    naming both a changed file and a file beside it is the story's own, because
    a finding that touches the change at all is a review comment on it.

    The attribution is **file-level**, deliberately: a finding about
    long-standing code that happens to live in a file the story touched is
    treated as the story's. That over-capture is the price of deciding from the
    changed set the harness already records, and it is taken knowingly — the
    alternative is line-level attribution, which nothing here has and which the
    changed-files record cannot supply.

    Bare paths, because a finding may cite a line and the changed set never
    does; `inspection.bare_paths` is the same derivation a brief's identity is
    built from, reached through there rather than respelled.
    """
    names = frozenset(changed)
    own: list = []
    others: list = []
    for one in found:
        try:
            paths = frozenset(inspection.bare_paths(one.finding))
        except Exception:  # noqa: BLE001 - a malformed finding is the backlog's
            paths = frozenset()
        (own if names & paths else others).append(one)
    return own, others


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------


def _counts(report) -> tuple[int, int, int]:
    """What the invocation produced, what was filed, and what was dropped.

    A drop that names something other than a finding — an invocation that wrote
    no artifact at all, or an area suggestion the reader refused — is counted
    as dropped but not as a finding, because it is not one.
    """
    dropped = len(report.dropped)
    findings = len(report.filed) + sum(
        1 for one in report.dropped if one.reason not in NOT_A_FINDING
    )
    return findings, len(report.filed), dropped


def _drops_by_reason(dropped) -> dict:
    """Each reason the drops carry mapped to how many carried it, in the
    order each reason was first seen.

    The one derivation both lines that name drop reasons — the inspection's
    summary and the completion filing's — count from, so neither holds a
    list of reasons of its own.
    """
    reasons: dict = {}
    for one in dropped:
        reasons[one.reason] = reasons.get(one.reason, 0) + 1
    return reasons


def _summary(label: str, report, excluded, trimmed, log: str, *,
             to_the_story: int = 0, held: int = 0) -> str:
    """One line saying what the inspection did, for the run's own events.log.

    Every way a finding was dropped is named with how many went that way, on
    the no-silent-bound rule the rest of this mechanism already follows: a
    count with no cause reads as a change with nothing wrong in it.

    A *path* left out is still named rather than counted, and nothing bounds
    how many are named, because a bound would be exactly the silent drop the
    naming exists against. What this line no longer carries is the names
    themselves: they live in the run's own log under the configured logs
    directory, and this says how many there are and which log holds them. The
    line was simultaneously the completion notice and the whole report — on one
    run it named about 130 files — and it is the wrong size for either job,
    while a log being appended to as each fact becomes known is the shape a
    watcher reads. The events log stays what it is: a short account of what the
    run decided.

    `to_the_story` is how many findings were about the files the story itself
    changed and so were routed to the stage rather than filed. It is counted as
    a finding and reported on its own terms, because a reader comparing the
    findings count against the filed count would otherwise read the difference
    as a silent drop — which is the one thing every count on this line exists
    to make impossible. The line says they were *routed* and nothing more,
    because at the moment it is written nothing has been decided about them;
    what the verdict acted on, and what was held because it did not, is said
    after the verdict by `account_after_verdict`.

    `held` is how many findings were about files beside the change and so
    were held for the run's completion rather than filed, on the same terms:
    counted as findings, and said in words true when the line is written —
    nothing is filed until the run completes, and what was filed is said then
    by `file_at_completion`.

    Where no invocation produced a findings artifact the line says the
    inspection could not be made, and why, in place of the tally. A tally
    there reports zero findings, which is what an inspection that looked and
    found nothing reports — and the two being indistinguishable in the run's
    own log is half of what let a dead reviewer's findings be lost. The count
    is not merely zero but absent: there is nothing to count, because nothing
    was read. Whether it was made is `inspection.was_made`, the same predicate
    the findings record and the cross-run boolean are decided by, so the three
    statements this run makes about one inspection cannot disagree.

    The reasons are the ones the drops themselves carry, each named once with
    its count in the order it was first seen, so the vocabulary of reasons is
    stated once, in the module that defines it: a reason the inspection
    module defines and no list here names is counted in the total *and*
    named, where a hand-written list would have counted it and named it
    nowhere. Nothing here names a reason constant.
    """
    findings, filed, dropped = _counts(report)
    findings += to_the_story + held
    if not inspection.was_made(report.dropped):
        # A tally here would report the inspection as having found nothing,
        # which is the reading this whole mechanism exists to stop: an
        # invocation produced no report at all, twice, so there is no count of
        # findings to state and stating one says the reviewer looked. The
        # clauses below still follow, because a scope was still computed and
        # trimmed and a query was still asked, and dropping them would lose
        # facts that are true of the attempt.
        line = (f"{label}: the inspection could not be made: "
                f"{inspection.not_made_detail(report.dropped)}")
    else:
        line = (
            f"{label}: {findings} finding(s), "
            f"{filed} filed, {dropped} dropped"
        )
    if to_the_story:
        # Worded for the moment it is written: the findings have been handed
        # to the stage and nothing has yet been decided about them. What the
        # verdict does with them is said afterwards, by the accounting.
        line += f"; routed to this story: {to_the_story}"
    if held:
        line += f"; held for the run's completion: {held}"
    for reason, count in _drops_by_reason(report.dropped).items():
        line += f"; {reason}: {count}"
    if not report.dedupe_ran:
        line += "; dedupe did not run"
    if trimmed or excluded:
        line += (
            f"; trimmed to the file cap: {len(trimmed)}; left out of scope: "
            f"{len(excluded)}; both named in {log}"
        )
    return line


# --------------------------------------------------------------------------
# What both entry points share
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Preparation:
    """Everything an inspection needs before an agent is invoked, or why not.

    Both entry points reach an invocation through this, so the bound
    resolution, the expansion, the cap and the exclusions are one derivation
    rather than one per mode — which is what makes "the scope the pre-stage
    inspection covers" and "the scope the post-story inspection covers" the
    same sentence rather than two that agree today.

    `problem` non-empty is a reason there will be no invocation, worded for the
    record. `off` is the narrower case of the mechanism being switched off
    entirely, which is not a problem and is reported nowhere.
    """

    paths: tuple = ()
    trimmed: tuple = ()
    excluded: tuple = ()
    found: Expansion = Expansion()
    bound: object = None
    problem: str = ""
    off: bool = False


def _prepare(run_dir: Path, target_root: Path, config: dict,
             harness_root: Path, story_id: str, stages) -> _Preparation:
    """Resolve the bound, the scope and the cap, or say why there is none."""
    cap, problem = max_files(config)
    if cap is None:
        return _Preparation(problem=problem, off=not problem)

    from story_coordinator import recorded_by_all_stages

    changed = recorded_by_all_stages(run_dir, stages)
    found = expansion(target_root, config, harness_root, changed)
    paths, trimmed = cap_paths(found, cap)
    if not paths:
        # The exclusions this path names are written to the log before its
        # record, so no exclusion survives only in a line this path no longer
        # carries. There is nothing trimmed here: the cap was never reached.
        write_detail(target_root, config, story_id, (), found.excluded)
        return _Preparation(
            excluded=found.excluded,
            found=found,
            problem=("nothing the story changed is in an inspected scope, so "
                     "no inspection was made"),
        )

    bound, bound_problem = inspection.bounds(config)
    if bound is None:
        return _Preparation(
            paths=tuple(paths), trimmed=tuple(trimmed),
            excluded=found.excluded, found=found, problem=bound_problem,
        )
    return _Preparation(
        paths=tuple(paths), trimmed=tuple(trimmed),
        excluded=found.excluded, found=found, bound=bound,
    )


def _invoke(prepared: _Preparation, run_dir: Path, target_root: Path,
            config: dict, harness_root: Path, story_id: str, framing: str,
            runner):
    """Make the one invocation, returning the scope and what it produced.

    One invocation whatever mix of source and tests files the story changed:
    the change is one subject, and splitting it by which half of the tree a
    file sits in would ask two agents about one change.
    """
    scope = inspection.Scope(
        path="",
        kind=inspection.CHANGE,
        paths=tuple(prepared.paths),
        origin=ORIGIN.format(story_id=story_id),
        framing=framing,
    )
    if runner is None:
        import agent_runner

        runner = agent_runner.run_agent
    # The detail goes to the log at the point both facts are known — after the
    # cap is applied and before the invocation — rather than at the end, so an
    # inspection's progress is already in the shape a watcher reads.
    write_detail(target_root, config, story_id, prepared.trimmed,
                 prepared.excluded)
    # The announcement is made here, after the scope is computed and the bound
    # resolved and immediately before the invocation, so the gap between it and
    # the summary is the invocation rather than the whole mechanism.
    _announce(run_dir, story_id, len(prepared.paths))
    # Timed around the invocation alone, after the announcement, so the figure
    # the completion record carries is the inspection's own.
    started = time.monotonic()
    result = inspection.inspect_scope(
        scope, target_root, config, harness_root, prepared.bound, (), runner,
        inspection.local_index(target_root, harness_root),
        # The seam the retry announces itself through. Both narrow modes hand
        # one over, so an invocation made again because the first produced no
        # findings artifact is announced in the run's events.log before it is
        # made, the way a stage being re-run is. The callable is guarded where
        # it is defined, so announcing cannot become the thing that stops the
        # inspection.
        announce=lambda: _announce(run_dir, story_id, len(prepared.paths),
                                   retrying=True),
    )
    # Timed around both invocations where two were made: what the duration log
    # records is how long the inspection took, and an inspection that was made
    # twice took that long.
    return scope, result, round(time.monotonic() - started, 3)


def _report(run_dir: Path, target_root: Path, config: dict, story_id: str,
            label: str, prepared: _Preparation, report, result,
            to_the_story: int, held: int = 0,
            duration_seconds: float | None = None) -> str:
    """Say what the inspection found, in the run's own record and the log.

    Shared by both modes, so the notes a reader relies on — a dedupe that could
    not answer, a tracker command that moved under the run, a filed brief that
    named no area — are written on the same terms wherever the inspection ran.
    `held` is the pre-stage mode's count of what it held for the run's
    completion; the post-story mode holds nothing and passes none.
    `duration_seconds` is how long the invocation took, as `_invoke` measured
    it, carried onto the completion record so the cross-run duration log holds
    one observation of the inspection per occurrence.

    Returns what its `_say` returned — "" where the record's append returned,
    and the swallowed exception's text where it did not — so a caller that
    goes on to commit the record can hand `commit_record` the cause of a
    record that was never written.
    """
    findings, filed_count, dropped_count = _counts(report)
    # A failed dedupe gets a line of its own, beside the summary rather than
    # instead of it. As a trailing clause on one long line it stayed true for
    # thirty stories without anybody reading it as the standing failure it was:
    # an inspection that could not ask the tracker may have refiled what is
    # already there, which is a different thing from an inspection that found
    # nothing worth filing, and the two read alike at the end of a summary.
    # Said first, because the summary below it reports what was filed and this
    # is what a reader needs in order to know what that count is worth.
    for one in report.dedupe:
        if not one.ran:
            _note(
                run_dir,
                f"{label}: dedupe did not run for {one.scope}: {one.reason}; "
                f"what was filed may already be filed",
            )
    # Beside that note, and on the same terms: a run that holds a filed-query
    # command the tree no longer names launched its dedupe as a command this
    # tree does not have, so an empty answer means a stale path rather than an
    # empty tracker. Said whether or not dedupe answered, because a query that
    # ran against a stale-but-existing command answered for a tracker the tree
    # no longer points at, which is the same doubt reached by a different road.
    #
    # The key is the query seam's own constant, reached through `inspection`
    # rather than by importing the seam here: which modules reach that seam is
    # a declared set, and noticing a moved command is not reaching it.
    moved = harness_config.moved_command(
        config, target_root, inspection.filed_query.COMMAND_KEY
    )
    if moved is not None:
        _note(
            run_dir,
            f"{label}: {moved.describe()}, so the dedupe query answered for a "
            f"tracker the tree no longer points at; what was filed may already "
            f"be filed",
        )
    # A filed brief that named no area gets a line of its own, before the
    # summary and never as a clause at the end of it. The clause is what a
    # failing dedupe had, and it stayed true for thirty stories without being
    # read as the standing failure it was; an area nobody named is the same
    # shape of fact — the sorting axis a developer works the board by is
    # missing for that brief, and the vocabulary may be missing a line. Said
    # before the summary, because the summary reports what was filed and this
    # says what one of those filings is missing. Where every filed brief named
    # an area, and on every inspection of a target that declares no
    # vocabulary, nothing is said at all.
    for brief, suggestion in report.unnamed_areas:
        line = f"{label}: no area was named for {brief.slug}"
        if suggestion:
            line += f"; the Inspector says it concerns: {suggestion}"
        _note(run_dir, line)
    write_problem = _say(
        run_dir,
        _summary(
            label, report, prepared.excluded, prepared.trimmed,
            _relative(detail_log(target_root, config, story_id), target_root),
            to_the_story=to_the_story, held=held,
        ),
        findings=findings + to_the_story + held, filed=filed_count,
        dropped=dropped_count,
        # A statement about the filed query alone, whatever the local index
        # said: that tier holds only what this machine filed, so reading it does
        # not make dedupe complete.
        dedupe_ran=report.dedupe_ran,
        # Carried from the invocation's own result through the scope result,
        # never re-derived: nothing here reads an agent log back. None where the
        # invocation reported nothing, which is how the record says it was told
        # no figure rather than saying the inspection was free.
        cost_usd=result.cost_usd, scope_files=result.scope_files,
        # What the scope actually made rather than the one this mode always
        # makes: an invocation that produced no findings artifact was made
        # again, and the count beside the summed cost is how a reader of the
        # record afterwards can tell that it was.
        invocations=result.invocations,
        # Whether an invocation delivered, decided by the one predicate the
        # summary line above and the findings record are decided by.
        inspection_made=inspection.was_made(report.dropped),
        duration_seconds=duration_seconds,
    )
    # Beside the stage invocations in that run's own cost.json, and never added
    # to the allowance the ceiling reads. See `record_cost` for why the two are
    # different jobs.
    record_cost(run_dir, result.cost_usd)
    return write_problem


# --------------------------------------------------------------------------
# The entry points
# --------------------------------------------------------------------------


# This entry point must not refuse, and restoring consistency with the
# refusing pre-flights around it would defeat the mechanism. It returns
# nothing, raises on no path, and declares no parameter by which a caller could
# be told to stop — the shape the queue's sweep seam already has, and for its
# reason: a mechanism whose whole purpose is to be helpful must not become the
# thing that stops the work.
def inspect_after_story(run_dir: Path, target_root: Path, config: dict,
                        harness_root: Path, story_id: str, stages,
                        *, runner=None) -> None:
    """Inspect what one completed story changed, and file what is found.

    Called from `_complete`, after the completion commit — so a slow inspection
    cannot delay the durability of the work — and above the completion sweep,
    so briefs it enqueues are filed by that same sweep rather than waiting for
    the next run. Since story-147 it is called there **only where no stage of
    the loaded workflow declares an inspection of its own**: a workflow that
    declares one has already inspected this story's change before that stage
    ran, and inspecting again from here would inspect the same diff twice.

    Every way this can go wrong ends here: the key unset, the key unusable, no
    changed path in scope, an agent that cannot be reached, a filed query that
    fails, an enqueue that drops every item, a record that cannot be appended
    and a commit that cannot be made. None of them has a way to tell the caller
    anything, because there is no value to tell it with.
    """
    label = f"post-story inspection of {story_id}"
    try:
        _inspect_after_story(
            run_dir, target_root, config, harness_root, story_id, stages,
            runner=runner,
        )
    except Exception as error:  # noqa: BLE001 - the totality is the guarantee
        # Nothing above is expected to raise — the inspection module is bounded
        # and the git calls are captured — but "expected" is not the standard
        # this function is held to. Returning on every path means every path,
        # including one nobody has thought of.
        print(
            f"the post-story inspection could not run: {error}", file=sys.stderr
        )
        # An inspection that failed says so where an inspection that succeeded
        # says what it found, so a reader of either record is not left deducing
        # a failure from a silence. Both halves are guarded in their own right,
        # and this one is guarded again: reporting a failure may not become a
        # second one.
        try:
            write_problem = _say(
                run_dir,
                f"{label}: it could not run: {error}",
                findings=0, filed=0, dropped=0,
                scope_files=0, invocations=0,
                # No invocation was made, so no filed query answered for this
                # run. False is the honest reading; the vacuous true an empty
                # scope list would give would say dedupe ran when nothing asked.
                dedupe_ran=False,
                # And no invocation means no findings artifact, so the
                # inspection was not made. Stated by the caller that knows it
                # made none rather than derived from the drops, which record
                # only what an invocation that *was* made failed to deliver.
                inspection_made=False,
            )
            commit_record(run_dir, target_root, config, story_id,
                          write_problem=write_problem)
        except Exception:  # noqa: BLE001 - reporting may not become the failure
            pass


def _inspect_after_story(run_dir: Path, target_root: Path, config: dict,
                         harness_root: Path, story_id: str, stages,
                         *, runner) -> None:
    """The body of the above, so the guard has one thing to guard."""
    label = f"post-story inspection of {story_id}"
    prepared = _prepare(
        run_dir, target_root, config, harness_root, story_id, stages
    )
    if prepared.bound is None:
        if prepared.off:
            # An absent key is the mechanism switched off: no invocation, no
            # event, no commit, and an events.log byte-for-byte what it was.
            return
        # Named in the record rather than refused. The run completes with the
        # status it would have had with the key unset, which is what a total
        # function's answer to a bad bound has to be.
        write_problem = _say(run_dir, f"{label}: {prepared.problem}",
                             findings=0, filed=0, dropped=0,
                             scope_files=0, invocations=0, dedupe_ran=False,
                             inspection_made=False)
        commit_record(run_dir, target_root, config, story_id,
                      write_problem=write_problem)
        return

    scope, result, took = _invoke(
        prepared, run_dir, target_root, config, harness_root, story_id,
        POST_STORY_FRAMING, runner,
    )

    # The cap on briefs, the enqueue and the ways a finding can be dropped on
    # the way to the queue are all `inspection.file_findings`'s, so this
    # producer files under exactly the terms the broad mode does — one brief
    # cap, one queue call, one set of named reasons — rather than under a
    # second copy of them. It is also why nothing here names the queue.
    filed, over = inspection.file_findings(
        target_root, result.found, prepared.bound.max_findings,
        # The same floor, resolved from the same key by the same `bounds` call
        # the broad mode makes. Passed rather than defaulted, because
        # `file_findings` defaults it to no floor so that every construction
        # that predates it is unchanged — a producer with a resolved bound in
        # its hand has to hand it over.
        min_severity=prepared.bound.min_severity,
    )
    report = inspection.Report(
        scopes=(scope,),
        # What the scope made, which is two where the first invocation
        # produced no findings artifact and was made again.
        invocations=result.invocations,
        filed=filed,
        dropped=tuple(result.dropped) + tuple(over),
        dedupe=(result.dedupe,) if result.dedupe is not None else (),
        cost_usd=result.cost_usd,
        scope_files=result.scope_files,
        min_severity=prepared.bound.min_severity,
        area_suggestions=tuple(result.area_suggestions),
    )
    write_problem = _report(run_dir, target_root, config, story_id, label,
                            prepared, report, result, 0,
                            duration_seconds=took)
    commit_record(run_dir, target_root, config, story_id,
                  write_problem=write_problem)


# The same guarantee, made again for the caller that runs inside the stage
# loop rather than after it. It returns nothing, raises on no path, and
# declares no parameter by which a caller could be told to stop — and moving
# the inspection inside the loop a retry re-enters does not weaken that: no
# stage outcome is conditional on it having worked, and there is no value here
# for a coordinator branch to read.
def inspect_before_stage(run_dir: Path, target_root: Path, config: dict,
                         harness_root: Path, story_id: str, stages,
                         *, artifact: str, attempt: int,
                         runner=None) -> None:
    """Inspect this attempt's change, ahead of the stage that judges it.

    Called from the stage loop, immediately before a stage that declares an
    inspection is entered, and once per attempt — the artifact this writes is
    what says the attempt has been inspected, so a self-route or a correction
    pass re-entering that stage within the attempt renders what is already
    there and a retry, which means the code changed, inspects again.

    What it does with what it finds is the whole of the difference from the
    post-story mode. A finding whose subject is among the files the story
    changed is written to `artifact` for the stage to read: a review comment on
    a change belongs to the change that produced it, and the stage about to
    judge that change is the only one still able to act on it. Every other
    finding is written to the held artifact beside the findings record and
    filed once, from the final attempt, by `file_at_completion` when the run
    completes — through the same floor, the same brief cap, the same dedupe
    and the same queue a filing from anywhere else goes through.

    **It supplies and does not decide.** Nothing here fails a run, forces a
    retry, spends a correction pass or is read by any coordinator branch; the
    stage that reads the artifact triages it on the terms it already triages
    findings on.

    It makes **no commit of its own**. The record it writes is carried by the
    run's completion, escalation or pause commit, and an inspection commit at
    HEAD here would sit where `_complete` reads HEAD to recognise a resumed
    escalation.
    """
    label = f"inspection of the change made by {story_id}"
    try:
        _inspect_before_stage(
            run_dir, target_root, config, harness_root, story_id, stages,
            artifact=artifact, attempt=attempt, runner=runner,
        )
    except Exception as error:  # noqa: BLE001 - the totality is the guarantee
        print(
            f"the pre-stage inspection could not run: {error}", file=sys.stderr
        )
        try:
            _say(
                run_dir, f"{label}: it could not run: {error}",
                findings=0, filed=0, dropped=0,
                scope_files=0, invocations=0, dedupe_ran=False,
                inspection_made=False,
            )
            # The stage is told the inspection could not be made rather than
            # being handed nothing: an absence a reader takes for agreement is
            # the failure this record exists against.
            write_findings(
                run_dir, artifact, attempt, story_id, ran=False,
                reason=f"the inspection could not run: {error}",
            )
            # An empty held artifact, so this attempt supersedes the earlier
            # attempt's reading rather than leaving it to be filed at
            # completion against a tree this attempt replaced: an inspection
            # that was attempted and could not run has nothing to file, and
            # the line above already says why.
            write_held(run_dir, artifact, attempt, story_id)
        except Exception:  # noqa: BLE001 - reporting may not become the failure
            pass


def _inspect_before_stage(run_dir: Path, target_root: Path, config: dict,
                          harness_root: Path, story_id: str, stages,
                          *, artifact: str, attempt: int, runner) -> None:
    """The body of the above, so the guard has one thing to guard."""
    label = f"inspection of the change made by {story_id}"
    prepared = _prepare(
        run_dir, target_root, config, harness_root, story_id, stages
    )
    if prepared.bound is None:
        reason = prepared.problem or (
            f"{MAX_FILES_KEY} is not set, so no inspection was made"
        )
        if not prepared.off:
            _say(run_dir, f"{label}: {prepared.problem}",
                 findings=0, filed=0, dropped=0,
                 scope_files=0, invocations=0, dedupe_ran=False,
                 inspection_made=False)
        # Written on this path too, and on the switched-off path as well: the
        # stage reads a statement of why there is nothing rather than an
        # absence it would have to interpret, and the presence of the file is
        # what keeps the once-per-attempt rule one rule rather than two.
        write_findings(run_dir, artifact, attempt, story_id, ran=False,
                       reason=reason)
        return

    scope, result, took = _invoke(
        prepared, run_dir, target_root, config, harness_root, story_id,
        PRE_STAGE_FRAMING, runner,
    )

    own, others = about_the_change(result.found, prepared.found.changed)

    # The findings that are not about this story's own change are *held* for
    # the run's completion rather than filed: nothing an inspection finds
    # during a run reaches the queue until the run completes, because a retry's
    # inspection re-finds a defect under a rephrased key and a brief filed per
    # attempt was one defect filed once per attempt. They are written as the
    # Inspector wrote them, so `file_at_completion` files exactly what a filing
    # here would have filed — through the same call, the same floor, the same
    # cap, the same named drop reasons and the same queue — once, from the
    # final attempt.
    write_held(run_dir, artifact, attempt, story_id,
               findings=[one.finding for one in others],
               area_suggestions=tuple(result.area_suggestions),
               # The suggestions the reader refused travel too, as their drop
               # details: the completion filing's area gate reads a refusal
               # as evidence of a vocabulary exactly as this inspection's
               # own gate does, and without them a run whose every held
               # brief named no area beside one refused suggestion would be
               # reported at completion as a target declaring no areas.
               refused_area_suggestions=[
                   one.detail for one in result.dropped
                   if one.reason == inspection.UNUSABLE_SUGGESTION
               ])
    report = inspection.Report(
        scopes=(scope,),
        # What the scope made, which is two where the first invocation
        # produced no findings artifact and was made again.
        invocations=result.invocations,
        filed=(),
        dropped=tuple(result.dropped),
        dedupe=(result.dedupe,) if result.dedupe is not None else (),
        cost_usd=result.cost_usd,
        scope_files=result.scope_files,
        min_severity=prepared.bound.min_severity,
        area_suggestions=tuple(result.area_suggestions),
    )
    write_problem = _report(run_dir, target_root, config, story_id, label,
                            prepared, report, result, len(own), len(others),
                            duration_seconds=took)
    if write_problem and not _segment_holds_record(target_root, config,
                                                   story_id):
        # This inspection commits no record of its own — the completion
        # commit carries it — so a record that was never written has no
        # commit to be noted against. It is named here instead, so the line
        # missing from the corpus is said on the path every real run takes
        # and not only on the post-story one. The segment files decide, as
        # they do in `commit_record`, and the swallowed text is only the
        # cause: the append projects the record before it prints, so a raise
        # out of it after the projection leaves the record on disk, and a
        # note saying it was not written would name a record the history
        # holds. After `_report`, so the holding above is not cost by it,
        # and before the findings record, which is written last for the same
        # reason.
        _note(run_dir, f"inspection record of {story_id} NOT written: "
                       f"{write_problem}")
    # Last, so a failure to write the record cannot cost the holding above it.
    # The findings are written as the Inspector wrote them: nothing here
    # rewrites, rates or summarises one, because what the stage is being given
    # is the Inspector's reading rather than the coordinator's.
    if inspection.was_made(result.dropped):
        write_findings(
            run_dir, artifact, attempt, story_id, ran=True,
            findings=[one.finding for one in own],
        )
    else:
        # Neither invocation produced a findings artifact, so there is nothing
        # the Inspector said about this change — and `ran` true with an empty
        # findings list is the shape that says it looked and found nothing.
        # This is the shape the exception path above already writes and the
        # one `prompts/story-verifier.md` already tells the verifier how to
        # read: ran false is no inspection was made. Whether it was made is
        # the one predicate the summary line and the cross-run boolean are
        # decided by, so what this record says and what the run said about
        # itself cannot differ.
        write_findings(
            run_dir, artifact, attempt, story_id, ran=False,
            reason=inspection.not_made_detail(result.dropped),
        )


# --------------------------------------------------------------------------
# Accounting for what was routed, after the verdict
# --------------------------------------------------------------------------
#
# A finding routed to the story is written to the artifact above and read by
# the stage that judges the change — and by nothing after the verdict. A
# finding the verifier declines to act on, which its prompt permits because a
# finding can be correct and still be too small to fail a run, was therefore
# neither fixed nor filed. What follows records a declined finding as the
# remainder, and the filing at the run's completion sends the final attempt's
# remainder where a declined finding went before the inspection was moved
# ahead of the verifier: to the backlog, as a brief, through the same call
# every other producer files through. The verifier's judgement is not touched;
# what changes is that declining no longer deletes — and that nothing is filed
# until the run completes, so a defect declined on three attempts is filed
# once rather than three times under three rephrased keys.
#
# Since story-164 the verdict may also say *why* it left a routed finding, in
# an optional `declined_findings` array naming a slug and a reason, and the
# accounting carries that reason beside each left finding — or the fact that
# none was given. The verifier's prompt makes correcting or repairing a routed
# finding the default and declining the exception that is recorded; this is
# where the record lands. It enforces nothing and stops no run: a finding left
# with a reason and one left in silence are both the remainder, both filed at
# completion, and told apart only in the artifact and the event.


#: What a location must name for a routed finding to count as acted on: the
#: finding's slug, as one whole token. Slug matching rather than file matching
#: is deliberate — a correctable finding about a docstring in a file must not
#: count as acting on a defect the Inspector found in the same file, which
#: would be the silent loss this accounting exists to close, one layer down.
_TOKEN_BOUNDARY = re.compile(r"[^A-Za-z0-9_-]+")


def _tokens(location) -> frozenset:
    """The slug-shaped tokens a verdict entry's location carries."""
    if not isinstance(location, str):
        return frozenset()
    return frozenset(one for one in _TOKEN_BOUNDARY.split(location) if one)


def _slug_of(finding) -> str:
    try:
        slug = finding.get("slug")
    except AttributeError:
        return ""
    return slug if isinstance(slug, str) else ""


def verdict_locations(verdict) -> tuple:
    """Every location the verdict names, on a blocking issue, a correctable
    finding or a repairable finding, as the verdict wrote it.

    A repairable finding counts for the reason a correctable one does: it is
    an entry the verdict raised to have something acted on, so a routed
    finding whose slug it names was acted on rather than declined, and is
    not filed as a brief.
    """
    if not isinstance(verdict, dict):
        return ()
    named: list = []
    for key in ("blocking_issues", "correctable_findings", "repairable_findings"):
        for entry in verdict.get(key) or ():
            if isinstance(entry, dict):
                named.append(entry.get("location"))
    return tuple(named)


def names_a_slug(location, slugs) -> bool:
    """Whether one verdict entry's location names any of `slugs`.

    The one rule that decides a finding was acted on, shared by the accounting
    and by the wording of the re-entry events so the two cannot count
    differently. A slug is matched as a whole token of the location — a slug
    that is a prefix of another is not the other — and by nothing else: not the
    file, not the line, not the finding's title.
    """
    return bool(_tokens(location) & frozenset(slugs))


def acted_on_by(findings, verdict, *, refused_slugs=()) -> tuple[list, list]:
    """Split routed findings into what the verdict acted on and the rest.

    Returns `(acted_on, remainder)`, in the order given. A finding is acted on
    when the verdict names its slug in the location of a blocking issue, a
    correctable finding or a repairable finding; every other finding is the
    remainder, including one
    the verdict named by file alone and one carrying no slug at all — a
    finding nothing can name is a finding nothing acted on, and filing it is
    the loss-free reading.

    `refused_slugs` names the routed slugs the coordinator refused a file of:
    a fix spanning files is one repairable finding per file sharing one slug,
    and where a bound refused one of them the accepted sibling still names the
    slug in its location. Such a finding is left, whatever its other files
    did, because the pass repairs only the accepted files and the brief the
    completion filing writes is the record of the rest. Empty by default, so
    a verdict carrying no refusal is split exactly as before the parameter
    existed.

    Pure over its inputs, so it can be held directly the way the partition
    tests hold `about_the_change`.
    """
    locations = verdict_locations(verdict)
    refused = frozenset(slug for slug in refused_slugs if isinstance(slug, str))
    acted: list = []
    remainder: list = []
    for one in findings:
        slug = _slug_of(one)
        if (slug and slug not in refused
                and any(names_a_slug(location, (slug,)) for location in locations)):
            acted.append(one)
        else:
            remainder.append(one)
    return acted, remainder


def declined_reasons(verdict) -> dict:
    """The reason the verdict's `declined_findings` gives for each slug.

    A mapping from slug to reason, read off the verdict's optional
    `declined_findings` array. An entry that is not a mapping, whose slug is
    not a non-empty string, or whose reason is not a non-empty string
    contributes nothing — the schema refuses the last two before a verdict
    reaches here, and reading defensively is what keeps the accounting total
    over a verdict it was handed anyway. The first entry naming a slug wins.
    Nothing routes on the answer: it is read into the accounting artifact and
    worded into its event, and decides nothing about what is filed.
    """
    if not isinstance(verdict, dict):
        return {}
    reasons: dict = {}
    for entry in verdict.get("declined_findings") or ():
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        reason = entry.get("reason")
        if not (isinstance(slug, str) and slug):
            continue
        if not (isinstance(reason, str) and reason.strip()):
            continue
        reasons.setdefault(slug, reason)
    return reasons


def declined_accounting(remainder, reasons) -> list:
    """One entry per finding the verdict left, saying why or that nothing said.

    Each carries the finding's slug and `reason_given`; the reason itself is
    present only where `declined_findings` gave one for that slug, so the
    marker that none was given is the boolean and not an empty string a reader
    could mistake for a reason that happened to be blank. A finding carrying
    no slug cannot have been declined by name and is recorded as declined
    without a reason.
    """
    declined: list = []
    for one in remainder:
        slug = _slug_of(one)
        entry = {"slug": slug, "reason_given": slug in reasons}
        if slug in reasons:
            entry["reason"] = reasons[slug]
        declined.append(entry)
    return declined


def routed_findings_named(locations, routed_slugs) -> int:
    """How many of the routed findings a set of verdict entries names.

    The re-entry events' number, counted over the **routed findings** and not
    over the entries, because a verdict raises an entry per location a repair
    has to reach and so names one finding several times: story-156's first
    repair pass carried seven entries naming three routed findings, and
    counting entries reported six of three — a number that cannot be true of
    the thing it counts. Counting findings is also what makes the claim in
    `names_a_slug` hold, that the events and the accounting cannot count
    differently: `acted_on_by` splits the routed findings, so the event has to
    be over findings for the two to agree.

    Decided by `names_a_slug`, which is that one rule.
    """
    slugs = frozenset(one for one in routed_slugs if isinstance(one, str))
    if not slugs:
        return 0
    return sum(1 for slug in slugs
               if any(names_a_slug(location, {slug}) for location in locations))


@dataclass(frozen=True)
class _Routed:
    """One routed finding on its way to the queue, in the shape
    `inspection.file_findings` files: the finding, and where it came from.
    """

    finding: dict
    scope: object


def _read_findings_record(directory: Path, artifact: str, attempt: int):
    """The attempt's findings artifact, or None where there is none to read.

    The directory is the run root or an earlier entry's, for the reason
    `_attempts_holding` states.
    """
    path = Path(directory) / findings_artifact_file(artifact, attempt)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def write_accounting(run_dir: Path, artifact: str, attempt: int,
                     story_id: str, *, routed, acted_on, remainder,
                     declined=None,
                     filed: int = 0, dropped: int = 0,
                     reason: str = "") -> None:
    """Write the accounting artifact for one attempt.

    It carries the slugs and not only the counts, because the re-entry events
    are worded from it: a correction pass or a retry says how many of the
    findings it carries are among the ones routed, and that is decidable only
    from the slugs. The remainder's slugs are what `file_at_completion` reads
    back to know which of the attempt's routed findings the verdict left
    unanswered. Beside them, `declined` records for each finding the verdict
    left the reason its `declined_findings` gave for the slug, or that none
    was given — see `declined_accounting` for the shape — and the two counts
    say how many were declined each way; a caller passing none records every
    left finding as declined without a reason, which is what a verdict
    carrying no `declined_findings` means. `filed` and `dropped` are zero when
    this is written, because the accounting files nothing: they are here so
    the record and the event say the same thing. Nothing routes on any of it.
    """
    if declined is None:
        declined = declined_accounting(remainder, {})
    with_reason = sum(1 for one in declined if one.get("reason_given"))
    try:
        path = Path(run_dir) / accounting_artifact_file(artifact, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "story_id": story_id,
                    "attempt": attempt,
                    "routed": len(routed),
                    "routed_slugs": [_slug_of(one) for one in routed],
                    "acted_on": len(acted_on),
                    "acted_on_slugs": [_slug_of(one) for one in acted_on],
                    "remainder": len(remainder),
                    "remainder_slugs": [_slug_of(one) for one in remainder],
                    "declined": declined,
                    "declined_with_reason": with_reason,
                    "declined_without_reason": len(declined) - with_reason,
                    "filed": filed,
                    "dropped": dropped,
                    "reason": reason,
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - the totality is the guarantee
        pass


def findings_slugs(run_dir: Path, artifact: str, attempt: int) -> tuple:
    """The slugs of the findings one attempt's inspection routed to the story,
    read off the findings artifact itself.

    For the caller that runs *before* the accounting exists — the coordinator
    deciding a scope extension needs the routed slugs so a refused finding
    can be declined by name — where `routed_slugs` below reads what the
    accounting recorded afterwards. Empty where no inspection ran, and read by
    nothing that routes.
    """
    try:
        record = _read_findings_record(run_dir, artifact, attempt)
        if record is None or not record.get("ran"):
            return ()
        return tuple(
            one["slug"] for one in record.get("findings") or ()
            if isinstance(one, dict) and isinstance(one.get("slug"), str) and one["slug"]
        )
    except Exception:  # noqa: BLE001 - reading a record may not become a failure
        return ()


def _accounting_slugs(directory: Path, artifact: str, attempt: int,
                      key: str) -> tuple:
    """One of the slug lists an attempt's accounting artifact records.

    The two readers below differ only in which list they ask for, so they
    share this one rather than each spelling the read: two readings of one
    record are two answers that can come to disagree about what that record
    says. The directory is the run root or an earlier entry's, for the reason
    `_attempts_holding` states.
    """
    try:
        path = Path(directory) / accounting_artifact_file(artifact, attempt)
        if not path.is_file():
            return ()
        record = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            one for one in record.get(key, ())
            if isinstance(one, str) and one
        )
    except Exception:  # noqa: BLE001 - reading a record may not become a failure
        return ()


def routed_slugs(run_dir: Path, artifact: str, attempt: int) -> tuple:
    """The slugs the accounting artifact records as routed on one attempt.

    Read back off the artifact rather than carried in memory, so what an
    event says about the routed findings and what the run directory records
    are one thing. Empty where no attempt was accounted, and read by nothing
    that routes: the coordinator words two events from it and decides nothing.
    """
    return _accounting_slugs(run_dir, artifact, attempt, "routed_slugs")


def acted_on_slugs(directory: Path, artifact: str, attempt: int) -> tuple:
    """The slugs the accounting artifact records the verdict as having acted
    on — a finding that attempt's verification repaired rather than left.

    Read by the filing at completion, so an earlier entry's copy of a defect
    a later entry of the same run went on to repair is not filed as a brief
    describing code as it no longer is. Empty where no attempt was accounted,
    and read by nothing that routes.
    """
    return _accounting_slugs(directory, artifact, attempt, "acted_on_slugs")


# The same guarantee a third time, for the caller that runs after the verdict
# and before any routing. It returns nothing, raises on no path, and declares
# no parameter by which a caller could be told to stop: a failure inside it —
# an unreadable artifact, a queue that cannot be reached — is printed and
# recorded and changes neither the verdict's routing, the run's status nor its
# exit code.
def account_after_verdict(run_dir: Path, target_root: Path, config: dict,
                          harness_root: Path, story_id: str, *,
                          artifact: str, attempt: int, verdict,
                          refused_slugs=()) -> None:
    """Account for every finding routed to this story, once per attempt.

    Called from the verifier branch after the verdict is archived and before
    the passed/failed routing, only where the stage declares an inspection. It
    reads the attempt's findings artifact, splits the routed findings into the
    ones the verdict acted on — named by slug in the location of a blocking
    issue, a correctable finding or a repairable finding — and the remainder,
    writes the accounting artifact carrying both, and says the split in the
    run's own record. `refused_slugs` names the routed slugs the coordinator
    refused a scope extension of; a finding carrying one is left whatever its
    accepted sibling did, so the refusal the verdict now records as declined
    for it is what the accounting reads and the completion filing files.
    Empty when nothing was refused, and then the split is exactly as before
    the parameter existed. For
    each finding of the remainder it also records the reason the verdict's
    `declined_findings` gave for its slug, or that none was given, and the
    event says how many were declined each way — a report and never a
    decision, so a verdict carrying no `declined_findings` is accounted as it
    always was, with every left finding recorded as declined without a reason.
    It **files nothing**: the remainder is held for the run's completion, where
    `file_at_completion` files the final attempt's through
    `inspection.file_findings`, so its event carries `filed` and `dropped` at
    zero because that is true when it is written.

    Once per attempt, decided by the presence of the accounting artifact: the
    second verdict of one attempt — the one a correction pass returns to —
    finds it and splits nothing twice, and a retry, whose findings artifact
    is its own, is accounted under the next attempt's number. What a later
    verdict of the same attempt *can* do is amend: where it names
    `refused_slugs`, each routed slug the artifact records as acted on is
    moved to the remainder with the refusal recorded as its declined reason,
    the artifact is rewritten with the counts recomputed, and an event of
    this kind says which slugs moved and why — a refusal the verifier
    discovers on the returning verdict of a correction or repair pass would
    otherwise be logged and then forgotten, with no brief filed for it. A
    refused slug the artifact already records in the remainder keeps the
    reason it was recorded with, and one the artifact does not record as
    routed changes nothing. A later verdict refusing nothing leaves the
    artifact byte-for-byte what the first verdict wrote.

    An attempt that routed nothing is not accounted: a findings artifact that
    is absent, that records `ran` false, or that carries no findings leaves
    nothing to act on, nothing to hold and nothing to say, so no artifact and
    no event are written for it. That is what keeps the events.log of a run
    whose inspection was switched off byte-for-byte what it was.

    No Inspector is invoked and nothing reaches the queue from here: the agent
    runner is not on this path at all.
    """
    label = f"accounting for the findings routed to {story_id}"
    try:
        _account_after_verdict(
            run_dir, target_root, config, harness_root, story_id,
            artifact=artifact, attempt=attempt, verdict=verdict,
            refused_slugs=refused_slugs,
        )
    except Exception as error:  # noqa: BLE001 - the totality is the guarantee
        print(f"the accounting after the verdict could not run: {error}",
              file=sys.stderr)
        try:
            _say(run_dir, f"{label}: it could not run: {error}",
                 kind=ACCOUNTING_EVENT, routed=0, acted_on=0,
                 filed=0, dropped=0)
        except Exception:  # noqa: BLE001 - reporting may not become the failure
            pass


def _account_after_verdict(run_dir: Path, target_root: Path, config: dict,
                           harness_root: Path, story_id: str, *,
                           artifact: str, attempt: int, verdict,
                           refused_slugs=()) -> None:
    """The body of the above, so the guard has one thing to guard."""
    label = f"accounting for the findings routed to {story_id}"
    if (Path(run_dir) / accounting_artifact_file(artifact, attempt)).is_file():
        # This attempt is accounted. A second verdict within it — the one a
        # correction or repair pass returns to — changes nothing about what
        # was routed; the one thing it can change is a refusal made on this
        # verdict of a slug the first verdict acted on, which is amended in.
        if refused_slugs:
            _amend_accounting(run_dir, story_id, artifact=artifact,
                              attempt=attempt, verdict=verdict,
                              refused_slugs=refused_slugs, label=label)
        return
    record = _read_findings_record(run_dir, artifact, attempt)
    if record is None or not record.get("ran"):
        return
    routed = [one for one in record.get("findings") or () if isinstance(one, dict)]
    if not routed:
        return

    acted, remainder = acted_on_by(routed, verdict, refused_slugs=refused_slugs)
    # Why each left finding was left, where the verdict said. The verdict's
    # `declined_findings` names a slug and a reason; a left finding it names
    # is recorded with that reason, and one it does not name is recorded as
    # declined without one. This decides nothing — the remainder is the
    # remainder whether or not it was declined by name — and stops no run: it
    # makes a silent decline visible where a reader of the run looks first.
    declined = declined_accounting(remainder, declined_reasons(verdict))
    with_reason = sum(1 for one in declined if one["reason_given"])
    without_reason = len(declined) - with_reason

    # The remainder is recorded and not filed. What is filed, and when, is
    # `file_at_completion`'s: it reads these slugs back off this artifact for
    # the final attempt and files them through the one filing call, so a
    # finding declined on every attempt reaches the queue once.
    write_accounting(run_dir, artifact, attempt, story_id, routed=routed,
                     acted_on=acted, remainder=remainder, declined=declined)
    # "left by the verdict" rather than "held", because `held` is already this
    # module's word for the *other* set — the findings about files beside the
    # change, which `_summary` and the filing at completion both count under
    # that name. A reader meets the two lines together, and one saying "1 held"
    # where the next says "0 held and 1 left by the verdict" cannot tell that
    # the word changed what it was about. Both sets are held for the
    # completion; only one of them is what `held` counts.
    _say(run_dir,
         f"{label}: {len(routed)} routed on attempt {attempt}; "
         f"{len(acted)} acted on by the verdict, {len(remainder)} left by it "
         f"for the run's completion, of which {with_reason} declined with a "
         f"reason and {without_reason} declined without one; 0 filed, 0 dropped",
         kind=ACCOUNTING_EVENT, routed=len(routed), acted_on=len(acted),
         filed=0, dropped=0)


def _refusal_reasons(verdict, slugs) -> dict:
    """The reason the verdict's `declined_findings` gives for each of
    `slugs`, taking the *last* entry naming a slug.

    `declined_reasons` takes the first, which is right for the verifier's own
    decline. A refusal is appended by the coordinator after whatever the
    verifier wrote (`verdict_after_refusals` extends the array), so for a
    slug the coordinator refused a file of, the last entry is the refusal —
    which is what the amendment records as the moved slug's reason.
    """
    wanted = frozenset(one for one in slugs if isinstance(one, str))
    reasons: dict = {}
    if not isinstance(verdict, dict):
        return reasons
    for entry in verdict.get("declined_findings") or ():
        if not isinstance(entry, dict):
            continue
        slug, reason = entry.get("slug"), entry.get("reason")
        if slug in wanted and isinstance(reason, str) and reason.strip():
            reasons[slug] = reason
    return reasons


def _amend_accounting(run_dir: Path, story_id: str, *, artifact: str,
                      attempt: int, verdict, refused_slugs, label: str) -> None:
    """Move the refused slugs the attempt's accounting records as acted on
    into its remainder, and say so.

    Reached only from a later verdict of an already-accounted attempt that
    names `refused_slugs`. The split itself is not redone: the acted-on set
    is the recorded one minus the moved slugs, the remainder is the routed
    findings less that, so everything the first verdict left stays exactly
    as it was recorded, reason included. Each moved slug gains a declined
    entry carrying the refusal — read off the verdict's `declined_findings`,
    where the coordinator wrote it — or recorded as declined without a
    reason where none is there. The artifact is rewritten through
    `write_accounting` with the counts recomputed, so `remainder_slugs` is
    what `file_at_completion` reads and the brief the refusal promised is
    filed. Where no recorded acted-on slug is refused there is nothing to
    move: the artifact is left byte-for-byte as written and no event is
    appended.

    The amendment rebuilds the two lists from the attempt's findings record,
    so it stops where the first split stops: a findings record that is absent
    or cannot be read as one would rewrite the accounting with nothing routed
    and nothing left, erasing what the first verdict recorded and the refusal
    with it. Where the record cannot be read the artifact is left byte-for-
    byte as written and an event of this kind says the refusal could not be
    amended in and why, so the run's record carries the refusal even though
    the accounting does not.
    """
    accounting = _read_record(
        Path(run_dir) / accounting_artifact_file(artifact, attempt))
    if accounting is None:
        return
    acted_slugs = [one for one in accounting.get("acted_on_slugs") or ()
                   if isinstance(one, str)]
    remainder_slugs = [one for one in accounting.get("remainder_slugs") or ()
                       if isinstance(one, str)]
    moved = [slug for slug in refused_slugs
             if isinstance(slug, str) and slug in acted_slugs
             and slug not in remainder_slugs]
    moved = list(dict.fromkeys(moved))
    if not moved:
        return
    record = _read_findings_record(run_dir, artifact, attempt)
    if record is None:
        _say(run_dir,
             f"{label}: not amended on a later verdict of attempt {attempt}; "
             f"a scope extension was refused of {', '.join(moved)}, which the "
             f"accounting records as acted on, but the attempt's findings "
             f"record {findings_artifact_file(artifact, attempt)} is absent "
             f"or cannot be read, so the accounting is left as the first "
             f"verdict wrote it",
             kind=ACCOUNTING_EVENT, routed=len(acted_slugs) + len(remainder_slugs),
             acted_on=len(acted_slugs), filed=0, dropped=0)
        return
    routed = _findings_in(record)
    kept = frozenset(acted_slugs) - frozenset(moved)
    acted = [one for one in routed if _slug_of(one) and _slug_of(one) in kept]
    acted_ids = {id(one) for one in acted}
    remainder = [one for one in routed if id(one) not in acted_ids]
    reasons = _refusal_reasons(verdict, moved)
    declined = [one for one in accounting.get("declined") or ()
                if isinstance(one, dict)]
    for slug in moved:
        entry = {"slug": slug, "reason_given": slug in reasons}
        if slug in reasons:
            entry["reason"] = reasons[slug]
        declined.append(entry)
    write_accounting(run_dir, artifact, attempt, story_id, routed=routed,
                     acted_on=acted, remainder=remainder, declined=declined)
    why = "; ".join(
        f"{slug}: {reasons[slug]}" if slug in reasons else
        f"{slug}: the coordinator refused a scope extension of it, "
        f"and the verdict records no reason"
        for slug in moved
    )
    _say(run_dir,
         f"{label}: amended on a later verdict of attempt {attempt}; "
         f"{len(moved)} moved from acted on to left for the run's completion "
         f"because a scope extension was refused — {why}; now "
         f"{len(acted)} acted on by the verdict, {len(remainder)} left by it; "
         f"0 filed, 0 dropped",
         kind=ACCOUNTING_EVENT, routed=len(routed), acted_on=len(acted),
         filed=0, dropped=0)


# --------------------------------------------------------------------------
# Filing at completion
# --------------------------------------------------------------------------
#
# The one moment anything an inspection found during the run reaches the
# queue. The final attempt's inspection read the code that shipped, so what it
# held and what its verdict left unanswered is what a single-attempt run over
# that same code would have filed — and filing that once is exact where any
# comparison between two attempts' findings would be a similarity threshold,
# a number nothing checks.


def _attempts_holding(directory: Path, artifact: str) -> list[int]:
    """Every attempt the directory holds a held artifact or an accounting
    artifact for, in ascending order.

    Read by globbing the two names with the attempt wildcarded, through the
    same functions that write them, and taking the attempt back out of each
    match against the name the function composes for a placeholder — so
    discovery cannot drift from writing, and no artifact name is spelled.

    The directory is the run root or an earlier entry's directory: a resume
    moves the attempt-keyed artifacts of the entry it ends into that entry's
    directory, each at the position it held beneath the run directory, so the
    same glob answers there. Pointing it at one is how the filing at
    completion reaches what an interrupted attempt held, and it is why the
    parameter is named for a directory rather than for the run.
    """
    found: set[int] = set()
    for compose in (held_artifact_file, accounting_artifact_file):
        placeholder = "\0"
        head, _, tail = compose(artifact, placeholder).partition(placeholder)
        pattern = re.compile(
            "^" + re.escape(head) + r"(\d+)" + re.escape(tail) + "$"
        )
        for path in Path(directory).glob(compose(artifact, "*")):
            matched = pattern.match(path.name)
            if matched:
                found.add(int(matched.group(1)))
    return sorted(found)


def entry_directories(run_dir: Path) -> list[Path]:
    """The earlier entries' directories this run holds, most recent first.

    A run is re-entered rather than restarted: everything an interrupted entry
    wrote under a name keyed by a counter the re-entry resets is moved into
    that entry's own directory, the inspection's three artifacts among them.
    So a run that was interrupted and resumed holds an earlier reading of its
    own change, and the filing at completion read only the current entry —
    which is why a finding raised before the interruption, including one a
    verifier looked at and reasonably declined, reached the backlog from
    nowhere. Its copy was sitting in the archive with nothing to go and get it.

    Where those directories are is `story_coordinator.entry_dir`'s to say and
    nowhere else's, so this asks that function rather than spelling the path:
    the name is composed for a placeholder and read back off the directories
    beside it, the idiom `_attempts_holding` above already uses for an
    artifact name. A gap in the numbering — which the entry counter cannot
    produce, since it increments by one — is therefore skipped rather than
    stopping the walk, and a directory whose name is not an entry's is not one.

    Most recent first, so the assembled list is ordered newest reading to
    oldest and the dedupe beneath it keeps the newest wording of a defect that
    survived an interruption.
    """
    from story_coordinator import entry_dir

    placeholder = "\0"
    named = entry_dir(Path(run_dir), placeholder)
    head, _, tail = named.name.partition(placeholder)
    pattern = re.compile("^" + re.escape(head) + r"(\d+)" + re.escape(tail) + "$")
    found: list[tuple[int, Path]] = []
    if named.parent.is_dir():
        for path in named.parent.iterdir():
            matched = pattern.match(path.name)
            if matched and path.is_dir():
                found.append((int(matched.group(1)), path))
    return [path for _, path in sorted(found, key=lambda one: -one[0])]


def _deduped(findings) -> list:
    """The findings with a later duplicate of an earlier one left out.

    By the key the queue would file each under, through
    `inspection.finding_key`, so what counts as the same defect here is
    exactly what the queue counts as the same defect — a second notion of
    sameness would file a duplicate the queue could not collapse, and nothing
    local could notice, because a landed entry drops the payload a comparison
    would have needed.

    First occurrence wins, and the list is assembled current entry first, so a
    defect an interrupted entry raised and this one raised again is filed once
    and under this entry's wording: the current reading is of the tree that
    shipped. A finding whose key cannot be derived — a malformed one — is kept
    rather than dropped, because refusing it here would be a second place that
    decides what a malformed finding costs; `file_findings` names it and drops
    it under its own reason, which is where that decision belongs.
    """
    seen: set[str] = set()
    kept: list = []
    for one in findings:
        try:
            key = inspection.finding_key(one)
        except Exception:  # noqa: BLE001 - a malformed finding is filing's to name
            kept.append(one)
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(one)
    return kept


def _read_record(path: Path):
    """A JSON object at `path`, or None where there is none to read."""
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _findings_in(record) -> list:
    if record is None:
        return []
    return [one for one in record.get("findings") or ()
            if isinstance(one, dict)]


def held_findings(directory: Path, artifact: str, attempt: int) -> list:
    """The findings one attempt's inspection held for the run's completion.

    As the Inspector wrote them, and none where the attempt held nothing. The
    directory is the run root or an earlier entry's, for the reason
    `_attempts_holding` states.
    """
    return _findings_in(
        _read_record(Path(directory) / held_artifact_file(artifact, attempt)))


def held_area_suggestions(directory: Path, artifact: str,
                          attempt: int) -> tuple:
    """The Inspector's area suggestions the held artifact carried.

    The directory is the run root or an earlier entry's, as above.
    """
    record = _read_record(
        Path(directory) / held_artifact_file(artifact, attempt))
    if record is None:
        return ()
    found = []
    for one in record.get("area_suggestions") or ():
        if not isinstance(one, dict):
            continue
        slug, concerns = one.get("slug"), one.get("concerns")
        if isinstance(slug, str) and isinstance(concerns, str):
            found.append(inspection.AreaSuggestion(slug, concerns))
    return tuple(found)


def held_refused_area_suggestions(directory: Path, artifact: str,
                                  attempt: int) -> tuple:
    """The area suggestions the pre-stage inspection refused, as the drops
    that inspection recorded them under.

    Read back as `Drop(UNUSABLE_SUGGESTION, detail)` so the completion
    filing's area gate reads them on the terms the inspection's own gate read
    them: refused is still written, and written is still evidence of a
    vocabulary. Tolerant of the key's absence — a held artifact written before
    the key existed carries none — and of an entry that is not a string,
    which contributes nothing. These are for the gate alone; the filing's own
    drop count never counts them, because the inspection's line already did.
    The directory is the run root or an earlier entry's, as above.
    """
    record = _read_record(
        Path(directory) / held_artifact_file(artifact, attempt))
    if record is None:
        return ()
    entries = record.get("refused_area_suggestions")
    if not isinstance(entries, list):
        return ()
    return tuple(
        inspection.Drop(inspection.UNUSABLE_SUGGESTION, one)
        for one in entries if isinstance(one, str)
    )


def remainder_findings(directory: Path, artifact: str, attempt: int) -> list:
    """The routed findings one attempt's verdict left unanswered.

    Read back off the attempt's findings record by the slugs its accounting
    recorded as the remainder, so what is filed is the finding as the
    Inspector wrote it and what decides which are filed is the record the
    verdict was accounted in. An accounting that records no remainder — or no
    accounting at all — leaves nothing. The directory is the run root or an
    earlier entry's, as above.
    """
    accounting = _read_record(
        Path(directory) / accounting_artifact_file(artifact, attempt))
    if accounting is None:
        return []
    slugs = accounting.get("remainder_slugs")
    if not isinstance(slugs, list):
        return []
    wanted = frozenset(one for one in slugs if isinstance(one, str))
    routed = _findings_in(_read_findings_record(directory, artifact, attempt))
    return [one for one in routed if _slug_of(one) in wanted]


# The same guarantee a fourth time, for the caller that runs after the
# completion commit and before the sweep. It returns nothing, raises on no
# path, and declares no parameter by which a caller could be told to stop: a
# failure inside it — an unreadable artifact, a queue that cannot be reached —
# is printed and recorded and changes neither the run's status nor its exit
# code, which are settled by the commit above it.
def file_at_completion(run_dir: Path, target_root: Path, config: dict,
                       harness_root: Path, story_id: str, stages) -> None:
    """File what the final attempt's inspection held and its verdict left.

    Called from `_complete` in the position the post-story inspection
    occupies, after the completion commit and before the sweep, only where a
    stage of the loaded workflow declares an inspection. It reads the artifact
    name off that declaration, finds the highest attempt in the run directory
    holding a held artifact or an accounting artifact — the final attempt whose
    inspection read the tree — and files that attempt's held findings and the
    remainder its accounting recorded through `inspection.file_findings` with
    the floor and the cap `inspection.bounds` resolves. An event of kind
    `inspection-filed` says how many were filed and dropped, each drop reason
    named with its count, and the same record reaches the cross-run
    inspection log.

    **It reads the earlier entries of the run as well.** A run that was
    interrupted and resumed moved everything the interrupted entry wrote into
    that entry's own directory, so reading the current entry alone stranded
    every finding raised before the interruption — a finding a verifier had
    looked at and declined among them, which is precisely the thing that is
    supposed to become a backlog item. Each earlier entry's own final attempt
    is read through the same three readers pointed at that directory, most
    recent first, and appended after the current entry's. The assembled list
    is deduped by the key the queue files under, first occurrence winning, so
    a defect found on both sides of an interruption is filed once and under
    the current entry's wording; the completion line says how many came from
    earlier entries, so the recovery is visible rather than silent.

    What a recovery must not do is file a brief about a defect this same run
    repaired, so an entry's findings are filtered by the slugs a *newer*
    entry's accounting recorded its verdict as having acted on. The area
    suggestions are assembled over the same entries as the findings, so a
    recovered brief that named no area is reported with the suggestion the
    entry that found it wrote rather than with none.

    Where no attempt of any entry holds either artifact, or the ones that do
    have nothing to file, it files nothing and writes no event, so a run under
    a workflow declaring no inspection, or one whose file cap is unset, leaves
    events.log byte-for-byte what it was. With no earlier entry present the
    behaviour, and the line, are what they were before it read them.

    No Inspector is invoked and no model is reached: everything filed comes
    from the artifacts the run directory already holds.
    """
    label = f"filing at the completion of {story_id}"
    try:
        _file_at_completion(
            run_dir, target_root, config, harness_root, story_id, stages
        )
    except Exception as error:  # noqa: BLE001 - the totality is the guarantee
        print(f"the filing at completion could not run: {error}",
              file=sys.stderr)
        try:
            write_problem = _say(run_dir, f"{label}: it could not run: {error}",
                                 kind=FILED_EVENT, filed=0, dropped=0)
            # The record above went into the tracked cross-run log after the
            # completion commit, so it is committed here on the terms the
            # post-story inspection's failure path commits its own: a commit
            # that cannot be made costs the commit and not the run, and a
            # record left uncommitted is what the next run's clean-tree
            # pre-flight refuses on.
            commit_record(run_dir, target_root, config, story_id,
                          FILED_COMMIT_SUBJECT, write_problem=write_problem)
        except Exception:  # noqa: BLE001 - reporting may not become the failure
            pass


def _file_at_completion(run_dir: Path, target_root: Path, config: dict,
                        harness_root: Path, story_id: str, stages) -> None:
    """The body of the above, so the guard has one thing to guard."""
    label = f"filing at the completion of {story_id}"
    from story_coordinator import inspection_declaration

    artifact = inspection_declaration(stages or []).get("result")
    if not artifact:
        return
    attempts = _attempts_holding(run_dir, artifact)
    attempt = attempts[-1] if attempts else 0
    held = held_findings(run_dir, artifact, attempt) if attempts else []
    remainder = remainder_findings(run_dir, artifact, attempt) if attempts else []

    # The area suggestions travel with the findings they are about, so a brief
    # recovered from an earlier entry carries that entry's own suggestion
    # rather than none: they are matched to a filed brief by slug, and the
    # suggestion for a slug only this entry's inspection saw is only in this
    # entry's held artifact. Ordered oldest last-wins — the gate keys them by
    # slug and the last wins, and the current entry's reading is of the tree
    # that shipped — which is the dedupe's rule said from the other side.
    suggestions: list = []
    refused: list = []
    if attempts:
        suggestions.extend(held_area_suggestions(run_dir, artifact, attempt))
        refused.extend(
            held_refused_area_suggestions(run_dir, artifact, attempt))

    # And the same of each earlier entry of this run, most recent first. A run
    # that was interrupted and resumed left its earlier reading in that
    # entry's own directory, and reading only the current entry stranded every
    # finding raised before the interruption — including one a verifier looked
    # at and reasonably declined, which is exactly what is supposed to become
    # a backlog item. Each entry is read through the same readers, pointed at
    # that directory, so there is one derivation of what an entry held rather
    # than one per place it is held.
    #
    # An entry's findings are filtered by what a *newer* entry's verdict acted
    # on, accumulated as the walk goes from newest to oldest. A defect an
    # earlier entry raised and a later one repaired is a brief describing code
    # as it no longer is, and filing it costs a planning interview, a story
    # and a merge for work already done; the current entry's own remainder
    # excludes its own acted-on findings by construction, so this only ever
    # drops a recovered one. The direction is what makes it right rather than
    # too eager: a defect repaired in one entry and found *again* by a later
    # entry's inspection is that later entry's finding, and the repair that
    # preceded it says nothing about it.
    #
    # Every attempt an entry holds is read for what its verdict acted on,
    # where the findings an entry contributes come from its last alone. The
    # accounting is written once per attempt, and a verdict that acts on a
    # finding advances no attempt counter — a correction or repair pass
    # spends no retry — so an entry can record a repair on one attempt and be
    # retried into a later one whose verdict acted on nothing. Reading that
    # entry's highest attempt alone would then find no repair, and the earlier
    # copy of a defect this run had already fixed would be filed.
    repaired: set = set()
    for one in attempts:
        repaired.update(acted_on_slugs(run_dir, artifact, one))
    earlier: list = []
    for directory in entry_directories(run_dir):
        found = _attempts_holding(directory, artifact)
        if not found:
            continue
        contributed = (held_findings(directory, artifact, found[-1])
                       + remainder_findings(directory, artifact, found[-1]))
        earlier.extend(one for one in contributed
                       if _slug_of(one) not in repaired)
        suggestions[:0] = held_area_suggestions(directory, artifact, found[-1])
        refused.extend(
            held_refused_area_suggestions(directory, artifact, found[-1]))
        for one in found:
            repaired.update(acted_on_slugs(directory, artifact, one))

    # Deduped by the key the queue files under, current entry first, so a
    # defect found on both sides of an interruption is filed once and under
    # the current entry's wording. The count from earlier entries is what
    # survived that, so the completion line reports what the recovery actually
    # added rather than what it read.
    current = _deduped(held + remainder)
    assembled = _deduped(held + remainder + earlier)
    from_earlier = len(assembled) - len(current)
    if not assembled:
        return

    # What every line below opens with, composed once so the two cannot
    # disagree about what was read. The attempt clause is omitted where the
    # current entry holds nothing — a run resumed past the stage that inspects
    # has only its earlier entries to file from — and the earlier-entry clause
    # where there were none, which is what keeps a run that was never
    # interrupted writing exactly the line it wrote before.
    head = f"{label}: {len(held)} held and {len(remainder)} left by the verdict"
    if attempt:
        head += f" on attempt {attempt}"
    if from_earlier:
        head += f"; from earlier entries of this run: {from_earlier}"

    bound, problem = inspection.bounds(config)
    if bound is None:
        # Unreachable while the inspection that held these resolved the same
        # bound, and handled anyway: nothing is filed on a bound the target got
        # wrong, and the record says so rather than obeying a default.
        write_problem = _say(
            run_dir,
            f"{head}; 0 filed, {len(assembled)} dropped: {problem}",
            kind=FILED_EVENT, filed=0, dropped=len(assembled))
        commit_record(run_dir, target_root, config, story_id,
                      FILED_COMMIT_SUBJECT, write_problem=write_problem)
        return

    scope = inspection.Scope(
        path="", kind=inspection.CHANGE,
        origin=ORIGIN.format(story_id=story_id), framing=PRE_STAGE_FRAMING,
    )
    # Through exactly the call every other producer files through — the same
    # floor, the same cap, the same named drop reasons, the same dedupe and
    # the same queue — with the held findings first, since they were found
    # first, and the remainder after them.
    filed, dropped = inspection.file_findings(
        target_root, [_Routed(one, scope) for one in assembled],
        bound.max_findings, min_severity=bound.min_severity,
    )
    # A filed brief that named no area is said here, where the brief reached
    # the queue, on the terms `_report` says it: the suggestions travelled with
    # the held findings for exactly this line, and so did the suggestions the
    # pre-stage reader refused — as drops under UNUSABLE_SUGGESTION, which the
    # area gate reads as evidence of a vocabulary exactly as the inspection's
    # own gate did. Both are assembled over the same entries the findings came
    # from, so a recovered brief is reported with the suggestion the entry
    # that found it wrote, and a run whose current entry holds nothing at all
    # is still reported from the entries it recovered. This report serves the
    # gate alone: the line below counts the filing's own drops, because the
    # refused suggestions were already counted on the inspection's line and
    # would be counted twice here.
    report = inspection.Report(
        filed=tuple(filed),
        dropped=tuple(dropped) + tuple(refused),
        min_severity=bound.min_severity,
        area_suggestions=tuple(suggestions),
    )
    for brief, suggestion in report.unnamed_areas:
        line = f"{label}: no area was named for {brief.slug}"
        if suggestion:
            line += f"; the Inspector says it concerns: {suggestion}"
        _note(run_dir, line)

    line = f"{head}; {len(filed)} filed as briefs, {len(dropped)} dropped"
    for reason, count in _drops_by_reason(dropped).items():
        line += f"; {reason}: {count}"
    write_problem = _say(run_dir, line, kind=FILED_EVENT, filed=len(filed),
                         dropped=len(dropped))
    # The record reaches the tracked cross-run log after the completion
    # commit, so it is committed the way the post-story inspection commits
    # its own: by name, and under a subject of its own.
    commit_record(run_dir, target_root, config, story_id,
                  FILED_COMMIT_SUBJECT, write_problem=write_problem)


