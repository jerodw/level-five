# l5 Harness Architecture

Maintained by the documenter stage as stories complete. Future planning agents load this document before generating story plans.

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

A stage that writes to the repository declares an optional `changed_files` key naming its changed-files record: the implementer declares `changed-files.json`, the tester declares `tester-changed-files.json`. After any stage with this declaration completes, the coordinator checks that record against `blocked_paths` and escalates on violation — enforcement is driven by the workflow definition, with no stage names hard-coded in the coordinator. The documenter declares no record and is intentionally unchecked; enabling it later is a one-line workflow change. Both records share the same schema verbatim (`modified`/`created`/`deleted` arrays).

### Prompts (`prompts/`)

One reusable template per agent role: `planner.md`, `implementer.md`, `tester.md`, `verifier.md`, `documenter.md`, `assist.md`. Each follows the five-layer structure: harness layer (durable rules shared by every agent), role layer (responsibilities and do-not boundaries), workflow layer (workflow priorities), stage layer (current objective), and runtime state layer (`{{placeholder}}` fields the coordinator fills at runtime). Optional placeholders render as `None` when nothing applies.

### Orchestration (`orchestration/`)

- `story_coordinator.py` — the Story Coordinator. Loads the workflow definition, story artifact, and rules; creates the story branch and run directory; loops: determine stage → assemble context → render prompt → invoke agent → save artifacts → update state → route (advance, retry, or escalate).
- `context_assembler.py` — builds each stage's runtime context from the story artifact, prior stage artifacts, retry state, and architecture documents, and renders it into the prompt template.
- `agent_runner.py` — invokes `claude -p` headlessly (`--permission-mode acceptEdits --output-format stream-json --verbose`, prompt on stdin), streams raw output to the run's log, and returns the agent's final result text.
- `run_status.py` — read-only status snapshot backing `l5-status`. Lists every run under the configured runs directory (story id, status, current stage, retry count, sorted by story id) or shows one run's full `RunState` plus the last 10 lines of its `events.log`. Reuses `story_coordinator.load_state` for state parsing (never duplicated); a run with a missing or unparseable `state.json` is flagged `unreadable` in the listing without aborting it, while the detail view fails loudly (stderr, exit 1). Never writes to run directories or anywhere else.

### Tool allowlist

Headless agents cannot answer permission prompts, so `.harness/config.yaml` carries an `allowed_tools` list of Bash command patterns (for example `Bash(.venv/bin/python:*)`) that the runner passes to every stage invocation via `--allowedTools`. Grant exactly what the stages need: the test command, `chmod`, and read-only git inspection. A command outside the allowlist is denied, and a stage that cannot gather its evidence will fail verification honestly rather than invent it. (story-001's first execution escalated for exactly this reason before the allowlist existed.)

### Rules (`rules/`)

`execution-rules.json` — `max_retries`, `blocked_paths`, `require_verifier_pass`. The coordinator refuses to advance past verification without a passing `verification-result.json`, stops retrying at the ceiling, and fails a stage that modified a blocked path. Blocked paths are checked after every stage that declares a `changed_files` record in the workflow definition, each stage against its own record only.

### Scripts (`scripts/`)

Thin entry points only; no orchestration logic. `l5-init`, `l5-plan`, `l5-run`, `l5-assist`, `l5-status`. Each resolves HARNESS_ROOT from its own location, adds `orchestration/` to `sys.path`, and locates the target repository by walking up to the nearest `.harness/config.yaml` before delegating to its orchestration module.

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
      changed-files.json        implementer's record (modified/created/deleted)
      tester-changed-files.json tester's record, same schema; required tester output
      test-results.json
      verification/iteration-1.json
      retry-guidance.json       written by the verifier on failure
      completion-report.md      or escalation-summary.md

## Decisions and constraints

- Story IDs are `story-NNN`, assigned sequentially by `l5-plan`.
- Branch per story: `story/<story-id>`, created from the current branch by the coordinator.
- The implementer runs existing tests locally as implementation discipline; the tester creates and runs new validation; the verifier evaluates evidence only.
- Every writing stage keeps its own changed-files record, and the verifier receives them injected separately: the implementer's `{{changed_files}}` is held to the approved story scope, while `{{tester_changed_files}}` lists test files that are expected additions of a later stage, not scope violations (`None` when absent, e.g. before the tester has run). Requiring the record in the stage's `outputs` list makes the existing required-artifacts check escalate when it is missing — no separate code path.
- The coordinator loads the workflow definition at run start, so changes to the workflow (new outputs, new `changed_files` declarations) take effect for runs started after they merge, not for the run that made them.
- Verification rules never change between retries; retries narrow scope, they do not restart the workflow.
- Capacity exhaustion (rate limits) is a reason to wait, not to fail; budget ceilings are a reason to stop.
