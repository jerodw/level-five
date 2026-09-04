[Prose Layer]

This applies to every word you write that a human later reads — a comment, a
docstring, a schema description, a story artifact, a report, a document — and
to every word you say to a person in a session, where they read it as you say
it.

Do not write the count of a list you are about to write. "Four assertions:"
followed by six names, "converted 8 of 22" beside a breakdown reading 0, then
3, then 2 — in each case the number and the list said the same thing, the list
was right, and the number went stale the moment anything was added. Delete the
count and that whole class of defect disappears, because there is no longer a
second statement to disagree with the first. Say "the assertions below" and let
them be counted by whoever needs a number.

The exception is a count the adjacent content does not already carry: a
measurement, a budget, a bound, a figure from a source the reader cannot see
from here. Those are facts rather than restatements, and a reader has no other
way to get them.

When a count that matters is not adjacent to what it counts, this repository's
habit is to hold it with a test rather than with prose — an assertion that goes
red when the number and the thing it counts stop agreeing. A number nothing
checks is a number that will be wrong, and a reader has no way to tell which
one they are looking at.

When you ask a person to decide something, or explain something to them in a
session, lead with the consequence rather than with the code. They are not
reading the file while they answer, and a question they cannot evaluate
without opening three files gets a guess for an answer. So a question carries
what is being decided, what each option costs or risks, and what will happen
by default if they have no preference. The citation goes beneath the question,
or on request; it is not the lead.

That is a rule about altitude and not about detail, so neither half of it is a
licence. Do not trade away precision: a decision stated so loosely that no
plan can be built on it is worse than the question it replaced. And do not
stop naming things: identifiers, paths and line numbers are how a person who
asks where something is finds it, and they get the path and the line.

As it was given:

    Escalated: governed_edits returned three paths under the restricted
    prefix, and run_clean_clone exited 0 with those edits undone, so permitted
    is False at story_coordinator.py:1482.

Pitched at the decision:

    The run stopped because three of the files it changed are ones that
    stage may not own, and undoing those changes still leaves everything
    passing — so nothing can establish the changes were needed. You can say
    in the story that this stage owns those files, which is a judgement you
    are recording for a reviewer, or move that work to the stage that may
    own them, which costs a re-plan. Doing nothing leaves the run stopped
    where it is. (story_coordinator.py:1482 is where it decided, if you want
    to read it.)
