# story-028 — Documentation Report

Route a retry to the stage that owns the defect.

One document was updated: **`.harness/docs/ARCHITECTURE.md`**, the only
architecture document this harness maintains. No other document was touched,
and no implementation, test or prompt file was modified.

## What was added, and why

### A new section, "Where a retry goes"

Placed between "The base a story branches from" and "Run directory anatomy",
because it is a decision about routing rather than about a stage, a schema or
the run directory, and none of the existing sections was its home. It records
what a future planning agent cannot recover from the code alone:

- **The defect and its evidence.** The route was a constant with no input, and
  story-011 is the observed case — a defect wholly inside a test file the
  tester created, routed to the implementer, costing both a wrong prompt and a
  stage denied the chance to repair its own output. Recorded as cited evidence;
  story-011 was not re-run or re-judged.
- **The routing table.** The verifier names a category, the workflow says where
  a category goes, and the categories are injected into the verifier's prompt
  from the same table the coordinator routes on. The shipped `implementation`
  and `validation` entries are described, with the note that adding a third is
  a workflow edit and nothing else.
- **Why no default route survives**, which is the decision most at risk of
  being undone by a well-meaning later story. A missing or unknown
  `retry_target` escalates rather than falling back, because a fallback would
  reproduce the old behaviour under a new name while looking fixed. Both
  escalations go through `_escalate` (so `retry_count` is untouched) and sit
  above `archive_attempt` (so no `attempts/attempt-N/` is written), and both
  reasons name the offending value and list the defined categories.
- **Why both sit above the ceiling comparison** — the reason a developer reads
  should be the bug rather than the budget.
- **Why the ceiling is single and global**, at length, because this is the
  passage a later story would otherwise re-derive: one `retry_count`, per-route
  ceilings need per-route counters, and an implementer/tester alternation would
  run 2 + 2 attempts while neither route tripped early — which is the
  non-convergence a ceiling exists to catch. The ceiling protects the run's
  budget; routing decides where an attempt goes.
- **The pre-flight check**, as a two-row table (undefined destination, forward
  destination) with the reasoning for validating at load rather than at retry,
  and the statement that the clean-clone route is held to both.
- **That no category name or destination lives in orchestration**, and that the
  route taken is *recorded* — `retry_category`/`retry_stage` on the history
  entry through the same `append_event` call, and the real destination in
  `retry-history.json`.
- **What was deliberately deferred**: the self-routing mechanism, its two
  budgets, and the warning against conflating them with `retry_count`, so a
  later story picking up `.harness/requests/retry-routing.md` inherits the
  boundary rather than rediscovering it.
- **The divergence from Appendix A's excerpts**, stated as a divergence rather
  than an erratum: the appendix prints the constant `on_failure`, the bare
  `clean_clone` string and the `stage_names` index on the constant, and all
  three are gone. The outstanding Chapter 17 conformance check is carried
  forward with the reason (the manuscript is not in this checkout) rather than
  quietly dropped.

### Existing sections amended where the story changed them

- **Workflow definitions** — the retry route became the routing table; the
  workflow carries no ceiling.
- **`clean_clone`** — rewritten for the widened object declaration, with the
  parallel to `revert_check`'s widening in story-019, and the note that its
  route is not a chooseable category (no `when`, never rendered) but is held to
  the same pre-flight.
- **Artifact schemas** — a new paragraph on `retry_target` (optional, with the
  validator-subset reason recorded in the property's own description, enforced
  by the coordinator) and on `retry_category`/`retry_stage`, plus the fact that
  no schema file was added or removed so the inventory tests were untouched.
- **Prompts** — `{{retry_routes}}` in `verifier.md` naming no category in
  prose, and the retry placeholders `tester.md` gained now that the tester can
  be a retry destination.
- **`story_coordinator.py`** — the sixth pre-flight refusal and the fifth
  `refuse` caller (`_refuse_bad_routing`), `append_event`'s two new keywords,
  and the clean-clone path's destination now read off its own declaration. The
  quoted `stage_names.index(stage["on_failure"]["retry_stage"])` was repointed
  to the expression that replaced it, so the document does not quote deleted
  code.
- **`context_assembler.py`** — `build_context`'s required `workflow` and why it
  is required rather than defaulted, `{{retry_routes}}` in `workflow_context`,
  and `retry_routes(stages)` as the single derivation with the reason it lives
  in this module (import direction) and the statement that the rendering exists
  once.
- **Rules** — `max_retries` is here and nowhere else, held by a test that
  searches the repository rather than inspecting two known files.
- **Story lifecycle diagram** — the retry arrow now goes to the stage the
  reported category routes to, and the escalation arrow covers an unroutable
  verdict as well as an exhausted ceiling.
- **Decisions and constraints** — the clean-clone reroute bullet repointed to
  the widened declaration, and one new bullet on this story's own run: not
  governed by the routing it adds (the coordinator imports its module at start,
  as this story's constraints state), and the widest test ripple yet — eleven
  modules, four uncollectable — with `tests/conftest.py`'s `first_retry_route`
  as the one-home fix and the standing lesson restated under the new key.

## Not recorded

Execution-log detail was left out deliberately: the intermediate suite runs,
the per-module list of repointed assertions, and the process failure the
implementer stage resumed from are in the run directory and the implementation
summary, and none of them is a fact a future planning agent needs. The
`test_story_029_validation.py` control change the verifier flagged as
`unverified` is already covered by the existing frozen-implementation-decay
paragraph, which story-028 is named in; it needed no new passage.
