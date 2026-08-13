# story-028 — escalated when its implementer's process died mid-response

Preserved 2026-08-13, after story-028 was resumed and merged. The story succeeded; this
keeps the attempt that did not, because the *reason* it failed is evidence for work that
is still open.

## What happened

The implementer ran for 34 minutes and 127 turns and then the connection dropped. From the
result record at the end of `story-028.log`:

    "terminal_reason":"api_error", "is_error":true,
    "duration_api_ms":898730, "num_turns":127, "total_cost_usd":14.115618999999997
    "result":"API Error: Connection closed mid-response. The response above may be incomplete."

The coordinator saw a non-zero exit, escalated with `retry_count` untouched, committed what
the run had left, and stopped. Nothing the implementer did caused it and no verdict was
involved: the run reached the first stage of four and ended there.

## Why it is worth keeping

**It is the observed case for `self-routing-retries.md`.** That request's conversion table
has `{name} agent process failed` as its first row, and the row was written from reasoning
rather than from a run. This is the run. A stage that dies for an infrastructure reason is
not making a judgement about the work, and here that is demonstrable rather than argued —
`$14.12` of implementer work was left needing a manual resume because one connection
dropped.

**It is also the case that produced two further stories.** Recovering it exposed the branch
this story had been cut from, which became story-030, and the resume's collision with the
retired differential instruments is part of why story-029 ran first. Neither is visible
from the merged history.

## What is here

- `run/` — the run directory as it stood when the story finished, so it holds both the
  escalated attempt and the resume that completed it. The escalated attempt is
  `attempts/attempt-1/` and the `escalated` entry in `events.log` and
  `execution-history.json`; `escalation-summary.md` is the summary that attempt wrote.
- `story-028.log` — **trimmed to the escalated attempt only**, ending in the result record
  quoted above. The full log ran to 9.4 MB because the resumed run appended to it; the
  remainder is the successful run and is not what this archive is for.

## What it is not

Not a defect in the story's work. story-028's subject — routing a retry to the stage that
owns the defect — was implemented and merged, and the code here is a partial attempt that
was superseded rather than repaired. Read `run/attempts/attempt-1/` as the state of an
interrupted attempt, not as a version of the story.
