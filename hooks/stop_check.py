#!/usr/bin/env python3
"""Block a stage that tries to end its turn with a required output unwritten.

This is a Stop hook, registered by hooks/settings.json and passed to a
workflow stage invocation by orchestration/agent_runner.py. It reads the hook
payload from stdin, runs the same checker `scripts/l5-check` runs, and writes
a block decision to stdout when a required output is missing, stale or
invalid; otherwise it writes nothing at all.

An invocation that names no run directory — the planner, the workflow
selector, the inspector — is given the declaration it was given before this
hook existed, with no Stop entry in it, so none of them meets this at all.

Three properties are load-bearing and none is an accident of the code:

**It blocks at most once per turn.** When the payload reports that a stop hook
is already active it emits no decision whatever the outputs look like, so a
stage that genuinely cannot write the file ends its turn and the coordinator's
existing self-route handles it exactly as it does today.

**Its bias is fail-open**, the Bash guard's bias. Unreadable stdin, a
malformed payload, an absent or unreadable baseline, an unreadable state.json
and an unloadable workflow each yield no decision rather than a block. A
defect in this program stops no run.

**It decides nothing.** The coordinator's required-output, freshness and
schema checks are the authority and read nothing this wrote or decided: a
stage that reports itself complete and is not is still re-entered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "orchestration"))

HOOK_EVENT = "Stop"


def block(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def main() -> int:
    # Every failure below is silent by design: no decision lets the turn end,
    # and the coordinator decides afterwards as it always has. See the module
    # docstring.
    try:
        payload = json.loads(sys.stdin.read())
    except (OSError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    # Block once. An active stop hook means this has already blocked in this
    # turn, so the stage is let go rather than held in a loop it may have no
    # way out of.
    if payload.get("stop_hook_active"):
        return 0

    arguments = sys.argv[1:]
    if len(arguments) != 2:
        return 0
    run_dir, stage = Path(arguments[0]), arguments[1]

    try:
        import output_check

        verdict = output_check.check(run_dir, stage)
        if verdict.complete or verdict.problem:
            return 0
        reason = "\n".join(output_check.report(verdict))
    except Exception:  # a defect here must not stop a run
        return 0
    if not reason:
        return 0

    json.dump(
        block(
            "This stage's turn is ending with its required outputs "
            "unwritten:\n"
            f"{reason}\n"
            "Write them into the run directory and then end the turn. The "
            "coordinator checks the same thing after the turn and re-enters "
            "the stage when it fails, which costs the run a whole invocation."
        ),
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
