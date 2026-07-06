# l5 Harness Architecture

Maintained by the assist and documenter agents. Update this document whenever a story changes the harness's structure or behavior. Future planning agents load this document before generating story plans.

## Purpose

l5 is a level 3 agentic harness: a story execution system. The workflow defines what should happen; the harness makes it happen. The developer plans stories interactively, approves them, and reviews the resulting implementations. The harness coordinates everything in between.

## Core principles

- Deterministic where possible, probabilistic where necessary. The Story Coordinator is deterministic Python; agents are invoked only where judgment is required.
- Agents cooperate through structured artifacts routed by the coordinator, never by talking to each other.
- Verdicts come from structured artifacts (`verification-result.json`), never from agent narrative.
- Workflow state (`state.json`, what is true now) is kept separate from execution history (`events.log`, what happened).
- Known context is injected into prompt templates by the coordinator; agents read source code themselves by reference because the coordinator cannot enumerate it in advance.
- Rules are enforced by the coordinator, not merely suggested to agents.

## Components

### Workflow definitions (`workflows/`)

`story-workflow.json` defines the execution structure: stage list (implementer, tester, verifier, documenter), the prompt and expected artifacts for each stage, the retry route (verification failure returns execution to the implementer), and the escalation rule (retries exhausted → escalate).

### Prompts (`prompts/`)

One reusable template per agent role: `planner.md`, `implementer.md`, `tester.md`, `verifier.md`, `documenter.md`, `assist.md`. Each follows the five-layer structure: harness layer (durable rules shared by every agent), role layer (responsibilities and do-not boundaries), workflow layer (workflow priorities), stage layer (current objective), and runtime state layer (`{{placeholder}}` fields the coordinator fills at runtime). Optional placeholders render as `None` when nothing applies.

### Orchestration (`orchestration/`)

- `story_coordinator.py` — the Story Coordinator. Loads the workflow definition, story artifact, and rules; creates the story branch and run directory; loops: determine stage → assemble context → render prompt → invoke agent → save artifacts → update state → route (advance, retry, or escalate).
- `context_assembler.py` — builds each stage's runtime context from the story artifact, prior stage artifacts, retry state, and architecture documents, and renders it into the prompt template.
- `agent_runner.py` — invokes `claude -p` headlessly (`--permission-mode acceptEdits --output-format stream-json --verbose`, prompt on stdin), streams raw output to the run's log, and returns the agent's final result text.

### Rules (`rules/`)

`execution-rules.json` — `max_retries`, `blocked_paths`, `require_verifier_pass`. The coordinator refuses to advance past verification without a passing `verification-result.json`, stops retrying at the ceiling, and fails a stage that modified a blocked path.

### Scripts (`scripts/`)

Thin entry points only; no orchestration logic. `l5-init`, `l5-plan`, `l5-run`, `l5-assist`.

### Target-repository state (`.harness/`)

- `config.yaml` — repository-specific settings (branch prefix, model, permission mode, workflow name).
- `standards/` — repository standards (architecture, coding, testing) that verifiers evaluate against.
- `stories/` — approved story artifacts produced by `l5-plan` (committed).
- `runs/<story-id>/` — per-run state, events, and artifacts (not committed).
- `logs/` — raw agent output logs (not committed).

## Story lifecycle

    Approved story artifact (.harness/stories/story-NNN.yaml)
        ↓
    l5-run → Story Coordinator
        ↓
    implement → test → verify ──fail──→ retry implement (bounded)
        ↓ pass                              ↓ retries exhausted
    document                            escalated (escalation-summary.md)
        ↓
    completed (completion-report.md)

## Run directory anatomy

    .harness/runs/story-001/
      state.json                current stage, status, retry_count, branch
      events.log                append-only stage/retry/escalation events
      implementation-summary.md
      changed-files.json
      test-results.json
      verification/iteration-1.json
      retry-guidance.json       written by the verifier on failure
      completion-report.md      or escalation-summary.md

## Decisions and constraints

- Story IDs are `story-NNN`, assigned sequentially by `l5-plan`.
- Branch per story: `story/<story-id>`, created from the current branch by the coordinator.
- The implementer runs existing tests locally as implementation discipline; the tester creates and runs new validation; the verifier evaluates evidence only.
- Verification rules never change between retries; retries narrow scope, they do not restart the workflow.
- Capacity exhaustion (rate limits) is a reason to wait, not to fail; budget ceilings are a reason to stop.
