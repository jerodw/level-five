"""The decision behind l5-plan's mandate stamp, kept out of the script.

Every function here returns what happened rather than printing it, exactly as
`plan_commit` and `plan_validation` do: the script decides the wording, and
this module decides nothing about it.

What a mandate is and what a run does with it is declared in
schemas/story.schema.json and resolved by the coordinator. What this module
adds is the other half of that contract: the block is written by the harness
process that observed the authorizing act, and never by an agent. So an
artifact that arrives from a session already carrying one is refused rather
than trusted — the rule that a required output must be written by the attempt
that ran, applied to an output no attempt is asked for. Nothing here lets a
stage prompt, a stage output or a planning session supply the block.

What the observing process observes is the developer answering: `approved`
below is how `l5-plan` asks, and it is the whole of the evidence behind a
block. Nothing else here reads a stream, and `confer` reads none at all, so a
process holding a recorded approval of some other kind confers through the
same seam without prompting.

The conferring record goes into the declared harness-scoped log through the
coordinator's own per-log append, with the history directory passed
explicitly. That seam is what this module needed and did not have:
`append_event` takes a run directory as its first argument, and a process that
observes an authorizing act has no run. Factoring the append out was the
answer rather than giving this module a second way of writing a declared log.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import harness_config
import plan_run_offer
import story_coordinator

#: The key the block is written under, and the only line this module looks for
#: when deciding whether a session wrote one of its own.
MANDATE_KEY = "mandate"

#: What this process records itself as. It is the process that observed the
#: act, never the agent that wrote the body around it.
RECORDED_BY = "l5-plan"

#: The event kind the conferring record carries. What routes it is the enum in
#: schemas/cross-run-history.schema.json that names it; nothing here decides
#: which log it reaches.
CONFERRED_EVENT = "mandate-conferred"

#: The same form events.log and execution-history.json use, so a timestamp in
#: the block and a timestamp in the log are one form rather than two.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

#: The replies that answer the approval question yes. Everything else answers
#: no, including the empty line: the run offer's Enter-runs-it default is the
#: opposite bias, and approval is the one question this process may not read a
#: silence as a yes to.
APPROVALS = ("y", "yes")

#: What the developer is handed on a rejection, so the artifacts left in the
#: tree can be removed with one command rather than by hand. Handed over rather
#: than acted on, following PAUSE_UNDO_COMMAND: a rejection deletes nothing,
#: and a destructive default would belong behind a flag this adds none of.
REMOVE_COMMAND = "rm"


@dataclass(frozen=True)
class Mandate:
    """One conferral: what was observed, or why nothing was.

    `stamped` is whether the block was appended. When it is false, `detail`
    says why and the artifact on disk is untouched — this module never
    rewrites, repairs or removes an artifact except where the developer has
    just authorized the discard, and never appends twice.

    `discarded_block` is whether that conferral removed a block the session
    wrote. It is what the conferring record says so a reader of the log can
    tell a conferral onto a clean artifact from one the developer approved
    with a session-written block in front of them.
    """

    path: Path
    story_id: str
    source_kind: str
    conferred_by: str
    conferred_at: str
    stamped: bool
    detail: str
    discarded_block: bool = False


def approved(stream: TextIO) -> bool:
    """Whether the developer on `stream` approves the plan.

    The default is no, which is the opposite of the run offer's Enter-runs-it,
    so this is its own function rather than a second caller of `should_run`:
    the run offer risks a run nobody wanted, and this risks a mandate nobody
    conferred. The reply is read once and never re-asked.

    A stream that is not a terminal answers no *without reading*, and a read
    that reaches end of input answers no — the same one-directional bias
    `can_prompt` already takes, reused rather than spelled a second time here.
    """
    if not plan_run_offer.can_prompt(stream):
        return False
    reply = stream.readline()
    if reply == "":
        return False
    return reply.strip().lower() in APPROVALS


def strip_mandate(text: str) -> str:
    """The artifact without its block, and with every other byte where it was.

    By line extent — the key line and the indented region beneath it — never by
    parsing and re-serialising, which would reformat everything the session
    wrote in order to remove four lines of it.

    A blank line and a full-line comment are held rather than decided on when
    they are met, because either can be inside the block or after it: held
    lines are discarded once another indented line proves the block continues,
    and written back out once a top-level key or the end of the file proves it
    did not. So a block ending the file, one followed by another top-level key,
    and one carrying blank or comment lines of its own all come out with the
    rest of the artifact untouched.
    """
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if not _opens_the_block(lines[index]):
            kept.append(lines[index])
            index += 1
            continue
        index += 1
        held: list[str] = []
        while index < len(lines):
            line = lines[index]
            if _undecided(line):
                held.append(line)
                index += 1
                continue
            if line.startswith((" ", "\t")):
                held = []
                index += 1
                continue
            break
        kept.extend(held)
    return "".join(kept)


def _opens_the_block(line: str) -> bool:
    content = line.rstrip("\r\n")
    return content.rstrip() == f"{MANDATE_KEY}:" or content.startswith(
        f"{MANDATE_KEY}: "
    )


def _undecided(line: str) -> bool:
    content = line.strip()
    return content == "" or content.startswith("#")


def carries_a_mandate(text: str) -> bool:
    """Whether an artifact already carries a block at the top level.

    A session-written block is what this reports: the artifact this process is
    about to stamp has not been stamped by anything, so any block in it came
    from the session that wrote the body. Read as a top-level key, because that
    is where the declaration puts it and where the parse would find it.
    """
    return any(
        line.rstrip() == f"{MANDATE_KEY}:" or line.startswith(f"{MANDATE_KEY}: ")
        for line in text.splitlines()
    )


def git_identity(target_root: Path) -> str:
    """The identity the authorizing act is observed from.

    The target repository's own configured identity, so the person the harness
    records is the person git would record for the commit this act produces.
    An identity git cannot answer for is reported as an empty string and
    refuses the stamp: recording an act with nobody attached to it would be a
    block that resolves to a human who is not named.
    """
    name = _git(target_root, "config", "user.name")
    email = _git(target_root, "config", "user.email")
    if name and email:
        return f"{name} <{email}>"
    return name or email


def _git(target_root: Path, *args: str) -> str:
    finished = subprocess.run(
        ["git", "-C", str(target_root), *args], capture_output=True, text=True
    )
    return finished.stdout.strip() if finished.returncode == 0 else ""


def block(conferred_by: str, conferred_at: str) -> str:
    """The block as the artifact dialect writes it.

    A source of kind human and no id, because a human is where a resolution
    walk ends and there is nothing further to follow. Composed here so the text
    appended to an artifact and the fields the record carries come from one
    place.
    """
    return (
        f"\n{MANDATE_KEY}:\n"
        f"  source:\n"
        f"    kind: {story_coordinator.HUMAN}\n"
        f"  conferred_at: {conferred_at}\n"
        f"  conferred_by: {conferred_by}\n"
        f"  recorded_by: {RECORDED_BY}\n"
    )


def confer(
    path: Path,
    target_root: Path,
    now: float | None = None,
    *,
    discarding: bool = False,
) -> Mandate:
    """Append a mandate to one artifact, or report why nothing was appended.

    This neither prompts nor reads any stream. What it is handed is a decision
    already taken — `now` is when the authorizing act was observed, and
    `discarding` says the developer was shown the session-written block and
    approved anyway — so a process that observed an act some other way confers
    through exactly this seam.

    Two things refuse, and neither touches the artifact: a session that already
    wrote a block the caller has not authorized discarding, and a target whose
    git identity cannot say who conferred anything. Otherwise the block is
    appended to the text the session wrote, leaving every byte of it in place,
    and what was written is returned so the caller can record it.
    """
    story_id = path.stem
    text = path.read_text(encoding="utf-8")
    conferred_at = time.strftime(
        TIMESTAMP_FORMAT, time.localtime(now if now is not None else time.time())
    )
    discarded = False
    if carries_a_mandate(text):
        if not discarding:
            return Mandate(
                path,
                story_id,
                "",
                "",
                "",
                False,
                f"the artifact already carries a {MANDATE_KEY} block, and a "
                f"block this process did not write is a block nothing "
                f"observed",
            )
        # The developer was shown the block and approved its discard, so the
        # write happens here rather than in the script: the scan that holds
        # `report` to writing nothing is what keeps the observing process's
        # writes in one module.
        text = strip_mandate(text)
        discarded = True
    conferred_by = git_identity(target_root)
    if not conferred_by:
        return Mandate(
            path,
            story_id,
            "",
            "",
            "",
            False,
            "the target repository's git identity names nobody, so there is "
            "no human to record as having conferred anything",
        )
    ending = "" if text.endswith("\n") else "\n"
    path.write_text(
        text + ending + block(conferred_by, conferred_at), encoding="utf-8"
    )
    return Mandate(
        path,
        story_id,
        story_coordinator.HUMAN,
        conferred_by,
        conferred_at,
        True,
        "",
        discarded,
    )


def record(target_root: Path, config: dict, conferred: Mandate) -> Path:
    """Write the conferring record into the declared log, and say where.

    Through the coordinator's own per-log append, with the history directory
    resolved exactly as a run resolves it and passed explicitly, because this
    process has no run directory to resolve one from. What reaches which log is
    read off schemas/cross-run-history.schema.json: this builds an entry
    carrying the kind that declaration names and hands it over, so no condition
    here decides where it goes.
    """
    directory = harness_config.history_dir(target_root, config)
    story_coordinator.append_history_records(
        directory,
        {
            "event": CONFERRED_EVENT,
            "timestamp": conferred.conferred_at,
            "conferred_by": conferred.conferred_by,
            "source_kind": conferred.source_kind,
            "recorded_by": RECORDED_BY,
            "discarded_session_block": conferred.discarded_block,
        },
        conferred.story_id,
        [],
    )
    return directory


@dataclass(frozen=True)
class Commit:
    """What committing the conferring records did, or why it did nothing."""

    paths: tuple[str, ...]
    subject: str
    committed: bool
    detail: str


def commit_records(target_root: Path, directory: Path, conferred) -> Commit:
    """Commit the conferring records this process wrote, before the artifacts.

    The cross-run history is versioned, so a record left in the working tree is
    a file the next thing to stage everything would absorb — and, sooner than
    that, a dirty tree the run this session is about to offer would be refused
    for. So the record is committed here rather than left for someone to notice.

    It is not `plan_commit`'s to commit and the division is not arbitrary: that
    module commits what the *session* produced, decided by what appeared under
    the stories directory, and composes a subject naming the stories it found.
    A conferring record is produced by this process rather than by the session,
    so it is neither one of those artifacts nor nameable by that subject.

    It is committed *before* the artifacts, so the artifact commit is still the
    branch tip and still says what it said: a reader of the log meets `Plan
    story-NNN` where they always did, with the record it rests on beneath it.
    Nothing here amends, resets or pushes, and only the paths named are staged.
    """
    conferred = list(conferred)
    paths = tuple(
        sorted(
            {
                str((directory / log).relative_to(target_root))
                for log in _logs_holding(conferred)
            }
        )
    )
    ids = ", ".join(sorted({one.story_id for one in conferred}))
    subject = f"Record the mandate conferred for {ids}"
    if not paths:
        return Commit((), subject, False, "no conferring record was written")
    staged = subprocess.run(
        ["git", "-C", str(target_root), "add", "--", *paths],
        capture_output=True,
        text=True,
    )
    if staged.returncode != 0:
        return Commit(paths, subject, False, staged.stderr.strip())
    committed = subprocess.run(
        ["git", "-C", str(target_root), "commit", "-m", subject, "--", *paths],
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0:
        return Commit(paths, subject, False, committed.stderr.strip())
    return Commit(paths, subject, True, "")


def _logs_holding(conferred) -> set[str]:
    """The declared logs a conferring record reaches, read off the declaration.

    Asked of the same projection the append took, so what is committed is what
    was written: a log the declaration stops naming for this kind stops being
    staged with no change here.
    """
    declarations = story_coordinator.history_log_declarations()
    return {
        log
        for log, declaration in declarations.items()
        for one in conferred
        if story_coordinator.history_record(
            {"event": CONFERRED_EVENT, "timestamp": one.conferred_at},
            [],
            one.story_id,
            declaration,
        )
        is not None
    }
