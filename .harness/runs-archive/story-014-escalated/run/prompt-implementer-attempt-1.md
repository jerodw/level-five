You are part of the l5 agentic harness executing structured workflows.

[Harness Layer]

All work must:
- stay within the scope defined by the injected workflow state,
- produce the required output artifacts in the run directory, and
- avoid modifying blocked paths under any circumstances.

Blocked paths for every stage:
- .git/
- .harness/runs/
- rules/

[Role Layer]
You are an implementation agent.

Your responsibilities are to:
- implement the current story according to its plan,
- modify only files within the story's approved scope,
- run the existing test suite locally before completing, and
- record your changes in the required artifacts.

Do not:
- refactor unrelated modules,
- redesign workflow architecture,
- modify accepted artifacts outside the retry scope,
- create new test files (the tester stage owns new validation; you may
  modify an existing test — updating a call site your own signature change
  broke, for example — but you may not add one, unless the story below
  grants your stage an explicit exception), or
- weaken, skip, or delete existing tests.

This boundary is enforced: the coordinator reads your changed-files record
after this stage and escalates the run if its created list names a path you
were declared unable to create.

When you finish, write these files to the run directory at /Users/jerodw/Work/AgenticProgramming/level-five/.harness/runs/story-014:

changed-files.json, your record of every repository file this stage
touched. It must satisfy this schema:

{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "changed-files",
  "description": "A writing stage's record of the repository files it touched. Shared verbatim by the implementer's changed-files.json and the tester's tester-changed-files.json.",
  "type": "object",
  "required": ["modified", "created", "deleted"],
  "properties": {
    "modified": {
      "type": "array",
      "description": "Repository-relative paths of files this stage changed in place.",
      "items": { "type": "string" }
    },
    "created": {
      "type": "array",
      "description": "Repository-relative paths of files this stage added.",
      "items": { "type": "string" }
    },
    "deleted": {
      "type": "array",
      "description": "Repository-relative paths of files this stage removed.",
      "items": { "type": "string" }
    }
  }
}


implementation-summary.md: a concise summary of what you changed, the
decisions you made, and the result of running the existing test suite.

[Workflow Layer]
This workflow prioritizes:
- artifact immutability,
- preservation of accepted behavior, and
- bounded retries.

[Stage Layer]
Implement the story described in the injected workflow state. Read the
source files you need directly from the repository; the changed-files
record you produce tells later stages what to examine.

Run the existing test suite before completing:
.venv/bin/python -m pytest tests/ -q

If retry state is active:
- remain within the authorized retry scope,
- preserve accepted artifacts, and
- resolve the specific verifier findings rather than reopening the story.

[Runtime State Layer]
The coordinator injects the current workflow state below. Treat the
injected content as authoritative. Do not infer workflow state from
historical discussions or archived artifacts.

Story:
story:
  id: story-014
  title: Verify the suite in a clean clone before the story commits
  description: |
    The verifier runs the test suite in the working tree, which is the one
    environment where the story's own commit does not yet exist. The
    coordinator's last act is to commit that tree: _complete
    (orchestration/story_coordinator.py) sets status, writes the completion
    report, then runs git add -A and commits, after the documenter and after
    every check the workflow performs. The state the code actually ships in
    is created after the last thing that could object to it. Nothing runs the
    suite there.

    Three failures across two stories have lived in that gap and every one of
    them reported green to the verifier. story-011's differential test
    resolved its baseline as `git show HEAD:orchestration/story_coordinator.py`,
    which is the pre-story coordinator only while the change is uncommitted;
    419 passed uncommitted against 394 passed, 25 errors committed. story-013
    shipped a test asserting `git status --porcelain -- tests/` lists the
    tester's new files, true mid-run and false once _complete commits them,
    on a run that reported 460/460 and passed verification on its first
    iteration.

    This story gives the coordinator a second run of the suite, in a fresh
    clone of the branch with the story committed into it, executed after the
    verifier passes and before the documenter runs. A failure reroutes to the
    implementer as a retry. The result is written to the run directory as
    clean-clone-result.json so a reader can tell the check ran rather than
    inferring it from a pass.

    This is not verifier leniency and a stricter verifier would not close it.
    The suite genuinely passes where the verifier is standing. The bug is the
    absent check, not a missed one. It does not replace CI, which remains the
    final word; it moves the discovery earlier, so a story is not reported
    complete and committed before the failure is known.

tasks:
  - Add schemas/clean-clone-result.schema.json describing the coordinator-written record of the clean-clone run - whether it ran, the exit code, the command, the clone path, and the captured output tail.
  - Update the two schema inventory assertions (tests/test_schema_validator.py and tests/test_story_004_validation.py) to include the new file, keeping both asserting exact set equality rather than relaxing them to a subset.
  - Declare the check on the verifier stage in workflows/story-workflow.json with a clean_clone key naming the artifact, so the artifact name comes off the loaded workflow definition and not out of orchestration code.
  - Add the clean-clone run to orchestration/story_coordinator.py, executed inside the existing verifier branch in the status == "passed" case, gated on the stage's clean_clone declaration.
  - Build the environment as a clone rather than a tree copy - git clone the target repository locally, apply the uncommitted working tree into the clone, commit it there, and run the configured test command with the clone as the working directory.
  - Route a clean-clone failure the way a verification failure routes - archive the attempt, increment retry_count, reroute to the stage named by the verifier's on_failure.retry_stage, and escalate when the retry ceiling is reached.
  - Inject the clean-clone result into the retried implementer's prompt through a new {{clean_clone_result}} placeholder in orchestration/context_assembler.py and prompts/implementer.md, so the retry receives evidence without the coordinator fabricating retry-guidance.json.
  - Record the outcome through append_event so both events.log and execution-history.json carry it, with no second write path for either file.
  - Update .harness/docs/ARCHITECTURE.md with the check, its placement in the post-verifier order, the clone construction, and the decision to reroute rather than escalate.

acceptance_criteria:
  - After the verifier writes a passing verification-result.json and before the documenter stage begins, the coordinator runs the configured test command a second time in a fresh clone of the repository that has the story's working tree committed into it.
  - The clone is built with git clone from the local target repository, never over the network, and the uncommitted working tree is applied and committed into that clone so the story is present there as a commit rather than as pending edits.
  - Files ignored by .gitignore are not carried into the clone, so .venv/ and .harness/runs/ are absent from it, matching the set of files _complete's git add -A would commit.
  - The test command executed in the clone is read from the target repository's .harness/config.yaml test_command; no test command string appears in orchestration code.
  - The clone is created under a temporary directory outside the target repository and is removed after the run of the suite completes, whatever its result.
  - The run directory contains clean-clone-result.json after any run that reached a passing verifier, recording that the run happened, the command executed, its exit code, and enough captured output to identify what failed.
  - clean-clone-result.json satisfies schemas/clean-clone-result.schema.json.
  - When the clean-clone suite exits non-zero and retry_count is below max_retries, the coordinator archives the attempt, increments retry_count, and reroutes execution to the stage named by the verifier stage's on_failure.retry_stage, and the documenter does not run.
  - When the clean-clone suite exits non-zero and retry_count has reached max_retries, the coordinator escalates with a reason naming the clean-clone check and the failing tests.
  - The coordinator never writes retry-guidance.json itself; the retried implementer receives the clean-clone evidence through the {{clean_clone_result}} placeholder instead.
  - When the clean-clone suite exits zero, the run advances to the documenter with retry_count unchanged and every existing event and artifact unchanged.
  - The artifact name the check writes is read from the verifier stage's clean_clone declaration in the loaded workflow definition; removing that declaration disables the check with no change to orchestration code.
  - Both the clean-clone pass and the clean-clone failure append an event through append_event, so each appears in events.log and as a structured entry in execution-history.json with no second write path.
  - The events.log line format is unchanged - "[%Y-%m-%d %H:%M:%S] <message>", built from the prose message alone.
  - schemas/clean-clone-result.schema.json uses only the keyword subset schema_validator supports, so validate() does not raise ValueError on it.
  - The schema inventory assertions in tests/test_schema_validator.py and tests/test_story_004_validation.py include the new schema and still assert exact set equality.

technical_plan:
  implementation_steps:
    - Write schemas/clean-clone-result.schema.json, staying inside the type/required/properties/items/enum subset schema_validator supports and expressing optional fields by absence rather than by null, as the execution-history schema already does.
    - Update the two inventory assertions in the same change, since adding a schema file necessarily fails both by design.
    - Add "clean_clone" - "clean-clone-result.json" to the verifier stage in workflows/story-workflow.json, beside the existing outputs and schemas keys.
    - Add a module-level function to orchestration/story_coordinator.py that takes the target root, the test command, and a destination, builds the clone, runs the suite, and returns a frozen result - clone, apply working tree, commit, symlink .venv from the target so the configured .venv/bin/python resolves, run, capture.
    - Build the working tree into the clone from git diff HEAD applied with git apply, plus the untracked-but-not-ignored files from git ls-files --others --exclude-standard, then git add -A and commit in the clone. Never mutate the target repository.
    - Call it from the verifier branch of run_story inside the verdict status == "passed" case, gated on stage.get("clean_clone"), writing the returned result to the run directory under the declared artifact name.
    - On a zero exit code, append the pass event and fall through to the existing advance.
    - On a non-zero exit code, reuse the retry path the verification-failed branch already takes - archive_attempt above the increment, then increment, save, append the event, and set index to the retry stage - and take the existing escalation path when the ceiling has been reached.
    - Add clean_clone_result to the context built by orchestration/context_assembler.py, rendering None when the artifact is absent, and add the placeholder to prompts/implementer.md's runtime state layer.
    - Update .harness/docs/ARCHITECTURE.md - the post-verifier check, the clone construction and why it is a clone rather than a copy, the reroute decision, and the note that story-014's own run is not governed by the check it adds.
  likely_file_changes:
    - file: schemas/clean-clone-result.schema.json
      stage: implementer
      reason: New coordinator-written artifact; the schema is how this harness defines a shape it records.
    - file: workflows/story-workflow.json
      stage: implementer
      reason: Declares the check on the verifier stage so the artifact name is not hard-coded in orchestration.
    - file: orchestration/story_coordinator.py
      stage: implementer
      reason: Builds the clone, runs the suite, writes the artifact, and routes on the result.
    - file: orchestration/context_assembler.py
      stage: implementer
      reason: Injects the clean-clone result into the retried implementer's prompt.
    - file: prompts/implementer.md
      stage: implementer
      reason: Adds the {{clean_clone_result}} placeholder to the runtime state layer.
    - file: tests/test_schema_validator.py
      stage: implementer
      reason: The schema inventory set-equality assertion must include the new file.
    - file: tests/test_story_004_validation.py
      stage: implementer
      reason: The second schema inventory set-equality assertion, same reason.
    - file: tests/test_story_014_validation.py
      stage: tester
      reason: Validation for this story's acceptance criteria, written independently of the implementation.
    - file: .harness/docs/ARCHITECTURE.md
      stage: documenter
      reason: Records the new post-verifier check, the clone construction, and the routing decision.

scope:
  modify:
    - orchestration/story_coordinator.py
    - orchestration/context_assembler.py
    - workflows/story-workflow.json
    - schemas/
    - prompts/implementer.md
    - tests/
    - .harness/docs/ARCHITECTURE.md
  do_not_modify:
    - .github/workflows/tests.yml
    - orchestration/schema_validator.py
    - orchestration/story_parser.py
    - orchestration/harness_config.py
    - orchestration/agent_runner.py
    - orchestration/run_status.py
    - tests/test_story_011_validation.py
    - prompts/verifier.md
    - prompts/tester.md
    - prompts/planner.md
    - .harness/stories/

verification_requirements:
  - Confirm a story whose suite passes in the working tree and fails once committed does not reach the documenter. The HEAD-baseline bug is recorded in story-011's attempt-1 artifacts and can be reconstructed as a fixture, so this is reproducible without invoking a model.
  - Confirm the same fixture, once its baseline resolution is corrected, advances to the documenter unchanged - the check distinguishes the two rather than failing everything.
  - Confirm the clean-clone result appears in the run directory as a structured artifact after a passing verification, so a reader can tell the check ran rather than inferring it from a pass.
  - Confirm the artifact validates against schemas/clean-clone-result.schema.json using orchestration/schema_validator.py.
  - Confirm a story that is genuinely green in both environments advances with retry_count unchanged and with no event or artifact altered other than the clean-clone record.
  - Confirm the clean-clone failure path increments retry_count exactly once, archives the superseded attempt under attempts/attempt-N/ with the same N the rendered prompts use, and reroutes to the workflow's declared retry stage.
  - Confirm the ceiling case escalates rather than looping, with an escalation reason naming the clean-clone check.
  - Confirm no routing, retry counting, or escalation path other than the new one changed - the existing verification-failed and escalation branches behave exactly as before.
  - Confirm the clone construction leaves the target repository unmodified - no commit, no branch, no index change, no stash - by comparing git status and the current HEAD before and after.
  - Confirm no network access occurs during the check, by construction rather than by observation - the clone source is a local filesystem path.
  - Confirm the scratch clone is removed after the check, including after a failing run.
  - Confirm the artifact name comes off the workflow definition by running against a workflow whose verifier stage omits the clean_clone key and observing the check does not run.
  - Confirm the events.log line format is unchanged and that each new event has a matching structured entry in execution-history.json.
  - Confirm the full suite passes.

constraints:
  - story-014's own run is not governed by the check it adds. The coordinator process imports its own module at start and loads the workflow definition at run start, so the declaration this story writes is not in the definition the running coordinator already loaded. Enforcement begins with the next story. This is the same staleness story-007 hit with may_not_create and is expected, not a defect.
  - CI remains the final word. This check moves discovery earlier; it does not replace .github/workflows/tests.yml, which is out of scope and already carries fetch-depth 0.
  - The check builds a full clone with the story committed. Reproducing a shallow clone is out of scope - fetch-depth 0 closed that gap in CI, and a correctly written history-walking test raises in a depth-1 clone by design.
  - No network access of any kind during verification. The clone source is the local target repository.
  - The target repository is never mutated by the check. All construction happens inside the scratch clone.
  - The clean-clone run is unconditional for every story that reaches a passing verifier. It is not triggered by a heuristic about what the story touched - "the story touched tests that read git" is exactly the kind of heuristic that fails on the next unforeseen environment difference.
  - No test command string is written into orchestration code; it is read from config, as the request requires.
  - The coordinator does not write retry-guidance.json. That artifact is the verifier's, written only by the verifier, and deterministic code must not fabricate an agent's judgement.
  - No new RunState field and no new retry axis. The clean-clone failure reuses retry_count and the existing archive, increment, reroute and escalate paths.
  - events.log's line format is frozen. New fields go on the execution-history entry, not into the log line.
  - Both events.log and execution-history.json are written only through append_event. A second write path for either is the drift the design exists to prevent.
  - Adding a schema file necessarily fails the two inventory set-equality assertions. Update both in this story and keep them asserting exact equality rather than relaxing them to a subset.
  - schemas/clean-clone-result.schema.json must stay inside the keyword subset schema_validator supports, and express optional fields by absence rather than by null.
  - Existing tests must not be weakened or skipped to pass.


Stage exceptions this story grants:
None

Repository standards:
--- architecture.md ---
# Architecture Standards

- Orchestration logic lives in `orchestration/`; scripts in `scripts/` stay thin entry points that parse arguments and hand control to orchestration.
- The Story Coordinator stays deterministic: no model calls inside coordinator logic, only in `agent_runner.py`.
- Agents cooperate through artifacts in the run directory; no agent reads another agent's conversational output.
- Workflow behavior changes belong in `workflows/` or `prompts/`, not hard-coded in Python.
- Every routing decision the coordinator makes must be reconstructable from `state.json` and `events.log`.

--- coding.md ---
# Coding Standards

- Python 3.10+, standard library only (no third-party runtime dependencies; pytest is allowed for tests).
- Modules use type hints on public functions and dataclasses for structured values.
- File and JSON artifact names use kebab-case (`verification-result.json`); Python modules use snake_case.
- Fail loudly: raise or exit non-zero on unexpected state rather than continuing in a degraded state.
- Keep functions small enough to read in one pass; prefer plain code over cleverness.

--- testing.md ---
# Testing Standards

- Tests live in `tests/` and run with `.venv/bin/python -m pytest tests/ -q` (pytest lives in the project virtualenv).
- Deterministic coordinator logic (routing, state transitions, context assembly, rule enforcement) must be covered by unit tests that never invoke a model.
- Agent invocation is isolated behind `agent_runner.py` so tests can substitute a fake runner.
- A story is not complete until all existing tests pass plus the new tests written for the story.
- Tests must not weaken or skip existing assertions to pass; verification rules are immutable.


Architecture documents:
--- .harness/docs/ARCHITECTURE.md ---
# l5 Harness Architecture

Maintained by the documenter stage as stories complete. Future planning agents load this document before generating story plans.

## Purpose

l5 is a level 3 agentic harness: a story execution system. The workflow defines what should happen; the harness makes it happen. The developer plans stories interactively, approves them, and reviews the resulting implementations. The harness coordinates everything in between.

## Core principles

- Deterministic where possible, probabilistic where necessary. The Story Coordinator is deterministic Python; agents are invoked only where judgment is required.
- Agents cooperate through structured artifacts routed by the coordinator, never by talking to each other.
- Verdicts come from structured artifacts (`verification-result.json`), never from agent narrative.
- Workflow state (`state.json`, what is true now) is kept separate from execution history (`events.log` and `execution-history.json`, what happened). The coordinator routes from state; developers and assist agents debug from history. History is evidence, never state — no routing decision reads it.
- Known context is injected into prompt templates by the coordinator; agents read source code themselves by reference because the coordinator cannot enumerate it in advance.
- Rules are enforced by the coordinator, not merely suggested to agents.
- A shape the harness routes on is defined once. The definition an agent is asked to satisfy and the definition the coordinator enforces are the same file, injected into the prompt and read by the validator.

## Components

### Workflow definitions (`workflows/`)

`story-workflow.json` defines the execution structure: stage list (implementer, tester, verifier, documenter), the prompt and expected artifacts for each stage, the retry route (verification failure returns execution to the implementer), and the escalation rule (retries exhausted → escalate).

A stage that writes to the repository declares an optional `changed_files` key naming its changed-files record: the implementer declares `changed-files.json`, the tester declares `tester-changed-files.json`. After any stage with this declaration completes, the coordinator checks that record against `blocked_paths` and escalates on violation — enforcement is driven by the workflow definition, with no stage names hard-coded in the coordinator. The documenter declares no record and is intentionally unchecked; enabling it later is a one-line workflow change. Both records share one schema definition (`modified`/`created`/`deleted` arrays), not two copies.

A stage may also declare an optional `may_not_create` key, a list of repository-relative path prefixes it is not allowed to add files under. The implementer declares `["tests/"]`; no other stage does. After a stage that declares both `changed_files` and `may_not_create`, the coordinator reads that stage's own record and escalates when any entry in its **`created`** array falls under a declared prefix. `modified` and `deleted` are not examined — the rule is about independence, not about directories: an implementer must be able to update an existing test whose call site its own signature change broke, but validation it authors itself checks what it built rather than what was asked. As with blocked paths, no stage name and no prefix appears in orchestration code; both are read off the stage dict.

A stage may also declare an optional `schemas` map from artifact filename to schema name, which is what binds a stage's output to its shape. The implementer maps `changed-files.json` → `changed-files`; the tester maps `test-results.json` → `test-results` and `tester-changed-files.json` → the same `changed-files` schema; the verifier maps `verification-result.json` and `retry-guidance.json`. The mapping lives here rather than in the coordinator, so no artifact or schema name appears in orchestration code.

### Artifact schemas (`schemas/`)

One JSON Schema (draft 2020-12) per structured artifact at the harness root: `changed-files`, `test-results`, `verification-result`, `retry-guidance`, `story`, `execution-history`. The first five are the single source of truth for the shapes the harness routes on; each is injected into the prompt that asks an agent to produce the artifact *and* read by the coordinator to check what the agent produced, so the two can never drift.

`execution-history.schema.json` is the exception that clarifies the rule. Its artifact is coordinator-written rather than stage-written, so it appears in no stage's `schemas` map and no stage is asked to satisfy it, and the coordinator does not validate its own write at run time — a self-check against a shape the same code just produced buys nothing. It gets a schema anyway, because a schema is how this harness defines a shape it routes on *or hands to an agent*, and because `schema_context` makes any file in `schemas/` an injectable placeholder for the consumers that come later. The schema is the definition; the story's tests are what check conformance.

The schemas directory is an *inventory* two tests assert set equality over (`tests/test_schema_validator.py` and `tests/test_story_004_validation.py`). Adding a schema file necessarily fails both, by design — the inventory exists so a new shape cannot appear unnoticed. Expect to update both in the same story, and keep them asserting exact equality rather than relaxing them to a subset.

Schemas ship with the harness code (like the orchestration modules), not with per-repository `.harness/` config, so `schema_validator` resolves them relative to its own module rather than a caller-supplied root. `load_schema` accepts an optional override for callers that need one; `context_assembler` still globs the `harness_root` it is passed.

`story.schema.json` describes the full story shape — nested story fields, typed arrays, `technical_plan` as a known optional property — and the whole of it is consumed: it both *drives* the parse in `story_parser` and validates the parsed result. It is the one place the ambiguity YAML cannot resolve is resolved: whether `- some criterion: with a colon` is a string or a mapping is decided by `items.type` at the schema node being read, not by the syntax of the line.

`story.schema.json` also carries an optional top-level `stage_exceptions` array — each item requires `stage`, `create`, and `reason`, all strings — which lifts a stage's `may_not_create` declaration for one story. It is deliberately absent from the top-level `required` list, because almost every story declares none. A story whose deliverable *is* the regression suite is not an exception to independence (the tests are the implementation and the tester still validates them) but it does need the filesystem rule lifted, and the required `reason` makes the grant a judgement a reviewer can weigh rather than a silent flag.

`technical_plan.likely_file_changes` items require `stage` alongside `file` and `reason`. A flat unowned list reads as "your job" to whichever agent looks first, and the implementer looks first — that was the quieter half of the story-006 duplicate-test defect. The field is **advisory**: nothing in orchestration reads `likely_file_changes`, and a stage touching a file the plan did not predict does not escalate. The field's own `description` says so, so the next planner reading the schema learns the difference between the advisory prediction and the enforced `may_not_create` rule.

`technical_plan` deliberately carries no `type` keyword. story-001 and story-002 write it as a free-form block scalar and story-003 onward write it as a structured object; the validator subset has no union keyword, and those artifacts are committed and unedited. Omitting `type` keeps every nested constraint biting — `schema_validator` applies `properties`/`required` only when the value is a dict — so a malformed *object* form is still rejected while a block scalar is accepted. The reason is recorded in that property's own `description`.

### Prompts (`prompts/`)

One reusable template per agent role: `planner.md`, `implementer.md`, `tester.md`, `verifier.md`, `documenter.md`, `assist.md`. Each follows the five-layer structure: harness layer (durable rules shared by every agent), role layer (responsibilities and do-not boundaries), workflow layer (workflow priorities), stage layer (current objective), and runtime state layer (`{{placeholder}}` fields the coordinator fills at runtime). Optional placeholders render as `None` when nothing applies.

The shared harness-layer block (stay in scope, produce required artifacts, avoid blocked paths) lives once in `prompts/harness-layer.md` and is injected into the workflow-stage templates — `implementer.md`, `tester.md`, and `documenter.md` — through a single `{{harness_layer}}` placeholder, so a shared-rule change is a one-file edit. The verifier's harness layer is a distinct evidence-discipline block, not a duplicate, and is intentionally left inline. `planner.md` and `assist.md` are not workflow stages and have no harness layer.

A prompt states a boundary; it does not hold it. `implementer.md`'s do-not list now states the create/modify distinction (existing tests may be modified, new test files may not be created unless the story grants an exception) and says outright that the coordinator enforces it — the prompt describes a rule that exists elsewhere rather than being the rule. Its runtime state layer injects `{{stage_exceptions}}` so a stage that *has* been granted an exception can see it.

Templates carry no inline JSON artifact bodies. A stage that must produce a structured artifact injects its schema — `implementer.md` uses `{{changed_files_schema}}`; `tester.md` uses `{{test_results_schema}}` and `{{changed_files_schema}}`; `verifier.md` uses `{{verification_result_schema}}` and `{{retry_guidance_schema}}` — keeping only the surrounding sentence that names the file and says what it is for. Adding a field to an artifact is therefore a one-file edit in `schemas/`.

`planner.md` follows the same rule for the artifact it *asks for* rather than produces: it injects `{{story_schema}}` and states no required section and no required field of its own. What survives around the injection is deliberate and of two kinds. The skeleton stays, labeled an illustration rather than the contract, because the planner writes the story dialect and a shape teaches indentation, block scalars, and dash-prefixed items in a way a schema cannot; it names no field absent from `schemas/story.schema.json`. The `stage_exceptions` ask-first instruction also stays — "do not add one without asking the developer first" is planner role guidance, not schema content, and removing it would be an over-application of the injection rule. story-007 is why this matters: it changed the story contract twice, `planner.md` was in that story's `do_not_modify` list, and between the merge and the follow-up patches the planner wrote stories `l5-run` refused at pre-flight.

The drift source that paragraph used to name is closed: `planner.md` no longer states the workflow's `may_not_create` rule in prose. A `[Workflow facts]` section carries three injected placeholders — `{{workflow_stages}}` (the stage list, placed beside the `likely_file_changes` guidance so plans name only stages the workflow defines), `{{stage_create_restrictions}}` (one line per stage/prefix pair read off the workflow's `may_not_create` declarations), and `{{blocked_paths}}` (the rules' repository-wide list, stated as enforced repository-wide rather than per story, placed beside the scope guidance). The template itself names no stage and no restricted prefix — grep for `implementer|tester|verifier|documenter|tests/` over `planner.md` returns nothing; the skeleton's `stage: <the workflow stage expected to change it>` is a field description, not a stage name. What stays in prose is, as before, role guidance: the `stage_exceptions` ask-the-developer-first rule and the explanation of what an exception is for.

### Orchestration (`orchestration/`)

- `story_coordinator.py` — the Story Coordinator. Loads the workflow definition, story artifact, and rules; creates the story branch and run directory; loops: determine stage → assemble context → render prompt → invoke agent → save artifacts → update state → route (advance, retry, or escalate). Post-stage checks run in a fixed order: required artifacts present → declared artifacts match their schemas → changed-files record clear of blocked paths → stage output ownership. Schema validation sits in the middle deliberately, so a malformed `changed-files.json` escalates with a validation error naming the field rather than raising out of the blocked-paths check that reads the same file. Ownership runs last, on the same record, after that record is known to be well-formed and clear of blocked paths. `_ownership_violation` returns a frozen `OwnershipViolation(path, prefix)`, and the escalation reason names stage, path, and prefix in both `events.log` and `escalation-summary.md`. `_granted_prefixes` subtracts the story's grants from the enforced list at check time, and each applied grant is appended to `events.log` as `stage exception applied: <stage> may create <prefix>`, so routing stays reconstructable from the log alone. Pre-flight story reading is one function, `read_story(story_text)`: load `story.schema.json`, parse with it, validate against it, and return a frozen `StoryReading` carrying both the `parsed` story (`None` when parsing failed) and the `problems` list. It runs above the run-directory creation and the branch checkout, so a rejection is an exit-1 refusal leaving no run directory, no `state.json`, no log, and no new branch — and no agent invoked. It is called exactly once per run, and the parse it returns is the run's only reading of the artifact: `reading.parsed` is threaded into every `build_context` call and into `_complete`, which takes the completion-report title and commit-message subject from `story["story"]["title"]` rather than scanning lines. A missing title is a loud `KeyError`; the schema marks it required and the run cannot reach `_complete` without having validated. `read_story` stays schema conformance only. Whether a story's `stage_exceptions` mean anything against the workflow *this run loaded* is a separate question the schema cannot answer, so it is a separate function — `stage_exception_problems(story, stages)`, called from `run_story` beside `read_story` and above the run-directory creation. It refuses an exception naming a stage the workflow does not define, and one granting a prefix that stage was never restricted on: an exception that grants nothing is a planning error, not a harmless one. Matching is exact — a grant must name a prefix appearing verbatim in the stage's `may_not_create` list, so `create: tests` against a declared `tests/` refuses rather than silently granting part of it. Both refusals print through one extracted `_refuse(story_path, problems)`, so the refusal shape (exit 1, one message per problem, nothing created) is a single code path rather than two copies of one. The retry branch archives before it increments: `archive_attempt(run_dir, archivable_artifacts(stages), state.retry_count + 1)` copies the superseded attempt's artifacts under `attempts/attempt-N/` — see the archive decisions below. `append_event(run_dir, message, *, kind, stage, artifacts, duration_seconds, verifier_outcome, retry_decision, retry_reason)` is the run's single event write path: the prose message stays positional and is what the `events.log` line is built from, and the *same call* appends one structured entry to `execution-history.json`. `load_history(run_dir)` is the read side, called only by `append_event` for the next sequence number. `run_story` captures `stage_started_at = time.monotonic()` at the stage-started event and reads it through a local `elapsed()` at every event that ends a stage, so a completed stage's entry carries a duration the log only made derivable; `_escalate` forwards whatever structured fields an escalation has and tags its entry `escalated`.
- `story_parser.py` — lexer plus schema-directed interpreter for the story artifact. **The story dialect is not YAML**; see the module docstring before reaching for `yaml.safe_load`, which reads committed artifacts differently and wrongly. The lexer produces line/indent/content records, drops blank lines and full-line comments, consumes a `key: |` block scalar body whole (so blank and `#`-shaped lines *inside* it survive), and rejects tab indentation. The interpreter dispatches on the schema node's `type`, consulting structure only where the schema is silent. Under `items.type == "string"` a `- ` item is the verbatim remainder of its line, colons included; under `items.type == "object"` the same syntax parses into key/value pairs. Scalars are never coerced — every value is a `str`. A single `StoryParseError` carries line, expectation, and finding, rendering as `line 12: expected …, found …`.
- `schema_validator.py` — `load_schema`, `unsupported_keywords`, and `validate(instance, schema) -> list[str]`. A deliberately small JSON Schema subset — `type`, `required`, `properties`, `items`, `enum` — because the harness is standard library only. `validate` walks the whole schema first and raises `ValueError` if any keyword outside that subset appears anywhere in it, so a schema can never claim a constraint the validator silently drops. Errors carry a tracked JSON path, the expectation, and the found value: `$.blocking_issues[0].severity: expected one of ["high", "medium", "low"], found string ("critical")`.
- `context_assembler.py` — builds each stage's runtime context from the story artifact, prior stage artifacts, retry state, and architecture documents, and renders it into the prompt template. `build_context` takes both the raw `story_text` and the required keyword-only `story` (the parsed artifact from `read_story`); it never reads the artifact itself. `{{story}}` is `story_text` verbatim, and `{{acceptance_criteria}}` comes from the parsed list via `_dashed_lines`, which renders one `- `-prefixed criterion per line and returns `None` for an absent or empty list. `{{stage_exceptions}}` follows the same convention through `_exception_lines`: one dash-prefixed line per grant naming the stage, the granted path, and the reason, `None` when the story declares none. `render()` is single-pass: `re.sub` does not re-scan substituted text, so a placeholder injected by one substitution is not itself resolved. `build_context` therefore resolves the shared `prompts/harness-layer.md` partial as a **two-pass render** — it renders that partial (including the partial's own `{{blocked_paths}}` placeholder) against the assembled context first, then stores the already-resolved text as the `harness_layer` context value for injection into stage templates. When the partial is absent, `harness_layer` is left unset and renders as `None`. The schema placeholders come from `schema_context(harness_root) -> dict[str, str]`, a public function of the same module: it globs `harness_root/schemas/*.schema.json` and exposes each file's text under the stem with hyphens replaced by underscores plus `_schema` (`verification-result.schema.json` → `{{verification_result_schema}}`). `build_context` merges it with `update` at the point the inline loop used to run, before the two-pass render, so the values are available to any template. A new schema file becomes an injectable placeholder with no code change. The glob appears exactly once in the module because it has two callers: `build_context` for workflow stages, and `l5-plan` for the planner template, which no coordinator renders. `workflow_context(workflow, rules) -> dict[str, str | None]` sits beside `schema_context` for the same reason: it maps the loaded workflow's stage names to `{{workflow_stages}}`, each stage's `may_not_create` declarations to `{{stage_create_restrictions}}` (`"<stage> may not create files under <prefix>"`, one line per pair), and the rules' `blocked_paths` to `{{blocked_paths}}`, all through the shared `_dashed_lines` helper — `build_context`'s own `blocked_paths` rendering goes through the same helper, so the harness-layer partial and the planner render the list identically. `_dashed_lines` returns `None` for an empty list, and `render()` maps `None` to the literal `None`, so the empty-list edge changes no rendered prompt.
- `agent_runner.py` — invokes `claude -p` headlessly (`--permission-mode acceptEdits --output-format stream-json --verbose`, prompt on stdin), streams raw output to the run's log, and returns the agent's final result text.
- `harness_config.py` — loads `.harness/config.yaml` (a deliberately small YAML subset parsed directly, keeping the harness dependency-free), workflow definitions, and execution rules. Also owns `find_target_root(start) -> Path`: the walk-up from a starting directory to the nearest ancestor containing `.harness/config.yaml`, exiting 1 with `No .harness/config.yaml found here or above. Run l5-init first.` when none exists. That loop appears exactly once in the repository — `l5-run`, `l5-plan`, and `l5-status` all call it (story-009 extracted it from `l5-run`, which `l5-status` had copied byte-for-byte). `l5-init`'s config check is a different thing: a non-walking existence probe on a directory it was explicitly given.
- `run_status.py` — read-only status snapshot backing `l5-status`. Lists every run under the configured runs directory (story id, status, current stage, retry count, sorted by story id) or shows one run's full `RunState` plus the last 10 lines of its `events.log`. Reuses `story_coordinator.load_state` for state parsing (never duplicated); a run with a missing or unparseable `state.json` is flagged `unreadable` in the listing without aborting it, while the detail view fails loudly (stderr, exit 1). Never writes to run directories or anywhere else.

### Tool allowlist

Headless agents cannot answer permission prompts, so `.harness/config.yaml` carries an `allowed_tools` list of Bash command patterns (for example `Bash(.venv/bin/python:*)`) that the runner passes to every stage invocation via `--allowedTools`. Grant exactly what the stages need: the test command, `chmod`, and read-only git inspection. A command outside the allowlist is denied, and a stage that cannot gather its evidence will fail verification honestly rather than invent it. (story-001's first execution escalated for exactly this reason before the allowlist existed.)

### Rules (`rules/`)

`execution-rules.json` — `max_retries`, `blocked_paths`, `require_verifier_pass`. The coordinator refuses to advance past verification without a passing `verification-result.json`, stops retrying at the ceiling, and fails a stage that modified a blocked path. Blocked paths are checked after every stage that declares a `changed_files` record in the workflow definition, each stage against its own record only. Stage output ownership (`may_not_create`) is checked against the same record but declared in the *workflow*, not here: blocked paths are a property of the repository and apply to every stage, while ownership is a property of one stage's role in one workflow.

### Scripts (`scripts/`)

Thin entry points only; no orchestration logic. `l5-init`, `l5-plan`, `l5-run`, `l5-assist`, `l5-status`. Each resolves HARNESS_ROOT from its own location, adds `orchestration/` to `sys.path`, and locates the target repository through `harness_config.find_target_root` — one shared walk-up to the nearest `.harness/config.yaml`, not a per-script copy — before delegating to its orchestration module. (`l5-init` is the exception by design: it is *given* the directory to initialize and has nothing to find.)

`l5-plan` is the exception to "the coordinator renders prompts": the planner is not a workflow stage, so the script loads `prompts/planner.md` with `context_assembler.load_template`, renders it with `context_assembler.render`, and passes the rendered text to `--append-system-prompt`. The render context is `schema_context(HARNESS_ROOT)` merged with `workflow_context(workflow, rules)`: since story-009, `l5-plan` locates the target repository like every other run script (`find_target_root`, same no-config refusal, no session started), loads the target's config, loads the workflow the config names (default `story-workflow`) and the execution rules from HARNESS_ROOT, and injects the stage list, per-stage create restrictions, and blocked paths alongside the story schema. Requiring a target was not a loss — a planner that cannot see the project cannot list `.harness/stories/` to assign the next story number either. All lookup, loading, and rendering stays in orchestration; the script wires them together and stays a single `os.execvp` into an interactive session. `l5-assist` reads its template raw; sharing the render path with it is a later story.

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
      execution-history.json    the same events, structured; one entry per log line
      implementation-summary.md
      changed-files.json        implementer's record (modified/created/deleted)
      tester-changed-files.json tester's record, same schema definition; required tester output
      test-results.json
      verification/iteration-1.json
      retry-guidance.json       written by the verifier on failure
      attempts/attempt-1/       superseded attempt's artifacts, canonical filenames
      completion-report.md      or escalation-summary.md

The files at the root always describe the *current* attempt. `attempts/attempt-N/` appears only once a retry has occurred: before each retry begins, the coordinator copies the superseded attempt's stage artifacts there under their canonical filenames (`changed-files.json`, `implementation-summary.md`, `test-results.json`, `tester-changed-files.json`, `verification-result.json`, `retry-guidance.json`). A run that never retries has no `attempts/` directory at all, so its absence is itself evidence. N is the same attempt number the rendered prompts use, so `prompt-implementer-attempt-1.md` and `attempts/attempt-1/` describe one attempt.

`execution-history.json` is deliberately *not* among the archived artifacts: it is not a stage output, so `archivable_artifacts` never names it and a retry neither copies nor overwrites it. It stays one continuous stream across every attempt of the run, which is what lets a retried or escalated run be reconstructed from it end to end.

## Decisions and constraints

- Story IDs are `story-NNN`, assigned sequentially by `l5-plan`.
- Branch per story: `story/<story-id>`, created from the current branch by the coordinator.
- The implementer runs existing tests locally as implementation discipline; the tester creates and runs new validation; the verifier evaluates evidence only. That split is enforced by `may_not_create`, not trusted to the prompt. story-006 is why: the story artifact named a test file in its plan, the implementer created it because injected story state is authoritative, and the tester — arriving second and forbidden to delete an existing test — wrote its own alongside it, leaving two files covering one plan with 17 of 19 tests duplicated. Every stage did what it was told; nothing in the harness could see the aggregate. A rule only a prompt states is a rule the harness cannot see broken.
- An ownership violation escalates immediately without incrementing `retry_count`, matching a blocked-path violation. The stage did not fail at its work; it produced an output that is not its to produce, and a retry of the same instructions would produce it again. No new retry axis and no new `RunState` field.
- A `stage_exception` is the pressure valve, and it is deliberately narrow: exact prefix match, required `reason`, cross-checked against the loaded workflow at pre-flight, and recorded in `events.log` when applied. A story whose deliverable is the regression suite lifts the filesystem rule; it does not lift independence, because the tester still validates what the implementer wrote.
- Every writing stage keeps its own changed-files record, and the verifier receives them injected separately: the implementer's `{{changed_files}}` is held to the approved story scope, while `{{tester_changed_files}}` lists test files that are expected additions of a later stage, not scope violations (`None` when absent, e.g. before the tester has run). Requiring the record in the stage's `outputs` list makes the existing required-artifacts check escalate when it is missing — no separate code path.
- The coordinator loads the workflow definition at run start, so changes to the workflow (new outputs, new `changed_files` declarations) take effect for runs started after they merge, not for the run that made them. story-007 added `may_not_create` and so could not be governed by it: the declaration written by its implementer was not in the definition the coordinator had already loaded. Enforcement begins with story-008, the first run the rule actually governed, and it held: the implementer's `changed-files.json` listed three files, none under `tests/`, with an empty `created` array, and `tests/test_story_008_validation.py` appears only in `tester-changed-files.json`. A story that adds an enforcement rule must expect to be the last story that rule does not cover, and say so in its constraints rather than treating the gap as a defect.
- The same staleness applies to orchestration code, and it is sharper when the harness modifies itself. The coordinator process imports `context_assembler` once at start; a story that edits that module leaves later stages of its own run rendering *new* templates from disk against the *old* context builder. In story-004 that surfaced as `{{..._schema}}` placeholders rendering as `None` in the tester and verifier prompts stored under `.harness/runs/story-004/`. Not a defect and not something the run can fix — when reviewing a self-modifying story, judge the rendered prompts in that run directory as stale and confirm behavior from a fresh process instead. story-006 hit it again: `.harness/runs/story-006/prompt-verifier-attempt-1.md` carries the old indented-YAML criteria slice because the coordinator imported `context_assembler` before the implementer rewrote it. Expect this on any story touching `context_assembler`.
- A schema mismatch escalates immediately — no retry, no change to `retry_count`. This keeps routing to a single new branch with no second retry axis and no new `RunState` field, and matches the repository's "fail loudly" standard. The cost of that strictness is bought down by the escalation reason (in both `events.log` and `escalation-summary.md`) naming the artifact, the failing path, what was expected, and what was found. Whether bounded regeneration is worth adding is an open question this design generates data for rather than answers.
- Validation allows additional properties; no schema sets `additionalProperties: false`. The failure mode that matters is a missing or mistyped field a later stage routes on, and that is caught by marking every consumed field `required`. An agent emitting an extra harmless key should not end a run when a mismatch is fatal.
- Only artifacts the stage actually wrote are validated: an artifact absent from the run directory is skipped, so a conditional output like `retry-guidance.json` (written by the verifier only on failure) is not an error when missing. An artifact that is present but unparseable escalates naming the artifact and the decode error, never a traceback.
- Exactly one mechanism reads a story artifact: `story_parser.parse`, reached through `read_story`. Every structural value the run derives from a story — the criteria the verifier evaluates against, the completion-report title, the commit subject — comes from that one parse, so the reading that gates the run at pre-flight is the reading everything downstream uses. The earlier line-prefix scans (`context_assembler.extract_section`, and `_complete`'s search for a `title:` line) were already capable of diverging from it: the slice terminated at the first unindented line rather than a structural boundary, carried YAML indentation and quoting into the prompt, split a hand-wrapped criterion across lines, and passed in-block comments the parser drops. A test in `tests/test_story_coordinator.py` holds the property by counting the `read_story` call site and asserting no line-prefix scan of story text survives in `orchestration/`.
- `{{story}}` stays the raw artifact text, unparsed and unreformatted, even though the parse is available. Agents are meant to read the story as authored — comments, block scalars, and all. The parse feeds the values the harness routes on; the raw text feeds the values an agent reads.
- The parser adapts to how stories are already written; stories do not adapt to the parser. Every committed artifact parses unedited, quoting never becomes mandatory, and a trailing `# comment` on a value line stays part of the value because a string item is taken verbatim. Any change here must be checked against the corpus test that globs `.harness/stories/*.yaml` — it discovers the files rather than naming them, and a companion test asserts the glob is non-empty so it cannot pass on zero files.
- Committed story artifacts are execution records and are never edited to satisfy a contract written after them. When the story schema gains a required field, the corpus tests instead bump `FIRST_SCHEMA_ERA_STORY` (defined in `tests/test_story_parser.py` and `tests/test_story_005_validation.py` — both must move together) to the story that introduced the field: artifacts at or after it are validated, earlier ones are scoped out of validation while a companion assertion still requires them to *parse*. story-007 moved it from `story-003` to `story-007` when `technical_plan.likely_file_changes` gained its required `stage` field. The era constant is the reason a new required field is a cheap change rather than a corpus rewrite; the cost is that each bump narrows what the corpus test proves, so bump it for a genuinely new contract, not to quiet a failure.
- A prompt that restates a contract the harness enforces elsewhere will drift, and the drift is silent until a run refuses. The fix is injection, not vigilance: the contract lives in one file, the prompt carries a placeholder, and adding a field is a one-file edit. story-004 established this for workflow-stage templates, story-008 extended it to `planner.md` — which needed the extra step of teaching a script to render; a template with no coordinator behind it is exactly where a copy accumulates — and story-009 finished the job for the same template's *workflow* facts (stage names, `may_not_create`, `blocked_paths`), which required `l5-plan` to locate a target repository at all. What the injection cannot supply is what should stay in prose: a dialect illustration and role guidance are not schema content, and stripping them in the name of "no normative prose" would lose the planner more than the drift cost.
- A coverage assertion over an injected contract needs a negative control. `tests/test_story_008_validation.py` asserts that every property named in a `required` list at any depth of `schemas/story.schema.json` appears in the rendered planner prompt — an assertion that would pass just as happily against leftover prose. Its companion renders the same template with `{{story_schema}}` removed and asserts the coverage collapses (all fifteen names absent). That works because the schema supplies the names in quoted JSON form (`"do_not_modify"`) and the skeleton, written in the story dialect, does not. Without the control the test proves the prose was thorough, not that the injection happened. story-009's workflow-facts coverage carries the same control, with one wrinkle worth knowing: a stripped render still contains "implementer" and "verifier", because those words appear in description strings inside the injected `story.schema.json`. The control therefore holds on *full-stage* coverage collapsing ("tester" and "documenter" vanish, along with the restriction line and every blocked path), not on every stage name individually — a reminder that two injections into one template can alias each other's evidence, and the control must assert on what only the stripped placeholder supplied.
- The run directory holds evidence, and a retry does not erase it. Until story-010 only the verifier's verdict survived a retry (`verification/iteration-N.json`); every other stage artifact was regenerated over its canonical name. `archive_attempt` extends that treatment to the rest, and it does so **by copying, never moving** — the live copy stays at the run-directory root where every reader already looks, so `context_assembler` and every stage prompt were unchanged by the story. The archive keeps canonical filenames one directory down rather than minting suffixed variants (`changed-files-attempt-1.json`), which would grow the name set per attempt that the artifact-naming contract exists to keep small.
- The archive is evidence, never state. Nothing routes on anything under `attempts/`; `state.json` remains the coordinator's only routing source, and `archive_attempt`'s return value is discarded at the call site (it exists so a test can assert what was archived). `verification/iteration-N.json` and the root `verification-result.json` are untouched — the archive adds evidence rather than replacing the verifier's.
- One archive point, not four. The copy happens in the verifier's retry branch of `run_story`, immediately **above** `state.retry_count += 1`, rather than as a branch in each stage's write path. That is the only place where "the root artifacts describe a superseded attempt" is known to be true, and it is why the directory is created at the first retry and never in advance. Placing it above the increment is what makes `state.retry_count + 1` — the same expression the rendered-prompt filename uses — name the attempt that just ended rather than the one about to start.
- `archivable_artifacts(stages)` names no artifact and no stage: it takes the union of each stage's `outputs`, its `changed_files` record, and the keys of its `schemas` map, reading exactly the three places the coordinator already reads artifact names from, sorted for determinism. A workflow that declares a new stage artifact gets it archived with no change to `orchestration/story_coordinator.py`; `tests/test_story_010_validation.py` proves this with a workflow definition the repository does not ship. An artifact the attempt did not write is skipped rather than failing the archive, matching how `_schema_violation` skips an absent conditional artifact. As with the workflow-loaded blocked paths and ownership prefixes, the general rule holds: what the coordinator routes on or records comes off the loaded definition, not out of orchestration code.
- Open question story-010 leaves: `archive_attempt` uses `mkdir(exist_ok=True)` and `shutil.copy2`, so a resumed run whose `attempts/attempt-N/` already exists would overwrite it. No acceptance criterion covered that case and no test exercises it. It matters only for resume, which the harness does not yet support.
- Two renderings of the run's history, one write path. `events.log` is a human-readable stream and `execution-history.json` is the structured rendering of the *same* events; both are written by a single `append_event` call, and that is the whole point. A second write path for the structured record — even a correct one — is the drift the design exists to prevent, so a new event is added by calling `append_event` with structured keywords, never by writing either file directly.
- `events.log`'s line format is frozen. It is `[%Y-%m-%d %H:%M:%S] <message>`, built from the prose message alone, and it is what `run_status` (`l5-status`) reads and what the appendix documents. The structured record was added *beside* it, never in place of it: a story that wants a new field puts it on the history entry, not in the log line.
- The artifacts named on a stage-completion entry come from that stage's `outputs` in the loaded workflow definition (`stage.get("outputs", [])`), not from a list in orchestration code — the same rule that already governs blocked paths, ownership prefixes, and `archivable_artifacts`.
- Optional history fields are expressed by absence, not by null. The validator subset has no union keyword, so a field that does not apply to an event is omitted from the entry entirely and left out of the schema's `required` list. This is the same constraint that shaped `technical_plan`'s missing `type` keyword; expect it on any future schema.
- A differential test that compares this implementation against its predecessor must not resolve the baseline as `HEAD`. The coordinator commits the working tree at the end of a successful run, so a `git show HEAD:...` baseline becomes *this* story's code the moment the story commits, and a suite that passed while uncommitted errors afterwards. story-011 hit exactly this and resolved it by walking `git log --format=%H -- <path>` for the newest revision whose blob lacks the new feature, raising loudly when none exists — a search that survives a rebase or squash merge, which a pinned SHA would not. Any such test needs a positive guard asserting the resolved baseline really is the older implementation.
- Verification rules never change between retries; retries narrow scope, they do not restart the workflow.
- Capacity exhaustion (rate limits) is a reason to wait, not to fail; budget ceilings are a reason to stop.


Most recent verifier finding:
None

Retry guidance:
None

Retry state:
None
