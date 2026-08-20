# Documentation report — story-048

## Document updated

`.harness/docs/ARCHITECTURE.md` — the one document this story's plan assigns to
this stage, and the only file this stage touched.

This is the documenter's **third** attempt in this run. Attempt 1 wrote the
chapter "A test builds the workflow it needs"; attempt 2 updated it for the
first retry. The tester then ran a third time, converting two more modules,
reclassifying one, adding an agreement check between the two classification
structures and — finally — recording the after runtime. This attempt updates
what that retry changed and leaves the rest of the chapter standing: the
builder, the materializer, the widened scan and the configuration module were
documented in attempt 1, verified sound, and are unchanged.

Every count and figure below was read off the tree and the run artifacts rather
than carried forward from the earlier report; two of the numbers in it had gone
stale, which is why they are re-derived rather than trusted.

## What changed, and why each entry earns its place

**1. A new paragraph on holding the two classification structures to
agreement.** The retry added `contradictory_reasons(permitted, awaiting)` and
`AWAITING_CONVERSION_MARKER` to `tests/test_baseline_honesty.py`, with a control
that plants a reason claiming subjecthood for an awaiting module, observes it
reported, then amends it with the disclosure and observes it accepted. This is
recorded because the failure it prevents is not a coding slip but a bookkeeping
one that produces *unfalsifiability*: a module sitting on
`WORKFLOW_INPUT_READERS_AWAITING_CONVERSION` while its
`PERMITTED_LIVE_ARTIFACT_READERS` reason claims the deployed stage list is what
that module is about leaves the repository documenting two incompatible facts
about the same read, both green, and reading the reason against the module
yields the opposite answer to reading the classification. The general shape is
stated for reuse: when a classification is split across two lists, assert their
agreement, not just each list's own shape.

**2. The conversion paragraph rewritten from three modules to five, plus the
one that moved the other way.** `tests/test_coordinator_contract.py` (subject:
the coordinator's output contract; its `FakeRunner` rewritten from a branch per
stage name to a lookup on each stage's own declaration, and its verifying stage
found by `on_failure` rather than by name) and
`tests/test_shared_baseline_resolution.py` (a two-stage workflow is all
`build_context` needs to render the prompts its assertions are about) are now
converted. `tests/test_planner_injection.py` is recorded separately because it
moved *into* `WORKFLOW_SUBJECT_READERS`: its `build_context` input became a
built workflow, while its remaining shipped read — this repository's planner
template names none of this repository's own stage names — is a subject a built
workflow's invented names could not answer. That is the clearest evidence in the
story that the classification is **per read**, not per module, which a future
planner will need before writing the next conversion story.

**3. The displaced-assertion list grown from six to ten, named individually.**
The two new conversions displaced four more into
`tests/test_shipped_workflow_is_valid.py`: the self-route placeholder in every
shipped template, the budget-outlier-records-why rule and the reason-states-the-
number rule, and the four-stages-ending-at-the-verifier statement. Naming them
individually is what makes the story's "no test removed without a stated
successor" checkable from this document rather than only from the diff. Added
beside it: the two displaced assertions that read the shipped definition's own
numbers keep a non-vacuity guard (`assert checked`, `assert routes`), because a
displaced assertion that finds nothing to check reads exactly like one that
checked and passed.

**4. The debt figures corrected.** 8 subject readers and 14 awaiting conversion
(was 7 and 17), and the permitted list is 28 names, not the 30 the previous
report recorded — the widened scan added names and the conversions removed
others, so the list moved in both directions within the story, as designed.

**5. The runtime bullet rewritten from a gap to a closed loop with a sharper
lesson.** The after figure now exists: the tester's third attempt wrote
`.harness/runs/story-048/suite-runtime.md` carrying both measurements — 2449
tests / 474.49 s (`7:54.84`) before, 2490 tests / 454.72 s (`7:34.95`) after,
**19.9 s recovered while adding 41 tests**, which is the effect the story
predicted. The bullet keeps the lesson but states its mechanical cause rather
than exhorting: the number went missing twice because `test-results.json` has no
duration field, and it landed only when a purpose-made artifact was created for
it. A story asking for a measurement must name the artifact the number goes in.

## What was deliberately not written

- **No new section for the builder, the materializer, the widened scan or
  `tests/test_shipped_workflow_is_valid.py`.** All four were documented in
  attempt 1 and this retry did not change them; re-describing them would be a
  rewrite of a section the story's later half did not affect.
- **The story-047 bullets keep their supersession notes as written.** They are
  still accurate: the permitted list is still a mapping, and the helper-route
  limit is still narrowed to exactly two named helpers.
- **The `conftest.py` substring-narrowing bullet is unchanged.** The tester's
  third attempt did not touch that narrowing.
- **No execution log.** Which stage ran again and in what order is in
  `retry-history.json` and `execution-history.json`; what belongs here is only
  what a future planner must know before writing a story against this suite.
