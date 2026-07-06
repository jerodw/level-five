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


@dataclass
class RunState:
    story_id: str
    branch: str
    status: str = "running"
    current_stage: str = ""
    retry_count: int = 0
    verification_iterations: int = 0
    artifacts: list[str] = field(default_factory=list)


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


def _blocked_violation(run_dir: Path, blocked: list[str]) -> str | None:
    changed = json.loads((run_dir / "changed-files.json").read_text(encoding="utf-8"))
    for group in ("modified", "created", "deleted"):
        for path in changed.get(group, []):
            for prefix in blocked:
                if path.startswith(prefix):
                    return path
    return None


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


def _complete(run_dir: Path, state: RunState, story_text: str, target_root: Path) -> int:
    state.status = "completed"
    state.current_stage = ""
    save_state(run_dir, state)
    title = ""
    for line in story_text.splitlines():
        if line.strip().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            break
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
            run_dir=run_dir,
            target_root=target_root,
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
        )
        if not result.ok:
            return _escalate(run_dir, state, f"{name} agent process failed")

        missing = [out for out in stage.get("outputs", []) if not (run_dir / out).is_file()]
        if missing:
            return _escalate(run_dir, state, f"{name} did not produce required artifacts: {', '.join(missing)}")

        if name == "implementer":
            violation = _blocked_violation(run_dir, rules.get("blocked_paths", []))
            if violation:
                return _escalate(run_dir, state, f"implementer modified blocked path: {violation}")

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

    return _complete(run_dir, state, story_text, target_root)
