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


@dataclass
class AgentResult:
    ok: bool
    result_text: str


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
