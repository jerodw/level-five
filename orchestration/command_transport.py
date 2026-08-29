"""Filing an outbox entry by running one command the target configured.

The harness files an entry by invoking a configured command and knowing
nothing else about the tracker. The payload goes to the command on stdin as
JSON, a reference comes back on stdout, and the exit code says landed, retry
or stop. GitLab, Jira and files-in-source-control are then scripts a target
writes rather than code the harness carries — the move `test_command`,
`census_command` and `test_selection_command` already make, applied to an
issue tracker. A built-in provider per tracker would be the no-target-stack
rule broken once per tracker.

It also dissolves the multi-call problem. Filing a finding means creating the
issue, labelling it and putting it on a project board — three API calls and a
partial-failure surface. The harness makes one invocation. Those three calls
are the command's business, and atomicity is a contract the command owes
rather than state the harness has to track.

**The command must be idempotent given the key.** That is the sentence the
whole design rests on. The harness hands the command the idempotency key, on
stdin and in the environment, and the command is expected to search by that
key before it creates. That is what makes the ambiguous write — created,
response lost — safe to retry. The harness cannot enforce it, so it is
documented here as a contract and demonstrated by the reference
implementation shipped at `templates/sync/github.sh`. It is also why this
transport offers **no `look_up` at all**: an ambiguous write is resolved by
re-invoking an idempotent command, not by a second call the harness makes,
and `outbox._look_up` already reports no problem for a transport offering
none.

Three outcomes are read as transient rather than terminal, all for one
reason — nothing was established about whether the request arrived: the
command that ran past its timeout, the command that could not be launched at
all, and the command that exited zero without naming a reference. A
misconfiguration a human fixes should be waiting pending when they fix it,
rather than marked failed and needing reconciliation.

Nothing here reads the reference. It is recorded and never parsed, and no
decision in the harness is taken from its content.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from outbox import Filing, deferred, filed, refused

#: The exit code a command uses to say that the attempt did not settle and is
#: worth retrying. 75 is EX_TEMPFAIL, the sysexits convention for a temporary
#: failure that should be retried, and it is far from the codes a shell
#: produces by accident: 1 and 2 are ordinary failures, 126 and 127 are a
#: command that could not be executed, and 128+N is a signal. A small number
#: would collide with all of those; this one collides with nothing.
TRANSIENT_EXIT_CODE = 75

#: How long a command may run when the target configures no timeout. Sixty
#: seconds rather than the zero `max_pause_wait_seconds` defaults to: a
#: timeout of zero here would be no timeout at all, which is the failure the
#: rule exists to prevent, so the default duration written in harness source
#: is a real one. Every path through this transport is bounded in time.
DEFAULT_TIMEOUT_SECONDS = 60

#: The longest reference this transport will accept. The reference is written
#: into a durable file and validated against the entry schema, so an
#: unbounded one is an unbounded write of somebody else's stdout into the
#: queue. A command naming something longer is transient with a reason rather
#: than written: nothing was established, and a human who fixes the command
#: finds the entry still pending.
REFERENCE_MAX_LENGTH = 2048

#: How much of a command's stderr is carried back as the error text on an
#: entry that did not land. A tail rather than the whole, because the text
#: goes into a durable file, and the tail rather than the head because a
#: command's last words are the ones that say why it stopped.
ERROR_TAIL_LENGTH = 2048

#: Where the entry's key is put in the command's environment, beside the copy
#: inside the document on stdin. Both are taken from `entry["key"]`, so the
#: two cannot disagree.
KEY_ENVIRONMENT_VARIABLE = "L5_SYNC_KEY"


def _tail(text: str) -> str:
    """The last of a command's stderr, bounded, with the bound made visible."""
    text = (text or "").strip()
    if len(text) <= ERROR_TAIL_LENGTH:
        return text
    return "…" + text[-ERROR_TAIL_LENGTH:]


def last_reference(stdout: str) -> str:
    """The last non-empty line of a command's stdout.

    The last rather than the first, so a command free to print whatever it
    likes on the way — a search it made, a board it updated — still names the
    reference it settled on by printing it last. It is stripped and otherwise
    unread: this transport never parses a reference and decides nothing from
    its content, so a reference that is not a URL lands exactly as one that
    is.
    """
    for line in reversed((stdout or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the process group the command leads, not merely the command.

    The command is spawned in its own session, so it leads a process group of
    its own and a kill delivered to that group reaches the children it
    spawned. Killing the process alone would leave a command's own children
    running past the transport's return, which is the whole reason the
    session is new.
    """
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        # The group is already gone, or this platform will not answer for it.
        # Either way the process itself must not be left behind.
        try:
            process.kill()
        except OSError:
            pass


@dataclass(frozen=True)
class CommandTransport:
    """A transport that files an entry by running one configured command.

    It answers only in the `Filing` values `orchestration/outbox.py` already
    defines, so the mapping from an exit code to a state is stated once here
    and the queue decides the state from the answer exactly as it does for any
    transport. `outbox.sync` consumes this through the contract it already
    has, and the queue module carries no subprocess of its own.

    It deliberately exposes no `look_up`. See the module docstring: an
    ambiguous write is resolved by re-invoking an idempotent command.
    """

    command: str
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    cwd: Path | None = None

    def file(self, entry: dict) -> Filing:
        """Run the command for one entry and read its answer off the exit code.

        Never raises: every failure this can meet — a command line that
        cannot be split, a command that cannot be launched, one that runs past
        the timeout, one that exits zero saying nothing — comes back as a
        `Filing`, because a transport that raised would put the queue's own
        totality at the mercy of a target's script.
        """
        try:
            argv = shlex.split(self.command)
        except ValueError as error:
            return deferred(
                f"the command could not be launched: {self.command!r} "
                f"cannot be read as an argument list: {error}"
            )
        if not argv:
            return deferred(
                f"the command could not be launched: {self.command!r} "
                "is an empty argument list"
            )

        key = entry["key"]
        document = json.dumps(entry, sort_keys=True)
        environment = {**os.environ, KEY_ENVIRONMENT_VARIABLE: key}

        try:
            process = subprocess.Popen(  # noqa: S603 - the command is the target's
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd is not None else None,
                env=environment,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            # Nothing was established about whether the request arrived,
            # because no request was made. A path that does not exist and a
            # file that is not executable both land here, and both are a
            # misconfiguration a human fixes — so the entry waits pending for
            # them rather than being failed and needing reconciliation.
            return deferred(
                f"the command could not be launched: {self.command}: {error}"
            )

        try:
            stdout, stderr = process.communicate(document, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            # Reap it, so the transport leaves nothing behind. The group is
            # dead by now, so this cannot wait on a child holding the pipes.
            try:
                stdout, stderr = process.communicate()
            except Exception:  # noqa: BLE001 - the answer is already decided
                stdout, stderr = "", ""
            return deferred(
                f"the command ran past its timeout of {self.timeout} seconds "
                f"and was killed: {self.command}"
                + (f": {_tail(stderr)}" if _tail(stderr) else "")
            )

        if process.returncode == 0:
            reference = last_reference(stdout)
            if not reference:
                return deferred(
                    "the command exited 0 and named no reference, which "
                    "establishes nothing about whether the request arrived"
                )
            if len(reference) > REFERENCE_MAX_LENGTH:
                return deferred(
                    f"the command named a reference of {len(reference)} "
                    f"characters, past the bound of {REFERENCE_MAX_LENGTH} "
                    "this transport will write"
                )
            return filed(reference)

        error = _tail(stderr) or f"the command exited {process.returncode} and said nothing"
        if process.returncode == TRANSIENT_EXIT_CODE:
            return deferred(error)
        return refused(error)
