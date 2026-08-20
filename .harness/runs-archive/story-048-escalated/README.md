# story-048 — escalated after a verifier authorized the outcome it then failed

Preserved 2026-08-19, before the story was restarted. The story did not finish; this keeps
the run because *why* it did not finish is the evidence behind two stories that have since
shipped, and because the reason is not visible from the branch it left behind.

## What happened

Three full retry cycles, roughly two hours of wall clock, and about a third of the assigned
work delivered. The story asked for every test module that resolves the shipped workflow to
be classified and every input reader converted to a fixture. It converted 5 of them and left
14 declared as awaiting conversion.

Ten agent invocations: implementer, then three cycles of tester → documenter → verifier.

| Invocation | Turns | Cost | Delivered |
| --- | --- | --- | --- |
| implementer | 6 | $1.60 | nothing, correctly — the story assigns every file to the tester |
| tester 1 | 109 | $11.89 | the builder, the materializer, the widened scan, the configuration module |
| documenter 1 / verifier 1 | 31 / 19 | $1.75 / $1.04 | failed: the conversion was not performed |
| tester 2 | 133 | $12.99 | 3 modules converted, including the two hardest |
| documenter 2 / verifier 2 | 31 / 28 | $1.54 / $1.90 | failed: 3 converted, 17 still declared |
| tester 3 | 76 | $5.76 | 2 modules converted, 8 disclosure reasons rewritten, the agreement assertion built |
| documenter 3 / verifier 3 | 31 / 14 | $1.54 / $0.97 | escalated: retries exhausted |

About $41 total.

## Why it is worth keeping

**Nothing stopped the tester. It was told it could stop.** Every one of the ten invocations
ends `"terminal_reason":"completed"` — no API error, no truncation, no timeout, no
mechanical failure, `self_route_count` 0. The agent runner invokes `claude -p` with no
`--max-turns` and no timeout, and the harness tracked no spend. The first diagnoses of this
run were all wrong in the same way: they looked for a constraint. There was none.

What there was is in `run/prompt-tester-attempt-3.md`. The guidance for the third attempt
asked for the whole job — *"Empty WORKFLOW_INPUT_READERS_AWAITING_CONVERSION by converting
the seventeen modules it names"* — and then authorized not doing it:

> "For any module that **cannot be converted within this retry**, rewrite its
> PERMITTED_LIVE_ARTIFACT_READERS reason so it names only the non-workflow read that keeps
> it on the list and **discloses that its workflow read is an input awaiting conversion**"

and commissioned machinery whose only purpose is tracking what does not get converted — an
agreement assertion between the permitted list and the awaiting list, with a control. The
tester delivered all of it. That is where attempt 3's 76 turns went, and why it converted
two modules while being the attempt that was told most explicitly to convert seventeen. The
verifier then failed it for the partial result its own guidance had sanctioned, and retracted
the fallback only in the guidance written *after* attempt 3 — *"Batch the work rather than
converting two modules per attempt"* — which no stage ever received, because the budget was
gone.

**The falling rate everyone read as the tester underperforming is the guidance working as
written.** 109 turns, then 133, then 76.

## What came out of it

- **story-049**, `unfinishable_by_retry`: a verifier verdict can report that no number of
  retries closes the gap, escalating on first sighting with the budget unspent. Filed as a
  plan-time size check; planning disproved that axis, because story-038 assigned 35 files to
  one stage and story-044 assigned 21 and both landed first time.
- **story-050**, defective retry guidance: `current_focus` entries now carry a
  `satisfied_when` written before the outcome is known, the verdict echoes every entry
  verbatim, and guidance that sanctioned the outcome it then fails is caught — the verifier
  self-routes to rewrite it rather than the stage being charged for it. This run is that
  story's motivating case.

Both are on `main`. The defect this run exposed cannot recur in the same shape.

## What is in here

`run/` is the complete run directory as it stood at escalation, including all twelve rendered
prompts and the three verification iterations. `run/prompt-tester-attempt-3.md` is the
primary document.

`story-048.log` is trimmed: the full agent log was 16 MB of stream-json, and what is kept is
the stage markers and the terminal result record of each invocation — the fields the table
above is drawn from. The rendered prompts, which are the part worth reading, are in `run/`.

## What was not kept

The branch. `story/story-048` carried about 2,300 insertions, of which the builder, the
materializer and the configuration module were sound and the conversions collided with
stories 049 and 050 — both of which borrowed helpers from `tests/test_retry_routing.py`, a
module this story had rewritten. Rebasing surfaced the collision as 43 failures in modules
this story never touched, which is the limit the story's own scan documents: it *"cannot see
an equivalent read reached through a helper in another module."*
