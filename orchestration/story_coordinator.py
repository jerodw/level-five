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


def stage_exception_problems(story: dict, stages: list[dict]) -> list[str]:
    """Cross-check a story's stage exceptions against the loaded workflow.

    read_story answers whether a story conforms to its schema. This answers
    the separate question the schema cannot: whether an exception means
    anything against the workflow this run actually loaded. An exception
    naming a stage that does not exist, or granting a path its stage was
    never restricted on, grants nothing — a planning error rather than a
    harmless one, so both refuse the run. Stage names and prefixes come from
    the workflow definition; none is named here.
    """
    restricted = {stage["name"]: stage.get("may_not_create", []) for stage in stages}
    problems = []
    for index, exception in enumerate(story.get("stage_exceptions", [])):
        name, granted = exception["stage"], exception["create"]
        if name not in restricted:
            problems.append(
                f"$.stage_exceptions[{index}]: names stage '{name}', which the "
                f"loaded workflow does not define"
            )
        elif granted not in restricted[name]:
            problems.append(
                f"$.stage_exceptions[{index}]: grants '{granted}' to stage "
                f"'{name}', which was never restricted from creating it"
            )
    return problems


def _granted_prefixes(story: dict, stage_name: str) -> list[str]:
    return [
        exception["create"]
        for exception in story.get("stage_exceptions", [])
        if exception["stage"] == stage_name
    ]


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


def _checkout_story_branch(target_root: Path, branch: str) -> None:
    exists = _git(target_root, "rev-parse", "--verify", branch).returncode == 0
    args = ["checkout", branch] if exists else ["checkout", "-b", branch]
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
    run_dir: Path, record_name: str, prefixes: list[str]
) -> OwnershipViolation | None:
    """Hold a stage to the outputs it declared it does not own.

    Only the created array is checked. The line falls between creating and
    modifying because the rule is about independence, not directories: an
    implementer must be able to update an existing test whose call site its
    own signature change broke, but validation it authors itself checks what
    it built rather than what was asked.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    for path in changed.get("created", []):
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


def interrupted_attempt_artifacts(stages: list[dict], attempt: int) -> list[str]:
    """What a resumed run would write over if the interrupted attempt stayed.

    The stage artifacts archive_attempt already derives from the workflow, plus
    that attempt's rendered prompts. The prompts are the addition a resume
    needs: a resumed run carries retry_count forward — it must, since resetting
    it would overwrite the escalated attempt's verification iteration — so it
    re-renders under the same attempt number and would write over the prompt
    the interrupted stage was actually given. No stage name and no artifact
    name is written here; both come off the loaded workflow.
    """
    return archivable_artifacts(stages) + [
        f"prompt-{stage['name']}-attempt-{attempt}.md" for stage in stages
    ]


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
        ["git", "clone", "--quiet", "--no-hardlinks", str(target_root), str(clone)],
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
        f"rerouted to {stage['on_failure']['retry_stage']}",
        kind="clean-clone-failed",
        stage=stage["name"],
        artifacts=[artifact],
        duration_seconds=duration_seconds,
        retry_decision="retry",
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


def stage_baseline_dir(
    run_dir: Path, baseline: str, stage_name: str, attempt: int
) -> Path:
    """Where one stage's pre-stage content for one attempt is kept.

    The directory name comes off the loaded workflow declaration; only the
    keying by stage and attempt is written here, and it is the same attempt
    number the rendered prompt filename and `attempts/attempt-N/` use.
    """
    return run_dir / baseline / f"{stage_name}-attempt-{attempt}"


def capture_stage_baseline(
    run_dir: Path,
    target_root: Path,
    baseline: str,
    stage_name: str,
    attempt: int,
    prefixes: list[str],
) -> Path:
    """Record what the tree held under a stage's governed prefixes before it ran.

    The file set is `git ls-files --cached --others --exclude-standard` under
    each prefix — the same tracked-plus-untracked set `_build_clone` carries
    into a clone — so a file an earlier stage of this run created and never
    committed is captured. Tracked files alone would miss exactly that file,
    which is the whole reason this exists.

    Capture once, reuse afterwards: a directory already recorded for this stage
    and attempt is returned untouched, so a re-entered stage is decided against
    the state it originally found rather than against its own completed edits.
    The directory is created even when it captures nothing, so its existence
    answers "was a baseline taken" and its absence is a distinct, reportable
    condition rather than an empty capture.

    It names no stage and no prefix: both come from the loaded workflow. The
    result is evidence — nothing routes on it, and it is not in state.json.
    """
    directory = stage_baseline_dir(run_dir, baseline, stage_name, attempt)
    if directory.exists():
        return directory
    directory.mkdir(parents=True)
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
    run_dir: Path, record_name: str, prefixes: list[str]
) -> GovernedEdits:
    """Read a stage's record for the edits the revert check decides on.

    Names no stage and no prefix; the caller passes the enforced list it has
    already narrowed by the story's grants. Sorted, so the record and the
    escalation reason are deterministic.
    """
    changed = json.loads((run_dir / record_name).read_text(encoding="utf-8"))
    paths, matched = set(), set()
    for group in ("modified", "deleted"):
        for path in changed.get(group, []):
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
# Ending a run so it can be resumed
#
# A successful run's work is durable: _complete commits it. An escalated run's
# was not, so it lived in the working tree and survived exactly until someone
# checked out another branch — a normal thing to do while deciding what to do
# about an escalation. Resume cannot recover what the harness did not preserve,
# so the escalation commits too.
#
# It commits with the same looseness _complete has, and the limit is stated
# rather than closed here: `git add -A` stages whatever is in the working tree,
# not what the run produced, and an escalation does that on a tree that is by
# definition unfinished. Closing it is a separate story, deliberately after
# this one.
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


def _refuse(story_path: Path, problems: list[str]) -> int:
    """The one pre-flight refusal path: exit 1, one message per problem."""
    print(f"{story_path} is not a valid story artifact:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "Fix the artifact or re-run planning before executing the story.",
        file=sys.stderr,
    )
    return 1


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
    summary = (
        f"# {state.story_id} Escalation Summary\n\n"
        f"## Status\nEscalated\n\n"
        f"## Reason\n{reason}\n\n"
        f"## Where Execution Stopped\nStage: {state.current_stage}, "
        f"retry count: {state.retry_count}\n\n"
        f"## Where to Look\nSee events.log for the run history and the "
        f"verification/ directory for verifier findings.\n"
    )
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
    _git(target_root, "commit", "-m", f"{state.story_id}: {title}\n\nImplemented by the l5 harness story workflow.")
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
) -> int:
    """Execute one story, from a fresh run or from where a run left off.

    `start_stage` overrides where execution enters — the recorded stage on a
    resume, the workflow's first stage on a fresh run. It is named
    `start_stage` rather than `stage` because `stage` is the loop's name for
    the stage being executed, and one name for two things is how this
    repository has repeatedly confused itself.
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
        return _refuse(story_path, reading.problems)

    # Conformance is one question, agreement with this workflow another. A
    # stage exception is checked here rather than inside read_story so schema
    # reading stays schema reading, and both refusals stay above run-directory
    # creation.
    exception_problems = stage_exception_problems(reading.parsed, stages)
    if exception_problems:
        return _refuse(story_path, exception_problems)

    run_dir = target_root / config.get("runs_dir", ".harness/runs") / story_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "verification").mkdir(exist_ok=True)
    log_path = target_root / config.get("logs_dir", ".harness/logs") / f"{story_id}.log"

    state = load_state(run_dir)
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
                run_dir, interrupted_attempt_artifacts(stages, attempt), attempt
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
        branch = config.get("branch_prefix", "story/") + story_id
        state = RunState(
            story_id=story_id,
            branch=branch,
            current_stage=start_stage or stage_names[0],
            # Recorded from the same text read_story was given, so the digest
            # and the run's one reading describe one artifact.
            story_digest=story_digest(story_text),
        )
        save_state(run_dir, state)
        append_event(
            run_dir, f"workflow started for {story_id}", kind="workflow-started"
        )

    _checkout_story_branch(target_root, state.branch)

    # Stage timing: started where the stage-started event is appended, read at
    # whichever event ends the stage, so a completed stage carries an elapsed
    # duration the log only made derivable.
    stage_started_at: float | None = None

    def elapsed() -> float | None:
        if stage_started_at is None:
            return None
        return round(time.monotonic() - stage_started_at, 3)

    index = stage_names.index(state.current_stage)
    while index < len(stages):
        stage = stages[index]
        name = stage["name"]
        state.current_stage = name
        save_state(run_dir, state)
        stage_started_at = time.monotonic()
        append_event(run_dir, f"{name} stage started", kind="stage-started", stage=name)

        context = context_assembler.build_context(
            story_text=story_text,
            story=reading.parsed,
            run_dir=run_dir,
            target_root=target_root,
            harness_root=harness_root,
            config=config,
            rules=rules,
            retry_count=state.retry_count,
        )
        template = context_assembler.load_template(harness_root, stage["prompt"])
        prompt = context_assembler.render(template, context)
        attempt = state.retry_count + 1
        (run_dir / f"prompt-{name}-attempt-{attempt}.md").write_text(prompt, encoding="utf-8")

        # What the stage's conditional artifacts looked like before it ran, so
        # a retry record can tell the guidance this attempt wrote from one an
        # earlier attempt left at the run root.
        conditional = conditional_artifacts(stage)
        conditional_before = artifact_signatures(run_dir, conditional)

        # What the tree held under this stage's governed prefixes before it
        # ran, which is the baseline the revert check below decides against.
        # Both names come off the stage's declaration, so removing that one
        # key disables the capture and the check together. Captured over the
        # declared prefixes rather than the grant-subtracted list: the
        # enforced list is computed after the stage, and capturing a superset
        # costs a few file copies while the restore set stays narrowed.
        declaration = stage.get("revert_check") or {}
        baseline_dir = (
            capture_stage_baseline(
                run_dir,
                target_root,
                declaration["baseline"],
                name,
                attempt,
                stage.get("may_not_create", []),
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
        if not result.ok:
            return _escalate(
                run_dir,
                state,
                f"{name} agent process failed",
                target_root=target_root,
                harness_root=harness_root,
                duration_seconds=elapsed(),
            )

        missing = [out for out in stage.get("outputs", []) if not (run_dir / out).is_file()]
        if missing:
            return _escalate(
                run_dir,
                state,
                f"{name} did not produce required artifacts: {', '.join(missing)}",
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
            enforced = list(stage.get("may_not_create", []))
            for granted in _granted_prefixes(reading.parsed, name):
                if granted in enforced:
                    enforced.remove(granted)
                    append_event(
                        run_dir,
                        f"stage exception applied: {name} may create {granted}",
                        kind="stage-exception-applied",
                        stage=name,
                    )
            ownership = _ownership_violation(run_dir, record_name, enforced)
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

            # The revert check, on the same record and the same enforced
            # prefixes the ownership check just used — the one record whose
            # edits under those prefixes are known to be this stage's alone.
            # The artifact name comes off the loaded workflow definition, so
            # removing that declaration disables the check with no change here.
            # It is the same declaration the baseline was captured under, read
            # here for the other name it carries.
            revert_artifact = declaration.get("result")
            edits = (
                governed_edits(run_dir, record_name, enforced)
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
                artifact = stage.get("clean_clone")
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
                        archive_attempt(
                            run_dir, archivable_artifacts(stages), state.retry_count + 1
                        )
                        append_retry_record(
                            run_dir,
                            state.retry_count + 1,
                            stage["on_failure"]["retry_stage"],
                            verdict,
                            artifacts_written_since(
                                run_dir, conditional, conditional_before
                            ),
                        )
                        state.retry_count += 1
                        save_state(run_dir, state)
                        _clean_clone_failed(
                            run_dir,
                            stage,
                            artifact,
                            state.retry_count,
                            rules["max_retries"],
                            elapsed(),
                        )
                        index = stage_names.index(stage["on_failure"]["retry_stage"])
                        continue
                    _clean_clone_passed(run_dir, name, artifact)
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
                append_retry_record(
                    run_dir,
                    state.retry_count + 1,
                    stage["on_failure"]["retry_stage"],
                    verdict,
                    artifacts_written_since(run_dir, conditional, conditional_before),
                )
                state.retry_count += 1
                save_state(run_dir, state)
                append_event(
                    run_dir,
                    f"verification failed; retry {state.retry_count} of "
                    f"{rules['max_retries']} rerouted to {stage['on_failure']['retry_stage']}",
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
                )
                index = stage_names.index(stage["on_failure"]["retry_stage"])
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
