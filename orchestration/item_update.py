"""Publishing a document onto the tracker item a brief was planned from.

When a brief is planned, the story artifact is written, committed and pushed,
and the tracker item the developer has been watching still shows the brief and
nothing about the plan approved from it. This is the seam that puts the plan
where the decision is read: one configured command, a JSON document on stdin,
an exit code as the answer, and no harness knowledge of whether the target
appended to a body, added a comment or wrote a file.

**What goes onto the item is a projection and not a move.** The story artifact
stays the source of truth and stays what a run reads, because the artifact
governs the run rather than describing it: `scope.modify` and
`scope.do_not_modify` decide which edits a stage may make, and `mandate`
records who approved what and when. A document living in a tracker is editable
by anyone at any moment, including during a run, so a mandate held there would
stop being a record of what was approved; and a run that had to read it would
stop working whenever the tracker was unreachable, in a harness whose every
other tracker interaction is explicitly allowed to fail without stopping
anything. So the item gets a copy, stamped with the story id it was taken from
and saying plainly that the artifact in the repository is what runs and that an
edit made on the item reaches nothing. Nothing in a run reads this back, and no
coordinator, resume, sweep or pre-flight path reaches this module.

**One command rather than two, because the status half shares it.** The command
takes an optional status beside an optional document, and a question carries
each only where a caller supplied one — a publish with nothing to say about
status, a status move with nothing to publish, or both at once. The sibling
work that moves an item's status as its story runs is about the same item, and
two scripts that must agree about one item are two scripts that can disagree,
which is why it fills this half in rather than bringing a second key and a
second script.

**The harness does not know what a status is.** The three tokens below are the
words on the wire and nothing more: no code here or above branches on which one
was sent to decide anything about a tracker, nothing composes a sentence from
one, and no status is ever parsed back out of the command. What a token means
in a tracker — an issue label, a project-board column, a row in a markdown
table — is the invoked command's business, exactly as what a key means is.

**The key is opaque.** It is sent exactly as it was given and nothing here
resolves it, normalizes it, joins it against a root, checks that it exists or
decides anything from its form; a URL, a repository-relative document path and
a digest are all keys, and which of those a target's keys are is the invoked
command's business. It travels twice — in the document on stdin and in the
environment as `L5_ITEM_KEY` — taken from the same value, so the two cannot
disagree.

**The command's stdout is not read.** No reference is recorded, nothing is
parsed, and a command that printed nothing publishes exactly as one that
printed a page. The exit code is the whole of the answer: zero means published
and any other code means it did not publish, with a bounded tail of stderr
carried back as the reason. **No exit code is read as a retry**, because
nothing retries: a publish that failed is reported and the run offer, the
commit and the push it followed are untouched.

This module **raises on nothing**. An unset command, a command line that cannot
be split, one that cannot be launched, one that exits non-zero and one that
runs past its bound each come back as a result carrying the reason, so the
caller decides what a failure is worth and this decides only what happened.
Nothing here prints: the wording a developer reads lives in the script.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: The command a document is published onto an item by running.
COMMAND_KEY = "item_update_command"

#: How long that command may run before it is killed.
TIMEOUT_KEY = "item_update_timeout_seconds"

#: The item's key, in the invoked command's environment. Taken from the same
#: value as the copy inside the document on stdin, so the two cannot disagree.
KEY_ENVIRONMENT_VARIABLE = "L5_ITEM_KEY"

#: The three moments the work reaches, as the tokens that go on the wire. They
#: are named here, beside the contract they belong to, so the words a call site
#: sends and the words the schema declares are one fact rather than two.
#:
#: They are snake-case tokens rather than display text because a target's
#: script compares them, and because the harness's other seams are spelled that
#: way — the outbox's own states are pending, landed and failed. THE HARNESS
#: ATTACHES NO MEANING TO ANY OF THEM beyond composing the document that
#: carries it: nothing branches on which was sent, and no status is read back.

#: The planning session's artifact has been committed and pushed.
PLANNED = "planned"

#: The run for that story has begun. Sent on a fresh run only — a resumed run
#: has already been announced, and nothing here knows whether the tracker moved
#: the item on since.
IN_PROGRESS = "in_progress"

#: The run completed, so there is a branch to review.
READY_TO_MERGE = "ready_to_merge"

#: The three, in the order the work reaches them. Written once so a reader —
#: and a target's script — meets the whole vocabulary in one place. Nothing in
#: this module reads it to decide anything; it is the list, not a check.
STATUSES = (PLANNED, IN_PROGRESS, READY_TO_MERGE)

#: How long the command may run when the target configures no bound. A real
#: duration rather than zero, for `sync_timeout_seconds`' reason: zero here
#: would be no bound at all, which is the failure a bound exists to prevent.
DEFAULT_TIMEOUT_SECONDS = 60.0

#: How much of a command's stderr is carried back in the reason it did not
#: publish. The reason carries the tail of the stderr that was *read*, and only
#: ERROR_TAIL_LENGTH * 2 bytes are read off the command at all — so a command
#: that said more than that has the middle of what it said carried, not its
#: last words. The bound is on what one failure may cost a terminal rather than
#: on which words survive. This is `filed_query.py`'s idiom, held to byte for
#: byte, so the two seams stay recognisably the same.
ERROR_TAIL_LENGTH = 2048


@dataclass(frozen=True)
class Published:
    """Whether the document reached the item, and the reason where it did not.

    `published` is true only when the command ran and exited zero. `reason` is
    empty then and says which way it failed otherwise, so a caller reports what
    actually happened rather than that something did.
    """

    published: bool = False
    reason: str = ""


def _not_published(reason: str) -> Published:
    """The one construction site for a publish that did not happen.

    Every way of not publishing funnels through here, so they cannot disagree
    about what such an answer looks like: not published, and a reason saying
    which way it was.
    """
    return Published(published=False, reason=reason)


def _tail(text: str) -> str:
    """The last of what was read of a command's stderr, bounded.

    The bound is made visible, and what arrives here is already bounded by what
    the caller read off the command — see ERROR_TAIL_LENGTH.
    """
    text = (text or "").strip()
    if len(text) <= ERROR_TAIL_LENGTH:
        return text
    return "…" + text[-ERROR_TAIL_LENGTH:]


def _bound(config: dict):
    """The time bound a publish runs under, or the reason it has none.

    Returns `(timeout, problem)`. A value that is not a positive number does
    not fall back to the default, for the reason the filed query's own bounds
    do not: a bound that cannot be read is a bound the target did not declare,
    and obeying the default in its place would obey a number nobody wrote.
    Unset is not a problem — it is the ordinary case, and it takes the default.
    """
    declared = config.get(TIMEOUT_KEY)
    if declared is None:
        return DEFAULT_TIMEOUT_SECONDS, ""
    try:
        timeout = float(declared)
    except (TypeError, ValueError):
        timeout = 0.0
    if timeout <= 0:
        return None, (
            f"{TIMEOUT_KEY}: {declared!r} is not a positive number of seconds, "
            "and an item-update command must be bounded in time"
        )
    return timeout, ""


def projection(story_id: str, artifact: str) -> str:
    """The story artifact as it is published onto an item.

    Composed here rather than at a call site for the reason
    `brief_fetch.render` is: it is content a test should be able to assert on
    without driving a planning session, and it is derived from what it is given
    rather than written wherever it happens to be sent from.

    It is headed by the story id it was taken from and by the statement the
    whole design rests on — that the artifact in the repository is what runs,
    and that an edit made on the item reaches nothing. A reader who believes
    otherwise will edit a copy and expect a run to obey it, so the copy says so
    where they meet it.
    """
    return "\n".join([
        f"l5 story {story_id}, as planned and committed.",
        "",
        f"This is a copy of the story artifact committed as {story_id}. The "
        "artifact in the repository is what runs: it is what the harness "
        "reads, what decides which files each stage may change, and what "
        "records who approved the work and when. An edit made here reaches "
        "nothing — change the artifact in the repository instead.",
        "",
        artifact,
    ])


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the process group the command leads, not merely the command.

    The command is spawned in its own session, so a kill delivered to its group
    reaches the children it spawned. Killing the process alone would leave a
    command's own children running past this function's return, which is the
    whole reason the session is new.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def publish(key: str, story_id: str, document: str | None, config: dict,
            target_root: Path | None = None,
            status: str | None = None) -> Published:
    """Say something about the item `key` names, or say why nothing was said.

    Raises on nothing: every failure comes back as a result carrying its
    reason. The caller decides what a failure is worth, and in this harness it
    is worth one reported line — the commit, the push, the run and the
    completion this follows have already landed by the time this is invoked,
    and none of them is reconsidered on its answer.

    The question is one JSON document on stdin carrying the item's key and the
    story id, a `document` only where one was given and a `status` only where
    one was given. At least one of the two is always there: a call giving
    neither has nothing to say and is reported rather than invoking anything,
    in this module's own shape of returning a result carrying a reason. The
    signature keeps its order, so a caller that publishes a document reads
    exactly as it did before a status could be sent.

    The key goes into the question verbatim and into the environment as
    `L5_ITEM_KEY`, from the same value.

    Nothing reads the command's stdout. Zero means it was done; any other exit
    code means it was not, with a bounded tail of its stderr as the reason, and
    no code is read as a retry, because nothing retries.
    """
    if document is None and status is None:
        return _not_published(
            "the question carried neither a document nor a status, so there "
            "was nothing to say about that item and no command was invoked"
        )

    command = config.get(COMMAND_KEY)
    if not command:
        return _not_published(
            f"no {COMMAND_KEY} is configured, so there is no command to "
            "publish the story onto that item with"
        )

    timeout, problem = _bound(config)
    if problem:
        return _not_published(
            f"the configuration a publish runs under was refused: {problem}"
        )

    # Each half is carried only where a caller supplied one, and at least one
    # of the two is always there — a question with neither was refused above.
    # A status is a token this composes into a document and nothing more: no
    # branch here reads which one it is, and none ever should.
    question = {"key": key, "story_id": story_id}
    if document is not None:
        question["document"] = document
    if status is not None:
        question["status"] = status

    try:
        argv = shlex.split(command)
    except ValueError as error:
        return _not_published(
            f"the command could not be launched: {command!r} cannot be read as "
            f"an argument list: {error}"
        )
    if not argv:
        return _not_published(
            f"the command could not be launched: {command!r} is an empty "
            "argument list"
        )

    environment = dict(os.environ)
    environment[KEY_ENVIRONMENT_VARIABLE] = key

    with tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(  # noqa: S603 - the command is the target's
                argv,
                stdin=subprocess.PIPE,
                # Not read: no reference is recorded and nothing is parsed, so
                # a command that printed a page publishes exactly as one that
                # printed nothing.
                stdout=subprocess.DEVNULL,
                stderr=err,
                cwd=str(target_root) if target_root is not None else None,
                env=environment,
                start_new_session=True,
            )
        except OSError as error:
            return _not_published(
                f"the command could not be launched: {command}: {error}"
            )

        payload = json.dumps(question, sort_keys=True).encode("utf-8")
        try:
            process.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            try:
                process.communicate()
            except Exception:  # noqa: BLE001 - the answer is already decided
                pass
            return _not_published(
                f"the command ran past its bound of {timeout} seconds and was "
                f"killed: {command}"
            )

        err.seek(0)
        stderr = err.read(ERROR_TAIL_LENGTH * 2).decode("utf-8", "replace")

    if process.returncode != 0:
        said = _tail(stderr)
        return _not_published(
            f"the command exited {process.returncode} and did not publish"
            + (f": {said}" if said else "")
        )

    return Published(published=True, reason="")
