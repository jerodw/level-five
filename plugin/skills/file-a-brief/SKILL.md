---
name: file-a-brief
description: File a story brief into the harness outbox. Use when the developer wants a harness improvement, defect, feature or refactor written up and filed so it can later be planned into a story, or when they ask you to file the brief you have just written.
---

# Filing a story brief

A brief is a pre-planning artifact: one piece of work, the case for it, and
the evidence behind it. It may state a defect, a feature the harness does not
have and should, or a refactor that changes the structure and not the
behaviour.

Filing one puts it in the harness's durable local queue, from which it reaches
a tracker when that queue is next drained. **Nothing executes a brief and
nothing is authorized by one.** It becomes work only when the developer plans
it into a story through `l5-plan`'s interview, and that interview is where the
mandate is conferred. So a brief nobody wants costs a human reading it and
deciding no — which is why filing one on the developer's word is safe, and why
filing one they did not ask for is not.

## What you are filing

`schemas/story-brief.schema.json` in the harness checkout is the shape, and it
says why each part of it is where it is. Read it rather than working from
memory. Its required parts are a title, a slug, a body, a category, a
severity, a confidence, an effort and the workflow the work should be planned
under; it may also carry the paths the work is about and what a story planned
from it should deliberately leave alone.

### The slug

The slug is part of what the brief is **filed under**, so two writings of one
piece of work must derive the same one or the second is filed as a duplicate.
Derive it by the rule the harness states:

- Name the work itself — the defect, the missing behaviour, the structure to
  be changed — and name neither the fix nor the file.
- Three to six words, lowercase, hyphen-separated, no digits unless the work
  is genuinely about a particular number.
- Describe it in the most general terms still true of this work and not of its
  neighbours.

The title is prose for a human scanning a list and is deliberately not part of
what the brief is filed under, so it may be as readable as you like.

### The paths, and where the evidence goes

**The paths a brief carries are bare repository-relative paths**, with no line
number, column or selector appended. That is mechanical rather than stylistic:
a path carrying a line number is filed under a marker no later query asks
about, which defeats the already-filed check for everyone who comes after you.

Line-level evidence is the standard and it belongs in the **body**, cited as
`file:line`, where a person reads it rather than a script searching for it.
Work that does not exist yet has no line to cite and needs none; make its case
from what the system does instead.

## Filing it

1. **Write the brief** as a JSON document satisfying the schema.

2. **Show it to the developer** and ask whether to file it. Show the whole
   brief — the title, the slug, the category, the severity, the confidence,
   the effort, the workflow and the body — not a summary of it, because what
   is filed is what they are agreeing to. Do not file it until they say so.

3. **Write the document to a file** the session can write to, and pass its
   path to the entry point:

   ```
   "$L5_HARNESS_ROOT/orchestration/brief_filing.py" <path-to-the-document>
   ```

   `L5_HARNESS_ROOT` is set for you by the command that started this session.
   If it is not set, the entry point sits beside this skill's own plugin
   directory, at `${CLAUDE_PLUGIN_ROOT}/../orchestration/brief_filing.py`.

   The entry point holds no judgement of its own: it validates the brief,
   refuses a workflow the harness does not define, derives the identity the
   brief is filed under, asks what is already filed, and enqueues once for a
   brief that survives all of that. **Exit status zero means a brief was
   enqueued and non-zero means nothing was.**

4. **Remove the document** once the entry point has answered. It is an input
   to the filing, not a record of it; the queue holds the record.

5. **Report what it said, and report a drop as a drop.** Read the status
   rather than the prose to decide whether anything was filed, and then say
   which of these happened:

   - filed, with the key it was filed under;
   - already filed — a tracker reported it;
   - already filed by this harness — the local queue holds it landed;
   - already queued — the local queue holds it written down and no tracker
     has seen it yet;
   - malformed, naming the field that failed;
   - naming a workflow the harness does not define;
   - dropped by the queue, which is an item that was lost and never a key.

   If the entry point says dedupe did not run, say so: the brief was filed
   anyway, and it may duplicate something already filed elsewhere.

Nothing you file here reaches a tracker by itself, and nothing about a brief
already filed is changed by filing another. Say that plainly rather than
implying the work has been picked up.
