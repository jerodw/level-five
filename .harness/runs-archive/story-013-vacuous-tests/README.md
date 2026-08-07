# story-013 — the run that passed verification with tests that could not fail

Preserved 2026-08-07, before re-running story-013 against a repaired harness.

This run is kept because it is evidence for two open story requests
(`verify-in-a-clean-clone.md` and `tests-must-be-shown-to-fail.md`), and both cite it.
It is not an escalation. The run succeeded — no retry, verification passed on the first
iteration, `test-results.json` recording `460/460` — and that is exactly what makes it
worth keeping.

## What went wrong

`_complete` commits the working tree as the run's last act, after the verifier and the
documenter. Three of the tester's assertions measure the gap between the working tree
and `HEAD`, so the commit that ends the run is what invalidates them.

**One test fails outright.** `test_the_implementer_added_no_test_file_and_no_test_function`
asks `git status --porcelain -- tests/` for the files the story added and expects the
tester's own new file. Post-commit the file is tracked and clean, the answer is empty,
and the assertion fails. On the committed branch the suite gives `1 failed, 459 passed`.

**Two tests pass while checking nothing.** `test_the_coordinator_is_untouched_by_this_story`
and `test_no_shipped_schema_was_edited_by_this_story` assert `git diff HEAD` is empty
for a set of paths. On a committed tree that diff is empty for *every* path. The story
modified `tests/test_schema_validator.py` by 22 lines and `git diff HEAD` on that file
returns zero, so the first of those would pass even if the story had rewritten the
coordinator.

The verifier's clean verdict is honest. The suite genuinely passed when it ran, in the
only environment where these assertions mean anything — the one the coordinator then
destroys by committing.

The rule was already written down: `.harness/docs/ARCHITECTURE.md` records that a
differential test must not resolve its baseline as `HEAD`, added by story-011's
documenter, in a file `.harness/config.yaml` injects into every stage. The tester had it
and wrote `git diff HEAD` four times regardless. The same idiom appears in stories 007,
008, 009 and 010, all merged.

## Contents

- `run/` — the complete run directory as the coordinator left it, including
  `verification/iteration-1.json`, the passing verdict
- `story-013.yaml` — the story artifact. It was never on `main`, so this is the only
  copy once the branch goes; restore it to `.harness/stories/` before re-running
- `pre-reset-branch.patch` — `git format-patch main..story/story-013`, the whole branch
- `pre-reset-test_story_013_validation.py` — the tester's file at the state described
  above, for reading without applying the patch
- `story-013.log` — the run log

## Re-running

The harness fixes come first; this run is what they must catch. Then restore
`story-013.yaml`, delete `.harness/runs/story-013/` (the coordinator refuses to re-run a
finished story until you do), reset the branch, and run it again. The re-run is expected
to produce a different and better outcome; this directory is what it should be compared
against.
