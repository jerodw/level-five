# Documentation report — story-048

## Document updated

`.harness/docs/ARCHITECTURE.md` — the one document this story's plan assigns to
this stage, and the only file this stage touched.

This is the documenter's **second** attempt in this run. The first wrote the
chapter "A test builds the workflow it needs" against the tree the tester left
at attempt 1; the verifier then failed that tree and the tester ran again,
converting two more modules and adding a second-tier classification. This
attempt updates what the retry changed and leaves the rest of the chapter
standing — the builder, the materializer, the widened scan and the
configuration module verified as sound in attempt 1 and are unchanged.

## What changed, and why each entry earns its place

**1. A new paragraph on the second-tier workflow classification** (in "A test
builds the workflow it needs"). The retry added `WORKFLOW_SUBJECT_READERS` (7
names with reasons) and `WORKFLOW_INPUT_READERS_AWAITING_CONVERSION` (17 bare
names) to `tests/test_baseline_honesty.py`, asserted disjoint and asserted equal
in both directions to the modules the scan reports a *workflow* read for. This
is a structural fact a future planner needs: `PERMITTED_LIVE_ARTIFACT_READERS`
answers "may this module read a live artifact at all" across five families, so
it cannot carry the workflow-family debt — a module reading the shipped rules
legitimately and the shipped workflow as an input has one entry and one reason
there. The finer split is what makes each future conversion *move a name*
rather than quietly vanish, and it is the next story's work list. Recorded
because a planner reading only the permitted list would conclude the burn-down
had been abandoned.

**2. The conversion paragraph rewritten from one module to three.** It said
`tests/test_story_coordinator.py` alone was converted and that the two modules
the story's evidence named still read the shipped definition. Both are now
converted, and the paragraph states what each builds and why a workflow is an
input to *its* subject — the budget shape for `test_self_routing_retry.py`, the
routing table for `test_retry_routing.py`. Added beside it: the six assertions
displaced into `tests/test_shipped_workflow_is_valid.py`, named individually, so
the story's "no test removed without a stated successor" is checkable from this
document rather than only from the diff. And the debt restated honestly — 7
subject readers, 17 awaiting conversion, permitted list 20 → 30 as the widened
scan surfaced readers the path-shape route never saw.

**3. A new constraints bullet on narrowing a substring scan over the shared
module.** `tests/test_git_history_loading_retired.py` asserts `conftest.py`
freezes no configuration key; the builder now writes
`declaration["clean_clone"] = ...`, which is the opposite of a frozen copy, so
the marker narrowed to the mapping-literal form `'"clean_clone": '` with a
control that constructs that form. This is the *second* narrowing of the same
assertion — story-038 was the first — which is what makes it a pattern worth
recording rather than an incident: a substring scan over a shared module goes
stale every time the shared module gains a new way to mention a name.

**4. The runtime bullet corrected and sharpened.** The passing count moved 2479
→ 2488, and the **after** wall-clock is *still* absent from every run artifact —
`test-results.json` records counts and no duration. The bullet now says the gap
survived a retry that named it explicitly, which is the evidence for the
standing lesson: guidance does not place a number that no artifact has a field
for. The **before** figure remains on the record (2449 tests, 474.49 s /
`7:54.84` wall at `dcda75d`).

## What was deliberately not written

- **No new section for the builder, the materializer, the widened scan or
  `tests/test_shipped_workflow_is_valid.py`.** All four were documented in
  attempt 1 and the retry did not change them; re-describing them would be a
  rewrite of a section this story's second half did not affect.
- **The two story-047 bullets keep their supersession notes as written.** They
  are accurate after the retry — the list is still a mapping, the helper-route
  limit is still narrowed to exactly two named helpers.
- **No estimate stands in for the missing runtime.** The story asked for two
  measurements; one exists, and the document says so rather than manufacturing
  the second.
- **No execution log.** The retry's mechanics (which stage ran again, in what
  order) are in `retry-history.json` and `execution-history.json`; what belongs
  here is only what a future planner must know before writing a story against
  this suite.
