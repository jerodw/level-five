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
routed finding the verdict did not name is filed as a brief from the artifact
already in the run directory — through the same filing call, with no second
invocation — once per attempt, and an event says how many were acted on, filed
and dropped. Declining a finding stays correct; declining no longer deletes it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
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

#: What a post-story inspection's cost is recorded as in that run's cost.json.
#: It is not an attempt at a stage, so it carries no attempt number of its own
#: and is recorded at attempt 0; the stage name says what the invocation was
#: rather than naming a stage the workflow declares.
COST_STAGE = "inspection"
COST_ATTEMPT = 0

#: The question this mode asks, supplied from here because it is this caller's
#: question. Broad mode's is `inspection.BROAD_FRAMING` and is supplied from
#: there for the same reason.
POST_STORY_FRAMING = (
    "A story has just completed and its work is committed. Ask whether that "
    "change left a defect: whether what it added is wrong, whether it agrees "
    "with the files beside it that it did not change, and whether anything it "
    "touched now contradicts something it did not. The files the story changed "
    "are listed first below and the files beside them follow; the change is "
    "your subject and its neighbours are the evidence you judge it against."
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
    "you judge it against."
)

#: The subject the record commit carries. It leads with the harness rather
#: than with the story id and carries no COMPLETION_COMMIT_MARKER, so it
#: matches neither the completion shape `completion_commits` reads nor the
#: escalation shape `_head_escalated` reads nor the pause shape beside it.
COMMIT_SUBJECT = "l5 recorded a post-story inspection of {story_id}"

#: The drop reasons that name something other than a finding the invocation
#: produced, so a count of findings does not count them.
NOT_A_FINDING = (inspection.NO_ARTIFACT,)


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
    """The parts of the tree a post-story inspection is bounded to.

    `source_dirs` plus `tests_dir`, which is the same pair broad mode covers.
    A target that declares neither is bounded to its whole working tree rather
    than to nothing — the reading `inspection.scopes` already takes, and the
    alternative would silently inspect nothing in every target that has not
    declared a source layout.
    """
    declared = list(config.get(inspection.SOURCE_DIRS_KEY) or [])
    tests_dir = config.get(inspection.TESTS_DIR_KEY)
    if tests_dir:
        declared.append(tests_dir)
    found = tuple(
        one for one in (_prefix(entry) for entry in declared) if one
    )
    return found or ("",)


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
    in. Bounded to the scope keys and with the execution rules' blocked
    prefixes excluded.

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
    blocked = tuple(
        _prefix(one) for one in inspection.blocked_prefixes(harness_root) if one
    )

    def in_scope(path: str) -> bool:
        return _under(path, prefixes) and not _under(path, blocked)

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
         kind: str = INSPECTION_EVENT,
         routed: int | None = None,
         acted_on: int | None = None) -> None:
    """Append the inspection's record through the coordinator's shared append.

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

    `kind` defaults to the inspection's own event and is overridden by the one
    other record this module writes, the accounting after the verdict, which
    carries `routed` and `acted_on` and no cost, no invocation count and no
    dedupe verdict — it made no invocation and asked no query.

    Imported inside the body, the idiom the queue module already uses for its
    own coordinator import: the coordinator imports this module, and a
    module-scope import would close the cycle. Guarded for the reason
    everything here is guarded — an inspection that could not report is still
    an inspection that must not stop a run.
    """
    try:
        from story_coordinator import append_event

        append_event(
            Path(run_dir), message, kind=kind,
            findings=findings, filed=filed, dropped=dropped,
            mode=MODE if kind == INSPECTION_EVENT else None,
            cost_usd=cost_usd, scope_files=scope_files,
            invocations=invocations, dedupe_ran=dedupe_ran,
            routed=routed, acted_on=acted_on,
        )
    except Exception:  # noqa: BLE001 - reporting may not become the failure
        pass


def _announce(run_dir: Path, story_id: str, scope_files: int) -> None:
    """Say the inspection has started and what the wait is worth.

    A run says "story completed" and then goes quiet for about as long as a
    stage, which is the one moment a developer has been told they may stop
    watching — so the silence reads as a hang rather than as work. This is the
    idiom the suite reruns already use, reached through the coordinator's own
    announcer so the announcement convention has one home and this module
    spells no event kind of its own.

    Imported inside the body and guarded for the reasons `_say` and
    `record_cost` are: the coordinator imports this module, and an announcement
    that cannot be written costs the announcement and never the run.
    """
    try:
        from story_coordinator import inspection_started

        inspection_started(Path(run_dir), story_id, scope_files)
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


def record_paths(target_root: Path, config: dict) -> tuple[str, ...]:
    """The repository-relative record paths this inspection's commit stages.

    Asked of the same projection the append took, the shape
    `plan_mandate._logs_holding` established: a declaration that stops routing
    this kind stops staging the file, with no edit here.

    The derivation is `inspection.record_paths`, reached through here rather
    than copied, because both modes write a record of the same kind and two
    derivations of which logs that kind reaches are two answers that can
    disagree. The guard stays here, because this producer may not raise.
    """
    try:
        return inspection.record_paths(target_root, config)
    except Exception:  # noqa: BLE001 - the totality is the guarantee
        return ()


def commit_record(target_root: Path, config: dict, story_id: str) -> None:
    """Commit the record this inspection wrote, and only the record.

    The paths are staged **by name** and never with `git add -A`, so an edit
    the inspection agent made elsewhere in the repository is left in the
    working tree rather than folded into this commit. The commit is skipped
    entirely where staging left the index empty — a target tracking no record
    path gains no commit — and the subject carries no completion marker and
    matches neither the completion, escalation nor pause shape, so nothing
    that reads a branch for one of those reads this.
    """
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
        # Nothing of ours is staged, so there is nothing to commit. An empty
        # commit here would be a commit about a record that does not exist.
        return
    subprocess.run(  # noqa: S603 - a fixed argument list
        [*argv, "commit", "-m", COMMIT_SUBJECT.format(story_id=story_id),
         "--", *paths],
        capture_output=True, text=True,
    )


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
    no artifact at all — is counted as dropped but not as a finding, because it
    is not one.
    """
    dropped = len(report.dropped)
    findings = len(report.filed) + sum(
        1 for one in report.dropped if one.reason not in NOT_A_FINDING
    )
    return findings, len(report.filed), dropped


def _summary(label: str, report, excluded, trimmed, log: str, *,
             to_the_story: int = 0) -> str:
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
    what the verdict acted on, and what was filed because it did not, is said
    after the verdict by `account_after_verdict`.
    """
    findings, filed, dropped = _counts(report)
    findings += to_the_story
    line = (
        f"{label}: {findings} finding(s), "
        f"{filed} filed, {dropped} dropped"
    )
    if to_the_story:
        # Worded for the moment it is written: the findings have been handed
        # to the stage and nothing has yet been decided about them. What the
        # verdict does with them is said afterwards, by the accounting.
        line += f"; routed to this story: {to_the_story}"
    reasons = [
        inspection.ALREADY_FILED,
        inspection.ALREADY_FILED_LOCALLY,
        inspection.ALREADY_QUEUED,
        inspection.MALFORMED,
        inspection.UNKNOWN_WORKFLOW,
        inspection.BENEATH_THE_FLOOR,
        inspection.PAST_THE_CAP,
        inspection.LOST_BY_THE_QUEUE,
        inspection.NO_ARTIFACT,
    ]
    for reason in reasons:
        count = len(report.dropped_for(reason))
        if count:
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
    result = inspection.inspect_scope(
        scope, target_root, config, harness_root, prepared.bound, (), runner,
        inspection.local_index(target_root, harness_root),
    )
    return scope, result


def _report(run_dir: Path, target_root: Path, config: dict, story_id: str,
            label: str, prepared: _Preparation, report, result,
            to_the_story: int) -> None:
    """Say what the inspection found, in the run's own record and the log.

    Shared by both modes, so the notes a reader relies on — a dedupe that could
    not answer, a tracker command that moved under the run, a filed brief that
    named no area — are written on the same terms wherever the inspection ran.
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
    _say(
        run_dir,
        _summary(
            label, report, prepared.excluded, prepared.trimmed,
            _relative(detail_log(target_root, config, story_id), target_root),
            to_the_story=to_the_story,
        ),
        findings=findings + to_the_story, filed=filed_count,
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
        invocations=1,
    )
    # Beside the stage invocations in that run's own cost.json, and never added
    # to the allowance the ceiling reads. See `record_cost` for why the two are
    # different jobs.
    record_cost(run_dir, result.cost_usd)


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
            _say(
                run_dir,
                f"{label}: it could not run: {error}",
                findings=0, filed=0, dropped=0,
                scope_files=0, invocations=0,
                # No invocation was made, so no filed query answered for this
                # run. False is the honest reading; the vacuous true an empty
                # scope list would give would say dedupe ran when nothing asked.
                dedupe_ran=False,
            )
            commit_record(target_root, config, story_id)
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
        _say(run_dir, f"{label}: {prepared.problem}",
             findings=0, filed=0, dropped=0,
             scope_files=0, invocations=0, dedupe_ran=False)
        commit_record(target_root, config, story_id)
        return

    scope, result = _invoke(
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
        invocations=1,
        filed=filed,
        dropped=tuple(result.dropped) + tuple(over),
        dedupe=(result.dedupe,) if result.dedupe is not None else (),
        cost_usd=result.cost_usd,
        scope_files=result.scope_files,
        min_severity=prepared.bound.min_severity,
        area_suggestions=tuple(result.area_suggestions),
    )
    _report(run_dir, target_root, config, story_id, label, prepared, report,
            result, 0)
    commit_record(target_root, config, story_id)


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
    finding is filed as a brief exactly as it is filed from anywhere else,
    through the same floor, the same brief cap, the same dedupe and the same
    queue.

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
            )
            # The stage is told the inspection could not be made rather than
            # being handed nothing: an absence a reader takes for agreement is
            # the failure this record exists against.
            write_findings(
                run_dir, artifact, attempt, story_id, ran=False,
                reason=f"the inspection could not run: {error}",
            )
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
                 scope_files=0, invocations=0, dedupe_ran=False)
        # Written on this path too, and on the switched-off path as well: the
        # stage reads a statement of why there is nothing rather than an
        # absence it would have to interpret, and the presence of the file is
        # what keeps the once-per-attempt rule one rule rather than two.
        write_findings(run_dir, artifact, attempt, story_id, ran=False,
                       reason=reason)
        return

    scope, result = _invoke(
        prepared, run_dir, target_root, config, harness_root, story_id,
        PRE_STAGE_FRAMING, runner,
    )

    own, others = about_the_change(result.found, prepared.found.changed)

    # Only the findings that are not about this story's own change are filed,
    # and they are filed through exactly the call every other producer files
    # through — the same floor, the same brief cap, the same named drop reasons
    # and the same queue — so a backlog item reaching the tracker from here is
    # indistinguishable from one reaching it from anywhere else.
    filed, over = inspection.file_findings(
        target_root, others, prepared.bound.max_findings,
        min_severity=prepared.bound.min_severity,
    )
    report = inspection.Report(
        scopes=(scope,),
        invocations=1,
        filed=filed,
        dropped=tuple(result.dropped) + tuple(over),
        dedupe=(result.dedupe,) if result.dedupe is not None else (),
        cost_usd=result.cost_usd,
        scope_files=result.scope_files,
        min_severity=prepared.bound.min_severity,
        area_suggestions=tuple(result.area_suggestions),
    )
    _report(run_dir, target_root, config, story_id, label, prepared, report,
            result, len(own))
    # Last, so a failure to write the record cannot cost the filing above it.
    # The findings are written as the Inspector wrote them: nothing here
    # rewrites, rates or summarises one, because what the stage is being given
    # is the Inspector's reading rather than the coordinator's.
    write_findings(
        run_dir, artifact, attempt, story_id, ran=True,
        findings=[one.finding for one in own],
    )


# --------------------------------------------------------------------------
# Accounting for what was routed, after the verdict
# --------------------------------------------------------------------------
#
# A finding routed to the story is written to the artifact above and read by
# the stage that judges the change — and by nothing after the verdict. A
# finding the verifier declines to act on, which its prompt permits because a
# finding can be correct and still be too small to fail a run, was therefore
# neither fixed nor filed. What follows sends a declined finding where a
# declined finding went before the inspection was moved ahead of the verifier:
# to the backlog, as a brief, through the same call every other producer files
# through. The verifier's judgement is not touched; what changes is that
# declining no longer deletes.


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


def acted_on_by(findings, verdict) -> tuple[list, list]:
    """Split routed findings into what the verdict acted on and the rest.

    Returns `(acted_on, remainder)`, in the order given. A finding is acted on
    when the verdict names its slug in the location of a blocking issue or a
    correctable finding; every other finding is the remainder, including one
    the verdict named by file alone and one carrying no slug at all — a
    finding nothing can name is a finding nothing acted on, and filing it is
    the loss-free reading.

    Pure over its inputs, so it can be held directly the way the partition
    tests hold `about_the_change`.
    """
    locations = verdict_locations(verdict)
    acted: list = []
    remainder: list = []
    for one in findings:
        slug = _slug_of(one)
        if slug and any(names_a_slug(location, (slug,)) for location in locations):
            acted.append(one)
        else:
            remainder.append(one)
    return acted, remainder


def entries_among_the_routed(locations, routed_slugs) -> int:
    """How many verdict entries name one of the routed findings' slugs.

    The re-entry events' number: of the findings an event carries, how many are
    among the ones routed to this story. Counted over the entries and decided
    by `names_a_slug`, so it is the accounting's own rule read from the other
    side rather than a second one.
    """
    slugs = frozenset(one for one in routed_slugs if isinstance(one, str))
    if not slugs:
        return 0
    return sum(1 for location in locations if names_a_slug(location, slugs))


@dataclass(frozen=True)
class _Routed:
    """One routed finding on its way to the queue, in the shape
    `inspection.file_findings` files: the finding, and where it came from.
    """

    finding: dict
    scope: object


def _read_findings_record(run_dir: Path, artifact: str, attempt: int):
    """The attempt's findings artifact, or None where there is none to read."""
    path = Path(run_dir) / findings_artifact_file(artifact, attempt)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def write_accounting(run_dir: Path, artifact: str, attempt: int,
                     story_id: str, *, routed, acted_on, filed: int,
                     dropped: int, reason: str = "") -> None:
    """Write the accounting artifact for one attempt.

    It carries the slugs and not only the counts, because the re-entry events
    are worded from it: a correction pass or a retry says how many of the
    findings it carries are among the ones routed, and that is decidable only
    from the slugs. Nothing routes on any of it.
    """
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


def routed_slugs(run_dir: Path, artifact: str, attempt: int) -> tuple:
    """The slugs the accounting artifact records as routed on one attempt.

    Read back off the artifact rather than carried in memory, so what an
    event says about the routed findings and what the run directory records
    are one thing. Empty where no attempt was accounted, and read by nothing
    that routes: the coordinator words two events from it and decides nothing.
    """
    try:
        path = Path(run_dir) / accounting_artifact_file(artifact, attempt)
        if not path.is_file():
            return ()
        record = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            one for one in record.get("routed_slugs", ())
            if isinstance(one, str) and one
        )
    except Exception:  # noqa: BLE001 - reading a record may not become a failure
        return ()


# The same guarantee a third time, for the caller that runs after the verdict
# and before any routing. It returns nothing, raises on no path, and declares
# no parameter by which a caller could be told to stop: a failure inside it —
# an unreadable artifact, a queue that cannot be reached — is printed and
# recorded and changes neither the verdict's routing, the run's status nor its
# exit code.
def account_after_verdict(run_dir: Path, target_root: Path, config: dict,
                          harness_root: Path, story_id: str, *,
                          artifact: str, attempt: int, verdict) -> None:
    """Account for every finding routed to this story, once per attempt.

    Called from the verifier branch after the verdict is archived and before
    the passed/failed routing, only where the stage declares an inspection. It
    reads the attempt's findings artifact, splits the routed findings into the
    ones the verdict acted on — named by slug in the location of a blocking
    issue or a correctable finding — and the remainder, files the remainder
    through `inspection.file_findings` with the resolved floor and cap, writes
    the accounting artifact, and says the split in the run's own record.

    Once per attempt, decided by the presence of the accounting artifact: the
    second verdict of one attempt — the one a correction pass returns to —
    finds it and files nothing twice, and a retry, whose findings artifact is
    its own, is accounted under the next attempt's number.

    An attempt that routed nothing is not accounted: a findings artifact that
    is absent, that records `ran` false, or that carries no findings leaves
    nothing to act on, nothing to file and nothing to say, so no artifact and
    no event are written for it. That is what keeps the events.log of a run
    whose inspection was switched off byte-for-byte what it was.

    No Inspector is invoked: the findings it files come from the artifact the
    pre-stage inspection already wrote, and the agent runner is not on this
    path at all.
    """
    label = f"accounting for the findings routed to {story_id}"
    try:
        _account_after_verdict(
            run_dir, target_root, config, harness_root, story_id,
            artifact=artifact, attempt=attempt, verdict=verdict,
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
                           artifact: str, attempt: int, verdict) -> None:
    """The body of the above, so the guard has one thing to guard."""
    label = f"accounting for the findings routed to {story_id}"
    if (Path(run_dir) / accounting_artifact_file(artifact, attempt)).is_file():
        # This attempt is accounted. A second verdict within it — the one a
        # correction pass returns to — changes nothing about what was routed.
        return
    record = _read_findings_record(run_dir, artifact, attempt)
    if record is None or not record.get("ran"):
        return
    routed = [one for one in record.get("findings") or () if isinstance(one, dict)]
    if not routed:
        return

    acted, remainder = acted_on_by(routed, verdict)

    bound, problem = inspection.bounds(config)
    if bound is None:
        # Unreachable while the inspection that wrote the artifact resolved the
        # same bound, and handled anyway: nothing is filed on a bound the target
        # got wrong, and the record says so rather than obeying a default.
        write_accounting(run_dir, artifact, attempt, story_id, routed=routed,
                         acted_on=acted, filed=0, dropped=len(remainder),
                         reason=problem)
        _say(run_dir,
             f"{label}: {len(routed)} routed on attempt {attempt}; "
             f"{len(acted)} acted on by the verdict, 0 filed, "
             f"{len(remainder)} dropped: {problem}",
             kind=ACCOUNTING_EVENT, routed=len(routed), acted_on=len(acted),
             filed=0, dropped=len(remainder))
        return

    scope = inspection.Scope(
        path="", kind=inspection.CHANGE,
        origin=ORIGIN.format(story_id=story_id), framing=PRE_STAGE_FRAMING,
    )
    # The remainder is filed through exactly the call every other producer
    # files through — the same floor, the same cap, the same named drop
    # reasons, the same dedupe and the same queue — from the artifact the run
    # directory already holds, with no second invocation of the Inspector.
    filed, dropped = inspection.file_findings(
        target_root, [_Routed(one, scope) for one in remainder],
        bound.max_findings, min_severity=bound.min_severity,
    )
    write_accounting(run_dir, artifact, attempt, story_id, routed=routed,
                     acted_on=acted, filed=len(filed), dropped=len(dropped))

    line = (
        f"{label}: {len(routed)} routed on attempt {attempt}; "
        f"{len(acted)} acted on by the verdict, {len(filed)} filed as briefs, "
        f"{len(dropped)} dropped"
    )
    reasons: dict = {}
    for one in dropped:
        reasons[one.reason] = reasons.get(one.reason, 0) + 1
    for reason, count in reasons.items():
        line += f"; {reason}: {count}"
    _say(run_dir, line, kind=ACCOUNTING_EVENT, routed=len(routed),
         acted_on=len(acted), filed=len(filed), dropped=len(dropped))


