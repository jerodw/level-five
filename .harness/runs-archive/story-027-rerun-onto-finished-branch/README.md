# story-027 — a finished story re-run onto the branch that already held it

Frozen 2026-08-12 by story-029, which deleted the reproduction that was this
defect's only executable account.

## The defect

A run's `state.json` says whether *this run directory* ended; the branch says
whether the *story* did, and only the first was consulted.
`_checkout_story_branch` reuses an existing story branch rather than resetting
it, so deleting a finished run's directory and running the story again checked
out the branch still carrying the completed work.

Every stage then did its job correctly against it. The implementer opened a
repository where the story was already done, the tester found the tests
written and passing, the verifier verified a tree that genuinely satisfies the
story, and the run completed. The output was a green run that changed nothing,
with nothing in the run directory distinguishing it from a real one.

## What the deleted reproduction demonstrated

`tests/test_story_027_validation.py::test_the_earlier_coordinator_reruns_the_finished_story_and_reports_success`
recovered the pre-story-027 coordinator out of git history, loaded it as a
module, and ran it on a repository whose story had already finished and whose
run directory had then been deleted. It demonstrated:

* the earlier coordinator had no `completion_commits` at all;
* the re-run returned 0, invoked agents, and recorded status `completed`;
* `git diff --name-only <branch head before> <branch>` was empty — the tree it
  "produced" is the tree it started from, so the story's own second commit is
  empty of change against the finished work it re-ran onto;

with the current coordinator on the identical repository refusing the same
move (exit 1), naming the branch, each completion commit found, and the run
directory.

## Why it is recorded here rather than re-run

The recovered coordinator is run against *today's* workflow, schemas and
config. `clean_clone` has since become an object-valued declaration, so the
recovered code raises `TypeError` before reaching the behaviour it was
recovered to show — the decay story-029 retires the practice for. The refusal
itself needs no recovered module: the whole of what the check does is asserted
against today's coordinator in that file, and the assertion that reads this
evidence holds the refusal to the exact move recorded here.

## Contents

| File | What it is |
| --- | --- |
| `evidence.json` | The defect and the reproduction's demonstrated facts, machine-readable. An assertion in `tests/test_story_027_validation.py` reads it. |
| `README.md` | This account. |
