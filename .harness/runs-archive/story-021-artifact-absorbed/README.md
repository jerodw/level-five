# story-021 — a story artifact absorbed into the story's own commit

Frozen 2026-08-12 by story-029, which deleted the reproduction that was this
defect's only executable account.

## The defect

A run commits the tree it ends on: `_complete` runs `git add -A` and commits.
Before story-021 nothing established what that tree held when the run *began*,
so anything already sitting uncommitted in the target — most sharply, the
story artifact the planner had just written and not committed — went into the
story's own commit. The run's own record of what changed does not name it,
because no stage produced it.

## The observed instance, and where it lives

`.harness/runs-archive/story-013-vacuous-tests/`, which is committed read-only
evidence and is not edited. Two facts in it are the defect:

* `pre-reset-branch.patch` contains `diff --git a/.harness/stories/story-013.yaml`
  under a commit whose subject begins `story-013:` — the story's own commit,
  carrying the story's own definition, 237 lines nothing in the run produced;
* `run/changed-files.json` names four files and none of them is that artifact.

Because the artifact went into the story commit rather than onto the base
branch, discarding the branch for the re-run would have destroyed the only
copy of the story's definition, and it had to be recovered from this archive.

## What the deleted reproduction demonstrated

`tests/test_story_021_validation.py::test_a_story_artifact_no_longer_reaches_a_story_commit`
carried a control that recovered the pre-story-021 coordinator out of git
history, loaded it as a module, and ran it against a fresh fixture whose story
artifact was left uncommitted. It demonstrated, on that fixture:

* the earlier coordinator ran the story to completion and returned 0;
* the uncommitted artifact `.harness/stories/story-002.yaml` was inside the
  story's own commit afterwards;

with the current coordinator on the identical fixture refusing the run
(exit 1) and, once the artifact was committed as `l5-plan` commits it,
completing with the artifact absent from the story's commit.

## Why it is recorded here rather than re-run

The recovered coordinator is run against *today's* workflow, schemas and
config. `clean_clone` has since become an object-valued declaration, so the
recovered code raises `TypeError` before reaching the behaviour it was
recovered to show: the instrument decayed, which is the reason story-029
retires it. The positive half of the assertion — that today's coordinator
refuses the uncommitted artifact and never absorbs it — needs no recovered
module and stays in the suite, and it reads `evidence.json` beside this file.

## Contents

| File | What it is |
| --- | --- |
| `evidence.json` | The defect and the reproduction's demonstrated facts, machine-readable. An assertion in `tests/test_story_021_validation.py` reads it. |
| `README.md` | This account. |
