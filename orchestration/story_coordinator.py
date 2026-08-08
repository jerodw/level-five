"""The Story Coordinator: deterministic execution of the story workflow.

The workflow definition says what should happen. The coordinator makes it
happen: it assembles context, invokes stage agents, saves state, and
routes execution from structured artifacts. It never reasons; every
decision here is a rule applied to a recorded fact.
"""
from __future__ import annotations

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


def archive_attempt(run_dir: Path, artifacts: list[str], attempt: int) -> list[str]:
    """Copy the superseded attempt's artifacts into attempts/attempt-N/.

    Evidence is not silently overwritten. The live copies stay at the
    run-directory root under their canonical names, where every reader
    expects them; the archive keeps those same names one directory down.
    An artifact the attempt did not write is skipped, the way an absent
    conditional artifact is skipped by _schema_violation. Returns the names
    actually archived.
    """
    destination = run_dir / "attempts" / f"attempt-{attempt}"
    destination.mkdir(parents=True, exist_ok=True)
    archived = []
    for artifact in artifacts:
        source = run_dir / artifact
        if not source.is_file():
            continue
        shutil.copy2(source, destination / artifact)
        archived.append(artifact)
    return archived


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


def _build_clone(target_root: Path, clone: Path) -> None:
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
) -> CleanCloneResult:
    """Run the configured test command in a fresh clone with the story committed.

    The command is the target repository's own `test_command`; nothing about
    it is written here. Only its interpreter is substituted, and only when the
    configuration names a `clean_clone_python`, so the check can exercise the
    oldest supported Python rather than whichever one the developer works in.
    The caller owns `destination` and removes it whatever the result.
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
    _build_clone(target_root, clone)
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


def _escalate(run_dir: Path, state: RunState, reason: str, **event_fields) -> int:
    """End the run, recording the escalation in both renderings.

    A run that failed must be as reconstructable as one that passed, so the
    history ends with this entry. Callers forward whatever structured fields
    the escalation has — the stage's elapsed time, the verifier's outcome,
    the decision that routed here — through event_fields.
    """
    state.status = "escalated"
    save_state(run_dir, state)
    append_event(
        run_dir,
        f"escalated: {reason}",
        kind="escalated",
        stage=state.current_stage or None,
        **event_fields,
    )
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
) -> int:
    config = harness_config.load_config(target_root)
    workflow = harness_config.load_workflow(harness_root, config.get("workflow", "story-workflow"))
    rules = harness_config.load_rules(harness_root)
    stages = workflow["stages"]
    stage_names = [s["name"] for s in stages]

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
    if state and state.status != "running":
        # Name the branch as well as the run directory. _checkout_story_branch
        # reuses an existing branch rather than resetting it, so deleting only
        # the run directory re-runs the story on top of the finished work — the
        # implementer opens a repository where the story is already done, and
        # the run reports success having changed nothing.
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
        append_event(
            run_dir,
            f"resumed at stage {state.current_stage}",
            kind="resumed",
            stage=state.current_stage,
        )
    else:
        branch = config.get("branch_prefix", "story/") + story_id
        state = RunState(story_id=story_id, branch=branch, current_stage=stage_names[0])
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
                run_dir, state, f"{name} agent process failed", duration_seconds=elapsed()
            )

        missing = [out for out in stage.get("outputs", []) if not (run_dir / out).is_file()]
        if missing:
            return _escalate(
                run_dir,
                state,
                f"{name} did not produce required artifacts: {', '.join(missing)}",
                duration_seconds=elapsed(),
            )

        violation = _schema_violation(run_dir, stage)
        if violation:
            return _escalate(
                run_dir,
                state,
                f"{name} wrote an invalid artifact: {violation}",
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
                    duration_seconds=elapsed(),
                )

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
