"""Invoke a coding agent headlessly and capture its final result.

This is the only module in the harness that talks to a model. The
coordinator injects a runner function, so tests substitute a fake runner
and never invoke a model.
"""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

# The hooks ship with the harness code, like the schemas, so they are resolved
# relative to this module rather than to a caller-supplied root.
HARNESS_ROOT = Path(__file__).resolve().parents[1]

SETTINGS_NAME = "settings.json"
GUARD_NAME = "bash_guard.py"
GUARD_PLACEHOLDER = "{guard_path}"


@dataclass
class AgentResult:
    ok: bool
    result_text: str


def hooks_dir(harness_root: Path | None = None) -> Path:
    return (harness_root or HARNESS_ROOT) / "hooks"


def guard_settings(harness_root: Path | None = None) -> str | None:
    """The shipped hook declaration, with the guard's own path resolved.

    The declaration's shape lives in hooks/settings.json, a data file, because
    the harness root varies by installation and only the absolute path can be
    computed here. An absent or unreadable declaration returns None and the
    stage runs without the hook: the guard is the net behind the allowlist,
    which is the gate, so failing to register it must not stop a run.
    """
    directory = hooks_dir(harness_root)
    guard = directory / GUARD_NAME
    try:
        declaration = (directory / SETTINGS_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    if not guard.is_file():
        return None
    return declaration.replace(GUARD_PLACEHOLDER, str(guard))


def run_agent(
    prompt: str,
    *,
    stage: str,
    cwd: Path,
    log_path: Path,
    permission_mode: str = "acceptEdits",
    model: str | None = None,
    allowed_tools: list[str] | None = None,
) -> AgentResult:
    """Run `claude -p` with the rendered prompt on stdin.

    Raw stream-json output is appended to log_path so every run remains
    inspectable after the fact. The agent's final result message is
    returned to the coordinator.
    """
    cmd = [
        "claude",
        "-p",
        "--permission-mode",
        permission_mode,
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    # Every stage invocation carries the deny-only Bash guard. It is resolved
    # here rather than passed in, so run_agent's signature is unchanged and no
    # caller — including the fake runners the suite injects — has to know the
    # hook exists.
    settings = guard_settings()
    if settings:
        cmd += ["--settings", settings]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    result_text = ""
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== stage: {stage} =====\n")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            text=True,
        )

        def feed() -> None:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()

        writer = threading.Thread(target=feed)
        writer.start()
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result_text = event.get("result") or ""
        writer.join()
        code = proc.wait()

    return AgentResult(ok=(code == 0), result_text=result_text)
