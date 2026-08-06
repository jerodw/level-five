"""The Story Coordinator: deterministic execution of the story workflow.

The workflow definition says what should happen. The coordinator makes it
happen: it assembles context, invokes stage agents, saves state, and
routes execution from structured artifacts. It never reasons; every
decision here is a rule applied to a recorded fact.
"""
from __future__ import annotations

import json
import subprocess
import sys
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


def append_event(run_dir: Path, message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(run_dir / "events.log", "a", encoding="utf-8") as log:
        log.write(f"[{stamp}] {message}\n")
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


def _escalate(run_dir: Path, state: RunState, reason: str) -> int:
    state.status = "escalated"
    save_state(run_dir, state)
    append_event(run_dir, f"escalated: {reason}")
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
    append_event(run_dir, f"story completed on branch {state.branch}")
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
        print(
            f"{story_id} already ended with status '{state.status}'. "
            f"Inspect {run_dir} or delete it to run the story again.",
            file=sys.stderr,
        )
        return 1
    if state:
        append_event(run_dir, f"resumed at stage {state.current_stage}")
    else:
        branch = config.get("branch_prefix", "story/") + story_id
        state = RunState(story_id=story_id, branch=branch, current_stage=stage_names[0])
        save_state(run_dir, state)
        append_event(run_dir, f"workflow started for {story_id}")

    _checkout_story_branch(target_root, state.branch)

    index = stage_names.index(state.current_stage)
    while index < len(stages):
        stage = stages[index]
        name = stage["name"]
        state.current_stage = name
        save_state(run_dir, state)
        append_event(run_dir, f"{name} stage started")

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
            return _escalate(run_dir, state, f"{name} agent process failed")

        missing = [out for out in stage.get("outputs", []) if not (run_dir / out).is_file()]
        if missing:
            return _escalate(run_dir, state, f"{name} did not produce required artifacts: {', '.join(missing)}")

        violation = _schema_violation(run_dir, stage)
        if violation:
            return _escalate(run_dir, state, f"{name} wrote an invalid artifact: {violation}")

        record_name = stage.get("changed_files")
        if record_name:
            violation = _blocked_violation(run_dir, record_name, rules.get("blocked_paths", []))
            if violation:
                return _escalate(run_dir, state, f"{name} modified blocked path: {violation}")

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
                    )
            ownership = _ownership_violation(run_dir, record_name, enforced)
            if ownership:
                return _escalate(
                    run_dir,
                    state,
                    f"{name} created {ownership.path}, which it declared it "
                    f"must not create under {ownership.prefix}",
                )

        if name == "verifier":
            verdict = json.loads((run_dir / "verification-result.json").read_text(encoding="utf-8"))
            state.verification_iterations += 1
            archive = run_dir / "verification" / f"iteration-{state.verification_iterations}.json"
            archive.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
            if verdict.get("status") == "passed":
                append_event(run_dir, "verification passed")
            elif verdict.get("retry_recommended") and state.retry_count < rules["max_retries"]:
                state.retry_count += 1
                save_state(run_dir, state)
                append_event(
                    run_dir,
                    f"verification failed; retry {state.retry_count} of "
                    f"{rules['max_retries']} rerouted to {stage['on_failure']['retry_stage']}",
                )
                index = stage_names.index(stage["on_failure"]["retry_stage"])
                continue
            elif verdict.get("retry_recommended"):
                return _escalate(run_dir, state, "verification failed and retries are exhausted")
            else:
                return _escalate(run_dir, state, "verification failed and the verifier did not recommend a retry")
        else:
            append_event(run_dir, f"{name} stage completed")

        index += 1

    return _complete(run_dir, state, reading.parsed, target_root)
