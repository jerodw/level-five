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
