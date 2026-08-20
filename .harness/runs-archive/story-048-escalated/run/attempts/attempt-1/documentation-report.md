# Documentation report — story-048

## What was updated

`.harness/docs/ARCHITECTURE.md`, and nothing else. The story's plan names this
document as the documenter's only file, and the change is in three places.

### 1. A new section, "A test builds the workflow it needs"

Placed immediately above "Decisions and constraints", because it is a standing
pattern a future planning agent needs before it plans test work, not a
retrospective note about one run. It records:

- **The builder.** `workflow_stage`, `build_workflow`, `StageRef` and
  `materialize_workflow` in `tests/conftest.py`, each with the property that
  makes it what it is rather than a summary of its signature: a key not asked
  for is absent (so "no budget" and "budget of zero" stay distinct), a route
  names a *position* because the builder assigns the name, and the materializer
  exists so a built definition can be **run** rather than only inspected.
- **The three load-bearing details of materialization** a later reader would
  otherwise rediscover: `rules/` and `schemas/` are symlinked at the shipped
  ones deliberately; `copy=` exists because `Path(__file__).resolve()` in a
  `scripts/` entry point follows a symlink straight back to this repository and
  would load the shipped workflow; and `conftest.VERIFYING_STAGE` is written
  once because the coordinator keys verdict handling on that name — a fact
  about the harness, not about this deployment.
- **The fixture seam.** `configured_workflow` plus a `harness_root` override is
  how a module drives the existing `target_root` under a definition this
  repository does not ship. This is the "how to reach for it" a converting
  story needs.
- **The classification.** Subject versus input, why it is decided module by
  module and never by grep, and that `PERMITTED_LIVE_ARTIFACT_READERS` is now a
  mapping to stated reasons rather than a burn-down list.
- **The widened scan.** Two routes, why the helper route matches a *name* when
  the standing rule is "shape, never a name" (there is no path expression left
  in the caller to recognise), why the list had to move in both directions in
  one story, and that the scan's other limits survive the widening.
- **`tests/test_shipped_workflow_is_valid.py`** as the home for a claim about
  this deployment, including displaced configuration assertions.
- **What actually converted**, stated plainly — see below.

### 2. Two story-047 bullets the widening made stale

Both under "Decisions and constraints", both amended in place rather than
rewritten, so story-047's own record survives and a reader does not act on a
claim that stopped being true:

- The `GRANDFATHERED_LIVE_ARTIFACT_READERS` bullet said the list "can therefore
  only shrink" and is a burn-down signal. It is now a classification that can
  legitimately grow, and says so with a pointer at the new section.
- The stated-limits bullet said the scan "cannot see an equivalent read reached
  through a helper in another module", naming `conftest.shipped_workflow` as an
  example of what it misses. That is exactly the limit story-048 narrowed. The
  amendment records the narrowing and that the limit still stands for every
  other helper — the two names are an enumeration, not a general capability.

### 3. Two new bullets in "Decisions and constraints"

- **An empty implementer stage is a plannable outcome.** Why a conversion
  cannot belong to the implementer (the revert check decides its edits by
  reverting them, and a conversion is the edit whose removal leaves the suite
  green), reinforced by `may_not_create: ["{{tests_dir}}"]`. Recorded because
  story-048 is the first story to plan for it deliberately, and the next one of
  this shape should plan the same way instead of inventing scaffolding.
- **A timing measurement needs a named artifact.** See the honest gap below.

## What was deliberately not written

No section on the shipped workflow, the rules, the prompts, the schemas or the
configuration was touched: the story's constraint is that nothing this
repository ships changed, and nothing did. No execution log was transcribed —
stage durations and the run's event stream are already in
`execution-history.json` and are not architectural memory.

## Two facts recorded rather than smoothed over

**The conversion is one module, not thirty.** The plan's
`likely_file_changes` named roughly thirty modules under `tests/`; the landed
change converts `tests/test_story_coordinator.py` and leaves the rest reading
the shipped definition, classified as subject readers with written reasons. The
declared list grew 20 → 30 as the widened scan surfaced readers the path-shape
route never saw. That may well be the classification working correctly, and the
document says so — but it also means `tests/test_self_routing_retry.py` and
`tests/test_retry_routing.py`, the two modules the story's own evidence names
as the coupling it exists to break, still call `conftest.shipped_workflow` at
module scope. The document states this plainly and names the next story's work
list rather than describing the coupling as removed. Judging whether that
outcome satisfies the story's acceptance criteria is the verifier's call, not
this stage's; the documentation records what is true of the tree.

**The "after" runtime is not on the record.** The before figure is —
2449 tests, 474.49 s (7:54.84 wall) at `dcda75d`, in the implementer's summary.
The tester's `test-results.json` records 2479 passing tests and no duration,
and no other run artifact carries one, so the before/after comparison the story
asked for cannot be made from this run's evidence. This stage did not re-run
the suite to manufacture the number: a timing taken on a different machine
state hours later is not the measurement the criterion asks for, and presenting
it as one would be worse than reporting the gap. The architecture document
records the gap, its mechanical cause (`test-results.json` has no duration
field), and the lesson for the next story that asks for a timing comparison.
