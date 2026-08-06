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
- A shape the harness routes on is defined once. The definition an agent is asked to satisfy and the definition the coordinator enforces are the same file, injected into the prompt and read by the validator.

## Components

### Workflow definitions (`workflows/`)

`story-workflow.json` defines the execution structure: stage list (implementer, tester, verifier, documenter), the prompt and expected artifacts for each stage, the retry route (verification failure returns execution to the implementer), and the escalation rule (retries exhausted → escalate).

A stage that writes to the repository declares an optional `changed_files` key naming its changed-files record: the implementer declares `changed-files.json`, the tester declares `tester-changed-files.json`. After any stage with this declaration completes, the coordinator checks that record against `blocked_paths` and escalates on violation — enforcement is driven by the workflow definition, with no stage names hard-coded in the coordinator. The documenter declares no record and is intentionally unchecked; enabling it later is a one-line workflow change. Both records share one schema definition (`modified`/`created`/`deleted` arrays), not two copies.

A stage may also declare an optional `schemas` map from artifact filename to schema name, which is what binds a stage's output to its shape. The implementer maps `changed-files.json` → `changed-files`; the tester maps `test-results.json` → `test-results` and `tester-changed-files.json` → the same `changed-files` schema; the verifier maps `verification-result.json` and `retry-guidance.json`. The mapping lives here rather than in the coordinator, so no artifact or schema name appears in orchestration code.

### Artifact schemas (`schemas/`)

One JSON Schema (draft 2020-12) per structured artifact at the harness root: `changed-files`, `test-results`, `verification-result`, `retry-guidance`, `story`. These are the single source of truth for the shapes the harness routes on. Each is injected into the prompt that asks an agent to produce the artifact *and* read by the coordinator to check what the agent produced, so the two can never drift.

Schemas ship with the harness code (like the orchestration modules), not with per-repository `.harness/` config, so `schema_validator` resolves them relative to its own module rather than a caller-supplied root. `load_schema` accepts an optional override for callers that need one; `context_assembler` still globs the `harness_root` it is passed.

`story.schema.json` describes the full story shape — nested story fields, typed arrays, `technical_plan` as a known optional property — although only its top-level `required` list is consumed today (see `REQUIRED_STORY_SECTIONS` below). The rest is the input to a schema-directed story parser, where the schema resolves an ambiguity YAML cannot: whether `- some criterion: with a colon` is a string or a mapping.

### Prompts (`prompts/`)

One reusable template per agent role: `planner.md`, `implementer.md`, `tester.md`, `verifier.md`, `documenter.md`, `assist.md`. Each follows the five-layer structure: harness layer (durable rules shared by every agent), role layer (responsibilities and do-not boundaries), workflow layer (workflow priorities), stage layer (current objective), and runtime state layer (`{{placeholder}}` fields the coordinator fills at runtime). Optional placeholders render as `None` when nothing applies.

The shared harness-layer block (stay in scope, produce required artifacts, avoid blocked paths) lives once in `prompts/harness-layer.md` and is injected into the workflow-stage templates — `implementer.md`, `tester.md`, and `documenter.md` — through a single `{{harness_layer}}` placeholder, so a shared-rule change is a one-file edit. The verifier's harness layer is a distinct evidence-discipline block, not a duplicate, and is intentionally left inline. `planner.md` and `assist.md` are not workflow stages and have no harness layer.

Templates carry no inline JSON artifact bodies. A stage that must produce a structured artifact injects its schema — `implementer.md` uses `{{changed_files_schema}}`; `tester.md` uses `{{test_results_schema}}` and `{{changed_files_schema}}`; `verifier.md` uses `{{verification_result_schema}}` and `{{retry_guidance_schema}}` — keeping only the surrounding sentence that names the file and says what it is for. Adding a field to an artifact is therefore a one-file edit in `schemas/`.

### Orchestration (`orchestration/`)

- `story_coordinator.py` — the Story Coordinator. Loads the workflow definition, story artifact, and rules; creates the story branch and run directory; loops: determine stage → assemble context → render prompt → invoke agent → save artifacts → update state → route (advance, retry, or escalate). Post-stage checks run in a fixed order: required artifacts present → declared artifacts match their schemas → changed-files record clear of blocked paths. Schema validation sits in the middle deliberately, so a malformed `changed-files.json` escalates with a validation error naming the field rather than raising out of the blocked-paths check that reads the same file. `REQUIRED_STORY_SECTIONS` is derived from `schemas/story.schema.json`'s `required` array rather than a hard-coded tuple.
- `schema_validator.py` — `load_schema`, `unsupported_keywords`, and `validate(instance, schema) -> list[str]`. A deliberately small JSON Schema subset — `type`, `required`, `properties`, `items`, `enum` — because the harness is standard library only. `validate` walks the whole schema first and raises `ValueError` if any keyword outside that subset appears anywhere in it, so a schema can never claim a constraint the validator silently drops. Errors carry a tracked JSON path, the expectation, and the found value: `$.blocking_issues[0].severity: expected one of ["high", "medium", "low"], found string ("critical")`.
- `context_assembler.py` — builds each stage's runtime context from the story artifact, prior stage artifacts, retry state, and architecture documents, and renders it into the prompt template. `render()` is single-pass: `re.sub` does not re-scan substituted text, so a placeholder injected by one substitution is not itself resolved. `build_context` therefore resolves the shared `prompts/harness-layer.md` partial as a **two-pass render** — it renders that partial (including the partial's own `{{blocked_paths}}` placeholder) against the assembled context first, then stores the already-resolved text as the `harness_layer` context value for injection into stage templates. When the partial is absent, `harness_layer` is left unset and renders as `None`. It also globs `harness_root/schemas/*.schema.json` and exposes each file's text under the stem with hyphens replaced by underscores plus `_schema` (`verification-result.schema.json` → `{{verification_result_schema}}`), before the two-pass render so the values are available to any template. A new schema file becomes an injectable placeholder with no code change.
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
      tester-changed-files.json tester's record, same schema definition; required tester output
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
- The same staleness applies to orchestration code, and it is sharper when the harness modifies itself. The coordinator process imports `context_assembler` once at start; a story that edits that module leaves later stages of its own run rendering *new* templates from disk against the *old* context builder. In story-004 that surfaced as `{{..._schema}}` placeholders rendering as `None` in the tester and verifier prompts stored under `.harness/runs/story-004/`. Not a defect and not something the run can fix — when reviewing a self-modifying story, judge the rendered prompts in that run directory as stale and confirm behavior from a fresh process instead.
- A schema mismatch escalates immediately — no retry, no change to `retry_count`. This keeps routing to a single new branch with no second retry axis and no new `RunState` field, and matches the repository's "fail loudly" standard. The cost of that strictness is bought down by the escalation reason (in both `events.log` and `escalation-summary.md`) naming the artifact, the failing path, what was expected, and what was found. Whether bounded regeneration is worth adding is an open question this design generates data for rather than answers.
- Validation allows additional properties; no schema sets `additionalProperties: false`. The failure mode that matters is a missing or mistyped field a later stage routes on, and that is caught by marking every consumed field `required`. An agent emitting an extra harmless key should not end a run when a mismatch is fatal.
- Only artifacts the stage actually wrote are validated: an artifact absent from the run directory is skipped, so a conditional output like `retry-guidance.json` (written by the verifier only on failure) is not an error when missing. An artifact that is present but unparseable escalates naming the artifact and the decode error, never a traceback.
- Verification rules never change between retries; retries narrow scope, they do not restart the workflow.
- Capacity exhaustion (rate limits) is a reason to wait, not to fail; budget ceilings are a reason to stop.
