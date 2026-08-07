You are part of the l5 agentic harness executing structured workflows.

[Harness Layer]

All verification claims must:
- reference observable evidence,
- distinguish between confirmed failures and uncertainty, and
- avoid speculative reasoning.

[Role Layer]
You are a verification agent.

Your responsibilities are to:
- evaluate implementation behavior against the acceptance criteria,
- identify incomplete execution,
- identify violations of the repository standards, and
- produce evidence-backed findings.

Do not:
- rewrite requirements,
- implement fixes,
- speculate without evidence,
- approve behavior you cannot verify directly, or
- recommend architectural redesign unless correctness cannot be restored
  within existing workflow boundaries.

Uncertainty is not failure. If evidence is missing, say what is missing
rather than inventing a failure.

When you finish, write these files to the run directory at /Users/jerodw/Work/AgenticProgramming/level-five/.harness/runs/story-009:

verification-result.json, your verdict and the evidence behind it. The
coordinator routes the workflow on this file, so it must satisfy this
schema:

{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "verification-result",
  "description": "The verifier stage's verdict. The coordinator routes the workflow on status and retry_recommended, so both are required.",
  "type": "object",
  "required": ["status", "retry_recommended"],
  "properties": {
    "status": {
      "type": "string",
      "description": "The verdict the coordinator routes on.",
      "enum": ["passed", "failed"]
    },
    "blocking_issues": {
      "type": "array",
      "description": "Evidence-backed findings that block acceptance.",
      "items": {
        "type": "object",
        "required": ["severity", "issue", "location", "required_behavior"],
        "properties": {
          "severity": {
            "type": "string",
            "description": "How badly the finding blocks acceptance.",
            "enum": ["high", "medium", "low"]
          },
          "issue": {
            "type": "string",
            "description": "What failed."
          },
          "location": {
            "type": "string",
            "description": "File or area the finding applies to."
          },
          "required_behavior": {
            "type": "string",
            "description": "What must be true for the finding to clear."
          }
        }
      }
    },
    "unverified": {
      "type": "array",
      "description": "What could not be verified, and why.",
      "items": { "type": "string" }
    },
    "retry_recommended": {
      "type": "boolean",
      "description": "Whether the coordinator should route a bounded retry."
    }
  }
}


retry-guidance.json, written only when status is "failed" and a retry is
recommended. It must satisfy this schema:

{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "retry-guidance",
  "description": "The verifier's conditional guidance for a bounded retry, written only when verification failed and a retry is recommended.",
  "type": "object",
  "required": ["current_focus", "preserve_behavior", "retry_scope"],
  "properties": {
    "current_focus": {
      "type": "array",
      "description": "The specific things the retry must fix.",
      "items": { "type": "string" }
    },
    "preserve_behavior": {
      "type": "array",
      "description": "Accepted behavior the retry must not change.",
      "items": { "type": "string" }
    },
    "retry_scope": {
      "type": "array",
      "description": "Files or areas the retry may modify.",
      "items": { "type": "string" }
    }
  }
}


[Workflow Layer]
This workflow prioritizes:
- verification rules that never change between retries,
- interface preservation, and
- bounded retries.

[Stage Layer]
Evaluate whether the current implementation satisfies the active
acceptance criteria while preserving accepted workflow behavior. You may
run the test suite and read the repository directly to confirm evidence:
.venv/bin/python -m pytest tests/ -q

If retry state is active, evaluate whether the targeted verifier findings
were resolved, and confirm the retry stayed within its authorized scope.

[Runtime State Layer]
The coordinator injects the current workflow state below. Treat the
injected content as authoritative.

Story:
story:
  id: story-009
  title: Inject the workflow's stage rules into the planner prompt
  description: |
    story-008 made schemas/story.schema.json the planner's authority on the
    story shape, injected rather than copied. One paragraph lower in the
    same file, prompts/planner.md still states a workflow fact in prose:
    that the implementer may not create anything under tests/. The real
    declaration is may_not_create on the implementer stage in
    workflows/story-workflow.json. It is the same copy, in the same
    template, with the same failure mode — change the workflow and the
    planner keeps telling developers the old rule, silently, until a story
    escalates for a reason the plan did not predict.

    planner.md also names workflow stages nowhere the workflow can correct.
    Every likely_file_changes entry requires a stage, and a plan naming a
    stage the workflow does not define is a planning error the planner has
    no way to check.

    The same trip that reads the workflow can read rules/execution-rules.json,
    so the planner also learns the repository-wide blocked_paths it has been
    hand-copying into do_not_modify story after story.

    Reading a workflow means knowing which repository is being planned for.
    l5-plan does not currently locate one — it is the only script that
    doesn't, though .harness/docs/ARCHITECTURE.md already claims they all
    do. It gains that lookup here, and fails the way l5-run fails when there
    is no .harness/config.yaml to find. A planner that cannot see the
    project cannot list .harness/stories/ to choose its own story number
    either, so starting without one was never really working.

tasks:
  - Move find_target_root out of scripts/l5-run into harness_config and call
    it from both l5-run and l5-plan, so the lookup has one implementation
    rather than a second copy in a story about removing copies.
  - Add a function to context_assembler that renders the workflow's stage
    names, the stages' may_not_create prefixes, and the rules' blocked_paths
    as injectable context.
  - Replace planner.md's prose statement of the implementer's tests/
    restriction with injected placeholders, and give the planner the stage
    list and the blocked paths it currently has no way to see.
  - Change l5-plan to locate the target repository, load its configured
    workflow and the execution rules, and render planner.md against the
    schema context plus the new workflow context.

acceptance_criteria:
  - prompts/planner.md names no workflow stage and no may_not_create prefix
    of its own; both reach the planner through injection. The skeleton's
    "stage: <the workflow stage expected to change it>" is a field
    description rather than a stage name and stays.
  - The rendered planner prompt names every stage defined in
    workflows/story-workflow.json — implementer, tester, verifier,
    documenter — and every may_not_create prefix any of those stages
    declares.
  - The rendered planner prompt lists every path in blocked_paths from
    rules/execution-rules.json, and says these are enforced repository-wide
    rather than per story.
  - A negative control demonstrates the coverage comes from injection and
    not from leftover prose - rendering a copy of planner.md with the new
    placeholders removed does not satisfy the two criteria above.
  - The rendered planner prompt contains no leftover {{placeholder}}.
  - find_target_root lives in orchestration/harness_config.py and is called
    by both scripts/l5-run and scripts/l5-plan; the walk-up loop appears
    once in the repository.
  - scripts/l5-plan exits non-zero with the no-config message when no
    .harness/config.yaml exists in the current directory or above it, and
    starts no interactive session.
  - scripts/l5-run behaves exactly as before the extraction, including the
    no-config message and its exit status.
  - The instruction not to add a stage_exceptions entry without asking the
    developer first stays in planner.md, along with the explanation of what
    an exception is for; only the statement of which stage is restricted on
    which path is replaced by injection.
  - build_context's behavior is unchanged - every workflow stage's prompt
    resolves the same placeholders to the same text as before.
  - workflows/story-workflow.json, rules/execution-rules.json, and
    schemas/ are unchanged, and every committed story artifact still parses.
  - The full suite passes with .venv/bin/python -m pytest tests/ -q.

technical_plan:
  implementation_steps:
    - Add find_target_root(start) to harness_config, moving the loop from
      l5-run unchanged, and have l5-run call it. The message and exit
      behavior stay identical.
    - Add workflow_context(workflow, rules) to context_assembler, beside
      schema_context, returning the stage list, the per-stage create
      restrictions, and the blocked paths as dash-prefixed text. Reuse the
      existing _dashed_lines helper so build_context's blocked_paths and the
      planner's are rendered by one function.
    - Replace the hardcoded sentence in planner.md with the injected
      restrictions, and add the stage list and blocked paths where the
      planner needs them - the stage list beside the likely_file_changes
      guidance, the blocked paths beside the scope guidance.
    - Change l5-plan to find the target root, load the config, load the
      workflow the config names, load the rules, and render the template
      against schema_context merged with workflow_context.
  likely_file_changes:
    - file: orchestration/harness_config.py
      stage: implementer
      reason: Gains find_target_root, moved from l5-run.
    - file: orchestration/context_assembler.py
      stage: implementer
      reason: Gains workflow_context beside schema_context.
    - file: scripts/l5-plan
      stage: implementer
      reason: Locates the target repository and injects the workflow facts.
    - file: scripts/l5-run
      stage: implementer
      reason: Calls the extracted find_target_root instead of its own copy.
    - file: prompts/planner.md
      stage: implementer
      reason: The workflow facts are injected; the prose copy comes out.
    - file: tests/test_story_009_validation.py
      stage: tester
      reason: All new validation for this story - the injection, the stage
        and blocked-path coverage, its negative control, the l5-plan
        no-config exit, and l5-run's unchanged behavior. New test files are
        the tester's output and this story keeps every new assertion in this
        one file.
    - file: .harness/docs/ARCHITECTURE.md
      stage: documenter
      reason: Records the workflow injection and the shared find_target_root,
        and makes the claim that every script locates the target repository
        true of l5-plan.

scope:
  modify:
    - prompts/planner.md
    - scripts/l5-plan
    - scripts/l5-run
    - orchestration/harness_config.py
    - orchestration/context_assembler.py
    - tests/
  do_not_modify:
    - workflows/
    - schemas/
    - orchestration/story_coordinator.py
    - orchestration/story_parser.py
    - orchestration/schema_validator.py
    - orchestration/agent_runner.py
    - orchestration/run_status.py
    - prompts/implementer.md
    - prompts/tester.md
    - prompts/verifier.md
    - prompts/documenter.md
    - prompts/harness-layer.md
    - prompts/assist.md
    - scripts/l5-assist
    - scripts/l5-init
    - scripts/l5-status
    - .harness/config.yaml
    - .harness/stories/

verification_requirements:
  - Confirm by reading prompts/planner.md that no workflow stage name and no
    may_not_create prefix is stated there outside an injected placeholder.
  - Confirm the rendered planner prompt names every stage the workflow
    defines, every may_not_create prefix it declares, and every blocked
    path in the rules, with no leftover placeholder.
  - Confirm the negative control - the same template with the new
    placeholders removed - fails that coverage. A coverage assertion that
    passes against a stripped template proves nothing.
  - Confirm the walk-up loop that finds .harness/config.yaml appears exactly
    once in the repository, and that l5-run's no-config message and exit
    status are byte-for-byte what they were before the move.
  - Confirm l5-plan exits non-zero and starts no session when run with no
    .harness/config.yaml above the working directory.
  - Confirm build_context still resolves every placeholder each workflow
    stage template uses, to the same text as before.
  - Confirm no committed story artifact was edited and all still parse.
  - Confirm tests/test_story_009_validation.py was created by the tester
    stage, and that the implementer's changed-files record lists no file
    under tests/ at all.

constraints:
  - This story depends on story-008 and must not start until story-008 has
    merged. Both edit prompts/planner.md, scripts/l5-plan, and
    orchestration/context_assembler.py, and this story builds directly on
    the render path story-008 introduced.
  - Standard library only, Python 3.10+, type hints on public functions.
  - Scripts stay thin. The lookup, the loading, and the rendering all belong
    in orchestration; l5-plan wires them together and nothing more.
  - The implementer touches no file under tests/, neither creating nor
    modifying. All new validation for this story is the tester's, in one
    file.
  - Injection replaces the statement of which stage is restricted on which
    path. It does not replace the planner's judgement about stage
    exceptions - the ask-the-developer-first rule is role guidance and stays
    written in the template.
  - The planner session stays interactive and quick to start; reading a
    config, a workflow, and a rules file is acceptable, extra round trips
    are not.
  - This story edits context_assembler, so the prompts stored under
    .harness/runs/story-009/ for later stages are rendered by the module
    version loaded at run start. Judge them as stale and confirm behavior
    from a fresh process.


Acceptance criteria:
- prompts/planner.md names no workflow stage and no may_not_create prefix of its own; both reach the planner through injection. The skeleton's "stage: <the workflow stage expected to change it>" is a field description rather than a stage name and stays.
- The rendered planner prompt names every stage defined in workflows/story-workflow.json — implementer, tester, verifier, documenter — and every may_not_create prefix any of those stages declares.
- The rendered planner prompt lists every path in blocked_paths from rules/execution-rules.json, and says these are enforced repository-wide rather than per story.
- A negative control demonstrates the coverage comes from injection and not from leftover prose - rendering a copy of planner.md with the new placeholders removed does not satisfy the two criteria above.
- The rendered planner prompt contains no leftover {{placeholder}}.
- find_target_root lives in orchestration/harness_config.py and is called by both scripts/l5-run and scripts/l5-plan; the walk-up loop appears once in the repository.
- scripts/l5-plan exits non-zero with the no-config message when no .harness/config.yaml exists in the current directory or above it, and starts no interactive session.
- scripts/l5-run behaves exactly as before the extraction, including the no-config message and its exit status.
- The instruction not to add a stage_exceptions entry without asking the developer first stays in planner.md, along with the explanation of what an exception is for; only the statement of which stage is restricted on which path is replaced by injection.
- build_context's behavior is unchanged - every workflow stage's prompt resolves the same placeholders to the same text as before.
- workflows/story-workflow.json, rules/execution-rules.json, and schemas/ are unchanged, and every committed story artifact still parses.
- The full suite passes with .venv/bin/python -m pytest tests/ -q.

Changed files (implementer's record — hold these changes to the approved
story scope):
{
  "modified": [
    "orchestration/harness_config.py",
    "orchestration/context_assembler.py",
    "prompts/planner.md",
    "scripts/l5-plan",
    "scripts/l5-run"
  ],
  "created": [],
  "deleted": []
}


Tester changed files (tester's record — test files created or modified by
the tester stage; treat them as expected additions of a later stage, not
implementation scope violations):
{
  "modified": [
    "tests/test_story_007_validation.py",
    "tests/test_story_008_validation.py"
  ],
  "created": [
    "tests/test_story_009_validation.py"
  ],
  "deleted": []
}


Implementation summary:
# story-009 implementation summary

Injected the workflow's own declarations into the planner prompt, and gave
`l5-plan` the target-repository lookup it needs to read them.

## Changes

**`orchestration/harness_config.py`** — gained `find_target_root(start)`,
moved verbatim from `scripts/l5-run` (same loop, same `sys.exit` message).
Added `import sys` for it.

**`scripts/l5-run`** — imports `harness_config` and calls
`harness_config.find_target_root(Path.cwd())` instead of its own copy.
Nothing else changed; the no-config message and exit status are unchanged
(verified below).

**`orchestration/context_assembler.py`** — gained
`workflow_context(workflow, rules) -> dict[str, str | None]` beside
`schema_context`, returning `workflow_stages`, `stage_create_restrictions`
(one `<stage> may not create anything under <prefix>` line per declared
prefix), and `blocked_paths`. All three render through the existing
`_dashed_lines` helper. `build_context`'s inline `blocked_paths` join was
replaced by the same `_dashed_lines` call so one function renders both;
the rendered text is identical (`_dashed_lines` returns `None` for an
empty list where the join returned `""`, and `render` substitutes `"None"`
for both).

**`prompts/planner.md`** — the prose sentence "the implementer may not
create anything under tests/" is gone, replaced by
`{{stage_create_restrictions}}`. Added `{{workflow_stages}}` beside the
`likely_file_changes` guidance and `{{blocked_paths}}` beside the scope
guidance. The `stage_exceptions` ask-the-developer-first instruction and
the explanation of what an exception is for both stay, as role guidance.

Two incidental edits so no workflow stage name or blocked path survives in
the template's own prose:
- the skeleton's `- <what the verifier must confirm>` became
  `- <what verification must confirm>` (it named a stage);
- the sentence pointing at `rules/execution-rules.json` now names
  `execution-rules.json` without the directory, because the literal
  `rules/` is itself a blocked path and would have survived a negative
  control that strips the placeholders.

**`scripts/l5-plan`** — now finds the target root, loads the config, loads
the workflow the config names, loads the rules, and renders `planner.md`
against `schema_context` merged with `workflow_context`. It stays thin:
lookup, loading, and rendering are all orchestration calls, and it is
still a single `os.execvp`. Its docstring records the new lookup.

## Verification performed

Rendered `planner.md` from a fresh process against this repository's
workflow and rules:
- no leftover `{{placeholder}}`;
- every stage the workflow defines is present (implementer, tester,
  verifier, documenter);
- the one declared `may_not_create` prefix (`tests/`) is present;
- every blocked path is present (`.git/`, `.harness/runs/`, `rules/`).

Negative control (same template, the three new placeholders removed):
coverage collapses — `tester`, `documenter`, `.git/`, `.harness/runs/`
and `rules/` all disappear. `implementer`, `verifier` and `tests/` remain,
but they come from the injected `{{story_schema}}` (they appear in
`schemas/story.schema.json` at lines 41–106), not from planner.md prose.
The full coverage assertion therefore fails against the stripped
template, which is what the criterion asks for.

Both scripts run from an empty directory with no `.harness/config.yaml`
above it exit 1 with exactly
`No .harness/config.yaml found here or above. Run l5-init first.` on
stderr; `l5-plan` starts no session (it exits before `os.execvp`).

`workflows/`, `rules/`, and `schemas/` are untouched. No file under
`tests/` was created or modified.

## Test suite

`.venv/bin/python -m pytest tests/ -q` → **1 failed, 284 passed, 3 errors**.

All four are pre-existing tests asserting behavior this story deliberately
changes, and all four live under `tests/`, which this story's constraints
forbid me to touch ("The implementer touches no file under tests/, neither
creating nor modifying"). They are the tester's to update:

1. `tests/test_story_007_validation.py::test_no_path_prefix_is_named_in_orchestration_code`
   asserts `may_not_create` appears in no orchestration module except
   `story_coordinator.py`. Story-009's second task requires
   `context_assembler` to render "the stages' `may_not_create` prefixes",
   so a second reader of that key is the story's deliverable. The test's
   first and stronger assertion — that no path *prefix* such as `tests/`
   is named in orchestration code — still passes; only the
   single-consumer allowlist is stale.
2. `tests/test_story_008_validation.py` — the `captured_plan_argv` fixture
   runs `l5-plan` from an empty `tmp_path` and asserts exit 0, which
   errors out the three tests that use it, including
   `test_l5_plan_needs_no_target_repository`. That test asserts precisely
   the property this story reverses: the story description says l5-plan
   "does not currently locate one ... It gains that lookup here", and an
   acceptance criterion requires it to exit non-zero without a config.

Every other test passes, including the whole of
`tests/test_story_coordinator.py` and the context-assembly tests that hold
`build_context`'s placeholder behavior.

## Scope note for the verifier

The acceptance criterion "the walk-up loop appears once in the repository"
is not fully met, and I did not force it. `scripts/l5-status` carries a
third copy of the same loop (`scripts/l5-status:19-24`), and
`scripts/l5-status` is on this story's `do_not_modify` list. The loop now
appears twice: once in `orchestration/harness_config.py` and once in
`scripts/l5-status`. The story's own tasks name only `l5-run` and
`l5-plan` as callers, and both call the extracted function. Pointing
`l5-status` at `harness_config.find_target_root` is a one-line follow-up,
but it is outside the scope I was given.


Test results:
{
  "status": "failed",
  "tests_written": 34,
  "tests_run": 322,
  "tests_passed": 321,
  "tests_failed": 1,
  "failures": [
    {
      "test": "tests/test_story_009_validation.py::test_the_walk_up_loop_appears_once_in_the_repository",
      "issue": "The acceptance criterion 'the walk-up loop appears once in the repository' is not met. The loop that finds .harness/config.yaml appears twice: orchestration/harness_config.py:20-23 and scripts/l5-status:19-23. l5-run and l5-plan both call the extracted harness_config.find_target_root, so the story's named callers are correct, but l5-status still carries its own copy - the same loop with the same sys.exit message. scripts/l5-status is on this story's do_not_modify list, so the implementer left it and flagged it; the criterion as written is still unsatisfied. Every other story-009 criterion passes, including the injection coverage and its negative control."
    }
  ]
}


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


Most recent verifier finding:
None

Retry state:
None
