# Architecture Standards

- Orchestration logic lives in `orchestration/`; scripts in `scripts/` stay thin entry points that parse arguments and hand control to orchestration.
- The Story Coordinator stays deterministic: no model calls inside coordinator logic, only in `agent_runner.py`.
- Agents cooperate through artifacts in the run directory; no agent reads another agent's conversational output.
- Workflow behavior changes belong in `workflows/` or `prompts/`, not hard-coded in Python.
- Every routing decision the coordinator makes must be reconstructable from `state.json` and `events.log`.
