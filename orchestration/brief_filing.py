#!/usr/bin/env python3
"""Filing one brief a developer asked for, and the entry point that invokes it.

The harness has had two things a brief can be written by and one thing that
files them. The Inspector reads a scope of code deliberately and enqueues what
survives; an assist session, which the developer talks to, could write a brief
and then had nowhere to put it, so the brief existed as text in a terminal and
reached nothing. This is the other half of that: the deterministic filing of
one brief, so that an assist session's brief and an inspection's are the same
artifact filed the same way.

**It is the outbox rather than a directory of files, and that is the design.**
A second producer path writing files would have to rebuild everything the queue
already provides beside it — the dedupe identity, the idempotency key, the sync
transport, the landed/pending/failed record — and dedupe would never see those
briefs, so the Inspector would go on filing findings a developer had already
filed by hand. Enqueueing costs nothing extra and makes the two
indistinguishable downstream, which is right, because they are the same
artifact differing only in what noticed the work. A target that wants briefs as
local files gets them by configuring a files-in-source-control sync command and
filed-query command, which the filing design names as a first-class target
rather than a workaround.

**Nothing here plans, and nothing here writes to the repository.** A filed
brief becomes a story through l5-plan's interview and the mandate is conferred
there; this stamps no mandate, writes nothing beneath the stories directory,
marks no brief planned, and changes the status of nothing already filed. What
it widens is what an assist agent may do — it writes, not to the repository,
but to a tracker outside it, through a command the harness invokes — and the
case for that is that a brief is inert: nothing executes one, nothing is
authorized by one, and a brief nobody wants costs a human reading it and
deciding no. The producer discipline the outbox already imposes is what keeps
an unwanted brief from being filed repeatedly.

**Every judgement is here and every word a developer reads is the entry
point's**, in the shape `brief_fetch` and `plan_commit` already take: the
functions below return what happened rather than printing it, and `main` turns
a result into lines and an exit status. Nothing raises out of the entry point:
every way of not filing is reported and turned into a status, so an assist
session can tell filed from not filed without reading prose.

**Dedupe is the two sources the Inspector already consults**, asked about one
key rather than about a scope. The filed query asks a tracker what it holds
against this brief's paths; the local queue is asked directly for the entry
this key would be written to, which is a single file read because a key names
its own file. Which state that entry is in decides what it is evidence of: a
landed entry means a provider named what it holds and suppresses; a pending one
is written down here and seen by no tracker, so it is reported as already
queued rather than already filed; a failed one is terminal and suppresses
nothing, because it is a brief that reached nobody and suppressing on it would
lose the brief permanently with no signal. A query that could not answer costs
dedupe and costs nothing else — the brief is filed anyway and the developer is
told dedupe did not run, because losing dedupe is not a reason to lose the
brief.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and __package__ is None:  # pragma: no cover - launch
    # Run directly, this is the entry point an assist session invokes, and its
    # sibling modules are reached the way every entry point the harness ships
    # reaches them. Imported as a module, this does nothing: the caller's path
    # already holds this directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import filed_query  # noqa: E402
import harness_config  # noqa: E402
import outbox  # noqa: E402
import schema_validator  # noqa: E402
import story_brief  # noqa: E402

#: The shape a brief is held to. One definition, injected into the prompts that
#: ask for a brief and read by the two paths that file one.
BRIEF_SCHEMA = "story-brief"

#: The harness this module ships with, resolved the way every entry point
#: resolves it, so the schemas and the workflow definitions a filing is judged
#: against are the ones beside this file.
HARNESS_ROOT = Path(__file__).resolve().parents[1]

#: The outcomes, one per way this can end. Each is named so that a developer
#: reading the result is told which of them decided it, and the three dedupe
#: outcomes are distinct because they are not the same evidence: a tracker
#: holds it, this harness filed it, or this harness has it written down and no
#: tracker has seen it.
FILED = "filed"
ALREADY_FILED = "already filed"
ALREADY_FILED_LOCALLY = "already filed by this harness"
ALREADY_QUEUED = "already queued"
MALFORMED = "malformed"
UNKNOWN_WORKFLOW = "names a workflow the harness does not define"
LOST_BY_THE_QUEUE = "lost by the queue"


@dataclass(frozen=True)
class Outcome:
    """What happened to one brief, and which check decided it.

    `outcome` is one of the constants above and is the whole of the decision;
    `detail` says why in words a developer reads. `key` is set only where the
    brief reached the queue, because story-090's contract is that an enqueue
    answering with the empty string is the item having been lost rather than a
    key — so nothing here is named after one and nothing is reported as filed
    on one.

    `dedupe_ran` is a statement about the filed query alone. The local queue is
    a subset that knows only what this machine filed, so reading it says
    nothing about a brief somebody else filed, and an answer nobody could get
    from the tracker is dedupe not having run whatever the queue said.
    `dedupe_asked` is whether the question was reached at all, which is what
    keeps a brief refused above it from being reported as one whose dedupe
    failed — a check that never ran did not fail.
    """

    outcome: str
    detail: str = ""
    key: str = ""
    dedupe_asked: bool = False
    dedupe_ran: bool = False
    dedupe_reason: str = ""

    @property
    def filed(self) -> bool:
        """Whether this brief reached the queue."""
        return self.outcome == FILED


def read_brief(path: Path):
    """One brief document read off disk, or the reason there is none.

    Returns `(brief, problem)`. Reading is a judgement like the rest of them,
    so it lives here rather than in the entry point: a document that is not
    there, cannot be read, is not JSON, or is not an object is refused in the
    same shape a document that fails the schema is.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"there is no brief document at {path}"
    except OSError as error:
        return None, f"the brief document at {path} could not be read: {error}"
    try:
        document = json.loads(text)
    except ValueError as error:
        return None, f"the brief document at {path} is not JSON: {error}"
    if not isinstance(document, dict):
        return None, f"the brief document at {path} is not a JSON object"
    return document, ""


def local_state(queue: Path, key: str, harness_root: Path | None = None) -> str:
    """The state the local queue holds this key in, or "" where it holds none.

    A key names its own file, so this is one read rather than an index of the
    whole queue: the question a single filing asks is about a single key. A
    file that is not there is a key the queue does not hold, and one that
    cannot be read as an entry is the same answer rather than an error — a
    poisoned entry stops nothing here, exactly as it stops nothing when the
    Inspector indexes the queue, and the brief is filed and replaces it.
    """
    path = outbox.entry_path(queue, key)
    if not path.is_file():
        return ""
    entry, _ = outbox.read_entry(path, harness_root)
    if entry is None:
        return ""
    return entry.get("state", "")


def file_brief(brief: dict, config: dict, target_root: Path,
               harness_root: Path | None = None) -> Outcome:
    """File one brief, or say which check decided not to.

    The order is the order the checks cost something in. The schema and the
    workflow are decided from the brief alone and cost nothing; the identity is
    derived once and is what both dedupe sources are asked about; the tracker
    is asked, then the local queue; and the enqueue is made once, for a brief
    that survived all of them.

    Raises on nothing. Every failure comes back as an outcome carrying its
    reason, so a failure to file is a status rather than a traceback.
    """
    problems = schema_validator.validate(
        brief, schema_validator.load_schema(BRIEF_SCHEMA, harness_root)
    )
    if problems:
        # Named by the field that failed rather than by the shape, so what the
        # developer is told is what to change.
        return Outcome(MALFORMED, problems[0])

    # The acceptable names are the definitions the harness holds, taken from
    # the one derivation that reports them rather than from a list restated
    # here, so a third workflow becomes filable by shipping a definition and
    # with no edit to this module. The refusal names them, so what the
    # developer is told is what to write instead.
    #
    # Asked here rather than through the fetch's own `workflow_problem`,
    # deliberately: the fetch is a plan-time input reached from the planning
    # entry point and from nothing else, and importing it here would make that
    # a property of a list of modules rather than of the whole import graph.
    # The derivation both spellings share is `harness_config.workflow_names`,
    # and that is the part that must not be written twice.
    defined = harness_config.workflow_names(harness_root)
    if brief.get("workflow") not in defined:
        listed = ", ".join(defined) if defined else "no workflow definitions"
        return Outcome(
            UNKNOWN_WORKFLOW,
            f"the brief names the workflow '{brief.get('workflow')}', which "
            f"the harness does not define; it defines: {listed}",
        )

    # The identity is `story_brief`'s and the key is the outbox's: this path
    # hashes nothing and derives no digest of its own, which is what makes a
    # brief filed here and a finding the Inspector filed land on one key.
    identity = story_brief.identity(brief)
    key = outbox.identity_key(identity)

    paths = story_brief.bare_paths(brief)
    answer = filed_query.query(paths, config, target_root, harness_root)
    asked = {"dedupe_asked": True, "dedupe_ran": answer.answered,
             "dedupe_reason": answer.reason}
    if answer.answered and any(item.key == key for item in answer.items):
        return Outcome(ALREADY_FILED,
                       "the filed query already reported it against these "
                       "paths", **asked)

    queue = outbox.queue_dir(target_root)
    state = local_state(queue, key, harness_root)
    if state == outbox.LANDED:
        return Outcome(ALREADY_FILED_LOCALLY,
                       "the local queue holds it landed, so this harness filed "
                       "it and a provider named what it holds", **asked)
    if state == outbox.PENDING:
        return Outcome(ALREADY_QUEUED,
                       "the local queue holds it pending, so it is written "
                       "down here and no tracker has seen it yet", **asked)
    # A key the queue holds *failed* reaches here deliberately and is filed: a
    # failed entry is terminal, no later sync will file it, and suppressing on
    # it would lose the brief permanently with no signal. It is replaced at the
    # same key by the pending entry written below, which is the brief getting
    # another chance rather than a duplicate, since the key is derived from the
    # identity alone.

    written = outbox.enqueue(queue, story_brief.payload(brief), identity)
    if not written:
        return Outcome(LOST_BY_THE_QUEUE,
                       "the queue dropped the item, so nothing was filed",
                       **asked)
    return Outcome(FILED, "", key=written, **asked)


# --------------------------------------------------------------------------
# The entry point an assist session invokes
# --------------------------------------------------------------------------


def report(outcome: Outcome) -> int:
    """Say what happened, and answer with the status that says it too.

    Zero when a brief was enqueued and non-zero when nothing was, so a session
    can tell the two apart without reading these words. A drop is reported as a
    drop and never as a key.
    """
    if outcome.filed:
        print(f"filed under key {outcome.key}")
    else:
        print(f"nothing was filed: {outcome.outcome}")
        if outcome.detail:
            print(f"  {outcome.detail}")

    # Said only where the question was reached: a brief refused above dedupe
    # did not have a dedupe that failed, and saying so would name a check that
    # never ran as the reason.
    if outcome.dedupe_asked:
        if outcome.dedupe_ran:
            print("  dedupe ran: the filed query answered")
        else:
            said = ": " + outcome.dedupe_reason if outcome.dedupe_reason else ""
            print(f"  dedupe did NOT run: the filed query could not answer"
                  f"{said}")
            if outcome.filed:
                # Said only where it is true. Losing dedupe is not a reason to
                # lose the brief, so a query that could not answer costs
                # dedupe and costs nothing else.
                print("      the brief was filed anyway rather than refused")

    if outcome.filed:
        print("nothing has reached a tracker: a brief is filed into the "
              "outbox, which is drained by l5-sync or by the sweeps a run "
              "makes")
    return 0 if outcome.filed else 1


def main(argv=None) -> int:
    """Read one brief document and file it, holding no decision of its own."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0].startswith("-"):
        print("usage: brief_filing.py <brief-document>", file=sys.stderr)
        return 1

    target_root = harness_config.find_target_root(Path.cwd())
    config = harness_config.load_config(target_root)

    brief, problem = read_brief(Path(arguments[0]))
    if brief is None:
        return report(Outcome(MALFORMED, problem))

    return report(file_brief(brief, config, target_root, HARNESS_ROOT))


if __name__ == "__main__":
    sys.exit(main())
