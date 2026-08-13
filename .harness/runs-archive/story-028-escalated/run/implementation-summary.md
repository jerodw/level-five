# story-028 — Implementation Summary

Route a retry to the stage that owns the defect.

This attempt resumed an implementer stage whose first attempt died as a
process failure. That attempt's work was preserved by the escalation commit
(`d8569c2`) and is on the branch; this attempt reviewed it against the story,
found the production change complete, and finished the ripple repair the
process failure interrupted — four modules could not even be *collected*, so
the suite the stage is required to run had never been run green.

## What changed

**`workflows/story-workflow.json`.** The verifier's `on_failure` body is now a
`retry_routing` table: `implementation` → implementer and `validation` →
tester, each with a `when` description the verifier chooses between. Both
`retry_stage` and the workflow's duplicate `max_retries` are gone — no default
route survives, and the ceiling now lives only in `rules/execution-rules.json`.
`clean_clone` widened from a bare artifact name to
`{"result": ..., "retry_stage": ...}`, the shape `revert_check` took in
story-019, so one key still turns the whole check on and its route is declared
rather than borrowed from the verifier's table.

**`schemas/`.** `verification-result` gains an optional `retry_target`, whose
description records why it is not in `required`: the validator subset has no
`if`/`then` and no `dependentRequired`, so "required when `retry_recommended`"
is inexpressible, and requiring it unconditionally would force a routing key
onto a passing verification. `execution-history` gains `retry_category` and
`retry_stage`, so the route taken is reconstructable from the history alone.
No file was added to or removed from `schemas/`; the manifest is untouched.

**`orchestration/context_assembler.py`.** `retry_routes(stages)` is the single
derivation of the workflow's (declaring stage, category, destination, when)
triples, returned as frozen `RetryRoute`s. It lives here rather than in the
coordinator because the coordinator imports this module and not the reverse.
`workflow_context` renders those routes as `{{retry_routes}}` through the
existing `_dashed_lines`, so there is exactly one rendering of them.
`build_context` takes a required keyword-only `workflow` and merges
`workflow_context` into the assembled context, and takes the optional
`retry_category`/`retry_stage` that the injected retry state now carries —
omitted rather than sent as null when absent, the optional-by-absence
convention the history schema already uses.

**`orchestration/story_coordinator.py`.** A pre-flight
`retry_routing_problems(stages)` sits beside `stage_exception_problems` and is
called from `run_story` above the run-directory creation, refusing through the
shared `refuse` (via `_refuse_bad_routing`): a destination the workflow does
not define, and a destination that does not sit strictly before the stage
declaring it, with the message saying that routing forward would skip
verification. Both the `retry_routing` entries and the widened `clean_clone`
route are held to it. Both constant route lookups are gone: the
verification-failed path reads `routes[verdict["retry_target"]]["stage"]`, and
the clean-clone path reads the destination off its own declaration. Two new
escalations sit **above** the ceiling comparison and above `archive_attempt`,
each through `_escalate` so `retry_count` is untouched and no
`attempts/attempt-N/` is written: a recommended retry carrying no
`retry_target`, and one naming a category the workflow does not define. Each
names the offending value and lists the categories the workflow defines.
`append_event` gained `retry_category` and `retry_stage` keywords, passed from
the retry and escalation sites that have them, so `events.log` and
`execution-history.json` stay two renderings of one write.

**Prompts.** `prompts/verifier.md` injects `{{retry_routes}}` beside the
verification-result schema and states the obligation to name one, naming no
category in prose. `prompts/tester.md` gained `{{retry_guidance}}` and
`{{retry_state}}`, which it has never needed until the tester could be a retry
destination.

No category name and no routing destination is written into either
orchestration module; both come off the loaded workflow.

## Existing tests repointed, never weakened

Reshaping `on_failure` and widening `clean_clone` turned assertions red across
eleven existing modules. Every one was repointed at the definition's new
shape with its subject and strictness intact:

- `tests/conftest.py` gained `first_retry_route(workflow)` — one home for
  "which category does this workflow declare, and where does it route" — and
  every module that drives a retry reads the pair from it rather than writing
  its own derivation. Failing verdicts in those modules now carry a
  `retry_target` read off the loaded workflow.
- Every `build_context` call site passes the workflow it now requires.
- `test_story_014` reads the clean-clone result name off `result` and the
  clean-clone route off `retry_stage`, both from the widened declaration; its
  renamed-declaration probe renames `result` inside the object; its "never runs
  the check" mutant matches the current source line; its ceiling assertion
  reads the rules' `max_retries`, which is now the only definition of it.
- `test_story_011`'s optional-history-field set is exact and gained the two new
  names; `test_story_012`'s planted mutation call was repointed at the
  category-keyed lookup.
- `test_story_029`'s green-then-red demonstration took its control coordinator
  from a pinned past revision; that pinned coordinator cannot run against a
  workflow this story reshaped, so every case was failing its *control* rather
  than its subject. It now runs against the coordinator as it stands. Each
  `old` literal still matches exactly once and the green-then-red pair is
  unchanged — this is the frozen-implementation decay the architecture document
  already records, met once more.

No test was weakened, skipped or deleted, and no test file was created — the
story's regression coverage (`tests/test_story_028_validation.py`) is the
tester's.

## Test suite

`.venv/bin/python -m pytest tests/ -q` → **1443 passed in 295.03s**, run from
the working tree at the end of this stage. The two intermediate runs are worth
recording: the first could not collect four modules at all (`KeyError:
'retry_stage'`), and the second reported 56 failed / 9 errors — all of it
decay from the two reshaped workflow keys, none of it a defect in the routing
change itself.

## Not in this story

The generalized self-routing mechanism — mechanical failures re-running in
place, `self_route_count` against a per-stage `max_self_routes`, the
`prompt-<stage>-attempt-N-try-M.md` naming, coordinator-authored evidence — is
deliberately absent and nothing here presumes it. `retry_count` is still spent
by backward routes only. `retry-guidance.json` and its schema are unchanged.
`.harness/docs/ARCHITECTURE.md` is the documenter's.

As the story's constraints state, this run executes under the coordinator
process that imported its module before these changes existed, so the routing
and the new pre-flight govern the *next* run rather than this one. Expected,
not a defect.
