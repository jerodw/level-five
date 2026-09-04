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
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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

    `changed` is what the run's own stages recorded, in scope and still tracked.
    `siblings` is what git tracks directly beside those files. They are kept
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


def _tracked(target_root: Path) -> tuple[str, ...]:
    """Every path git tracks, as repository-relative paths.

    Run through a module-local subprocess call with a fixed argument list, the
    idiom `inspection._tracked` already uses. A repository git cannot answer
    for tracks nothing here rather than raising: this module may not raise, and
    an inspection is not the place to discover a broken checkout.
    """
    argv = ["git", "-C", str(target_root), "ls-files", "-z"]
    try:
        completed = subprocess.run(  # noqa: S603 - a fixed argument list
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
    except OSError:
        return ()
    if completed.returncode != 0:
        return ()
    return tuple(one for one in completed.stdout.split("\0") if one)


def containing_directory(path: str) -> str:
    """The directory a repository-relative path sits directly in.

    "" for a path at the repository root, which is the same value the tracked
    listing gives such a path, so the two compare without a special case.
    """
    head, separator, _ = path.rpartition("/")
    return head if separator else ""


def scope_prefixes(config: dict) -> tuple[str, ...]:
    """The parts of the tree a post-story inspection is bounded to.

    `source_dirs` plus `tests_dir`, which is the same pair broad mode covers.
    A target that declares neither is bounded to its whole tracked tree rather
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

    The changed paths, plus for each of them the files git tracks *directly* in
    its containing directory — one level and not recursively, so a populated
    subdirectory beneath a changed file's directory is not pulled in. Bounded
    to the scope keys and with the execution rules' blocked prefixes excluded.

    A changed path outside both scope keys is dropped and named, and its
    directory is not pulled in: what the harness will not inspect it should not
    inspect the neighbours of either. A changed path git no longer tracks —
    a deleted one — contributes its containing directory but not itself, which
    falls out of taking the paths from the tracked listing rather than from the
    record. No model is involved in any of this: it is two set operations over
    one `git ls-files`.
    """
    tracked = frozenset(_tracked(target_root))
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
        if path in tracked:
            kept.append(path)
        else:
            # Deleted by the story, or never tracked. Its directory is in scope
            # because what sits beside a removal is exactly what a removal can
            # have broken; the path itself is not, because there is nothing
            # there to read.
            excluded.append(f"{path}: the repository no longer tracks it")

    beside = sorted(
        path for path in tracked
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
         invocations: int | None = None) -> None:
    """Append the inspection's record through the coordinator's shared append.

    Every value goes through that one call, so events.log, the run's structured
    history and the cross-run log stay one write path and not three. The cost,
    the scope size and the invocation count are passed beside the three counts
    and are omitted when absent on exactly the terms those three already are —
    which is how an invocation that reported no cost records that it reported
    none rather than recording a zero.

    Imported inside the body, the idiom the queue module already uses for its
    own coordinator import: the coordinator imports this module, and a
    module-scope import would close the cycle. Guarded for the reason
    everything here is guarded — an inspection that could not report is still
    an inspection that must not stop a run.
    """
    try:
        from story_coordinator import append_event

        append_event(
            Path(run_dir), message, kind=INSPECTION_EVENT,
            findings=findings, filed=filed, dropped=dropped,
            mode=MODE, cost_usd=cost_usd, scope_files=scope_files,
            invocations=invocations,
        )
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


def _summary(story_id: str, report, excluded, trimmed) -> str:
    """One line saying what the inspection did, for the run's own events.log.

    Every way a finding was dropped is named with how many went that way, on
    the no-silent-bound rule the rest of this mechanism already follows: a
    count with no cause reads as a change with nothing wrong in it.

    A *path* left out is named rather than counted, and that is the same rule
    one step further: a count of trimmed files tells a reader that the scope
    was smaller than the expansion and leaves them no way to find out which
    file the inspection did not read. The run directory is gitignored and the
    cross-run record is deliberately a summary, so this line is the only place
    those names survive. There is no second bound on how many are named,
    because a bound here would be exactly the silent drop the naming exists
    against — what bounds the list is the file cap the reader is being told
    about.
    """
    findings, filed, dropped = _counts(report)
    line = (
        f"post-story inspection of {story_id}: {findings} finding(s), "
        f"{filed} filed, {dropped} dropped"
    )
    reasons = [
        inspection.ALREADY_FILED,
        inspection.ALREADY_FILED_LOCALLY,
        inspection.ALREADY_QUEUED,
        inspection.MALFORMED,
        inspection.UNKNOWN_WORKFLOW,
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
    if trimmed:
        line += "; trimmed to the file cap: " + ", ".join(trimmed)
    if excluded:
        line += "; left out of scope: " + "; ".join(excluded)
    return line


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
    the next run.

    Every way this can go wrong ends here: the key unset, the key unusable, no
    changed path in scope, an agent that cannot be reached, a filed query that
    fails, an enqueue that drops every item, a record that cannot be appended
    and a commit that cannot be made. None of them has a way to tell the caller
    anything, because there is no value to tell it with.
    """
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
                f"post-story inspection of {story_id}: it could not run: "
                f"{error}",
                findings=0, filed=0, dropped=0,
                scope_files=0, invocations=0,
            )
            commit_record(target_root, config, story_id)
        except Exception:  # noqa: BLE001 - reporting may not become the failure
            pass


def _inspect_after_story(run_dir: Path, target_root: Path, config: dict,
                         harness_root: Path, story_id: str, stages,
                         *, runner) -> None:
    """The body of the above, so the guard has one thing to guard."""
    cap, problem = max_files(config)
    if cap is None:
        if problem:
            # Named in the record rather than refused. The run completes with
            # the status it would have had with the key unset, which is what a
            # total function's answer to a bad bound has to be.
            _say(run_dir, f"post-story inspection of {story_id}: {problem}",
                 findings=0, filed=0, dropped=0,
                 scope_files=0, invocations=0)
            commit_record(target_root, config, story_id)
        # An absent key is the mechanism switched off: no invocation, no event,
        # no commit, and an events.log byte-for-byte what it was.
        return

    from story_coordinator import recorded_by_all_stages

    changed = recorded_by_all_stages(run_dir, stages)
    found = expansion(target_root, config, harness_root, changed)
    paths, trimmed = cap_paths(found, cap)
    excluded = found.excluded

    if not paths:
        _say(
            run_dir,
            f"post-story inspection of {story_id}: nothing the story changed "
            f"is in an inspected scope, so no inspection was made",
            findings=0, filed=0, dropped=0,
            scope_files=0, invocations=0,
        )
        commit_record(target_root, config, story_id)
        return

    bound, bound_problem = inspection.bounds(config)
    if bound is None:
        _say(run_dir, f"post-story inspection of {story_id}: {bound_problem}",
             findings=0, filed=0, dropped=0,
             scope_files=0, invocations=0)
        commit_record(target_root, config, story_id)
        return

    # One invocation whatever mix of source and tests files the story changed:
    # the change is one subject, and splitting it by which half of the tree a
    # file sits in would ask two agents about one change.
    scope = inspection.Scope(
        path="",
        kind=inspection.CHANGE,
        paths=tuple(paths),
        origin=ORIGIN.format(story_id=story_id),
        framing=POST_STORY_FRAMING,
    )
    if runner is None:
        import agent_runner

        runner = agent_runner.run_agent
    result = inspection.inspect_scope(
        scope, target_root, config, harness_root, bound, (), runner,
        inspection.local_index(target_root, harness_root),
    )

    # The cap on briefs, the enqueue and the ways a finding can be dropped on
    # the way to the queue are all `inspection.file_findings`'s, so this
    # producer files under exactly the terms the broad mode does — one brief
    # cap, one queue call, one set of named reasons — rather than under a
    # second copy of them. It is also why nothing here names the queue.
    filed, over = inspection.file_findings(
        target_root, result.found, bound.max_findings
    )
    report = inspection.Report(
        scopes=(scope,),
        invocations=1,
        filed=filed,
        dropped=tuple(result.dropped) + tuple(over),
        dedupe=(result.dedupe,) if result.dedupe is not None else (),
        cost_usd=result.cost_usd,
        scope_files=result.scope_files,
    )
    findings, filed_count, dropped_count = _counts(report)
    _say(
        run_dir, _summary(story_id, report, excluded, trimmed),
        findings=findings, filed=filed_count, dropped=dropped_count,
        # Carried from the invocation's own result through the scope result,
        # never re-derived: nothing here reads an agent log back for it. None
        # where the invocation reported nothing, which is how the record says
        # it was told no figure rather than saying the inspection was free.
        cost_usd=result.cost_usd, scope_files=result.scope_files,
        invocations=1,
    )
    # Beside the stage invocations in that run's own cost.json, and never added
    # to the allowance the ceiling reads. See `record_cost` for why the two are
    # different jobs.
    record_cost(run_dir, result.cost_usd)
    commit_record(target_root, config, story_id)
