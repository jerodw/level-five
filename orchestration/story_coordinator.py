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
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import agent_runner
import context_assembler
import harness_config
import schema_validator
import story_parser


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


def story_digest(story_text: str) -> str:
    """A digest of the story artifact exactly as the run was given it.

    Taken from the same text read_story was handed, so the digest and the
    reading describe one artifact. It is recorded on state.json at run start
    for one purpose: a refusal to resume can say whether the story has been
    amended since the run escalated. It never authorizes or triggers a resume —
    an incidental edit to a story must not silently restart a run.
    """
    return hashlib.sha256(story_text.encode("utf-8")).hexdigest()


def stage_restrictions(stages: list[dict]) -> list[tuple[str, str]]:
    """The workflow's (stage, prefix) create-restriction pairs, in declared order.

    The mapping from a stage to the prefixes it may not create under is
    derived here and nowhere else, so the exception cross-check below and the
    plan-time strictness check in plan_validation read one derivation rather
    than two copies of it. No stage name and no prefix is written here; both
    come off the loaded workflow definition.
    """
    return [
        (stage["name"], prefix)
        for stage in stages
        for prefix in stage.get("may_not_create", [])
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

    A granted value is accepted when it *equals* one of that stage's declared
    prefixes or falls *under* one of them, so a grant may name a single file,
    or a directory, beneath a declared prefix rather than only the whole
    prefix. Widening what is accepted narrows nothing: a whole-prefix grant
    keeps its meaning and its effect on both checks. A value beneath no
    declared prefix is still refused with the message it prints today — a
    grant that is no subset of something the stage was restricted on grants
    nothing — as is a stage the loaded workflow does not define.
    """
    restricted: dict[str, list[str]] = {stage["name"]: [] for stage in stages}
    for name, prefix in stage_restrictions(stages):
        restricted[name].append(prefix)
    problems = []
    for index, exception in enumerate(story.get("stage_exceptions", [])):
        name, granted = exception["stage"], exception["create"]
        if name not in restricted:
            problems.append(
                f"$.stage_exceptions[{index}]: names stage '{name}', which the "
                f"loaded workflow does not define"
            )
        elif not any(
            granted == prefix or granted.startswith(prefix)
            for prefix in restricted[name]
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
        if "max_self_routes" not in stage:
            continue
        budget = stage["max_self_routes"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            problems.append(
                f"stage '{stage['name']}' declares max_self_routes "
                f"{budget!r}, which is not a non-negative integer"
            )
    return problems


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
) -> None:
    """Append one event in both renderings, from one call.

    events.log stays exactly what it was — one `[timestamp] message` line,
    written from the prose message alone — because it is the format l5-status
    reads and the appendix documents. The structured keyword fields feed
    execution-history.json, the same events rendered for a reader that wants
    to route a query rather than read a stream. One write path is the point:
    a second one, however correct, is drift waiting to happen.

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
    }
    entry.update({key: value for key, value in optional.items() if value is not None})
    history.append(entry)
    _history_path(run_dir).write_text(
        json.dumps(history, indent=2) + "\n", encoding="utf-8"
    )

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
    configured = _git(target_root, "config", f"branch.{base}.remote")
    remote = configured.stdout.strip() if configured.returncode == 0 else ""
    if not remote:
        remotes = _git(target_root, "remote")
        if remotes.returncode != 0 or not remotes.stdout.split():
            return None
        remote = "origin" if "origin" in remotes.stdout.split() else remotes.stdout.split()[0]
    candidate = f"refs/remotes/{remote}/{base}"
    if _git(target_root, "rev-parse", "--verify", candidate).returncode != 0:
        return None
    return candidate


def base_problems(target_root: Path, base: str, declared: bool) -> list[str]:
    """What refuses a run or a plan that would cut a branch from `base`.

    The empty list is the whole of "go ahead". A declared base is checked for
    one thing only — that the ref resolves — because stating a base is stating
    a deliberate departure: branching one story from another's branch is the
    case it exists for, so neither leg below applies to it.

    Otherwise two legs, in this order, returning after the first that has
    something to say. Leg one is that HEAD is standing on the base, because a
    branch is cut from what is checked out. Leg two is that the base matches
    its remote-tracking counterpart in *either* direction — behind, ahead or
    diverged — because a branch cut from a local base that is not the shared
    one is a branch nobody else can see the history of. Only the first is
    printed when both hold: the second is not actionable until the first is
    fixed, and two refusals for one act read as two problems.

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
) -> None:
    exists = _git(target_root, "rev-parse", "--verify", branch).returncode == 0
    if exists:
        args = ["checkout", branch]
    else:
        # A declared base is branched from explicitly. With no declaration the
        # start point is HEAD, exactly as before, and the pre-flight above is
        # what establishes that HEAD is the base.
        args = ["checkout", "-b", branch] + ([start_point] if start_point else [])
    result = _git(target_root, *args)
    if result.returncode != 0:
        raise RuntimeError(f"Could not check out branch {branch}: {result.stderr.strip()}")


def _blocked_violation(run_dir: Path, record_name: str, blocked: list[str]) -> str | None:
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    for group in ("modified", "created", "deleted"):
        for path in changed.get(group, []):
            for prefix in blocked:
                if path.startswith(prefix):
                    return path
    return None


@dataclass(frozen=True)
class OwnershipViolation:
    """A path a stage created under a prefix it declared it must not create."""

    path: str
    prefix: str


def _ownership_violation(
    run_dir: Path, record_name: str, prefixes: list[str], granted: list[str]
) -> OwnershipViolation | None:
    """Hold a stage to the outputs it declared it does not own.

    Only the created array is checked. The line falls between creating and
    modifying because the rule is about independence, not directories: an
    implementer must be able to update an existing test whose call site its
    own signature change broke, but validation it authors itself checks what
    it built rather than what was asked.

    The enforced prefix list arrives whole; a path the story's grants cover is
    exempted rather than the prefix being removed, so a grant naming one path
    leaves every other path beneath the same prefix governed. Whether a grant
    covers a path is grant_covers's decision and no other's.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    for path in changed.get("created", []):
        if grant_covers(granted, path):
            continue
        for prefix in prefixes:
            if path.startswith(prefix):
                return OwnershipViolation(path, prefix)
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
    """Collect every stage artifact name the loaded workflow declares.

    The union of each stage's outputs, its changed_files record, and the keys
    of its schemas map — the same three places the coordinator already reads
    artifact names from. No artifact name is written here, so a workflow that
    adds a stage artifact gets it archived with no code change. Sorted, so
    the archive is deterministic.
    """
    names: set[str] = set()
    for stage in stages:
        names.update(stage.get("outputs", []))
        record = stage.get("changed_files")
        if record:
            names.add(record)
        names.update(stage.get("schemas", {}))
    return sorted(names)


def attempt_dir(run_dir: Path, attempt: int) -> Path:
    """Where one attempt's superseded artifacts are kept.

    The one place the archive directory is named. Both readers derive it from
    here: the archive that writes it, and the resume that refuses when it
    already exists rather than writing over the evidence in it.
    """
    return run_dir / "attempts" / f"attempt-{attempt}"


def archive_attempt(run_dir: Path, artifacts: list[str], attempt: int) -> list[str]:
    """Copy the superseded attempt's artifacts into attempts/attempt-N/.

    Evidence is not silently overwritten. The live copies stay at the
    run-directory root under their canonical names, where every reader
    expects them; the archive keeps those same names one directory down.
    An artifact the attempt did not write is skipped, the way an absent
    conditional artifact is skipped by _schema_violation. Returns the names
    actually archived.
    """
    destination = attempt_dir(run_dir, attempt)
    destination.mkdir(parents=True, exist_ok=True)
    archived = []
    for artifact in artifacts:
        source = run_dir / artifact
        if not source.is_file():
            continue
        shutil.copy2(source, destination / artifact)
        archived.append(artifact)
    return archived


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
    are read off it by self_route_artifacts. It is optional so a caller asking
    only what the workflow declares gets exactly what it got before.
    """
    names = archivable_artifacts(stages) + [
        prompt_file(stage["name"], attempt) for stage in stages
    ]
    if run_dir is not None:
        names += self_route_artifacts(run_dir, stages, attempt)
    return names


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


# --------------------------------------------------------------------------
# The clean-clone check
#
# The verifier runs the suite in the working tree, which is the one
# environment where the story's own commit does not exist yet: _complete
# commits the tree after every check the workflow performs. Everything below
# runs the same suite once more where the code actually ships — a fresh clone
# of the repository with the story committed into it — after the verifier
# passes and before the documenter runs.
# --------------------------------------------------------------------------

#: How much of the run's combined output the record keeps. Enough to identify
#: what failed; not the whole log, which the run directory is not a home for.
CLEAN_CLONE_OUTPUT_TAIL = 8000

_VERSION_PROBE = "import platform; print(platform.python_version())"
_VERSION = re.compile(r"\d+\.\d+\.\d+\S*")


@dataclass(frozen=True)
class CleanCloneResult:
    """What the clean-clone check did, as it is recorded in the run directory.

    Optional fields are expressed by absence in the written record rather than
    by null, matching the execution-history convention: a check that refused to
    run has no exit code and no output to report, only a reason.
    """

    ran: bool
    command: str
    python: str
    python_version: str | None = None
    clone_path: str | None = None
    exit_code: int | None = None
    output_tail: str | None = None
    reason: str | None = None

    def as_record(self) -> dict:
        record: dict = {"ran": self.ran, "command": self.command, "python": self.python}
        optional = {
            "python_version": self.python_version,
            "clone_path": self.clone_path,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
            "reason": self.reason,
        }
        record.update({key: value for key, value in optional.items() if value is not None})
        return record


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


def _interpreter_version(interpreter: Path) -> str | None:
    """What the interpreter reports its version to be, or None.

    None is not a failure: the configured test command need not be a Python
    interpreter at all, and a record with no version is honest about that. A
    *configured* clean_clone_python that does not exist is a different case,
    handled by the caller, because a check quietly testing the wrong version
    is worse than one that refuses.
    """
    try:
        result = subprocess.run(
            [str(interpreter), "-c", _VERSION_PROBE],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    version = result.stdout.strip()
    return version if result.returncode == 0 and _VERSION.fullmatch(version) else None


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
        for rel in revert:
            source, destination = baseline / rel, clone / rel
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif destination.is_file():
                destination.unlink()

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
    """
    linked = []
    for interpreter in interpreters:
        path = Path(interpreter)
        if path.is_absolute() or len(path.parts) < 2:
            continue
        source, destination = target_root / path.parts[0], clone / path.parts[0]
        if source.is_dir() and not destination.exists():
            destination.symlink_to(source, target_is_directory=True)
            linked.append(path.parts[0])
    if linked:
        exclude = clone / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as handle:
            handle.write("\n".join(["", *linked]) + "\n")


def run_clean_clone(
    target_root: Path,
    test_command: str,
    clean_clone_python: str | None,
    destination: Path,
    revert: list[str] | tuple[str, ...] = (),
    baseline: Path | None = None,
) -> CleanCloneResult:
    """Run the configured test command in a fresh clone with the story committed.

    The command is the target repository's own `test_command`; nothing about
    it is written here. Only its interpreter is substituted, and only when the
    configuration names a `clean_clone_python`, so the check can exercise the
    oldest supported Python rather than whichever one the developer works in.
    The caller owns `destination` and removes it whatever the result.

    This is the single build-a-clone-and-run-the-suite path. `revert` and the
    `baseline` it is restored from are passed through to the clone builder and
    default to reverting nothing, so the clean-clone check runs exactly as it
    did; the revert check is this same operation with the governed paths
    restored to the state the stage found them in rather than applied.
    """
    argv = shlex.split(test_command)
    interpreter = clean_clone_python or argv[0]
    command = shlex.join([interpreter, *argv[1:]])

    resolved = _resolve_interpreter(target_root, interpreter)
    if clean_clone_python and resolved is None:
        return CleanCloneResult(
            ran=False,
            command=command,
            python=interpreter,
            reason=(
                f"clean_clone_python names {clean_clone_python}, which is not an "
                f"interpreter that exists under {target_root}"
            ),
        )

    clone = destination / "clone"
    _build_clone(target_root, clone, revert=revert, baseline=baseline)
    _link_interpreter_roots(target_root, clone, [argv[0], interpreter])

    result = subprocess.run(
        [interpreter, *argv[1:]],
        cwd=clone,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return CleanCloneResult(
        ran=True,
        command=command,
        python=interpreter,
        python_version=_interpreter_version(resolved) if resolved else None,
        clone_path=str(clone),
        exit_code=result.returncode,
        output_tail=output[-CLEAN_CLONE_OUTPUT_TAIL:],
    )


def clean_clone_check(
    run_dir: Path, target_root: Path, config: dict, artifact: str
) -> CleanCloneResult:
    """Run the check in a scratch directory and record what it did.

    The clone is built under a temporary directory outside the target
    repository and removed once the run of the suite completes, whatever its
    result. The record stays: a reader can tell the check ran rather than
    inferring it from a pass.
    """
    scratch = Path(tempfile.mkdtemp(prefix="l5-clean-clone-"))
    try:
        result = run_clean_clone(
            target_root,
            config["test_command"],
            config.get("clean_clone_python"),
            scratch,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    (run_dir / artifact).write_text(
        json.dumps(result.as_record(), indent=2) + "\n", encoding="utf-8"
    )
    return result


def _clean_clone_passed(run_dir: Path, stage_name: str, artifact: str) -> None:
    append_event(
        run_dir,
        "clean-clone suite passed with the story committed",
        kind="clean-clone-passed",
        stage=stage_name,
        artifacts=[artifact],
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

    Not only for reading length. tests/test_story_011_validation.py proves its
    own non-vacuity by deleting the first `retry_decision="retry",` line at the
    verification-failed branch's indentation; an inline clean-clone branch
    nests deeper, its own line contains that same indented text, and it sits
    earlier in the file, so the mutation would silently land here instead of
    where it was aimed.
    """
    append_event(
        run_dir,
        f"clean-clone suite failed; retry {retry_count} of {max_retries} "
        f"rerouted to {destination}",
        kind="clean-clone-failed",
        stage=stage["name"],
        artifacts=[artifact],
        duration_seconds=duration_seconds,
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


def recorded_by_other_stages(
    run_dir: Path, stages: list[dict], stage_name: str
) -> set[str]:
    """Every repository path some *other* stage's changed-files record names.

    Whoever created a governed path is the fact the baseline merge turns on,
    and this is where the harness already records it: every writing stage
    declares the name of its own record in the workflow, so the records of
    every stage but this one are the account of what this stage did not write.

    The route a stage was entered by is not consulted and must not be: a resume
    can change several things between two entries, so it is a proxy for
    authorship rather than the fact itself.

    A record that is absent or unreadable contributes nothing rather than
    raising — the question is what another stage is *known* to have touched,
    and an answer that cannot be established is not one. It names no stage and
    no artifact; both come off the loaded workflow.
    """
    paths: set[str] = set()
    for stage in stages:
        record = stage.get("changed_files")
        if not record or stage["name"] == stage_name:
            continue
        try:
            changed = json.loads((run_dir / record).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for group in ("modified", "created", "deleted"):
            paths.update(changed.get(group, []))
    return paths


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
    """The stage's own modifications and deletions under its governed prefixes.

    `created` is not collected: the ownership check has already escalated on
    it, so anything reaching here is an edit to something that already existed.
    """

    paths: tuple[str, ...]
    prefixes: tuple[str, ...]


def governed_edits(
    run_dir: Path, record_name: str, prefixes: list[str], granted: list[str]
) -> GovernedEdits:
    """Read a stage's record for the edits the revert check decides on.

    Names no stage and no prefix; the caller passes the stage's enforced list
    whole and, beside it, the values the story grants that stage. A path a
    grant covers is skipped, so it is exempt from this check exactly as it is
    exempt from the ownership check, and by the same decision — grant_covers,
    which both read. Sorted, so the record and the escalation reason are
    deterministic.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    paths, matched = set(), set()
    for group in ("modified", "deleted"):
        for path in changed.get(group, []):
            if grant_covers(granted, path):
                continue
            for prefix in prefixes:
                if path.startswith(prefix):
                    paths.add(path)
                    matched.add(prefix)
    return GovernedEdits(tuple(sorted(paths)), tuple(sorted(matched)))


@dataclass(frozen=True)
class RevertCheckResult:
    """What the revert check did, as it is recorded in the run directory.

    `permitted` is absent from the record when the check did not run, following
    the optional-by-absence convention the other coordinator-written records
    use: a check that could not run decided nothing, and null would claim it
    decided something.
    """

    result: CleanCloneResult
    paths: tuple[str, ...]
    permitted: bool | None
    baseline: str | None = None

    def as_record(self) -> dict:
        record = {"ran": self.result.ran, "paths": list(self.paths)}
        record.update(
            {key: value for key, value in self.result.as_record().items() if key != "ran"}
        )
        if self.permitted is not None:
            record["permitted"] = self.permitted
        if self.baseline is not None:
            record["baseline"] = self.baseline
        return record


def revert_check(
    run_dir: Path,
    target_root: Path,
    config: dict,
    artifact: str,
    paths: tuple[str, ...],
    baseline: Path | None = None,
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
    that did not run, with the reason, rather than as a permission: a stage
    that declares the check with no baseline captured, and a clone that
    cannot be built at all.
    """
    command = config["test_command"]
    python = config.get("clean_clone_python") or shlex.split(command)[0]
    resolved = baseline if baseline is not None and baseline.is_dir() else None

    if resolved is None:
        result = CleanCloneResult(
            ran=False,
            command=command,
            python=python,
            reason=(
                "no baseline was captured for the stage, so there is no state "
                f"to revert the edits to: {baseline}"
            ),
        )
    else:
        scratch = Path(tempfile.mkdtemp(prefix="l5-revert-check-"))
        try:
            result = run_clean_clone(
                target_root,
                command,
                config.get("clean_clone_python"),
                scratch,
                revert=list(paths),
                baseline=resolved,
            )
        except (RuntimeError, OSError) as error:
            result = CleanCloneResult(
                ran=False,
                command=command,
                python=python,
                reason=f"the clone with the edits reverted could not be built: {error}",
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    decided = RevertCheckResult(
        result=result,
        paths=paths,
        permitted=(result.exit_code != 0) if result.ran else None,
        baseline=str(resolved) if resolved is not None else None,
    )
    (run_dir / artifact).write_text(
        json.dumps(decided.as_record(), indent=2) + "\n", encoding="utf-8"
    )
    return decided


def _revert_check_permitted(
    run_dir: Path, stage_name: str, artifact: str, edits: GovernedEdits
) -> None:
    append_event(
        run_dir,
        f"{stage_name} edits under {', '.join(edits.prefixes)} permitted: the "
        f"suite fails with {', '.join(edits.paths)} reverted",
        kind="revert-check-permitted",
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
# violations, stage output ownership, the revert check's two escalations and
# the clean-clone check's two.
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


def self_route_statement(failure: str, artifacts: list[str]) -> str:
    """What the coordinator tells a stage it is re-running, in its own words.

    Nothing here is an agent's judgement and the text says so. The failed
    process gets a different statement from the other two deliberately: there
    is no output to name, because the invocation did not reach the point of
    declaring what it had done, and what the stage most needs to know is that
    the tree already holds whatever that invocation wrote before it exited.
    """
    named = ", ".join(artifacts)
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
    return (
        f"The previous invocation of this stage left required output "
        f"unwritten: {named}. What sits at the run directory root under those "
        f"names is a previous attempt's, not that invocation's. This is not a "
        f"judgement about the work — no verifier saw it. Write the named "
        f"output afresh this time."
    )


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
) -> SelfRouteDecision:
    """Decide one mechanical failure, and record it when the stage runs again.

    The one decision behind all three mechanical failure sites, so they share a
    rule rather than repeating it three times. A stage that declares no budget
    escalates with exactly the reason it escalated with before this existed,
    which is what makes landing this change nothing until a workflow opts in.

    When there is budget left the count is incremented, the coordinator's own
    evidence is written under a name keyed by stage, attempt and try, one
    `self-routed` event is appended, and the state is saved so a crash mid-stage
    leaves a count a reader can trust. The budget comes off the loaded stage
    dict exactly as may_not_create and clean_clone do; no stage name and no
    budget value is written here.
    """
    name = stage["name"]
    budget = stage.get("max_self_routes", 0)
    if state.self_route_count >= budget:
        if budget:
            return SelfRouteDecision(
                False,
                f"{reason}; {name} has exhausted its self-route budget of "
                f"{budget}",
            )
        return SelfRouteDecision(False, reason)

    state.self_route_count += 1
    record: dict = {
        "stage": name,
        "attempt": attempt,
        "try": state.self_route_count,
        "failure": failure,
        "reason": reason,
        "statement": self_route_statement(failure, artifacts),
    }
    # Optional by absence, as clean-clone-result and execution-history already
    # are: a failed process names no artifact, and null would claim it named
    # none rather than that naming one does not apply.
    if artifacts:
        record["artifacts"] = list(artifacts)
    (run_dir / self_route_result_file(name, attempt, state.self_route_count)).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    append_event(
        run_dir,
        f"self-routed: {name} runs again in place ({reason}); self-route "
        f"{state.self_route_count} of {budget}",
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


def completion_commit_message(state: RunState, title: str) -> str:
    """The completion commit's message: the story it finished, and by what.

    Byte for byte what `_complete` composed inline before it was extracted.
    The subject names the story as a reader scanning the branch would want it
    named; the body is the single marker sentence, which is what makes the
    commit recognizable as a finished run's rather than as anyone's commit
    about the story.
    """
    return (
        f"{completion_commit_subject(state.story_id, title)}\n"
        f"\n"
        f"{COMPLETION_COMMIT_MARKER}"
    )


def completion_commits(target_root: Path, branch: str, story_id: str) -> list[str]:
    """Commits reachable from `branch` that a finished run of `story_id` made.

    One "<abbrev sha> <subject>" line per match, newest first, and an empty
    list when there is nothing to say. A commit qualifies on two pieces of
    evidence together: a subject of the completion shape for *this* story, and
    a body carrying `COMPLETION_COMMIT_MARKER`. Neither alone is enough — the
    subject shape is one a hand-written commit can wear, and the marker without
    the subject would report another story's run.

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
        prefix = completion_commit_subject(story_id, "")
        if not subject.startswith(prefix) or subject == prefix:
            continue
        if COMPLETION_COMMIT_MARKER not in body:
            continue
        found.append(f"{sha} {subject}")
    return found


def escalation_commit_message(state: RunState, reason: str) -> str:
    """The escalation commit's message: what it is, why, and how to undo it.

    The subject names the stage execution stopped at. The body says outright
    that this is a holding place rather than a decision about the work, carries
    the escalation reason, and names the command that returns the changes to
    the working tree.
    """
    stage = state.current_stage or "no stage"
    return (
        f"{ESCALATION_COMMIT_MARKER} {state.story_id} stopped at {stage}\n"
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
) -> str:
    """Open the escalation's commit of what the run left, and name it.

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
        escalation_commit_message(state, reason),
    )
    return _revision(target_root) if committed.returncode == 0 else ""


def commit_escalated_tree(target_root: Path, state: RunState, reason: str) -> None:
    """Commit the work the escalated run left, on top of its record.

    The second of the two commits, and the branch tip an escalation leaves. It
    carries the same message as the commit it sits on, because it is the same
    escalation: a reader scanning the branch should meet the escalation rather
    than a bookkeeping entry.
    """
    _git(target_root, "add", "-A")
    _git(
        target_root,
        "commit",
        "--allow-empty",
        "-m",
        escalation_commit_message(state, reason),
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
    blocks.append(
        f"Once you have made a change, `l5-run {state.story_id}` resumes this "
        f"run at the stage it stopped at ({state.current_stage}); `--stage "
        f"<stage>` overrides that and enters somewhere else. The resume is "
        f"refused while the story artifact, the branch and the harness are all "
        f"unchanged, because it would reach the same point the same way."
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

    The third comparison has two forms, decided by whether the harness and the
    target are one checkout. When they are separate — the deployment the guard
    was written for — it is the recorded-revision comparison, unchanged. When
    they are one tree, deferring to the branch comparison is what is sound:
    leg two has already established that the branch is exactly the escalation
    commit with nothing uncommitted, and under one tree that covers every
    change to the harness source, so the recorded revision would answer a
    question already answered. It could not answer it anyway — the revision is
    recorded as the first act of `_escalate`, before the two escalation commits
    move HEAD, so a shared-root comparison of it always differs and the guard
    can never refuse.

    A tree hash over the harness's own source directories was considered and
    not taken. It is a second definition of "has the harness changed" living
    beside the branch comparison that already answers it, and it would have to
    choose which directories count — a choice nothing else in the harness
    makes.
    """
    if not state.story_digest or state.story_digest != story_digest(story_text):
        return []
    evidence = [
        "the story artifact is byte for byte the one the escalated run read"
    ]

    # The escalation commit is the branch tip's parent, not the tip: the record
    # of its sha is committed on top of it, because a commit cannot carry its
    # own sha. A branch a developer has committed on since therefore fails this
    # comparison, which is what it is for.
    if (
        not state.escalation_commit
        or _revision(target_root, "HEAD~1") != state.escalation_commit
    ):
        return []
    porcelain = _git(target_root, "status", "--porcelain")
    if porcelain.returncode != 0 or porcelain.stdout.strip():
        return []
    evidence.append(
        f"branch {state.branch} is exactly the escalation commit "
        f"{state.escalation_commit[:12]}, with nothing uncommitted"
    )

    # Under one checkout the branch comparison above is the harness comparison:
    # it established that the branch is exactly the escalation commit with
    # nothing uncommitted, which covers every change to the harness source. The
    # recorded revision cannot serve here — it is recorded before the escalation
    # commits move HEAD, so it always differs — and a tree hash over the
    # harness's source directories was rejected rather than reached for: it is a
    # second definition of "has the harness changed" beside the one already
    # answering it, and it would have to choose which directories count.
    if same_repository(target_root, harness_root):
        evidence.append(
            "the harness is the same checkout as the target, so it is covered "
            "by the branch comparison above"
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
        "Fix the workflow definition's max_self_routes before running a story "
        "under it.",
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


def _complete(run_dir: Path, state: RunState, story: dict, target_root: Path) -> int:
    state.status = "completed"
    state.current_stage = ""
    save_state(run_dir, state)
    # The schema marks title required and the run cannot reach here without
    # having passed validation, so a missing title is a loud failure rather
    # than a silently empty report.
    title = story["story"]["title"]
    report = (
        f"# {state.story_id} Completion Report\n\n"
        f"## Story\n{title}\n\n"
        f"## Outcome\nCompleted on branch {state.branch} after "
        f"{state.retry_count} retr{'y' if state.retry_count == 1 else 'ies'}.\n\n"
        f"## Evidence\n"
        f"- test-results.json\n"
        f"- verification/iteration-{state.verification_iterations}.json (passed)\n"
        f"- documentation-report.md\n\n"
        f"## Next Step\nReview the branch and merge it when you accept the story.\n"
    )
    (run_dir / "completion-report.md").write_text(report, encoding="utf-8")
    _git(target_root, "add", "-A")
    _git(target_root, "commit", "-m", completion_commit_message(state, title))
    append_event(
        run_dir,
        f"story completed on branch {state.branch}",
        kind="story-completed",
    )
    return 0


def run_story(
    story_id: str,
    harness_root: Path,
    target_root: Path,
    runner=agent_runner.run_agent,
    start_stage: str | None = None,
    *,
    base: str | None = None,
) -> int:
    """Execute one story, from a fresh run or from where a run left off.

    `start_stage` overrides where execution enters — the recorded stage on a
    resume, the workflow's first stage on a fresh run. It is named
    `start_stage` rather than `stage` because `stage` is the loop's name for
    the stage being executed, and one name for two things is how this
    repository has repeatedly confused itself.

    `base` declares what a *new* story branch is cut from. None is the normal
    case and means the repository's own default branch; see `resolve_base`.
    """
    config = harness_config.load_config(target_root)
    workflow = harness_config.load_workflow(harness_root, config.get("workflow", "story-workflow"))
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

    story_path = target_root / config.get("stories_dir", ".harness/stories") / f"{story_id}.yaml"
    if not story_path.is_file():
        print(f"No story artifact at {story_path}. Run l5-plan first.", file=sys.stderr)
        return 1
    story_text = story_path.read_text(encoding="utf-8")

    # Pre-flight: refuse a bad story before any run state exists, so a
    # rejection leaves no run directory, no state.json, and no new branch.
    # The schema ships with the harness code, so it is resolved by
    # schema_validator relative to its own module, not from harness_root.
    reading = read_story(story_text)
    if reading.problems:
        return refuse_bad_story(story_path, reading.problems)

    # Conformance is one question, agreement with this workflow another. A
    # stage exception is checked here rather than inside read_story so schema
    # reading stays schema reading, and both refusals stay above run-directory
    # creation.
    exception_problems = stage_exception_problems(reading.parsed, stages)
    if exception_problems:
        return refuse_bad_story(story_path, exception_problems)

    run_dir = target_root / config.get("runs_dir", ".harness/runs") / story_id
    state = load_state(run_dir)

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
    if not branch_existed:
        problems = base_problems(target_root, resolved_base, base is not None)
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
    if state is None or state.status == "escalated":
        dirty = dirty_paths(target_root)
        if dirty:
            return _refuse_dirty_tree(target_root, dirty)

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "verification").mkdir(exist_ok=True)
    log_path = target_root / config.get("logs_dir", ".harness/logs") / f"{story_id}.log"

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
    if state:
        # A resume, of a crashed run or an escalated one. Chapter 18 treats
        # the two identically and so does this: restore nothing, because the
        # artifacts and the state are already here, and continue at the
        # recorded stage. Nothing is reinitialized — retry_count and
        # verification_iterations key the rendered prompt and verification
        # iteration filenames, so resetting them would overwrite the evidence
        # of the attempt being resumed.
        if state.status == "escalated":
            # Resuming is inferred from the recorded status and from nothing
            # else. What the guard adds is a refusal in the one case where a
            # resume is knowably pointless: the story, the tree and the harness
            # are all establishably what they were when the run escalated.
            evidence = unchanged_since_escalation(
                state, story_text, target_root, harness_root
            )
            if evidence:
                print(
                    _resume_refusal(story_path, run_dir, state, evidence),
                    file=sys.stderr,
                )
                return 1
        if start_stage:
            state.current_stage = start_stage
        # A resumed stage starts with its full self-route budget. The count is
        # the live count for one stage invocation and nothing carries it across
        # a resume: the stage is being entered afresh, not re-running itself.
        state.self_route_count = 0
        if state.status == "escalated":
            # The interrupted attempt is archived before the resumed stage
            # runs, under the attempt number it was written with. Refuse rather
            # than overwrite: the archive is the evidence a resume exists to
            # preserve, and story-010 recorded exactly this case as open.
            attempt = state.retry_count + 1
            destination = attempt_dir(run_dir, attempt)
            if destination.exists():
                print(
                    f"{destination} already holds an archived attempt, and "
                    f"resuming {story_id} would write attempt {attempt} over "
                    f"it. Move or remove it if that attempt is not worth "
                    f"keeping, then run the story again.",
                    file=sys.stderr,
                )
                return 1
            archive_attempt(
                run_dir,
                interrupted_attempt_artifacts(stages, attempt, run_dir=run_dir),
                attempt,
            )
            state.status = "running"
            save_state(run_dir, state)
        append_event(
            run_dir,
            f"resumed at stage {state.current_stage}",
            kind="resumed",
            stage=state.current_stage,
        )
    else:
        state = RunState(
            story_id=story_id,
            branch=story_branch(config, story_id),
            current_stage=start_stage or stage_names[0],
            # Recorded from the same text read_story was given, so the digest
            # and the run's one reading describe one artifact.
            story_digest=story_digest(story_text),
        )
        save_state(run_dir, state)
        append_event(
            run_dir, f"workflow started for {story_id}", kind="workflow-started"
        )

    # A declared base is where the new branch is cut from. Undeclared, the
    # start point stays HEAD exactly as it was, and the pre-flight above is
    # what establishes HEAD is the base.
    start_point = resolved_base if base is not None else None
    _checkout_story_branch(target_root, state.branch, start_point)

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
        self_routed = False
        save_state(run_dir, state)
        stage_started_at = time.monotonic()
        append_event(run_dir, f"{name} stage started", kind="stage-started", stage=name)

        attempt = state.retry_count + 1

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
            allowed_tools=config.get("allowed_tools"),
            self_route_result=self_route_result,
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
        declaration = stage.get("revert_check") or {}
        baseline_dir = (
            capture_stage_baseline(
                run_dir,
                target_root,
                declaration["baseline"],
                name,
                stage.get("may_not_create", []),
                accounted_for=recorded_by_other_stages(run_dir, stages, name),
            )
            if declaration
            else None
        )

        result = runner(
            prompt,
            stage=name,
            cwd=target_root,
            log_path=log_path,
            permission_mode=config.get("permission_mode", "acceptEdits"),
            model=config.get("model"),
            allowed_tools=config.get("allowed_tools"),
        )
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
            # The enforced list stays whole; a grant is an exemption on the
            # paths it covers rather than the removal of a prefix, so a story
            # granting one file beneath a prefix leaves the rest of that
            # prefix governed. One event per grant whatever its granularity.
            enforced = list(stage.get("may_not_create", []))
            exempt = granted_paths(reading.parsed, name)
            for granted in exempt:
                append_event(
                    run_dir,
                    f"stage exception applied: {name} may create {granted}",
                    kind="stage-exception-applied",
                    stage=name,
                )
            ownership = _ownership_violation(run_dir, record_name, enforced, exempt)
            if ownership:
                return _escalate(
                    run_dir,
                    state,
                    f"{name} created {ownership.path}, which it declared it "
                    f"must not create under {ownership.prefix}",
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
                prefixes = ", ".join(edits.prefixes)
                listed = ", ".join(edits.paths)
                decided = revert_check(
                    run_dir,
                    target_root,
                    config,
                    revert_artifact,
                    edits.paths,
                    baseline_dir,
                )
                if not decided.result.ran:
                    return _escalate(
                        run_dir,
                        state,
                        f"the revert check on {name}'s edits ({listed}) under "
                        f"{prefixes} could not run: {decided.result.reason}",
                        target_root=target_root,
                        harness_root=harness_root,
                        duration_seconds=elapsed(),
                    )
                if not decided.permitted:
                    return _escalate(
                        run_dir,
                        state,
                        f"{name} edited {listed} under {prefixes}, which it "
                        f"declared it must not create under, and the suite "
                        f"still passes with those edits reverted",
                        target_root=target_root,
                        harness_root=harness_root,
                        duration_seconds=elapsed(),
                    )
                _revert_check_permitted(run_dir, name, revert_artifact, edits)

        if name == "verifier":
            verdict = json.loads((run_dir / "verification-result.json").read_text(encoding="utf-8"))
            state.verification_iterations += 1
            archive = run_dir / "verification" / f"iteration-{state.verification_iterations}.json"
            archive.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
            # The artifacts an entry names come off the stage's declared
            # outputs in the loaded workflow, never a list written here.
            outputs = stage.get("outputs", [])
            # Where a failed verification goes is read off the table this
            # stage declares, keyed on the category the verifier reported.
            # There is no default: a recommended retry naming no category, or
            # one the table does not define, escalates below rather than being
            # absorbed by a fallback route — a silent fallback is the drift the
            # table exists to remove. No category name appears here.
            routes = stage.get("on_failure", {}).get("retry_routing", {})
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
                    verifier_outcome=verdict["status"],
                )

                # The suite passed where the verifier stood; run it once more
                # where the code ships. The artifact name comes off the loaded
                # workflow definition, so removing that declaration disables
                # the check with no change here.
                # Both names come off the one declaration: the result the
                # check writes and the stage a failure routes to. One key
                # still turns the whole check on, so removing the declaration
                # disables it with no change here.
                clean_clone = stage.get("clean_clone") or {}
                artifact = clean_clone.get("result")
                if artifact:
                    clean = clean_clone_check(run_dir, target_root, config, artifact)
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
                        append_retry_record(
                            run_dir,
                            state.retry_count + 1,
                            destination,
                            verdict,
                            artifacts_written_since(
                                run_dir, conditional, artifacts_before
                            ),
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
                            elapsed(),
                        )
                        # A clean-clone failure carries no category: nothing
                        # chose it, the declaration names the route outright.
                        routed_category, routed_stage = None, destination
                        index = stage_names.index(destination)
                        continue
                    _clean_clone_passed(run_dir, name, artifact)
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
                append_retry_record(
                    run_dir,
                    state.retry_count + 1,
                    destination,
                    verdict,
                    artifacts_written_since(run_dir, conditional, artifacts_before),
                )
                state.retry_count += 1
                save_state(run_dir, state)
                append_event(
                    run_dir,
                    f"verification failed; retry {state.retry_count} of "
                    f"{rules['max_retries']} rerouted to {destination} "
                    f"for {target}",
                    kind="verification-failed",
                    stage=name,
                    artifacts=outputs,
                    duration_seconds=elapsed(),
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
            )

        index += 1

    return _complete(run_dir, state, reading.parsed, target_root)
