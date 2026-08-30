"""Asking one configured command what is already filed against a path set.

This is the read side of the harness's external filing, and it is the filing
side's shape run backwards. A command the target configured is asked one scoped question, the
question goes to it on stdin as JSON, the answer comes back on stdout as JSON,
and the exit code says whether it answered. The harness knows nothing else
about the tracker, so GitLab, Jira and files-in-source-control are scripts a
target writes exactly as `sync_command` already made them.

**The question is scoped rather than a listing, and that is what makes it
scale.** A command asked to enumerate everything open transfers a tracker's
whole backlog in order to answer a question about one directory, and it gets
worse as the tracker grows. A command asked what is filed against a path set
transfers what is about that code, which stays small however large the tracker
is, and it pushes the searching to the only thing that knows how the tracker
searches. So there is no listing operation here and no call site may ask for
everything: the cost of that grows with the tracker rather than with the code
being asked about.

**Nothing known and nothing filed are different answers, and a caller must be
able to tell them apart.** An empty item list is what both look like, so the
answer says whether it was answered at all — a caller reading only the items
would report silence as agreement, and file a duplicate while believing dedupe
had run. `Answer.answered` is that flag, and a later caller that cannot get one
should say dedupe did not run rather than proceed as though the tracker were
empty.

The query is **total**: every failure mode returns an answer carrying no items
and a reason saying which, and none of them raises. An absent command, one that
cannot be launched, one that exits non-zero, one that runs past its timeout, one
whose stdout is not a single JSON document, one whose stdout does not satisfy
the filed-items schema, and one that answered with more than the harness will
read all resolve to nothing known. A failed query costs dedupe and costs nothing
else. Only `resolve_settings` refuses, and only to a terminal caller that asked
for a refusal; `query` consumes its problem into the answer.

**No silent bound.** Every limit here names what it excluded, in the answer it
returns: the item cap says how many it dropped, the per-field bound says how
many fields it shortened, and the stdout bound says the document was never read
whole. A bound whose effect is not stated reads as a tracker with nothing in it.

**Whether a closed item suppresses a refiling is the answering command's
policy.** The harness must not encode it, must not infer it, and must not add a
field that would let it start; see `schemas/filed-items.schema.json`, where a
reader meets the rule, and `templates/query/github.sh`, where a script author
does.
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

import schema_validator

#: The command that answers what is already filed against a path set.
COMMAND_KEY = "filed_query_command"

#: How long that command may run before it is killed.
TIMEOUT_KEY = "filed_query_timeout_seconds"

#: How many items the harness will read out of one answer.
MAX_ITEMS_KEY = "filed_query_max_items"

#: The shape a command's stdout must satisfy.
ITEMS_SCHEMA = "filed-items"

#: How long the command may run when the target configures no timeout. A real
#: duration rather than zero, for `sync_timeout_seconds`' reason: zero here
#: would be no bound at all, which is the failure a bound exists to prevent.
#: Every path through this query is bounded in time.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: How many items one answer may carry when the target declares no bound. A
#: count rather than a duration: what is filed against one path set is meant to
#: be small, and a command answering with far more than this has answered a
#: broader question than it was asked.
DEFAULT_MAX_ITEMS = 50

#: The most stdout this will read from a command. A document larger than this
#: is not read whole and is not parsed at all, because a document truncated
#: mid-token is not a document — so an over-long answer is nothing known with
#: the bound named, rather than a partial answer that looks complete.
MAX_STDOUT_BYTES = 1 << 20

#: The longest any one text field of an item may be. Longer text is shortened
#: to the bound and the shortening is named in the answer, because the fields
#: go on to be read by a person and an unbounded one is an unbounded read of
#: somebody else's stdout.
MAX_TEXT_LENGTH = 4096

#: How much of a command's stderr is carried back in the reason it did not
#: answer. A tail rather than the whole, and the tail rather than the head
#: because a command's last words are the ones that say why it stopped.
ERROR_TAIL_LENGTH = 2048


@dataclass(frozen=True)
class Item:
    """One item the command reported, and nothing the harness invented.

    Every field is what the command said, bounded in length and otherwise
    unread: no status is parsed, no policy is inferred, and no field is
    synthesized for a command that did not report one.
    """

    key: str
    title: str
    summary: str = ""
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Answer:
    """What one query established, including that it established nothing.

    `answered` is the whole of the difference between nothing filed and
    nothing known: it is true only when a command ran and answered in the
    shape this module accepts. `items` is empty in both cases, so a caller
    reading it alone cannot tell them apart and must read the flag.

    `reason` says why nothing was known, and is empty on an answer that was
    given. `excluded` names what a bound left out of an answer that was
    given, so no limit here is silent.
    """

    items: tuple[Item, ...] = ()
    answered: bool = False
    reason: str = ""
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    """What a query needs from the target's configuration, already resolved."""

    command: str
    timeout: float
    max_items: int


def _tail(text: str) -> str:
    """The last of a command's stderr, bounded, with the bound made visible."""
    text = (text or "").strip()
    if len(text) <= ERROR_TAIL_LENGTH:
        return text
    return "…" + text[-ERROR_TAIL_LENGTH:]


def resolve_settings(config: dict):
    """The settings a query runs under, or the reason they are refused.

    Returns `(settings, problem)`, in the shape the sweep's own transport
    build already takes and for its reason: a terminal caller may refuse a configuration it cannot
    obey, while `query` below consumes the problem into its answer rather than
    raising on it. An **absent command is not a problem** — a target that
    queries nothing is the ordinary case, and it gets no settings and no
    complaint.

    A `filed_query_timeout_seconds` that is not a positive number and a
    `filed_query_max_items` that is not a positive integer are each a problem
    naming the key and the value. Neither falls back to its default: a bound
    that cannot be read is a bound the target did not declare, and obeying the
    default in its place would obey a number nobody wrote. Both are checked
    above the command, so a target that declares an unusable bound and no
    command is still told about the bound.
    """
    problems: list[str] = []

    declared_timeout = config.get(TIMEOUT_KEY)
    timeout = DEFAULT_TIMEOUT_SECONDS
    if declared_timeout is not None:
        try:
            timeout = float(declared_timeout)
        except (TypeError, ValueError):
            timeout = 0.0
        if timeout <= 0:
            problems.append(
                f"{TIMEOUT_KEY}: {declared_timeout!r} is not a positive number "
                "of seconds, and a filed-query command must be bounded in time"
            )

    declared_max = config.get(MAX_ITEMS_KEY)
    max_items = DEFAULT_MAX_ITEMS
    if declared_max is not None:
        try:
            max_items = int(str(declared_max))
        except (TypeError, ValueError):
            max_items = 0
        if max_items <= 0:
            problems.append(
                f"{MAX_ITEMS_KEY}: {declared_max!r} is not a positive integer, "
                "and an answer must be bounded in how many items it carries"
            )

    if problems:
        return None, "; ".join(problems)

    command = config.get(COMMAND_KEY)
    if not command:
        return None, ""
    return Settings(command=command, timeout=timeout, max_items=max_items), ""


def _nothing_known(reason: str) -> Answer:
    """The one construction site for an answer that establishes nothing.

    Every way of knowing nothing funnels through here, so they cannot disagree
    about what such an answer looks like: no items, `answered` false, and a
    reason saying which way it was.
    """
    return Answer(items=(), answered=False, reason=reason, excluded=())


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


def _bounded_text(value, excluded: list[str]) -> str:
    """One text field, shortened to the per-field bound if it exceeds it."""
    text = value if isinstance(value, str) else ""
    if len(text) <= MAX_TEXT_LENGTH:
        return text
    excluded.append(
        f"a text field of {len(text)} characters was shortened to the "
        f"{MAX_TEXT_LENGTH} character bound this query reads"
    )
    return text[:MAX_TEXT_LENGTH]


def _item(reported: dict, excluded: list[str]) -> Item:
    """One reported item, carrying what the command said and nothing else."""
    paths = reported.get("paths")
    return Item(
        key=_bounded_text(reported.get("key"), excluded),
        title=_bounded_text(reported.get("title"), excluded),
        summary=_bounded_text(reported.get("summary", ""), excluded),
        paths=tuple(
            _bounded_text(path, excluded)
            for path in (paths if isinstance(paths, list) else ())
        ),
    )


def _read_answer(document: dict, max_items: int) -> Answer:
    """The answer a validated document describes, bounded and named.

    The command's own order is kept: an answer past the item cap loses its
    tail rather than being reordered, and what it lost is named.
    """
    excluded: list[str] = []
    reported = document["items"]
    if len(reported) > max_items:
        excluded.append(
            f"{MAX_ITEMS_KEY} is {max_items} and the command answered with "
            f"{len(reported)} items, so {len(reported) - max_items} were dropped"
        )
        reported = reported[:max_items]
    items = tuple(_item(one, excluded) for one in reported)
    return Answer(items=items, answered=True, reason="", excluded=tuple(excluded))


def query(paths, config: dict, target_root: Path | None = None,
          harness_root: Path | None = None) -> Answer:
    """What is already filed against `paths`, or the reason that is not known.

    Total: this returns an `Answer` on every path and raises on none. A failed
    query costs dedupe and costs nothing else — it never blocks, never refuses
    and never fails a run — so every failure mode below comes back as an answer
    carrying no items, `answered` false, and a reason naming which one it was.

    The question the command is asked is one JSON document on stdin carrying
    the paths. Its stdout must be a single JSON document satisfying the
    filed-items schema and nothing else: a command that prints a debug line, a
    progress message or a shell trace beside its document has printed something
    that is not one document, and this reads that as nothing known rather than
    parsing what it can. Diagnostics belong on stderr, where a tail of them is
    carried back in the reason.
    """
    settings, problem = resolve_settings(config)
    if problem:
        return _nothing_known(
            f"the query was not run because the configuration was refused: {problem}"
        )
    if settings is None:
        return _nothing_known(
            f"no {COMMAND_KEY} is configured, so nothing was asked and nothing "
            "is known about what is already filed"
        )

    try:
        argv = shlex.split(settings.command)
    except ValueError as error:
        return _nothing_known(
            f"the command could not be launched: {settings.command!r} cannot be "
            f"read as an argument list: {error}"
        )
    if not argv:
        return _nothing_known(
            f"the command could not be launched: {settings.command!r} is an "
            "empty argument list"
        )

    question = json.dumps({"paths": list(paths)}, sort_keys=True)

    # The command's output goes to files rather than to pipes, so that what
    # this reads into memory is bounded by the read below rather than by how
    # much the command chose to print.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(  # noqa: S603 - the command is the target's
                argv,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                cwd=str(target_root) if target_root is not None else None,
                start_new_session=True,
            )
        except OSError as error:
            return _nothing_known(
                f"the command could not be launched: {settings.command}: {error}"
            )

        try:
            process.communicate(question.encode("utf-8"), timeout=settings.timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            try:
                process.communicate()
            except Exception:  # noqa: BLE001 - the answer is already decided
                pass
            return _nothing_known(
                f"the command ran past its timeout of {settings.timeout} seconds "
                f"and was killed: {settings.command}"
            )

        out.seek(0)
        # One byte past the bound, so an answer that reaches the bound can be
        # told from one that exceeds it without the whole of it being read.
        raw = out.read(MAX_STDOUT_BYTES + 1)
        err.seek(0)
        stderr = err.read(ERROR_TAIL_LENGTH * 2).decode("utf-8", "replace")

    if process.returncode != 0:
        said = _tail(stderr)
        return _nothing_known(
            f"the command exited {process.returncode} and did not answer"
            + (f": {said}" if said else "")
        )

    if len(raw) > MAX_STDOUT_BYTES:
        return _nothing_known(
            f"the command answered with more than the {MAX_STDOUT_BYTES} bytes "
            "of stdout this query reads, so the document was not read whole "
            "and was not parsed: a document truncated mid-token is not a document"
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return _nothing_known(
            f"the command's stdout is not a single JSON document: it is not "
            f"readable as text: {error}"
        )

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        return _nothing_known(
            "the command's stdout is not a single JSON document, so nothing "
            f"was read from it: {error}. Diagnostics belong on stderr; stdout "
            "carries the document and nothing else"
        )

    problems = schema_validator.validate(
        document, schema_validator.load_schema(ITEMS_SCHEMA, harness_root)
    )
    if problems:
        return _nothing_known(
            "the command's stdout does not satisfy the filed-items schema, so "
            "it is read as nothing known rather than best-effort parsed: "
            + "; ".join(problems)
        )

    return _read_answer(document, settings.max_items)
