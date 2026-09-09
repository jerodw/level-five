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
takes an optional status beside the optional document, and nothing in this
harness sends one yet. The sibling work that moves an item's status as its
story runs is about the same item, and two scripts that must agree about one
item are two scripts that can disagree — so the contract declares both halves
now and that work fills its half in, rather than adding a second key and a
second script.

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


def publish(key: str, story_id: str, document: str, config: dict,
            target_root: Path | None = None,
            status: str | None = None) -> Published:
    """Publish `document` onto the item `key` names, or say why it did not.

    Raises on nothing: every failure comes back as a result carrying its
    reason. The caller decides what a failure is worth, and in this harness it
    is worth one printed line — the commit and the push have already landed by
    the time this is invoked, and neither is reconsidered on its answer.

    The question is one JSON document on stdin carrying the item's key, the
    story id and the projected document, and a status only where a caller
    supplied one. The key goes into it verbatim and into the environment as
    `L5_ITEM_KEY`, from the same value.

    Nothing reads the command's stdout. Zero means published; any other exit
    code means it did not publish, with a bounded tail of its stderr as the
    reason, and no code is read as a retry, because nothing retries.
    """
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

    question = {"key": key, "story_id": story_id, "document": document}
    if status is not None:
        # Carried only where a caller supplied one. The status half of this
        # contract is declared and unsent: nothing in this harness sends a
        # status yet, and the harness parses none and infers none either.
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
