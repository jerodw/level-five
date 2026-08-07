# story-014 — escalated on a scope conflict it could not resolve

Preserved 2026-08-07, before re-running story-014 against a repaired harness.

The run reached the verifier and stopped: `verification failed and the verifier did
not recommend a retry`. That verdict was correct, and the reasoning is in
`run/verification-result.json`.

## What happened

story-014 declares `clean_clone` on the verifier stage in the shipped workflow. Six
tests in `tests/test_story_011_validation.py` ran the pre-story-011 coordinator beside
the current one and asserted identical output. The historical coordinator predates the
key and never runs the check, so on the two shapes that reach a passing verifier the
current run carried one extra `events.log` line and one extra artifact. Nine failures,
all of them that comparison.

The verifier declined a retry rather than routing one, because no bounded retry could
have fixed it: `tests/test_story_011_validation.py` is in story-014's `do_not_modify`,
its differential runs read the shipped workflow through the real `harness_root`, and
story-014's own criteria require the check to be unconditional. Satisfying the story
and satisfying those assertions were mutually exclusive. It named the two ways out —
bound the assertions to a story-011-era workflow, or amend story-014 — and left the
choice to a human.

story-016 took a third path: it retired the six comparisons and restated their
guarantees as direct assertions in `tests/test_coordinator_contract.py`. With that
merged, story-014's blocking issue is gone.

## Why this run is kept

It is the first escalation caused by a differential test outliving its story, and it
is the evidence behind story-016 and behind the `ARCHITECTURE.md` rule that such an
instrument should be retired once the constraint it was built for has landed.

Its implementation was never committed. The escalation left the work uncommitted in
the tree, where story-016's run swept it into that story's commit via `git add -A`;
story-016's branch was rebuilt without it. The re-run regenerates it, so only the
run's artifacts are preserved here.

## Contents

- `run/` — the run directory as the coordinator left it, including
  `verification/iteration-1.json` and `escalation-summary.md`
- `story-014.log` — the run log

The story artifact is on `main` at `.harness/stories/story-014.yaml` and has since been
amended to point the clean-clone check at Python 3.10 rather than the interpreter the
harness runs under.
