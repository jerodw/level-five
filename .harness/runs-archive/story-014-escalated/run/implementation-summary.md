# story-014 Implementation Summary

## What changed

**`schemas/clean-clone-result.schema.json`** (new). The coordinator-written
record of the clean-clone run: `ran`, `command`, `exit_code`, `clone_path`,
`output_tail`, all required. It stays inside the `type`/`required`/
`properties` subset `schema_validator` supports, sets no
`additionalProperties`, and uses no union keyword. Like
`execution-history.schema.json` it is coordinator-written, so it appears in no
stage's `schemas` map and no agent is asked to satisfy it.

**`workflows/story-workflow.json`.** The verifier stage gains
`"clean_clone": "clean-clone-result.json"`, beside its existing `outputs` and
`schemas` keys. The artifact name comes off the loaded definition; deleting
that one key disables the check with no change to orchestration code.

**`schemas/execution-history.schema.json`.** Two entries added to the `event`
enum: `clean-clone-passed` and `clean-clone-failed`. Nothing else changed; the
optional-by-absence convention and the frozen log line format are untouched.

**`orchestration/story_coordinator.py`.** New module-level code:

- `CleanCloneResult`, a frozen dataclass carrying the five recorded fields.
- `_build_clone(target_root, clone)` — `git clone --no-hardlinks <local path>`
  (a filesystem path, so no network access is possible by construction), then
  the target's working tree applied *into the clone*: tracked edits as
  `git diff HEAD --binary` fed to `git apply`, untracked-but-not-ignored files
  from `git ls-files --others --exclude-standard` copied by name, then
  `git add -A` and a commit inside the clone. Files `.gitignore` excludes are
  carried by neither path, which is what makes the clone's contents the same
  set `_complete`'s `git add -A` would commit. The target repository is only
  read; no commit, branch, index write or stash happens there.
- `run_clean_clone(target_root, test_command, scratch)` — builds the clone,
  symlinks the target's `.venv` into it (the configured interpreter lives in a
  gitignored directory, so the environment is linked while only the source is
  under test), runs the caller's command with the clone as the working
  directory, and returns the result with the last 200 lines of combined
  stdout/stderr.
- `_clean_clone_check` — wraps the run in a `tempfile.TemporaryDirectory`
  (outside the target repository, removed whatever the result) and writes the
  record to the run directory under the caller's artifact name.
- `_clean_clone_passed`, `_clean_clone_failed`, `_clean_clone_escalation_reason`
  — the two events and the escalation prose.

The call sits in the verifier branch of `run_story`, inside the
`status == "passed"` case, immediately after the `verification passed` event
and therefore before the documenter and before `_complete`. Zero exit falls
through to the existing advance. Non-zero takes the retry path the
verification-failed branch already takes — `archive_attempt` above the
increment, then increment, save, event, and `index = stage_names.index(
stage["on_failure"]["retry_stage"])` — and the existing escalation path at the
ceiling. No new `RunState` field, no new retry axis, no second write path for
`events.log` or `execution-history.json`, and the coordinator writes no
`retry-guidance.json`.

**`orchestration/context_assembler.py` and `prompts/implementer.md`.** A
`clean_clone_result` context value reading `clean-clone-result.json`, and a
`{{clean_clone_result}}` placeholder in the implementer's runtime state layer.
A clean-clone retry has no verifier finding behind it — the verifier passed —
so this is how the retried implementer receives the evidence.

**`tests/test_schema_validator.py`, `tests/test_story_004_validation.py`.**
Both schema inventories gain `clean-clone-result` and both still assert exact
set equality.

## Decisions

- **Placement inside the passed branch, after the event.** The check is
  unconditional for every story that reaches a passing verifier; there is no
  heuristic about what the story touched.
- **A clone, not a tree copy.** A copy would carry `.venv/` and
  `.harness/runs/` and would not have the story as a commit, which is the
  whole point: a history-walking test that resolves a baseline as
  `git show HEAD:...` only misbehaves once the story *is* HEAD.
- **Reroute rather than escalate.** A clean-clone failure is a defect in the
  implementation, which is what a retry addresses; the verifier's own
  `on_failure.retry_stage` names where to send it.
- **The clean-clone artifact is not in the verifier's `schemas` map.** It is
  coordinator-written, following `execution-history.json`. One consequence:
  `archivable_artifacts` does not name it, so a second attempt overwrites the
  first attempt's record rather than archiving it. The story's task list asked
  only for the `clean_clone` key, so I did not widen the declaration.
- **`.harness/docs/ARCHITECTURE.md` was left alone.** The story's task list
  names it, but `technical_plan.likely_file_changes` assigns it
  `stage: documenter`, and that field exists precisely to settle which stage
  owns a file. The documenter has the material it needs in this summary.
- **Two coordinator helpers exist for indentation, not only for style.**
  `tests/test_story_011_validation.py`'s mutation fixture replaces the first
  occurrence of a 20-space-indented `retry_decision="retry",` line. An inline
  clean-clone retry branch nests deeper and would have been matched first,
  silently mutating the wrong call site and making that non-vacuity test pass
  for the wrong reason. Hoisting the event to module level keeps the mutation
  landing where it was aimed.

## Two pre-existing tests I had to repair

`tests/test_story_009_validation.py` and `tests/test_story_010_validation.py`
each asserted `git diff HEAD -- <path>` was empty for the paths their stories
left alone. That asks "is the working tree dirty here", which is a question
about whoever is working right now: it is vacuously green once anything is
committed and red for every later story that legitimately edits one of those
paths — this story edits `schemas/`, `workflows/`, `prompts/` and
`context_assembler.py`, so all six went red. Commit 3b05b99 fixed the same
defect in story-011's prompt-scope assertion and explicitly left these four.

They are now bounded at both ends, the same way: walk the marker file's
history for the oldest revision carrying the feature that story introduced
(`workflow_context` for story-009, `archive_attempt` for story-010), and diff
that commit against its parent. This is strictly stronger than what was there
— the assertion now says what its name says and keeps saying it forever,
rather than becoming vacuous — and nothing was relaxed or skipped. While a
story is still uncommitted the marker resolves to `None` and the working tree
is the end bound, which is the original comparison.

## Test suite

`.venv/bin/python -m pytest tests/ -q` → **417 passed, 9 failed.**

All nine failures are one root cause, in one file, and I could not fix it:
`tests/test_story_011_validation.py` is in this story's `do_not_modify` list.

    test_events_log_lines_are_byte_identical_to_the_pre_story_format[happy_path]
    test_events_log_lines_are_byte_identical_to_the_pre_story_format[retry_then_pass]
    test_the_legacy_run_wrote_no_history_and_this_one_did[happy_path]
    test_the_legacy_run_wrote_no_history_and_this_one_did[retry_then_pass]
    test_l5_status_renders_a_run_identically_before_and_after[happy_path]
    test_l5_status_renders_a_run_identically_before_and_after[retry_then_pass]
    test_l5_status_through_the_script_is_unchanged[happy_path]
    test_l5_status_through_the_script_is_unchanged[retry_then_pass]
    test_a_run_whose_history_keeps_disappearing_routes_identically

Its `both_implementations` fixture loads the pre-story-011 coordinator out of
git history and drives the same run shape through both implementations
against the repository's real workflow, then asserts the two runs produced the
same `events.log` messages, the same line count, and run directories differing
by exactly `{"execution-history.json"}`. That is not "story-011 changed no
existing event"; it is "no story may ever add an event or an artifact again",
and it is the same over-broad-scope defect commit 3b05b99 fixed one instance
of in this very file. Any story that adds a coordinator event breaks it —
story-014 is simply the first to try.

It cannot be satisfied by implementing differently. The legacy coordinator
predates the `clean_clone` key and will never run the check, so as long as the
shipped workflow declares it, the current coordinator writes one extra
`events.log` line and one extra file. The only fix is to bound those
assertions the way story-011's prompt-scope assertion was bounded — pinning
the current coordinator to the *story-011-era* workflow definition rather than
to whatever the repository ships — and that edit is in a file this story
forbids me to touch. I left it forbidden and am reporting it rather than
widening my own scope.

Everything else is green, including the two inventory assertions, all of
`test_story_010_validation.py`'s archive tests, and the non-vacuity mutants.

## Behavior verified beyond the suite

Driven through `run_story` with a fake runner against a throwaway target
repository (script written, run, and deleted; it created no test file):

- Green in both environments: exit 0, documenter runs, `retry_count` stays 0,
  one `clean-clone-passed` event in `events.log` with a matching structured
  entry, `clean-clone-result.json` valid against its schema.
- Failing only in the clone (`test ! -f src/new.py`, where `src/new.py` is
  untracked in the target and therefore present in the clone): the documenter
  never runs, `retry_count` reaches 2, `attempts/attempt-1/` and
  `attempts/attempt-2/` both appear, execution reroutes to `implementer` each
  time, and the run escalates with a reason naming the clean-clone check.
- Same failure with `max_retries: 0`: escalates immediately, no retry, no
  archive.
- Clone contents: the tracked edit is present, the untracked non-ignored file
  is present, the gitignored `secret.txt` is absent, and `git log` in the
  clone shows the story on top of the original history.
- On both failing runs the target's `HEAD` and `git status --porcelain` were
  byte-identical before and after, and `clone_path` no longer existed after
  the run.

## Note on this story's own run

Per the story's constraints, story-014 is not governed by the check it adds:
the coordinator loaded the workflow definition at run start, before the
`clean_clone` key existed in it. Enforcement begins with the next story.
